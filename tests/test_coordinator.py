"""Coordinator tests (require pytest-homeassistant-custom-component)."""
from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant

from custom_components.suru_water_monitor.api import SuruApiError
from custom_components.suru_water_monitor.coordinator import SuruCoordinator


def _api() -> AsyncMock:
    api = AsyncMock()
    api.async_get_units.return_value = [{"id": "1"}]
    api.async_get_unit.return_value = {"devices": [{"id": "dev-1"}]}
    api.async_get_reading.return_value = {"reading": 12.345}
    api.async_get_consumption.return_value = {"values": [1, 2, None]}
    api.async_get_incidents.return_value = {"incidents": {}}
    return api


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
