"""Infrared receivers for Broadlink remotes."""

from __future__ import annotations

from typing import Any, override

from broadlink_async.remote import CapturedSignal

from homeassistant.components.infrared import (
    InfraredReceivedSignal,
    InfraredReceiverEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BroadSniffConfigEntry
from .capture import CaptureKind, signal_kind
from .device import BroadlinkRemote
from .entity import BroadSniffCaptureEntity
from .switch import ATTR_WINDOW_ENDS_AT, window_ends_at

PARALLEL_UPDATES = 0

ATTR_CAPTURING = "capturing"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BroadSniffConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the infrared receivers."""
    async_add_entities(
        BroadlinkInfraredReceiverEntity(remote)
        for remote in entry.runtime_data.remotes.values()
    )


def broadlink_pulses_to_timings(pulses: tuple[int, ...]) -> list[int]:
    """Convert a captured pulse sequence to signed microsecond timings.

    The device reports unsigned durations, mark first and alternating. The
    infrared platform wants marks positive and spaces negative, ending on a
    mark as ESPHome receivers do, so the trailing end-of-code gap is dropped.
    """
    timings = [
        pulse if index % 2 == 0 else -pulse for index, pulse in enumerate(pulses)
    ]
    if len(timings) % 2 == 0:
        timings.pop()
    return timings


class BroadlinkInfraredReceiverEntity(BroadSniffCaptureEntity, InfraredReceiverEntity):
    """Publishes captured infrared codes on the infrared platform.

    The device only listens while a capture window is open, so the
    capturing attribute says whether it is listening right now.
    """

    _attr_translation_key = "infrared_receiver"

    def __init__(self, device: BroadlinkRemote) -> None:
        """Initialize the entity."""
        super().__init__(device)
        self._attr_unique_id = f"{device.unique_id}-receiver"

    @override
    async def async_added_to_hass(self) -> None:
        """Listen for captured signals."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._device.capture.async_add_signal_listener(self._on_signal)
        )

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return whether the device is listening and until when."""
        capturing = self._device.capture.kind is CaptureKind.IR
        return {
            ATTR_CAPTURING: capturing,
            ATTR_WINDOW_ENDS_AT: window_ends_at(self._device) if capturing else None,
        }

    @callback
    def _on_signal(self, signal: CapturedSignal, kind: CaptureKind) -> None:
        """Publish an infrared code; radio frequency codes are not infrared."""
        if signal_kind(signal) is not CaptureKind.IR:
            return
        self._handle_received_signal(
            InfraredReceivedSignal(timings=broadlink_pulses_to_timings(signal.pulses))
        )
