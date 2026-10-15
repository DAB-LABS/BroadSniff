"""Constants."""

from homeassistant.const import Platform

DOMAIN = "broadlink_capture"

DOMAINS_AND_TYPES = {
    Platform.CLIMATE: {"HYS"},
    Platform.EVENT: {"RM4MINI", "RM4PRO", "RM5PLUS", "RMMINI", "RMMINIB", "RMPRO"},
    Platform.INFRARED: {"RM4MINI", "RM4PRO", "RM5PLUS", "RMMINI", "RMMINIB", "RMPRO"},
    Platform.LIGHT: {"LB1", "LB2"},
    Platform.NUMBER: {"RM4MINI", "RM4PRO", "RM5PLUS", "RMMINI", "RMMINIB", "RMPRO"},
    Platform.RADIO_FREQUENCY: {"RM4PRO", "RMPRO"},
    Platform.REMOTE: {"RM4MINI", "RM4PRO", "RM5PLUS", "RMMINI", "RMMINIB", "RMPRO"},
    Platform.SELECT: {"HYS"},
    Platform.SENSOR: {
        "A1",
        "A2",
        "MP1S",
        "RM4MINI",
        "RM4PRO",
        "RMPRO",
        "SP2S",
        "SP3S",
        "SP4",
        "SP4B",
    },
    Platform.SWITCH: {
        "BG1",
        "MP1",
        "MP1S",
        "RM4MINI",
        "RM4PRO",
        "RM5PLUS",
        "RMMINI",
        "RMMINIB",
        "RMPRO",
        "SP1",
        "SP2",
        "SP2S",
        "SP3",
        "SP3S",
        "SP4",
        "SP4B",
    },
    Platform.TIME: {"HYS"},
}
DEVICE_TYPES = set.union(*DOMAINS_AND_TYPES.values())

DEFAULT_CAPTURE_WINDOW = 30.0
MIN_CAPTURE_WINDOW = 1.0
MAX_CAPTURE_WINDOW = 300.0
DEFAULT_STOP_AFTER_FIRST = True
DEFAULT_SLIDING_WINDOW = False
# The RM pro and RM4 pro learn on 315 MHz and 433 MHz; 0 means sweep for the
# carrier instead of naming it.
RF_LEARN_FREQUENCY_SWEEP = 0.0
MIN_RF_LEARN_FREQUENCY = 300.0
MAX_RF_LEARN_FREQUENCY = 450.0

DEFAULT_PORT = 80
DEFAULT_TIMEOUT = 5
