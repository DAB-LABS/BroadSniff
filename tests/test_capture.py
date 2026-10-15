"""Tests for capture on Broadlink universal remotes."""

import asyncio
from base64 import b64encode
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

from broadlink_async.exceptions import NetworkTimeoutError
from broadlink_async.remote import CapturedSignal, SignalKind, pulses_to_data
import pytest

from custom_components.broadlink_capture.capture_entities import (
    broadlink_pulses_to_timings,
)
from custom_components.broadlink_capture.const import DOMAIN
from homeassistant.components.infrared import (
    InfraredReceivedSignal,
    async_subscribe_receiver,
)
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.remote import (
    DOMAIN as REMOTE_DOMAIN,
    SERVICE_LEARN_COMMAND,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError

from . import get_device

from pytest_homeassistant_custom_component.common import mock_restore_cache

IR_PULSES = (9000, 4500, 560, 560, 560, 1690, 560, 40000)
IR_PACKET = pulses_to_data(list(IR_PULSES))
# An RM4 pro RF capture: type 0xB1, carrier 433.92 MHz at bytes 4 to 7.
RF_PACKET = bytes.fromhex("b1c00c00009f06000b210d210b210d21")

GARAGE = "Garage"  # RM4 pro, IR and RF
ENTRANCE = "Entrance"  # RM mini 3, IR only


class FakeWindow:
    """Stands in for the library's capture and capture_rf generators."""

    def __init__(self) -> None:
        """Initialize."""
        self.queue: asyncio.Queue[CapturedSignal | None] = asyncio.Queue()
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.closed = 0
        self.error: Exception | None = None

    def factory(self, name: str):
        """Return a replacement for api.capture or api.capture_rf."""

        def start(*args: Any, **kwargs: Any) -> AsyncGenerator[CapturedSignal]:
            self.calls.append((name, args, kwargs))
            return self._run(kwargs.get("stop_after_first", True))

        return start

    async def _run(self, stop_after_first: bool) -> AsyncGenerator[CapturedSignal]:
        try:
            while True:
                if self.error is not None:
                    raise self.error
                signal = await self.queue.get()
                if signal is None:
                    return
                yield signal
                if stop_after_first:
                    return
        finally:
            self.closed += 1

    def send(self, signal: CapturedSignal | None) -> None:
        """Deliver a signal, or close the window with None."""
        self.queue.put_nowait(signal)


async def _setup(hass: HomeAssistant, name: str) -> tuple[MagicMock, FakeWindow]:
    """Set up a device whose capture windows are fake."""
    device = get_device(name)
    window = FakeWindow()
    api = device.get_mock_api()
    api.capture = MagicMock(side_effect=window.factory("capture"))
    api.capture_rf = MagicMock(side_effect=window.factory("capture_rf"))
    await device.setup_entry(hass, mock_api=api)
    return api, window


async def _call(hass: HomeAssistant, domain: str, service: str, **data: Any) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await hass.async_block_till_done()


def _ir() -> CapturedSignal:
    return CapturedSignal.from_packet(IR_PACKET)


def _rf() -> CapturedSignal:
    return CapturedSignal.from_packet(RF_PACKET)


@pytest.mark.parametrize(
    ("name", "rf"),
    [
        pytest.param(GARAGE, True, id="rm4pro"),
        pytest.param(ENTRANCE, False, id="rmmini"),
    ],
)
async def test_capture_entities_created(
    hass: HomeAssistant, name: str, rf: bool
) -> None:
    """Every remote gets the IR capture entities; RF models get the RF ones."""
    await _setup(hass, name)
    slug = name.lower()
    ir_entities = [
        f"switch.{slug}_capture",
        f"switch.{slug}_stop_after_first_signal",
        f"switch.{slug}_sliding_window",
        f"number.{slug}_capture_window",
        f"event.{slug}_ir_capture",
        f"infrared.{slug}_ir_receiver",
    ]
    rf_entities = [
        f"switch.{slug}_rf_capture",
        f"number.{slug}_rf_learn_frequency",
        f"event.{slug}_rf_capture",
    ]
    for entity_id in ir_entities:
        assert hass.states.get(entity_id) is not None, entity_id
    for entity_id in rf_entities:
        assert (hass.states.get(entity_id) is not None) is rf, entity_id


async def test_no_capture_entities_on_non_remote(hass: HomeAssistant) -> None:
    """Devices that are not remotes get no capture entities."""
    device = get_device("Bedroom")
    await device.setup_entry(hass)
    assert not hass.states.async_entity_ids("event")
    assert not hass.states.async_entity_ids("number")


async def test_capture_switch_runs_a_window(hass: HomeAssistant) -> None:
    """The switch opens a window, publishes the code and turns off after it."""
    api, window = await _setup(hass, GARAGE)
    events: list[Event] = []
    hass.bus.async_listen("state_changed", events.append)

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_capture")
    state = hass.states.get("switch.garage_capture")
    assert state.state == STATE_ON
    assert state.attributes["window_ends_at"] is not None
    receiver = hass.states.get("infrared.garage_ir_receiver")
    assert receiver.attributes["capturing"] is True
    assert window.calls == [
        ("capture", (30.0,), {"stop_after_first": True, "extend_on_signal": False})
    ]

    window.send(_ir())
    await hass.async_block_till_done()

    assert hass.states.get("switch.garage_capture").state == STATE_OFF
    assert (
        hass.states.get("infrared.garage_ir_receiver").attributes["capturing"] is False
    )
    event = hass.states.get("event.garage_ir_capture")
    assert event.attributes["event_type"] == "window_ended"
    assert event.attributes["signals"] == 1
    assert event.attributes["stopped"] is False
    signal_events = [
        e.data["new_state"]
        for e in events
        if e.data["entity_id"] == "event.garage_ir_capture"
        and e.data["new_state"].attributes.get("event_type") == "signal"
    ]
    assert len(signal_events) == 1
    assert signal_events[0].attributes["code"] == "b64:" + b64encode(IR_PACKET).decode()
    assert window.closed == 1


async def test_capture_switch_off_stops_the_window(hass: HomeAssistant) -> None:
    """Turning the switch off closes the window early."""
    _, window = await _setup(hass, GARAGE)
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_capture")
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id="switch.garage_capture"
    )

    assert hass.states.get("switch.garage_capture").state == STATE_OFF
    assert window.closed == 1
    event = hass.states.get("event.garage_ir_capture")
    assert event.attributes["event_type"] == "window_ended"
    assert event.attributes["signals"] == 0
    assert event.attributes["stopped"] is True


