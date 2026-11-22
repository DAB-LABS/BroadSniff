"""The RF receiver: its attributes, the bands it declares and what it emits.

It exists from Home Assistant 2026.11; on older releases these tests skip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from broadlink_async.remote import (
    CapturedSignal,
    SignalKind,
    data_to_pulses,
    pulses_to_data,
)
import pytest

from custom_components.broadsniff.const import HAS_RF_RECEIVER
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
from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from . import ENTRANCE, GARAGE, FakeApis, FakeWindow, add_remote, setup_capture

from pytest_homeassistant_custom_component.common import mock_restore_cache

pytestmark = pytest.mark.skipif(
    not HAS_RF_RECEIVER, reason="Home Assistant has no RF receivers before 2026.11"
)

RECEIVER = "radio_frequency.garage_rf_receiver"
IR_RECEIVER = "infrared.garage_ir_receiver"
SWITCH = "switch.garage_rf_capture"
IR_SWITCH = "switch.garage_ir_capture"
MODE = "select.garage_capture_mode"
FREQUENCY = "select.garage_rf_frequency"
WINDOW = "number.garage_capture_window"
CUSTOM = "number.garage_rf_custom_frequency"
PRESET_OPTIONS = {
    "mhz_433_92": 433_920_000,
    "mhz_433_42": 433_420_000,
    "mhz_315": 315_000_000,
    "sweep": "sweep",
}

# Button 1 of a DEWENWILS IT104 keyfob, captured by an RM4 pro.
KEYFOB_BUTTON_1 = bytes.fromhex(
    (Path(__file__).parent / "fixtures" / "dewenwils_it104_button_1.hex")
    .read_text()
    .strip()
)
# An RM4 pro RF capture: type 0xB1, carrier 433.92 MHz at bytes 4 to 7.
RF_PACKET = bytes.fromhex("b1c00c00009f06000b210d210b210d21")
IR_PACKET = pulses_to_data([9000, 4500, 560, 560, 560, 1690, 560, 40000])
# Five durations, so the packet ends on carrier on.
RF_PULSES = [350, 1050, 350, 10500, 350]


@pytest.fixture(autouse=True)
def _broadlink(broadlink: None) -> None:
    """Use the built-in integration stub in every test."""


async def _setup(hass: HomeAssistant, fake_apis: FakeApis) -> FakeWindow:
    """Set up the garage remote (RM4 pro) and the entrance (RM mini 3)."""
    await add_remote(hass, GARAGE)
    await add_remote(hass, ENTRANCE)
    await setup_capture(hass)
    return fake_apis.windows[GARAGE.mac]


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


def _attributes(hass: HomeAssistant, entity_id: str = RECEIVER) -> dict[str, Any]:
    return dict(hass.states.get(entity_id).attributes)


def _subscribe(hass: HomeAssistant, entity_id: str = RECEIVER) -> list[Any]:
    from homeassistant.components.radio_frequency import (
        async_subscribe_receiver,
    )

    received: list[Any] = []
    async_subscribe_receiver(hass, entity_id, received.append)
    return received


def _entity(hass: HomeAssistant, entity_id: str = RECEIVER) -> Any:
    from homeassistant.components.radio_frequency.const import (
        DATA_COMPONENT,
    )

    return hass.data[DATA_COMPONENT].get_entity(entity_id)


async def test_receiver_on_rf_remotes_only(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """The RM4 pro gets an RF receiver; the RM mini 3 does not."""
    await _setup(hass, fake_apis)

    registry = er.async_get(hass)
    entry = registry.async_get(RECEIVER)
    assert entry.unique_id == f"{GARAGE.mac}-rf_receiver"
    assert entry.original_device_class == "receiver"
    assert entry.device_id == registry.async_get(IR_RECEIVER).device_id
    assert hass.states.get(RECEIVER).state == STATE_UNKNOWN
    assert not [
        entity_id
        for entity_id in hass.states.async_entity_ids("radio_frequency")
        if "entrance" in entity_id
    ]


async def test_declared_bands(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """The receiver declares the bands an RM4 pro was measured hearing on."""
    await _setup(hass, fake_apis)
    entity = _entity(hass)

    assert entity.supported_frequency_ranges == [
        (305_000_000, 335_000_000),
        (433_420_000, 438_000_000),
    ]
    for heard in (305_000_000, 315_000_000, 320_000_000, 335_000_000):
        assert entity.supports_frequency(heard)
    for heard in (433_420_000, 433_920_000, 438_000_000):
        assert entity.supports_frequency(heard)
    for missed in (303_875_000, 340_000_000, 390_000_000, 418_000_000, 440_000_000):
        assert not entity.supports_frequency(missed)
    assert not entity.supports_frequency(868_350_000)


async def test_attributes_from_startup(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """The contract's attributes are there before any window opens."""
    await _setup(hass, fake_apis)

    assert _attributes(hass) | {"friendly_name": None, "device_class": None} == {
        "friendly_name": None,
        "device_class": None,
        "capturing": False,
        "window_ends_at": None,
        "capture_mode": "sliding",
        "capture_phase": None,
        "frequency_hz": None,
        "capture_switch": SWITCH,
        "window_length": WINDOW,
        "capture_mode_select": MODE,
        "frequency_select": FREQUENCY,
        "frequency_options": PRESET_OPTIONS,
    }


