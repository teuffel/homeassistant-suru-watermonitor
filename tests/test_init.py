"""Setup/migration tests (require pytest-homeassistant-custom-component)."""
from datetime import timedelta

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.suru_water_monitor import async_migrate_entry
from custom_components.suru_water_monitor.const import DOMAIN
from custom_components.suru_water_monitor.coordinator import consumption_statistic_id


async def test_migration_clears_hourly_statistics(hass: HomeAssistant) -> None:
    """0.2.0 statistics (30-day backfill) are dropped to rebuild the full history."""
    stat_id = consumption_statistic_id("1")
    hour = dt_util.start_of_local_day() - timedelta(days=1)
    async_add_external_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.NONE, has_sum=True, name=None, source=DOMAIN,
            statistic_id=stat_id, unit_class="volume", unit_of_measurement="L",
        ),
        [StatisticData(start=hour, sum=1.0)],
    )
    await async_wait_recording_done(hass)
    assert await hass.async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, False, {"sum"}
    )

    entry = MockConfigEntry(domain=DOMAIN, version=1, minor_version=1, data={})
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    await async_wait_recording_done(hass)

    assert entry.minor_version == 2
    rows = await hass.async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, False, {"sum"}
    )
    assert rows == {}
