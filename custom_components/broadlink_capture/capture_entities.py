"""Entities for capture on Broadlink universal remotes.

Kept apart from the platform modules, which follow core's Broadlink
integration, so syncing with core stays a small diff.
"""

from __future__ import annotations

import asyncio
from base64 import b64encode
import logging
from typing import Any, override

from broadlink_async.remote import CapturedSignal, SignalKind

from homeassistant.components.event import EventEntity
from homeassistant.components.infrared import (
    InfraredReceivedSignal,
    InfraredReceiverEntity,
)
from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import (
    STATE_ON,
    EntityCategory,
    Platform,
    UnitOfFrequency,
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.restore_state import RestoreEntity

from .capture import CaptureKind, CaptureWindowResult
from .const import (
    DEFAULT_CAPTURE_WINDOW,
    DEFAULT_SLIDING_WINDOW,
    DEFAULT_STOP_AFTER_FIRST,
    DOMAIN,
    DOMAINS_AND_TYPES,
    MAX_CAPTURE_WINDOW,
    MAX_RF_LEARN_FREQUENCY,
    MIN_CAPTURE_WINDOW,
    MIN_RF_LEARN_FREQUENCY,
    RF_LEARN_FREQUENCY_SWEEP,
)
from .device import BroadlinkDevice
from .entity import BroadlinkEntity

_LOGGER = logging.getLogger(__name__)

ATTR_CAPTURING = "capturing"
ATTR_WINDOW_ENDS_AT = "window_ends_at"
ATTR_CODE = "code"
ATTR_FREQUENCY_MHZ = "frequency_mhz"
ATTR_SIGNALS = "signals"
ATTR_STOPPED = "stopped"

EVENT_SIGNAL = "signal"
EVENT_WINDOW_ENDED = "window_ended"


def supports_rf(device: BroadlinkDevice) -> bool:
    """Return True if the device can capture radio frequency."""
    return device.api.type in DOMAINS_AND_TYPES[Platform.RADIO_FREQUENCY]


def capture_kinds(device: BroadlinkDevice) -> list[CaptureKind]:
    """Return the bands the device can capture on."""
    return [CaptureKind.IR, CaptureKind.RF] if supports_rf(device) else [CaptureKind.IR]


def _window_ends_at(device: BroadlinkDevice) -> str | None:
    """Return the open window's deadline as an ISO string."""
    ends_at = device.capture.ends_at
    return ends_at.isoformat() if ends_at is not None else None


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


class BroadlinkCaptureEntity(BroadlinkEntity):
    """Base for entities that follow the device's capture windows."""

    _attr_has_entity_name = True

    @override
    async def async_added_to_hass(self) -> None:
        """Follow window changes on top of the device updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._device.capture.async_add_state_listener(self.async_write_ha_state)
        )


class BroadlinkCaptureSwitch(BroadlinkCaptureEntity, SwitchEntity):
    """Opens a capture window while on.

    Turning it off closes the window and takes the device out of learning
    mode. It turns itself off when the window closes on its own.
    """

    def __init__(self, device: BroadlinkDevice, kind: CaptureKind) -> None:
        """Initialize the switch."""
        super().__init__(device)
        self._kind = kind
        if kind is CaptureKind.RF:
            self._attr_translation_key = "rf_capture"
            self._attr_unique_id = f"{device.unique_id}-rf_capture"
        else:
            self._attr_translation_key = "capture"
            self._attr_unique_id = f"{device.unique_id}-capture"

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
            ATTR_WINDOW_ENDS_AT: _window_ends_at(self._device) if self.is_on else None
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


class BroadlinkCaptureSettingSwitch(BroadlinkEntity, SwitchEntity, RestoreEntity):
    """A capture setting kept on the device and restored across restarts."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _setting: str
    _default: bool

    def __init__(self, device: BroadlinkDevice) -> None:
        """Initialize the switch."""
        super().__init__(device)
        self._attr_unique_id = f"{device.unique_id}-{self._setting}"
        self._attr_is_on = self._default

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last value and hand it to the device."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._apply(last_state.state == STATE_ON)

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the setting on."""
        self._apply(True)
        self.async_write_ha_state()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the setting off."""
        self._apply(False)
        self.async_write_ha_state()

    def _apply(self, value: bool) -> None:
        """Set the value on the device and on the entity."""
        setattr(self._device.capture, self._setting, value)
        self._attr_is_on = value


class BroadlinkStopAfterFirstSwitch(BroadlinkCaptureSettingSwitch):
    """Close a capture window as soon as it catches a signal."""

    _attr_translation_key = "stop_after_first"
    _setting = "stop_after_first"
    _default = DEFAULT_STOP_AFTER_FIRST


class BroadlinkSlidingWindowSwitch(BroadlinkCaptureSettingSwitch):
    """Restart a capture window's countdown on every signal."""

    _attr_translation_key = "sliding_window"
    _setting = "sliding"
    _default = DEFAULT_SLIDING_WINDOW


class BroadlinkCaptureNumber(BroadlinkEntity, RestoreNumber):
    """A capture setting kept on the device and restored across restarts."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _setting: str

    def __init__(self, device: BroadlinkDevice) -> None:
        """Initialize the number."""
        super().__init__(device)
        self._attr_unique_id = f"{device.unique_id}-{self._attr_translation_key}"

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last value and hand it to the device."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._apply(last.native_value)

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Store the value on the device."""
        self._apply(value)
        self.async_write_ha_state()

    def _apply(self, value: float) -> None:
        """Set the value on the device and on the entity."""
        setattr(self._device.capture, self._setting, value)
        self._attr_native_value = value


class BroadlinkCaptureWindowNumber(BroadlinkCaptureNumber):
    """How long a capture window stays open, in seconds."""

    _attr_translation_key = "capture_window"
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = MIN_CAPTURE_WINDOW
    _attr_native_max_value = MAX_CAPTURE_WINDOW
    _attr_native_step = 1
    _attr_native_value = DEFAULT_CAPTURE_WINDOW
    _setting = "window"


class BroadlinkRFLearnFrequencyNumber(BroadlinkCaptureNumber):
    """The carrier the device listens on for RF capture and learning.

    Zero, the default, has the device sweep for the carrier while a button
    is held. Naming the carrier skips the sweep, which is unreliable on some
    firmware.
    """

    _attr_translation_key = "rf_learn_frequency"
    _attr_native_unit_of_measurement = UnitOfFrequency.MEGAHERTZ
    _attr_native_min_value = RF_LEARN_FREQUENCY_SWEEP
    _attr_native_max_value = MAX_RF_LEARN_FREQUENCY
    _attr_native_step = 0.01
    _attr_native_value = RF_LEARN_FREQUENCY_SWEEP
    _setting = "rf_frequency"

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Accept zero (sweep) or a carrier inside the device's bands."""
        if value != RF_LEARN_FREQUENCY_SWEEP and value < MIN_RF_LEARN_FREQUENCY:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="rf_learn_frequency_invalid",
                translation_placeholders={
                    "min": f"{MIN_RF_LEARN_FREQUENCY:g}",
                    "max": f"{MAX_RF_LEARN_FREQUENCY:g}",
                },
            )
        await super().async_set_native_value(value)


