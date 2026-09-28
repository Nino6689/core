"""Tests for the Anycubic diagnostics."""

import json
from unittest.mock import AsyncMock

from anycubic_lan import PrinterConnectionInfo
from anycubic_lan.handshake import parse_discovery
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.core import HomeAssistant

from .conftest import make_connection_info

from tests.common import MockConfigEntry
from tests.components.diagnostics import get_diagnostics_for_config_entry
from tests.typing import ClientSessionGenerator

UPLOAD_TOKEN = "5f3a9c1e7b2d4a60"
UPLOAD_URL = f"http://192.168.1.50:18910/gcode_upload?s={UPLOAD_TOKEN}"


@pytest.mark.usefixtures("init_integration")
async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test diagnostics are redacted."""
    assert (
        await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)
        == snapshot
    )


@pytest.mark.usefixtures("mock_client")
@pytest.mark.parametrize(
    "extra_fields",
    [
        pytest.param({}, id="live_document"),
        pytest.param({"urls": {"fileUploadurl": UPLOAD_URL}}, id="info_urls"),
        pytest.param({"FILEUPLOADURL": UPLOAD_URL}, id="key_case"),
        pytest.param({"uploadLinks": [UPLOAD_URL]}, id="unknown_key"),
    ],
)
async def test_diagnostics_hide_signed_upload_url(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    mock_handshake: AsyncMock,
    extra_fields: dict[str, object],
) -> None:
    """Test the signed upload URL and its token never reach diagnostics."""
    connection = make_connection_info()
    document = connection.discovery.raw
    # The live discovery document carries the signed upload URL.
    assert document["fileUploadurl"] == UPLOAD_URL
    mock_handshake.return_value = PrinterConnectionInfo(
        host=connection.host,
        discovery=parse_discovery({**document, **extra_fields}),
        credentials=connection.credentials,
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    dumped = json.dumps(
        await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)
    )

    assert "gcode_upload" not in dumped
    assert UPLOAD_TOKEN not in dumped
    # The info report fixture carries its own signed URL in state.
    assert "SIGNED" not in dumped
