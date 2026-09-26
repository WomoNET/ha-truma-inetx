"""Tests for selects, sensors and the flame sensor."""

from __future__ import annotations

from homeassistant.components.select import (
    ATTR_OPTION,
    ATTR_OPTIONS,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest

from tests.conftest import PanelHarness, settle

HEATER = 0x0201


async def test_heating_mode_select(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """Options come from the enum the heater reports."""
    entity_id = "select.combi_6_heating_mode"
    state = hass.states.get(entity_id)
    assert state.state == "comfort"
    assert state.attributes[ATTR_OPTIONS] == ["fast", "comfort"]

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "fast"},
        blocking=True,
    )
    await settle(hass)

    assert setup_integration.panel.writes == [(HEATER, "AirHeating", "Mode", 0)]
    assert hass.states.get(entity_id).state == "fast"


async def test_unavailable_option_is_rejected(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """Options outside the reported enum cannot be selected."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: "select.combi_6_gas", ATTR_OPTION: "boost"},
            blocking=True,
        )


async def test_selects_only_for_reported_energy_sources(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """A gas heater has a gas select but no diesel or electric select."""
    assert hass.states.get("select.combi_6_gas").state == "gas_on"
    assert hass.states.get("select.combi_6_diesel") is None
    assert hass.states.get("select.combi_6_electric_heating") is None


async def test_temperatures_and_flame(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """Sensors follow live reports from the panel."""
    assert hass.states.get("sensor.combi_6_room_temperature").state == "19.5"
    assert hass.states.get("sensor.combi_6_water_temperature").state == "35.2"
    assert hass.states.get("binary_sensor.inet_x_panel_flame").state == STATE_OFF

    setup_integration.panel.change("WaterHeating", "Temp", 401)
    setup_integration.panel.change("System", "FlameStatus", 1)
    await settle(hass)

    assert hass.states.get("sensor.combi_6_water_temperature").state == "40.1"
    assert hass.states.get("binary_sensor.inet_x_panel_flame").state == STATE_ON
