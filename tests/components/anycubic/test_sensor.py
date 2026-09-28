"""Tests for the Anycubic sensors."""

from dataclasses import replace
from unittest.mock import MagicMock

from anycubic_lan import PrinterState, ReportKind, parse_message
from freezegun.api import FrozenDateTimeFactory
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import push_state, state_from_fixture

from tests.common import MockConfigEntry, snapshot_platform

PREFIX = "sensor.anycubic_kobra_s1"


@pytest.fixture(autouse=True)
def freeze_time(freezer: FrozenDateTimeFactory) -> None:
    """Freeze time for the print end time."""
    freezer.move_to("2026-09-26 12:00:30+00:00")


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "mock_handshake")
@pytest.mark.parametrize(
    "printer_state",
    [
        pytest.param(state_from_fixture("info_printing.json"), id="printing"),
        pytest.param(state_from_fixture("info_idle.json"), id="idle"),
    ],
)
async def test_sensors(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Test the sensor entities and their states."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_chamber_sensor_added(
    hass: HomeAssistant, mock_client: MagicMock, printer_state: PrinterState
) -> None:
    """Test the chamber sensor appears once a chamber reading is reported."""
    assert hass.states.get(f"{PREFIX}_chamber_temperature") is None

    report = parse_message(
        {
            "type": ReportKind.TEMPERATURE.value,
            "action": "query",
            "state": "done",
            "data": {"curr_chamber_temp": 35, "target_chamber_temp": 40},
        }
    )
    assert report is not None
    push_state(mock_client, printer_state.apply(report))
    await hass.async_block_till_done()
    assert hass.states.get(f"{PREFIX}_chamber_temperature").state == "35"

    # Later updates do not add it again; the two fan sensors are disabled.
    push_state(mock_client, printer_state.apply(report))
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids("sensor")) == 16


@pytest.mark.usefixtures("mock_handshake")
@pytest.mark.parametrize(
    "printer_state",
    [state_from_fixture("info_printing.json", "print_failed.json")],
)
async def test_last_error_code(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Test the last error code the printer reported is shown."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(f"{PREFIX}_last_error_code").state == "10108"


@pytest.mark.usefixtures("init_integration")
async def test_unknown_values(
    hass: HomeAssistant, mock_client: MagicMock, printer_state: PrinterState
) -> None:
    """Test values the integration cannot map read as unknown."""
    assert printer_state.job is not None
    push_state(
        mock_client,
        replace(
            printer_state,
            printer_state="updating",
            speed_mode_raw=7,
            job=replace(
                printer_state.job, print_status=None, remain_time=None, filename=None
            ),
        ),
    )
    await hass.async_block_till_done()
    for key in ("speed_mode", "job_status", "print_end_time", "job_name"):
        assert hass.states.get(f"{PREFIX}_{key}").state == STATE_UNKNOWN, key

    push_state(mock_client, replace(printer_state, printer_state="updating", job=None))
    await hass.async_block_till_done()
    assert hass.states.get(f"{PREFIX}_status").state == STATE_UNKNOWN
    assert hass.states.get(f"{PREFIX}_job_status").state == "idle"
    assert hass.states.get(f"{PREFIX}_print_progress").state == STATE_UNKNOWN


@pytest.mark.usefixtures("init_integration")
async def test_finished_job(
    hass: HomeAssistant, mock_client: MagicMock, printer_state: PrinterState
) -> None:
    """Test a finished job has no end time but keeps its last values."""
    assert printer_state.job is not None
    push_state(
        mock_client,
        replace(printer_state, job=replace(printer_state.job, state="finished")),
    )
    await hass.async_block_till_done()
    assert hass.states.get(f"{PREFIX}_print_end_time").state == STATE_UNKNOWN
    assert hass.states.get(f"{PREFIX}_print_progress").state == "60"
