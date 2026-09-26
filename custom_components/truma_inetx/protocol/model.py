"""State model: nodes, their parameter schemas and the latest values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .messages import KEY_PARAMETER, KEY_TOPIC, KEY_VALUE

TOPIC_IDENTIFY = "Identify"
_READ_ONLY_PERMISSION = 0
_REPORTED_FIELDS = {
    "type": "type_code",
    "min": "minimum",
    "max": "maximum",
    "perm": "permission",
}
"""Description keys that map one to one onto Parameter attributes."""


@dataclass(frozen=True, slots=True)
class EnumOption:
    """One option of an enum parameter."""

    value: int
    name: str
    available: bool


@dataclass(slots=True)
class Parameter:
    """A parameter as described by the node that owns it.

    Descriptions arrive in parts: discovery responses carry the full schema,
    info messages sometimes only the value. Missing fields keep their last
    known content.
    """

    node: int
    topic: str
    name: str
    type_code: int | None = None
    minimum: int | None = None
    maximum: int | None = None
    permission: int | None = None
    available: bool = True
    options: tuple[EnumOption, ...] = ()
    value: Any = None

    @property
    def writable(self) -> bool:
        """Return False for parameters the node marks as read-only."""
        return self.permission != _READ_ONLY_PERMISSION

    def available_options(self) -> tuple[EnumOption, ...]:
        """Return the enum options the node currently offers."""
        return tuple(option for option in self.options if option.available)

    def option_by_value(self, value: Any) -> EnumOption | None:
        """Return the enum option for a raw value."""
        return next((option for option in self.options if option.value == value), None)

    def apply(self, report: Mapping[str, Any]) -> bool:
        """Merge a received description; return True if anything changed."""
        before = self.snapshot()
        for key, attribute in _REPORTED_FIELDS.items():
            if key in report:
                setattr(self, attribute, report[key])
        if "avail" in report:
            self.available = bool(report["avail"])
        if isinstance(report.get("enum"), list):
            self.options = tuple(
                EnumOption(
                    value=option["v"],
                    name=str(option.get("n", option["v"])),
                    available=bool(option.get("a", True)),
                )
                for option in report["enum"]
                if isinstance(option, Mapping) and "v" in option
            )
        if KEY_VALUE in report:
            self.value = report[KEY_VALUE]
        return self.snapshot() != before

    def snapshot(self) -> tuple[Any, ...]:
        """Return all fields as a comparable tuple."""
        return (
            self.type_code,
            self.minimum,
            self.maximum,
            self.permission,
            self.available,
            self.options,
            self.value,
        )


@dataclass(slots=True)
class Node:
    """A device on the Truma bus, e.g. the panel or a heater."""

    address: int
    parameters: dict[tuple[str, str], Parameter] = field(default_factory=dict)

    def identity(self, name: str) -> Any:
        """Return a value of the node's Identify topic."""
        parameter = self.parameters.get((TOPIC_IDENTIFY, name))
        return None if parameter is None else parameter.value

    @property
    def topics(self) -> set[str]:
        """Return the topics this node reports."""
        return {topic for topic, _ in self.parameters}


class SystemState:
    """All nodes reported by one panel.

    Application topics (e.g. RoomClimate, AirHeating) belong to exactly one
    node, which is also the node that accepts writes for them. Housekeeping
    topics such as Identify exist on every node and are looked up per node.
    """

    def __init__(self) -> None:
        """Initialize an empty state."""
        self.nodes: dict[int, Node] = {}

    def apply(self, node_address: int, report: Mapping[str, Any]) -> bool:
        """Merge one parameter description; return True if anything changed."""
        topic = report.get(KEY_TOPIC)
        name = report.get(KEY_PARAMETER)
        if not isinstance(topic, str) or not isinstance(name, str):
            return False
        node = self.nodes.setdefault(node_address, Node(node_address))
        parameter = node.parameters.get((topic, name))
        if parameter is None:
            parameter = node.parameters[(topic, name)] = Parameter(
                node=node_address, topic=topic, name=name
            )
            parameter.apply(report)
            return True
        return parameter.apply(report)

    def parameter(self, topic: str, name: str) -> Parameter | None:
        """Return an application parameter, wherever its owner is."""
        for node in self.nodes.values():
            if (parameter := node.parameters.get((topic, name))) is not None:
                return parameter
        return None

    def value(self, topic: str, name: str) -> Any:
        """Return the current value of an application parameter."""
        parameter = self.parameter(topic, name)
        return None if parameter is None else parameter.value

    def owner(self, topic: str) -> int | None:
        """Return the address of the node owning an application topic."""
        for node in self.nodes.values():
            if topic in node.topics:
                return node.address
        return None

    def set_value(self, topic: str, name: str, value: Any) -> bool:
        """Record a value written by us before the node confirms it."""
        parameter = self.parameter(topic, name)
        if parameter is None or parameter.value == value:
            return False
        parameter.value = value
        return True


def describe_parameter(parameter: Parameter) -> dict[str, Any]:
    """Return a JSON-serialisable description of a parameter."""
    description: dict[str, Any] = {
        "topic": parameter.topic,
        "name": parameter.name,
        "type": parameter.type_code,
        "available": parameter.available,
        "writable": parameter.writable,
        "value": parameter.value,
    }
    if parameter.minimum is not None or parameter.maximum is not None:
        description["range"] = [parameter.minimum, parameter.maximum]
    if parameter.options:
        description["options"] = [
            {"value": option.value, "name": option.name, "available": option.available}
            for option in parameter.options
        ]
    return description
