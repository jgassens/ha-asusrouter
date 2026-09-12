"""Tests for device internet-access control."""

from __future__ import annotations

import asyncio
from functools import partial
import logging
from unittest.mock import ANY, AsyncMock, Mock, call, patch

from asusrouter.error import AsusRouterError
from asusrouter.modules.data import AsusData
from asusrouter.modules.parental_control import (
    KEY_PC_MAC,
    KEY_PC_NAME,
    KEY_PC_TIMEMAP,
    KEY_PC_TYPE,
    ParentalControlCapabilities,
    ParentalControlRule,
    PCRuleType,
    read_pc_rules,
)
from asusrouter.modules.service import ServiceResult
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import format_mac
import pytest
import voluptuous as vol

from custom_components.asusrouter.bridge import ARBridge
from custom_components.asusrouter.const import ASUSROUTER, DOMAIN, ROUTER
from custom_components.asusrouter.router import ARDevice
from custom_components.asusrouter.services import (
    DEVICE_INTERNET_ACCESS_SCHEMA,
    SERVICE_DEVICE_INTERNET_ACCESS,
    _client_entity_data,
    _internet_access_entity_data,
    async_setup_services,
)
from custom_components.asusrouter.switch import ClientInternetSwitch


def _result(
    success: bool,
    needed_time: int | None = None,
) -> ServiceResult:
    """Build service metadata returned by the pinned library API."""

    return ServiceResult(
        success=success, needed_time=needed_time, last_id=None
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
    bridge.api.async_get_parental_control_capabilities = AsyncMock(
        return_value=ParentalControlCapabilities()
    )
    bridge.api.async_run_service_result = AsyncMock(
        side_effect=[_result(success) for success in (results or (True,))]
    )
    bridge.api.async_set_state = AsyncMock(return_value=True)
    return bridge


def _written_macs(bridge: ARBridge) -> str:
    """Return the MAC list from the single written rule table."""

    call = bridge.api.async_run_service_result.await_args
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

    result = await bridge.async_pc_rule(
        state=state,
        devices=[{"mac": "aa:bb:cc:dd:ee:ff", "name": "Console"}],
    )
    assert result.success is True

    arguments = bridge.api.async_run_service_result.await_args.kwargs[
        "arguments"
    ]
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
    bridge = _bridge(True, rules={keep.mac: keep, drop.mac: drop})

    result = await bridge.async_pc_rule(
        state="remove",
        devices=[{"mac": "AA-BB-CC-DD-EE-FF"}],
    )
    assert result.success is True

    assert _written_macs(bridge) == keep.mac


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mac",
    [
        "AA:BB:CC:DD:EE:FF",
        "AA-BB-CC-DD-EE-FF",
        "AABBCCDDEEFF",
    ],
)
async def test_pc_rule_mac_forms_replace_existing_rule(mac: str) -> None:
    """Every accepted MAC form should key the same router rule."""

    existing = ParentalControlRule(
        mac="AA:BB:CC:DD:EE:FF", name="Old", type=PCRuleType.DISABLE
    )
    bridge = _bridge(True, rules={existing.mac: existing})

    result = await bridge.async_pc_rule(
        state="block", devices=[{"mac": mac, "name": "New"}]
    )

    assert result.success is True
    rules = _read_written_rules(
        bridge.api.async_run_service_result.await_args.kwargs["arguments"]
    )
    assert list(rules) == [existing.mac]
    assert rules[existing.mac].name == "New"
    assert rules[existing.mac].type is PCRuleType.BLOCK


