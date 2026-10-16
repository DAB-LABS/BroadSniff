"""Capture switches for Broadlink remotes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, override

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BroadSniffConfigEntry
from .capture import CaptureKind
from .device import BroadlinkRemote
from .entity import BroadSniffCaptureEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

ATTR_WINDOW_ENDS_AT = "window_ends_at"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BroadSniffConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the capture switches."""
    async_add_entities(
        BroadSniffCaptureSwitch(remote, kind)
        for remote in entry.runtime_data.remotes.values()
        for kind in remote.capture_kinds
    )


def window_ends_at(device: BroadlinkRemote) -> str | None:
    """Return the open window's deadline as an ISO string."""
    ends_at = device.capture.ends_at
    return ends_at.isoformat() if ends_at is not None else None


class BroadSniffCaptureSwitch(BroadSniffCaptureEntity, SwitchEntity):
    """Opens a capture window while on.

    Turning it off closes the window and takes the device out of learning
    mode. It turns itself off when the window closes on its own.
    """

    def __init__(self, device: BroadlinkRemote, kind: CaptureKind) -> None:
        """Initialize the switch."""
        super().__init__(device)
        self._kind = kind
        if kind is CaptureKind.RF:
            self._attr_translation_key = "rf_capture"
            self._attr_unique_id = f"{device.unique_id}-rf_capture"
        else:
            self._attr_translation_key = "ir_capture"
            self._attr_unique_id = f"{device.unique_id}-ir_capture"

    @property
    @override
    def is_on(self) -> bool:
        """Return True while a window of this band is open."""
        return self._device.capture.kind is self._kind

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when the open window closes."""
        return {
            ATTR_WINDOW_ENDS_AT: window_ends_at(self._device) if self.is_on else None
        }

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Open a window with the current settings."""
        window = await self._device.capture.async_start(self._kind)
        window.add_done_callback(self._log_window_result)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Close the window if this band's window is open."""
        if self.is_on:
            await self._device.capture.async_stop()

    @callback
    def _log_window_result(self, window: asyncio.Task[int]) -> None:
        """Log a window that ended with a device error."""
        if window.cancelled():
            return
        if (err := window.exception()) is not None:
            _LOGGER.error("Capture window on %s failed: %s", self._device.name, err)
