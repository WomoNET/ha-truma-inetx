"""Tests for the room climate entity."""

from __future__ import annotations

from homeassistant.components.climate import (
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    SERVICE_TURN_ON,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import PanelHarness, settle

ENTITY_ID = "climate.inet_x_panel_room_climate"
PANEL = 0x0101
HEATER = 0x0201


async def _call(hass: HomeAssistant, service: str, **data: object) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID, **data}, blocking=True
    )
    await settle(hass)


async def test_state_reflects_the_panel(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """Modes, temperatures and limits come from the reported schema."""
    state = hass.states.get(ENTITY_ID)

    assert state.state == HVACMode.OFF
    assert state.attributes[ATTR_HVAC_MODES] == [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.FAN_ONLY,
    ]
    assert state.attributes[ATTR_TEMPERATURE] == 21.0
    assert state.attributes["current_temperature"] == 19.5
    assert state.attributes[ATTR_MIN_TEMP] == 5.0
    assert state.attributes[ATTR_MAX_TEMP] == 30.0
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.OFF


async def test_heating_is_switched_on_at_the_panel(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """Heating is one write of the room mode to the panel."""
    await _call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.HEAT})

    assert setup_integration.panel.writes == [(PANEL, "RoomClimate", "Mode", 3)]
    assert hass.states.get(ENTITY_ID).state == HVACMode.HEAT


async def test_heater_activity_sets_the_action(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """The action follows the heater's control loop."""
    await _call(hass, SERVICE_TURN_ON)
    assert hass.states.get(ENTITY_ID).attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE

    setup_integration.panel.change("AirHeating", "Active", 1)
    await settle(hass)

    assert hass.states.get(ENTITY_ID).attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING


async def test_target_temperature_is_written_to_the_heater(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """The effective target temperature belongs to the heater, in tenths."""
    await _call(
        hass,
        SERVICE_SET_TEMPERATURE,
        **{ATTR_TEMPERATURE: 22.5, ATTR_HVAC_MODE: HVACMode.HEAT},
    )

    assert setup_integration.panel.writes == [
        (PANEL, "RoomClimate", "Mode", 3),
        (HEATER, "AirHeating", "TgtTemp", 225),
    ]
    assert hass.states.get(ENTITY_ID).attributes[ATTR_TEMPERATURE] == 22.5


async def test_ventilation_is_entered_and_left_cleanly(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """Ventilation needs air circulation, and leaving it ends air circulation."""
    await _call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.FAN_ONLY})
    await _call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.HEAT})

    assert setup_integration.panel.writes == [
        (PANEL, "RoomClimate", "Mode", 5),
        (HEATER, "AirCirculation", "Active", 1),
        (HEATER, "AirCirculation", "Active", 0),
        (PANEL, "RoomClimate", "Mode", 3),
    ]
    assert hass.states.get(ENTITY_ID).state == HVACMode.HEAT


async def test_modes_the_panel_does_not_offer_are_rejected(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """Cooling is not offered by a heater-only system."""
    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
    assert setup_integration.panel.writes == []


async def test_rejected_write_raises(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """A transfer the panel refuses surfaces as an error."""
    setup_integration.panel.refuse_transfers = True

    with pytest.raises(HomeAssistantError, match="RoomClimate/Mode"):
        await _call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.HEAT})


async def test_write_while_disconnected_raises(
    hass: HomeAssistant, config_entry: MockConfigEntry, setup_integration: PanelHarness
) -> None:
    """A command racing a connection loss fails instead of being dropped."""
    await setup_integration.client.drop()
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="not connected"):
        await config_entry.runtime_data.async_write("RoomClimate", "Mode", 3)
