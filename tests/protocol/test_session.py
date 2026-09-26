"""Tests for the protocol session against the emulated panel."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from custom_components.truma_inetx.protocol.const import MessageType
from custom_components.truma_inetx.protocol.errors import (
    InetXTimeoutError,
    InetXUnknownParameterError,
)
from custom_components.truma_inetx.protocol.session import (
    SUBSCRIBED_TOPICS,
    InetXSession,
)
from tests.fake_panel import APP_ADDRESS, FakePanel


@pytest.fixture
async def panel() -> AsyncGenerator[FakePanel]:
    """Return the emulated Combi 6 panel."""
    fake = FakePanel()
    yield fake
    await fake.close()


@pytest.fixture(autouse=True)
def short_discovery_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let tests with silent nodes finish quickly."""
    monkeypatch.setattr(
        "custom_components.truma_inetx.protocol.session.DISCOVERY_TIMEOUT", 0.2
    )


def connect(panel: FakePanel) -> InetXSession:
    """Create a session wired to the emulated panel."""
    session = InetXSession(panel)
    panel.attach(session.transport.handle_command, session.transport.handle_data)
    return session


async def test_start_registers_discovers_and_subscribes(panel: FakePanel) -> None:
    """Start leaves the session with both nodes and a live subscription."""
    session = connect(panel)

    await session.start()

    assert session.address == APP_ADDRESS
    assert set(session.state.nodes) == {0x0101, 0x0201}
    assert session.state.owner("RoomClimate") == 0x0101
    assert session.state.owner("WaterHeating") == 0x0201
    assert session.state.nodes[0x0201].identity("Name") == "Combi 6"
    subscriptions = [
        frame.body["tn"]
        for frame in panel.received
        if frame.message_type == MessageType.SUBSCRIBE
    ]
    assert subscriptions == [list(SUBSCRIBED_TOPICS)]
    await session.close()


async def test_start_fails_without_panel_answer(panel: FakePanel) -> None:
    """The panel node is mandatory."""
    panel.silent_nodes.add(0x0101)
    session = connect(panel)

    with pytest.raises(InetXTimeoutError):
        await session.start()
    await session.close()


async def test_start_succeeds_without_heater_answer(panel: FakePanel) -> None:
    """A system without a node at the default heater address still starts."""
    panel.silent_nodes.add(0x0201)
    session = connect(panel)

    await session.start()

    assert set(session.state.nodes) == {0x0101}
    await session.close()


async def test_node_publishing_later_is_discovered(panel: FakePanel) -> None:
    """A node first seen through an info message gets its schema read."""
    panel.silent_nodes.add(0x0201)
    session = connect(panel)
    await session.start()
    panel.silent_nodes.clear()

    panel.change("AirHeating", "Temp", 222)

    assert await session.wait_for(
        "WaterHeating", "Mode", lambda value: value is not None, timeout=2
    )
    assert session.state.value("AirHeating", "Temp") == 222
    await session.close()


async def test_write_is_routed_to_topic_owner(panel: FakePanel) -> None:
    """Writes go to the node that owns the topic and update the state."""
    updates: list[None] = []
    session = InetXSession(panel, lambda: updates.append(None))
    panel.attach(session.transport.handle_command, session.transport.handle_data)
    await session.start()

    await session.write("RoomClimate", "Mode", 3)
    await session.write("AirHeating", "TgtTemp", 225)

    assert panel.writes == [
        (0x0101, "RoomClimate", "Mode", 3),
        (0x0201, "AirHeating", "TgtTemp", 225),
    ]
    assert session.state.value("AirHeating", "TgtTemp") == 225
    assert updates
    await session.close()


async def test_write_without_echo_keeps_written_value(panel: FakePanel) -> None:
    """A write the node does not echo is still reflected in the state."""
    panel.echo_writes = False
    session = connect(panel)
    await session.start()

    await session.write("AirCirculation", "FanLevel", 7)

    assert session.state.value("AirCirculation", "FanLevel") == 7
    await session.close()


async def test_write_of_unknown_parameter_fails(panel: FakePanel) -> None:
    """Parameters the system does not report cannot be written."""
    session = connect(panel)
    await session.start()

    with pytest.raises(InetXUnknownParameterError):
        await session.write("AirCooling", "TgtTemp", 200)
    await session.close()


async def test_wait_for_sees_panel_changes(panel: FakePanel) -> None:
    """Values changed at the panel reach waiting callers."""
    session = connect(panel)
    await session.start()

    await session.write("WaterHeating", "Active", 1)

    assert await session.wait_for("WaterHeating", "Active", lambda v: v == 2, 1)
    assert await session.wait_for("WaterHeating", "Active", lambda v: v == 1, 1)
    assert not await session.wait_for("WaterHeating", "Active", lambda v: v == 0, 0.1)
    await session.close()
