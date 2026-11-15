"""The SURU WaterMonitor integration."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import async_list_statistic_ids
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SuruWaterApi
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES, DOMAIN
from .coordinator import SuruCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SURU WaterMonitor from a config entry."""
    api = SuruWaterApi(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )
    minutes = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES))
    coordinator = SuruCoordinator(hass, api, timedelta(minutes=minutes))
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old config entry."""
    if entry.version == 1 and entry.minor_version < 2:
        # 0.2.1 backfills the whole history instead of 30 days: drop the
        # hourly statistics written by 0.2.0 so they are rebuilt.
        ids = [
            stat["statistic_id"]
            for stat in await async_list_statistic_ids(hass, statistic_type="sum")
            if stat["source"] == DOMAIN
        ]
        if ids:
            done = hass.loop.create_future()
            get_instance(hass).async_clear_statistics(
                ids,
                on_done=lambda: hass.loop.call_soon_threadsafe(done.set_result, None),
            )
            await done
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options (e.g. scan interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
