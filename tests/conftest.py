"""Fixtures for BroadSniff tests.

The built-in Broadlink integration is replaced with a stub that registers
each entry's device the way the real one does, by MAC address. BroadSniff
only reads the entries and the device registry, so the stub is all it
sees.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from custom_components.broadsniff.const import BROADLINK_DOMAIN
from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.const import CONF_MAC, MAJOR_VERSION, MINOR_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import (
    MockModule,
    mock_config_flow,
    mock_integration,
    mock_platform,
)

from . import FakeApis

# What the stub's setup and unload return to say they succeeded. From Home
# Assistant 2026.11 a built-in integration returns None and a boolean is an
# error; earlier releases require a boolean. The stub stands in for a
# built-in integration, so it answers the way each release expects.
STUB_OK: bool | None = None if (MAJOR_VERSION, MINOR_VERSION) >= (2026, 11) else True


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components in every test."""


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Enable entities that start disabled."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        return_value=True,
        new_callable=PropertyMock,
    ):
        yield


@pytest.fixture
def fake_apis() -> Generator[FakeApis]:
    """Replace the library's device factory."""
    apis = FakeApis()
    with patch(
        "custom_components.broadsniff.device.blk.gendevice",
        side_effect=apis.create,
    ):
        yield apis


class _StubFlow(ConfigFlow):
    """Stands in for the built-in integration's config flow."""

    VERSION = 1


@pytest.fixture(autouse=True)
def fast_sweep() -> Generator[None]:
    """Poll a sweep every few milliseconds instead of every half second."""
    with patch("custom_components.broadsniff.capture.DEFAULT_POLL_INTERVAL", 0.005):
        yield


@pytest.fixture
async def broadlink(hass: HomeAssistant) -> AsyncGenerator[None]:
    """Replace the built-in Broadlink integration with a stub."""

    async def setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool | None:
        dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            connections={(dr.CONNECTION_NETWORK_MAC, entry.data[CONF_MAC])},
            identifiers={(BROADLINK_DOMAIN, entry.unique_id)},
            manufacturer="Broadlink",
            name=entry.title,
        )
        return STUB_OK

    mock_integration(
        hass,
        MockModule(
            BROADLINK_DOMAIN,
            async_setup_entry=setup_entry,
            async_unload_entry=AsyncMock(return_value=STUB_OK),
        ),
    )
    mock_platform(hass, f"{BROADLINK_DOMAIN}.config_flow")
    with mock_config_flow(BROADLINK_DOMAIN, _StubFlow):
        assert await async_setup_component(hass, BROADLINK_DOMAIN, {})
        yield
