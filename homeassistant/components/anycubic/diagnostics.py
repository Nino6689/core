"""Diagnostics support for the Anycubic integration."""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import AnycubicConfigEntry

TO_REDACT = {
    "cn",
    "fileUploadurl",
    "file_upload_url",
    "mac",
    "password",
    "serial",
    "token",
    "username",
    "usn",
}

# The signed upload URL's "s" parameter authorises uploads to the printer.
UPLOAD_URL_MARKER = "gcode_upload?s="


def _scrub_upload_urls(value: Any) -> Any:
    """Redact any string carrying a signed upload URL, whatever its key."""
    if isinstance(value, Mapping):
        return {key: _scrub_upload_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_upload_urls(item) for item in value]
    if isinstance(value, str) and UPLOAD_URL_MARKER in value.lower():
        return REDACTED
    return value


def _as_dict(value: Any) -> Any:
    """Convert library objects to JSON-friendly values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _as_dict(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _as_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_dict(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AnycubicConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    connection = coordinator.connection
    diagnostics = async_redact_data(
        {
            "printer": {
                "model_id": connection.model_id,
                "model": connection.model,
                "model_name": connection.model_name,
                "serial": connection.serial,
                "mac": connection.mac,
            },
            "discovery": connection.discovery.as_redacted_dict(),
            "connected": coordinator.client is not None
            and coordinator.client.is_connected,
            "state": _as_dict(coordinator.data),
        },
        TO_REDACT,
    )
    return {key: _scrub_upload_urls(value) for key, value in diagnostics.items()}
