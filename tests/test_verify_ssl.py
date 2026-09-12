"""Tests for SSL certificate verification settings."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from asusrouter.connection_config import ARConnectionConfigKey as ARCCKey
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.asusrouter.bridge import ARBridge
from custom_components.asusrouter.config_flow import (
    AROptionsFlowHandler,
    _create_form_credentials,
)
from custom_components.asusrouter.const import CONFIGS, ROUTER
from custom_components.asusrouter.router import ARDevice

HOST = "192.0.2.1"
CREDENTIALS = {
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "password",
    CONF_PORT: 8443,
    CONF_SSL: True,
}
CONFIG = {CONF_HOST: HOST, **CREDENTIALS}


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param({CONF_VERIFY_SSL: True}, True, id="enabled"),
        pytest.param({CONF_VERIFY_SSL: False}, False, id="disabled"),
        pytest.param(None, False, id="absent"),
    ],
)
def test_bridge_configures_ssl_verification(
    options: dict[str, Any] | None, expected: bool
) -> None:
    """The HA session and library connection use the selected setting."""

    hass = Mock()
    session = Mock()
    cookie_jar = Mock()

    with (
        patch(
            "custom_components.asusrouter.bridge.async_create_clientsession",
            return_value=session,
        ) as create_session,
        patch(
            "custom_components.asusrouter.bridge.get_cookie_jar",
            return_value=cookie_jar,
        ),
        patch("custom_components.asusrouter.bridge.AsusRouter") as api_class,
    ):
        ARBridge(hass, CONFIG, options)

    create_session.assert_called_once_with(
        hass,
        verify_ssl=expected,
        cookie_jar=cookie_jar,
    )
    assert api_class.call_args.kwargs["session"] is session
    assert api_class.call_args.kwargs["connection_config"] == {
        ARCCKey.VERIFY_SSL: expected
    }


def test_credentials_form_defaults_verify_ssl_off() -> None:
    """The credentials form offers certificate verification defaulting off."""

    schema = _create_form_credentials()
    verify_ssl_key = next(
        key for key in schema.schema if key == CONF_VERIFY_SSL
    )

    assert verify_ssl_key.default() is False
    assert schema({})[CONF_VERIFY_SSL] is False


@pytest.mark.asyncio
async def test_verify_ssl_change_validates_and_requires_reload() -> None:
    """Changing only certificate verification validates and reloads."""

    flow = AROptionsFlowHandler()
    flow.hass = Mock()
    flow._configs = {CONF_HOST: HOST}
    flow._options = {**CREDENTIALS, CONF_VERIFY_SSL: False}
    flow._mode = ROUTER
    submitted = {**CREDENTIALS, CONF_VERIFY_SSL: True}

    with patch(
        "custom_components.asusrouter.config_flow._async_check_connection",
        new=AsyncMock(return_value={CONFIGS: submitted}),
    ) as check_connection:
        result = await flow.async_step_credentials(submitted)

    assert result["type"] is FlowResultType.MENU
    check_connection.assert_awaited_once_with(
        flow.hass, flow._configs, submitted
    )

    router = ARDevice.__new__(ARDevice)
    router._options = {CONF_VERIFY_SSL: False}
    assert router.update_options({CONF_VERIFY_SSL: True}) is True
