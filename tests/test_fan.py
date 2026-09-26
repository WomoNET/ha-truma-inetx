"""Tests for the ventilation fan."""

from __future__ import annotations

from homeassistant.components.fan import (
    ATTR_PERCENTAGE,
    ATTR_PERCENTAGE_STEP,
    DOMAIN as FAN_DOMAIN,
    SERVICE_SET_PERCENTAGE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from tests.conftest import PanelHarness, settle

ENTITY_ID = "fan.combi_6_ventilation"
PANEL = 0x0101
HEATER = 0x0201


async def _call(hass: HomeAssistant, service: str, **data: object) -> None:
    await hass.services.async_call(
        FAN_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID, **data}, blocking=True
    )
    await settle(hass)


async def test_levels_are_speeds(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """The ten fan levels map to percentage steps of ten."""
    state = hass.states.get(ENTITY_ID)

    assert state.state == STATE_OFF
    assert state.attributes[ATTR_PERCENTAGE_STEP] == 10


async def test_turn_on_selects_ventilation_first(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """A level is only accepted after ventilation and air circulation are on."""
    await _call(hass, SERVICE_TURN_ON, **{ATTR_PERCENTAGE: 50})

    assert setup_integration.panel.writes == [
        (PANEL, "RoomClimate", "Mode", 5),
        (HEATER, "AirCirculation", "Active", 1),
        (HEATER, "AirCirculation", "FanLevel", 5),
    ]
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_PERCENTAGE] == 50


async def test_speed_change_while_on_is_one_write(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """Changing the speed of running ventilation only writes the level."""
    await _call(hass, SERVICE_TURN_ON)
    setup_integration.panel.writes.clear()

    await _call(hass, SERVICE_SET_PERCENTAGE, **{ATTR_PERCENTAGE: 100})

    assert setup_integration.panel.writes == [
        (HEATER, "AirCirculation", "FanLevel", 10)
    ]


async def test_turn_off_ends_ventilation(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """Off ends air circulation and switches the room climate off."""
    await _call(hass, SERVICE_TURN_ON, **{ATTR_PERCENTAGE: 30})
    setup_integration.panel.writes.clear()

    await _call(hass, SERVICE_SET_PERCENTAGE, **{ATTR_PERCENTAGE: 0})

    assert setup_integration.panel.writes == [
        (HEATER, "AirCirculation", "Active", 0),
        (PANEL, "RoomClimate", "Mode", 0),
    ]
    assert hass.states.get(ENTITY_ID).state == STATE_OFF


async def test_turn_off_service(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """The turn off service stops ventilation as well."""
    await _call(hass, SERVICE_TURN_ON)
    await _call(hass, SERVICE_TURN_OFF)

    assert hass.states.get(ENTITY_ID).state == STATE_OFF
