"""Support for Broadlink capture events."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .capture_entities import BroadlinkCaptureEvent, capture_kinds
from .const import DOMAIN
from .device import BroadlinkDevice

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Broadlink capture events."""
    device: BroadlinkDevice = hass.data[DOMAIN].devices[config_entry.entry_id]
    async_add_entities(
        BroadlinkCaptureEvent(device, kind) for kind in capture_kinds(device)
    )
