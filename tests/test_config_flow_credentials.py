"""Tests for credential changes and reauthentication."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

from asusrouter.error import AsusRouterAccessError
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed
import pytest

from custom_components.asusrouter import update_listener
from custom_components.asusrouter.config_flow import (
    ARFlowHandler,
    AROptionsFlowHandler,
)
from custom_components.asusrouter.const import (
    ASUSROUTER,
    BASE,
    CONFIGS,
    DOMAIN,
    ERRORS,
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


@pytest.mark.asyncio
async def test_setup_access_error_raises_auth_failed() -> None:
    """An access failure starts reauth instead of an endless retry loop."""

    router = ARDevice.__new__(ARDevice)
    router.bridge = Mock()
    router.bridge.async_connect = AsyncMock(
        side_effect=AsusRouterAccessError("access denied")
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await router.setup()


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