async def test_settings_reach_the_window(hass: HomeAssistant) -> None:
    """Window length, stop-after-first and sliding come from the entities."""
    _, window = await _setup(hass, GARAGE)
    await _call(
        hass,
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        entity_id="number.garage_capture_window",
        **{ATTR_VALUE: 120},
    )
    await _call(
        hass,
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        entity_id="switch.garage_stop_after_first_signal",
    )
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_sliding_window"
    )
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_capture")

    assert window.calls[-1] == (
        "capture",
        (120.0,),
        {"stop_after_first": False, "extend_on_signal": True},
    )
    first_deadline = hass.states.get("switch.garage_capture").attributes[
        "window_ends_at"
    ]
    window.send(_ir())
    window.send(_ir())
    await hass.async_block_till_done()
    assert hass.states.get("switch.garage_capture").state == STATE_ON
    assert (
        hass.states.get("switch.garage_capture").attributes["window_ends_at"]
        >= first_deadline
    )
    window.send(None)
    await hass.async_block_till_done()
    assert hass.states.get("event.garage_ir_capture").attributes["signals"] == 2


async def test_settings_are_restored(hass: HomeAssistant) -> None:
    """Settings survive a restart."""
    mock_restore_cache(
        hass,
        [
            State("switch.garage_stop_after_first_signal", STATE_OFF),
            State("switch.garage_sliding_window", STATE_ON),
        ],
    )
    _, window = await _setup(hass, GARAGE)
    assert hass.states.get("switch.garage_stop_after_first_signal").state == STATE_OFF
    assert hass.states.get("switch.garage_sliding_window").state == STATE_ON
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_capture")
    assert window.calls[-1][2] == {"stop_after_first": False, "extend_on_signal": True}
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id="switch.garage_capture"
    )


@pytest.mark.parametrize(
    ("frequency", "expected"),
    [pytest.param(0, None, id="sweep"), pytest.param(433.92, 433.92, id="fixed")],
)
async def test_rf_capture(
    hass: HomeAssistant, frequency: float, expected: float | None
) -> None:
    """RF capture uses the learn frequency and reports the packet's carrier."""
    _, window = await _setup(hass, GARAGE)
    await _call(
        hass,
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        entity_id="number.garage_rf_learn_frequency",
        **{ATTR_VALUE: frequency},
    )
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_rf_capture"
    )
    assert hass.states.get("switch.garage_rf_capture").state == STATE_ON
    assert hass.states.get("switch.garage_capture").state == STATE_OFF
    assert window.calls[-1] == (
        "capture_rf",
        (30.0,),
        {"frequency": expected, "stop_after_first": True, "extend_on_signal": False},
    )

    received: list[InfraredReceivedSignal] = []
    async_subscribe_receiver(hass, "infrared.garage_ir_receiver", received.append)
    window.send(_rf())
    await hass.async_block_till_done()

    assert received == []
    assert hass.states.get("infrared.garage_ir_receiver").state == STATE_UNKNOWN
    rf_event = hass.states.get("event.garage_rf_capture")
    assert rf_event.attributes["event_type"] == "window_ended"
    assert hass.states.get("event.garage_ir_capture").state == STATE_UNKNOWN