@pytest.mark.asyncio
async def test_pc_rule_rejects_empty_targets() -> None:
    """An empty action must not report success."""

    bridge = _bridge()

    result = await bridge.async_pc_rule(state="block", devices=[])
    assert result.success is False
    assert result.needed_time is None
    assert result.last_id is None
    bridge.api.async_get_data.assert_not_awaited()
    bridge.api.async_run_service_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_pc_rule_reports_router_write_failure() -> None:
    """A failed router write should fail the whole action."""

    bridge = _bridge(False)

    result = await bridge.async_pc_rule(
        state="block",
        devices=[{"mac": "00:11:22:33:44:55"}],
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_pc_rule_rejects_unknown_state_before_router_calls() -> None:
    """An unknown state should fail without router round-trips."""

    bridge = _bridge()

    with pytest.raises(ServiceValidationError, match="Unknown.*pause"):
        await bridge.async_pc_rule(
            state="pause",
            devices=[{"mac": "AA:BB:CC:DD:EE:FF"}],
        )

    bridge.api.async_get_parental_control_capabilities.assert_not_awaited()
    bridge.api.async_get_data.assert_not_awaited()
    bridge.api.async_run_service_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_pc_rule_preserves_existing_rules_on_cold_cache() -> None:
    """A fresh read must seed the write so nothing else is dropped."""

    existing = ParentalControlRule(
        mac="00:11:22:33:44:55", name="Existing", type=PCRuleType.DISABLE
    )
    bridge = _bridge(True, rules={existing.mac: existing})

    result = await bridge.async_pc_rule(
        state="block",
        devices=[{"mac": "AA:BB:CC:DD:EE:FF", "name": "Console"}],
    )
    assert result.success is True

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

    result = await bridge.async_pc_rule(
        state="block",
        devices=[
            {"mac": "00:11:22:33:44:55"},
            {"mac": "AA:BB:CC:DD:EE:FF"},
        ],
    )
    assert result.success is True

    bridge.api.async_run_service_result.assert_awaited_once()
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

    bridge.api.async_run_service_result.assert_not_awaited()


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

    def register(_domain: str, service: str, handler: object, *_a, **_kw):
        # Services are registered through HA's admin wrapper, a partial of
        # (hass, HassJob(service_func)). These tests exercise the service
        # bodies, so capture the wrapped function; the wrapper itself is
        # covered by tests/test_service_authorization.py.
        if isinstance(handler, partial):
            handler = handler.args[1].target
        handlers[service] = handler

    hass.services.async_register.side_effect = register
    return hass, handlers


def _router(bridge: ARBridge | None = None) -> Mock:
    """Create a router-mode service target."""

    router = Mock()
    router.mode = ROUTER
    router.mac = "24:4b:fe:f5:ee:20"
    router.pc_rules = {}
    router._static_dhcp_mac.side_effect = lambda mac: format_mac(str(mac))
    router._pc_rule_lock = asyncio.Lock()
    router._async_remove_pc_rule_entities = AsyncMock()

    async def set_internet_access(**kwargs: dict) -> None:
        await ARDevice.async_set_internet_access(router, **kwargs)

    router.async_set_internet_access = AsyncMock(
        side_effect=set_internet_access
    )

    def state_matches(
        state: str,
        devices: list[dict],
        expected_rules: dict[str, ParentalControlRule],
    ) -> bool:
        return ARDevice._internet_access_state_matches(
            router, state, devices, expected_rules
        )

    router._internet_access_state_matches.side_effect = state_matches

    async def apply_rule(
        *,
        state: str,
        devices: list[dict[str, str]],
        expected_rules: dict[str, ParentalControlRule],
    ) -> ServiceResult:
        for device in devices:
            mac = format_mac(str(device["mac"]))
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
            expected_rules[mac] = router.pc_rules[mac]
        return _result(True, 0)

    router.bridge.async_pc_rule = AsyncMock(side_effect=apply_rule)
    router.update_pc_rules = AsyncMock(return_value=True)
    if bridge is not None:
        router.bridge = bridge
        router._pc_rules = {}
        router._conf_host = "router.test"
        router._connect_error = False

        async def refresh(*, force: bool = False) -> bool:
            with patch(
                "custom_components.asusrouter.router.async_dispatcher_send"
            ):
                result = await ARDevice.update_pc_rules(router, force=force)
            router.pc_rules = router._pc_rules
            return result

        router.update_pc_rules = AsyncMock(side_effect=refresh)
    return router


@pytest.mark.asyncio
async def test_service_routes_direct_devices_to_only_router() -> None:
    """A direct MAC target should safely resolve the only loaded router."""

    router = _router()
    hass, handlers = _service_hass(router)
    await async_setup_services(hass)
    handler = handlers[SERVICE_DEVICE_INTERNET_ACCESS]
    call = Mock(
        data=DEVICE_INTERNET_ACCESS_SCHEMA(
            {
                "devices": [{"mac": "AA-BB-CC-DD-EE-FF", "name": "Console"}],
                "state": "block",
            }
        )
    )

    with patch(
        "custom_components.asusrouter.services._get_entity_ids",
        return_value=[],
    ):
        await handler(call)

    router.bridge.async_pc_rule.assert_awaited_once_with(
        state="block",
        devices=[{"mac": "aa:bb:cc:dd:ee:ff", "name": "Console"}],
        expected_rules=ANY,
    )
    router.update_pc_rules.assert_awaited_once()
    router.async_set_internet_access.assert_awaited_once_with(
        state="block",
        devices=[{"mac": "aa:bb:cc:dd:ee:ff", "name": "Console"}],
    )


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
        expected_rules=ANY,
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
async def test_idempotent_remove_cleans_entities_under_rule_lock() -> None:
    """An already-removed rule still cleans up before another writer runs."""

    router = _router()
    router.bridge.async_pc_rule.side_effect = None
    router.bridge.async_pc_rule.return_value = _result(False)
    hass, handlers = _service_hass(router)
    await async_setup_services(hass)

    async def cleanup_while_locked(*_args: object) -> None:
        assert router._pc_rule_lock.locked()

    router._async_remove_pc_rule_entities.side_effect = cleanup_while_locked
    devices = [{"mac": "AA:BB:CC:DD:EE:FF"}]
    with patch(
        "custom_components.asusrouter.services._get_entity_ids",
        return_value=[],
    ):
        await handlers[SERVICE_DEVICE_INTERNET_ACCESS](
            Mock(data={"devices": devices, "state": "remove"})
        )

    router._async_remove_pc_rule_entities.assert_awaited_once_with(devices)
    router.update_pc_rules.assert_awaited_once_with(force=True)
    router.hass.config_entries.async_unload_platforms.assert_not_called()


@pytest.mark.asyncio
async def test_service_surfaces_unconfirmed_router_write() -> None:
    """A failed router write must become a visible Home Assistant error."""

    router = _router()
    router.bridge.async_pc_rule.side_effect = None
    router.bridge.async_pc_rule.return_value = _result(False)
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
            "custom_components.asusrouter.router.PC_RULE_CONFIRM_DELAY",
            0,
        ),
        pytest.raises(HomeAssistantError, match="could not be confirmed"),
    ):
        await handler(call)

    assert router.update_pc_rules.await_count > 1
    assert router.update_pc_rules.await_args.kwargs.get("force") is True


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

    async def apply_rule(
        *,
        state: str,
        devices: list[dict[str, str]],
        expected_rules: dict[str, ParentalControlRule],
    ) -> ServiceResult:
        for device in devices:
            mac = str(device["mac"]).lower()
            pending[mac] = ParentalControlRule(
                mac=mac,
                name=device.get("name", ""),
                type=PCRuleType.BLOCK,
            )
            expected_rules[mac] = pending[mac]
        return _result(True, 0)

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
            "custom_components.asusrouter.router.PC_RULE_CONFIRM_DELAY",
            0,
        ),
    ):
        await handler(call)

    assert router.update_pc_rules.await_count == applies_after


