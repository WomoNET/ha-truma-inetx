"""Temperature sensors of the panel and the heater."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import InetXConfigEntry
from .entity import InetXEntity, InetXEntityDescription, async_add_described_entities

PARALLEL_UPDATES = 0

PARAMETER_TEMPERATURE = "Temp"


@dataclass(frozen=True, kw_only=True)
class InetXSensorEntityDescription(InetXEntityDescription, SensorEntityDescription):
    """Describes a temperature sensor bound to one parameter."""

    device_class: SensorDeviceClass | None = SensorDeviceClass.TEMPERATURE
    native_unit_of_measurement: str | None = UnitOfTemperature.CELSIUS
    state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT


SENSOR_DESCRIPTIONS = (
    InetXSensorEntityDescription(
        key="room_temperature",
        translation_key="room_temperature",
        topic="AirHeating",
        parameter=PARAMETER_TEMPERATURE,
    ),
    InetXSensorEntityDescription(
        key="water_temperature",
        translation_key="water_temperature",
        topic="WaterHeating",
        parameter=PARAMETER_TEMPERATURE,
    ),
    InetXSensorEntityDescription(
        key="panel_temperature",
        translation_key="panel_temperature",
        topic="Temperature",
        parameter="Internal",
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InetXConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the temperature sensors the system reports."""
    async_add_described_entities(
        entry.runtime_data, async_add_entities, SENSOR_DESCRIPTIONS, InetXSensor
    )


class InetXSensor(InetXEntity, SensorEntity):
    """A temperature reported in tenths of a degree."""

    entity_description: InetXSensorEntityDescription

    @property
    def native_value(self) -> float | None:
        """Return the temperature in degrees Celsius."""
        description = self.entity_description
        return self.temperature(description.topic, description.parameter)
