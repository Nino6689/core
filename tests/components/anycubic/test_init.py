"""Tests for the Anycubic integration setup and connection handling."""

import asyncio
from dataclasses import replace
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from anycubic_lan import (
    NotConnectedError,
    PrinterState,
    PrinterUnreachableError,
    RequestRejectedError,
)
from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.anycubic.const import DOMAIN, QUERY_INTERVAL
from homeassistant.components.homeassistant import (
    DOMAIN as HOMEASSISTANT_DOMAIN,
    SERVICE_UPDATE_ENTITY,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

from .conftest import (
    MOCK_DEVICE_ID,
    make_connection_info,
    push_state,
    set_connected,
    state_from_fixture,
)

from tests.common import MockConfigEntry, async_fire_time_changed

ENTITY_ID = "sensor.anycubic_kobra_s1_nozzle_temperature"


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Advance time by one query interval."""
    freezer.tick(QUERY_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Test the entry is set up and unloaded cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED
    mock_client.connect.assert_awaited_once()
    assert hass.states.get(ENTITY_ID).state == "219"

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED
    mock_client.disconnect.assert_awaited_once()
    assert not mock_client.state_listeners
    assert not mock_client.connection_listeners


@pytest.mark.usefixtures("mock_client")
async def test_setup_handshake_fails(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_handshake: AsyncMock,
) -> None:
    """Test setup is retried when the printer does not answer."""
    mock_handshake.side_effect = PrinterUnreachableError("timeout")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_wrong_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_handshake: AsyncMock,
    mock_client: MagicMock,
) -> None:
    """Test setup is retried when another printer answers at the address."""
    mock_handshake.return_value = make_connection_info("another-printer")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_client.connect.assert_not_awaited()


@pytest.mark.usefixtures("mock_handshake")
async def test_setup_connect_fails(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Test setup is retried and cleaned up when the broker refuses."""
    mock_client.connect.side_effect = RequestRejectedError("refused")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_client.disconnect.assert_awaited()
    assert not mock_client.state_listeners


@pytest.mark.usefixtures("mock_handshake")
async def test_setup_no_first_report(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Test setup is retried when the printer sends no info report."""
    # Reports without the info block do not complete setup.
    mock_client.state = PrinterState()
    mock_config_entry.add_to_hass(hass)
    with patch("homeassistant.components.anycubic.coordinator.FIRST_REPORT_TIMEOUT", 0):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_client.disconnect.assert_awaited()


@pytest.mark.usefixtures("init_integration")
async def test_connection_lost_and_restored(
    hass: HomeAssistant,
    mock_client: MagicMock,
    printer_state: PrinterState,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test entities go unavailable once and come back with the next report."""
    caplog.set_level(logging.INFO)
    set_connected(mock_client, False)
    set_connected(mock_client, False)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE
    assert caplog.text.count("Lost connection to the printer") == 1

    # The connection returning is not enough; the printer must report again.
    set_connected(mock_client, True)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE

    push_state(mock_client, printer_state)
    push_state(mock_client, printer_state)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == "219"
    assert caplog.text.count("Connection to the printer at") == 1


@pytest.mark.usefixtures("init_integration")
async def test_periodic_queries(
    hass: HomeAssistant, mock_client: MagicMock, freezer: FrozenDateTimeFactory
) -> None:
    """Test every report kind is queried on a fixed interval."""
    mock_client.query_all.reset_mock()
    await _tick(hass, freezer)
    mock_client.query_all.assert_awaited_once()

    # Pushed reports do not delay the next query.
    freezer.tick(QUERY_INTERVAL / 2)
    push_state(mock_client, mock_client.state)
    await _tick(hass, freezer)
    assert mock_client.query_all.await_count == 2


@pytest.mark.usefixtures("init_integration")
async def test_query_fails_marks_unavailable(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_handshake: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a query on a dropped connection marks the printer unavailable."""
    mock_client.query_all.side_effect = NotConnectedError("gone")
    mock_handshake.side_effect = PrinterUnreachableError("timeout")
    await _tick(hass, freezer)
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("init_integration")
async def test_reconnect_with_new_handshake(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_client_class: MagicMock,
    mock_handshake: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a lost printer is found again with a fresh handshake."""
    set_connected(mock_client, False)
    mock_handshake.side_effect = PrinterUnreachableError("timeout")
    await _tick(hass, freezer)
    assert mock_handshake.await_count == 2
    assert mock_client_class.call_count == 1
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE

    mock_handshake.side_effect = None
    await _tick(hass, freezer)
    assert mock_handshake.await_count == 3
    assert mock_client_class.call_count == 2
    mock_client.disconnect.assert_awaited_once()
    assert mock_client.connect.await_count == 2
    assert len(mock_client.state_listeners) == 1
    assert hass.states.get(ENTITY_ID).state == "219"


@pytest.mark.usefixtures("init_integration")
async def test_reconnect_different_printer(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_client_class: MagicMock,
    mock_handshake: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test no connection is made when another printer answers."""
    set_connected(mock_client, False)
    mock_handshake.return_value = make_connection_info("another-printer")
    await _tick(hass, freezer)
    assert mock_client_class.call_count == 1
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("init_integration")
async def test_reconnect_connect_fails(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_client_class: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a failed reconnect is retried on the next interval."""
    connect = mock_client.connect.side_effect
    set_connected(mock_client, False)
    mock_client.connect.side_effect = PrinterUnreachableError("timeout")
    await _tick(hass, freezer)
    assert mock_client_class.call_count == 2
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE

    mock_client.connect.side_effect = connect
    await _tick(hass, freezer)
    assert mock_client_class.call_count == 3
    assert hass.states.get(ENTITY_ID).state == "219"


@pytest.mark.usefixtures("init_integration")
async def test_reconnect_in_progress(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_handshake: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a slow reconnect is not started twice."""
    set_connected(mock_client, False)
    release = asyncio.Event()

    async def _slow_handshake(*args: object) -> None:
        await release.wait()
        raise PrinterUnreachableError("timeout")

    mock_handshake.side_effect = _slow_handshake
    freezer.tick(QUERY_INTERVAL)
    async_fire_time_changed(hass)
    await asyncio.sleep(0)
    freezer.tick(QUERY_INTERVAL)
    async_fire_time_changed(hass)
    await asyncio.sleep(0)
    release.set()
    await hass.async_block_till_done()
    assert mock_handshake.await_count == 2


async def test_firmware_update(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    printer_state: PrinterState,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test the device's firmware version follows the printer."""
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, MOCK_DEVICE_ID), init_integration.entry_id
    )
    assert device is not None
    assert device.sw_version == "2.7.2.7"

    push_state(mock_client, replace(printer_state, firmware_version="2.8.0.0"))
    await hass.async_block_till_done()
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, MOCK_DEVICE_ID), init_integration.entry_id
    )
    assert device is not None
    assert device.sw_version == "2.8.0.0"


@pytest.mark.usefixtures("init_integration")
async def test_update_entity_service(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Test asking for an update sends the queries."""
    await async_setup_component(hass, HOMEASSISTANT_DOMAIN, {})
    mock_client.query_all.reset_mock()
    await hass.services.async_call(
        HOMEASSISTANT_DOMAIN,
        SERVICE_UPDATE_ENTITY,
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )
    mock_client.query_all.assert_awaited_once()
    assert hass.states.get(ENTITY_ID).state == "219"


@pytest.mark.usefixtures("init_integration")
async def test_update_entity_service_disconnected(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Test asking for an update while disconnected keeps it unavailable."""
    await async_setup_component(hass, HOMEASSISTANT_DOMAIN, {})
    mock_client.is_connected = False
    await hass.services.async_call(
        HOMEASSISTANT_DOMAIN,
        SERVICE_UPDATE_ENTITY,
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("init_integration")
async def test_new_client_waits_for_info_report(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Test a state without the info report does not replace known values."""
    set_connected(mock_client, False)
    push_state(mock_client, PrinterState())
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE

    push_state(mock_client, state_from_fixture("info_idle.json"))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == "34"
