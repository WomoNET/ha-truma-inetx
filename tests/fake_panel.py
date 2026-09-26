"""Emulation of an iNet X panel for tests.

The emulation speaks the real transport handshake and answers with parameter
descriptions recorded from a panel with a Truma Combi 6 heater. Behaviour
seen on that system is reproduced where tests depend on it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from pathlib import Path
from typing import Any

from custom_components.truma_inetx.protocol.const import (
    ADDRESS_BROADCAST,
    ADDRESS_MESSAGE_BROKER,
    ControlType,
    MessageType,
    RegistrationType,
)
from custom_components.truma_inetx.protocol.frame import (
    Frame,
    decode_frame,
    encode_frame,
)

SCHEMA_FILE = Path(__file__).parent / "fixtures" / "combi6_schema.json"
APP_ADDRESS = 0x0501
_HANDSHAKE_TIMEOUT = 2.0


def load_schema() -> dict[int, list[dict[str, Any]]]:
    """Return the recorded Combi 6 schema keyed by node address."""
    raw = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    return {int(address, 16): parameters for address, parameters in raw.items()}


class FakePanel:
    """Panel side of the GATT link."""

    max_data_write_size = 20
    """Small on purpose, so every test also exercises chunking."""

    def __init__(self, schema: dict[int, list[dict[str, Any]]] | None = None) -> None:
        """Initialize the panel with the recorded Combi 6 schema by default."""
        self.nodes: dict[int, dict[tuple[str, str], dict[str, Any]]] = {
            address: {(p["tn"], p["pn"]): dict(p) for p in parameters}
            for address, parameters in (schema or load_schema()).items()
        }
        self.received: list[Frame] = []
        self.writes: list[tuple[int, str, str, Any]] = []
        self.silent_nodes: set[int] = set()
        self.echo_writes = True
        self.refuse_transfers = False
        self.water_ready_delay = 0.05
        self._on_command: Callable[[bytes], None] | None = None
        self._on_data: Callable[[bytes], None] | None = None
        self._incoming_length = 0
        self._incoming = bytearray()
        self._outgoing: asyncio.Queue[bytes] = asyncio.Queue()
        self._receive_ready = asyncio.Event()
        self._receive_ack = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()

    def attach(
        self, on_command: Callable[[bytes], None], on_data: Callable[[bytes], None]
    ) -> None:
        """Start delivering notifications to the app side."""
        self._on_command = on_command
        self._on_data = on_data
        self._spawn(self._send_loop())

    async def close(self) -> None:
        """Stop the emulation."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._on_command = self._on_data = None

    async def write_command(self, data: bytes) -> None:
        """Receive a write to the command characteristic."""
        if data[0] == 0x01:
            self._incoming_length = int.from_bytes(data[1:3], "little")
            self._incoming.clear()
            self._notify_command(bytes([0x81, 0x01 if self.refuse_transfers else 0x00]))
        elif data == b"\x03\x00":
            self._receive_ready.set()
        elif data == b"\xf0\x01":
            self._receive_ack.set()

    async def write_data(self, data: bytes) -> None:
        """Receive a write to the data characteristic."""
        self._incoming.extend(data)
        if len(self._incoming) < self._incoming_length:
            return
        frame = decode_frame(bytes(self._incoming))
        self._incoming.clear()
        self._notify_command(b"\xf0\x01")
        self.received.append(frame)
        self._handle(frame)

    def value(self, topic: str, name: str) -> Any:
        """Return the current value of a parameter on any node."""
        return self._find(topic, name)[1]["v"]

    def change(self, topic: str, name: str, value: Any) -> None:
        """Change a value as if it was operated at the panel and broadcast it."""
        node, parameter = self._find(topic, name)
        parameter["v"] = value
        self._queue(node, ADDRESS_BROADCAST, MessageType.INFO, 0, parameter)

    def _handle(self, frame: Frame) -> None:
        if frame.control == ControlType.REGISTRATION:
            self._queue(
                ADDRESS_MESSAGE_BROKER,
                ADDRESS_BROADCAST,
                RegistrationType.RESPONSE,
                frame.correlation_id,
                {"pv": [5, 1], "addr": APP_ADDRESS},
                control=ControlType.REGISTRATION,
            )
        elif frame.message_type == MessageType.PARAMETER_DISCOVERY:
            self._answer_discovery(frame)
        elif frame.message_type == MessageType.SUBSCRIBE:
            self._queue(
                ADDRESS_MESSAGE_BROKER,
                frame.source,
                MessageType.SUBSCRIBE_RESPONSE,
                frame.correlation_id,
                frame.body,
            )
        elif frame.message_type == MessageType.WRITE:
            self._apply_write(frame.destination, frame.body)

    def _answer_discovery(self, frame: Frame) -> None:
        node = frame.destination
        if node not in self.nodes or node in self.silent_nodes:
            return
        by_topic: dict[str, list[dict[str, Any]]] = {}
        for (topic, _), parameter in self.nodes[node].items():
            by_topic.setdefault(topic, []).append(parameter)
        for topic, parameters in by_topic.items():
            body = {"avail": 1, "topics": [{"tn": topic, "parameters": parameters}]}
            self._queue(
                node,
                frame.source,
                MessageType.PARAMETER_DISCOVERY_RESPONSE,
                frame.correlation_id,
                body,
            )
        self._queue(
            node,
            frame.source,
            MessageType.PARAMETER_DISCOVERY_RESPONSE,
            frame.correlation_id,
            {"LastMessage": 1},
        )

    def _apply_write(self, node: int, body: dict[str, Any]) -> None:
        topic, name, value = body["tn"], body["pn"], body["v"]
        self.writes.append((node, topic, name, value))
        parameter = self.nodes.get(node, {}).get((topic, name))
        if parameter is None or not self.echo_writes:
            return
        if (topic, name, value) == ("WaterHeating", "Active", 1):
            # The heater reports "ready" (2) first and "active" (1) later.
            self.change(topic, name, 2)
            self._spawn(self._later(self.water_ready_delay, topic, name, 1))
            return
        self.change(topic, name, value)
        if (topic, name, value) == ("AirCirculation", "Active", 0):
            self.change("RoomClimate", "Mode", 0)

    async def _later(self, delay: float, topic: str, name: str, value: Any) -> None:
        await asyncio.sleep(delay)
        self.change(topic, name, value)

    def _find(self, topic: str, name: str) -> tuple[int, dict[str, Any]]:
        for node, parameters in self.nodes.items():
            if (topic, name) in parameters:
                return node, parameters[(topic, name)]
        raise KeyError(f"{topic}/{name}")

    def _queue(
        self,
        source: int,
        destination: int,
        message_type: int,
        correlation_id: int,
        body: Any,
        *,
        control: ControlType = ControlType.MESSAGE_BROKER,
    ) -> None:
        self._outgoing.put_nowait(
            encode_frame(
                destination=destination,
                source=source,
                control=control,
                message_type=message_type,
                correlation_id=correlation_id,
                body=body,
            )
        )

    async def _send_loop(self) -> None:
        while True:
            frame = await self._outgoing.get()
            self._receive_ready.clear()
            self._receive_ack.clear()
            self._notify_command(bytes([0x83]) + len(frame).to_bytes(2, "little"))
            async with asyncio.timeout(_HANDSHAKE_TIMEOUT):
                await self._receive_ready.wait()
            for offset in range(0, len(frame), self.max_data_write_size):
                chunk = frame[offset : offset + self.max_data_write_size]
                if self._on_data is not None:
                    self._on_data(chunk)
            async with asyncio.timeout(_HANDSHAKE_TIMEOUT):
                await self._receive_ack.wait()

    def _notify_command(self, data: bytes) -> None:
        if self._on_command is not None:
            asyncio.get_running_loop().call_soon(self._on_command, data)

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
