"""Diagnostics: the full parameter schema of every node.

The schema is what support for further appliances is built from, so all
parameters are included, not only the ones that have entities.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .coordinator import InetXConfigEntry
from .protocol import TOPIC_IDENTIFY, describe_parameter

REDACTED_IDENTITY = {"SerialNr", "UniqueID", "CertThumb"}
TO_REDACT = {CONF_ADDRESS}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: InetXConfigEntry
) -> dict[str, Any]:
    """Return the connection state and all nodes with their parameters."""
    coordinator = entry.runtime_data
    state = coordinator.data
    nodes = {}
    for address, node in sorted(state.nodes.items()):
        parameters = []
        for parameter in node.parameters.values():
            description = describe_parameter(parameter)
            if (
                parameter.topic == TOPIC_IDENTIFY
                and parameter.name in REDACTED_IDENTITY
            ):
                description["value"] = "**REDACTED**"
            parameters.append(description)
        nodes[f"0x{address:04X}"] = parameters
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "connected": coordinator.connected,
        "nodes": nodes,
    }
