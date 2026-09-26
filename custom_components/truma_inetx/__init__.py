"""The Truma iNet X integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .coordinator import InetXConfigEntry, InetXCoordinator
from .entity import node_device_info
from .protocol import ADDRESS_PANEL

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.FAN,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.WATER_HEATER,
]


async def async_setup_entry(hass: HomeAssistant, entry: InetXConfigEntry) -> bool:
    """Connect to the panel and set up its entities."""
    coordinator = InetXCoordinator(hass, entry)
    await coordinator.async_start()
    entry.runtime_data = coordinator
    # Registered before any entity so heater devices can link to the panel.
    panel = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **node_device_info(coordinator, coordinator.data.nodes[ADDRESS_PANEL]),
    )
    coordinator.panel_device_id = panel.id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: InetXConfigEntry) -> bool:
    """Unload the entities; the coordinator releases the panel on unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
