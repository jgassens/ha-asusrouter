"""Tests for parental-control capacity validation."""

from __future__ import annotations

from unittest.mock import Mock, patch

from asusrouter.error import AsusRouterError
from asusrouter.modules.parental_control import (
    DEFAULT_PC_MAX_RULES,
    DEFAULT_PC_TIMEMAP,
    ParentalControlCapabilities,
    ParentalControlCapacityError,
    ParentalControlRule,
    PCRuleType,
    read_pc_rules,
)
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.asusrouter.services import (
    SERVICE_DEVICE_INTERNET_ACCESS,
    async_setup_services,
)
from tests import test_device_internet_access as internet_access

RULE_LIMIT = DEFAULT_PC_MAX_RULES
OVER_LIMIT_RULE_COUNT = RULE_LIMIT + 1


def _mac(index: int) -> str:
    """Build a distinct MAC address for a rule fixture."""

    return f"00:00:00:00:00:{index:02X}"


def _rules(count: int) -> dict[str, ParentalControlRule]:
    """Build a rule table whose default timemaps each have two windows."""

    return {
        (mac := _mac(index)): ParentalControlRule(
            mac=mac,
            name=f"Retained {index}",
            timemap=DEFAULT_PC_TIMEMAP,
            type=PCRuleType.BLOCK,
        )
        for index in range(count)
    }


def _written_rules(bridge: object) -> dict[str, ParentalControlRule]:
    """Decode the rule table from a bridge write."""

    arguments = bridge.api.async_run_service_result.await_args.kwargs[
        "arguments"
    ]
    return read_pc_rules(
        {key: value.replace(">", "&#62") for key, value in arguments.items()}
    )


def _assert_retained_rule(
    written: dict[str, ParentalControlRule],
    retained: ParentalControlRule,
) -> None:
    """Assert an accepted payload preserves retained string fields."""

    assert written[retained.mac].name == retained.name
    assert written[retained.mac].timemap == retained.timemap


@pytest.mark.asyncio
async def test_rule_table_exactly_at_advertised_limit_writes() -> None:
    """Adding the sixteenth rule is within a sixteen-rule capacity."""

    current = _rules(15)
    retained = current[_mac(0)]
    bridge = internet_access._bridge(rules=current)
    bridge.api.async_get_parental_control_capabilities.return_value = (
        ParentalControlCapabilities(max_rules=RULE_LIMIT)
    )

    result = await bridge.async_pc_rule(
        state="block", devices=[{"mac": _mac(15), "name": "New"}]
    )

    assert result.success is True
    bridge.api.async_get_parental_control_capabilities.assert_awaited_once()
    bridge.api.async_run_service_result.assert_awaited_once()
    written = _written_rules(bridge)
    assert len(written) == RULE_LIMIT
    _assert_retained_rule(written, retained)


@pytest.mark.asyncio
async def test_seventeenth_rule_uses_advertised_limit_message() -> None:
    """An advertised rule limit rejects growth before the router write."""

    bridge = internet_access._bridge(rules=_rules(RULE_LIMIT))
    bridge.api.async_get_parental_control_capabilities.return_value = (
        ParentalControlCapabilities(max_rules=RULE_LIMIT)
    )

    with pytest.raises(ServiceValidationError) as raised:
        await bridge.async_pc_rule(state="block", devices=[{"mac": _mac(16)}])

    assert "from 16 to 17" in str(raised.value)
    assert "MaxRule_parentctrl" in str(raised.value)
    assert str(raised.value) == str(raised.value.__cause__)
    bridge.api.async_get_parental_control_capabilities.assert_awaited_once()
    bridge.api.async_run_service_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_seventeenth_rule_uses_assumed_limit_message() -> None:
    """An unreported rule limit identifies the library fallback as assumed."""

    bridge = internet_access._bridge(rules=_rules(RULE_LIMIT))

    with pytest.raises(ServiceValidationError, match="assumed"):
        await bridge.async_pc_rule(state="allow", devices=[{"mac": _mac(16)}])

    bridge.api.async_get_parental_control_capabilities.assert_awaited_once()
    bridge.api.async_run_service_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_window_limit_rejects_before_write() -> None:
    """Two two-window rules cannot fit within a three-window limit."""

    bridge = internet_access._bridge(rules=_rules(1))
    bridge.api.async_get_parental_control_capabilities.return_value = (
        ParentalControlCapabilities(max_rules=RULE_LIMIT, max_entries=3)
    )

    with pytest.raises(ServiceValidationError, match="schedule windows"):
        await bridge.async_pc_rule(state="block", devices=[{"mac": _mac(1)}])

    bridge.api.async_get_parental_control_capabilities.assert_awaited_once()
    bridge.api.async_run_service_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_devices_are_counted_in_final_table() -> None:
    """All devices in one request contribute to the proposed rule count."""

    bridge = internet_access._bridge(rules=_rules(15))
    bridge.api.async_get_parental_control_capabilities.return_value = (
        ParentalControlCapabilities(max_rules=RULE_LIMIT)
    )

    with pytest.raises(ServiceValidationError, match="from 15 to 17"):
        await bridge.async_pc_rule(
            state="block",
            devices=[{"mac": _mac(15)}, {"mac": _mac(16)}],
        )

    bridge.api.async_get_parental_control_capabilities.assert_awaited_once()
    bridge.api.async_run_service_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_device_is_counted_once_in_final_table() -> None:
    """Repeated targets collapse to their single final table entry."""

    current = _rules(15)
    retained = current[_mac(0)]
    bridge = internet_access._bridge(rules=current)
    bridge.api.async_get_parental_control_capabilities.return_value = (
        ParentalControlCapabilities(max_rules=RULE_LIMIT)
    )

    result = await bridge.async_pc_rule(
        state="block",
        devices=[{"mac": _mac(15)}, {"mac": _mac(15)}],
    )

    assert result.success is True
    bridge.api.async_get_parental_control_capabilities.assert_awaited_once()
    bridge.api.async_run_service_result.assert_awaited_once()
    written = _written_rules(bridge)
    assert len(written) == RULE_LIMIT
    _assert_retained_rule(written, retained)