async def test_frequency_options_follow_the_select(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Every option the map offers is one the select has, in its order."""
    await _setup(hass, fake_apis)

    options = _attributes(hass)["frequency_options"]
    select_options = hass.states.get(FREQUENCY).attributes["options"]
    assert list(options) == [option for option in select_options if option in options]
    # Custom is left out while its number is disabled, as it is by default.
    assert "custom" not in options


async def test_no_frequency_number_while_custom_is_disabled(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Without the custom number there is nothing for "Other..." to write."""
    await _setup(hass, fake_apis)

    attributes = _attributes(hass)
    assert "frequency_number" not in attributes
    assert "frequency_number_option" not in attributes


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_frequency_number_is_declared_with_its_option(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """The custom number is named, with the option that listens on it."""
    await _setup(hass, fake_apis)

    attributes = _attributes(hass)
    assert attributes["frequency_number"] == CUSTOM
    assert attributes["frequency_number_option"] == "custom"
    # The option the number feeds is one the select really has, and the
    # map still gives it a carrier, so a HAIR that reads only the map works.
    assert "custom" in hass.states.get(FREQUENCY).attributes["options"]
    assert attributes["frequency_options"]["custom"] == 433_920_000

    er.async_get(hass).async_update_entity(
        CUSTOM, new_entity_id="number.garage_learn_carrier"
    )
    await hass.async_block_till_done()
    assert _attributes(hass)["frequency_number"] == "number.garage_learn_carrier"

    er.async_get(hass).async_update_entity(
        "number.garage_learn_carrier", disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    attributes = _attributes(hass)
    assert "frequency_number" not in attributes
    assert "frequency_number_option" not in attributes


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_custom_option_follows_the_number(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """With the custom number enabled, custom is offered at its carrier."""
    await _setup(hass, fake_apis)
    assert _attributes(hass)["frequency_options"] == PRESET_OPTIONS | {
        "custom": 433_920_000
    }

    await _call(
        hass,
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        entity_id=CUSTOM,
        **{ATTR_VALUE: 433.42},
    )

    assert _attributes(hass)["frequency_options"]["custom"] == 433_420_000

    er.async_get(hass).async_update_entity(
        CUSTOM, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert _attributes(hass)["frequency_options"] == PRESET_OPTIONS


async def test_renamed_frequency_select(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Renaming the frequency select reaches the receiver without a window."""
    await _setup(hass, fake_apis)

    er.async_get(hass).async_update_entity(
        FREQUENCY, new_entity_id="select.garage_learn_carrier"
    )
    await hass.async_block_till_done()

    assert _attributes(hass)["frequency_select"] == "select.garage_learn_carrier"


async def test_disabled_frequency_select_takes_its_options(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Without the select there is nothing to offer, so the map goes too."""
    await _setup(hass, fake_apis)

    er.async_get(hass).async_update_entity(
        FREQUENCY, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()

    attributes = _attributes(hass)
    assert "frequency_select" not in attributes
    assert "frequency_options" not in attributes
    assert attributes["window_length"] == WINDOW
    assert attributes["capture_switch"] == SWITCH


async def test_capture_mode_is_reported_and_restored(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """The receiver reports the mode the next window opens in."""
    mock_restore_cache(hass, [State(MODE, "multiple")])
    await _setup(hass, fake_apis)
    assert _attributes(hass)["capture_mode"] == "multiple"

    await _select(hass, MODE, "single")
    assert _attributes(hass)["capture_mode"] == "single"


@pytest.mark.parametrize(
    ("option", "hz"),
    [
        pytest.param("mhz_433_92", 433_920_000, id="433"),
        pytest.param("mhz_315", 315_000_000, id="315"),
    ],
)
async def test_attributes_during_an_rf_window(
    hass: HomeAssistant, fake_apis: FakeApis, option: str, hz: int
) -> None:
    """While an RF window on a named carrier is open, it is listening there."""
    await _setup(hass, fake_apis)
    await _select(hass, FREQUENCY, option)

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)

    attributes = _attributes(hass)
    assert attributes["capturing"] is True
    assert attributes["window_ends_at"] is not None
    assert attributes["capture_phase"] == "listening"
    assert attributes["frequency_hz"] == hz
    assert attributes["capture_switch"] == SWITCH
    assert _attributes(hass, IR_RECEIVER)["capturing"] is False

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=SWITCH)

    attributes = _attributes(hass)
    assert attributes["capturing"] is False
    assert attributes["window_ends_at"] is None
    assert attributes["capture_phase"] is None
    assert attributes["frequency_hz"] is None


async def test_an_ir_window_is_not_capturing(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """During an IR window the RF receiver is closed and names the RF switch."""
    window = await _setup(hass, fake_apis)
    received = _subscribe(hass)

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)
    window.send(CapturedSignal.from_packet(IR_PACKET))
    await hass.async_block_till_done()

    attributes = _attributes(hass)
    assert attributes["capturing"] is False
    assert attributes["capture_phase"] is None
    assert attributes["capture_switch"] == SWITCH
    assert _attributes(hass, IR_RECEIVER)["capturing"] is True
    assert received == []
    assert hass.states.get(RECEIVER).state == STATE_UNKNOWN

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=IR_SWITCH)


async def test_rm4_capture_is_published(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """An RM4 pro capture reaches subscribers with its own carrier."""
    window = await _setup(hass, fake_apis)
    received = _subscribe(hass)
    ir_received: list[Any] = []

    from homeassistant.components.infrared import (
        async_subscribe_receiver as subscribe_ir,
    )

    subscribe_ir(hass, IR_RECEIVER, ir_received.append)
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)
    window.send(CapturedSignal.from_packet(RF_PACKET))
    await hass.async_block_till_done()

    pulses = data_to_pulses(RF_PACKET)
    assert len(received) == 1
    signal = received[0]
    assert signal.frequency == 433_920_000
    assert signal.modulation.name == "OOK"
    assert signal.timings == [
        pulse if index % 2 == 0 else -pulse for index, pulse in enumerate(pulses)
    ]
    assert hass.states.get(RECEIVER).state != STATE_UNKNOWN
    assert ir_received == []


@pytest.mark.parametrize(
    ("kind", "carrier", "hz"),
    [
        pytest.param(SignalKind.RF_433, 433.92, 433_920_000, id="0xb2"),
        pytest.param(SignalKind.RF_315, 315.0, 315_000_000, id="0xd7"),
    ],
)
async def test_other_packets_carry_the_window_carrier(
    hass: HomeAssistant, fake_apis: FakeApis, kind: SignalKind, carrier: float, hz: int
) -> None:
    """A packet without a carrier field is published on the window's carrier."""
    window = await _setup(hass, fake_apis)
    received = _subscribe(hass)
    packet = pulses_to_data(RF_PULSES, kind=kind)

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)
    window.send(CapturedSignal.from_packet(packet, carrier))
    await hass.async_block_till_done()

    assert received[0].frequency == hz
    assert received[0].timings == [
        pulse if index % 2 == 0 else -pulse
        for index, pulse in enumerate(data_to_pulses(packet))
    ]


async def test_trailing_carrier_off_is_kept(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Unlike infrared, a final carrier-off duration is part of the signal."""
    window = await _setup(hass, fake_apis)
    received = _subscribe(hass)
    packet = pulses_to_data([*RF_PULSES, 10500], kind=SignalKind.RF_433)

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)
    window.send(CapturedSignal.from_packet(packet, 433.92))
    await hass.async_block_till_done()

    timings = received[0].timings
    assert len(timings) == 6
    assert timings[-1] < 0
    assert timings[-1] == -data_to_pulses(packet)[-1]


async def test_renamed_switch(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """Renaming the RF switch reaches the receiver without a window."""
    await _setup(hass, fake_apis)

    er.async_get(hass).async_update_entity(SWITCH, new_entity_id="switch.garage_rf")
    await hass.async_block_till_done()

    assert _attributes(hass)["capture_switch"] == "switch.garage_rf"
    assert _attributes(hass, IR_RECEIVER)["capture_switch"] == IR_SWITCH


async def test_disabled_switch_is_left_out(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """A disabled RF switch cannot be turned on, so it is not offered."""
    await _setup(hass, fake_apis)
    registry = er.async_get(hass)

    registry.async_update_entity(SWITCH, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    assert "capture_switch" not in _attributes(hass)
    assert _attributes(hass, IR_RECEIVER)["capture_switch"] == IR_SWITCH

    registry.async_update_entity(SWITCH, disabled_by=None)
    await hass.async_block_till_done()
    assert _attributes(hass)["capture_switch"] == SWITCH


async def test_failed_lookup_omits_the_switch(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """With no registry entry the attribute is left out, not left stale."""
    await _setup(hass, fake_apis)

    er.async_get(hass).async_remove(SWITCH)
    await hass.async_block_till_done()

    attributes = _attributes(hass)
    assert "capture_switch" not in attributes
    assert attributes["capturing"] is False


async def test_ir_switch_changes_leave_the_rf_receiver_alone(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Renaming the IR switch does not write the RF receiver."""
    await _setup(hass, fake_apis)
    before = hass.states.get(RECEIVER)

    er.async_get(hass).async_update_entity(IR_SWITCH, new_entity_id="switch.g_ir")
    await hass.async_block_till_done()

    assert hass.states.get(RECEIVER).last_updated == before.last_updated


def _frames(timings: list[int]) -> list[list[int]]:
    """Split signed timings after every gap longer than 5 ms."""
    frames: list[list[int]] = []
    current: list[int] = []
    for timing in timings:
        current.append(timing)
        if timing < -5000:
            frames.append(current)
            current = []
    if current:
        frames.append(current)
    return frames


async def test_keyfob_capture(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """A real RM4 pro capture of a keyfob button reaches subscribers whole.

    The packet holds the end of one frame, nine whole frames and a frame
    cut short, and ends on 1500 ticks: where the read of the remote's
    buffer stopped, published as reported. Every whole frame reads as the
    same 24-bit code by its marks; the space at index 249 is stretched by
    the buffer seam, which a 5 ms split does not cut.
    """
    window = await _setup(hass, fake_apis)
    received = _subscribe(hass)

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)
    window.send(CapturedSignal.from_packet(KEYFOB_BUTTON_1))
    await hass.async_block_till_done()

    assert KEYFOB_BUTTON_1[0] == 0xB1
    signal = received[0]
    assert signal.frequency == 433_920_000
    timings = signal.timings
    assert len(timings) == 500
    assert timings[:2] == [456, -12881]
    assert timings[-1] == -45680
    assert all(timing > 0 for timing in timings[::2])
    assert all(timing < 0 for timing in timings[1::2])

    whole = [frame for frame in _frames(timings) if len(frame) == 50]
    assert len(whole) == 9
    codes = {
        sum(1 << (23 - bit) for bit in range(24) if frame[2 * bit] > 800)
        for frame in whole
    }
    assert codes == {0x950E49}
    gaps = {-frame[-1] for frame in whole}
    assert all(12_800 < gap < 13_000 for gap in gaps)


async def test_315_capture_is_published_with_its_real_timings(
    hass: HomeAssistant, fake_apis: FakeApis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capture away from 433.92 MHz reaches subscribers read correctly."""
    from custom_components.broadsniff import rm4_rf

    from .test_rm4_rf import AS_433_92, CAPTURE_315

    monkeypatch.setattr(rm4_rf, "LIBRARY_READS_EVERY_CARRIER", False)
    window = await _setup(hass, fake_apis)
    received = _subscribe(hass)

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)
    window.send(CapturedSignal.from_packet(CAPTURE_315, 315.0, kind=SignalKind.RF_315))
    await hass.async_block_till_done()

    assert received[0].frequency == 315_000_000
    assert received[0].timings == [
        pulse if index % 2 == 0 else -pulse
        for index, pulse in enumerate(data_to_pulses(AS_433_92))
    ]
