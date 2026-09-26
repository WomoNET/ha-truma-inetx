"""Exceptions raised by the Truma iNet X protocol implementation."""

from __future__ import annotations


class InetXError(Exception):
    """Base class for all protocol errors."""


class InetXTransportError(InetXError):
    """The transport handshake failed or timed out."""


class InetXProtocolError(InetXError):
    """The panel answered with something the protocol does not allow."""


class InetXTimeoutError(InetXError):
    """The panel did not answer a request in time."""


class InetXUnknownParameterError(InetXError):
    """A write targets a parameter the connected system does not report."""
