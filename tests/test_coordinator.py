"""Coordinator tests (require pytest-homeassistant-custom-component)."""
from datetime import timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.suru_water_monitor.api import SuruApiError
from custom_components.suru_water_monitor.const import (
    BACKFILL_DAYS_PER_UPDATE,
    DOMAIN,
)
from custom_components.suru_water_monitor.coordinator import (
    SuruCoordinator,
    consumption_statistic_id,
)

STAT_ID = consumption_statistic_id("1")


def _api(day_values=None) -> AsyncMock:
    """API mock; consumption values via `day_values(day, range_)`."""
    api = AsyncMock()
    api.async_get_units.return_value = [{"id": "1"}]
    api.async_get_unit.return_value = {"devices": [{"id": "dev-1"}]}
    api.async_get_reading.return_value = {"reading": 12.345}
    api.async_get_incidents.return_value = {"incidents": {}}
    if day_values is None:
        api.async_get_consumption.return_value = {"values": [1, 2, None]}
    else:
        api.async_get_consumption.side_effect = lambda _unit, day, range_="day": {
            "values": day_values(day, range_)
        }
    return api


async def _last_stat(hass: HomeAssistant) -> tuple:
    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        get_last_statistics, hass, 1, STAT_ID, False, {"sum"}
    )
    row = rows[STAT_ID][0]
    return dt_util.as_local(dt_util.utc_from_timestamp(row["start"])), row["sum"]


async def test_update(hass: HomeAssistant) -> None:
    coordinator = SuruCoordinator(hass, _api(), timedelta(hours=1))
    data = await coordinator._async_update_data()
    assert data["1"]["reading"] == {"reading": 12.345}
    assert data["1"]["consumption_today_l"] == 3.0


async def test_consumption_forbidden_does_not_fail_update(hass: HomeAssistant) -> None:
    """BASIC accounts get HTTP 403 on range=day (issue #3)."""
    api = _api()
    api.async_get_consumption.side_effect = SuruApiError("HTTP 403")
    coordinator = SuruCoordinator(hass, api, timedelta(hours=1))
    data = await coordinator._async_update_data()
    assert data["1"]["reading"] == {"reading": 12.345}
    assert data["1"]["consumption_today_l"] is None
    assert data["1"]["incidents"] == {"incidents": {}}


