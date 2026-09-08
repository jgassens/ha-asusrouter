"""Exercise rule entity lifetimes with HA's installed platform and mock I/O."""

import asyncio
from collections.abc import AsyncIterator
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asusrouter.modules.parental_control import ParentalControlRule, PCRuleType
from homeassistant.const import Platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import EntityPlatform
import pytest
import pytest_asyncio

from custom_components.asusrouter import button, device_tracker, switch
from custom_components.asusrouter.const import ASUSROUTER, DOMAIN, ROUTER
from custom_components.asusrouter.router import ARDevice
from custom_components.asusrouter.services import (
    SERVICE_DEVICE_INTERNET_ACCESS,
    async_setup_services,
)
from tests.test_device_internet_access import (
    _bridge,
    _read_written_rules,
    _result,
    _service_hass,
)

MAC = "AA:BB:CC:DD:EE:FF"
OTHER_MAC = "00:11:22:33:44:55"


@pytest_asyncio.fixture
async def lifecycle() -> AsyncIterator[SimpleNamespace]:
    """Use real HA add/remove bookkeeping without network or HA startup."""

    original = ParentalControlRule(
        mac=MAC, name="Console", type=PCRuleType.BLOCK
    )
    router = ARDevice.__new__(ARDevice)
    router._mac = "24:4b:fe:f5:ee:20"
    router._mode = ROUTER
    router._pc_rules = {MAC: original}
    router._pc_rule_lock = asyncio.Lock()
    router._on_close = []
    router._conf_host = "router.test"
    router._connect_error = False
    router.create_devices = False
    router.bridge = _bridge(rules={MAC: original})
    hass, handlers = _service_hass(router)
    router.hass = hass
    hass.loop = asyncio.get_running_loop()
    hass.states.async_available.return_value = True
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    unload_callbacks = []
    entry = Mock(entry_id="router-1", options={})
    entry.async_on_unload.side_effect = unload_callbacks.append
    router._config_entry = entry

    registry = Mock()
    entries = {}
    registry.async_get_entity_id.side_effect = lambda _dom, _platform, uid: (
        entries[uid].entity_id if uid in entries else None
    )

    def register(_domain: str, _platform: str, uid: str, **_kwargs: dict):
        return entries.setdefault(
            uid,
            SimpleNamespace(
                entity_id=f"switch.{uid.replace(':', '')}",
                disabled=False,
                disabled_by=None,
                unique_id=uid,
                domain=Platform.SWITCH,
                platform=DOMAIN,
            ),
        )

    def remove(entity_id: str) -> None:
        uid = next(
            uid
            for uid, entry in entries.items()
            if entry.entity_id == entity_id
        )
        del entries[uid]

    registry.async_get_or_create.side_effect = register
    registry.async_remove.side_effect = remove
    registry.async_get.side_effect = lambda entity_id: next(
        (entry for entry in entries.values() if entry.entity_id == entity_id),
        None,
    )
    platform = Mock(
        hass=hass,
        config_entry=None,
        domain=Platform.SWITCH,
        platform_name=DOMAIN,
        entities={},
        domain_entities={},
        domain_platform_entities={},
        logger=logging.getLogger("test.entity_platform"),
    )
    platform._get_parallel_updates_semaphore.return_value = None
    platform._entity_id_already_exists.side_effect = lambda entity_id: (
        EntityPlatform._entity_id_already_exists(platform, entity_id)
    )
    pending = []

    async def drain() -> None:
        while pending:
            await EntityPlatform._async_add_entity(
                platform, pending.pop(0), False, registry, None
            )

    async def write(**kwargs: dict):
        router.bridge.api.async_get_data.return_value = {
            "rules": _read_written_rules(kwargs["arguments"])
        }
        return _result(True, 0)

    router.bridge.api.async_run_service_result.side_effect = write
    with (
        patch.object(switch, "async_setup_ar_entry", new_callable=AsyncMock),
        patch.object(switch.ClientInternetSwitch, "async_write_ha_state"),
        patch.object(
            switch.ClientInternetSwitch,
            "async_internal_added_to_hass",
            new_callable=AsyncMock,
        ),
        patch.object(
            switch.ClientInternetSwitch,
            "async_internal_will_remove_from_hass",
            new_callable=AsyncMock,
        ),
        patch(
            "homeassistant.helpers.entity_platform._async_derive_object_ids",
            return_value=("console", "console"),
        ),
        patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=registry,
        ),
        patch(
            "homeassistant.helpers.entity_registry."
            "async_entries_for_config_entry",
            side_effect=lambda *_args: list(entries.values()),
        ),
        patch(
            "custom_components.asusrouter.services._get_entity_ids",
            return_value=[],
        ),
    ):
        await switch.async_setup_entry(hass, entry, pending.extend)
        await drain()
        await async_setup_services(hass)
        yield SimpleNamespace(
            router=router,
            hass=hass,
            handlers=handlers,
            platform=platform,
            registry=registry,
            entries=entries,
            drain=drain,
            unload_callbacks=unload_callbacks,
        )
        for entity in list(platform.entities.values()):
            await entity.async_remove(force_remove=True)
        for unsubscribe in unload_callbacks:
            unsubscribe()
        for unsubscribe in router._on_close:
            unsubscribe()


