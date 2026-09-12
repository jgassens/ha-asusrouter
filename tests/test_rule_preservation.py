"""Regression tests for lossless parental-control toggles."""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

from asusrouter.modules.parental_control import (
    DEFAULT_PC_TIMEMAP,
    KEY_PC_NAME,
    KEY_PC_TIMEMAP,
    ParentalControlRule,
    PCRuleType,
)
from asusrouter.modules.service import ServiceResult
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.asusrouter.services import (
    SERVICE_DEVICE_INTERNET_ACCESS,
    async_setup_services,
)
from custom_components.asusrouter.switch import ClientInternetSwitch
from tests.test_device_internet_access import (
    _bridge,
    _read_written_rules,
    _result,
    _router,
    _service_hass,
)

MAC = "AA:BB:CC:DD:EE:FF"
CUSTOM_TIMEMAP = "W01308000930<W02417302245<W04010100200"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["block", "allow"])
@pytest.mark.parametrize("name", [None, "", "Renamed"])
async def test_existing_rule_retains_schedule_and_optional_name(
    state: str, name: str | None
) -> None:
    """Updating a TIME row changes only type and an explicit nonempty name."""

    original = ParentalControlRule(
        mac=MAC,
        name="Router Name",
        timemap=CUSTOM_TIMEMAP,
        type=PCRuleType.TIME,
    )
    bridge = _bridge(rules={MAC: original})
    device = {"mac": MAC}
    if name is not None:
        device["name"] = name
    await bridge.async_pc_rule(state=state, devices=[device])

    arguments = bridge.api.async_run_service_result.await_args.kwargs[
        "arguments"
    ]
    assert arguments[KEY_PC_TIMEMAP].encode() == CUSTOM_TIMEMAP.encode()
    assert arguments[KEY_PC_NAME] == (name or original.name)
    assert original.type is PCRuleType.TIME
    assert original.name == "Router Name"
    assert original.timemap == CUSTOM_TIMEMAP


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["block", "allow"])
async def test_switch_preserves_fresh_rule_instead_of_stale_entity(
    state: str,
) -> None:
    """A toggle retains router edits made since the last entity refresh."""

    original = ParentalControlRule(
        mac=MAC,
        name="Router Name",
        timemap=CUSTOM_TIMEMAP,
        type=PCRuleType.TIME,
    )
    bridge = _bridge(rules={MAC: original})
    router = _router(bridge)
    switch = ClientInternetSwitch(
        router, replace(original, name="Old Tracker Name", timemap="stale")
    )

    async def write(**kwargs: dict) -> ServiceResult:
        bridge.api.async_get_data.return_value = {
            "rules": _read_written_rules(kwargs["arguments"])
        }
        return _result(True, 0)

    bridge.api.async_run_service_result.side_effect = write
    if state == "block":
        await switch.async_turn_on()
    else:
        await switch.async_turn_off()

    arguments = bridge.api.async_run_service_result.await_args.kwargs[
        "arguments"
    ]
    assert arguments[KEY_PC_TIMEMAP].encode() == CUSTOM_TIMEMAP.encode()
    assert arguments[KEY_PC_NAME] == original.name
    assert switch._rule.timemap == CUSTOM_TIMEMAP
    assert switch._rule.name == original.name
    assert switch.is_on is (state == "block")


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_tracker_name_only_supplies_new_rule_default(
    existing: bool,
) -> None:
    """A discovered tracker name must not rename an existing router rule."""

    original = ParentalControlRule(
        mac=MAC,
        name="Router Name",
        timemap=CUSTOM_TIMEMAP,
        type=PCRuleType.TIME,
    )
    bridge = _bridge(rules={MAC: original} if existing else {})
    router = _router(bridge)
    hass, handlers = _service_hass(router)
    await async_setup_services(hass)

    async def write(**kwargs: dict) -> ServiceResult:
        bridge.api.async_get_data.return_value = {
            "rules": _read_written_rules(kwargs["arguments"])
        }
        return _result(True, 0)

    bridge.api.async_run_service_result.side_effect = write
    with (
        patch(
            "custom_components.asusrouter.services._get_entity_ids",
            return_value=["device_tracker.console"],
        ),
        patch(
            "custom_components.asusrouter.services._device_tracker_entity_data",
            return_value=(router, MAC, "Tracker Name"),
        ),
    ):
        await handlers[SERVICE_DEVICE_INTERNET_ACCESS](
            Mock(data={"state": "block"})
        )

    arguments = bridge.api.async_run_service_result.await_args.kwargs[
        "arguments"
    ]
    assert arguments[KEY_PC_NAME] == (
        "Router Name" if existing else "Tracker Name"
    )
    assert arguments[KEY_PC_TIMEMAP] == (
        CUSTOM_TIMEMAP if existing else DEFAULT_PC_TIMEMAP
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["timemap", "name"])
@pytest.mark.parametrize("success", [False, True])
async def test_confirmation_rejects_changed_retained_field(
    field: str, success: bool
) -> None:
    """Matching type cannot confirm a write that damaged retained fields."""

    original = ParentalControlRule(
        mac=MAC,
        name="Router Name",
        timemap=CUSTOM_TIMEMAP,
        type=PCRuleType.TIME,
    )
    bridge = _bridge(rules={MAC: original})
    router = _router(bridge)

    async def write(**kwargs: dict) -> ServiceResult:
        rules = _read_written_rules(kwargs["arguments"])
        rules[MAC] = replace(rules[MAC], **{field: "changed by router"})
        bridge.api.async_get_data.return_value = {"rules": rules}
        return _result(success, 0)

    bridge.api.async_run_service_result.side_effect = write
    with (
        patch(
            "custom_components.asusrouter.router.asyncio.sleep",
            new_callable=AsyncMock,
        ),
        pytest.raises(HomeAssistantError, match="could not be confirmed"),
    ):
        await router.async_set_internet_access(
            state="block", devices=[{"mac": MAC}]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [None, ""])
