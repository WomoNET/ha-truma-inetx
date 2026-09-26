"""Constants for the Truma iNet X integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "truma_inetx"
MANUFACTURER: Final = "Truma"

LOCAL_NAME_PREFIX: Final = "Truma iNetX-"
"""Advertised name of the panel; the suffix is stable across address changes."""

CONF_LOCAL_NAME: Final = "local_name"


def panel_id_from_name(local_name: str) -> str | None:
    """Return the panel ID encoded in the advertised name, if it is a panel."""
    if not local_name.startswith(LOCAL_NAME_PREFIX):
        return None
    return local_name.removeprefix(LOCAL_NAME_PREFIX).upper() or None
