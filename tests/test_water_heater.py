"""Tests for the hot water entity."""

from __future__ import annotations

from homeassistant.components.water_heater import (
    ATTR_OPERATION_LIST,
    ATTR_OPERATION_MODE,
    DOMAIN as WATER_HEATER_DOMAIN,
    SERVICE_SET_OPERATION_MODE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from tests.conftest import PanelHarness, settle

ENTITY_ID = "water_heater.combi_6_hot_water"
HEATER = 0x0201


async def _call(hass: HomeAssistant, service: str, **data: object) -> None:
    await hass.services.async_call(
        WATER_HEATER_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID, **data}, blocking=True
    )
    await settle(hass)


async def test_levels_come_from_the_heater(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """The operation modes are the temperature levels the heater reports."""
    state = hass.states.get(ENTITY_ID)

    assert state.state == STATE_OFF
    assert state.attributes[ATTR_OPERATION_LIST] == [STATE_OFF, "40", "60", "70"]
    assert state.attributes["current_temperature"] == 35.2


async def test_level_from_off_waits_until_ready(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """From off, the level is only sent after the heater reports it is ready."""
    await _call(hass, SERVICE_SET_OPERATION_MODE, **{ATTR_OPERATION_MODE: "40"})

    assert setup_integration.panel.writes == [
        (HEATER, "WaterHeating", "Active", 1),
        (HEATER, "WaterHeating", "Mode", 0),
    ]
    state = hass.states.get(ENTITY_ID)
    assert state.state == "40"
    assert state.attributes[ATTR_TEMPERATURE] == 40


async def test_level_is_not_sent_when_heater_stays_off(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """Without the ready report the level is not sent."""
    setup_integration.panel.echo_writes = False

    with pytest.raises(HomeAssistantError):
        await _call(hass, SERVICE_SET_OPERATION_MODE, **{ATTR_OPERATION_MODE: "60"})

    assert setup_integration.panel.writes == [(HEATER, "WaterHeating", "Active", 1)]


async def test_level_while_on_is_one_write(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """Changing the level of running hot water only writes the mode."""
    await _call(hass, SERVICE_TURN_ON)
    setup_integration.panel.writes.clear()

    await _call(hass, SERVICE_SET_OPERATION_MODE, **{ATTR_OPERATION_MODE: "70"})

    assert setup_integration.panel.writes == [(HEATER, "WaterHeating", "Mode", 2)]


async def test_off_and_unknown_levels(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """Off deactivates hot water; unknown levels are rejected."""
    await _call(hass, SERVICE_TURN_ON)
    await _call(hass, SERVICE_TURN_OFF)
    assert hass.states.get(ENTITY_ID).state == STATE_OFF

    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_SET_OPERATION_MODE, **{ATTR_OPERATION_MODE: "90"})
    assert setup_integration.panel.writes[-1] == (HEATER, "WaterHeating", "Active", 0)
