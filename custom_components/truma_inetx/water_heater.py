"""Hot water of the heater."""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components.water_heater import (
    STATE_OFF,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .commands import (
    ACTIVE,
    ACTIVE_ON,
    INACTIVE,
    MODE,
    WATER_HEATING,
    async_set_water_mode,
)
from .const import DOMAIN
from .coordinator import InetXConfigEntry
from .entity import (
    InetXEntity,
    InetXEntityDescription,
    async_add_described_entities,
    option_key,
)
from .protocol import EnumOption

PARALLEL_UPDATES = 1

TEMPERATURE: Final = "Temp"

WATER_HEATER_DESCRIPTION = InetXEntityDescription(
    key="water_heater",
    translation_key="water_heater",
    topic=WATER_HEATING,
    parameter=MODE,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InetXConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up hot water once the heater reports it."""
    async_add_described_entities(
        entry.runtime_data,
        async_add_entities,
        [WATER_HEATER_DESCRIPTION],
        InetXWaterHeater,
    )


class InetXWaterHeater(InetXEntity, WaterHeaterEntity):
    """Hot water with the levels of the heater as operation modes.

    The heater names its levels after their water temperature ("40", "60",
    "70"), so the operation modes are temperatures as well.
    """

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        WaterHeaterEntityFeature.OPERATION_MODE | WaterHeaterEntityFeature.ON_OFF
    )

    @property
    def operation_list(self) -> list[str]:
        """Return off plus the hot water levels the heater offers."""
        return [STATE_OFF, *self._options]

    @property
    def current_operation(self) -> str | None:
        """Return the current hot water level, or off."""
        if self.value(WATER_HEATING, ACTIVE) == INACTIVE:
            return STATE_OFF
        parameter = self.described_parameter
        option = (
            None if parameter is None else parameter.option_by_value(parameter.value)
        )
        return None if option is None else option_key(option)

    @property
    def current_temperature(self) -> float | None:
        """Return the measured water temperature."""
        return self.temperature(WATER_HEATING, TEMPERATURE)

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature of the current level, when it names one."""
        operation = self.current_operation
        if operation is None or operation == STATE_OFF:
            return None
        try:
            return float(operation)
        except ValueError:
            return None

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Switch hot water off or to a level."""
        if operation_mode == STATE_OFF:
            await self.async_turn_off()
            return
        option = self._options.get(operation_mode)
        if option is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_operation_mode",
                translation_placeholders={"mode": operation_mode},
            )
        await async_set_water_mode(self.coordinator, option.value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch hot water on at the last level."""
        await self.coordinator.async_write(WATER_HEATING, ACTIVE, ACTIVE_ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch hot water off."""
        await self.coordinator.async_write(WATER_HEATING, ACTIVE, INACTIVE)

    @property
    def _options(self) -> dict[str, EnumOption]:
        parameter = self.described_parameter
        if parameter is None:
            return {}
        return {option_key(o): o for o in parameter.available_options()}
