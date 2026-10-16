"""Helpers for BroadSniff tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import broadlink_async as blk
from broadlink_async.remote import CapturedSignal

from custom_components.broadsniff.const import BROADLINK_DOMAIN, DOMAIN
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_TIMEOUT, CONF_TYPE
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry


@dataclass(frozen=True)
class Remote:
    """A device as the built-in integration stores it."""

    name: str
    product_id: int
    mac: str
    host: str


_GENDEVICE = blk.gendevice

GARAGE = Remote("Garage", 0x520B, "34ea34c43f31", "192.168.0.10")  # RM4 pro
ENTRANCE = Remote("Entrance", 0x5F36, "34ea34b61d2e", "192.168.0.11")  # RM mini 3
BEDROOM = Remote("Bedroom", 0x7568, "34ea34b4b9c8", "192.168.0.12")  # SP4 plug


class FakeWindow:
    """Stands in for the library's capture and capture_rf generators."""

    def __init__(self) -> None:
        """Initialize."""
        self.queue: asyncio.Queue[CapturedSignal | None] = asyncio.Queue()
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.closed = 0
        self.error: Exception | None = None

    def factory(self, name: str):
        """Return a replacement for api.capture or api.capture_rf."""

        def start(*args: Any, **kwargs: Any) -> AsyncGenerator[CapturedSignal]:
            self.calls.append((name, args, kwargs))
            return self._run(kwargs.get("stop_after_first", True))

        return start

    async def _run(self, stop_after_first: bool) -> AsyncGenerator[CapturedSignal]:
        try:
            while True:
                if self.error is not None:
                    raise self.error
                signal = await self.queue.get()
                if signal is None:
                    return
                yield signal
                if stop_after_first:
                    return
        finally:
            self.closed += 1

    def send(self, signal: CapturedSignal | None) -> None:
        """Deliver a signal, or close the window with None."""
        self.queue.put_nowait(signal)


@dataclass
class FakeApis:
    """The library devices BroadSniff opened, by MAC."""

    apis: dict[str, MagicMock] = field(default_factory=dict)
    windows: dict[str, FakeWindow] = field(default_factory=dict)

    def create(
        self, product_id: int, host: tuple[str, int], mac: bytes, name: str = ""
    ) -> MagicMock:
        """Build a fake device the way gendevice would."""
        real = _GENDEVICE(product_id, host, mac, name=name)
        window = FakeWindow()
        api = MagicMock()
        api.type = real.type
        api.manufacturer = real.manufacturer
        api.model = real.model
        api.host = host
        api.auth = AsyncMock(return_value=True)
        api.aclose = AsyncMock()
        api.capture = MagicMock(side_effect=window.factory("capture"))
        api.capture_rf = MagicMock(side_effect=window.factory("capture_rf"))
        self.apis[mac.hex()] = api
        self.windows[mac.hex()] = window
        return api


async def add_remote(hass: HomeAssistant, remote: Remote) -> MockConfigEntry:
    """Set up a device in the built-in integration stub."""
    entry = MockConfigEntry(
        domain=BROADLINK_DOMAIN,
        title=remote.name,
        unique_id=remote.mac,
        data={
            CONF_HOST: remote.host,
            CONF_MAC: remote.mac,
            CONF_TYPE: remote.product_id,
            CONF_TIMEOUT: 10,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def setup_capture(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Set up BroadSniff."""
    entry = MockConfigEntry(domain=DOMAIN, title="BroadSniff", options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
