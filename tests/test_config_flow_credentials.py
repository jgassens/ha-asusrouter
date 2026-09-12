"""Tests for credential changes and reauthentication."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

from asusrouter.error import AsusRouterAccessError
from asusrouter.modules.endpoint.error import AccessError
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
import pytest

from custom_components.asusrouter import update_listener
from custom_components.asusrouter.config_flow import (
    ARFlowHandler,
    AROptionsFlowHandler,
    _async_check_connection,
)
from custom_components.asusrouter.const import (
    ASUSROUTER,
    BASE,
    CONFIGS,
    DOMAIN,
    ERRORS,
    RESULT_LOGIN_BLOCKED,
    RESULT_WRONG_CREDENTIALS,
    ROUTER,
)
from custom_components.asusrouter.router import ARDevice

HOST = "192.0.2.1"
ENTRY_ID = "router-entry"
OLD_CREDENTIALS = {
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "old-password",
    CONF_PORT: 8443,
    CONF_SSL: True,
}
NEW_CREDENTIALS = {
    CONF_USERNAME: "new-admin",
    CONF_PASSWORD: "new-password",
    CONF_PORT: 443,
    CONF_SSL: True,
}


def _options_flow() -> AROptionsFlowHandler:
    """Create an options-flow shell with existing credentials."""

    flow = AROptionsFlowHandler()
    flow.hass = Mock()
    flow._configs = {CONF_HOST: HOST}
    flow._options = {**OLD_CREDENTIALS, "unchanged": "value"}
    flow._mode = ROUTER
    return flow


@pytest.mark.asyncio
async def test_options_credentials_rejection_preserves_options() -> None:
    """Rejected credentials remain on the form but not in saved options."""

    flow = _options_flow()
    original_options = flow._options.copy()
    candidate_options = {**original_options, **NEW_CREDENTIALS}

    with patch(
        "custom_components.asusrouter.config_flow._async_check_connection",
        new=AsyncMock(
            return_value={ERRORS: RESULT_WRONG_CREDENTIALS},
        ),
    ) as check_connection:
        result = await flow.async_step_credentials(NEW_CREDENTIALS)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"
    assert result["errors"] == {BASE: RESULT_WRONG_CREDENTIALS}
    assert flow._options == original_options
    password_key = next(
        key for key in result["data_schema"].schema if key == CONF_PASSWORD
    )
    assert password_key.default() == ""
    check_connection.assert_awaited_once_with(
        flow.hass, flow._configs, candidate_options
    )


@pytest.mark.asyncio
async def test_options_credentials_success_merges_options() -> None:
    """Validated credentials are merged while unrelated options survive."""

    flow = _options_flow()

    with patch(
        "custom_components.asusrouter.config_flow._async_check_connection",
        new=AsyncMock(return_value={CONFIGS: NEW_CREDENTIALS}),
    ):
        result = await flow.async_step_credentials(NEW_CREDENTIALS)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "options"
    assert flow._options == {**NEW_CREDENTIALS, "unchanged": "value"}


@pytest.mark.asyncio
async def test_password_only_option_update_reloads_entry() -> None:
    """Changing only the password requests a config-entry reload."""

    router = ARDevice.__new__(ARDevice)
    router._options = {CONF_PASSWORD: OLD_CREDENTIALS[CONF_PASSWORD]}
    config_entry = Mock(
        entry_id=ENTRY_ID,
        options={CONF_PASSWORD: NEW_CREDENTIALS[CONF_PASSWORD]},
    )
    hass = Mock()
    hass.config_entries.async_reload = AsyncMock()
    hass.data = {DOMAIN: {ENTRY_ID: {ASUSROUTER: router}}}

    await update_listener(hass, config_entry)

    hass.config_entries.async_reload.assert_awaited_once_with(ENTRY_ID)


def _wrapped_access_error(code: AccessError) -> AsusRouterAccessError:
    """Build the library's message-only wrapper around an access error."""

    original = AsusRouterAccessError("Access error", code, {"timeout": 30})
    wrapped = AsusRouterAccessError("Cannot access login.cgi")
    wrapped.__cause__ = original
    return wrapped


@pytest.mark.asyncio
async def test_setup_wrong_credentials_raises_auth_failed() -> None:
    """Rejected credentials start reauth instead of an endless retry loop."""

    router = ARDevice.__new__(ARDevice)
    router.bridge = Mock()
    router.bridge.async_connect = AsyncMock(
        side_effect=_wrapped_access_error(AccessError.CREDENTIALS)
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await router.setup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        _wrapped_access_error(AccessError.TRY_AGAIN),
        _wrapped_access_error(AccessError.ANOTHER),
        AsusRouterAccessError("Cannot access login.cgi, status 503"),
    ],
)
async def test_setup_transient_access_error_retries(
    error: AsusRouterAccessError,
) -> None:
    """Lockouts, other admins, and bad statuses retry instead of reauth."""

    router = ARDevice.__new__(ARDevice)
    router.bridge = Mock()
    router.bridge.async_connect = AsyncMock(side_effect=error)

    with pytest.raises(ConfigEntryNotReady):
        await router.setup()


