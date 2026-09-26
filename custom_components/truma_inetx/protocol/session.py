"""Protocol session with one panel over an established Bluetooth link."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import logging
from typing import Any

from .const import (
    ADDRESS_DEFAULT_HEATER,
    ADDRESS_MESSAGE_BROKER,
    ADDRESS_PANEL,
    MAX_TOPICS_PER_SUBSCRIPTION,
    ControlType,
    MessageType,
)
from .errors import InetXError, InetXTimeoutError, InetXUnknownParameterError
from .frame import Frame, decode_frame
from .messages import (
    discovery_request,
    is_last_message,
    parameter_reports,
    registered_address,
    registration_request,
    subscribe_request,
    write_request,
)
from .model import SystemState
from .transport import GattLink, Transport

_LOGGER = logging.getLogger(__name__)

REGISTRATION_TIMEOUT = 10.0
DISCOVERY_TIMEOUT = 30.0
SUBSCRIPTION_BATCH_DELAY = 0.25

TOPIC_ROOM_CLIMATE = "RoomClimate"
TOPIC_AIR_HEATING = "AirHeating"

SUBSCRIBED_TOPICS = (
    TOPIC_ROOM_CLIMATE,
    TOPIC_AIR_HEATING,
    "AirCooling",
    "AirCirculation",
    "WaterHeating",
    "EnergySrc",
    "Temperature",
    "System",
)
"""Topics with live updates. Topics a system lacks are simply never sent."""

_INITIAL_NODES = (
    (ADDRESS_PANEL, TOPIC_ROOM_CLIMATE),
    (ADDRESS_DEFAULT_HEATER, TOPIC_AIR_HEATING),
)
"""Nodes asked for their schema at start, with a topic each node is known to own.

