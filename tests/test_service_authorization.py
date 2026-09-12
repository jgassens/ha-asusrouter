"""Tests for service authorization."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, call, patch

from homeassistant.core import Context, ServiceCall
from homeassistant.exceptions import Unauthorized
import pytest

from custom_components.asusrouter.const import DOMAIN
from custom_components.asusrouter.router import ARDevice
from custom_components.asusrouter.services import (
    SERVICE_DEVICE_INTERNET_ACCESS,
    SERVICE_REFRESH_STATIC_DHCP_LEASES,
    SERVICE_REMOVE_STATIC_DHCP_LEASE,
    SERVICE_RESERVE_CURRENT_IP,
    SERVICE_SET_STATIC_DHCP_LEASE,
    async_setup_services,
)

ADMIN_SERVICES = (
    SERVICE_DEVICE_INTERNET_ACCESS,
    SERVICE_SET_STATIC_DHCP_LEASE,
    SERVICE_REMOVE_STATIC_DHCP_LEASE,
    SERVICE_RESERVE_CURRENT_IP,
    SERVICE_REFRESH_STATIC_DHCP_LEASES,
)


@pytest.mark.asyncio
async def test_all_services_are_registered_as_admin_only() -> None:
    """Every router-mutating service should use the admin helper."""

    hass = Mock()
    hass.services.has_service.return_value = False

    with patch(
        "custom_components.asusrouter.services.async_register_admin_service"
    ) as async_register_admin:
        await async_setup_services(hass)

    assert async_register_admin.call_args_list == [
        call(hass, DOMAIN, service, ANY, schema=ANY)
        for service in ADMIN_SERVICES
    ]
    hass.services.async_register.assert_not_called()


def _authorization_hass() -> tuple[Mock, dict[str, object]]:
    """Create a Home Assistant shell that captures registered handlers."""

    handlers: dict[str, object] = {}
    hass = Mock()
    hass.services.has_service.return_value = False
    hass.services.async_register.side_effect = (
        lambda _domain, service, handler, *_args, **_kwargs: (
            handlers.__setitem__(service, handler)
        )
    )
    hass.async_run_hass_job = Mock(
        side_effect=lambda service_job, service_call: service_job.target(
            service_call
        )
    )
    return hass, handlers


@pytest.mark.asyncio
async def test_non_admin_user_is_rejected_before_handler_runs() -> None:
    """A non-admin user should not reach the underlying service handler."""

    hass, handlers = _authorization_hass()
    hass.auth.async_get_user = AsyncMock(
        return_value=SimpleNamespace(is_admin=False)
    )
    await async_setup_services(hass)
    service_call = ServiceCall(
        DOMAIN,
        SERVICE_DEVICE_INTERNET_ACCESS,
        {},
        context=Context(user_id="non-admin-user"),
    )

    with (
        patch(
            "custom_components.asusrouter.services."
            "_async_device_internet_access",
            new_callable=AsyncMock,
        ) as handler_body,
        pytest.raises(Unauthorized),
    ):
        await handlers[SERVICE_DEVICE_INTERNET_ACCESS](service_call)

    handler_body.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_without_user_runs_handler() -> None:
    """Automation and system calls without a user should still run."""

    hass, handlers = _authorization_hass()
    await async_setup_services(hass)
    service_call = ServiceCall(
        DOMAIN,
        SERVICE_DEVICE_INTERNET_ACCESS,
        {},
        context=Context(),
    )

    with patch(
        "custom_components.asusrouter.services._async_device_internet_access",
        new_callable=AsyncMock,
    ) as handler_body:
        await handlers[SERVICE_DEVICE_INTERNET_ACCESS](service_call)

    handler_body.assert_awaited_once_with(hass, service_call)
    hass.auth.async_get_user.assert_not_called()


@pytest.mark.asyncio
async def test_remove_trackers_is_registered_as_admin_only() -> None:
    """The per-router tracker removal service is admin-only too."""

    router = ARDevice.__new__(ARDevice)
    router.hass = Mock()

    with patch(
        "custom_components.asusrouter.router.async_register_admin_service"
    ) as register:
        await router._init_services()

    register.assert_called_once_with(
        router.hass, DOMAIN, "remove_trackers", ANY
    )
    router.hass.services.async_register.assert_not_called()
