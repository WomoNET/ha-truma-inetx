"""Room climate of the panel."""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .commands import (
    ACTIVE,
    AIR_CIRCULATION,
    MODE,
    ROOM_CLIMATE,
    ROOM_MODE_HEATING,
    ROOM_MODE_OFF,
    ROOM_MODE_VENTILATING,
    async_set_room_mode,
)
from .const import DOMAIN
from .coordinator import InetXConfigEntry
from .entity import (
    TEMPERATURE_SCALE,
    InetXEntity,
    InetXEntityDescription,
    async_add_described_entities,
)

PARALLEL_UPDATES = 1

AIR_HEATING: Final = "AirHeating"
TARGET_TEMPERATURE: Final = "TgtTemp"
TEMPERATURE: Final = "Temp"
CONTROL_ACTIVE: Final = 1
"""Active value of a heating or air circulation control loop that is running."""

ROOM_MODES: Final = {
    ROOM_MODE_OFF: HVACMode.OFF,
    ROOM_MODE_HEATING: HVACMode.HEAT,
    ROOM_MODE_VENTILATING: HVACMode.FAN_ONLY,
}
"""Room modes whose commands were verified; other modes are not offered."""

ROOM_CLIMATE_DESCRIPTION = InetXEntityDescription(
    key="room_climate",
    translation_key="room_climate",
    topic=ROOM_CLIMATE,
    parameter=MODE,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InetXConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the room climate once the panel reports it."""
    async_add_described_entities(
        entry.runtime_data, async_add_entities, [ROOM_CLIMATE_DESCRIPTION], InetXClimate
    )


class InetXClimate(InetXEntity, ClimateEntity):
    """Heating and ventilation of the room."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the verified modes the panel currently offers."""
        parameter = self.described_parameter
        if parameter is None:
            return []
        return [
            ROOM_MODES[option.value]
            for option in parameter.available_options()
            if option.value in ROOM_MODES
        ]

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current room mode."""
        return ROOM_MODES.get(self.value(ROOM_CLIMATE, MODE))

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return whether the heater or the fan is running right now."""
        mode = self.hvac_mode
        if mode == HVACMode.OFF:
            return HVACAction.OFF
        if mode == HVACMode.HEAT:
            running = self.value(AIR_HEATING, ACTIVE) == CONTROL_ACTIVE
            return HVACAction.HEATING if running else HVACAction.IDLE
        if mode == HVACMode.FAN_ONLY:
            running = self.value(AIR_CIRCULATION, ACTIVE) == CONTROL_ACTIVE
            return HVACAction.FAN if running else HVACAction.IDLE
        return None

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the features; the target temperature needs a heater."""
        features = ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
        target = self.parameter(AIR_HEATING, TARGET_TEMPERATURE)
        if target is not None and target.writable:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def current_temperature(self) -> float | None:
        """Return the room temperature measured by the heater."""
        return self.temperature(AIR_HEATING, TEMPERATURE)

    @property
    def target_temperature(self) -> float | None:
        """Return the heating target temperature."""
        return self.temperature(AIR_HEATING, TARGET_TEMPERATURE)

    @property
    def min_temp(self) -> float:
        """Return the lowest target temperature the heater accepts."""
        target = self.parameter(AIR_HEATING, TARGET_TEMPERATURE)
        if target is None or target.minimum is None:
            return super().min_temp
        return target.minimum / TEMPERATURE_SCALE

    @property
    def max_temp(self) -> float:
        """Return the highest target temperature the heater accepts."""
        target = self.parameter(AIR_HEATING, TARGET_TEMPERATURE)
        if target is None or target.maximum is None:
            return super().max_temp
        return target.maximum / TEMPERATURE_SCALE

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch the room mode."""
        if hvac_mode not in self.hvac_modes:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_hvac_mode",
                translation_placeholders={"mode": hvac_mode},
            )
        mode = next(value for value, mode in ROOM_MODES.items() if mode == hvac_mode)
        await async_set_room_mode(self.coordinator, mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the heating target temperature and optionally the mode."""
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(hvac_mode)
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.coordinator.async_write(
                AIR_HEATING,
                TARGET_TEMPERATURE,
                round(temperature * TEMPERATURE_SCALE),
            )

    async def async_turn_on(self) -> None:
        """Switch heating on."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Switch the room climate off."""
        await self.async_set_hvac_mode(HVACMode.OFF)