async def _service(env: SimpleNamespace, state: str, mac: str = MAC) -> None:
    """Invoke the actual service handler and finish queued entity adds."""

    await env.handlers[SERVICE_DEVICE_INTERNET_ACCESS](
        Mock(data={"state": state, "devices": [{"mac": mac}]})
    )
    await env.drain()


@pytest.mark.asyncio
async def test_remove_deletes_entity_and_registry_without_platform_unload(
    lifecycle: SimpleNamespace,
) -> None:
    """Confirmed deletion clears all three installed HA platform maps."""

    env = lifecycle
    entity = next(iter(env.platform.entities.values()))
    await _service(env, "remove")
    assert not env.platform.entities
    assert not env.platform.domain_entities
    assert not env.platform.domain_platform_entities
    assert not env.entries
    env.registry.async_remove.assert_called_once_with(entity.entity_id)
    env.hass.states.async_remove.assert_called_once_with(
        entity.entity_id, context=entity._context
    )
    env.hass.config_entries.async_unload_platforms.assert_not_awaited()
    env.hass.config_entries.async_forward_entry_setups.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mac", [MAC, OTHER_MAC])
async def test_new_rule_after_remove_adds_exactly_one_live_entity(
    lifecycle: SimpleNamespace, mac: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A later rule, including the same MAC, reaches only the live platform."""

    env = lifecycle
    old = next(iter(env.platform.entities.values()))
    await _service(env, "remove")
    await _service(env, "block", mac)
    assert len(env.platform.entities) == 1
    new = next(iter(env.platform.entities.values()))
    assert new is not old
    if mac == MAC:
        assert new.unique_id == old.unique_id
    assert len(env.entries) == 1
    assert "does not generate unique IDs" not in caplog.text
    assert "Entity id already exists" not in caplog.text


@pytest.mark.asyncio
async def test_router_side_removal_marks_switch_unavailable(
    lifecycle: SimpleNamespace,
) -> None:
    """External deletion keeps the entity but publishes unavailable state."""

    env = lifecycle
    entity = next(iter(env.platform.entities.values()))
    entity.async_write_ha_state.reset_mock()
    env.router.bridge.api.async_get_data.return_value = {"rules": {}}
    assert await env.router.update_pc_rules(force=True)
    assert entity.available is False
    entity.async_write_ha_state.assert_called_once_with()
    assert entity.entity_id in env.platform.entities
    await _service(env, "block")
    assert entity.available is True
    assert list(env.platform.entities.values()) == [entity]


@pytest.mark.asyncio
async def test_remove_preserves_other_live_switches(
    lifecycle: SimpleNamespace,
) -> None:
    """Deleting one rule leaves unrelated entities and registry rows live."""

    env = lifecycle
    await _service(env, "block", OTHER_MAC)
    other = next(
        entity
        for entity in env.platform.entities.values()
        if entity._rule.mac == OTHER_MAC
    )
    await _service(env, "remove")
    assert list(env.platform.entities.values()) == [other]
    assert list(env.entries) == [other.unique_id]
    assert other.available is True
    assert other.is_on is True


@pytest.mark.asyncio
async def test_remove_cleans_registry_without_live_entity(
    lifecycle: SimpleNamespace,
) -> None:
    """Disabled or previously removed entities still have registry cleanup."""

    env = lifecycle
    entity = next(iter(env.platform.entities.values()))
    await entity.async_remove(force_remove=True)
    assert entity.unique_id in env.entries
    await _service(env, "remove")
    assert not env.entries
    env.hass.config_entries.async_unload_platforms.assert_not_awaited()


@pytest.mark.asyncio
async def test_registry_cleanup_failure_keeps_platform_live(
    lifecycle: SimpleNamespace,
) -> None:
    """A registry failure propagates without unloading unrelated switches."""

    env = lifecycle
    await _service(env, "block", OTHER_MAC)
    failure = RuntimeError("registry broken")
    env.registry.async_remove.side_effect = failure
    with pytest.raises(RuntimeError, match="registry broken") as raised:
        await _service(env, "remove")
    assert raised.value is failure
    assert len(env.platform.entities) == 1
    assert next(iter(env.platform.entities.values()))._rule.mac == OTHER_MAC
    assert not env.router._pc_rule_lock.locked()
    env.hass.config_entries.async_unload_platforms.assert_not_awaited()
    env.hass.config_entries.async_forward_entry_setups.assert_not_awaited()


@pytest.mark.asyncio
async def test_readd_waits_for_entity_removal_under_rule_lock(
    lifecycle: SimpleNamespace,
) -> None:
    """The next write cannot race the prior entity's awaited removal."""

    env = lifecycle
    old = next(iter(env.platform.entities.values()))
    removal_started = asyncio.Event()
    release_removal = asyncio.Event()
    next_writer_waiting = asyncio.Event()

    class ObservedLock(asyncio.Lock):
        """Expose the next writer reaching the lock during removal."""

        async def acquire(self) -> bool:
            """Record contention before awaiting the lock."""

            if self.locked():
                next_writer_waiting.set()
            return await super().acquire()

    env.router._pc_rule_lock = ObservedLock()

    async def wait_for_removal() -> None:
        removal_started.set()
        assert env.router._pc_rule_lock.locked()
        await release_removal.wait()

    old.async_will_remove_from_hass = AsyncMock(side_effect=wait_for_removal)
    async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
        tasks.create_task(_service(env, "remove"))
        await removal_started.wait()
        tasks.create_task(_service(env, "block"))
        await next_writer_waiting.wait()
        env.router.bridge.api.async_run_service_result.assert_awaited_once()
        release_removal.set()

    assert len(env.platform.entities) == 1
    new = next(iter(env.platform.entities.values()))
    assert new is not old
    assert new.unique_id == old.unique_id


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [switch, device_tracker, button])
async def test_entity_add_subscription_unsubscribes_on_entry_unload(
    module: object,
) -> None:
    """An unloaded entry cannot call a stale platform's add callback."""

    router = Mock(pc_rules={}, devices={}, sensor_coordinator={})
    hass = Mock(data={DOMAIN: {"router-1": {ASUSROUTER: router}}})
    callbacks = []
    entry = Mock(entry_id="router-1", options={})
    entry.async_on_unload.side_effect = callbacks.append
    add = Mock()
    signal = (
        router.signal_pc_rules_new
        if module is switch
        else router.signal_device_new
    )
    with patch.object(
        module,
        "add_entities" if module is not button else "add_static_dhcp_entities",
    ) as discover:
        await module.async_setup_entry(hass, entry, add)
        discover.reset_mock()
        assert len(callbacks) == 1
        callbacks[0]()
        async_dispatcher_send(hass, signal)
        discover.assert_not_called()
        router.async_on_close.assert_not_called()


def test_rule_removed_signal_is_scoped_to_router() -> None:
    """Removing a MAC from one router must not remove another router's rule."""

    first = ARDevice.__new__(ARDevice)
    second = ARDevice.__new__(ARDevice)
    first._config_entry = Mock(entry_id="first")
    second._config_entry = Mock(entry_id="second")
    assert first.signal_pc_rules_removed != second.signal_pc_rules_removed
