"""Tests for ignoring clients with randomised MAC addresses."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asusrouter.modules.client import (
    AsusClient,
    AsusClientConnection,
    AsusClientDescription,
)
from asusrouter.modules.connection import ConnectionState, ConnectionType
from homeassistant.components.device_tracker import CONF_CONSIDER_HOME
import pytest

from custom_components.asusrouter.const import ROUTER
from custom_components.asusrouter.router import ARDevice, is_random_mac

NORMAL_MAC = "a0:11:22:33:44:55"
RANDOM_MAC = "a2:11:22:33:44:55"


@pytest.mark.parametrize(
    ("mac", "expected"),
    [
        ("00:11:22:33:44:55", False),
        ("aa:bb:cc:dd:ee:ff", True),
        ("a2:11:22:33:44:55", True),
        ("a6:11:22:33:44:55", True),
        ("aa:11:22:33:44:55", True),
        ("ae:11:22:33:44:55", True),
        ("01:00:5e:00:00:01", False),
        ("03:00:00:00:00:01", True),
        ("ff:ff:ff:ff:ff:ff", True),
    ],
)
def test_is_random_mac(mac: str, expected: bool) -> None:
    """The locally administered bit determines whether a MAC is random."""

    assert is_random_mac(mac) is expected


def _api_client(name: str) -> AsusClient:
    """Return a connected API client."""

    return AsusClient(
        state=ConnectionState.CONNECTED,
        description=AsusClientDescription(name=name),
        connection=AsusClientConnection(
            type=ConnectionType.WIRED,
            online=True,
        ),
    )


def _router(
    clients: dict[str, AsusClient],
    *,
    ignore_random_mac: bool,
    known_clients: dict[str, object] | None = None,
) -> ARDevice:
    """Construct only the router state needed by update_clients."""

    router = ARDevice.__new__(ARDevice)
    router._options = {CONF_CONSIDER_HOME: 45}
    router.ignore_random_mac = ignore_random_mac
    router._conf_host = "router.test"
    router._mode = ROUTER
    router._connect_error = False
    router.bridge = SimpleNamespace(
        async_get_clients=AsyncMock(return_value=clients)
    )
    router._clients = known_clients or {}
    router._client_filter = "no_filter"
    router._client_filter_list = []
    router._clients_number = 0
    router._clients_list = []
    router._gn_clients_number = 0
    router._latest_connected = None
    router._latest_connected_list = []
    router.hass = Mock()
    router.fire_event = Mock()
    router.update_latest_connected = Mock()
    router._update_unpolled_sensors = AsyncMock()
    return router


@pytest.mark.asyncio
async def test_random_mac_is_ignored_for_new_client() -> None:
    """Only the normal new client is added when the option is enabled."""

    router = _router(
        {
            RANDOM_MAC: _api_client("Private phone"),
            NORMAL_MAC: _api_client("Normal device"),
        },
        ignore_random_mac=True,
    )

    with patch("custom_components.asusrouter.router.async_dispatcher_send"):
        await router.update_clients()

    assert list(router._clients) == [NORMAL_MAC]
    assert router._clients_number == 1
    router.fire_event.assert_called_once_with(
        "device_connected", router._clients[NORMAL_MAC].identity
    )


@pytest.mark.asyncio
async def test_random_mac_is_added_when_option_is_disabled() -> None:
    """Both new clients are added when the option is disabled."""

    router = _router(
        {
            RANDOM_MAC: _api_client("Private phone"),
            NORMAL_MAC: _api_client("Normal device"),
        },
        ignore_random_mac=False,
    )

    with patch("custom_components.asusrouter.router.async_dispatcher_send"):
        await router.update_clients()

    assert list(router._clients) == [RANDOM_MAC, NORMAL_MAC]
    assert router._clients_number == len(router._clients)
    assert [call.args[0] for call in router.fire_event.call_args_list] == [
        "device_connected",
        "device_connected",
    ]


@pytest.mark.asyncio
async def test_known_random_mac_still_updates() -> None:
    """The option does not suppress a randomised MAC that is already known."""

    known = Mock(state=True, connection=None, identity={"mac": RANDOM_MAC})
    client_info = _api_client("Known private phone")
    router = _router(
        {RANDOM_MAC: client_info},
        ignore_random_mac=True,
        known_clients={RANDOM_MAC: known},
    )

    with patch("custom_components.asusrouter.router.async_dispatcher_send"):
        await router.update_clients()

    known.update.assert_called_once_with(
        client_info,
        45,
        event_call=router.fire_event,
    )
    assert router._clients == {RANDOM_MAC: known}
    assert router._clients_number == 1
    router.fire_event.assert_not_called()
