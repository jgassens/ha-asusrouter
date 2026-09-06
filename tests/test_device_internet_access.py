"""Tests for device internet-access control."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

from asusrouter.error import AsusRouterError
from asusrouter.modules.parental_control import (
    KEY_PC_MAC,
    KEY_PC_NAME,
    KEY_PC_TYPE,
    ParentalControlRule,
    PCRuleType,
)
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
import voluptuous as vol

from custom_components.asusrouter.bridge import ARBridge
from custom_components.asusrouter.const import ASUSROUTER, DOMAIN, ROUTER
from custom_components.asusrouter.services import (
    DEVICE_INTERNET_ACCESS_SCHEMA,
    SERVICE_DEVICE_INTERNET_ACCESS,
    _internet_access_entity_data,
    _internet_access_state_matches,
    _reload_parental_control_switches,
    async_setup_services,
)


def _bridge(
    *results: bool,
    rules: dict[str, ParentalControlRule] | None = None,
) -> ARBridge:
    """Create a bridge shell with mocked router reads and writes."""

    bridge = ARBridge.__new__(ARBridge)
    bridge._api = Mock()
    bridge.api.async_get_data = AsyncMock(
        return_value={"rules": rules if rules is not None else {}}
    )
    bridge.api.async_run_service = AsyncMock(
        side_effect=results or (True,)
    )
    return bridge


def _written_macs(bridge: ARBridge) -> str:
    """Return the MAC list from the single written rule table."""

    call = bridge.api.async_run_service.await_args
    assert call.kwargs["service"] == "restart_firewall"
    assert call.kwargs["apply"] is True
    return call.kwargs["arguments"][KEY_PC_MAC]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "rule_type"),
    [
        ("allow", PCRuleType.DISABLE),
        ("block", PCRuleType.BLOCK),
    ],
)
async def test_pc_rule_maps_state_and_normalizes_mac(
    state: str,
    rule_type: PCRuleType,
) -> None:
    """Each public state should produce the matching router rule."""

    bridge = _bridge(True)

    assert await bridge.async_pc_rule(
        state=state,
        devices=[{"mac": "aa:bb:cc:dd:ee:ff", "name": "Console"}],
    )

    arguments = bridge.api.async_run_service.await_args.kwargs["arguments"]
    assert arguments[KEY_PC_MAC] == "AA:BB:CC:DD:EE:FF"
    assert arguments[KEY_PC_NAME] == "Console"
    assert arguments[KEY_PC_TYPE] == str(rule_type.value)
    bridge.api.async_get_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_pc_rule_remove_drops_only_the_named_rule() -> None:
    """A remove action should keep every other rule on the router."""

    keep = ParentalControlRule(
        mac="00:11:22:33:44:55", name="Keep", type=PCRuleType.BLOCK
    )
    drop = ParentalControlRule(
        mac="AA:BB:CC:DD:EE:FF", name="Drop", type=PCRuleType.BLOCK
    )
    bridge = _bridge(
        True, rules={keep.mac: keep, drop.mac: drop}
    )

    assert await bridge.async_pc_rule(
        state="remove",
        devices=[{"mac": "aa:bb:cc:dd:ee:ff"}],
    )

    assert _written_macs(bridge) == keep.mac


@pytest.mark.asyncio
async def test_pc_rule_rejects_empty_targets() -> None:
    """An empty action must not report success."""

    bridge = _bridge()

    assert not await bridge.async_pc_rule(state="block", devices=[])
    bridge.api.async_get_data.assert_not_awaited()
    bridge.api.async_run_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_pc_rule_reports_router_write_failure() -> None:
    """A failed router write should fail the whole action."""

    bridge = _bridge(False)

    assert not await bridge.async_pc_rule(
        state="block",
        devices=[{"mac": "00:11:22:33:44:55"}],
    )


@pytest.mark.asyncio
async def test_pc_rule_preserves_existing_rules_on_cold_cache() -> None:
    """A fresh read must seed the write so nothing else is dropped."""

    existing = ParentalControlRule(
        mac="00:11:22:33:44:55", name="Existing", type=PCRuleType.DISABLE
    )
    bridge = _bridge(True, rules={existing.mac: existing})

    assert await bridge.async_pc_rule(
        state="block",
        devices=[{"mac": "AA:BB:CC:DD:EE:FF", "name": "Console"}],
    )

    bridge.api.async_get_data.assert_awaited_once()
    assert (
        bridge.api.async_get_data.await_args.kwargs.get("force") is True
        or bridge.api.async_get_data.await_args.args[1] is True
    )
    written = _written_macs(bridge)
    assert existing.mac in written
    assert "AA:BB:CC:DD:EE:FF" in written


@pytest.mark.asyncio
async def test_pc_rule_applies_all_devices_in_single_write() -> None:
    """Every requested device should end up in the one written table."""

    bridge = _bridge(True)

    assert await bridge.async_pc_rule(
        state="block",
        devices=[
            {"mac": "00:11:22:33:44:55"},
            {"mac": "AA:BB:CC:DD:EE:FF"},
        ],
    )

    bridge.api.async_run_service.assert_awaited_once()
    written = _written_macs(bridge)
    assert "00:11:22:33:44:55" in written
    assert "AA:BB:CC:DD:EE:FF" in written


@pytest.mark.asyncio
async def test_pc_rule_refuses_write_when_rules_unreadable() -> None:
    """No write may happen when the current rules cannot be read."""

    bridge = _bridge(True)
    bridge.api.async_get_data.return_value = {}

    with pytest.raises(AsusRouterError, match="current parental control"):
        await bridge.async_pc_rule(
            state="block",
            devices=[{"mac": "AA:BB:CC:DD:EE:FF"}],
        )

    bridge.api.async_run_service.assert_not_awaited()


def test_service_schema_requires_target() -> None:
    """The action should reject calls that cannot identify a device."""

    with pytest.raises(vol.MultipleInvalid, match="target is required"):
        DEVICE_INTERNET_ACCESS_SCHEMA({"state": "block"})


def _service_hass(router: Mock) -> tuple[Mock, dict[str, object]]:
    """Create a Home Assistant shell and capture service handlers."""

    handlers: dict[str, object] = {}
    hass = Mock()
    hass.data = {DOMAIN: {"router-1": {ASUSROUTER: router}}}
    hass.services.has_service.return_value = False
    hass.services.async_register.side_effect = (
        lambda _domain, service, handler, **_kwargs: handlers.__setitem__(
            service, handler
        )
    )
    return hass, handlers


def _router() -> Mock:
    """Create a router-mode service target."""

    router = Mock()
    router.mode = ROUTER
    router.mac = "24:4b:fe:f5:ee:20"
    router.pc_rules = {}
    router._static_dhcp_mac.side_effect = lambda mac: str(mac).lower()
    router._pc_switch_reload_lock = asyncio.Lock()

    async def apply_rule(*, state: str, devices: list[dict[str, str]]) -> bool:
        for device in devices:
            mac = str(device["mac"]).lower()
            if state == "remove":
                router.pc_rules.pop(mac, None)
                continue
            router.pc_rules[mac] = ParentalControlRule(
                mac=mac,
                name=device.get("name", ""),
                type={
                    "allow": PCRuleType.DISABLE,
                    "block": PCRuleType.BLOCK,
                }[state],
            )
        return True

    router.bridge.async_pc_rule = AsyncMock(side_effect=apply_rule)
    router.update_pc_rules = AsyncMock(return_value=True)
    return router


@pytest.mark.asyncio
async def test_service_routes_direct_devices_to_only_router() -> None:
    """A direct MAC target should safely resolve the only loaded router."""

    router = _router()
    hass, handlers = _service_hass(router)
    await async_setup_services(hass)
    handler = handlers[SERVICE_DEVICE_INTERNET_ACCESS]
    call = Mock(
        data={
            "devices": [{"mac": "AA:BB:CC:DD:EE:FF", "name": "Console"}],
            "state": "block",
        }
    )

    with patch(
        "custom_components.asusrouter.services._get_entity_ids",
        return_value=[],
    ):
        await handler(call)

    router.bridge.async_pc_rule.assert_awaited_once_with(
        state="block",
        devices=[{"mac": "AA:BB:CC:DD:EE:FF", "name": "Console"}],
    )
    router.update_pc_rules.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_routes_entity_target_to_own_router() -> None:
    """The GUI entity target should select its owning router."""

    router = _router()
    hass, handlers = _service_hass(router)
    await async_setup_services(hass)
    handler = handlers[SERVICE_DEVICE_INTERNET_ACCESS]
    device = {"mac": "AA:BB:CC:DD:EE:FF", "name": "Console"}
    call = Mock(
        data={
            "entity_id": ["device_tracker.console"],
            "state": "allow",
        }
    )

    with (
        patch(
            "custom_components.asusrouter.services._get_entity_ids",
            return_value=["device_tracker.console"],
        ),
        patch(
            "custom_components.asusrouter.services."
            "_internet_access_entity_data",
            return_value=(router, device),
        ),
    ):
        await handler(call)

    router.bridge.async_pc_rule.assert_awaited_once_with(
        state="allow",
        devices=[device],
    )


@pytest.mark.asyncio
async def test_direct_devices_require_router_for_multiple_entries() -> None:
    """Direct MAC targets must not be sent through an arbitrary router."""

    router = _router()
    second_router = _router()
    hass, handlers = _service_hass(router)
    hass.data[DOMAIN]["router-2"] = {ASUSROUTER: second_router}
    await async_setup_services(hass)
    handler = handlers[SERVICE_DEVICE_INTERNET_ACCESS]
    call = Mock(
        data={
            "devices": [{"mac": "AA:BB:CC:DD:EE:FF"}],
            "state": "block",
        }
    )

    with (
        patch(
            "custom_components.asusrouter.services._get_entity_ids",
            return_value=[],
        ),
        pytest.raises(ServiceValidationError, match="config_entry_id"),
    ):
        await handler(call)

    router.bridge.async_pc_rule.assert_not_awaited()
    second_router.bridge.async_pc_rule.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_idempotent_remove_reloads_switches() -> None:
    """An already-removed rule should still clean up switch entities."""

    router = _router()
    router.bridge.async_pc_rule.side_effect = None
    router.bridge.async_pc_rule.return_value = False
    hass, handlers = _service_hass(router)
    await async_setup_services(hass)
    handler = handlers[SERVICE_DEVICE_INTERNET_ACCESS]
    call = Mock(
        data={
            "config_entry_id": "router-1",
            "devices": [{"mac": "AA:BB:CC:DD:EE:FF"}],
            "state": "remove",
        }
    )

    with (
        patch(
            "custom_components.asusrouter.services._get_entity_ids",
            return_value=[],
        ),
        patch(
            "custom_components.asusrouter.services."
            "_reload_parental_control_switches",
            new_callable=AsyncMock,
        ) as reload_switches,
    ):
        await handler(call)

    reload_switches.assert_awaited_once_with(router, call.data["devices"])


@pytest.mark.asyncio
async def test_remove_deletes_matching_switch_registry_entry() -> None:
    """Removing a rule should not leave an unavailable GUI entity."""

    router = _router()
    router._config_entry = Mock(entry_id="router-1")
    router.hass.config_entries.async_unload_platforms = AsyncMock(
        return_value=True
    )
    router.hass.config_entries.async_forward_entry_setups = AsyncMock()
    registry = Mock()
    matching = Mock(
        domain=Platform.SWITCH,
        platform=DOMAIN,
        unique_id=("24:4b:fe:f5:ee:20_aa:bb:cc:dd:ee:ff_block_internet"),
        entity_id="switch.console_block_internet",
    )
    unrelated = Mock(
        domain=Platform.SWITCH,
        platform=DOMAIN,
        unique_id="24:4b:fe:f5:ee:20_block_internet",
        entity_id="switch.router_block_internet",
    )

    with (
        patch(
            "custom_components.asusrouter.services.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.asusrouter.services.er."
            "async_entries_for_config_entry",
            return_value=[matching, unrelated],
        ),
    ):
        await _reload_parental_control_switches(
            router, [{"mac": "AA:BB:CC:DD:EE:FF"}]
        )

    registry.async_remove.assert_called_once_with(
        "switch.console_block_internet"
    )


@pytest.mark.asyncio
async def test_service_surfaces_unconfirmed_router_write() -> None:
    """A failed router write must become a visible Home Assistant error."""

    router = _router()
    router.bridge.async_pc_rule.side_effect = None
    router.bridge.async_pc_rule.return_value = False
    hass, handlers = _service_hass(router)
    await async_setup_services(hass)
    handler = handlers[SERVICE_DEVICE_INTERNET_ACCESS]
    call = Mock(
        data={
            "config_entry_id": "router-1",
            "devices": [{"mac": "AA:BB:CC:DD:EE:FF"}],
            "state": "block",
        }
    )

    with (
        patch(
            "custom_components.asusrouter.services._get_entity_ids",
            return_value=[],
        ),
        patch(
            "custom_components.asusrouter.services.PC_RULE_CONFIRM_DELAY",
            0,
        ),
        pytest.raises(HomeAssistantError, match="could not be confirmed"),
    ):
        await handler(call)

    assert router.update_pc_rules.await_count > 1
    assert (
        router.update_pc_rules.await_args.kwargs.get("force") is True
    )


@pytest.mark.asyncio
async def test_service_refuses_write_when_rules_unreadable() -> None:
    """An unreadable rule set must fail the call, not wipe the table."""

    router = _router()
    router.bridge.async_pc_rule.side_effect = AsusRouterError(
        "Unable to read the current parental control rules from the router"
    )
    hass, handlers = _service_hass(router)
    await async_setup_services(hass)
    handler = handlers[SERVICE_DEVICE_INTERNET_ACCESS]
    call = Mock(
        data={
            "config_entry_id": "router-1",
            "devices": [{"mac": "AA:BB:CC:DD:EE:FF"}],
            "state": "block",
        }
    )

    with (
        patch(
            "custom_components.asusrouter.services._get_entity_ids",
            return_value=[],
        ),
        pytest.raises(
            HomeAssistantError, match="Unable to change device internet access"
        ),
    ):
        await handler(call)

    router.update_pc_rules.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_read_back_is_confirmed_after_retry() -> None:
    """A write that is slow to apply should still be confirmed."""

    router = _router()
    pending: dict[str, ParentalControlRule] = {}

    async def apply_rule(*, state: str, devices: list[dict[str, str]]) -> bool:
        for device in devices:
            mac = str(device["mac"]).lower()
            pending[mac] = ParentalControlRule(
                mac=mac,
                name=device.get("name", ""),
                type=PCRuleType.BLOCK,
            )
        return True

    refreshes = 0
    applies_after = 2

    async def stale_refresh(force: bool = False) -> bool:
        nonlocal refreshes
        refreshes += 1
        # The first read-back is stale; the router applies the change
        # before the second one.
        if refreshes >= applies_after:
            router.pc_rules.update(pending)
        return True

    router.bridge.async_pc_rule = AsyncMock(side_effect=apply_rule)
    router.update_pc_rules = AsyncMock(side_effect=stale_refresh)
    hass, handlers = _service_hass(router)
    await async_setup_services(hass)
    handler = handlers[SERVICE_DEVICE_INTERNET_ACCESS]
    call = Mock(
        data={
            "config_entry_id": "router-1",
            "devices": [{"mac": "AA:BB:CC:DD:EE:FF"}],
            "state": "block",
        }
    )

    with (
        patch(
            "custom_components.asusrouter.services._get_entity_ids",
            return_value=[],
        ),
        patch(
            "custom_components.asusrouter.services.PC_RULE_CONFIRM_DELAY",
            0,
        ),
    ):
        await handler(call)

    assert router.update_pc_rules.await_count == applies_after


@pytest.mark.asyncio
async def test_switch_reload_restores_platform_on_failure() -> None:
    """A failing registry cleanup must not leave switches unloaded."""

    router = _router()
    router._config_entry = Mock(entry_id="router-1")
    router.hass.config_entries.async_unload_platforms = AsyncMock(
        return_value=True
    )
    router.hass.config_entries.async_forward_entry_setups = AsyncMock()
    registry = Mock()
    matching = Mock(
        domain=Platform.SWITCH,
        platform=DOMAIN,
        unique_id=("24:4b:fe:f5:ee:20_aa:bb:cc:dd:ee:ff_block_internet"),
        entity_id="switch.console_block_internet",
    )
    registry.async_remove.side_effect = RuntimeError("registry broken")

    with (
        patch(
            "custom_components.asusrouter.services.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.asusrouter.services.er."
            "async_entries_for_config_entry",
            return_value=[matching],
        ),
        pytest.raises(RuntimeError, match="registry broken"),
    ):
        await _reload_parental_control_switches(
            router, [{"mac": "AA:BB:CC:DD:EE:FF"}]
        )

    router.hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        router._config_entry, [Platform.SWITCH]
    )


@pytest.mark.asyncio
async def test_state_matches_rejects_malformed_rules() -> None:
    """An unexpected rule payload must surface as HomeAssistantError."""

    router = _router()
    router.pc_rules = {"aa:bb:cc:dd:ee:ff": "not-a-rule"}

    with pytest.raises(HomeAssistantError, match="unexpected"):
        _internet_access_state_matches(
            router, "block", [{"mac": "AA:BB:CC:DD:EE:FF"}]
        )


@pytest.mark.asyncio
async def test_entity_data_rejects_malformed_capabilities() -> None:
    """Malformed device tracker data must surface as validation error."""

    router = _router()
    hass, _handlers = _service_hass(router)
    entry = Mock(
        domain="device_tracker",
        platform=DOMAIN,
        config_entry_id="router-1",
        capabilities=["not-a-dict"],
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
