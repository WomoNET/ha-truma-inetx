"""Encoding and decoding of protocol frames.

A frame is a 16-byte header followed by a payload::

    0-1   destination address (uint16 LE)
    2-3   source address      (uint16 LE)
    4-5   size                (uint16 LE): 9 + payload length
    6     control type
    7-15  segment header      (all zero for unsegmented frames)
    16-   payload

Message broker payloads start with a message type and a correlation ID,
followed by a CBOR document.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any

import cbor2

from .const import FRAME_HEADER_SIZE, FRAME_SIZE_OFFSET, ControlType
from .errors import InetXProtocolError

_HEADER = struct.Struct("<HHHB9x")
_SEGMENT_FLAGS_OFFSET = 7
_BODY_OFFSET = 2


@dataclass(frozen=True, slots=True)
class Frame:
    """A decoded frame with a two-byte message header and a CBOR body."""

    destination: int
    source: int
    control: int
    message_type: int
    correlation_id: int
    body: Any


def encode_frame(
    *,
    destination: int,
    source: int,
    control: ControlType,
    message_type: int,
    correlation_id: int,
    body: Any,
) -> bytes:
    """Build the bytes of an unsegmented frame."""
    payload = bytes([message_type, correlation_id]) + cbor2.dumps(body)
    header = _HEADER.pack(
        destination, source, len(payload) + FRAME_SIZE_OFFSET, control
    )
    return header + payload


def decode_frame(data: bytes) -> Frame:
    """Decode a received frame.

    Raises InetXProtocolError for truncated, segmented or undecodable frames.
    Segmented frames have not been observed on a real panel, so they are
    rejected instead of being decoded incorrectly.
    """
    if len(data) < FRAME_HEADER_SIZE + _BODY_OFFSET:
        raise InetXProtocolError(f"Frame too short: {len(data)} bytes")
    destination, source, size, control = _HEADER.unpack_from(data)
    if data[_SEGMENT_FLAGS_OFFSET] != 0:
        raise InetXProtocolError(
            f"Segmented frames are not supported (flags 0x{data[7]:02X})"
        )
    # Some frames carry trailing padding, so the size field is authoritative.
    payload_length = size - FRAME_SIZE_OFFSET
    payload = data[FRAME_HEADER_SIZE : FRAME_HEADER_SIZE + payload_length]
    if payload_length < _BODY_OFFSET or len(payload) < payload_length:
        raise InetXProtocolError(
            f"Frame size field {size} does not match {len(data)} received bytes"
        )
    body_bytes = payload[_BODY_OFFSET:]
    try:
        body = cbor2.loads(body_bytes) if body_bytes else None
    except cbor2.CBORDecodeError as err:
        raise InetXProtocolError(f"Invalid CBOR body: {err}") from err
    return Frame(
        destination=destination,
        source=source,
        control=control,
        message_type=payload[0],
        correlation_id=payload[1],
        body=body,
    )
