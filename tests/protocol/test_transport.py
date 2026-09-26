"""Tests for the transport handshake."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.truma_inetx.protocol.errors import InetXTransportError
from custom_components.truma_inetx.protocol.transport import Transport


class RecordingLink:
    """Link that records writes and lets the test play the panel."""

    max_data_write_size = 4

    def __init__(self) -> None:
        self.commands: list[bytes] = []
        self.data: list[bytes] = []
        self.transport: Transport | None = None
        self.ready_reply: bytes | None = b"\x81\x00"
        self.ack_reply: bytes | None = b"\xf0\x01"

    async def write_command(self, data: bytes) -> None:
        self.commands.append(data)
        if data[0] == 0x01 and self.ready_reply is not None:
            self._reply(self.ready_reply)

    async def write_data(self, data: bytes) -> None:
        self.data.append(data)
        announced = int.from_bytes(self.commands[-1][1:3], "little")
        if sum(map(len, self.data)) == announced and self.ack_reply is not None:
            self._reply(self.ack_reply)

    def _reply(self, data: bytes) -> None:
        assert self.transport is not None
        asyncio.get_running_loop().call_soon(self.transport.handle_command, data)


@pytest.fixture
def link() -> RecordingLink:
    """Return a link whose panel side accepts every transfer."""
    return RecordingLink()


def make_transport(link: RecordingLink, frames: list[bytes] | None = None) -> Transport:
    """Create a transport connected to the recording link."""
    transport = Transport(
        link, (frames if frames is not None else []).append, timeout=0.2
    )
    link.transport = transport
    return transport


async def test_send_announces_length_and_chunks_data(link: RecordingLink) -> None:
    """A frame is announced with its length and written in link-sized chunks."""
    transport = make_transport(link)

    await transport.send(bytes(range(10)))

    assert link.commands == [b"\x01\x0a\x00"]
    assert link.data == [bytes([0, 1, 2, 3]), bytes([4, 5, 6, 7]), bytes([8, 9])]


async def test_send_fails_when_panel_is_not_ready(link: RecordingLink) -> None:
    """Without a ready reply no data is written and the send fails."""
    link.ready_reply = None
    transport = make_transport(link)

    with pytest.raises(InetXTransportError):
        await transport.send(b"\x00")
    assert link.data == []


async def test_send_fails_when_panel_refuses(link: RecordingLink) -> None:
    """A non-zero ready status is a refusal."""
    link.ready_reply = b"\x81\x02"
    transport = make_transport(link)

    with pytest.raises(InetXTransportError, match="refused"):
        await transport.send(b"\x00")


async def test_send_fails_when_panel_rejects_data(link: RecordingLink) -> None:
    """An acknowledgement with an error status fails the send."""
    link.ack_reply = b"\xf0\x03"
    transport = make_transport(link)

    with pytest.raises(InetXTransportError, match="rejected"):
        await transport.send(b"\x00")


async def test_send_tolerates_missing_acknowledgement(link: RecordingLink) -> None:
    """A missing acknowledgement does not fail the send."""
    link.ack_reply = None
    transport = make_transport(link)

    await transport.send(b"\x00")


async def test_announced_frame_is_reassembled_and_acknowledged(
    link: RecordingLink,
) -> None:
    """An announced frame split over notifications is delivered once, then acked."""
    frames: list[bytes] = []
    transport = make_transport(link, frames)

    transport.handle_command(b"\x83\x05\x00")
    transport.handle_data(b"\x01\x02\x03")
    transport.handle_data(b"\x04\x05")
    await asyncio.sleep(0)

    assert frames == [b"\x01\x02\x03\x04\x05"]
    assert link.commands == [b"\x03\x00", b"\xf0\x01"]
    await transport.close()


async def test_unannounced_notification_is_one_frame(link: RecordingLink) -> None:
    """Without an announcement every notification is a complete frame."""
    frames: list[bytes] = []
    transport = make_transport(link, frames)

    transport.handle_data(b"\x01\x02")
    transport.handle_data(b"\x03")
    await asyncio.sleep(0)

    assert frames == [b"\x01\x02", b"\x03"]


async def test_unknown_command_messages_are_ignored(link: RecordingLink) -> None:
    """Unknown or stray command messages neither fail nor produce replies."""
    transport = make_transport(link)

    transport.handle_command(b"")
    transport.handle_command(b"\x42\x00")
    transport.handle_command(b"\xf0\x01")
    await asyncio.sleep(0)

    assert link.commands == []
