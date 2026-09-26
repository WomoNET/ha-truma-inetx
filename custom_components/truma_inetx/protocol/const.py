"""Constants of the Truma iNet X Bluetooth protocol."""

from __future__ import annotations

from enum import IntEnum
from typing import Final

_UUID_SUFFIX: Final = "-f3b2-11e8-8eb2-f2801f1b9fd1"

CHAR_COMMAND: Final = f"fc314001{_UUID_SUFFIX}"
"""Transport handshake messages (write with response, notify)."""

CHAR_DATA_WRITE: Final = f"fc314002{_UUID_SUFFIX}"
"""Outgoing protocol frames (write without response)."""

CHAR_DATA_READ: Final = f"fc314003{_UUID_SUFFIX}"
"""Incoming protocol frames (notify).

The fourth characteristic (fc314004) is never subscribed: enabling its
notifications breaks the transport on the panel.
"""

TRUMA_MANUFACTURER_ID: Final = 0x0C73
PROTOCOL_VERSION: Final = (5, 1)

ADDRESS_MESSAGE_BROKER: Final = 0x0000
ADDRESS_PANEL: Final = 0x0101
ADDRESS_DEFAULT_HEATER: Final = 0x0201
ADDRESS_UNREGISTERED_APP: Final = 0x0500
ADDRESS_BROADCAST: Final = 0xFFFF

FRAME_HEADER_SIZE: Final = 16
"""Destination, source, size, control type and the 9-byte segment header."""

FRAME_SIZE_OFFSET: Final = 9
"""The size field counts the segment header plus the payload."""

MAX_TOPICS_PER_SUBSCRIPTION: Final = 10

LAST_MESSAGE_KEY: Final = "LastMessage"
"""Marks the final frame of a parameter discovery response."""


class ControlType(IntEnum):
    """Frame control type (byte 6 of the frame header)."""

    REGISTRATION = 0x01
    MESSAGE_BROKER = 0x03


class RegistrationType(IntEnum):
    """First payload byte of a registration frame."""

    REQUEST = 0x01
    RESPONSE = 0x02


class MessageType(IntEnum):
    """First payload byte of a message broker frame."""

    INFO = 0x00
    WRITE = 0x01
    SUBSCRIBE = 0x02
    PARAMETER_DISCOVERY = 0x04
    SUBSCRIBE_RESPONSE = 0x82
    PARAMETER_DISCOVERY_RESPONSE = 0x84


class TransportOpcode(IntEnum):
    """First byte of a message on the command characteristic."""

    SEND_REQUEST = 0x01
    """App announces an outgoing frame: opcode + length (uint16 LE)."""

    RECEIVE_READY = 0x03
    """App accepts an incoming frame announced by the panel."""

    SEND_READY = 0x81
    """Panel accepts the frame the app announced."""

    RECEIVE_REQUEST = 0x83
    """Panel announces an incoming frame: opcode + length (uint16 LE)."""

    DATA_ACK = 0xF0
    """Transfer acknowledgement: opcode + status."""


class DataAckStatus(IntEnum):
    """Status byte of a data acknowledgement."""

    TRANSFER_COMPLETED = 0x01
    NOT_READY = 0x02
    TOO_MUCH_DATA = 0x03
    TIMEOUT_ON_SEND = 0x04
    INTERNAL_ERROR = 0x05
    TIMEOUT_ON_RECEIVE = 0x06


class ParameterType(IntEnum):
    """Known values of the ``type`` field of a parameter description."""

    INTEGER = 1
    ENUM = 2
    STRING = 4
    TEMPERATURE = 10
    """Integer in tenths of a degree Celsius."""
