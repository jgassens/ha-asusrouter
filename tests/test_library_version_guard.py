"""Tests for the companion-library version guard."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

from homeassistant.exceptions import ConfigEntryError
import pytest

from custom_components.asusrouter import (
    async_setup_entry,
    check_library_version,
)
from custom_components.asusrouter.const import MIN_LIBRARY_VERSION


@pytest.mark.parametrize("installed", ["2.0.0+jgassens.1", "1.21.3"])
def test_guard_rejects_older_library(installed: str) -> None:
    """An older library must fail setup with an actionable message."""

    with (
        patch("custom_components.asusrouter.version", return_value=installed),
        pytest.raises(ConfigEntryError, match="or newer is required"),
    ):
        check_library_version()


@pytest.mark.parametrize(
    "installed", [MIN_LIBRARY_VERSION, "2.0.0+jgassens.3", "2.1.0"]
)
def test_guard_accepts_current_library(installed: str) -> None:
    """The pinned version and anything newer pass the guard."""

    with patch("custom_components.asusrouter.version", return_value=installed):
        check_library_version()


def test_guard_rejects_unreadable_version() -> None:
    """A version string that cannot be parsed is not silently accepted."""

    with (
        patch("custom_components.asusrouter.version", return_value="garbage"),
        pytest.raises(ConfigEntryError, match="unreadable version"),
    ):
        check_library_version()


@pytest.mark.asyncio
async def test_setup_entry_checks_library_before_building_router() -> None:
    """A stale library stops setup before the router is constructed."""

    hass = Mock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda job: job())

    with (
        patch(
            "custom_components.asusrouter.version",
            return_value="2.0.0+jgassens.1",
        ),
        patch("custom_components.asusrouter.ARDevice") as router,
        pytest.raises(ConfigEntryError),
    ):
        await async_setup_entry(hass, Mock())

    hass.async_add_executor_job.assert_awaited_once_with(check_library_version)
    router.assert_not_called()


def test_installed_test_library_satisfies_guard() -> None:
    """The library in the test environment must match the declared minimum."""

    check_library_version()
