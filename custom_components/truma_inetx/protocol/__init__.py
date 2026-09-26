"""Truma iNet X Bluetooth protocol.

This package has no Home Assistant dependencies so it can be tested on its
own and moved into a standalone library later.
"""

from .ble import BleakLink
from .const import ADDRESS_PANEL, TRUMA_MANUFACTURER_ID
from .errors import (
    InetXError,
    InetXProtocolError,
    InetXTimeoutError,
    InetXTransportError,
    InetXUnknownParameterError,
)
from .model import (
    TOPIC_IDENTIFY,
    EnumOption,
    Node,
    Parameter,
    SystemState,
    describe_parameter,
)
from .session import InetXSession

__all__ = [
    "ADDRESS_PANEL",
    "TOPIC_IDENTIFY",
    "TRUMA_MANUFACTURER_ID",
    "BleakLink",
    "EnumOption",
    "InetXError",
    "InetXProtocolError",
    "InetXSession",
    "InetXTimeoutError",
    "InetXTransportError",
    "InetXUnknownParameterError",
    "Node",
    "Parameter",
    "SystemState",
    "describe_parameter",
]
