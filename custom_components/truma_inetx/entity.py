"""Base entity and helpers shared by all platforms."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import InetXCoordinator
from .protocol import ADDRESS_PANEL, EnumOption, Node, Parameter, SystemState

TEMPERATURE_SCALE = 10
"""Temperatures travel as integers in tenths of a degree Celsius."""


@dataclass(frozen=True, kw_only=True)
class InetXEntityDescription(EntityDescription):
    """Describes an entity bound to one parameter.

    The entity is created once the system reports the parameter as available,
    so appliances a system lacks never show up.
    """

    topic: str
    parameter: str


class InetXEntity(CoordinatorEntity[InetXCoordinator]):
    """Entity of a node on the panel's bus."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: InetXCoordinator, description: InetXEntityDescription
    ) -> None:
        """Initialize the entity for the node that owns the described topic."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{description.key}"
        node = coordinator.data.owner(description.topic)
        self._attr_device_info = node_device_info(
            coordinator, coordinator.data.nodes[ADDRESS_PANEL if node is None else node]
        )

    @property
    def available(self) -> bool:
        """Return True while connected and the parameter is available."""
        parameter = self.described_parameter
        return (
            super().available
            and self.coordinator.connected
            and parameter is not None
            and parameter.available
        )

    @property
    def described_parameter(self) -> Parameter | None:
        """Return the parameter this entity is bound to."""
        description = self.entity_description
        assert isinstance(description, InetXEntityDescription)
        return self.parameter(description.topic, description.parameter)

    def parameter(self, topic: str, name: str) -> Parameter | None:
        """Return any parameter of the system."""
        return self.coordinator.data.parameter(topic, name)

    def value(self, topic: str, name: str) -> Any:
        """Return the value of any parameter of the system."""
        return self.coordinator.data.value(topic, name)

    def temperature(self, topic: str, name: str) -> float | None:
        """Return a temperature parameter in degrees Celsius."""
        value = self.value(topic, name)
        return value / TEMPERATURE_SCALE if isinstance(value, int) else None


def option_key(option: EnumOption) -> str:
    """Return a stable key for an enum option, usable as a translation key."""
    return option.name.strip().lower().replace(" ", "_")


def node_device_info(coordinator: InetXCoordinator, node: Node) -> DeviceInfo:
    """Return the device of a node; nodes behind the panel link to the panel."""
    panel_id = coordinator.config_entry.unique_id
    model = node.identity("Name")
    info = DeviceInfo(
        identifiers={(DOMAIN, _node_identifier(panel_id, node.address))},
        manufacturer=MANUFACTURER,
        name=model or f"Truma 0x{node.address:04X}",
        model=model,
        serial_number=node.identity("SerialNr"),
        sw_version=_version(node, "SwMaj", "SwMin", "SwBgFx"),
        hw_version=_version(node, "HwMaj", "HwMin"),
    )
    if node.address == ADDRESS_PANEL:
        info["connections"] = {(CONNECTION_BLUETOOTH, coordinator.address)}
    elif coordinator.panel_device_id is not None:
        info["via_device_id"] = coordinator.panel_device_id
    return info


def async_add_described_entities[DescriptionT: InetXEntityDescription](
    coordinator: InetXCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    descriptions: Iterable[DescriptionT],
    factory: Callable[[InetXCoordinator, DescriptionT], Entity],
    supported: Callable[[SystemState, DescriptionT], bool] | None = None,
) -> None:
    """Add entities now and whenever a later update reports their parameter.

    Nodes can appear after setup (e.g. a heater that answered late), so every
    update checks for descriptions that became supported.
    """
    pending = list(descriptions)

    def _is_supported(state: SystemState, description: DescriptionT) -> bool:
        if supported is not None:
            return supported(state, description)
        parameter = state.parameter(description.topic, description.parameter)
        return parameter is not None and parameter.available

    def _add_supported() -> None:
        state = coordinator.data
        ready = [d for d in pending if _is_supported(state, d)]
        if not ready:
            return
        for description in ready:
            pending.remove(description)
        async_add_entities(factory(coordinator, d) for d in ready)

    _add_supported()
    if pending:
        coordinator.config_entry.async_on_unload(
            coordinator.async_add_listener(_add_supported)
        )


def _node_identifier(panel_id: str | None, address: int) -> str:
    if address == ADDRESS_PANEL:
        return str(panel_id)
    return f"{panel_id}_{address:04x}"


def _version(node: Node, *parts: str) -> str | None:
    values = [node.identity(part) for part in parts]
    if any(value is None for value in values):
        return None
    return ".".join(str(value) for value in values)
