"""Tests for entity command error propagation."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.asusrouter.button import ARButton
from custom_components.asusrouter.entity import ARBinaryEntity

SENTINEL_ERROR = "sentinel entity command failure"


@pytest.mark.asyncio
async def test_binary_entity_set_state_raises_home_assistant_error() -> None:
    """A failed LED command is surfaced to Home Assistant."""

    entity = Mock()
    entity.api.async_set_state = AsyncMock(
        side_effect=RuntimeError(SENTINEL_ERROR)
    )
    entity.coordinator.async_request_refresh = AsyncMock()

    with pytest.raises(HomeAssistantError, match=SENTINEL_ERROR) as raised:
        await ARBinaryEntity._set_state(entity, Mock())

    assert isinstance(raised.value.__cause__, RuntimeError)
    entity.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_button_set_state_raises_home_assistant_error() -> None:
    """A failed button command is surfaced to Home Assistant."""

    button = Mock()
    button.api.async_set_state = AsyncMock(
        side_effect=RuntimeError(SENTINEL_ERROR)
    )

    with pytest.raises(HomeAssistantError, match=SENTINEL_ERROR) as raised:
        await ARButton._set_state(button, Mock())

    assert isinstance(raised.value.__cause__, RuntimeError)
