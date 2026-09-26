"""Builders and parsers for registration and message broker messages."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .const import (
    ADDRESS_BROADCAST,
    ADDRESS_MESSAGE_BROKER,
    ADDRESS_UNREGISTERED_APP,
    LAST_MESSAGE_KEY,
    PROTOCOL_VERSION,
    ControlType,
    MessageType,
    RegistrationType,
)
from .frame import Frame, encode_frame

_REGISTRATION_CORRELATION_ID = 0x42
_WRITE_CORRELATION_ID = 0x00

KEY_TOPIC = "tn"
KEY_PARAMETER = "pn"
KEY_VALUE = "v"


def registration_request() -> bytes:
    """Ask the panel to assign an application address."""
    return encode_frame(
        destination=ADDRESS_BROADCAST,
        source=ADDRESS_UNREGISTERED_APP,
        control=ControlType.REGISTRATION,
        message_type=RegistrationType.REQUEST,
        correlation_id=_REGISTRATION_CORRELATION_ID,
        body={"pv": list(PROTOCOL_VERSION)},
    )


def registered_address(frame: Frame) -> int | None:
    """Return the application address assigned by a registration response."""
    if (
        frame.control != ControlType.REGISTRATION
        or frame.message_type != RegistrationType.RESPONSE
        or not isinstance(frame.body, Mapping)
    ):
        return None
    address = frame.body.get("addr")
    return address if isinstance(address, int) else None


def discovery_request(
    *, source: int, destination: int, topic: str, correlation_id: int
) -> bytes:
    """Request the parameter schema of a node.

    The node answers with all of its topics, not only the requested one, and
    ends the response with a frame carrying the same correlation ID.
    """
    return encode_frame(
        destination=destination,
        source=source,
        control=ControlType.MESSAGE_BROKER,
        message_type=MessageType.PARAMETER_DISCOVERY,
        correlation_id=correlation_id,
        body={KEY_TOPIC: topic},
    )


def subscribe_request(
    *, source: int, topics: Iterable[str], correlation_id: int
) -> bytes:
    """Subscribe to live updates of topics via the message broker."""
    return encode_frame(
        destination=ADDRESS_MESSAGE_BROKER,
        source=source,
        control=ControlType.MESSAGE_BROKER,
        message_type=MessageType.SUBSCRIBE,
        correlation_id=correlation_id,
        body={KEY_TOPIC: list(topics)},
    )


def write_request(
    *, source: int, destination: int, topic: str, parameter: str, value: Any
) -> bytes:
    """Write one parameter; the destination must be the node owning the topic."""
    return encode_frame(
        destination=destination,
        source=source,
        control=ControlType.MESSAGE_BROKER,
        message_type=MessageType.WRITE,
        correlation_id=_WRITE_CORRELATION_ID,
        body={KEY_TOPIC: topic, KEY_PARAMETER: parameter, KEY_VALUE: value, "id": 0},
    )


def is_last_message(frame: Frame) -> bool:
    """Return True for the frame that ends a parameter discovery response."""
    return (
        frame.message_type == MessageType.PARAMETER_DISCOVERY_RESPONSE
        and isinstance(frame.body, Mapping)
        and bool(frame.body.get(LAST_MESSAGE_KEY))
    )


def parameter_reports(frame: Frame) -> list[Mapping[str, Any]]:
    """Extract parameter descriptions from an info or discovery frame.

    Info messages carry a single flat description. Discovery responses group
    descriptions by topic: ``{"topics": [{"tn": ..., "parameters": [...]}]}``.
    Every returned mapping contains at least the topic and parameter name.
    """
    body = frame.body
    if frame.control != ControlType.MESSAGE_BROKER or not isinstance(body, Mapping):
        return []
    if frame.message_type == MessageType.INFO:
        return [body] if _is_report(body) else []
    if frame.message_type != MessageType.PARAMETER_DISCOVERY_RESPONSE:
        return []

    reports: list[Mapping[str, Any]] = []
    topics = body.get("topics")
    if not isinstance(topics, list):
        return reports
    for topic in topics:
        if not isinstance(topic, Mapping) or not isinstance(
            topic.get("parameters"), list
        ):
            continue
        for parameter in topic["parameters"]:
            if not isinstance(parameter, Mapping):
                continue
            report = {KEY_TOPIC: topic.get(KEY_TOPIC), **parameter}
            if _is_report(report):
                reports.append(report)
    return reports


def _is_report(candidate: Mapping[str, Any]) -> bool:
    return isinstance(candidate.get(KEY_TOPIC), str) and isinstance(
        candidate.get(KEY_PARAMETER), str
    )
