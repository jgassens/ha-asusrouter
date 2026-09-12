"""Tests for service input validation."""

from __future__ import annotations

from unittest.mock import Mock, patch

from asusrouter.modules.parental_control import PCRuleType
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.asusrouter.bridge import ARBridge
from custom_components.asusrouter.const import ASUSROUTER, DOMAIN
from custom_components.asusrouter.helpers import normalize_mac
from custom_components.asusrouter.services import (
    DEVICE_INTERNET_ACCESS_SCHEMA,
    _get_router,
    _internet_access_entity_data,
)
from tests.test_device_internet_access import _router, _service_hass


@pytest.mark.parametrize(
    "mac",
    [
        "AA:BB:CC:DD:EE:FF",
        "AA-BB-CC-DD-EE-FF",
        "AABBCCDDEEFF",
    ],
)
def test_internet_access_schema_normalizes_mac(mac: str) -> None:
    """Every accepted spelling should become one canonical MAC."""

    validated = DEVICE_INTERNET_ACCESS_SCHEMA(
        {"devices": [{"mac": mac}], "state": "block"}
    )

    assert validated["devices"] == [{"mac": "aa:bb:cc:dd:ee:ff"}]


@pytest.mark.parametrize(
    "name",
    [
        "kid>extra",
        "kid<extra",
        "kid&#60extra",
        "kid&#62extra",
        "kid\nextra",
        "x" * 33,
    ],
)
def test_internet_access_schema_rejects_unsafe_direct_name(
    name: str,
) -> None:
    """Unsafe direct names should fail before a router can be written."""

    router = _router()

    with pytest.raises(ServiceValidationError):
        DEVICE_INTERNET_ACCESS_SCHEMA(
            {
                "devices": [{"mac": "AA:BB:CC:DD:EE:FF", "name": name}],
                "state": "block",
            }
        )

    router.async_set_internet_access.assert_not_awaited()
    router.bridge.async_pc_rule.assert_not_awaited()


@pytest.mark.parametrize(
    "domain_data",
    [
        {},
        {"router-1": {}},
    ],
)
def test_get_router_rejects_unknown_or_unloaded_entry(
    domain_data: dict[str, dict[str, object]],
) -> None:
    """User-selected unavailable entries should be validation errors."""

    hass = Mock()
    hass.data = {DOMAIN: domain_data}

    with pytest.raises(ServiceValidationError, match="not loaded: router-1"):
        _get_router(hass, "router-1")


def test_get_router_returns_loaded_entry() -> None:
    """The validation helper should preserve a loaded router."""

    router = _router()
    hass = Mock()
    hass.data = {DOMAIN: {"router-1": {ASUSROUTER: router}}}

    assert _get_router(hass, "router-1") is router


@pytest.mark.parametrize(
    "mac",
    ["not-a-mac", "aa:bb:cc:dd:ee", "aabbccddeeff00", "", "aa:bb:cc:dd:ee:gg"],
)
def test_normalize_mac_rejects_malformed_values(mac: str) -> None:
    """HA's format_mac returns junk unchanged; the helper must not."""

    with pytest.raises(ValueError, match="not a MAC address"):
        normalize_mac(mac)


@pytest.mark.parametrize(
    "mac", ["AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "AABBCCDDEEFF"]
)
def test_normalize_mac_accepts_every_supported_form(mac: str) -> None:
    """All accepted spellings map to one canonical form."""

    assert normalize_mac(mac) == "aa:bb:cc:dd:ee:ff"


def test_pc_device2rule_rejects_malformed_mac() -> None:
    """A junk MAC must not become a new router rule."""

    bridge = ARBridge.__new__(ARBridge)
    with pytest.raises(ServiceValidationError, match="MAC"):
        bridge._pc_device2rule({"mac": "not-a-mac"}, PCRuleType.BLOCK)


def test_entity_data_rejects_malformed_tracker_mac() -> None:
    """A tracker carrying a junk MAC is reported, not written."""

    router = _router()
    hass, _handlers = _service_hass(router)
    entry = Mock(
        domain="device_tracker",
        platform=DOMAIN,
        config_entry_id="router-1",
        capabilities={"mac": "not-a-mac"},
    )
    registry = Mock()
    registry.async_get.return_value = entry

    with (
        patch(
            "custom_components.asusrouter.services.er.async_get",
            return_value=registry,
        ),
        pytest.raises(ServiceValidationError, match="malformed"),
    ):
        _internet_access_entity_data(hass, "device_tracker.console")
