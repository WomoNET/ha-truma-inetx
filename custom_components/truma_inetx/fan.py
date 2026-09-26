"""Ventilation fan of the heater."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from .commands import (
    AIR_CIRCULATION,
    FAN_LEVEL,
    MODE,
    ROOM_CLIMATE,
    ROOM_MODE_VENTILATING,
    async_start_ventilation,
    async_stop_ventilation,
)
from .coordinator import InetXConfigEntry
from .entity import InetXEntity, InetXEntityDescription, async_add_described_entities

PARALLEL_UPDATES = 1

DEFAULT_MAX_LEVEL = 10
LOWEST_LEVEL = 1

VENTILATION_DESCRIPTION = InetXEntityDescription(
    key="ventilation",
    translation_key="ventilation",
    topic=AIR_CIRCULATION,
    parameter=FAN_LEVEL,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InetXConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the ventilation fan once the heater reports it."""
    async_add_described_entities(
        entry.runtime_data,
        async_add_entities,
        [VENTILATION_DESCRIPTION],
        InetXVentilationFan,
    )


class InetXVentilationFan(InetXEntity, FanEntity):
    """Air circulation without heating, with levels as fan speeds."""

    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    @property
    def speed_count(self) -> int:
        """Return the number of fan levels."""
        parameter = self.described_parameter
        if parameter is None or not parameter.maximum:
            return DEFAULT_MAX_LEVEL
        return parameter.maximum

    @property
    def is_on(self) -> bool:
        """Return True while ventilating with a level above zero."""
        return self._ventilating and bool(self._level)

    @property
    def percentage(self) -> int | None:
        """Return the fan level as a percentage."""
        if not self.is_on:
            return 0
        return ranged_value_to_percentage((LOWEST_LEVEL, self.speed_count), self._level)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Start ventilation, at the lowest level unless a speed is given."""
        await async_start_ventilation(
            self.coordinator,
            LOWEST_LEVEL if percentage is None else self._level_for(percentage),
        )

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the fan level; zero switches ventilation off."""
        if percentage == 0:
            await self.async_turn_off()
            return
        await async_start_ventilation(self.coordinator, self._level_for(percentage))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop ventilation."""
        await async_stop_ventilation(self.coordinator)

    @property
    def _ventilating(self) -> bool:
        return self.value(ROOM_CLIMATE, MODE) == ROOM_MODE_VENTILATING

    @property
    def _level(self) -> int:
        level = self.value(AIR_CIRCULATION, FAN_LEVEL)
        return level if isinstance(level, int) else 0

    def _level_for(self, percentage: int) -> int:
        level = round(
            percentage_to_ranged_value((LOWEST_LEVEL, self.speed_count), percentage)
        )
        return max(LOWEST_LEVEL, level)
