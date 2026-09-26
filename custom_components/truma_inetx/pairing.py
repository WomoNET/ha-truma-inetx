"""Pairing with a panel that is in pairing mode."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .connection import async_find_panel, async_open_session
from .protocol import ADDRESS_PANEL, InetXError

_LOGGER = logging.getLogger(__name__)

PANEL_WAIT_TIMEOUT = 30
FORGET_BOND_TIMEOUT = 10.0


class PairingError(Exception):
    """Pairing failed; reason is the config flow error key."""

    def __init__(self, reason: str) -> None:
        """Initialize with the config flow error key."""
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PairingResult:
    """Outcome of a successful pairing."""

    address: str
    """Address the bond is stored under; stable unlike the advertised one."""

    panel_name: str | None


async def async_pair_panel(
    hass: HomeAssistant, local_name: str, *, forget_address: str | None = None
) -> PairingResult:
    """Pair, verify that the panel answers the protocol, then release it.

    forget_address names a bond to remove first, for panels that have dropped
    the bond on their side.
    """
    forgotten = False
    if forget_address and (
        old_device := bluetooth.async_ble_device_from_address(
            hass, forget_address, connectable=True
        )
    ):
        forgotten = await _async_forget_bond(old_device)
    device = await _async_wait_for_panel(hass, local_name, fresh=forgotten)
    try:
        connection = await async_open_session(device, pair=True)
    except InetXError as err:
        _LOGGER.debug("Panel paired but did not answer: %s", err)
        raise PairingError("no_response") from err
    except BLEAK_RETRY_EXCEPTIONS as err:
        _LOGGER.debug("Pairing with %s failed: %s", local_name, err)
        raise PairingError("pairing_failed") from err
    try:
        panel = connection.session.state.nodes.get(ADDRESS_PANEL)
        return PairingResult(
            address=connection.address,
            panel_name=None if panel is None else panel.identity("Name"),
        )
    finally:
        await connection.async_close()


async def _async_wait_for_panel(
    hass: HomeAssistant, local_name: str, *, fresh: bool
) -> BLEDevice:
    """Return the panel, waiting for an advertisement if needed.

    After a bond was removed the known device is stale, so a fresh
    advertisement is required.
    """
    if not fresh and (device := async_find_panel(hass, None, local_name)):
        return device
    try:
        service_info = await bluetooth.async_process_advertisements(
            hass,
            lambda service_info: service_info.name == local_name,
            bluetooth.BluetoothCallbackMatcher(local_name=local_name, connectable=True),
            bluetooth.BluetoothScanningMode.ACTIVE,
            PANEL_WAIT_TIMEOUT,
        )
    except TimeoutError as err:
        raise PairingError("not_found") from err
    return service_info.device


async def _async_forget_bond(device: BLEDevice) -> bool:
    """Remove the host's copy of a bond; return True if one was removed.

    BlueZ keeps using a stored key and the panel rejects every connection
    made with a key it forgot, so pairing again only works once the key is
    gone. Only local BlueZ adapters store bonds on the host.
    """
    if not isinstance(device.details, dict) or "path" not in device.details:
        return False
    try:
        # The BlueZ backend can only be imported where D-Bus is available.
        from bleak.backends.bluezdbus.client import (  # noqa: PLC0415
            BleakClientBlueZDBus,
        )

        await BleakClientBlueZDBus(
            device, bluez={}, timeout=FORGET_BOND_TIMEOUT
        ).unpair()
    except (ImportError, BleakError, EOFError, TimeoutError) as err:
        _LOGGER.debug("Could not remove the old bond of %s: %s", device.address, err)
        return False
    return True
