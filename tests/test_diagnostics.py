"""Tests for diagnostic-data redaction."""

from __future__ import annotations

from unittest.mock import Mock, patch

from homeassistant.components.diagnostics import async_redact_data
import pytest

from custom_components.asusrouter.const import (
    ASUSROUTER,
    DEVICE_ATTRIBUTE_LAST_ACTIVITY,
    DOMAIN,
)
from custom_components.asusrouter.diagnostics import (
    async_get_config_entry_diagnostics,
)

REDACTED = "**REDACTED**"
SENTINEL_IP = "198.51.100.42"
SENTINEL_MAC = "00:11:22:33:44:55"
SENTINEL_NAME = "diagnostic-client-secret"
SENTINEL_RADIUS_KEY = "diagnostic-radius-secret"
SENTINEL_WAN_IP = "203.0.113.42"


@pytest.mark.asyncio
async def test_diagnostics_redact_router_and_client_secrets() -> None:
    """Diagnostics redact config, entity, and tracked-client secrets."""

    entry = Mock(entry_id="entry-id")
    entry.as_dict.return_value = {"data": {"radius_key": SENTINEL_RADIUS_KEY}}

    tracked_device = Mock(name=SENTINEL_NAME, ip_address=SENTINEL_IP)
    tracked_device.extra_state_attributes = {
        DEVICE_ATTRIBUTE_LAST_ACTIVITY: "safe-last-activity"
    }
    router = Mock()
    router.device_info = {"identifiers": {(DOMAIN, "router-id")}}
    router.devices = {SENTINEL_MAC: tracked_device}

    wan_entry = Mock(
        entity_id="sensor.router_wan_ip_extra_secondary",
        domain="sensor",
        original_device_class="connectivity",
        original_name="WAN IP (Extra) (Secondary)",
        as_partial_dict={},
    )
    connectivity_entry = Mock(
        entity_id="binary_sensor.router_internet",
        domain="binary_sensor",
        original_device_class="connectivity",
        original_name="Internet",
        as_partial_dict={},
    )
    states = {
        wan_entry.entity_id: Mock(),
        connectivity_entry.entity_id: Mock(),
    }
    states[wan_entry.entity_id].as_dict.return_value = {
        "entity_id": wan_entry.entity_id,
        "state": SENTINEL_WAN_IP,
        "context": "not-useful",
        "attributes": {
            "mac": SENTINEL_MAC,
            "radius_key": SENTINEL_RADIUS_KEY,
            "safe": "visible",
        },
    }
    states[connectivity_entry.entity_id].as_dict.return_value = {
        "entity_id": connectivity_entry.entity_id,
        "state": "on",
        "context": "not-useful",
        "attributes": {},
    }

    hass = Mock()
    hass.data = {DOMAIN: {entry.entry_id: {ASUSROUTER: router}}}
    hass.states.get.side_effect = states.get

    hass_device = Mock(
        id="router-device-id",
        dict_repr={"identifiers": {(DOMAIN, "router-id")}},
    )
    device_registry = Mock()
    device_registry.async_get_device.return_value = hass_device
    redact = Mock(wraps=async_redact_data)

    with (
        patch(
            "custom_components.asusrouter.diagnostics.async_redact_data",
            redact,
        ),
        patch(
            "custom_components.asusrouter.diagnostics.dr.async_get",
            return_value=device_registry,
        ),
        patch("custom_components.asusrouter.diagnostics.er.async_get"),
        patch(
            "custom_components.asusrouter.diagnostics.er.async_entries_for_device",
            return_value=[wan_entry, connectivity_entry],
        ),
    ):
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["data"]["radius_key"] == REDACTED
    wan_state = diagnostics["device"]["entities"][wan_entry.entity_id]["state"]
    assert wan_state["state"] == REDACTED
    assert wan_state["attributes"] == {
        "mac": REDACTED,
        "radius_key": REDACTED,
        "safe": "visible",
    }
    assert (
        diagnostics["device"]["entities"][connectivity_entry.entity_id][
            "state"
        ]["state"]
        == "on"
    )
    assert ["state"] in [call.args[1] for call in redact.call_args_list]
    assert diagnostics["device"]["tracked_devices"] == [
        {
            "name": REDACTED,
            "ip_address": REDACTED,
            "last_activity": "safe-last-activity",
        }
    ]
