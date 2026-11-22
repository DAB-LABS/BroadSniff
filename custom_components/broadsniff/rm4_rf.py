"""Read RM4 pro RF captures away from 433.92 MHz correctly.

An RM4 pro writes a different first byte for each carrier it captures on
(0x78 at 315 MHz, 0xB0 at 433.42 and so on), with the carrier itself at
bytes 4 to 7 and the timings from byte 8. broadlink-async before 1.4.1
recognizes only 0xB1, the 433.92 MHz byte, and reads every other capture
from byte 4: four made-up timings in front, and the first two real ones
lost. This rereads such a capture as the 0xB1 layout it really is.

It is a stopgap: once broadlink-async recognizes these captures itself
(it then has ``is_rm4_rf_packet``), signals are passed on untouched.
"""

from __future__ import annotations

import dataclasses

from broadlink_async import remote as broadlink_remote
from broadlink_async.remote import CapturedSignal, data_to_pulses, rf_carrier_mhz

# True when the installed broadlink-async reads these captures itself.
LIBRARY_READS_EVERY_CARRIER = hasattr(broadlink_remote, "is_rm4_rf_packet")

_RM4_RF_TYPE = 0xB1
_IR_TYPE = 0x26
_RM4_RF_BYTE1 = 0xC0
_HEADER_LEN = 8
# The carriers, in kHz, that mark bytes 4 to 7 as a carrier field.
_CARRIER_KHZ = (300_000, 950_000)


def misread_rm4_rf(packet: bytes) -> bool:
    """Return whether the library reads this packet from the wrong offset.

    That is an RM4 pro RF capture whose first byte is not 0xB1: byte 1 is
    0xC0, the length field covers the carrier field, and bytes 4 to 7 hold
    a carrier between 300 and 950 MHz.
    """
    if len(packet) < _HEADER_LEN or packet[0] in (_RM4_RF_TYPE, _IR_TYPE):
        return False
    if packet[1] != _RM4_RF_BYTE1:
        return False
    if int.from_bytes(packet[2:4], "little") < 4:
        return False
    khz = int.from_bytes(packet[4:8], "little")
    return _CARRIER_KHZ[0] <= khz <= _CARRIER_KHZ[1]


def reread_rm4_rf(signal: CapturedSignal) -> CapturedSignal:
    """Return the signal with its timings and carrier read correctly.

    The packet, the device's own bytes, is kept as it is, so a code sent
    back is unchanged. Any signal the library already reads correctly is
    returned as it is.
    """
    if LIBRARY_READS_EVERY_CARRIER or not misread_rm4_rf(signal.packet):
        return signal
    as_433_92 = bytes([_RM4_RF_TYPE]) + signal.packet[1:]
    return dataclasses.replace(
        signal,
        pulses=tuple(data_to_pulses(as_433_92)),
        frequency_mhz=rf_carrier_mhz(as_433_92) or signal.frequency_mhz,
    )
