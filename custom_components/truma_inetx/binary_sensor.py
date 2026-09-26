"""Burner flame of the heater."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import InetXConfigEntry
from .entity import InetXEntity, InetXEntityDescription, async_add_described_entities

PARALLEL_UPDATES = 0

FLAME_DESCRIPTION = InetXEntityDescription(
    key="flame",
    translation_key="flame",
    topic="System",
    parameter="FlameStatus",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InetXConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the flame sensor once the panel reports it."""
    async_add_described_entities(
        entry.runtime_data, async_add_entities, [FLAME_DESCRIPTION], InetXBinarySensor
    )


class InetXBinarySensor(InetXEntity, BinarySensorEntity):
    """A parameter that is either zero or not."""

    @property
    def is_on(self) -> bool | None:
        """Return True while the value is not zero."""
        value = self.described_parameter
        return None if value is None or value.value is None else bool(value.value)