@pytest.mark.asyncio
async def test_check_connection_reads_wrapped_credentials_error() -> None:
    """The flow finds the credentials code behind the library wrapper."""

    with patch(
        "custom_components.asusrouter.config_flow.ARBridge"
    ) as bridge_class:
        bridge_class.return_value.async_connect = AsyncMock(
            side_effect=_wrapped_access_error(AccessError.CREDENTIALS)
        )
        bridge_class.return_value.async_clean = AsyncMock()
        result = await _async_check_connection(Mock(), {CONF_HOST: HOST})

    assert result == {ERRORS: RESULT_WRONG_CREDENTIALS}


@pytest.mark.asyncio
async def test_check_connection_reads_wrapped_try_again_error() -> None:
    """A lockout is reported with its remaining time, not as unknown."""

    with patch(
        "custom_components.asusrouter.config_flow.ARBridge"
    ) as bridge_class:
        bridge_class.return_value.async_connect = AsyncMock(
            side_effect=_wrapped_access_error(AccessError.TRY_AGAIN)
        )
        bridge_class.return_value.async_clean = AsyncMock()
        result = await _async_check_connection(Mock(), {CONF_HOST: HOST})

    assert result[ERRORS] == RESULT_LOGIN_BLOCKED


@pytest.mark.asyncio
async def test_reauth_confirm_updates_options_and_aborts() -> None:
    """Valid replacement credentials update and reload the existing entry."""

    entry = Mock(
        data={CONF_HOST: HOST},
        entry_id=ENTRY_ID,
        options={**OLD_CREDENTIALS, "unchanged": "value"},
        title="Test router",
        update_listeners=[],
    )
    config_entries = Mock()
    config_entries.async_get_known_entry.return_value = entry
    config_entries.async_update_entry.return_value = True
    hass = Mock(config_entries=config_entries)
    flow = ARFlowHandler()
    flow.hass = hass
    flow.context = {"source": SOURCE_REAUTH, "entry_id": ENTRY_ID}

    initial_result = await flow.async_step_reauth(entry.data)

    assert initial_result["type"] is FlowResultType.FORM
    assert initial_result["step_id"] == "reauth_confirm"
    assert initial_result["description_placeholders"] == {
        "name": "Test router"
    }

    submitted = {
        CONF_USERNAME: NEW_CREDENTIALS[CONF_USERNAME],
        CONF_PASSWORD: NEW_CREDENTIALS[CONF_PASSWORD],
    }
    checked_options = {**entry.options, **submitted}
    with patch(
        "custom_components.asusrouter.config_flow._async_check_connection",
        new=AsyncMock(return_value={CONFIGS: checked_options}),
    ) as check_connection:
        result = await flow.async_step_reauth_confirm(submitted)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    check_connection.assert_awaited_once_with(
        hass, dict(entry.data), checked_options
    )
    assert config_entries.async_update_entry.call_args.kwargs["options"] == (
        checked_options
    )
    config_entries.async_schedule_reload.assert_called_once_with(ENTRY_ID)


@pytest.mark.asyncio
async def test_reauth_confirm_leaves_reload_to_update_listener() -> None:
    """A loaded entry reloads through its listener, not a second reload."""

    entry = Mock(
        data={CONF_HOST: HOST},
        entry_id=ENTRY_ID,
        options={**OLD_CREDENTIALS},
        title="Test router",
        update_listeners=[Mock()],
    )
    config_entries = Mock()
    config_entries.async_get_known_entry.return_value = entry
    hass = Mock(config_entries=config_entries)
    flow = ARFlowHandler()
    flow.hass = hass
    flow.context = {"source": SOURCE_REAUTH, "entry_id": ENTRY_ID}
    submitted = {CONF_USERNAME: "new-admin", CONF_PASSWORD: "new-password"}

    with patch(
        "custom_components.asusrouter.config_flow._async_check_connection",
        new=AsyncMock(return_value={CONFIGS: {**entry.options, **submitted}}),
    ):
        result = await flow.async_step_reauth_confirm(submitted)

    assert result["reason"] == "reauth_successful"
    config_entries.async_update_entry.assert_called_once()
    config_entries.async_schedule_reload.assert_not_called()