async def test_target_empty_fields_remain_empty(empty: str | None) -> None:
    """Existing empty target fields must not acquire library defaults."""

    original = ParentalControlRule(
        mac=MAC, name=empty, timemap=empty, type=PCRuleType.TIME
    )
    bridge = _bridge(rules={MAC: original})
    await bridge.async_pc_rule(state="block", devices=[{"mac": MAC}])
    arguments = bridge.api.async_run_service_result.await_args.kwargs[
        "arguments"
    ]
    assert arguments[KEY_PC_NAME] == ""
    assert arguments[KEY_PC_TIMEMAP] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("mac", [MAC, MAC.lower()])
@pytest.mark.parametrize("timemap", [None, "", "W01308000930&#60W02417302245"])
async def test_confirmation_accepts_preserved_wire_representation(
    mac: str, timemap: str | None
) -> None:
    """Compare decoded schedules and blank fields using wire semantics."""

    original = ParentalControlRule(
        mac=mac, name=None, timemap=timemap, type=PCRuleType.TIME
    )
    bridge = _bridge(rules={mac: original})
    router = _router(bridge)

    async def write(**kwargs: dict) -> ServiceResult:
        bridge.api.async_get_data.return_value = {
            "rules": _read_written_rules(kwargs["arguments"])
        }
        return _result(True, 0)

    bridge.api.async_run_service_result.side_effect = write
    await router.async_set_internet_access(
        state="block", devices=[{"mac": MAC}]
    )
    arguments = bridge.api.async_run_service_result.await_args.kwargs[
        "arguments"
    ]
    assert arguments[KEY_PC_NAME] == ""
    assert arguments[KEY_PC_TIMEMAP] == (timemap or "").replace("&#60", "<")
    assert list(router.pc_rules) == [mac]


@pytest.mark.asyncio
async def test_confirmation_accepts_html_escaped_name_readback() -> None:
    """A router that escapes `&` on readback still confirms the write."""

    original = ParentalControlRule(
        mac=MAC, name="Tom & Jerry", type=PCRuleType.DISABLE
    )
    bridge = _bridge(rules={MAC: original})
    router = _router(bridge)

    async def write(**kwargs: dict) -> ServiceResult:
        rules = _read_written_rules(kwargs["arguments"])
        bridge.api.async_get_data.return_value = {
            "rules": {
                mac: replace(rule, name=rule.name.replace("&", "&amp;"))
                for mac, rule in rules.items()
            }
        }
        return _result(True, 0)

    bridge.api.async_run_service_result.side_effect = write
    await router.async_set_internet_access(
        state="block", devices=[{"mac": MAC}]
    )
    assert router.pc_rules[MAC].type == PCRuleType.BLOCK
