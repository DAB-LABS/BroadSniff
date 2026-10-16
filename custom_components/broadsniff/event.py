"""Capture events for Broadlink remotes."""

from __future__ import annotations

from base64 import b64encode
from typing import Any, override

from broadlink_async.remote import CapturedSignal

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BroadSniffConfigEntry
from .capture import CaptureKind, CaptureWindowResult, signal_kind
from .device import BroadlinkRemote
from .entity import BroadSniffEntity

PARALLEL_UPDATES = 0

ATTR_CODE = "code"
ATTR_FREQUENCY_MHZ = "frequency_mhz"
ATTR_SIGNALS = "signals"
ATTR_STOPPED = "stopped"

EVENT_SIGNAL = "signal"
EVENT_WINDOW_ENDED = "window_ended"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BroadSniffConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the capture events."""
    async_add_entities(
        BroadSniffCaptureEvent(remote, kind)
        for remote in entry.runtime_data.remotes.values()
        for kind in remote.capture_kinds
    )


class BroadSniffCaptureEvent(BroadSniffEntity, EventEntity):
    """Fires for every captured code and when a window closes.

    The code is in the form remote.send_command accepts (b64:...), so a
    capture can be sent straight back or stored.
    """

    _attr_event_types = [EVENT_SIGNAL, EVENT_WINDOW_ENDED]

    def __init__(self, device: BroadlinkRemote, kind: CaptureKind) -> None:
        """Initialize the event entity."""
        super().__init__(device)
        self._kind = kind
        if kind is CaptureKind.RF:
            self._attr_translation_key = "rf_capture"
            self._attr_unique_id = f"{device.unique_id}-rf_capture_event"
        else:
            self._attr_translation_key = "ir_capture"
            self._attr_unique_id = f"{device.unique_id}-ir_capture_event"

    @override
    async def async_added_to_hass(self) -> None:
        """Listen for signals and closed windows."""
        await super().async_added_to_hass()
        capture = self._device.capture
        self.async_on_remove(capture.async_add_signal_listener(self._on_signal))
        self.async_on_remove(capture.async_add_ended_listener(self._on_ended))

    @callback
    def _on_signal(self, signal: CapturedSignal, kind: CaptureKind) -> None:
        """Fire for a code caught on this entity's band."""
        if signal_kind(signal) is not self._kind:
            return
        data: dict[str, Any] = {
            ATTR_CODE: "b64:" + b64encode(signal.packet).decode("ascii")
        }
        if self._kind is CaptureKind.RF:
            data[ATTR_FREQUENCY_MHZ] = signal.frequency_mhz
        self._trigger_event(EVENT_SIGNAL, data)
        self.async_write_ha_state()

    @callback
    def _on_ended(self, result: CaptureWindowResult) -> None:
        """Fire when a window on this entity's band closes."""
        if result.kind is not self._kind:
            return
        self._trigger_event(
            EVENT_WINDOW_ENDED,
            {ATTR_SIGNALS: result.signals, ATTR_STOPPED: result.stopped},
        )
        self.async_write_ha_state()
