"""Infrared receivers for Broadlink remotes."""

from __future__ import annotations

from typing import Any, override

from broadlink_async.remote import CapturedSignal

from homeassistant.components.infrared import (
    InfraredReceivedSignal,
    InfraredReceiverEntity,
)
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BroadSniffConfigEntry
from .capture import CaptureKind, signal_kind
from .const import DOMAIN
from .device import BroadlinkRemote
from .entity import BroadSniffCaptureEntity
from .switch import ATTR_WINDOW_ENDS_AT, capture_switch_unique_id, window_ends_at

PARALLEL_UPDATES = 0

# The receiver's attributes follow the windowed-receiver contract that
# HAIR reads: capturing says the receiver captures on demand and whether a
# window is open; capture_switch names what opens one; window_ends_at says
# when the open window closes; capture_mode is the mode the next window
# opens in.
ATTR_CAPTURING = "capturing"
ATTR_CAPTURE_SWITCH = "capture_switch"
ATTR_CAPTURE_MODE = "capture_mode"
# The entities that set the next window, so HAIR can offer them as pickers
# on the receiver: window_length is the window number, capture_mode_select
# the capture mode select.
ATTR_WINDOW_LENGTH = "window_length"
ATTR_CAPTURE_MODE_SELECT = "capture_mode_select"

# The translation keys the settings entities build their unique IDs from
# (see number.py and select.py).
CAPTURE_WINDOW_KEY = "capture_window"
CAPTURE_MODE_KEY = "capture_mode"

# The domains of the entities a receiver names.
_CAPTURE_ENTITY_DOMAINS = (SWITCH_DOMAIN, SELECT_DOMAIN, NUMBER_DOMAIN)


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


def capture_switch_entity_id(
    hass: HomeAssistant, device: BroadlinkRemote, kind: CaptureKind = CaptureKind.IR
) -> str | None:
    """Return the entity ID of the remote's capture switch for a band, if usable.

    Looked up by unique ID every time, so a renamed switch is never named
    by its old ID. A disabled switch has no state and cannot be turned on,
    so it is not offered.
    """
    return _usable_entity_id(
        hass, SWITCH_DOMAIN, capture_switch_unique_id(device, kind)
    )


def setting_entity_id(
    hass: HomeAssistant, device: BroadlinkRemote, domain: str, key: str
) -> str | None:
    """Return the entity ID of one of the remote's window settings, if usable.

    Looked up by unique ID every time, like the capture switch. A disabled
    setting has no state to read or set, so it is not named.
    """
    return _usable_entity_id(hass, domain, f"{device.unique_id}-{key}")


def _usable_entity_id(hass: HomeAssistant, domain: str, unique_id: str) -> str | None:
    """Return the entity ID for a unique ID, unless it is missing or disabled."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
    if entity_id is None:
        return None
    entry = registry.async_get(entity_id)
    if entry is None or entry.disabled:
        return None
    return entity_id


def window_setting_declarations(
    hass: HomeAssistant, device: BroadlinkRemote
) -> dict[str, str]:
    """Return the window settings every receiver declares, by attribute.

    The window number and the capture mode select apply to IR and RF
    windows alike. A setting that is disabled is left out.
    """
    declared: dict[str, str] = {}
    if window := setting_entity_id(hass, device, NUMBER_DOMAIN, CAPTURE_WINDOW_KEY):
        declared[ATTR_WINDOW_LENGTH] = window
    if mode := setting_entity_id(hass, device, SELECT_DOMAIN, CAPTURE_MODE_KEY):
        declared[ATTR_CAPTURE_MODE_SELECT] = mode
    return declared


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
    capturing attribute says whether it is listening right now. The
    state is written again whenever a window opens, closes or moves its
    deadline, the capture mode changes, or the IR capture switch or a
    window setting it declares is renamed, disabled, enabled, removed or
    added.
    """

    _attr_translation_key = "infrared_receiver"

    def __init__(self, device: BroadlinkRemote) -> None:
        """Initialize the entity."""
        super().__init__(device)
        self._attr_unique_id = f"{device.unique_id}-receiver"
        # The entities last declared, to tell a change that matters from
        # registry traffic that does not.
        self._declared: dict[str, Any] = {}

    @override
    async def async_added_to_hass(self) -> None:
        """Listen for captured signals and for changes to the declared entities."""
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
        """Return the windowed-receiver attributes."""
        capturing = self._device.capture.kind is CaptureKind.IR
        attributes: dict[str, Any] = {
            ATTR_CAPTURING: capturing,
            ATTR_WINDOW_ENDS_AT: window_ends_at(self._device) if capturing else None,
            ATTR_CAPTURE_MODE: self._device.capture.mode.value,
        }
        self._declared = self._declarations()
        return attributes | self._declared

    def _declarations(self) -> dict[str, Any]:
        """Return the capture switch and the window settings, by attribute."""
        declared: dict[str, Any] = {}
        if switch := capture_switch_entity_id(self.hass, self._device):
            declared[ATTR_CAPTURE_SWITCH] = switch
        return declared | window_setting_declarations(self.hass, self._device)

    @callback
    def _on_registry_updated(
        self, event: Event[er.EventEntityRegistryUpdatedData]
    ) -> None:
        """Write the state again if an entity the receiver names changed."""
        if self._declarations() != self._declared:
            self.async_write_ha_state()

    @callback
    def _on_signal(self, signal: CapturedSignal, kind: CaptureKind) -> None:
        """Publish an infrared code; radio frequency codes are not infrared."""
        if signal_kind(signal) is not CaptureKind.IR:
            return
        self._handle_received_signal(
            InfraredReceivedSignal(timings=broadlink_pulses_to_timings(signal.pulses))
        )


@callback
def is_capture_entity_event(event_data: er.EventEntityRegistryUpdatedData) -> bool:
    """Return True for registry changes to a switch, select or number.

    Those are the domains a receiver names: its capture switch and its
    window settings. Renames count too.
    """
    entity_ids = (event_data["entity_id"], event_data.get("old_entity_id") or "")
    prefixes = tuple(f"{domain}." for domain in _CAPTURE_ENTITY_DOMAINS)
    return any(entity_id.startswith(prefixes) for entity_id in entity_ids)
