"""Enum settings of the heater, e.g. heating mode and energy sources."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import InetXConfigEntry
from .entity import (
    InetXEntity,
    InetXEntityDescription,
    async_add_described_entities,
    option_key,
)
from .protocol import SystemState

PARALLEL_UPDATES = 1

ENERGY_SOURCE = "EnergySrc"

SELECT_DESCRIPTIONS = (
    InetXEntityDescription(
        key="heating_mode",
        translation_key="heating_mode",
        topic="AirHeating",
        parameter="Mode",
    ),
    InetXEntityDescription(
        key="gas",
        translation_key="gas",
        topic=ENERGY_SOURCE,
        parameter="GasLevel",
        entity_category=EntityCategory.CONFIG,
    ),
    InetXEntityDescription(
        key="electric_level",
        translation_key="electric_level",
        topic=ENERGY_SOURCE,
        parameter="ElectricLevel",
        entity_category=EntityCategory.CONFIG,
    ),
    InetXEntityDescription(
        key="diesel",
        translation_key="diesel",
        topic=ENERGY_SOURCE,
        parameter="DieselLevel",
        entity_category=EntityCategory.CONFIG,
    ),
)


def _is_supported(state: SystemState, description: InetXEntityDescription) -> bool:
    parameter = state.parameter(description.topic, description.parameter)
    return (
        parameter is not None
        and parameter.available
        and parameter.writable
        and bool(parameter.available_options())
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InetXConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a select for every writable enum setting the system reports."""
    async_add_described_entities(
        entry.runtime_data,
        async_add_entities,
        SELECT_DESCRIPTIONS,
        InetXSelect,
        _is_supported,
    )


class InetXSelect(InetXEntity, SelectEntity):
    """A setting whose options are defined by the node."""

    @property
    def options(self) -> list[str]:
        """Return the options the node currently offers."""
        parameter = self.described_parameter
        if parameter is None:
            return []
        return [option_key(option) for option in parameter.available_options()]

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        parameter = self.described_parameter
        if parameter is None:
            return None
        option = parameter.option_by_value(parameter.value)
        return None if option is None else option_key(option)

    async def async_select_option(self, option: str) -> None:
        """Write the selected option."""
        parameter = self.described_parameter
        choice = next(
            (
                candidate
                for candidate in (parameter.available_options() if parameter else ())
                if option_key(candidate) == option
            ),
            None,
        )
        if parameter is None or choice is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_option",
                translation_placeholders={"option": option},
            )
        await self.coordinator.async_write(
            parameter.topic, parameter.name, choice.value
        )