The panel is mandatory. Other nodes are found when they publish their first
info message, so a system without a node at the default heater address only
costs one discovery timeout.
"""

_FIRST_CORRELATION_ID = 0x60


class InetXSession:
    """Registers with the panel, mirrors its state and writes parameters."""

    def __init__(
        self,
        link: GattLink,
        on_update: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the session; on_update is called after every state change."""
        self.state = SystemState()
        self.transport = Transport(link, self._handle_frame)
        self.address: int | None = None
        self._on_update = on_update
        self._registration: asyncio.Future[int] | None = None
        self._discoveries: dict[tuple[int, int], asyncio.Future[None]] = {}
        self._discovered_nodes: set[int] = set()
        self._correlation_id = _FIRST_CORRELATION_ID
        self._changed = asyncio.Event()
        self._started = False
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        """Register, read the schema of the known nodes and subscribe to updates."""
        await self._register()
        for node, topic in _INITIAL_NODES:
            try:
                await self.discover_node(node, topic)
            except InetXTimeoutError:
                if node == ADDRESS_PANEL:
                    raise
                _LOGGER.debug("No node answered at 0x%04X", node)
        await self._subscribe(SUBSCRIBED_TOPICS)
        self._started = True

    async def close(self) -> None:
        """Stop background work; the link itself is closed by its owner."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.transport.close()

    async def discover_node(self, node: int, topic: str) -> None:
        """Read the full parameter schema of a node."""
        source = self._require_address()
        correlation_id = self._next_correlation_id()
        done = asyncio.get_running_loop().create_future()
        self._discoveries[(node, correlation_id)] = done
        try:
            await self.transport.send(
                discovery_request(
                    source=source,
                    destination=node,
                    topic=topic,
                    correlation_id=correlation_id,
                )
            )
            async with asyncio.timeout(DISCOVERY_TIMEOUT):
                await done
        except TimeoutError as err:
            raise InetXTimeoutError(f"Node 0x{node:04X} did not answer") from err
        finally:
            self._discoveries.pop((node, correlation_id), None)
        self._discovered_nodes.add(node)

    async def write(
        self, topic: str, name: str, value: Any, *, assume_written: bool = True
    ) -> None:
        """Write a parameter to the node that owns its topic.

        Nodes do not echo every accepted write, so by default the written value
        is shown until the node reports otherwise. Callers waiting for the
        node's own report of a transition pass assume_written=False.
        """
        parameter = self.state.parameter(topic, name)
        if parameter is None:
            raise InetXUnknownParameterError(f"{topic}/{name} is not reported")
        await self.transport.send(
            write_request(
                source=self._require_address(),
                destination=parameter.node,
                topic=topic,
                parameter=name,
                value=value,
            )
        )
        if assume_written and self.state.set_value(topic, name, value):
            self._state_changed()

    async def wait_for(
        self, topic: str, name: str, predicate: Callable[[Any], bool], timeout: float
    ) -> bool:
        """Wait until a parameter value satisfies predicate; False on timeout."""
        try:
            async with asyncio.timeout(timeout):
                while not predicate(self.state.value(topic, name)):
                    await self._changed.wait()
        except TimeoutError:
            return False
        return True

    async def _register(self) -> None:
        self._registration = asyncio.get_running_loop().create_future()
        try:
            await self.transport.send(registration_request())
            async with asyncio.timeout(REGISTRATION_TIMEOUT):
                self.address = await self._registration
        except TimeoutError as err:
            raise InetXTimeoutError("Panel did not complete the registration") from err
        finally:
            self._registration = None
        _LOGGER.debug("Registered with address 0x%04X", self.address)

    async def _subscribe(self, topics: tuple[str, ...]) -> None:
        source = self._require_address()
        for start in range(0, len(topics), MAX_TOPICS_PER_SUBSCRIPTION):
            if start:
                await asyncio.sleep(SUBSCRIPTION_BATCH_DELAY)
            await self.transport.send(
                subscribe_request(
                    source=source,
                    topics=topics[start : start + MAX_TOPICS_PER_SUBSCRIPTION],
                    correlation_id=self._next_correlation_id(),
                )
            )

    def _handle_frame(self, data: bytes) -> None:
        try:
            frame = decode_frame(data)
        except InetXError as err:
            _LOGGER.debug("Dropping frame %s: %s", data.hex(" "), err)
            return
        if frame.control == ControlType.REGISTRATION:
            self._handle_registration(frame)
        elif frame.control == ControlType.MESSAGE_BROKER:
            self._handle_message(frame)

    def _handle_registration(self, frame: Frame) -> None:
        address = registered_address(frame)
        if address is None or self._registration is None or self._registration.done():
            return
        self._registration.set_result(address)

    def _handle_message(self, frame: Frame) -> None:
        if frame.source in (self.address, ADDRESS_MESSAGE_BROKER):
            return
        changed = False
        for report in parameter_reports(frame):
            changed |= self.state.apply(frame.source, report)
        if is_last_message(frame):
            done = self._discoveries.get((frame.source, frame.correlation_id))
            if done is not None and not done.done():
                done.set_result(None)
        if frame.message_type == MessageType.INFO and isinstance(frame.body, dict):
            self._discover_new_node(frame.source, frame.body.get("tn"))
        if changed:
            self._state_changed()

    def _discover_new_node(self, node: int, topic: Any) -> None:
        if not self._started or node in self._discovered_nodes or not topic:
            return
        # Marked right away so a burst of info messages starts one discovery.
        self._discovered_nodes.add(node)
        self._spawn(self._discover_in_background(node, str(topic)))

    async def _discover_in_background(self, node: int, topic: str) -> None:
        try:
            await self.discover_node(node, topic)
        except InetXError as err:
            _LOGGER.debug("Discovery of node 0x%04X failed: %s", node, err)
            self._discovered_nodes.discard(node)

    def _state_changed(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()
        if self._on_update is not None:
            self._on_update()

    def _next_correlation_id(self) -> int:
        correlation_id = self._correlation_id
        self._correlation_id = correlation_id % 0xFF + 1
        return correlation_id

    def _require_address(self) -> int:
        if self.address is None:
            raise InetXError("Session is not registered")
        return self.address

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
