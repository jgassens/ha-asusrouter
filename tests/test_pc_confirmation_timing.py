"""Tests for parental-control confirmation timing and locking."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, call, patch

from asusrouter.modules.data import AsusData
from asusrouter.modules.parental_control import ParentalControlCapabilities
from asusrouter.modules.service import ServiceResult
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.asusrouter.bridge import ARBridge
from custom_components.asusrouter.router import ARDevice

DEVICES = [{"mac": "AA:BB:CC:DD:EE:FF"}]
REPORTED_DELAY = 5
WRITER_COUNT = 2


def _result(success: bool, needed_time: Any = None) -> ServiceResult:
    """Build service metadata, including deliberately invalid delays."""

    return ServiceResult(
        success=success, needed_time=needed_time, last_id=None
    )


def _router(
    result: ServiceResult,
    matches: bool | list[bool] = True,
) -> Mock:
    """Create the minimum router shell needed by the confirmation method."""

    router = Mock()
    router._pc_rule_lock = asyncio.Lock()
    router.bridge.async_pc_rule = AsyncMock(return_value=result)
    router.update_pc_rules = AsyncMock(return_value=True)
    if isinstance(matches, list):
        router._internet_access_state_matches.side_effect = matches
    else:
        router._internet_access_state_matches.return_value = matches
    return router


async def _set_internet_access(router: Mock) -> None:
    """Invoke the unbound device method on a lightweight router shell."""

    await ARDevice.async_set_internet_access(
        router, state="block", devices=DEVICES
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("matches", "expected_sleeps"),
    [
        ([True], [call(5)]),
        ([False, False, True], [call(5), call(1.0), call(1.0)]),
    ],
)
async def test_reported_delay_precedes_confirmation_reads(
    matches: list[bool], expected_sleeps: list[Any]
) -> None:
    """Wait once before reading, then retain one-second retry spacing."""

    router = _router(_result(True, 5), matches)

    with patch(
        "custom_components.asusrouter.router.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep:
        await _set_internet_access(router)

    assert sleep.await_args_list == expected_sleeps
    assert router.update_pc_rules.await_count == len(matches)
    router.bridge.async_pc_rule.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("needed_time", "expected_sleeps"),
    [
        (0, []),
        (None, [call(1.0)]),
        (600, [call(10.0)]),
        (True, [call(1.0)]),
        (-3, [call(1.0)]),
        (2.5, [call(1.0)]),
    ],
)
async def test_initial_confirmation_delay_policy(
    needed_time: Any, expected_sleeps: list[Any]
) -> None:
    """Sanitize, default, or cap call-local service delays."""

    router = _router(_result(True, needed_time))

    with patch(
        "custom_components.asusrouter.router.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep:
        await _set_internet_access(router)

    assert sleep.await_args_list == expected_sleeps
    router.update_pc_rules.assert_awaited_once_with(force=True)
    router.bridge.async_pc_rule.assert_awaited_once()


@pytest.mark.asyncio
async def test_unsuccessful_write_confirms_idempotent_state_immediately() -> (
    None
):
    """A false service result must not delay an idempotent read-back."""

    router = _router(_result(False, 600))

    with patch(
        "custom_components.asusrouter.router.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep:
        await _set_internet_access(router)

    sleep.assert_not_awaited()
    router.update_pc_rules.assert_awaited_once_with(force=True)
    router.bridge.async_pc_rule.assert_awaited_once()


@pytest.mark.asyncio
async def test_unsuccessful_unconfirmed_write_raises_after_three_reads() -> (
    None
):
    """Use all read-back attempts after failure without retrying the write."""

    matches = [False, False, False]
    router = _router(_result(False, 600), matches)

    with (
        patch(
            "custom_components.asusrouter.router.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep,
        pytest.raises(HomeAssistantError, match="could not be confirmed"),
    ):
        await _set_internet_access(router)

    assert sleep.await_args_list == [call(1.0), call(1.0)]
    assert router.update_pc_rules.await_count == len(matches)
    router.bridge.async_pc_rule.assert_awaited_once()


@pytest.mark.asyncio
async def test_initial_wait_blocks_only_writers_for_the_same_router() -> None:
    """Keep the local lock held during delay without blocking other routers."""

    wait_started = asyncio.Event()
    release_wait = asyncio.Event()
    second_waiting = asyncio.Event()

    class ObservedLock(asyncio.Lock):
        """Signal when another writer reaches an already-held lock."""

        async def acquire(self) -> bool:
            """Observe contention before waiting for the lock."""

            if self.locked():
                second_waiting.set()
            return await super().acquire()

    same_router = _router(_result(True, 5))
    same_router._pc_rule_lock = ObservedLock()
    same_router.bridge.async_pc_rule.side_effect = [
        _result(True, 5),
        _result(True, 0),
    ]
    other_router = _router(_result(True, 0))

    async def gated_sleep(delay: float) -> None:
        assert delay == REPORTED_DELAY
        wait_started.set()
        await release_wait.wait()

    with patch(
        "custom_components.asusrouter.router.asyncio.sleep",
        new_callable=AsyncMock,
        side_effect=gated_sleep,
    ) as sleep:
        first_writer = asyncio.create_task(_set_internet_access(same_router))
        await wait_started.wait()
        second_writer = asyncio.create_task(_set_internet_access(same_router))
        other_writer = asyncio.create_task(_set_internet_access(other_router))
        try:
            await second_waiting.wait()
            await other_writer
            assert not second_writer.done()
            assert same_router.bridge.async_pc_rule.await_count == 1
            other_router.bridge.async_pc_rule.assert_awaited_once()
        finally:
            release_wait.set()
            await asyncio.gather(first_writer, second_writer)

    assert sleep.await_args_list == [call(5)]
    assert same_router.bridge.async_pc_rule.await_count == WRITER_COUNT


@pytest.mark.asyncio
async def test_cancellation_during_initial_wait_releases_lock() -> None:
    """Cancellation must leave the per-router writer lock available."""

    wait_started = asyncio.Event()
    release_wait = asyncio.Event()
    router = _router(_result(True, 5))

    async def gated_sleep(delay: float) -> None:
        assert delay == REPORTED_DELAY
        wait_started.set()
        await release_wait.wait()

    with patch(
        "custom_components.asusrouter.router.asyncio.sleep",
        new_callable=AsyncMock,
        side_effect=gated_sleep,
    ) as sleep:
        writer = asyncio.create_task(_set_internet_access(router))
        await wait_started.wait()
        writer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await writer

    assert not router._pc_rule_lock.locked()
    sleep.assert_awaited_once_with(5)
    router.bridge.async_pc_rule.assert_awaited_once()
    router.update_pc_rules.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["allow", "block", "remove"])
async def test_each_rule_operation_calls_service_result_once(
    state: str,
) -> None:
    """Each whole-table operation performs exactly one metadata write call."""

    bridge = ARBridge.__new__(ARBridge)
    bridge._api = Mock()
    bridge.api.async_get_data = AsyncMock(return_value={"rules": {}})
    bridge.api.async_get_parental_control_capabilities = AsyncMock(
        return_value=ParentalControlCapabilities()
    )
    expected = _result(True, 5)
    bridge.api.async_run_service_result = AsyncMock(return_value=expected)
    bridge.api.async_run_service = AsyncMock()

    result = await bridge.async_pc_rule(state=state, devices=DEVICES)

    assert result is expected
    assert result.success is True
    bridge.api.async_get_data.assert_awaited_once_with(
        AsusData.PARENTAL_CONTROL, force=True
    )
    bridge.api.async_run_service_result.assert_awaited_once()
    bridge.api.async_run_service.assert_not_awaited()
