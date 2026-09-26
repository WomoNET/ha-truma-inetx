"""Config flow: find the panel and pair with it."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
import voluptuous as vol

from .const import CONF_LOCAL_NAME, DOMAIN, panel_id_from_name
from .pairing import PairingError, PairingResult, async_pair_panel

_LOGGER = logging.getLogger(__name__)


class InetXConfigFlow(ConfigFlow, domain=DOMAIN):
    """Guide the user through pairing a panel."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._local_name: str | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}
        self._pair_task: asyncio.Task[PairingResult] | None = None
        self._pair_errors: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a panel found by Bluetooth discovery."""
        panel_id = panel_id_from_name(discovery_info.name)
        if panel_id is None:
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(panel_id)
        self._abort_if_unique_id_configured()
        self._local_name = discovery_info.name
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_pair()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick one of the panels in range."""
        if user_input is not None:
            local_name = self._discovered[user_input[CONF_LOCAL_NAME]].name
            await self.async_set_unique_id(
                panel_id_from_name(local_name), raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            self._local_name = local_name
            return await self.async_step_pair()

        configured = self._async_current_ids(include_ignore=False)
        for service_info in async_discovered_service_info(self.hass, connectable=True):
            panel_id = panel_id_from_name(service_info.name)
            if panel_id is not None and panel_id not in configured:
                # A panel advertises under changing addresses, so the name
                # identifies it.
                self._discovered[service_info.name] = service_info
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_LOCAL_NAME): vol.In(sorted(self._discovered))}
            ),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Pair again after the panel dropped the bond."""
        self._local_name = entry_data[CONF_LOCAL_NAME]
        return await self.async_step_pair()

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain how to start pairing mode, then pair while showing progress."""
        if self._pair_task is None:
            if user_input is None:
                errors, self._pair_errors = self._pair_errors, {}
                return self.async_show_form(
                    step_id="pair",
                    description_placeholders={"name": str(self._local_name)},
                    errors=errors,
                )
            self._pair_task = self.hass.async_create_task(self._async_pair())
        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="pairing",
                description_placeholders={"name": str(self._local_name)},
                progress_task=self._pair_task,
            )
        return self.async_show_progress_done(next_step_id="pair_finished")

    async def async_step_pair_finished(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create or update the entry, or return to the instructions on failure."""
        assert self._pair_task is not None
        task, self._pair_task = self._pair_task, None
        try:
            result = task.result()
        except PairingError as err:
            self._pair_errors = {"base": err.reason}
            return await self.async_step_pair()
        except Exception:
            _LOGGER.exception("Unexpected error while pairing")
            self._pair_errors = {"base": "unknown"}
            return await self.async_step_pair()

        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates={CONF_ADDRESS: result.address}
            )
        panel_id = self.unique_id
        return self.async_create_entry(
            title=f"{result.panel_name or 'iNet X'} {panel_id}",
            data={CONF_ADDRESS: result.address, CONF_LOCAL_NAME: self._local_name},
        )

    async def _async_pair(self) -> PairingResult:
        assert self._local_name is not None
        forget_address = None
        if self.source == SOURCE_REAUTH:
            forget_address = self._get_reauth_entry().data[CONF_ADDRESS]
        return await async_pair_panel(
            self.hass, self._local_name, forget_address=forget_address
        )