@pytest.mark.asyncio
async def test_state_matches_rejects_malformed_rules() -> None:
    """An unexpected rule payload must surface as HomeAssistantError."""

    router = _router()
    router.pc_rules = {"aa:bb:cc:dd:ee:ff": "not-a-rule"}

    with pytest.raises(HomeAssistantError, match="unexpected"):
        ARDevice._internet_access_state_matches(
            router, "block", [{"mac": "AA:BB:CC:DD:EE:FF"}], {}
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


@pytest.mark.parametrize(
    ("name", "expected_name"),
    [
        ("kid>extra", "kidextra"),
        ("kid>extra\n" + "x" * 40, "kidextra" + "x" * 24),
        ("kid&#60extra&#62", "kidextra"),
        ("Kid &amp; Jerry", "Kid  Jerry"),
        ("Kid &#x3e; Other", "Kid  Other"),
    ],
)
def test_entity_data_sanitizes_discovered_name_and_mac(
    caplog: pytest.LogCaptureFixture,
    name: str,
    expected_name: str,
) -> None:
    """Tracker-derived values should be safe and canonical at the boundary."""

    router = _router()
    client = Mock()
    client.name = name
    client.ip_address = "192.168.50.10"
    router.devices = {"aa:bb:cc:dd:ee:ff": client}
    hass, _handlers = _service_hass(router)
    hass.states.get.return_value = None
    entry = Mock(
        domain="device_tracker",
        platform=DOMAIN,
        config_entry_id="router-1",
        capabilities={"mac": "AA-BB-CC-DD-EE-FF"},
    )
    registry = Mock()
    registry.async_get.return_value = entry

    with (
        patch(
            "custom_components.asusrouter.services.er.async_get",
            return_value=registry,
        ),
        caplog.at_level(logging.DEBUG),
    ):
        resolved_router, device = _internet_access_entity_data(
            hass, "device_tracker.console"
        )

    assert resolved_router is router
    assert device == {
        "mac": "aa:bb:cc:dd:ee:ff",
        "default_name": expected_name,
    }
    assert "Sanitized parental-control device name" in caplog.text
    assert name not in caplog.text


def test_client_entity_data_rejects_non_asusrouter_tracker() -> None:
    """All entity-backed services should enforce the tracker platform."""

    router = _router()
    hass, _handlers = _service_hass(router)
    registry = Mock()
    registry.async_get.return_value = Mock(
        domain="sensor",
        platform="other",
        config_entry_id="router-1",
    )

    with (
        patch(
            "custom_components.asusrouter.services.er.async_get",
            return_value=registry,
        ),
        pytest.raises(ServiceValidationError, match="not an AsusRouter"),
    ):
        _client_entity_data(hass, "sensor.console")


@pytest.mark.asyncio
async def test_multi_router_applies_all_before_reporting_failures() -> None:
    """A failed router should not prevent later router updates."""

    failed_router = _router()
    successful_router = _router()
    failure = OSError("offline")
    failed_router.async_set_internet_access = AsyncMock(side_effect=failure)
    successful_router.async_set_internet_access = AsyncMock()
    hass, handlers = _service_hass(failed_router)
    hass.data[DOMAIN]["router-2"] = {ASUSROUTER: successful_router}
    await async_setup_services(hass)
    handler = handlers[SERVICE_DEVICE_INTERNET_ACCESS]
    devices = {
        "device_tracker.first": (
            failed_router,
            {"mac": "00:11:22:33:44:55", "name": "First"},
        ),
        "device_tracker.second": (
            successful_router,
            {"mac": "AA:BB:CC:DD:EE:FF", "name": "Second"},
        ),
    }

    with (
        patch(
            "custom_components.asusrouter.services._get_entity_ids",
            return_value=list(devices),
        ),
        patch(
            "custom_components.asusrouter.services."
            "_internet_access_entity_data",
            side_effect=lambda _hass, entity_id: devices[entity_id],
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await handler(Mock(data={"state": "block"}))

    assert "succeeded: router-2" in str(raised.value)
    assert "failed: router-1 (offline)" in str(raised.value)
    assert raised.value.__cause__ is failure
    failed_router.async_set_internet_access.assert_awaited_once()
    successful_router.async_set_internet_access.assert_awaited_once()


def _read_written_rules(arguments: dict[str, str]) -> dict:
    """Decode a fake router write using the library's response delimiter."""

    return read_pc_rules(
        {key: value.replace(">", "&#62") for key, value in arguments.items()}
    )


@pytest.mark.asyncio
async def test_concurrent_services_serialize_read_write_and_confirmation() -> (
    None
):
    """A second writer cannot read until the first writer has confirmed."""

    write_started = asyncio.Event()
    release_write = asyncio.Event()
    confirm_started = asyncio.Event()
    release_confirm = asyncio.Event()
    second_waiting = asyncio.Event()

    class ObservedLock(asyncio.Lock):
        """Expose a contending writer without relying on scheduling sleeps."""

        async def acquire(self) -> bool:
            """Signal that a second writer has reached the held lock."""

            if self.locked():
                second_waiting.set()
            return await super().acquire()

    first_mac = "AA:BB:CC:DD:EE:FF"
    second_mac = "00:11:22:33:44:55"
    table = {}
    reads = []
    writes = []
    bridge = _bridge()

    async def read(datatype: AsusData, *, force: bool) -> dict:
        assert datatype == AsusData.PARENTAL_CONTROL
        assert force is True
        reads.append(set(table))
        if writes and not confirm_started.is_set():
            confirm_started.set()
            await release_confirm.wait()
        return {"rules": table}

    async def write(
        *, service: str, arguments: dict, apply: bool
    ) -> ServiceResult:
        assert service == "restart_firewall"
        assert apply is True
        if not writes:
            write_started.set()
            await release_write.wait()
        table.clear()
        table.update(_read_written_rules(arguments))
        writes.append(set(table))
        return _result(True, 0)

    bridge.api.async_get_data.side_effect = read
    bridge.api.async_run_service_result.side_effect = write
    router = _router(bridge)
    router._pc_rule_lock = ObservedLock()
    hass, handlers = _service_hass(router)
    await async_setup_services(hass)
    handler = handlers[SERVICE_DEVICE_INTERNET_ACCESS]

    with patch(
        "custom_components.asusrouter.services._get_entity_ids",
        return_value=[],
    ):
        async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
            tasks.create_task(
                handler(
                    Mock(
                        data={
                            "devices": [{"mac": first_mac}],
                            "state": "block",
                        }
                    )
                )
            )
            await write_started.wait()
            tasks.create_task(
                handler(
                    Mock(
                        data={
                            "devices": [{"mac": second_mac}],
                            "state": "block",
                        }
                    )
                )
            )
            await second_waiting.wait()
            assert reads == [set()]
            release_write.set()
            await confirm_started.wait()
            assert reads == [set(), {first_mac}]
            release_confirm.set()

    assert reads == [set(), {first_mac}, {first_mac}, {first_mac, second_mac}]
    assert writes == [{first_mac}, {first_mac, second_mac}]
    assert set(table) == {first_mac, second_mac}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["block", "allow"])
async def test_switch_uses_router_writer_and_retains_other_rules(
    state: str,
) -> None:
    """Both switch directions use one confirmed write of the entire table."""

    keep = ParentalControlRule(
        mac="00:11:22:33:44:55", name="Keep", type=PCRuleType.TIME
    )
    original = ParentalControlRule(
        mac="AA:BB:CC:DD:EE:FF",
        name="Console",
        type=PCRuleType.DISABLE if state == "block" else PCRuleType.BLOCK,
    )
    bridge = _bridge(rules={keep.mac: keep, original.mac: original})
    router = _router(bridge)
    switch = ClientInternetSwitch(router, original)

    async def write(**kwargs: dict) -> ServiceResult:
        assert switch._rule is original
        bridge.api.async_get_data.return_value = {
            "rules": _read_written_rules(kwargs["arguments"])
        }
        return _result(True, 0)

    bridge.api.async_run_service_result.side_effect = write
    if state == "block":
        await switch.async_turn_on()
    else:
        await switch.async_turn_off()

    router.async_set_internet_access.assert_awaited_once_with(
        state=state, devices=[{"mac": original.mac}]
    )
    assert _written_macs(bridge).split(">") == [keep.mac, original.mac]
    bridge.api.async_run_service_result.assert_awaited_once()
    router.update_pc_rules.assert_awaited_once_with(force=True)
    bridge.api.async_get_data.assert_has_awaits(
        [call(AsusData.PARENTAL_CONTROL, force=True)] * 2
    )
    bridge.api.async_set_state.assert_not_called()
    assert switch.is_on is (state == "block")


@pytest.mark.asyncio
@pytest.mark.parametrize("matches", [False, True])
async def test_switch_false_write_requires_matching_confirmation(
    matches: bool,
) -> None:
    """Rejected writes succeed only with a matching confirmation."""

    original = ParentalControlRule(
        mac="AA:BB:CC:DD:EE:FF", name="Console", type=PCRuleType.DISABLE
    )
    confirmed = ParentalControlRule(
        mac=original.mac,
        name=original.name,
        type=PCRuleType.BLOCK if matches else PCRuleType.DISABLE,
    )
    bridge = _bridge(False, rules={original.mac: original})
    bridge.api.async_get_data.side_effect = [
        {"rules": {original.mac: original}},
        *[{"rules": {confirmed.mac: confirmed}}] * 3,
    ]
    router = _router(bridge)
    switch = ClientInternetSwitch(router, original)

    with patch(
        "custom_components.asusrouter.router.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep:
        if matches:
            await switch.async_turn_on()
            assert switch.is_on is True
            router.update_pc_rules.assert_awaited_once_with(force=True)
            sleep.assert_not_awaited()
        else:
            with pytest.raises(
                HomeAssistantError, match="could not be confirmed"
            ):
                await switch.async_turn_on()
            assert switch._rule is original
            assert switch.is_on is False
            assert (
                router.update_pc_rules.await_args_list
                == [call(force=True)] * 3
            )
            assert sleep.await_args_list == [call(1.0), call(1.0)]

    bridge.api.async_run_service_result.assert_awaited_once()
    router.async_set_internet_access.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"rules": None},
        {"rules": []},
        {"rules": {"AA:BB:CC:DD:EE:FF": "not-a-rule"}},
        {
            "rules": {
                "AA:BB:CC:DD:EE:FF": ParentalControlRule(
                    mac="00:11:22:33:44:55"
                )
            }
        },
        {"rules": {"": ParentalControlRule(mac="")}},
        {"rules": {None: ParentalControlRule(mac=None)}},
        {"rules": {123: ParentalControlRule(mac=123)}},
    ],
)
async def test_pc_rule_rejects_malformed_snapshot_before_write(
    payload: object,
) -> None:
    """Every table entry must have a nonempty string key matching its rule."""

    bridge = _bridge()
    bridge.api.async_get_data.return_value = payload
    with pytest.raises(
        AsusRouterError, match="read the current parental control rules"
    ):
        await bridge.async_pc_rule(
            state="block", devices=[{"mac": "AA:BB:CC:DD:EE:FF"}]
        )
    bridge.api.async_run_service_result.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["block", "remove"])
