"""Tests for capture on Broadlink remotes."""

from base64 import b64encode
from typing import Any

from broadlink_async.exceptions import AuthenticationError, NetworkTimeoutError
from broadlink_async.remote import CapturedSignal, SignalKind, pulses_to_data
import pytest

from custom_components.broadsniff.infrared import broadlink_pulses_to_timings
from homeassistant.components.infrared import (
    InfraredReceivedSignal,
    async_subscribe_receiver,
)
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
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
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from . import ENTRANCE, GARAGE, FakeApis, FakeWindow, add_remote, setup_capture

from pytest_homeassistant_custom_component.common import (
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

IR_PULSES = (9000, 4500, 560, 560, 560, 1690, 560, 40000)
IR_PACKET = pulses_to_data(list(IR_PULSES))
# An RM4 pro RF capture: type 0xB1, carrier 433.92 MHz at bytes 4 to 7.
RF_PACKET = bytes.fromhex("b1c00c00009f06000b210d210b210d21")


@pytest.fixture(autouse=True)
def _broadlink(broadlink: None) -> None:
    """Use the built-in integration stub in every test."""


async def _setup(hass: HomeAssistant, fake_apis: FakeApis) -> FakeWindow:
    """Set up the garage remote (RM4 pro) with fake capture windows."""
    await add_remote(hass, GARAGE)
    await add_remote(hass, ENTRANCE)
    await setup_capture(hass)
    return fake_apis.windows[GARAGE.mac]


async def _set_mode(hass: HomeAssistant, mode: str, remote: str = "garage") -> None:
    await _call(
        hass,
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        entity_id=f"select.{remote}_capture_mode",
        **{ATTR_OPTION: mode},
    )


async def _call(hass: HomeAssistant, domain: str, service: str, **data: Any) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await hass.async_block_till_done()


def _ir() -> CapturedSignal:
    return CapturedSignal.from_packet(IR_PACKET)


def _rf() -> CapturedSignal:
    return CapturedSignal.from_packet(RF_PACKET)


async def test_capture_switch_runs_a_window(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """The switch opens a window, publishes the code and turns off after it."""
    window = await _setup(hass, fake_apis)
    await _set_mode(hass, "single")
    events: list[Event] = []
    hass.bus.async_listen("state_changed", events.append)

    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_ir_capture"
    )
    state = hass.states.get("switch.garage_ir_capture")
    assert state.state == STATE_ON
    assert state.attributes["window_ends_at"] is not None
    receiver = hass.states.get("infrared.garage_ir_receiver")
    assert receiver.attributes["capturing"] is True
    assert window.calls == [
        ("capture", (10.0,), {"stop_after_first": True, "extend_on_signal": False})
    ]

    window.send(_ir())
    await hass.async_block_till_done()

    assert hass.states.get("switch.garage_ir_capture").state == STATE_OFF
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


async def test_capture_switch_off_stops_the_window(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Turning the switch off closes the window early."""
    window = await _setup(hass, fake_apis)
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_ir_capture"
    )
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id="switch.garage_ir_capture"
    )

    assert hass.states.get("switch.garage_ir_capture").state == STATE_OFF
    assert window.closed == 1
    event = hass.states.get("event.garage_ir_capture")
    assert event.attributes["event_type"] == "window_ended"
    assert event.attributes["signals"] == 0
    assert event.attributes["stopped"] is True


async def test_settings_reach_the_window(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Window length and the sliding mode come from the entities."""
    window = await _setup(hass, fake_apis)
    await _call(
        hass,
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        entity_id="number.garage_capture_window",
        **{ATTR_VALUE: 45},
    )
    await _set_mode(hass, "sliding")
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_ir_capture"
    )

    assert window.calls[-1] == (
        "capture",
        (45.0,),
        {"stop_after_first": False, "extend_on_signal": True},
    )
    first_deadline = hass.states.get("switch.garage_ir_capture").attributes[
        "window_ends_at"
    ]
    window.send(_ir())
    window.send(_ir())
    await hass.async_block_till_done()
    assert hass.states.get("switch.garage_ir_capture").state == STATE_ON
    assert (
        hass.states.get("switch.garage_ir_capture").attributes["window_ends_at"]
        >= first_deadline
    )
    window.send(None)
    await hass.async_block_till_done()
    assert hass.states.get("event.garage_ir_capture").attributes["signals"] == 2


async def test_defaults(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """A new remote listens for 10 seconds, restarting after each code, at 433.92."""
    window = await _setup(hass, fake_apis)
    assert hass.states.get("number.garage_capture_window").state == "10.0"
    assert hass.states.get("select.garage_capture_mode").state == "sliding"
    assert hass.states.get("select.garage_rf_frequency").state == "mhz_433_92"
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_rf_capture"
    )
    assert window.calls[-1] == (
        "capture_rf",
        (10.0,),
        {"frequency": 433.92, "stop_after_first": False, "extend_on_signal": True},
    )
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id="switch.garage_rf_capture"
    )


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        pytest.param(
            "single", {"stop_after_first": True, "extend_on_signal": False}, id="single"
        ),
        pytest.param(
            "multiple",
            {"stop_after_first": False, "extend_on_signal": False},
            id="multiple",
        ),
        pytest.param(
            "sliding",
            {"stop_after_first": False, "extend_on_signal": True},
            id="sliding",
        ),
    ],
)
async def test_capture_modes(
    hass: HomeAssistant, fake_apis: FakeApis, mode: str, expected: dict[str, bool]
) -> None:
    """Each capture mode maps to the library's window settings."""
    window = await _setup(hass, fake_apis)
    await _set_mode(hass, mode)
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_ir_capture"
    )
    assert window.calls[-1][2] == expected
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id="switch.garage_ir_capture"
    )


