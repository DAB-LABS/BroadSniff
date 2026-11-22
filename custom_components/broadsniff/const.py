"""Constants."""

from homeassistant.const import Platform

DOMAIN = "broadsniff"
BROADLINK_DOMAIN = "broadlink"

PLATFORMS = [
    Platform.EVENT,
    Platform.INFRARED,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


def _has_rf_receiver() -> bool:
    """Return True if Home Assistant has radio frequency receivers (2026.11)."""
    try:
        from homeassistant.components.radio_frequency import (  # noqa: F401
            RadioFrequencyReceiverEntity,
        )
    except ImportError:
        return False
    return True


# The RF receiver needs Home Assistant 2026.11. On older releases BroadSniff
# loads without it.
HAS_RF_RECEIVER = _has_rf_receiver()
if HAS_RF_RECEIVER:
    PLATFORMS.append(Platform.RADIO_FREQUENCY)

# Universal remotes, the Broadlink devices that can learn codes.
REMOTE_TYPES = {"RM4MINI", "RM4PRO", "RM5PLUS", "RMMINI", "RMMINIB", "RMPRO"}
# The remotes that also learn radio frequency codes.
RF_TYPES = {"RM4PRO", "RMPRO"}

CONF_EXCLUDED = "excluded"

DEFAULT_PORT = 80

DEFAULT_CAPTURE_WINDOW = 10.0
MIN_CAPTURE_WINDOW = 1.0
MAX_CAPTURE_WINDOW = 60.0

# The carriers the RF custom frequency number accepts, in MHz: the span of
# the bands an RM4 pro was measured receiving on (RF_FREQUENCY_RANGES).
MIN_RF_FREQUENCY = 305.0
MAX_RF_FREQUENCY = 438.0
DEFAULT_RF_CUSTOM_FREQUENCY = 433.92

# The bands the RF receiver declares, in Hz: where an RM4 pro was measured
# hearing through a capture window (2026-11-19, two runs). It was heard at
# 305, 315, 320, 335, 433.42, 433.92 and 438 MHz, and not at 303.875, 340,
# 390, 418 or 440. 431 to 433.42 was not tested, so it is left out.
RF_FREQUENCY_RANGES: list[tuple[int, int]] = [
    (305_000_000, 335_000_000),
    (433_420_000, 438_000_000),
]
