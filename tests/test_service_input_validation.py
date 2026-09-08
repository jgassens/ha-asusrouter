"""Tests for service input validation."""

from __future__ import annotations

from unittest.mock import Mock

from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.asusrouter.const import ASUSROUTER, DOMAIN
from custom_components.asusrouter.services import (
    DEVICE_INTERNET_ACCESS_SCHEMA,
    _get_router,
)
from tests.test_device_internet_access import _router


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