class BroadlinkCaptureEvent(BroadlinkEntity, EventEntity):
    """Fires for every captured code and when a window closes.

    The code is in the form remote.send_command accepts (b64:...), so a
    capture can be sent straight back or stored.
    """

    _attr_has_entity_name = True
    _attr_event_types = [EVENT_SIGNAL, EVENT_WINDOW_ENDED]

    def __init__(self, device: BroadlinkDevice, kind: CaptureKind) -> None:
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
        if _signal_kind(signal) is not self._kind:
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


class BroadlinkInfraredReceiverEntity(BroadlinkCaptureEntity, InfraredReceiverEntity):
    """Publishes captured infrared codes on the infrared platform.

    The device only listens while a capture window is open, so the
    capturing attribute says whether it is listening right now.
    """

    _attr_translation_key = "infrared_receiver"

    def __init__(self, device: BroadlinkDevice) -> None:
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
            ATTR_WINDOW_ENDS_AT: _window_ends_at(self._device) if capturing else None,
        }

    @callback
    def _on_signal(self, signal: CapturedSignal, kind: CaptureKind) -> None:
        """Publish an infrared code; radio frequency codes are not infrared."""
        if _signal_kind(signal) is not CaptureKind.IR:
            return
        self._handle_received_signal(
            InfraredReceivedSignal(timings=broadlink_pulses_to_timings(signal.pulses))
        )


def _signal_kind(signal: CapturedSignal) -> CaptureKind:
    """Return the band a captured signal belongs to."""
    return CaptureKind.IR if signal.kind is SignalKind.IR else CaptureKind.RF
