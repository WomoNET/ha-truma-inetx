"""Fixtures for Home Assistant tests of the integration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from habluetooth import BaseHaRemoteScanner, HaBluetoothConnector
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import MONOTONIC_TIME, BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.truma_inetx.const import CONF_LOCAL_NAME, DOMAIN
from custom_components.truma_inetx.protocol.const import (
    CHAR_COMMAND,
    CHAR_DATA_READ,
    TRUMA_MANUFACTURER_ID,
)
from tests.fake_panel import FakePanel

LOCAL_NAME = "Truma iNetX-F4FCBA"
PANEL_ID = "F4FCBA"
IDENTITY_ADDRESS = "74:D2:85:F4:FC:BA"
RANDOM_ADDRESS = "7D:9B:E2:85:C4:AA"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


PROXY_SOURCE = "AA:BB:CC:DD:EE:FF"


@contextmanager
def bluetooth_time(value: float) -> Generator[None]:
    """Set the clock of the Bluetooth stack, which freezegun does not reach."""
    with (
        patch("habluetooth.base_scanner.monotonic_time_coarse", return_value=value),
        patch("habluetooth.manager.monotonic_time_coarse", return_value=value),
    ):
        yield


class FakeProxy:
    """A connectable Bluetooth proxy that relays the panel's advertisements."""

    def __init__(self, scanner: BaseHaRemoteScanner) -> None:
        self.scanner = scanner

    def advertise(
        self, address: str = IDENTITY_ADDRESS, name: str = LOCAL_NAME
    ) -> None:
        """Relay one advertisement of the panel."""
        self.scanner._async_on_advertisement(
            address,
            -60,
            name,
            [],
            {},
            {TRUMA_MANUFACTURER_ID: b"\x00\x01"},
            None,
            {},
            MONOTONIC_TIME(),
        )


@pytest.fixture(autouse=True)
async def proxy(hass: HomeAssistant, mock_bluetooth: None) -> AsyncGenerator[FakeProxy]:
    """Set up Bluetooth with one connectable proxy in range of the panel."""
    assert await async_setup_component(hass, bluetooth.DOMAIN, {})
    await hass.async_block_till_done()
    scanner = BaseHaRemoteScanner(
        PROXY_SOURCE,
        "Fake proxy",
        HaBluetoothConnector(
            client=object, source=PROXY_SOURCE, can_connect=lambda: True
        ),
        True,
    )
    unregister = bluetooth.async_register_scanner(hass, scanner, connection_slots=3)
    cancel_setup = scanner.async_setup()
    yield FakeProxy(scanner)
    cancel_setup()
    unregister()


@pytest.fixture(autouse=True)
def short_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests with silent nodes or missing replies fast."""
    monkeypatch.setattr(
        "custom_components.truma_inetx.protocol.session.DISCOVERY_TIMEOUT", 0.3
    )
    monkeypatch.setattr(
        "custom_components.truma_inetx.commands.WATER_READY_TIMEOUT", 0.3
    )
    monkeypatch.setattr("custom_components.truma_inetx.pairing.PANEL_WAIT_TIMEOUT", 1)


class FakeBleakClient:
    """Stands in for a connected bleak client talking to the fake panel."""

    def __init__(
        self,
        panel: FakePanel,
        address: str,
        disconnected_callback: Callable[[Any], None] | None,
    ) -> None:
        self.panel = panel
        self.address = address
        self.is_connected = True
        self.notifying: set[str] = set()
        self._disconnected_callback = disconnected_callback
        self._handlers: dict[str, Callable[[Any, bytearray], None]] = {}
        self.services = self

    def get_characteristic(self, _uuid: str) -> Any:
        """Return a characteristic with the panel's chunk size."""
        return type(
            "Characteristic",
            (),
            {"max_write_without_response_size": self.panel.max_data_write_size},
        )()

    async def start_notify(
        self, uuid: str, handler: Callable[[Any, bytearray], None]
    ) -> None:
        self._handlers[uuid] = handler
        self.notifying.add(uuid)
        if {CHAR_COMMAND, CHAR_DATA_READ} <= self.notifying:
            self.panel.attach(
                lambda data: self._handlers[CHAR_COMMAND](None, bytearray(data)),
                lambda data: self._handlers[CHAR_DATA_READ](None, bytearray(data)),
            )

    async def stop_notify(self, uuid: str) -> None:
        self.notifying.discard(uuid)

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool) -> None:
        if uuid == CHAR_COMMAND:
            await self.panel.write_command(bytes(data))
        else:
            await self.panel.write_data(bytes(data))

    async def disconnect(self) -> None:
        await self.drop()

    async def drop(self) -> None:
        """Lose the connection, as when the panel goes out of range."""
        if not self.is_connected:
            return
        self.is_connected = False
        await self.panel.close()
        if self._disconnected_callback is not None:
            self._disconnected_callback(self)