async def test_settings_are_restored(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """Settings survive a restart."""
    mock_restore_cache(hass, [State("select.garage_capture_mode", "multiple")])
    window = await _setup(hass, fake_apis)
    assert hass.states.get("select.garage_capture_mode").state == "multiple"
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_ir_capture"
    )
    assert window.calls[-1][2] == {"stop_after_first": False, "extend_on_signal": False}
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id="switch.garage_ir_capture"
    )


@pytest.mark.parametrize(
    ("option", "expected"),
    [
        pytest.param("mhz_433_92", 433.92, id="433"),
        pytest.param("mhz_315", 315.0, id="315"),
        pytest.param("sweep", None, id="sweep"),
    ],
)
async def test_rf_capture(
    hass: HomeAssistant, fake_apis: FakeApis, option: str, expected: float | None
) -> None:
    """RF capture listens on the chosen carrier and is not infrared."""
    window = await _setup(hass, fake_apis)
    await _set_mode(hass, "single")
    await _call(
        hass,
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        entity_id="select.garage_rf_frequency",
        **{ATTR_OPTION: option},
    )
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_rf_capture"
    )
    assert hass.states.get("switch.garage_rf_capture").state == STATE_ON
    assert hass.states.get("switch.garage_ir_capture").state == STATE_OFF
    assert window.calls[-1] == (
        "capture_rf",
        (10.0,),
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


async def test_rf_event_carries_code_and_carrier(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """The RF signal event has the code and the carrier from the packet."""
    window = await _setup(hass, fake_apis)
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


async def test_receiver_publishes_ir_timings(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Captured IR codes reach infrared platform subscribers as timings."""
    window = await _setup(hass, fake_apis)
    received: list[InfraredReceivedSignal] = []
    async_subscribe_receiver(hass, "infrared.garage_ir_receiver", received.append)

    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_ir_capture"
    )
    signal = _ir()
    window.send(signal)
    await hass.async_block_till_done()

    assert len(received) == 1
    assert received[0].timings == broadlink_pulses_to_timings(signal.pulses)
    assert hass.states.get("infrared.garage_ir_receiver").state != STATE_UNKNOWN


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_rf_custom_frequency(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """Custom uses the custom frequency entity, inside the receiver's band."""
    window = await _setup(hass, fake_apis)
    await _call(
        hass,
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        entity_id="number.garage_rf_custom_frequency",
        **{ATTR_VALUE: 390},
    )
    await _call(
        hass,
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        entity_id="select.garage_rf_frequency",
        **{ATTR_OPTION: "custom"},
    )
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_rf_capture"
    )
    assert window.calls[-1][2]["frequency"] == 390.0
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            entity_id="number.garage_rf_custom_frequency",
            **{ATTR_VALUE: 868},
        )


async def test_restored_window_is_clamped(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """A window saved above today's maximum comes back as the maximum."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("number.garage_capture_window", "135.0"),
                {
                    "native_value": 135.0,
                    "native_min_value": 1.0,
                    "native_max_value": 300.0,
                    "native_step": 1.0,
                    "native_unit_of_measurement": "s",
                },
            )
        ],
    )
    window = await _setup(hass, fake_apis)
    assert hass.states.get("number.garage_capture_window").state == "60.0"
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_ir_capture"
    )
    assert window.calls[-1][1] == (60.0,)


async def test_capture_window_tops_out_at_a_minute(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """The capture window slider runs from 1 to 60 seconds."""
    await _setup(hass, fake_apis)
    state = hass.states.get("number.garage_capture_window")
    assert (state.attributes["min"], state.attributes["max"]) == (1, 60)
    assert state.attributes["mode"] == "slider"


async def test_window_error_turns_switch_off(
    hass: HomeAssistant, fake_apis: FakeApis, caplog: pytest.LogCaptureFixture
) -> None:
    """A device error during a window is logged and the window closes."""
    window = await _setup(hass, fake_apis)
    window.error = NetworkTimeoutError(-4000, "Network timeout", "No response")
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_ir_capture"
    )
    await hass.async_block_till_done()

    assert hass.states.get("switch.garage_ir_capture").state == STATE_OFF
    assert "Capture window on Garage failed" in caplog.text


async def test_session_opens_once(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """The first window opens the capture session; later ones reuse it."""
    window = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    for _ in range(2):
        await _call(
            hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_ir_capture"
        )
        window.send(None)
        await hass.async_block_till_done()
    api.auth.assert_awaited_once()
    assert len(window.calls) == 2


async def test_session_refused(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """A device that refuses the session opens no window."""
    window = await _setup(hass, fake_apis)
    fake_apis.apis[GARAGE.mac].auth.side_effect = AuthenticationError(
        -1, "Authentication failed", "Locked"
    )
    with pytest.raises(HomeAssistantError, match="Capture failed"):
        await _call(
            hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_ir_capture"
        )
    assert window.calls == []
    assert hass.states.get("switch.garage_ir_capture").state == STATE_OFF


async def test_ir_only_remote(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """A remote without RF captures infrared."""
    await _setup(hass, fake_apis)
    await _set_mode(hass, "single", "entrance")
    window = fake_apis.windows[ENTRANCE.mac]
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.entrance_ir_capture"
    )
    window.send(_ir())
    await hass.async_block_till_done()
    assert window.calls[0][0] == "capture"
    assert hass.states.get("event.entrance_ir_capture").attributes["signals"] == 1
    assert hass.states.get("switch.entrance_rf_capture") is None


async def test_codes_captured_counts_live(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """The counter resets when a window opens and counts each code."""
    window = await _setup(hass, fake_apis)
    sensor = "sensor.garage_codes_captured"
    assert hass.states.get(sensor).state == "0"

    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_ir_capture"
    )
    window.send(_ir())
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "1"
    window.send(_ir())
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "2"
    window.send(None)
    await hass.async_block_till_done()
    assert hass.states.get("switch.garage_ir_capture").state == STATE_OFF
    assert hass.states.get(sensor).state == "2"

    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_rf_capture"
    )
    assert hass.states.get(sensor).state == "0"
    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id="switch.garage_rf_capture"
    )


def test_pulses_to_timings() -> None:
    """Marks are positive, spaces negative, and the trailing gap is dropped."""
    assert broadlink_pulses_to_timings((100, 200, 300, 400)) == [100, -200, 300]
    assert broadlink_pulses_to_timings((100, 200, 300)) == [100, -200, 300]
    assert SignalKind.IR == CapturedSignal.from_packet(IR_PACKET).kind
