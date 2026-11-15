"""Data update coordinator for SURU WaterMonitor."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SuruApiError, SuruAuthError, SuruWaterApi
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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
                try:
                    consumption = await self.api.async_get_consumption(
                        unit_id, dt_util.start_of_local_day()
                    )
                    values = [
                        v for v in (consumption.get("values") or []) if v is not None
                    ]
                except SuruApiError:
                    # range=day is subscription-gated (BASIC -> HTTP 403), so
                    # "consumption today" degrades to unknown like incidents.
                    values = []

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
