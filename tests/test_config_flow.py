"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.truma_inetx.const import CONF_LOCAL_NAME, DOMAIN
from custom_components.truma_inetx.pairing import _async_forget_bond
from tests.conftest import (
    IDENTITY_ADDRESS,
    LOCAL_NAME,
    PANEL_ID,
    RANDOM_ADDRESS,
    FakeProxy,
    PanelHarness,
    panel_service_info,
)


async def _pair(hass: HomeAssistant, flow: FlowResult) -> FlowResult:
    """Submit the pairing instructions and wait for pairing to finish."""
    assert flow["type"] is FlowResultType.FORM
    assert flow["step_id"] == "pair"
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    if result["type"] is not FlowResultType.SHOW_PROGRESS:
        # Pairing that fails right away skips the progress screen.
        return result
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(flow["flow_id"])


async def _discovered_flow(hass: HomeAssistant, proxy: FakeProxy) -> FlowResult:
    """Advertise the panel and return the discovery flow Home Assistant starts."""
    proxy.advertise(RANDOM_ADDRESS)
    await hass.async_block_till_done(wait_background_tasks=True)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_BLUETOOTH
    return await hass.config_entries.flow.async_configure(flows[0]["flow_id"])


async def test_discovered_panel_is_paired(
    hass: HomeAssistant, harness: PanelHarness, proxy: FakeProxy
) -> None:
    """A discovered panel is paired and stored under its identity address."""
    flow = await _discovered_flow(hass, proxy)

    result = await _pair(hass, flow)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"iNet X Panel {PANEL_ID}"
    assert result["data"] == {
        CONF_ADDRESS: IDENTITY_ADDRESS,
        CONF_LOCAL_NAME: LOCAL_NAME,
    }
    assert result["result"].unique_id == PANEL_ID
    assert harness.pair_requests[0] is True
    assert not harness.clients[0].is_connected


async def test_discovery_of_configured_panel_aborts(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A panel is only configured once, whatever address it advertises."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=panel_service_info(RANDOM_ADDRESS),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_of_other_device_aborts(hass: HomeAssistant) -> None:
    """Devices whose name does not identify a panel are rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=panel_service_info(name="Truma LevelControl"),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_user_picks_panel_in_range(
    hass: HomeAssistant, harness: PanelHarness, proxy: FakeProxy
) -> None:
    """The user step lists each panel once, even under several addresses."""
    proxy.advertise(RANDOM_ADDRESS)
    proxy.advertise("5B:87:FE:13:0E:98")
    proxy.advertise("C0:23:8D:E7:50:86", name="Other device")

    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert flow["type"] is FlowResultType.FORM
    assert flow["data_schema"].schema[CONF_LOCAL_NAME].container == [LOCAL_NAME]

    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_LOCAL_NAME: LOCAL_NAME}
    )
    result = await _pair(hass, flow)

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_step_without_panel_aborts(hass: HomeAssistant) -> None:
    """Without a panel in range there is nothing to set up."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


@pytest.mark.parametrize(
    ("prepare", "error"),
    [
        pytest.param(
            lambda harness: setattr(harness, "connect_error", BleakError("rejected")),
            "pairing_failed",
            id="pairing rejected",
        ),
        pytest.param(
            lambda harness: setattr(
                harness,
                "configure_panel",
                lambda panel: panel.silent_nodes.add(0x0101),
            ),
            "no_response",
            id="panel silent",
        ),
        pytest.param(
            lambda harness: setattr(harness, "connect_error", RuntimeError("bug")),
            "unknown",
            id="unexpected error",
        ),
    ],
)
async def test_failed_pairing_can_be_retried(
    hass: HomeAssistant,
    harness: PanelHarness,
    proxy: FakeProxy,
    prepare,
    error: str,
) -> None:
    """A failed attempt returns to the instructions with an error."""
    flow = await _discovered_flow(hass, proxy)
    prepare(harness)

    result = await _pair(hass, flow)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    harness.connect_error = None
    harness.configure_panel = None
    result = await _pair(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_pairing_fails_when_panel_disappears(
    hass: HomeAssistant, harness: PanelHarness
) -> None:
    """A panel that is not advertising cannot be paired."""
    flow = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=panel_service_info(RANDOM_ADDRESS),
    )

    result = await _pair(hass, flow)

    assert result["errors"] == {"base": "not_found"}
    assert harness.clients == []


async def test_reauth_pairs_again(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    harness: PanelHarness,
    proxy: FakeProxy,
) -> None:
    """Re-pairing updates the entry and removes the stale bond first."""
    proxy.advertise(IDENTITY_ADDRESS)
    harness.identity_address = "74:D2:85:F4:FC:BB"
    forget = AsyncMock(return_value=True)

    with patch("custom_components.truma_inetx.pairing._async_forget_bond", forget):
        flow = await config_entry.start_reauth_flow(hass)
        proxy.advertise(IDENTITY_ADDRESS)
        result = await _pair(hass, flow)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_ADDRESS] == "74:D2:85:F4:FC:BB"
    assert forget.await_args.args[0].address == IDENTITY_ADDRESS


async def test_forget_bond_removes_bluez_device() -> None:
    """A bond held by a local BlueZ adapter is removed."""
    device = BLEDevice(IDENTITY_ADDRESS, LOCAL_NAME, {"path": "/org/bluez/hci0/dev_x"})
    with patch(
        "bleak.backends.bluezdbus.client.BleakClientBlueZDBus.unpair", AsyncMock()
    ) as unpair:
        assert await _async_forget_bond(device)
    unpair.assert_awaited_once()


async def test_forget_bond_ignores_proxies_and_failures() -> None:
    """Adapters without a host-side bond and D-Bus failures are skipped."""
    assert not await _async_forget_bond(BLEDevice(IDENTITY_ADDRESS, LOCAL_NAME, {}))

    device = BLEDevice(IDENTITY_ADDRESS, LOCAL_NAME, {"path": "/org/bluez/hci0/dev_x"})
    with patch(
        "bleak.backends.bluezdbus.client.BleakClientBlueZDBus.unpair",
        AsyncMock(side_effect=BleakError("no such device")),
    ):
        assert not await _async_forget_bond(device)
