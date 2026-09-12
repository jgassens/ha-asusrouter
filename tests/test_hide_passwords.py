"""Tests for hiding secret entity attributes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.asusrouter import binary_sensor, switch
from custom_components.asusrouter.const import (
    ASUSROUTER,
    CONF_DEFAULT_HIDE_PASSWORDS,
    CONF_HIDE_PASSWORDS,
    DOMAIN,
    HIDDEN_SECRET_ATTRIBUTES,
)
from custom_components.asusrouter.dataclass import ARSwitchDescription
from custom_components.asusrouter.entity import async_setup_ar_entry


def test_default_hide_passwords_includes_radius_key() -> None:
    """Secrets, including RADIUS keys, are hidden by default."""

    assert CONF_DEFAULT_HIDE_PASSWORDS is True
    assert "radius_key" in HIDDEN_SECRET_ATTRIBUTES
    assert "psk_state" in HIDDEN_SECRET_ATTRIBUTES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "expected_hide"),
    [({}, list(HIDDEN_SECRET_ATTRIBUTES)), ({CONF_HIDE_PASSWORDS: False}, [])],
)
async def test_switch_hide_passwords_option(
    options: dict[str, bool], expected_hide: list[str]
) -> None:
    """Switch setup honors the default and explicit hide-passwords option."""

    router = SimpleNamespace(pc_rules={}, signal_pc_rules_new="pc_rules")
    hass = SimpleNamespace(data={DOMAIN: {"entry": {ASUSROUTER: router}}})
    entry = SimpleNamespace(
        entry_id="entry", options=options, async_on_unload=Mock()
    )

    with (
        patch.object(
            switch, "async_setup_ar_entry", new_callable=AsyncMock
        ) as setup,
        patch.object(switch, "async_dispatcher_connect", return_value=Mock()),
        patch.object(switch, "add_entities"),
    ):
        await switch.async_setup_entry(hass, entry, Mock())

    assert setup.await_args.args[-1] == expected_hide


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "expected_hide"),
    [({}, list(HIDDEN_SECRET_ATTRIBUTES)), ({CONF_HIDE_PASSWORDS: False}, [])],
)
async def test_binary_sensor_hide_passwords_option(
    options: dict[str, bool], expected_hide: list[str]
) -> None:
    """Binary-sensor setup honors the default and explicit option."""

    router = SimpleNamespace(
        signal_aimesh_new="aimesh",
        signal_device_new="devices",
        async_on_close=Mock(),
    )
    hass = SimpleNamespace(data={DOMAIN: {"entry": {ASUSROUTER: router}}})
    entry = SimpleNamespace(entry_id="entry", options=options)

    with (
        patch.object(
            binary_sensor, "async_setup_ar_entry", new_callable=AsyncMock
        ) as setup,
        patch.object(
            binary_sensor, "async_dispatcher_connect", return_value=Mock()
        ),
        patch.object(binary_sensor, "add_entities"),
        patch.object(binary_sensor, "add_static_dhcp_entities"),
    ):
        await binary_sensor.async_setup_entry(hass, entry, Mock())

    assert setup.await_args.args[-1] == expected_hide


@pytest.mark.asyncio
async def test_hiding_does_not_mutate_shared_descriptions() -> None:
    """Hiding filters a per-entry copy; the module-level description stays."""

    description = ARSwitchDescription(
        key="wlan_0",
        key_group="wlan",
        name="Wi-Fi",
        extra_state_attributes={"wpa_psk": "password", "ssid": "ssid"},
    )
    router = Mock()
    router.sensor_coordinator = {
        "wlan": {"coordinator": Mock(), "wlan": {"wlan_0": True}}
    }
    hass = Mock()
    hass.data = {DOMAIN: {"entry-1": {ASUSROUTER: router}}}
    entry = Mock(entry_id="entry-1")
    created = []

    def entity_class(_coordinator, _router, desc):
        created.append(desc)
        return Mock()

    await async_setup_ar_entry(
        hass, entry, Mock(), [description], entity_class, ["password"]
    )

    assert created[0].extra_state_attributes == {"ssid": "ssid"}
    assert description.extra_state_attributes == {
        "wpa_psk": "password",
        "ssid": "ssid",
    }
