"""Data update coordinator for SURU WaterMonitor."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util, slugify
from homeassistant.util.unit_conversion import VolumeConverter

from .api import SuruApiError, SuruAuthError, SuruWaterApi
from .const import DOMAIN, HISTORY_DAYS

_LOGGER = logging.getLogger(__name__)

_HOUR = timedelta(hours=1)


def consumption_statistic_id(unit_id: str) -> str:
    """External statistic holding the hourly water consumption of a unit."""
    return f"{DOMAIN}:{slugify(unit_id)}_water_consumption"


class SuruCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Fetch unit detail, meter reading and today's consumption per unit."""

    def __init__(
        self, hass: HomeAssistant, api: SuruWaterApi, update_interval: timedelta
    ) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=update_interval
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            units = await self.api.async_get_units()
            data: dict[str, dict] = {}
            for unit in units:
                unit_id = unit.get("id")
                if not unit_id:
                    continue
                detail = await self.api.async_get_unit(unit_id)
                reading = await self.api.async_get_reading(unit_id)
                today = dt_util.start_of_local_day()
                try:
                    consumption = await self.api.async_get_consumption(unit_id, today)
                except SuruApiError:
                    # range=day is subscription-gated (BASIC -> HTTP 403), so
                    # "consumption today" degrades to unknown like incidents.
                    consumption = None
                values = [
                    v for v in ((consumption or {}).get("values") or []) if v is not None
                ]
                if consumption is not None:
                    try:
                        await self._async_import_statistics(
                            unit_id, today, consumption, reading
                        )
                    except SuruApiError as err:
                        _LOGGER.warning(
                            "Could not update hourly statistics for unit %s: %s",
                            unit_id, err,
                        )

                devices = detail.get("devices") or []
                device_id = devices[0].get("id") if devices else None
                try:
                    incidents = await self.api.async_get_incidents(unit_id, device_id)
                except SuruApiError:
                    incidents = None  # alarm sensor degrades gracefully

                data[unit_id] = {
                    "detail": detail,
                    "reading": reading,
                    "consumption_today_l": float(sum(values)) if values else None,
                    "incidents": incidents,
                }
            return data
        except SuruAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SuruApiError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_import_statistics(
        self, unit_id: str, today: datetime, today_consumption: dict, reading: dict
    ) -> None:
        """Write the hourly consumption buckets as an external statistic.

        Many meters upload only once a day, and the API reports 0 (not None)
        for hours after the last upload. So only hours fully covered by the
        last measurement (`measuredAt` of the reading) are written. Each run
        rewrites everything since the day of the last stored hour and
        continues the running sum from the last stored hour before that day.
        Without stored statistics, the last HISTORY_DAYS days are backfilled.
        """
        statistic_id = consumption_statistic_id(unit_id)
        # The row before the last stored day is always among the last 72.
        rows = (
            await get_instance(self.hass).async_add_executor_job(
                get_last_statistics, self.hass, 72, statistic_id, False, {"sum"}
            )
        ).get(statistic_id, [])

        oldest = today - timedelta(days=HISTORY_DAYS)
        start = oldest
        if rows:
            last_day = dt_util.start_of_local_day(
                dt_util.as_local(dt_util.utc_from_timestamp(rows[0]["start"]))
            )
            start = max(last_day, oldest)
        base = next(
            (row["sum"] or 0.0 for row in rows if row["start"] < start.timestamp()),
            0.0,
        )

        hours: list[tuple[datetime, float | None]] = []
        for back in range((today.date() - start.date()).days, -1, -1):
            day = today - timedelta(days=back)
            if back == 0:
                data = today_consumption
            else:
                try:
                    data = await self.api.async_get_consumption(unit_id, day)
                except SuruApiError:
                    if rows:
                        raise  # don't leave a hole in the stored statistics
                    continue  # backfill: e.g. days before the meter was installed
            # values[i] is the bucket starting i hours after (local) midnight.
            day_utc = dt_util.as_utc(day)
            hours.extend(
                (day_utc + i * _HOUR, value)
                for i, value in enumerate(data.get("values") or [])
            )

        cutoff = dt_util.utcnow()
        if (ts := reading.get("measuredAt")) and (
            measured := dt_util.parse_datetime(ts)
        ):
            cutoff = min(cutoff, dt_util.as_utc(measured))
        hours = [(start_, value) for start_, value in hours if start_ + _HOUR <= cutoff]
        if not hours:
            return

        total = base
        statistics: list[StatisticData] = []
        for start_, value in hours:
            total += float(value or 0)
            statistics.append(StatisticData(start=start_, sum=total))

        async_add_external_statistics(
            self.hass,
            StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=f"SURU WaterMonitor {unit_id} water consumption",
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_class=VolumeConverter.UNIT_CLASS,
                unit_of_measurement=UnitOfVolume.LITERS,
            ),
            statistics,
        )
