"""Transport handshake around every frame.

Every frame is announced with its length and acknowledged after transfer.
Both directions mirror each other on the command characteristic::

    app -> panel                     panel -> app
    ---------------------------      ---------------------------
    app:   01 <len16>  (announce)    panel: 83 <len16>  (announce)
    panel: 81 00       (ready)       app:   03 00       (ready)
    app:   <frame> on DATA_WRITE     panel: <frame> on DATA_READ
    panel: F0 <status> (ack)         app:   F0 01       (ack)

The panel stalls if an announcement or a frame is not answered, so incoming
traffic is answered from background tasks without waiting for the caller.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import logging
from typing import Any, Protocol

from .const import DataAckStatus, TransportOpcode
from .errors import InetXTransportError

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0
_LENGTH_BYTES = 2
_RECEIVE_READY = bytes([TransportOpcode.RECEIVE_READY, 0x00])
_RECEIVE_ACK = bytes([TransportOpcode.DATA_ACK, DataAckStatus.TRANSFER_COMPLETED])
_SEND_READY_OK = 0x00


class GattLink(Protocol):
    """Access to the two writable characteristics of the panel."""

    @property
    def max_data_write_size(self) -> int:
        """Return the largest chunk a single data write can carry."""

    async def write_command(self, data: bytes) -> None:
        """Write to the command characteristic (with response)."""

    async def write_data(self, data: bytes) -> None:
        """Write to the data characteristic (without response)."""


class Transport:
    """Transfers frames to and from the panel."""

    def __init__(
        self,
        link: GattLink,
        on_frame: Callable[[bytes], None],
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the transport; on_frame receives every complete frame."""
        self._link = link
        self._on_frame = on_frame
        self._timeout = timeout
        self._send_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._send_ready: asyncio.Future[int] | None = None
        self._send_ack: asyncio.Future[int] | None = None
        self._incoming_length: int | None = None
        self._incoming = bytearray()
        self._tasks: set[asyncio.Task[None]] = set()

    def handle_command(self, data: bytes) -> None:
        """Process a notification from the command characteristic."""
        if not data:
            return
        opcode = data[0]
        if opcode == TransportOpcode.SEND_READY and len(data) >= 2:
            _resolve(self._send_ready, data[1])
        elif opcode == TransportOpcode.DATA_ACK and len(data) >= 2:
            _resolve(self._send_ack, data[1])
        elif opcode == TransportOpcode.RECEIVE_REQUEST and len(data) > _LENGTH_BYTES:
            self._incoming_length = int.from_bytes(data[1:3], "little")
            self._incoming.clear()
            self._spawn(self._write_command(_RECEIVE_READY))
        else:
            _LOGGER.debug("Ignoring command message %s", data.hex(" "))

    def handle_data(self, data: bytes) -> None:
        """Process a notification from the data characteristic."""
        self._incoming.extend(data)
        expected = self._incoming_length
        if expected is not None and len(self._incoming) < expected:
            return
        frame = bytes(self._incoming if expected is None else self._incoming[:expected])
        self._incoming.clear()
        self._incoming_length = None
        self._spawn(self._write_command(_RECEIVE_ACK))
        self._on_frame(frame)

    async def send(self, frame: bytes) -> None:
        """Transfer one frame; raise InetXTransportError if the panel refuses."""
        async with self._send_lock:
            loop = asyncio.get_running_loop()
            self._send_ready = loop.create_future()
            self._send_ack = loop.create_future()
            try:
                await self._transfer(frame, self._send_ready, self._send_ack)
            finally:
                self._send_ready = None
                self._send_ack = None

    async def close(self) -> None:
        """Cancel pending background replies."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _transfer(
        self, frame: bytes, ready: asyncio.Future[int], ack: asyncio.Future[int]
    ) -> None:
        announce = bytes([TransportOpcode.SEND_REQUEST]) + len(frame).to_bytes(
            _LENGTH_BYTES, "little"
        )
        await self._write_command(announce)
        status = await self._wait(ready, "ready for the transfer")
        if status != _SEND_READY_OK:
            raise InetXTransportError(f"Panel refused the transfer (0x{status:02X})")

        chunk_size = max(1, self._link.max_data_write_size)
        for offset in range(0, len(frame), chunk_size):
            await self._link.write_data(frame[offset : offset + chunk_size])

        try:
            status = await self._wait(ack, "acknowledge the transfer")
        except InetXTransportError:
            # The acknowledgement is occasionally missing although the panel
            # processes the frame; the caller observes the effect instead.
            _LOGGER.debug("No acknowledgement for a %d byte frame", len(frame))
            return
        if status != DataAckStatus.TRANSFER_COMPLETED:
            raise InetXTransportError(f"Panel rejected the frame (0x{status:02X})")

    async def _wait(self, future: asyncio.Future[int], action: str) -> int:
        try:
            async with asyncio.timeout(self._timeout):
                return await future
        except TimeoutError as err:
            raise InetXTransportError(f"Panel did not {action} in time") from err

    async def _write_command(self, data: bytes) -> None:
        async with self._command_lock:
            await self._link.write_command(data)

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (err := task.exception()) is not None:
            _LOGGER.debug("Transport reply failed: %s", err)


def _resolve(future: asyncio.Future[int] | None, status: int) -> None:
    if future is None or future.done():
        _LOGGER.debug("Unexpected transport status 0x%02X", status)
        return
    future.set_result(status)
