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

# The RM pro and RM4 pro receive RF between these carriers.
MIN_RF_FREQUENCY = 300.0
MAX_RF_FREQUENCY = 450.0
DEFAULT_RF_CUSTOM_FREQUENCY = 433.92

# The bands the RF receiver declares, in Hz: the same two the built-in
# Broadlink integration's RF transmitter declares for these remotes.
RF_FREQUENCY_RANGES: list[tuple[int, int]] = [
    (433_050_000, 434_790_000),
    (314_950_000, 315_250_000),
]