async def test_retained_empty_fields_are_lossless_without_snapshot_mutation(
    state: str,
) -> None:
    """Retain empty fields without defaults or snapshot mutation."""

    keep = ParentalControlRule(
        mac="00:11:22:33:44:55", name=None, timemap=None, type=PCRuleType.TIME
    )
    target = ParentalControlRule(
        mac="AA:BB:CC:DD:EE:FF",
        name=None,
        timemap=None,
        type=PCRuleType.DISABLE,
    )
    snapshot = {keep.mac: keep, target.mac: target}
    bridge = _bridge(rules=snapshot)
    result = await bridge.async_pc_rule(
        state=state, devices=[{"mac": target.mac, "name": None}]
    )
    assert result.success is True

    arguments = bridge.api.async_run_service_result.await_args.kwargs[
        "arguments"
    ]
    assert arguments[KEY_PC_NAME].split(">")[0] == ""
    assert arguments[KEY_PC_TIMEMAP].split(">")[0] == ""
    assert arguments[KEY_PC_TYPE].split(">")[0] == str(PCRuleType.TIME.value)
    assert snapshot == {keep.mac: keep, target.mac: target}
    assert snapshot[keep.mac] is keep
    assert snapshot[target.mac] is target
    assert keep.name is None
    assert keep.timemap is None
    assert target.name is None
    assert target.timemap is None
    assert target.type == PCRuleType.DISABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [AsusRouterError("read failed"), OSError("offline")]
)
async def test_switch_wraps_library_errors(error: Exception) -> None:
    """Wrap expected router failures as Home Assistant errors with a cause."""

    router = _router()
    router.async_set_internet_access.side_effect = error
    original = ParentalControlRule(
        mac="AA:BB:CC:DD:EE:FF", type=PCRuleType.DISABLE
    )
    switch = ClientInternetSwitch(router, original)
    with pytest.raises(HomeAssistantError, match="Unable to change") as raised:
        await switch.async_turn_on()
    assert raised.value.__cause__ is error
    assert switch._rule is original


