"""Config flow for the Anycubic integration."""

import logging
from typing import Any, override

from anycubic_lan import (
    AnycubicLanError,
    LanModeDisabledError,
    PrinterConnectionInfo,
    UnsupportedPrinterError,
    handshake,
)
import probatio

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = probatio.Schema({probatio.Required(CONF_HOST): str})


class AnycubicConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Anycubic."""

    VERSION = 1

    _host: str
    _connection: PrinterConnectionInfo

    async def _async_try_handshake(self, host: str) -> str | None:
        """Run the handshake; return an error key on failure."""
        try:
            self._connection = await handshake(async_get_clientsession(self.hass), host)
        except LanModeDisabledError:
            return "not_in_lan_mode"
        except UnsupportedPrinterError:
            return "unsupported_printer"
        except AnycubicLanError:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected exception")
            return "unknown"
        return None

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if (error := await self._async_try_handshake(host)) is None:
                await self.async_set_unique_id(self._connection.device_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=self._connection.model_name, data={CONF_HOST: host}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    @override
    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Handle a printer discovered by DHCP."""
        self._host = discovery_info.ip
        self._async_abort_entries_match({CONF_HOST: self._host})
        if (error := await self._async_try_handshake(self._host)) is not None:
            return self.async_abort(reason=error)
        await self.async_set_unique_id(self._connection.device_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
        self.context["title_placeholders"] = {CONF_NAME: self._connection.model_name}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered printer."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._connection.model_name, data={CONF_HOST: self._host}
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={
                CONF_NAME: self._connection.model_name,
                CONF_HOST: self._host,
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address of a configured printer."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if (error := await self._async_try_handshake(host)) is None:
                await self.async_set_unique_id(self._connection.device_id)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input or entry.data
            ),
            errors=errors,
        )
