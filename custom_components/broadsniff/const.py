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
