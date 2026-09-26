"""Opening and closing a protocol session over Bluetooth."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    close_stale_connections_by_address,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback

from .protocol import BleakLink, InetXError, InetXSession

_LOGGER = logging.getLogger(__name__)

CONNECTION_ERRORS = (*BLEAK_RETRY_EXCEPTIONS, InetXError)
"""Everything that can go wrong while opening or using a session."""


@dataclass(slots=True)
class PanelConnection:
    """A connected client with a started protocol session."""

    client: BleakClientWithServiceCache
    link: BleakLink
    session: InetXSession

    @property
    def address(self) -> str:
        """Return the address the client is connected to.

        After pairing a panel that advertised a random address, this is the
        identity address the bond is stored under.
        """
        return self.client.address

    async def async_close(self) -> None:
        """End the session and release the link cleanly."""
        await self.session.close()
        await self.link.release()


@callback
def async_find_panel(
    hass: HomeAssistant, address: str | None, local_name: str
) -> BLEDevice | None:
    """Return the panel, looked up by address first and by name second.

    The name lookup finds the panel even when the Bluetooth stack does not
    resolve its rotating random address to the bonded identity address.
    """
    if address and (
        device := bluetooth.async_ble_device_from_address(
            hass, address, connectable=True
        )
    ):
        return device
    for service_info in bluetooth.async_discovered_service_info(hass, connectable=True):
        if service_info.name == local_name:
            return service_info.device
    return None


async def async_open_session(
    device: BLEDevice,
    *,
    pair: bool,
    on_update: Callable[[], None] | None = None,
    on_disconnect: Callable[[], None] | None = None,
) -> PanelConnection:
    """Connect, optionally pair, and start a protocol session.

    Pairing has to happen before GATT discovery: the panel drops unbonded
    connections while services are being resolved.
    """
    await close_stale_connections_by_address(device.address)
    client = await establish_connection(
        BleakClientWithServiceCache,
        device,
        device.name or device.address,
        disconnected_callback=(
            None if on_disconnect is None else lambda _client: on_disconnect()
        ),
        pair=pair,
    )
    link = BleakLink(client)
    session = InetXSession(link, on_update)
    try:
        await link.start(session.transport)
        await session.start()
    except BaseException:
        await session.close()
        await link.release()
        raise
    _LOGGER.debug("Session with %s started", client.address)
    return PanelConnection(client, link, session)
