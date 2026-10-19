"""A Broadlink remote set up in the built-in Broadlink integration."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging

import broadlink_async as blk
from broadlink_async.exceptions import (
    AuthorizationError,
    BroadlinkException,
    ConnectionClosedError,
    EndpointClosedError,
    NetworkTimeoutError,
)

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_TIMEOUT,
    CONF_TYPE,
    STATE_UNAVAILABLE,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.event import async_track_state_change_event

from .capture import CaptureManager, CaptureKind
from .const import BROADLINK_DOMAIN, DEFAULT_PORT, REMOTE_TYPES, RF_TYPES

_LOGGER = logging.getLogger(__name__)


def remote_type(core_entry: ConfigEntry) -> str | None:
    """Return the model family of a Broadlink entry, if it is a remote."""
    product_id = core_entry.data.get(CONF_TYPE)
    for device_class, products in blk.SUPPORTED_TYPES.items():
        if product_id in products:
            device_type = getattr(device_class, "TYPE", None)
            return device_type if device_type in REMOTE_TYPES else None
    return None


def remote_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return the Broadlink entries for remotes, which can capture."""
    return [
        entry
        for entry in hass.config_entries.async_entries(
            BROADLINK_DOMAIN, include_ignore=False, include_disabled=False
        )
        if remote_type(entry) is not None
    ]


def find_device(hass: HomeAssistant, core_entry: ConfigEntry) -> dr.DeviceEntry | None:
    """Return the device the built-in integration registered for an entry."""
    return dr.async_get(hass).async_get_device_by_connection(
        (dr.CONNECTION_NETWORK_MAC, dr.format_mac(core_entry.data[CONF_MAC])),
        core_entry.entry_id,
    )