class PanelHarness:
    """Creates a fresh fake panel and client for every connection."""

    def __init__(self) -> None:
        self.panel = FakePanel()
        self.clients: list[FakeBleakClient] = []
        self.pair_requests: list[bool] = []
        self.identity_address = IDENTITY_ADDRESS
        self.connect_error: Exception | None = None
        self.configure_panel: Callable[[FakePanel], None] | None = None

    @property
    def client(self) -> FakeBleakClient:
        """Return the most recent client."""
        return self.clients[-1]

    async def establish_connection(
        self,
        _client_class: type,
        device: BLEDevice,
        _name: str,
        disconnected_callback: Callable[[Any], None] | None = None,
        pair: bool = False,
        **_kwargs: Any,
    ) -> FakeBleakClient:
        self.pair_requests.append(pair)
        if self.connect_error is not None:
            raise self.connect_error
        if self.clients:
            # Every connection talks to a freshly booted panel emulation.
            self.panel = FakePanel()
        if self.configure_panel is not None:
            self.configure_panel(self.panel)
        address = self.identity_address if pair else device.address
        client = FakeBleakClient(self.panel, address, disconnected_callback)
        self.clients.append(client)
        return client


@pytest.fixture
def harness() -> Generator[PanelHarness]:
    """Route all connections of the integration to the fake panel."""
    fake = PanelHarness()
    with (
        patch(
            "custom_components.truma_inetx.connection.establish_connection",
            side_effect=fake.establish_connection,
        ),
        patch(
            "custom_components.truma_inetx.connection.close_stale_connections_by_address",
            AsyncMock(),
        ),
    ):
        yield fake


def panel_service_info(
    address: str = IDENTITY_ADDRESS, name: str = LOCAL_NAME
) -> BluetoothServiceInfoBleak:
    """Return an advertisement of the panel."""
    manufacturer_data = {TRUMA_MANUFACTURER_ID: b"\x00\x01"}
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=[],
        source=PROXY_SOURCE,
        device=BLEDevice(address, name, {"path": f"/org/bluez/hci0/dev_{address}"}),
        advertisement=AdvertisementData(
            local_name=name,
            manufacturer_data=manufacturer_data,
            service_data={},
            service_uuids=[],
            tx_power=None,
            rssi=-60,
            platform_data=(),
        ),
        connectable=True,
        time=MONOTONIC_TIME(),
        tx_power=None,
    )


async def settle(hass: HomeAssistant, rounds: int = 300) -> None:
    """Let the panel emulation exchange all pending frames.

    The emulation runs on plain asyncio tasks, which async_block_till_done
    does not wait for, and time may be frozen, so only zero-delay yields are
    used.
    """
    for _ in range(rounds):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry of a paired panel."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PANEL_ID,
        title=f"iNet X Panel {PANEL_ID}",
        data={CONF_ADDRESS: IDENTITY_ADDRESS, CONF_LOCAL_NAME: LOCAL_NAME},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    harness: PanelHarness,
    proxy: FakeProxy,
) -> AsyncGenerator[PanelHarness]:
    """Set up the integration with the panel in range."""
    proxy.advertise()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield harness
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
