"""Tests for setup, reconnects and unload."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.bluetooth import MONOTONIC_TIME
from homeassistant.components.bluetooth.const import UNAVAILABLE_TRACK_SECONDS
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.truma_inetx.const import DOMAIN
from custom_components.truma_inetx.coordinator import (
    FAILURES_BEFORE_REPAIR,
    RECONNECT_DELAYS,
)
from custom_components.truma_inetx.protocol.const import CHAR_COMMAND, CHAR_DATA_READ
from tests.conftest import (
    IDENTITY_ADDRESS,
    PANEL_ID,
    FakeProxy,
    PanelHarness,
    bluetooth_time,
    settle,
)

EXPECTED_ENTITIES = {
    "binary_sensor.inet_x_panel_flame",
    "climate.inet_x_panel_room_climate",
    "fan.combi_6_ventilation",
    "select.combi_6_gas",
    "select.combi_6_heating_mode",
    "sensor.combi_6_room_temperature",
    "sensor.combi_6_water_temperature",
    "water_heater.combi_6_hot_water",
}


async def test_setup_creates_devices_and_entities(
    hass: HomeAssistant, config_entry: MockConfigEntry, setup_integration: PanelHarness
) -> None:
    """The panel and the heater become devices with their entities."""
    entities = {
        entry.entity_id
        for entry in er.async_entries_for_config_entry(
            er.async_get(hass), config_entry.entry_id
        )
    }
    assert entities >= EXPECTED_ENTITIES
    assert "sensor.inet_x_panel_panel_temperature" in entities
    assert hass.states.get("sensor.inet_x_panel_panel_temperature") is None

    device_registry = dr.async_get(hass)
    panel = device_registry.async_get_device_by_identifier(
        (DOMAIN, PANEL_ID), config_entry.entry_id
    )
    heater = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{PANEL_ID}_0201"), config_entry.entry_id
    )
    assert panel is not None and heater is not None
    assert panel.model == "iNet X Panel"
    assert panel.sw_version == "3.4.27"
    assert (dr.CONNECTION_BLUETOOTH, IDENTITY_ADDRESS) in panel.connections
    assert heater.model == "Combi 6"
    assert heater.via_device_id == panel.id
    assert setup_integration.pair_requests == [False]


async def test_setup_retries_while_panel_is_out_of_range(
    hass: HomeAssistant, config_entry: MockConfigEntry, harness: PanelHarness
) -> None:
    """Without an advertisement the setup is retried later."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert harness.clients == []


async def test_connection_lost_during_setup_is_retried(
    hass: HomeAssistant, config_entry: MockConfigEntry, proxy: FakeProxy
) -> None:
    """A link that drops while the session starts is not kept."""
    connection = MagicMock()
    connection.client.is_connected = False
    connection.async_close = AsyncMock()
    proxy.advertise()

    with patch(
        "custom_components.truma_inetx.coordinator.async_open_session",
        AsyncMock(return_value=connection),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    connection.async_close.assert_awaited_once()


async def test_lost_connection_reconnects_after_delay(
    hass: HomeAssistant,
    setup_integration: PanelHarness,
    freezer: FrozenDateTimeFactory,
    proxy: FakeProxy,
) -> None:
    """Entities become unavailable on loss and recover after the backoff delay."""
    await setup_integration.client.drop()
    await hass.async_block_till_done()
    assert (
        hass.states.get("climate.inet_x_panel_room_climate").state == STATE_UNAVAILABLE
    )

    freezer.tick(timedelta(seconds=RECONNECT_DELAYS[0]))
    proxy.advertise()
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(setup_integration.clients) == 2
    assert hass.states.get("climate.inet_x_panel_room_climate").state == "off"


async def test_reappearing_panel_reconnects_immediately(
    hass: HomeAssistant,
    setup_integration: PanelHarness,
    freezer: FrozenDateTimeFactory,
    monkeypatch: pytest.MonkeyPatch,
    proxy: FakeProxy,
) -> None:
    """A panel that comes back into range does not wait for the backoff."""
    monkeypatch.setattr(
        "custom_components.truma_inetx.coordinator.RECONNECT_DELAYS", (3600,)
    )
    await setup_integration.client.drop()
    await hass.async_block_till_done()

    with bluetooth_time(MONOTONIC_TIME() + 2 * UNAVAILABLE_TRACK_SECONDS):
        # The proxy expires the panel first, the next sweep reports it gone.
        for _ in range(2):
            freezer.tick(timedelta(seconds=UNAVAILABLE_TRACK_SECONDS + 1))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
        proxy.advertise()
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    assert hass.states.get("climate.inet_x_panel_room_climate").state == "off"


async def test_panel_still_in_range_waits_for_backoff(
    hass: HomeAssistant,
    setup_integration: PanelHarness,
    freezer: FrozenDateTimeFactory,
    monkeypatch: pytest.MonkeyPatch,
    proxy: FakeProxy,
) -> None:
    """Advertisements of a panel that never left do not bypass the backoff."""
    monkeypatch.setattr(
        "custom_components.truma_inetx.coordinator.RECONNECT_DELAYS", (3600,)
    )
    await setup_integration.client.drop()
    await hass.async_block_till_done()

    freezer.tick(timedelta(seconds=10))
    proxy.advertise()
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(setup_integration.clients) == 1


async def test_repeated_failures_in_range_ask_to_pair_again(
    hass: HomeAssistant,
    setup_integration: PanelHarness,
    freezer: FrozenDateTimeFactory,
    proxy: FakeProxy,
) -> None:
    """A panel that keeps refusing connections leads to a re-pairing flow."""
    await setup_integration.client.drop()
    await hass.async_block_till_done()
    setup_integration.connect_error = TimeoutError()

    for attempt in range(FAILURES_BEFORE_REPAIR):
        delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
        freezer.tick(timedelta(seconds=delay))
        proxy.advertise()
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_unload_releases_the_panel(
    hass: HomeAssistant, config_entry: MockConfigEntry, setup_integration: PanelHarness
) -> None:
    """Unloading stops notifications before disconnecting."""
    client = setup_integration.client

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not client.is_connected
    assert client.notifying.isdisjoint({CHAR_COMMAND, CHAR_DATA_READ})
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_home_assistant_stop_releases_the_panel(
    hass: HomeAssistant, setup_integration: PanelHarness
) -> None:
    """The panel is released when Home Assistant stops."""
    client = setup_integration.client

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert not client.is_connected
    assert len(setup_integration.clients) == 1


async def test_late_heater_gets_entities(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    harness: PanelHarness,
    proxy: FakeProxy,
) -> None:
    """A heater that only answers after setup still gets its entities."""
    harness.configure_panel = lambda panel: panel.silent_nodes.add(0x0201)
    proxy.advertise()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("water_heater.combi_6_hot_water") is None
    assert hass.states.get("climate.inet_x_panel_room_climate") is not None

    harness.panel.silent_nodes.clear()
    harness.panel.change("AirHeating", "Temp", 205)
    await settle(hass)

    assert hass.states.get("water_heater.combi_6_hot_water") is not None
