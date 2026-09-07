"""Tests for sensitive-data redaction in bridge logs."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, Mock

from asusrouter.modules.data import AsusData
from asusrouter.modules.parental_control import (
    KEY_PC_MAC,
    KEY_PC_NAME,
    ParentalControlCapabilities,
)
from asusrouter.modules.service import ServiceResult
import pytest

from custom_components.asusrouter.bridge import ARBridge

LOGGER_NAME = "custom_components.asusrouter.bridge"
SENTINELS = (
    "S3NT1NEL-PSK",
    "S3NT1NEL-RADIUS",
    "Sentinel Laptop",
    "AA:BB:CC:DD:EE:FF",
)
RAW_PAYLOAD = {
    "credentials": {
        "wpa_psk": SENTINELS[0],
        "radius_key": SENTINELS[1],
    },
    "client": {"name": SENTINELS[2], "mac": SENTINELS[3]},
}
EXPECTED_SENSORS = [
    "credentials_wpa_psk",
    "credentials_radius_key",
    "client_name",
    "client_mac",
]


def _bridge(*results: bool) -> ARBridge:
    """Create a bridge shell with mocked router reads and writes."""

    bridge = ARBridge.__new__(ARBridge)
    bridge._api = Mock()
    bridge.api.async_get_data = AsyncMock(return_value={"rules": {}})
    bridge.api.async_get_parental_control_capabilities = AsyncMock(
        return_value=ParentalControlCapabilities()
    )
    bridge.api.async_run_service_result = AsyncMock(
        side_effect=[
            ServiceResult(success=success, needed_time=None, last_id=None)
            for success in (results or (True,))
        ]
    )
    return bridge


def _bridge_messages(caplog: pytest.LogCaptureFixture) -> str:
    """Return messages emitted by the bridge logger."""

    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER_NAME
    )


def _assert_discovery_metadata(messages: str) -> None:
    """Assert discovery logs contain metadata but no router values."""

    assert "datatype=WLAN" in messages
    assert "response_type=dict" in messages
    assert "key_count=2" in messages
    assert all(sentinel not in messages for sentinel in SENTINELS)


@pytest.mark.asyncio
async def test_get_sensors_log_redacts_raw_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legacy sensor discovery should log metadata, not raw values."""

    bridge = _bridge()
    bridge.api.async_get_data.return_value = RAW_PAYLOAD
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    sensors = await bridge._get_sensors(
        AsusData.WLAN, sensor_type=AsusData.WLAN.value
    )

    assert sensors == EXPECTED_SENSORS
    _assert_discovery_metadata(_bridge_messages(caplog))


@pytest.mark.asyncio
async def test_get_sensors_modern_log_redacts_raw_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Modern sensor discovery should log metadata, not raw values."""

    bridge = _bridge()
    bridge.api.async_get_data.return_value = RAW_PAYLOAD
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    sensors = await bridge._get_sensors_modern(AsusData.WLAN)

    assert sensors == EXPECTED_SENSORS
    _assert_discovery_metadata(_bridge_messages(caplog))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("success", "outcome"),
    [(True, "success"), (False, "rejected")],
)
async def test_pc_rule_log_redacts_targets_and_preserves_arguments(
    caplog: pytest.LogCaptureFixture,
    success: bool,
    outcome: str,
) -> None:
    """Rule outcomes should omit targets without changing the router write."""

    bridge = _bridge(success)
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    result = await bridge.async_pc_rule(
        state="block",
        devices=[{"mac": SENTINELS[3], "name": SENTINELS[2]}],
    )

    assert result.success is success
    messages = _bridge_messages(caplog)
    assert f"outcome={outcome}" in messages
    assert "rule_type=BLOCK" in messages
    assert "target_count=1" in messages
    assert SENTINELS[2] not in messages
    assert SENTINELS[3] not in messages

    arguments = bridge.api.async_run_service_result.await_args.kwargs[
        "arguments"
    ]
    assert arguments[KEY_PC_MAC] == SENTINELS[3]
    assert arguments[KEY_PC_NAME] == SENTINELS[2]
