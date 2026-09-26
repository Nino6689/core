"""Coordinator for the Anycubic integration."""

import asyncio
from collections.abc import Callable
from datetime import datetime
import logging
from typing import override

from anycubic_lan import (
    AnycubicLanClient,
    AnycubicLanError,
    NotConnectedError,
    PrinterConnectionInfo,
    PrinterState,
    handshake,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, FIRST_REPORT_TIMEOUT, QUERY_INTERVAL

_LOGGER = logging.getLogger(__name__)

type AnycubicConfigEntry = ConfigEntry[AnycubicCoordinator]


class AnycubicCoordinator(DataUpdateCoordinator[PrinterState]):
    """Keep the MQTT connection to one printer and its latest state.

    Reports are pushed by the printer; queries are sent on a fixed timer of
    our own because a pushed update would otherwise reset the coordinator's
    refresh interval and starve the queries.
    """

    config_entry: AnycubicConfigEntry
    connection: PrinterConnectionInfo

    def __init__(self, hass: HomeAssistant, config_entry: AnycubicConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, _LOGGER, config_entry=config_entry, name=DOMAIN)
        self.client: AnycubicLanClient | None = None
        self._unsub_client: list[Callable[[], None]] = []
        self._first_report = asyncio.Event()
        self._reconnect_lock = asyncio.Lock()
        self._firmware_version: str | None = None

    @property
    def host(self) -> str:
        """Return the configured host of the printer."""
        return str(self.config_entry.data[CONF_HOST])

    async def _async_handshake(self) -> PrinterConnectionInfo:
        """Run the LAN Mode handshake against the configured host."""
        return await handshake(async_get_clientsession(self.hass), self.host)

    @override
    async def _async_setup(self) -> None:
        """Connect to the printer and wait for its first info report."""
        try:
            self.connection = await self._async_handshake()
        except AnycubicLanError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"host": self.host, "error": str(err)},
            ) from err
        if self.connection.device_id != self.config_entry.unique_id:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="wrong_device",
                translation_placeholders={"host": self.host},
            )
        try:
            await self._async_connect()
            async with asyncio.timeout(FIRST_REPORT_TIMEOUT):
                await self._first_report.wait()
        except (AnycubicLanError, TimeoutError) as err:
            await self._async_disconnect()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"host": self.host, "error": repr(err)},
            ) from err
        self.config_entry.async_on_unload(
            async_track_time_interval(
                self.hass,
                self._async_query,
                QUERY_INTERVAL,
                name=f"{DOMAIN} query {self.host}",
                cancel_on_shutdown=True,
            )
        )

    @override
    async def _async_update_data(self) -> PrinterState:
        """Ask the printer for every report; the answers arrive as pushes."""
        if not await self._async_send_queries():
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="not_connected",
                translation_placeholders={"host": self.host},
            )
        return self.data

    @override
    async def async_shutdown(self) -> None:
        """Disconnect from the printer."""
        await super().async_shutdown()
        await self._async_disconnect()

    async def _async_connect(self) -> None:
        """Create a client for the current connection info and connect it."""
        client = AnycubicLanClient(self.connection)
        self._first_report.clear()
        self._unsub_client = [
            client.add_state_listener(self._handle_state),
            client.add_connection_listener(self._handle_connection),
        ]
        self.client = client
        try:
            await client.connect()
        except AnycubicLanError:
            await self._async_disconnect()
            raise

    async def _async_disconnect(self) -> None:
        """Stop listening to the client and disconnect it."""
        while self._unsub_client:
            self._unsub_client.pop()()
        if self.client is not None:
            await self.client.disconnect()

    async def _async_send_queries(self) -> bool:
        """Ask the printer for every report; return False when disconnected."""
        if self.client is None or not self.client.is_connected:
            return False
        try:
            await self.client.query_all()
        except NotConnectedError:
            return False
        return True

    async def _async_query(self, _now: datetime) -> None:
        """Send the periodic queries, or reconnect when the printer is gone."""
        if await self._async_send_queries():
            return
        self._async_set_unavailable()
        if self._reconnect_lock.locked():
            return
        async with self._reconnect_lock:
            await self._async_reconnect()

    async def _async_reconnect(self) -> None:
        """Run the handshake again and connect with fresh credentials.

        The printer rotates its broker credentials when it restarts, so the
        client's own reconnect attempts with the old ones may never succeed.
        """
        try:
            connection = await self._async_handshake()
        except AnycubicLanError as err:
            _LOGGER.debug("Printer at %s is still unreachable: %s", self.host, err)
            return
        if connection.device_id != self.connection.device_id:
            _LOGGER.debug("A different printer now answers at %s", self.host)
            return
        await self._async_disconnect()
        self.connection = connection
        try:
            await self._async_connect()
        except AnycubicLanError as err:
            _LOGGER.debug("Reconnecting to %s failed: %s", self.host, err)

    @callback
    def _async_set_unavailable(self) -> None:
        """Mark the printer unavailable, logging only the first time."""
        if not self.last_update_success:
            return
        _LOGGER.warning("Lost connection to the printer at %s", self.host)
        self.last_update_success = False
        self.async_update_listeners()

    @callback
    def _handle_connection(self, connected: bool) -> None:
        """Handle the client losing or regaining its connection.

        A regained connection is only reported once the printer has sent
        state again, so entities never show stale values in between.
        """
        if not connected:
            self._async_set_unavailable()

    @callback
    def _handle_state(self, state: PrinterState) -> None:
        """Handle a state update pushed by the printer."""
        # A new client knows nothing until the printer's info report arrives.
        if state.printer_state is None:
            return
        self._first_report.set()
        if not self.last_update_success:
            _LOGGER.info("Connection to the printer at %s restored", self.host)
        if state.firmware_version != self._firmware_version:
            self._firmware_version = state.firmware_version
            self._async_update_sw_version(state.firmware_version)
        self.async_set_updated_data(state)

    @callback
    def _async_update_sw_version(self, firmware_version: str | None) -> None:
        """Keep the device's firmware version current after an update."""
        registry = dr.async_get(self.hass)
        if device := registry.async_get_device_by_identifier(
            (DOMAIN, self.connection.device_id), self.config_entry.entry_id
        ):
            registry.async_update_device(device.id, sw_version=firmware_version)
