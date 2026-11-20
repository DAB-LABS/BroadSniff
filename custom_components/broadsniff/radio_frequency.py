"""Radio frequency receivers for Broadlink remotes that learn RF codes.

Home Assistant has radio frequency receivers from 2026.11; BroadSniff only
sets this platform up when they exist (see const.HAS_RF_RECEIVER).
"""

from __future__ import annotations

from typing import Any, override

from broadlink_async.remote import CapturedSignal

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.radio_frequency import (
    RadioFrequencyReceivedSignal,
    RadioFrequencyReceiverEntity,
)
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BroadSniffConfigEntry
from .capture import (
    RF_PRESET_CARRIERS,
    CaptureKind,
    CaptureManager,
    RFFrequency,
    signal_kind,
)
from .const import RF_FREQUENCY_RANGES
from .device import BroadlinkRemote
from .entity import BroadSniffCaptureEntity
from .infrared import (
    ATTR_CAPTURE_MODE,
    ATTR_CAPTURE_SWITCH,
    ATTR_CAPTURING,
    capture_switch_entity_id,
    is_capture_entity_event,
    setting_entity_id,
    window_setting_declarations,
)
from .switch import ATTR_WINDOW_ENDS_AT, window_ends_at

PARALLEL_UPDATES = 0

# The windowed-receiver contract HAIR reads also lets an RF window say what
# it is doing (sweeping for the carrier while a button is held, or
# listening for a code) and which carrier it listens on.
ATTR_CAPTURE_PHASE = "capture_phase"
ATTR_FREQUENCY_HZ = "frequency_hz"
# The RF frequency select, and what each of its options means: a carrier
# in Hz, or "sweep".
ATTR_FREQUENCY_SELECT = "frequency_select"
ATTR_FREQUENCY_OPTIONS = "frequency_options"
SWEEP = "sweep"

# The translation keys the RF settings entities build their unique IDs from
# (see select.py and number.py).
RF_FREQUENCY_KEY = "rf_frequency"
RF_CUSTOM_FREQUENCY_KEY = "rf_custom_frequency"


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


def frequency_options(capture: CaptureManager, *, custom: bool) -> dict[str, int | str]:
    """Return what each RF frequency option means, in the select's order.

    A preset is its carrier in Hz and sweep is "sweep". Custom is the
    carrier on the custom frequency number, and is left out when that
    number is disabled, since nothing would set its carrier.
    """
    options: dict[str, int | str] = {}
    for choice in RFFrequency:
        if choice is RFFrequency.SWEEP:
            options[choice.value] = SWEEP
        elif choice is RFFrequency.CUSTOM:
            if custom:
                options[choice.value] = round(capture.rf_custom_frequency * 1_000_000)
        else:
            options[choice.value] = round(RF_PRESET_CARRIERS[choice] * 1_000_000)
    return options


class BroadlinkRadioFrequencyReceiverEntity(
    BroadSniffCaptureEntity, RadioFrequencyReceiverEntity
):
    """Publishes captured RF codes on the radio frequency platform.

    The device only listens while an RF capture window is open, so the
    capturing attribute says whether it is listening right now. The state
    is written again whenever a window opens, closes, finds its carrier or
    moves its deadline, the capture mode or custom carrier changes, or the
    RF capture switch or a window setting it declares is renamed, disabled,
    enabled, removed or added.
    """

    _attr_translation_key = "rf_receiver"

    def __init__(self, device: BroadlinkRemote) -> None:
        """Initialize the entity."""
        super().__init__(device)
        self._attr_unique_id = f"{device.unique_id}-rf_receiver"
        # The entities last declared, to tell a change that matters from
        # registry traffic that does not.
        self._declared: dict[str, Any] = {}

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
                event_filter=is_capture_entity_event,
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
        self._declared = self._declarations()
        return attributes | self._declared

    def _declarations(self) -> dict[str, Any]:
        """Return the capture switch and the window settings, by attribute."""
        hass, device = self.hass, self._device
        declared: dict[str, Any] = {}
        if switch := capture_switch_entity_id(hass, device, CaptureKind.RF):
            declared[ATTR_CAPTURE_SWITCH] = switch
        declared |= window_setting_declarations(hass, device)
        if select := setting_entity_id(hass, device, SELECT_DOMAIN, RF_FREQUENCY_KEY):
            custom = setting_entity_id(
                hass, device, NUMBER_DOMAIN, RF_CUSTOM_FREQUENCY_KEY
            )
            declared[ATTR_FREQUENCY_SELECT] = select
            declared[ATTR_FREQUENCY_OPTIONS] = frequency_options(
                device.capture, custom=custom is not None
            )
        return declared

    @callback
    def _on_registry_updated(
        self, event: Event[er.EventEntityRegistryUpdatedData]
    ) -> None:
        """Write the state again if an entity the receiver names changed."""
        if self._declarations() != self._declared:
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
