"""RF capture windows that sweep for the remote's carrier first."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from broadlink_async.exceptions import NetworkTimeoutError
from broadlink_async.remote import CapturedSignal, SignalKind, pulses_to_data
import pytest

from custom_components.broadsniff.capture import (
    NO_LOCK,
    CaptureManager,
    CapturePhase,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, callback

from . import GARAGE, FakeApis, FakeWindow, add_remote, setup_capture, wait_for

SWITCH = "switch.garage_rf_capture"
RF_PACKET = pulses_to_data([350, 1050, 350, 10500, 350], kind=SignalKind.RF_433)


@pytest.fixture(autouse=True)
def _broadlink(broadlink: None) -> None:
    """Use the built-in integration stub in every test."""


async def _setup(hass: HomeAssistant, fake_apis: FakeApis) -> FakeWindow:
    await add_remote(hass, GARAGE)
    entry = await setup_capture(hass)
    window = fake_apis.windows[GARAGE.mac]
    window.manager = entry.runtime_data.remotes[
        next(iter(entry.runtime_data.remotes))
    ].capture
    await _select(hass, "select.garage_rf_frequency", "sweep")
    return window


async def _call(hass: HomeAssistant, domain: str, service: str, **data: Any) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await hass.async_block_till_done()


async def _select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    await _call(
        hass,
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        entity_id=entity_id,
        **{ATTR_OPTION: option},
    )


def _ended(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Collect the RF capture event's window_ended data as it fires."""
    ended: list[dict[str, Any]] = []

    @callback
    def capture(event: Event) -> None:
        if event.data["entity_id"] == "event.garage_rf_capture":
            attrs = event.data["new_state"].attributes
            if attrs.get("event_type") == "window_ended":
                ended.append(dict(attrs))

    hass.bus.async_listen("state_changed", capture)
    return ended


def _manager(window: FakeWindow) -> CaptureManager:
    return window.manager


async def test_sweep_then_listen(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """The window says it is sweeping, then listening on the carrier found."""
    window = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    window.locks = [(False, 0.0), (False, 0.0), (True, 433.84)]

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)

    manager = _manager(window)
    assert manager.phase is CapturePhase.SWEEPING
    assert manager.frequency_hz is None
    api.sweep_frequency.assert_awaited_once()

    await wait_for(hass, lambda: window.calls)

    assert manager.phase is CapturePhase.LISTENING
    assert manager.frequency_hz == 433_840_000
    assert api.check_frequency.await_count == 3
    name, _, kwargs = window.calls[-1]
    assert name == "capture_rf"
    assert kwargs["frequency"] == 433.84
    assert hass.states.get(SWITCH).state == STATE_ON

    window.send(CapturedSignal.from_packet(RF_PACKET, 433.84))
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=SWITCH)
    assert manager.phase is None
    assert manager.frequency_hz is None
    # Once capture_rf is open, closing it cancels learning, not the sweep.
    api.cancel_sweep_frequency.assert_not_awaited()


async def test_sliding_window_restarts_at_lock(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """A sliding window gets its full length once the carrier locks."""
    window = await _setup(hass, fake_apis)

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)
    await wait_for(hass, lambda: window.calls)

    _, (seconds,), kwargs = window.calls[-1]
    assert seconds == 10.0
    assert kwargs["extend_on_signal"] is True
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=SWITCH)


@pytest.mark.parametrize("mode", ["single", "multiple"])
async def test_other_windows_keep_their_deadline(
    hass: HomeAssistant, fake_apis: FakeApis, mode: str
) -> None:
    """Single and multiple windows count the sweep against their length."""
    window = await _setup(hass, fake_apis)
    await _select(hass, "select.garage_capture_mode", mode)
    window.locks = [(False, 0.0), (True, 433.92)]

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)
    await wait_for(hass, lambda: window.calls)

    _, (seconds,), _ = window.calls[-1]
    assert 0 < seconds < 10.0
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=SWITCH)


async def test_stop_during_the_sweep(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """Turning the switch off mid-sweep cancels the sweep and ends cleanly."""
    window = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    window.locks = [(False, 0.0)]
    ended = _ended(hass)

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)
    await wait_for(hass, lambda: api.check_frequency.await_count >= 2)
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=SWITCH)

    api.cancel_sweep_frequency.assert_awaited_once()
    assert window.calls == []
    assert hass.states.get(SWITCH).state == STATE_OFF
    assert _manager(window).phase is None
    assert ended[-1]["stopped"] is True
    assert "reason" not in ended[-1]


async def test_sweep_that_never_locks(
    hass: HomeAssistant, fake_apis: FakeApis, caplog: pytest.LogCaptureFixture
) -> None:
    """A sweep that runs out of time ends the window, saying why, without a warning."""
    window = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    window.locks = [(False, 0.0)]
    ended = _ended(hass)
    await _call(
        hass, "number", "set_value", entity_id="number.garage_capture_window", value=1
    )

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)
    await wait_for(hass, lambda: hass.states.get(SWITCH).state == STATE_OFF)

    assert window.calls == []
    api.cancel_sweep_frequency.assert_awaited_once()
    assert ended[-1]["reason"] == NO_LOCK
    assert ended[-1]["stopped"] is False
    assert ended[-1]["signals"] == 0
    assert "found no carrier" in caplog.text
    assert "failed" not in caplog.text


async def test_long_sweep_is_started_again(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """A sweep that runs long is started again, as an open window re-arms."""
    window = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    window.locks = [(False, 0.0)]

    with patch("custom_components.broadsniff.capture.DEFAULT_REARM_INTERVAL", 0.02):
        await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)
        await wait_for(hass, lambda: api.sweep_frequency.await_count >= 3)
        await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=SWITCH)


async def test_remote_stops_answering_during_the_sweep(
    hass: HomeAssistant, fake_apis: FakeApis, caplog: pytest.LogCaptureFixture
) -> None:
    """Unanswered sweep requests end the window as stopped answering."""
    window = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    window.locks = [(False, 0.0)]
    ended = _ended(hass)

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)
    api.raw_send.side_effect = NetworkTimeoutError(-4000, "timed out")
    await wait_for(hass, lambda: hass.states.get(SWITCH).state == STATE_OFF)

    assert window.calls == []
    assert "stopped answering" in caplog.text
    assert "reason" not in ended[-1]


async def test_named_carrier_does_not_sweep(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """A preset carrier goes straight to listening, with no sweep."""
    window = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    await _select(hass, "select.garage_rf_frequency", "mhz_315")

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)

    manager = _manager(window)
    assert manager.phase is CapturePhase.LISTENING
    assert manager.frequency_hz == 315_000_000
    api.sweep_frequency.assert_not_awaited()
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=SWITCH)


async def test_ir_window_has_no_carrier(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """An IR window is listening on no RF carrier."""
    window = await _setup(hass, fake_apis)

    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_ir_capture"
    )

    manager = _manager(window)
    assert manager.phase is CapturePhase.LISTENING
    assert manager.frequency_hz is None
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id="switch.garage_ir_capture"
    )