async def test_rf_event_carries_code_and_carrier(hass: HomeAssistant) -> None:
    """The RF signal event has the code and the carrier from the packet."""
    _, window = await _setup(hass, GARAGE)
    fired: list[dict[str, Any]] = []

    @callback
    def capture(event: Event) -> None:
        if event.data["entity_id"] == "event.garage_rf_capture":
            attrs = event.data["new_state"].attributes
            if attrs.get("event_type") == "signal":
                fired.append(dict(attrs))

    hass.bus.async_listen("state_changed", capture)
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_rf_capture"
    )
    window.send(_rf())
    await hass.async_block_till_done()

    assert fired[0]["code"] == "b64:" + b64encode(RF_PACKET).decode()
    assert fired[0]["frequency_mhz"] == 433.92


async def test_receiver_publishes_ir_timings(hass: HomeAssistant) -> None:
    """Captured IR codes reach infrared platform subscribers as timings."""
    _, window = await _setup(hass, GARAGE)
    received: list[InfraredReceivedSignal] = []
    async_subscribe_receiver(hass, "infrared.garage_ir_receiver", received.append)

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_capture")
    signal = _ir()
    window.send(signal)
    await hass.async_block_till_done()

    assert len(received) == 1
    assert received[0].timings == broadlink_pulses_to_timings(signal.pulses)
    assert hass.states.get("infrared.garage_ir_receiver").state != STATE_UNKNOWN


async def test_invalid_rf_learn_frequency(hass: HomeAssistant) -> None:
    """Frequencies between 0 and the bottom of the band are refused."""
    await _setup(hass, GARAGE)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            entity_id="number.garage_rf_learn_frequency",
            **{ATTR_VALUE: 100},
        )


async def test_window_error_turns_switch_off(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A device error during a window is logged and the window closes."""
    _, window = await _setup(hass, GARAGE)
    window.error = NetworkTimeoutError(-4000, "Network timeout", "No response")
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_capture")
    await hass.async_block_till_done()

    assert hass.states.get("switch.garage_capture").state == STATE_OFF
    assert "Capture window on Garage failed" in caplog.text


async def test_learn_command_uses_capture(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """remote.learn_command captures through the same window and stores it."""
    _, window = await _setup(hass, GARAGE)
    window.send(_ir())
    await _call(
        hass,
        REMOTE_DOMAIN,
        SERVICE_LEARN_COMMAND,
        entity_id="remote.garage",
        device="tv",
        command=["power"],
    )

    assert window.calls[-1] == (
        "capture",
        (30.0,),
        {"stop_after_first": True, "extend_on_signal": False},
    )
    key = f"{DOMAIN}_remote_34ea34c43f31_codes"
    assert hass_storage[key]["data"] == {"tv": {"power": b64encode(IR_PACKET).decode()}}


async def test_learn_rf_command_uses_learn_frequency(hass: HomeAssistant) -> None:
    """An RF learn listens on the configured carrier."""
    _, window = await _setup(hass, GARAGE)
    await _call(
        hass,
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        entity_id="number.garage_rf_learn_frequency",
        **{ATTR_VALUE: 433.92},
    )
    window.send(_rf())
    await _call(
        hass,
        REMOTE_DOMAIN,
        SERVICE_LEARN_COMMAND,
        entity_id="remote.garage",
        device="gate",
        command=["open"],
        command_type="rf",
    )
    assert window.calls[-1][0] == "capture_rf"
    assert window.calls[-1][2]["frequency"] == 433.92


async def test_learn_command_times_out(
    hass: HomeAssistant, hass_storage: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """A window that closes with nothing caught stores nothing."""
    _, window = await _setup(hass, GARAGE)
    window.send(None)
    await _call(
        hass,
        REMOTE_DOMAIN,
        SERVICE_LEARN_COMMAND,
        entity_id="remote.garage",
        device="tv",
        command=["power"],
    )
    assert f"{DOMAIN}_remote_34ea34c43f31_codes" not in hass_storage
    assert "No infrared code received" in caplog.text


def test_pulses_to_timings() -> None:
    """Marks are positive, spaces negative, and the trailing gap is dropped."""
    assert broadlink_pulses_to_timings((100, 200, 300, 400)) == [100, -200, 300]
    assert broadlink_pulses_to_timings((100, 200, 300)) == [100, -200, 300]
    assert SignalKind.IR == CapturedSignal.from_packet(IR_PACKET).kind
