"""Tests for the Anycubic config flow."""

from unittest.mock import AsyncMock

from anycubic_lan import (
    InvalidResponseError,
    LanModeDisabledError,
    PrinterUnreachableError,
    RequestRejectedError,
    UnsupportedPrinterError,
)
import pytest

from homeassistant.components.anycubic.const import DOMAIN
from homeassistant.config_entries import SOURCE_DHCP, SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .conftest import MOCK_DEVICE_ID, MOCK_HOST, make_connection_info

from tests.common import MockConfigEntry

NEW_HOST = "192.168.1.60"

DHCP_DISCOVERY = DhcpServiceInfo(
    ip=MOCK_HOST, hostname="kobra-s1", macaddress="a4e88d8054c8"
)

ERRORS = [
    pytest.param(
        PrinterUnreachableError("timeout"), "cannot_connect", id="unreachable"
    ),
    pytest.param(InvalidResponseError("bad"), "cannot_connect", id="invalid"),
    pytest.param(RequestRejectedError("refused"), "cannot_connect", id="rejected"),
    pytest.param(LanModeDisabledError("off"), "not_in_lan_mode", id="lan_mode_off"),
    pytest.param(
        UnsupportedPrinterError("old"), "unsupported_printer", id="unsupported"
    ),
    pytest.param(RuntimeError("boom"), "unknown", id="unknown"),
]


@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_flow(hass: HomeAssistant, mock_handshake: AsyncMock) -> None:
    """Test the user flow creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: f" {MOCK_HOST} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Anycubic Kobra S1"
    assert result["data"] == {CONF_HOST: MOCK_HOST}
    assert result["result"].unique_id == MOCK_DEVICE_ID
    mock_handshake.assert_awaited_once()
    assert mock_handshake.call_args.args[1] == MOCK_HOST


@pytest.mark.usefixtures("mock_setup_entry")
@pytest.mark.parametrize(("exception", "error"), ERRORS)
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_handshake: AsyncMock,
    exception: Exception,
    error: str,
) -> None:
    """Test the user flow shows errors and recovers."""
    mock_handshake.side_effect = exception
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: MOCK_HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_handshake.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: MOCK_HOST}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_handshake", "mock_setup_entry")
async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test the user flow aborts for a known printer and updates its host."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == NEW_HOST


@pytest.mark.usefixtures("mock_handshake", "mock_setup_entry")
async def test_dhcp_flow(hass: HomeAssistant) -> None:
    """Test a DHCP discovered printer is confirmed and added."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP_DISCOVERY
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert result["description_placeholders"] == {
        "name": "Anycubic Kobra S1",
        "host": MOCK_HOST,
    }

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Anycubic Kobra S1"
    assert result["data"] == {CONF_HOST: MOCK_HOST}
    assert result["result"].unique_id == MOCK_DEVICE_ID


@pytest.mark.usefixtures("mock_handshake")
async def test_dhcp_flow_updates_host(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test DHCP discovery of a known printer at a new address updates it."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DHCP},
        data=DhcpServiceInfo(ip=NEW_HOST, hostname="kobra", macaddress="a4e88d8054c8"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == NEW_HOST


async def test_dhcp_flow_known_host(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_handshake: AsyncMock,
) -> None:
    """Test DHCP discovery at a configured address aborts without a handshake."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP_DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_handshake.assert_not_awaited()


@pytest.mark.parametrize(("exception", "reason"), ERRORS)
async def test_dhcp_flow_errors(
    hass: HomeAssistant,
    mock_handshake: AsyncMock,
    exception: Exception,
    reason: str,
) -> None:
    """Test DHCP discovery aborts when the printer cannot be used."""
    mock_handshake.side_effect = exception
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP_DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_handshake: AsyncMock,
) -> None:
    """Test reconfiguring the printer's address."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOST] == NEW_HOST
    assert mock_handshake.call_args.args[1] == NEW_HOST


@pytest.mark.usefixtures("mock_setup_entry")
@pytest.mark.parametrize(("exception", "error"), ERRORS)
async def test_reconfigure_flow_errors(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_handshake: AsyncMock,
    exception: Exception,
    error: str,
) -> None:
    """Test the reconfigure flow shows errors and recovers."""
    mock_config_entry.add_to_hass(hass)
    mock_handshake.side_effect = exception
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_handshake.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_flow_wrong_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_handshake: AsyncMock,
) -> None:
    """Test reconfiguring to a different printer aborts."""
    mock_config_entry.add_to_hass(hass)
    mock_handshake.return_value = make_connection_info("another-printer")
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert mock_config_entry.data[CONF_HOST] == MOCK_HOST
