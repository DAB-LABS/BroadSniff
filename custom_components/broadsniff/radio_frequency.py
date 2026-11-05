"""Radio frequency receivers for Broadlink remotes that learn RF codes.

Home Assistant has radio frequency receivers from 2026.11; BroadSniff only
sets this platform up when they exist (see const.HAS_RF_RECEIVER).
"""

from __future__ import annotations

from typing import Any, override

from broadlink_async.remote import CapturedSignal

from homeassistant.components.radio_frequency import (
    RadioFrequencyReceivedSignal,
    RadioFrequencyReceiverEntity,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BroadSniffConfigEntry
from .capture import CaptureKind, signal_kind
from .const import RF_FREQUENCY_RANGES
from .device import BroadlinkRemote
from .entity import BroadSniffCaptureEntity
from .infrared import (
    ATTR_CAPTURE_MODE,
    ATTR_CAPTURE_SWITCH,
    ATTR_CAPTURING,
    capture_switch_entity_id,
    is_switch_event,
)
from .switch import ATTR_WINDOW_ENDS_AT, window_ends_at

PARALLEL_UPDATES = 0

# Beyond the windowed-receiver contract HAIR reads, an RF window says what
# it is doing (sweeping for the carrier while a button is held, or
# listening for a code) and which carrier it listens on.
ATTR_CAPTURE_PHASE = "capture_phase"
ATTR_FREQUENCY_HZ = "frequency_hz"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BroadSniffConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up an RF receiver for every remote that learns RF codes."""
    async_add_entities(
        BroadlinkRadioFrequencyReceiverEntity(remote)
        for remote in entry.runtime_data.remotes.values()
        if remote.supports_rf
    )


def broadlink_rf_pulses_to_timings(pulses: tuple[int, ...]) -> list[int]:
    """Convert a captured RF pulse sequence to signed microsecond timings.

    The device reports unsigned durations, carrier on first and
    alternating. Carrier on is positive and carrier off negative. Every
    duration is kept: an RF capture holds several repeats of a code, and
    the times between them are part of what was heard. On an RM4 pro the
    last value is where the read of the remote's buffer stopped, not a
    gap, and the duration at every 250th position can be stretched by a
    seam in that buffer; both are passed on as reported, for the reader
    to discard.
    """
    return [pulse if index % 2 == 0 else -pulse for index, pulse in enumerate(pulses)]


def signal_frequency_hz(signal: CapturedSignal) -> int | None:
    """Return the carrier a captured RF signal was heard on, in Hz.

    An RM4 Pro capture records the carrier it was learned on; for other
    packets the library gives the carrier the window listened on.
    """
    if signal.frequency_mhz is None:
        return None
    return round(signal.frequency_mhz * 1_000_000)


class BroadlinkRadioFrequencyReceiverEntity(
    BroadSniffCaptureEntity, RadioFrequencyReceiverEntity
):
    """Publishes captured RF codes on the radio frequency platform.

    The device only listens while an RF capture window is open, so the
    capturing attribute says whether it is listening right now. The state
    is written again whenever a window opens, closes, finds its carrier or
    moves its deadline, the capture mode changes, or the RF capture switch
    is renamed, disabled, enabled, removed or added.
    """

    _attr_translation_key = "rf_receiver"

    def __init__(self, device: BroadlinkRemote) -> None:
        """Initialize the entity."""
        super().__init__(device)
        self._attr_unique_id = f"{device.unique_id}-rf_receiver"
        # The switch ID last written, to tell a change that matters from
        # registry traffic that does not.
        self._capture_switch: str | None = None

    @property
    @override
    def supported_frequency_ranges(self) -> list[tuple[int, int]]:
        """Return the bands the remote receives on."""
        return RF_FREQUENCY_RANGES

    @override
    async def async_added_to_hass(self) -> None:
        """Listen for captured signals and for changes to the switch."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._device.capture.async_add_signal_listener(self._on_signal)
        )
        self.async_on_remove(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED,
                self._on_registry_updated,
                event_filter=is_switch_event,
            )
        )

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the windowed-receiver attributes and the window's progress."""
        capture = self._device.capture
        capturing = capture.kind is CaptureKind.RF
        attributes: dict[str, Any] = {
            ATTR_CAPTURING: capturing,
            ATTR_WINDOW_ENDS_AT: window_ends_at(self._device) if capturing else None,
            ATTR_CAPTURE_MODE: capture.mode.value,
            ATTR_CAPTURE_PHASE: capture.phase.value
            if capturing and capture.phase is not None
            else None,
            ATTR_FREQUENCY_HZ: capture.frequency_hz if capturing else None,
        }
        self._capture_switch = capture_switch_entity_id(
            self.hass, self._device, CaptureKind.RF
        )
        if self._capture_switch is not None:
            attributes[ATTR_CAPTURE_SWITCH] = self._capture_switch
        return attributes

    @callback
    def _on_registry_updated(
        self, event: Event[er.EventEntityRegistryUpdatedData]
    ) -> None:
        """Write the state again if the switch the receiver names changed."""
        current = capture_switch_entity_id(self.hass, self._device, CaptureKind.RF)
        if current != self._capture_switch:
            self.async_write_ha_state()

    @callback
    def _on_signal(self, signal: CapturedSignal, kind: CaptureKind) -> None:
        """Publish an RF code; infrared codes are not radio frequency."""
        if signal_kind(signal) is not CaptureKind.RF:
            return
        self._handle_received_signal(
            RadioFrequencyReceivedSignal(
                timings=broadlink_rf_pulses_to_timings(signal.pulses),
                frequency=signal_frequency_hz(signal),
            )
        )
