"""Keeps the connection to the panel alive and publishes its state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .connection import (
    CONNECTION_ERRORS,
    PanelConnection,
    async_find_panel,
    async_open_session,
)
from .const import CONF_LOCAL_NAME, DOMAIN
from .protocol import InetXError, SystemState

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAYS = (5, 10, 30, 60, 120, 300)
"""Seconds between reconnect attempts; the last value repeats."""

FAILURES_BEFORE_REPAIR = 5
"""Failed attempts while the panel is in range before asking to pair again.

A panel keeps a limited number of bonds, so pairing another phone can
silently remove the bond of Home Assistant.
"""

type InetXConfigEntry = ConfigEntry[InetXCoordinator]


class InetXCoordinator(DataUpdateCoordinator[SystemState]):
    """Owns the Bluetooth session with one panel."""

    config_entry: InetXConfigEntry

    def __init__(self, hass: HomeAssistant, entry: InetXConfigEntry) -> None:
        """Initialize the coordinator; nothing connects before the first refresh."""
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=entry.title, update_interval=None
        )
        self.address: str = entry.data[CONF_ADDRESS]
        self.local_name: str = entry.data[CONF_LOCAL_NAME]
        self._connection: PanelConnection | None = None
        self._connect_lock = asyncio.Lock()
        self._reconnect_timer: CALLBACK_TYPE | None = None
        self._attempt = 0
        self._failures_in_range = 0
        self._panel_gone = False
        self._stopping = False
        self.panel_device_id: str | None = None
        """Device registry ID of the panel, which the other nodes link to."""

    @property
    def connected(self) -> bool:
        """Return True while a session is running."""
        return self._connection is not None

    async def async_start(self) -> None:
        """Connect for the first time and keep reconnecting afterwards."""
        await self.async_config_entry_first_refresh()
        entry = self.config_entry
        entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self._async_advertisement,
                bluetooth.BluetoothCallbackMatcher(
                    local_name=self.local_name, connectable=True
                ),
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )
        entry.async_on_unload(
            bluetooth.async_track_unavailable(
                self.hass, self._async_panel_gone, self.address, connectable=True
            )
        )
        entry.async_on_unload(
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self._async_home_assistant_stop
            )
        )

    async def async_shutdown(self) -> None:
        """Stop reconnecting and release the panel."""
        self._stopping = True
        self._cancel_reconnect()
        await super().async_shutdown()
        await self._async_close()

    async def async_write(
        self, topic: str, name: str, value: Any, *, assume_written: bool = True
    ) -> None:
        """Write a parameter; raise HomeAssistantError if that is not possible."""
        connection = self._require_connection()
        try:
            await connection.session.write(
                topic, name, value, assume_written=assume_written
            )
        except CONNECTION_ERRORS as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={
                    "parameter": f"{topic}/{name}",
                    "error": str(err),
                },
            ) from err

    async def async_wait_for(
        self, topic: str, name: str, predicate: Callable[[Any], bool], timeout: float
    ) -> bool:
        """Wait until a parameter satisfies predicate; False on timeout."""
        return await self._require_connection().session.wait_for(
            topic, name, predicate, timeout
        )

    async def _async_update_data(self) -> SystemState:
        """Connect if necessary and return the state of the session."""
        async with self._connect_lock:
            if self._connection is None:
                self._connection = await self._async_connect()
            return self._connection.session.state

    async def _async_connect(self) -> PanelConnection:
        device = async_find_panel(self.hass, self.address, self.local_name)
        if device is None:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="panel_not_found",
                translation_placeholders={"name": self.local_name},
            )
        try:
            connection = await async_open_session(
                device,
                pair=False,
                on_update=self._async_session_update,
                on_disconnect=self._async_disconnected,
            )
        except CONNECTION_ERRORS as err:
            self._record_failure_in_range()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="connection_failed",
                translation_placeholders={"error": str(err) or type(err).__name__},
            ) from err
        if not connection.client.is_connected:
            # A loss during setup is reported before the session is stored,
            # so the disconnect callback could not handle it.
            await connection.async_close()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="connection_failed",
                translation_placeholders={"error": "lost during setup"},
            )
        self._attempt = 0
        self._failures_in_range = 0
        return connection

    @callback
    def _async_session_update(self) -> None:
        if self._connection is not None:
            self.async_set_updated_data(self._connection.session.state)

    @callback
    def _async_disconnected(self) -> None:
        if self._connection is None:
            return
        _LOGGER.debug("Disconnected from %s", self.local_name)
        connection, self._connection = self._connection, None
        self.config_entry.async_create_background_task(
            self.hass, connection.session.close(), "truma_inetx session cleanup"
        )
        self.async_set_update_error(InetXError("Connection to the panel was lost"))
        self._schedule_reconnect()

    @callback
    def _async_panel_gone(
        self, _service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        _LOGGER.debug("%s is no longer advertising", self.local_name)
        self._panel_gone = True

    @callback
    def _async_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        # Unchanged advertisements are not delivered, so after the panel was
        # gone the first delivered one means it is back in range.
        if not self._panel_gone:
            return
        self._panel_gone = False
        if not self.connected and not self._stopping:
            self._attempt = 0
            self._schedule_reconnect(delay=0)

    def _schedule_reconnect(self, delay: float | None = None) -> None:
        if self._stopping:
            return
        self._cancel_reconnect()
        if delay is None:
            delay = RECONNECT_DELAYS[min(self._attempt, len(RECONNECT_DELAYS) - 1)]
        self._attempt += 1
        self._reconnect_timer = async_call_later(
            self.hass, delay, self._async_reconnect
        )

    async def _async_reconnect(self, _now: Any) -> None:
        self._reconnect_timer = None
        await self.async_refresh()
        if not self.last_update_success:
            self._schedule_reconnect()

    def _cancel_reconnect(self) -> None:
        if self._reconnect_timer is not None:
            self._reconnect_timer()
            self._reconnect_timer = None

    def _record_failure_in_range(self) -> None:
        self._failures_in_range += 1
        if self._failures_in_range == FAILURES_BEFORE_REPAIR:
            _LOGGER.warning(
                "Connecting to %s keeps failing although it is in range; "
                "the Bluetooth pairing may have been removed on the panel",
                self.local_name,
            )
            self.config_entry.async_start_reauth(self.hass)

    def _require_connection(self) -> PanelConnection:
        if self._connection is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_connected"
            )
        return self._connection

    async def _async_home_assistant_stop(self, _event: Event) -> None:
        self._stopping = True
        self._cancel_reconnect()
        await self._async_close()

    async def _async_close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            await connection.async_close()