@pytest.mark.asyncio
async def test_confirmation_rejects_missing_rules_even_for_remove() -> None:
    """A malformed read-back cannot confirm removal as an empty table."""

    bridge = _bridge(True)
    bridge.api.async_get_data.side_effect = [{"rules": {}}, {}, {}, {}]
    router = _router(bridge)
    with (
        patch(
            "custom_components.asusrouter.router.asyncio.sleep",
            new_callable=AsyncMock,
        ),
        pytest.raises(HomeAssistantError, match="could not be confirmed"),
    ):
        await router.async_set_internet_access(
            state="remove", devices=[{"mac": "AA:BB:CC:DD:EE:FF"}]
        )

    bridge.api.async_run_service_result.assert_awaited_once()
    assert router.update_pc_rules.await_args_list == [call(force=True)] * 3


@pytest.mark.asyncio
async def test_refresh_does_not_remove_rules_from_library_cache() -> None:
    """Refreshing an HA rule must preserve the library's live table."""

    existing = ParentalControlRule(
        mac="AA:BB:CC:DD:EE:FF", type=PCRuleType.BLOCK
    )
    snapshot = {existing.mac: existing}
    bridge = _bridge(rules=snapshot)
    router = _router(bridge)
    router._pc_rules = dict(snapshot)

    assert await router.update_pc_rules(force=True)
    assert snapshot == {existing.mac: existing}
    assert snapshot[existing.mac] is existing
    bridge.api.async_run_service_result.assert_not_awaited()