@pytest.mark.asyncio
async def test_remove_over_limit_skips_capabilities_and_writes() -> None:
    """A shrinking removal does not need router capacity metadata."""

    current = _rules(OVER_LIMIT_RULE_COUNT)
    retained = current[_mac(0)]
    bridge = internet_access._bridge(rules=current)

    result = await bridge.async_pc_rule(
        state="remove", devices=[{"mac": _mac(16)}]
    )

    assert result.success is True
    bridge.api.async_get_parental_control_capabilities.assert_not_awaited()
    bridge.api.async_get_data.assert_awaited_once()
    bridge.api.async_run_service_result.assert_awaited_once()
    written = _written_rules(bridge)
    assert len(written) == RULE_LIMIT
    _assert_retained_rule(written, retained)


@pytest.mark.asyncio
async def test_allow_existing_rule_over_limit_writes_without_growth() -> None:
    """A type-only update remains valid when the table is already too large."""

    current = _rules(OVER_LIMIT_RULE_COUNT)
    retained = current[_mac(0)]
    bridge = internet_access._bridge(rules=current)
    bridge.api.async_get_parental_control_capabilities.return_value = (
        ParentalControlCapabilities(max_rules=RULE_LIMIT, max_entries=32)
    )

    result = await bridge.async_pc_rule(
        state="allow", devices=[{"mac": _mac(16), "name": "Changed"}]
    )

    assert result.success is True
    bridge.api.async_get_parental_control_capabilities.assert_awaited_once()
    bridge.api.async_run_service_result.assert_awaited_once()
    written = _written_rules(bridge)
    assert len(written) == OVER_LIMIT_RULE_COUNT
    assert written[_mac(16)].type is PCRuleType.DISABLE
    _assert_retained_rule(written, retained)


@pytest.mark.asyncio
async def test_capability_transport_error_propagates_without_write() -> None:
    """A failed capability request aborts before snapshot and write."""

    bridge = internet_access._bridge(rules=_rules(1))
    error = AsusRouterError("capabilities unavailable")
    bridge.api.async_get_parental_control_capabilities.side_effect = error

    with pytest.raises(AsusRouterError) as raised:
        await bridge.async_pc_rule(state="block", devices=[{"mac": _mac(1)}])

    assert raised.value is error
    bridge.api.async_get_parental_control_capabilities.assert_awaited_once()
    bridge.api.async_get_data.assert_not_awaited()
    bridge.api.async_run_service_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_handler_preserves_capacity_validation_error() -> None:
    """The service path exposes the bridge validation error unchanged."""

    bridge = internet_access._bridge(rules=_rules(RULE_LIMIT))
    bridge.api.async_get_parental_control_capabilities.return_value = (
        ParentalControlCapabilities(max_rules=RULE_LIMIT)
    )
    router = internet_access._router(bridge)
    hass, handlers = internet_access._service_hass(router)
    await async_setup_services(hass)
    handler = handlers[SERVICE_DEVICE_INTERNET_ACCESS]
    call = Mock(
        data={
            "config_entry_id": "router-1",
            "devices": [{"mac": _mac(16)}],
            "state": "block",
        }
    )

    with (
        patch(
            "custom_components.asusrouter.services._get_entity_ids",
            return_value=[],
        ),
        pytest.raises(ServiceValidationError) as raised,
    ):
        await handler(call)

    assert "from 16 to 17" in str(raised.value)
    assert "MaxRule_parentctrl" in str(raised.value)
    assert isinstance(raised.value.__cause__, ParentalControlCapacityError)
    assert str(raised.value) == str(raised.value.__cause__)
    bridge.api.async_get_parental_control_capabilities.assert_awaited_once()
    bridge.api.async_run_service_result.assert_not_awaited()
    router.async_set_internet_access.assert_awaited_once()
    router.update_pc_rules.assert_not_awaited()
