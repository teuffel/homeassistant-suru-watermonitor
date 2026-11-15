"""Coordinator tests (require pytest-homeassistant-custom-component)."""
from datetime import timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder.statistics import get_last_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.suru_water_monitor.api import SuruApiError
from custom_components.suru_water_monitor.const import HISTORY_DAYS
from custom_components.suru_water_monitor.coordinator import (
    SuruCoordinator,
    consumption_statistic_id,
)

STAT_ID = consumption_statistic_id("1")


def _api(day_values=None) -> AsyncMock:
    """API mock; consumption values per local date via `day_values(date)`."""
    api = AsyncMock()
    api.async_get_units.return_value = [{"id": "1"}]
    api.async_get_unit.return_value = {"devices": [{"id": "dev-1"}]}
    api.async_get_reading.return_value = {"reading": 12.345}
    api.async_get_incidents.return_value = {"incidents": {}}
    if day_values is None:
        api.async_get_consumption.return_value = {"values": [1, 2, None]}
    else:
        api.async_get_consumption.side_effect = lambda _unit, day: {
            "values": day_values(day.date())
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
    """Backfill on first run, then fill in hours the meter uploads late.

    Mirrors the real API: the meter uploads once a day (here 09:13), hours
    after that are reported as 0 until "now", later hours as None.
    """
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-01-15 17:30:00+01:00")
    day1 = dt_util.now().date()
    day2 = day1 + timedelta(days=1)

    # Day 1: buckets 00-09 measured (09 only partly), 10-17 zero-filled.
    uploads = {day1: [1] * 10 + [0] * 8 + [None] * 6}
    api = _api(lambda d: uploads.get(d, [1] * 24))
    api.async_get_reading.return_value = {
        "reading": 1.0, "measuredAt": "2026-01-15T09:13:00.000+01:00"
    }
    coordinator = SuruCoordinator(hass, api, timedelta(hours=1))
    await coordinator._async_update_data()

    assert api.async_get_consumption.await_count == HISTORY_DAYS + 1
    start, total = await _last_stat(hass)
    assert start.isoformat() == "2026-01-15T08:00:00+01:00"  # last full hour
    assert total == HISTORY_DAYS * 24 + 9

    # Day 2: the upload at 09:13 completed day 1 and covers day 2 until then.
    freezer.move_to("2026-01-16 17:30:00+01:00")
    uploads = {day1: [1] * 24, day2: [2] * 10 + [0] * 8 + [None] * 6}
    api.async_get_reading.return_value = {
        "reading": 2.0, "measuredAt": "2026-01-16T09:13:00.000+01:00"
    }
    api.async_get_consumption.reset_mock()
    await coordinator._async_update_data()

    assert api.async_get_consumption.await_count == 2  # today + day 1 only
    start, total = await _last_stat(hass)
    assert start.isoformat() == "2026-01-16T08:00:00+01:00"
    assert total == HISTORY_DAYS * 24 + 24 + 9 * 2
