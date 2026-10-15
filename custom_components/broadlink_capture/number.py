"""Support for Broadlink numbers."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .capture_entities import (
    BroadlinkCaptureNumber,
    BroadlinkCaptureWindowNumber,
    BroadlinkRFLearnFrequencyNumber,
    supports_rf,
)
from .const import DOMAIN
from .device import BroadlinkDevice

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Broadlink capture settings."""
    device: BroadlinkDevice = hass.data[DOMAIN].devices[config_entry.entry_id]
    numbers: list[BroadlinkCaptureNumber] = [BroadlinkCaptureWindowNumber(device)]
    if supports_rf(device):
        numbers.append(BroadlinkRFLearnFrequencyNumber(device))
    async_add_entities(numbers)
