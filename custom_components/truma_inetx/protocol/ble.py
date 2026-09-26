"""GATT link to the panel on top of a connected bleak client."""

from __future__ import annotations

import contextlib
import logging

from bleak import BleakClient
from bleak.exc import BleakError

from .const import CHAR_COMMAND, CHAR_DATA_READ, CHAR_DATA_WRITE
from .transport import Transport

_LOGGER = logging.getLogger(__name__)

_ATT_WRITE_OVERHEAD = 3
_DEFAULT_MTU = 23


class BleakLink:
    """Connects a Transport to the characteristics of a bleak client."""

    def __init__(self, client: BleakClient) -> None:
        """Initialize the link for an already connected client."""
        self._client = client

    @property
    def max_data_write_size(self) -> int:
        """Return the largest chunk a single data write can carry."""
        characteristic = self._client.services.get_characteristic(CHAR_DATA_WRITE)
        if characteristic is None:
            return _DEFAULT_MTU - _ATT_WRITE_OVERHEAD
        return characteristic.max_write_without_response_size

    async def start(self, transport: Transport) -> None:
        """Enable the notifications the transport needs.

        The data characteristic is only enabled after the command
        characteristic, matching the order the panel is known to accept.
        """
        await self._client.start_notify(
            CHAR_COMMAND, lambda _, data: transport.handle_command(bytes(data))
        )
        await self._client.start_notify(
            CHAR_DATA_READ, lambda _, data: transport.handle_data(bytes(data))
        )

    async def write_command(self, data: bytes) -> None:
        """Write to the command characteristic (with response)."""
        await self._client.write_gatt_char(CHAR_COMMAND, data, response=True)

    async def write_data(self, data: bytes) -> None:
        """Write to the data characteristic (without response)."""
        await self._client.write_gatt_char(CHAR_DATA_WRITE, data, response=False)

    async def release(self) -> None:
        """Stop notifications and disconnect.

        Dropping the link with notifications still enabled leaves the panel
        ignoring its own buttons for a while, so the release is explicit.
        """
        for characteristic in (CHAR_DATA_READ, CHAR_COMMAND):
            with contextlib.suppress(BleakError, EOFError, TimeoutError):
                if self._client.is_connected:
                    await self._client.stop_notify(characteristic)
        try:
            await self._client.disconnect()
        except (BleakError, EOFError, TimeoutError) as err:
            _LOGGER.debug("Disconnect failed: %s", err)
