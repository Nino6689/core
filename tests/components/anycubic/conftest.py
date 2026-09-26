"""Common fixtures for the Anycubic tests."""

from collections.abc import Callable, Generator
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

from anycubic_lan import (
    BrokerCredentials,
    DiscoveryInfo,
    PrinterConnectionInfo,
    PrinterState,
    parse_message,
)
import pytest

from homeassistant.components.anycubic.const import DOMAIN
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry, load_json_object_fixture

MOCK_HOST = "192.168.1.50"
MOCK_DEVICE_ID = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"


def state_from_fixture(*names: str) -> PrinterState:
    """Build a printer state from captured report fixtures."""
    state = PrinterState()
    for name in names:
        report = parse_message(load_json_object_fixture(name, DOMAIN))
        assert report is not None
        state = state.apply(report)
    return state


def make_connection_info(device_id: str = MOCK_DEVICE_ID) -> PrinterConnectionInfo:
    """Build what the handshake returns for the test printer."""
    document = load_json_object_fixture("discovery.json", DOMAIN)
    return PrinterConnectionInfo(
        host=MOCK_HOST,
        discovery=DiscoveryInfo(
            token=document["token"],
            ctrl_info_url=document["ctrlInfoUrl"],
            model_id=document["modelId"],
            ctrl_type=document["ctrlType"],
            serial=document["cn"],
            usn=document["usn"],
            model_name=document["modelName"],
            device_type=document["deviceType"],
            raw=MappingProxyType(document),
        ),
        credentials=BrokerCredentials(
            host=MOCK_HOST,
            port=9883,
            username="user",
            password="secret",
            device_id=device_id,
        ),
    )


def push_state(client: MagicMock, state: PrinterState) -> None:
    """Deliver a state update to the listeners registered on the client."""
    client.state = state
    for listener in list(client.state_listeners):
        listener(state)


def set_connected(client: MagicMock, connected: bool) -> None:
    """Change the client's connection and notify its listeners."""
    client.is_connected = connected
    for listener in list(client.connection_listeners):
        listener(connected)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return the default mocked config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Anycubic Kobra S1",
        data={CONF_HOST: MOCK_HOST},
        unique_id=MOCK_DEVICE_ID,
    )


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "homeassistant.components.anycubic.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        yield mock_setup_entry


@pytest.fixture
def mock_handshake() -> Generator[AsyncMock]:
    """Mock the LAN Mode handshake."""
    handshake = AsyncMock(return_value=make_connection_info())
    with (
        patch("homeassistant.components.anycubic.config_flow.handshake", handshake),
        patch("homeassistant.components.anycubic.coordinator.handshake", handshake),
    ):
        yield handshake


@pytest.fixture
def printer_state() -> PrinterState:
    """Return the state the printer reports after connecting."""
    return state_from_fixture("info_printing.json")


@pytest.fixture
def mock_client_class(printer_state: PrinterState) -> Generator[MagicMock]:
    """Mock the MQTT client; connecting delivers the printer's first report."""

    def _listener(name: str) -> Callable[[Callable[..., None]], Callable[[], None]]:
        def add(callback: Callable[..., None]) -> Callable[[], None]:
            getattr(client, name).append(callback)
            return lambda: getattr(client, name).remove(callback)

        return add

    async def _connect() -> None:
        client.is_connected = True
        push_state(client, client.state)

    async def _disconnect() -> None:
        client.is_connected = False

    with patch(
        "homeassistant.components.anycubic.coordinator.AnycubicLanClient"
    ) as client_class:
        client = client_class.return_value
        client.state = printer_state
        client.is_connected = False
        client.state_listeners = []
        client.connection_listeners = []
        client.add_state_listener.side_effect = _listener("state_listeners")
        client.add_connection_listener.side_effect = _listener("connection_listeners")
        client.connect = AsyncMock(side_effect=_connect)
        client.disconnect = AsyncMock(side_effect=_disconnect)
        client.query_all = AsyncMock()
        yield client_class


@pytest.fixture
def mock_client(mock_client_class: MagicMock) -> MagicMock:
    """Return the mocked MQTT client instance."""
    return mock_client_class.return_value


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_handshake: AsyncMock,
    mock_client: MagicMock,
) -> MockConfigEntry:
    """Set up the integration."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