def test_parental_control_sensor_data_tolerates_missing_rules() -> None:
    """The sensor coordinator must tolerate a poll without a rule table."""

    data = ARBridge._process_data_parental_control({})

    assert data["list"] == []


@pytest.mark.asyncio
async def test_refresh_keeps_known_rules_when_poll_omits_table() -> None:
    """A poll without a readable rule table is not a connection error."""

    existing = ParentalControlRule(
        mac="AA:BB:CC:DD:EE:FF", type=PCRuleType.BLOCK
    )
    bridge = _bridge()
    bridge.api.async_get_data.return_value = {}
    router = _router(bridge)
    router._pc_rules = {existing.mac: existing}

    assert not await router.update_pc_rules(force=True)
    assert router._pc_rules == {existing.mac: existing}
    assert router._connect_error is False


@pytest.mark.asyncio
async def test_switch_toggle_log_omits_mac_and_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A switch toggle must not write the device MAC or name to the log."""

    original = ParentalControlRule(
        mac="AA:BB:CC:DD:EE:FF",
        name="Sentinel Laptop",
        type=PCRuleType.DISABLE,
    )
    bridge = _bridge(rules={original.mac: original})
    router = _router(bridge)
    switch = ClientInternetSwitch(router, original)

    async def write(**kwargs: dict) -> ServiceResult:
        bridge.api.async_get_data.return_value = {
            "rules": _read_written_rules(kwargs["arguments"])
        }
        return _result(True, 0)

    bridge.api.async_run_service_result.side_effect = write
    with caplog.at_level(logging.DEBUG):
        await switch.async_turn_on()

    assert "AA:BB:CC:DD:EE:FF" not in caplog.text
    assert "Sentinel Laptop" not in caplog.text
    assert "rule_type=BLOCK" in caplog.text