class BroadlinkRemote:
    """A remote from the built-in integration, with its own capture session.

    The built-in integration owns the device and its connection. This keeps
    a second session on the device for capture windows and reads everything
    else from the built-in config entry and device.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        core_entry: ConfigEntry,
        core_device_id: str,
    ) -> None:
        """Initialize from the built-in config entry."""
        self.hass = hass
        self.entry = entry
        self.core_entry = core_entry
        self.core_device_id = core_device_id
        self.api = blk.gendevice(
            core_entry.data[CONF_TYPE],
            (core_entry.data[CONF_HOST], DEFAULT_PORT),
            bytes.fromhex(core_entry.data[CONF_MAC]),
            name=core_entry.title,
        )
        self.api.timeout = core_entry.data[CONF_TIMEOUT]
        self.capture = CaptureManager(self)
        self._watch_requests()
        self._authorized = False
        self._auth_lock = asyncio.Lock()
        # The built-in entities followed for availability, and whether
        # they last said the device answers.
        self._followed: tuple[str, ...] = ()
        self._answering = True
        self._availability_listeners: list[CALLBACK_TYPE] = []
        self._unsub_states: CALLBACK_TYPE | None = None
        self._unsub_registry: CALLBACK_TYPE | None = None

    @property
    def name(self) -> str:
        """Return the name the device has in the built-in integration."""
        device = dr.async_get(self.hass).async_get(self.core_device_id)
        if device is not None and (name := device.name_by_user or device.name):
            return name
        return self.core_entry.title

    @property
    def area_name(self) -> str | None:
        """Return the area the device has in the built-in integration."""
        device = dr.async_get(self.hass).async_get(self.core_device_id)
        if device is None or device.area_id is None:
            return None
        area = ar.async_get(self.hass).async_get_area(device.area_id)
        return area.name if area is not None else None

    @property
    def unique_id(self) -> str:
        """Return the id the built-in integration uses, the MAC in hex."""
        return self.core_entry.unique_id or self.core_entry.data[CONF_MAC]

    @property
    def mac_address(self) -> str:
        """Return the MAC address in hex."""
        return self.core_entry.data[CONF_MAC]

    @property
    def configured_timeout(self) -> float:
        """Return the request timeout set in the built-in integration."""
        return self.core_entry.data[CONF_TIMEOUT]

    @property
    def available(self) -> bool:
        """Return True while the device answers the built-in integration.

        The built-in entry stays loaded when the device stops answering;
        only its entities go unavailable. So the device counts as available
        while the entry is loaded and its entities are not all unavailable.
        """
        return self.core_entry.state is ConfigEntryState.LOADED and self._answering

    @callback
    def async_start(self) -> None:
        """Follow the built-in entities on this device."""
        self._unsub_registry = self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry_updated
        )
        self._follow()

    @callback
    def async_stop_following(self) -> None:
        """Stop following the built-in entities."""
        if self._unsub_registry is not None:
            self._unsub_registry()
            self._unsub_registry = None
        if self._unsub_states is not None:
            self._unsub_states()
            self._unsub_states = None

    @callback
    def async_add_availability_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call listener when the device starts or stops answering."""
        self._availability_listeners.append(listener)

        @callback
        def remove() -> None:
            with suppress(ValueError):
                self._availability_listeners.remove(listener)

        return remove

    def _followed_entities(self) -> tuple[str, ...]:
        """Return the built-in integration's enabled entities on the device.

        All of them share one availability in the built-in integration,
        which polls the device; reading their states costs no traffic. A
        disabled entity has no state, so it is left out.
        """
        registry = er.async_get(self.hass)
        return tuple(
            sorted(
                entry.entity_id
                for entry in er.async_entries_for_device(registry, self.core_device_id)
                if entry.config_entry_id == self.core_entry.entry_id
                and not entry.disabled
            )
        )

    @callback
    def _follow(self) -> None:
        """Track the states of the followed entities and evaluate them."""
        followed = self._followed_entities()
        if followed != self._followed or self._unsub_states is None:
            if self._unsub_states is not None:
                self._unsub_states()
            self._followed = followed
            self._unsub_states = async_track_state_change_event(
                self.hass, list(followed), self._on_state_changed
            )
        self._evaluate()

    @callback
    def _on_registry_updated(
        self, event: Event[er.EventEntityRegistryUpdatedData]
    ) -> None:
        """Pick up built-in entities renamed, enabled, disabled or removed."""
        self._follow()

    @callback
    def _on_state_changed(self, event: Event) -> None:
        """Re-evaluate when a followed entity changes state."""
        self._evaluate()

    @callback
    def _evaluate(self) -> None:
        """Decide whether the device answers, and act on a change.

        With no followed entity, or none with a state yet, this falls back
        to the built-in entry being loaded, as before.
        """
        states = [
            state
            for entity_id in self._followed
            if (state := self.hass.states.get(entity_id)) is not None
        ]
        answering = not states or any(
            state.state != STATE_UNAVAILABLE for state in states
        )
        if answering == self._answering:
            return
        self._answering = answering
        if answering:
            # A Broadlink forgets its session when it loses power, so the
            # session from before is stale.
            self._authorized = False
            _LOGGER.debug("%s answers again", self.name)
        else:
            _LOGGER.debug("%s stopped answering the built-in integration", self.name)
        if not answering and self.capture.kind is not None:
            self.entry.async_create_background_task(
                self.hass,
                self.capture.async_fail("the remote is unavailable"),
                name=f"broadsniff close window {self.name}",
            )
        for listener in tuple(self._availability_listeners):
            listener()

    def _watch_requests(self) -> None:
        """Tell the capture manager how each request to the device went.

        The library tolerates a few missed polls in a window and then ends
        it on the window's own deadline without an error, so a remote that
        stops answering mid-window would otherwise look like a quiet
        window. Every request of a window goes through send_packet on this
        session, which BroadSniff uses for capture alone.
        """
        send_packet = self.api.send_packet

        async def watched(packet_type: int, payload: bytes | bytearray) -> bytes:
            try:
                response = await send_packet(packet_type, payload)
            except (NetworkTimeoutError, OSError) as err:
                self.capture.request_failed(err)
                raise
            self.capture.request_answered()
            return response

        self.api.send_packet = watched

    async def async_probe(self) -> None:
        """Make one real request, at the configured timeout.

        A session the device has forgotten, after a power loss, is opened
        again once and the request retried. Raises what the device or the
        network raised.
        """
        await self.async_ensure_auth()
        try:
            await self.api.update()
        except (AuthorizationError, ConnectionClosedError) as err:
            if isinstance(err, EndpointClosedError):
                raise
            _LOGGER.debug("%s refused the session, opening a new one", self.name)
            async with self._auth_lock:
                self._authorized = False
                await self.api.auth()
                self._authorized = True
            await self.api.update()

    @property
    def supports_rf(self) -> bool:
        """Return True if the remote also captures radio frequency."""
        return self.api.type in RF_TYPES

    @property
    def capture_kinds(self) -> list[CaptureKind]:
        """Return the bands the remote captures on."""
        if self.supports_rf:
            return [CaptureKind.IR, CaptureKind.RF]
        return [CaptureKind.IR]

    async def async_ensure_auth(self) -> None:
        """Open the capture session if it is not open yet."""
        async with self._auth_lock:
            if not self._authorized:
                await self.api.auth()
                self._authorized = True

    async def async_auth(self) -> bool:
        """Open a new capture session after the device rejected the old one."""
        async with self._auth_lock:
            self._authorized = False
            try:
                await self.api.auth()
            except (BroadlinkException, OSError) as err:
                _LOGGER.debug(
                    "Failed to authenticate to the device at %s: %s",
                    self.api.host[0],
                    err,
                )
                return False
            self._authorized = True
            return True

    async def async_unload(self) -> None:
        """Close any open window and the capture session."""
        self.async_stop_following()
        await self.capture.async_stop()
        await self.api.aclose()
