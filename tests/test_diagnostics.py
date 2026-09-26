"""Tests for diagnostics."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from tests.conftest import PanelHarness


async def test_diagnostics_contain_schema_without_identifiers(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: MockConfigEntry,
    setup_integration: PanelHarness,
) -> None:
    """All parameters are included; serial numbers and the address are not."""
    assert await async_setup_component(hass, "diagnostics", {})

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, config_entry
    )

    assert diagnostics["connected"] is True
    assert diagnostics["entry"]["address"] == "**REDACTED**"
    heater = {(p["topic"], p["name"]): p for p in diagnostics["nodes"]["0x0201"]}
    assert heater[("Identify", "SerialNr")]["value"] == "**REDACTED**"
    assert heater[("Identify", "Name")]["value"] == "Combi 6"
    assert heater[("WaterHeating", "Mode")]["options"] == [
        {"value": 0, "name": "40", "available": True},
        {"value": 1, "name": "60", "available": True},
        {"value": 2, "name": "70", "available": True},
    ]
    assert heater[("AirHeating", "Temp")]["writable"] is False
