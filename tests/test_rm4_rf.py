"""The stopgap that rereads RM4 pro RF captures away from 433.92 MHz."""

from __future__ import annotations

from broadlink_async.remote import (
    CapturedSignal,
    SignalKind,
    data_to_pulses,
    pulses_to_data,
)
import pytest

from custom_components.broadsniff import rm4_rf
from custom_components.broadsniff.rm4_rf import misread_rm4_rf, reread_rm4_rf

# A real RM4 pro capture at 315 MHz: first byte 0x78, byte 1 0xC0, the
# carrier (315000 kHz) at bytes 4 to 7, then the timings.
CAPTURE_315 = bytes.fromhex(
    "78c06c0078ce0400111008081108081110080910080908080809080808081108"
    "0809071110090810090808081108081110080811100907110809080808081110"
    "1108080908100809080810090808081110111008080908101110080910080908"
    "081108080808111011101009080005dc"
)
# The same capture as it would be with the 433.92 MHz first byte.
AS_433_92 = bytes([0xB1]) + CAPTURE_315[1:]


@pytest.fixture(autouse=True)
def _old_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """Act as a broadlink-async that reads only 0xB1 captures."""
    monkeypatch.setattr(rm4_rf, "LIBRARY_READS_EVERY_CARRIER", False)


def test_315_capture_is_reread() -> None:
    signal = CapturedSignal.from_packet(CAPTURE_315, 315.0, kind=SignalKind.RF_315)
    fixed = reread_rm4_rf(signal)
    assert fixed.packet == CAPTURE_315
    assert fixed.pulses == tuple(data_to_pulses(AS_433_92))
    assert fixed.frequency_mhz == 315.0
    assert fixed.kind is SignalKind.RF_315
    # Read from byte 4, the carrier field became four timings in front and
    # the first two real ones were lost.
    assert signal.pulses[4:] == fixed.pulses[2:]


def test_the_packet_carrier_wins() -> None:
    signal = CapturedSignal.from_packet(CAPTURE_315, 433.92, kind=SignalKind.RF_315)
    assert reread_rm4_rf(signal).frequency_mhz == 315.0


@pytest.mark.parametrize(
    "packet",
    [
        pytest.param(AS_433_92, id="0xb1"),
        pytest.param(pulses_to_data([9000, 4500, 560, 560]), id="ir"),
        pytest.param(
            pulses_to_data([300, 900], kind=SignalKind.RF_433, repeat=3), id="0xb2"
        ),
        pytest.param(
            pulses_to_data([300, 900, 300, 900], kind=SignalKind.RF_433, repeat=192),
            id="0xb2_repeat_192",
        ),
        pytest.param(bytes.fromhex("78c00600f88f04000d0d"), id="carrier_too_low"),
        pytest.param(bytes.fromhex("78c0020078ce04000d0d"), id="short_length"),
        pytest.param(bytes.fromhex("78c0030078ce04"), id="too_short"),
    ],
)
def test_other_packets_are_left_alone(packet: bytes) -> None:
    assert not misread_rm4_rf(packet)
    signal = CapturedSignal.from_packet(packet, 433.92, kind=SignalKind.RF_433)
    assert reread_rm4_rf(signal) is signal


def test_a_fixed_library_is_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rm4_rf, "LIBRARY_READS_EVERY_CARRIER", True)
    signal = CapturedSignal.from_packet(CAPTURE_315, 315.0, kind=SignalKind.RF_315)
    assert reread_rm4_rf(signal) is signal
