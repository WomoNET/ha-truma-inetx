"""Command sequences that were verified on a panel with a Combi heater.

Several operations need more than one write in a fixed order, and the order
matters: for example, a fan level is ignored unless ventilation has been
selected and air circulation activated first.
"""

from __future__ import annotations

from typing import Final

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .coordinator import InetXCoordinator

ROOM_CLIMATE: Final = "RoomClimate"
AIR_CIRCULATION: Final = "AirCirculation"
WATER_HEATING: Final = "WaterHeating"
MODE: Final = "Mode"
ACTIVE: Final = "Active"
FAN_LEVEL: Final = "FanLevel"

ROOM_MODE_OFF: Final = 0
ROOM_MODE_HEATING: Final = 3
ROOM_MODE_VENTILATING: Final = 5

INACTIVE: Final = 0
ACTIVE_ON: Final = 1
WATER_READY_STATES: Final = frozenset({1, 2})
"""Active values reported once the heater accepted hot water: 2 ready, 1 active."""

WATER_READY_TIMEOUT: Final = 15.0


async def async_set_room_mode(coordinator: InetXCoordinator, mode: int) -> None:
    """Switch the room climate mode, leaving ventilation cleanly first."""
    data = coordinator.data
    ventilating = data.value(ROOM_CLIMATE, MODE) == ROOM_MODE_VENTILATING
    if mode == ROOM_MODE_VENTILATING:
        await async_start_ventilation(coordinator)
        return
    if ventilating:
        # Deactivating air circulation is what ends ventilation on the panel.
        await coordinator.async_write(AIR_CIRCULATION, ACTIVE, INACTIVE)
    await coordinator.async_write(ROOM_CLIMATE, MODE, mode)


async def async_start_ventilation(
    coordinator: InetXCoordinator, fan_level: int | None = None
) -> None:
    """Select ventilation, activate air circulation and optionally set a level."""
    data = coordinator.data
    if data.value(ROOM_CLIMATE, MODE) != ROOM_MODE_VENTILATING:
        await coordinator.async_write(ROOM_CLIMATE, MODE, ROOM_MODE_VENTILATING)
    if data.value(AIR_CIRCULATION, ACTIVE) != ACTIVE_ON:
        await coordinator.async_write(AIR_CIRCULATION, ACTIVE, ACTIVE_ON)
    if fan_level is not None:
        await coordinator.async_write(AIR_CIRCULATION, FAN_LEVEL, fan_level)


async def async_stop_ventilation(coordinator: InetXCoordinator) -> None:
    """End ventilation and switch the room climate off."""
    await coordinator.async_write(AIR_CIRCULATION, ACTIVE, INACTIVE)
    await coordinator.async_write(ROOM_CLIMATE, MODE, ROOM_MODE_OFF)


async def async_set_water_mode(coordinator: InetXCoordinator, mode: int) -> None:
    """Switch hot water on if needed, then select the mode.

    When hot water is off, the mode is only accepted after the heater reports
    that it is ready; a mode sent earlier leaves the heater stuck in "ready".
    """
    if coordinator.data.value(WATER_HEATING, ACTIVE) == INACTIVE:
        await coordinator.async_write(
            WATER_HEATING, ACTIVE, ACTIVE_ON, assume_written=False
        )
        if not await coordinator.async_wait_for(
            WATER_HEATING,
            ACTIVE,
            lambda value: value in WATER_READY_STATES,
            WATER_READY_TIMEOUT,
        ):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="water_not_ready"
            )
    await coordinator.async_write(WATER_HEATING, MODE, mode)
