"""Capture windows on Broadlink universal remotes.

A device has one receiver, so it runs at most one capture window at a time,
infrared or radio frequency. The library owns the window (arming, polling,
re-arming, closing after one code or on a sliding deadline, and taking the device out of learning
mode when it closes); this runs it as a background task, keeps the settings
the entities change, and tells listeners about signals and windows.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import logging
from typing import TYPE_CHECKING

from broadlink_async.exceptions import (
    AuthorizationError,
    BroadlinkException,
    ConnectionClosedError,
    EndpointClosedError,
)
from broadlink_async.remote import CapturedSignal, SignalKind

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import DEFAULT_CAPTURE_WINDOW, DEFAULT_RF_CUSTOM_FREQUENCY, DOMAIN

if TYPE_CHECKING:
    from .device import BroadlinkRemote

_LOGGER = logging.getLogger(__name__)


class CaptureKind(StrEnum):
    """The band a capture window listens on."""

    IR = "ir"
    RF = "rf"


class CaptureMode(StrEnum):
    """How many codes a capture window takes and when it closes."""

    # Close after the first code.
    SINGLE = "single"
    # Take every code until the window runs out.
    MULTIPLE = "multiple"
    # Take every code; each one restarts the countdown.
    SLIDING = "sliding"


DEFAULT_CAPTURE_MODE = CaptureMode.SLIDING


class RFFrequency(StrEnum):
    """The carrier an RF capture window listens on."""

    MHZ_433_92 = "mhz_433_92"
    MHZ_315 = "mhz_315"
    # The remote finds the carrier while a button is held.
    SWEEP = "sweep"
    # The carrier set on the custom frequency entity.
    CUSTOM = "custom"


# While a window is open the capture session gives each request this long,
# so an unplugged remote shows within seconds whatever the window length.
# A local Broadlink answers in well under a tenth of a second.
WINDOW_REQUEST_TIMEOUT = 1.5
# Consecutive unanswered requests that end a window as a failure. The
# library forgives two missed polls and then lets the window run out its
# own deadline without an error, so BroadSniff counts for itself.
MAX_MISSED_REQUESTS = 2
# Why a window ended when the remote went quiet under it.
STOPPED_ANSWERING = "the remote stopped answering"


class RemoteStoppedAnswering(Exception):
    """Raised into a window when the remote has stopped answering it."""


DEFAULT_RF_FREQUENCY = RFFrequency.MHZ_433_92
RF_PRESET_CARRIERS = {RFFrequency.MHZ_433_92: 433.92, RFFrequency.MHZ_315: 315.0}


@dataclass(frozen=True, slots=True)
class CaptureWindowResult:
    """How a capture window ended."""

    kind: CaptureKind
    signals: int
    stopped: bool


type SignalListener = Callable[[CapturedSignal, CaptureKind], None]
type EndedListener = Callable[[CaptureWindowResult], None]


class CaptureManager:
    """Runs capture windows on one device and fans out what they catch."""

    def __init__(self, device: BroadlinkRemote) -> None:
        """Initialize with the default settings."""
        self._device = device
        self.window: float = DEFAULT_CAPTURE_WINDOW
        self._mode: CaptureMode = DEFAULT_CAPTURE_MODE
        self.rf_frequency: RFFrequency = DEFAULT_RF_FREQUENCY
        self.rf_custom_frequency: float = DEFAULT_RF_CUSTOM_FREQUENCY
        self._task: asyncio.Task[int] | None = None
        # Codes caught by the open window, or by the last one once it closes.
        self.codes: int = 0
        self._kind: CaptureKind | None = None
        self._ends_at: datetime | None = None
        # Unanswered requests in a row in the open window, and why the open
        # window is being ended as a failure, if it is.
        self._misses = 0
        self._failure: str | None = None
        self._signal_listeners: list[SignalListener] = []
        self._state_listeners: list[CALLBACK_TYPE] = []
        self._ended_listeners: list[EndedListener] = []

    @property
    def mode(self) -> CaptureMode:
        """Return the mode the next window opens in."""
        return self._mode

    @mode.setter
    def mode(self, mode: CaptureMode) -> None:
        """Set the mode for the next window and tell the state listeners.

        The receiver reports the mode, so a change has to reach it even
        when no window is open.
        """
        if mode is self._mode:
            return
        self._mode = mode
        self._notify_state()

    @property
    def rf_carrier(self) -> float | None:
        """Return the carrier to listen on in MHz, or None to sweep for it."""
        if self.rf_frequency is RFFrequency.SWEEP:
            return None
        if self.rf_frequency is RFFrequency.CUSTOM:
            return self.rf_custom_frequency
        return RF_PRESET_CARRIERS[self.rf_frequency]

    @property
    def kind(self) -> CaptureKind | None:
        """Return the band of the open window, or None if none is open."""
        return self._kind

    @property
    def ends_at(self) -> datetime | None:
        """Return when the open window closes, if it has a deadline."""
        return self._ends_at

    @callback
    def async_add_signal_listener(self, listener: SignalListener) -> CALLBACK_TYPE:
        """Call listener with every signal any window catches."""
        return _add(self._signal_listeners, listener)

    @callback
    def async_add_state_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call listener when a window opens, closes or moves its deadline, or the mode changes."""
        return _add(self._state_listeners, listener)

    @callback
    def async_add_ended_listener(self, listener: EndedListener) -> CALLBACK_TYPE:
        """Call listener when a window closes, with how it ended."""
        return _add(self._ended_listeners, listener)

    async def async_start(
        self,
        kind: CaptureKind,
        *,
        window: float | None = None,
        mode: CaptureMode | None = None,
        on_signal: Callable[[CapturedSignal], None] | None = None,
    ) -> asyncio.Task[int]:
        """Open a capture window, closing any window already open.

        Nothing is published until the remote has answered one request, so
        a remote that does not answer opens no window and the caller gets
        HomeAssistantError. Settings not given come from the entities. The
        returned task finishes when the window closes, with the number of
        signals caught; a window the remote stops answering is ended,
        logged once and reported to the ended listeners.
        """
        await self.async_stop()
        if not self._device.available:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="capture_failed",
                translation_placeholders={
                    "error": f"{self._device.name} is not answering"
                },
            )
        try:
            await self._device.async_probe()
        except (BroadlinkException, OSError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="capture_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        window = self.window if window is None else window
        mode = self.mode if mode is None else mode
        stop_after_first = mode is CaptureMode.SINGLE
        sliding = mode is CaptureMode.SLIDING
        api = self._device.api
        signals: AsyncGenerator[CapturedSignal]
        if kind is CaptureKind.RF:
            signals = api.capture_rf(
                window,
                frequency=self.rf_carrier,
                stop_after_first=stop_after_first,
                extend_on_signal=sliding,
            )
        else:
            signals = api.capture(
                window, stop_after_first=stop_after_first, extend_on_signal=sliding
            )
        self._kind = kind
        self._ends_at = _deadline(window)
        self.codes = 0
        self._misses = 0
        self._failure = None
        self._device.api.timeout = min(
            self._device.configured_timeout, WINDOW_REQUEST_TIMEOUT
        )
        self._notify_state()
        task = self._device.entry.async_create_background_task(
            self._device.hass,
            self._async_run(signals, kind, window, sliding, on_signal),
            name=f"broadsniff {kind} window {self._device.name}",
        )
        self._task = task
        return task

    async def async_stop(self) -> None:
        """Close the open window, if any."""
        if (task := self._task) is None:
            return
        self._task = None
        if task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError, HomeAssistantError):
            await task

    async def async_fail(self, reason: str) -> None:
        """End the open window as a failure, if one is open."""
        if self._task is None or self._task.done():
            return
        self._failure = reason
        await self.async_stop()

    @callback
    def request_answered(self) -> None:
        """Note that the remote answered a request."""
        self._misses = 0

    def request_failed(self, err: Exception) -> None:
        """Note an unanswered request; end the window after too many.

        Raised into the window's own request, so the library's generator
        unwinds at once instead of polling on to its deadline. Outside a
        window (the probe) the caller handles the error itself.
        """
        if self._task is None or self._failure is not None:
            return
        self._misses += 1
        if self._misses >= MAX_MISSED_REQUESTS:
            self._failure = STOPPED_ANSWERING
            raise RemoteStoppedAnswering(STOPPED_ANSWERING) from err

    async def _async_run(
        self,
        signals: AsyncGenerator[CapturedSignal],
        kind: CaptureKind,
        window: float,
        sliding: bool,
        on_signal: Callable[[CapturedSignal], None] | None,
    ) -> int:
        """Consume one window and publish what it catches.

        A window can end four ways: on its own (deadline, or the first code
        in single mode), stopped by a caller, failed by the device, or
        failed because the remote went unavailable. A failed window is
        logged once as a warning and does not raise.
        """
        caught = 0
        stopped = False
        failure: str | None = None
        try:
            async with aclosing(signals):
                async for signal in signals:
                    caught += 1
                    self.codes = caught
                    if sliding:
                        self._ends_at = _deadline(window)
                    self._notify_state()
                    self._publish(signal, kind, on_signal)
        except asyncio.CancelledError:
            if self._failure is None:
                stopped = True
            raise
        except RemoteStoppedAnswering:
            pass
        except (BroadlinkException, OSError) as err:
            # A rejected session starts the reauth flow, except when the
            # device was closed on purpose, as with any other request.
            if isinstance(
                err, (AuthorizationError, ConnectionClosedError)
            ) and not isinstance(err, EndpointClosedError):
                await self._device.async_auth()
            failure = str(err)
        finally:
            if failure is None and not stopped:
                failure = self._failure
            if failure is None and not stopped and self._misses:
                # The window ran out its deadline while the remote was not
                # answering, which the library reports as a quiet window.
                failure = STOPPED_ANSWERING
            self._device.api.timeout = self._device.configured_timeout
            if self._task is asyncio.current_task():
                self._task = None
            self._kind = None
            self._ends_at = None
            self._misses = 0
            self._failure = None
            self._notify_state()
            if failure is not None:
                _LOGGER.warning(
                    "Capture window on %s failed: %s", self._device.name, failure
                )
            result = CaptureWindowResult(kind=kind, signals=caught, stopped=stopped)
            for listener in tuple(self._ended_listeners):
                _call(listener, result)
        return caught

    @callback
    def _publish(
        self,
        signal: CapturedSignal,
        kind: CaptureKind,
        on_signal: Callable[[CapturedSignal], None] | None,
    ) -> None:
        """Hand a signal to the window's caller and to every listener."""
        if on_signal is not None:
            on_signal(signal)
        for listener in tuple(self._signal_listeners):
            _call(listener, signal, kind)

    @callback
    def _notify_state(self) -> None:
        """Tell the state listeners that the window changed."""
        for listener in tuple(self._state_listeners):
            _call(listener)


def signal_kind(signal: CapturedSignal) -> CaptureKind:
    """Return the band a captured signal belongs to."""
    return CaptureKind.IR if signal.kind is SignalKind.IR else CaptureKind.RF


def _deadline(window: float) -> datetime | None:
    """Return when a window of this length opened now would close."""
    if window <= 0:
        return None
    return dt_util.utcnow() + timedelta(seconds=window)


def _add[_T](listeners: list[_T], listener: _T) -> CALLBACK_TYPE:
    """Add a listener and return the function that removes it."""
    listeners.append(listener)

    @callback
    def remove() -> None:
        with suppress(ValueError):
            listeners.remove(listener)

    return remove


def _call(listener: Callable[..., None], *args: object) -> None:
    """Call a listener, logging instead of raising if it fails."""
    try:
        listener(*args)
    except Exception:
        _LOGGER.exception("Error in a BroadSniff capture listener")
