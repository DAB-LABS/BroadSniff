"""Capture counters for Broadlink remotes."""

from __future__ import annotations

from typing import override

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BroadSniffConfigEntry
from .device import BroadlinkRemote
from .entity import BroadSniffCaptureEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BroadSniffConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the codes captured sensors."""
    async_add_entities(
        BroadSniffCodesCapturedSensor(remote)
        for remote in entry.runtime_data.remotes.values()
    )


class BroadSniffCodesCapturedSensor(BroadSniffCaptureEntity, SensorEntity):
    """How many codes the open window has caught.

    It goes back to zero when a window opens, counts up with each code, and
    keeps the total once the window closes, so the device page shows a
    capture happening as it happens.
    """

    _attr_translation_key = "codes_captured"

    def __init__(self, device: BroadlinkRemote) -> None:
        """Initialize the sensor."""
        super().__init__(device)
        self._attr_unique_id = f"{device.unique_id}-codes_captured"

    @property
    @override
    def native_value(self) -> int:
        """Return the codes caught by the open or the last window."""
        return self._device.capture.codes