async def test_hourly_statistics_backfill_and_late_upload(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Backfill the whole history in chunks, then fill in late uploads.

    Mirrors the real API: the meter uploads once a day (here 09:13), hours
    after that are reported as 0 until "now", later hours as None. Monthly
    totals (range=year) show data from December 2025 on.
    """
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-01-15 17:30:00+01:00")
    jan15 = dt_util.now().date()
    jan16 = jan15 + timedelta(days=1)
    years = {2026: [5] + [None] * 11, 2025: [None] * 11 + [5]}
    uploads = {jan15: [1] * 10 + [0] * 8 + [None] * 6}

    def values(day, range_):
        if range_ == "year":
            return years.get(day.year, [None] * 12)
        return uploads.get(day.date(), [1] * 24)

    api = _api(values)
    api.async_get_reading.return_value = {
        "reading": 1.0, "measuredAt": "2026-01-15T09:13:00.000+01:00"
    }
    coordinator = SuruCoordinator(hass, api, timedelta(hours=1))

    # Run 1: today + 2 years scanned + first chunk (Dec 1-31).
    await coordinator._async_update_data()
    assert api.async_get_consumption.await_count == 1 + 2 + BACKFILL_DAYS_PER_UPDATE
    start, total = await _last_stat(hass)
    assert start.isoformat() == "2025-12-31T23:00:00+01:00"
    assert total == 31 * 24

    # Run 2: continues from Dec 31 up to today's last measurement (08:00 bucket).
    api.async_get_consumption.reset_mock()
    await coordinator._async_update_data()
    # today + history check (2 years, once per start) + Dec 31-Jan 14
    assert api.async_get_consumption.await_count == 1 + 2 + 15
    start, total = await _last_stat(hass)
    assert start.isoformat() == "2026-01-15T08:00:00+01:00"
    assert total == 31 * 24 + 14 * 24 + 9

    # Next day: the upload at 09:13 completed Jan 15 and covers Jan 16 until then.
    freezer.move_to("2026-01-16 17:30:00+01:00")
    uploads = {jan15: [1] * 24, jan16: [2] * 10 + [0] * 8 + [None] * 6}
    api.async_get_reading.return_value = {
        "reading": 2.0, "measuredAt": "2026-01-16T09:13:00.000+01:00"
    }
    api.async_get_consumption.reset_mock()
    await coordinator._async_update_data()
    assert api.async_get_consumption.await_count == 2  # today + Jan 15
    start, total = await _last_stat(hass)
    assert start.isoformat() == "2026-01-16T08:00:00+01:00"
    assert total == 31 * 24 + 15 * 24 + 9 * 2


async def test_hourly_statistics_failing_days(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A failing old day is skipped; a failing recent day is retried later."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-01-10 17:30:00+01:00")
    failing: set = set()

    def values(day, range_):
        if range_ == "year":
            return [5] + [None] * 11 if day.year == 2026 else [None] * 12
        if day.date() in failing:
            raise SuruApiError("HTTP 500")
        return [1] * 24

    api = _api(values)
    api.async_get_reading.return_value = {
        "reading": 1.0, "measuredAt": "2026-01-10T09:13:00.000+01:00"
    }
    coordinator = SuruCoordinator(hass, api, timedelta(hours=1))
    await coordinator._async_update_data()
    start, total = await _last_stat(hass)
    assert start.isoformat() == "2026-01-10T08:00:00+01:00"
    assert total == 9 * 24 + 9

    # Five days later, Jan 12 fails permanently: skipped, the rest is written.
    freezer.move_to("2026-01-15 17:30:00+01:00")
    failing = {dt_util.now().date() - timedelta(days=3)}
    api.async_get_reading.return_value = {
        "reading": 2.0, "measuredAt": "2026-01-15T09:13:00.000+01:00"
    }
    await coordinator._async_update_data()
    start, total = await _last_stat(hass)
    assert start.isoformat() == "2026-01-15T08:00:00+01:00"
    assert total == 9 * 24 + 24 * 4 + 9  # Jan 10, 11, 13, 14 + Jan 15 until 09:00

    # Next day, yesterday fails: nothing is written, retried on the next update.
    freezer.move_to("2026-01-16 17:30:00+01:00")
    failing = {dt_util.now().date() - timedelta(days=1)}
    api.async_get_reading.return_value = {
        "reading": 3.0, "measuredAt": "2026-01-16T09:13:00.000+01:00"
    }
    data = await coordinator._async_update_data()
    assert data["1"]["reading"]["reading"] == 3.0  # the update itself succeeds
    start, total = await _last_stat(hass)
    assert start.isoformat() == "2026-01-15T08:00:00+01:00"


async def test_short_history_from_020_is_rebuilt(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Statistics that don't reach back to the first data (0.2.0) are rebuilt."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-01-15 17:30:00+01:00")
    years = {2026: [5] + [None] * 11, 2025: [None] * 11 + [5]}  # data from Dec

    def values(day, range_):
        if range_ == "year":
            return years.get(day.year, [None] * 12)
        return [1] * 24

    # A 0.2.0-style statistic starting on Jan 10 with an unrelated sum.
    async_add_external_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.NONE, has_sum=True, name=None,
            source=DOMAIN, statistic_id=STAT_ID, unit_class="volume",
            unit_of_measurement="L",
        ),
        [
            StatisticData(start=dt_util.as_utc(dt_util.parse_datetime(
                f"2026-01-{day}T00:00:00+01:00")), sum=1000.0 + int(day))
            for day in ("10", "11")
        ],
    )
    await async_wait_recording_done(hass)

    api = _api(values)
    api.async_get_reading.return_value = {
        "reading": 1.0, "measuredAt": "2026-01-15T09:13:00.000+01:00"
    }
    coordinator = SuruCoordinator(hass, api, timedelta(hours=1))
    await coordinator._async_update_data()

    # Cleared and rebuilt from Dec 1 (first chunk: Dec 1-31).
    start, total = await _last_stat(hass)
    assert start.isoformat() == "2025-12-31T23:00:00+01:00"
    assert total == 31 * 24


async def test_no_statistics_during_startup(hass: HomeAssistant) -> None:
    """The recorder doesn't write before HA has started: skip, don't block."""
    hass.set_state(CoreState.starting)
    api = _api(lambda day, range_: [1] * 24)
    coordinator = SuruCoordinator(hass, api, timedelta(hours=1))
    data = await coordinator._async_update_data()
    assert data["1"]["consumption_today_l"] == 24.0
    assert api.async_get_consumption.await_count == 1  # today only, no backfill
