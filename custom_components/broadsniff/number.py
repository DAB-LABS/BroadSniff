"""Capture settings for Broadlink remotes."""

from __future__ import annotations

from typing import override

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfFrequency, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BroadSniffConfigEntry
from .const import (
    DEFAULT_CAPTURE_WINDOW,
    DEFAULT_RF_CUSTOM_FREQUENCY,
    MAX_CAPTURE_WINDOW,
    MAX_RF_FREQUENCY,
    MIN_CAPTURE_WINDOW,
    MIN_RF_FREQUENCY,
)
from .device import BroadlinkRemote
from .entity import BroadSniffEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BroadSniffConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the capture settings."""
    numbers: list[BroadSniffNumber] = []
    for remote in entry.runtime_data.remotes.values():
        numbers.append(BroadSniffCaptureWindowNumber(remote))
        if remote.supports_rf:
            numbers.append(BroadSniffRFCustomFrequencyNumber(remote))
    async_add_entities(numbers)


class BroadSniffNumber(BroadSniffEntity, RestoreNumber):
    """A capture setting, restored across restarts."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _setting: str

    def __init__(self, device: BroadlinkRemote) -> None:
        """Initialize the number."""
        super().__init__(device)
        self._attr_unique_id = f"{device.unique_id}-{self._attr_translation_key}"

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last value and hand it to the capture manager."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            # A value saved under an older, wider range comes back clamped.
            self._apply(
                min(
                    max(last.native_value, self.native_min_value), self.native_max_value
                )
            )

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Store the value."""
        self._apply(value)
        self.async_write_ha_state()

    def _apply(self, value: float) -> None:
        """Set the value on the capture manager and on the entity."""
        setattr(self._device.capture, self._setting, value)
        self._attr_native_value = value


class BroadSniffCaptureWindowNumber(BroadSniffNumber):
    """How long a capture window stays open, in seconds."""

    _attr_translation_key = "capture_window"
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = MIN_CAPTURE_WINDOW
    _attr_native_max_value = MAX_CAPTURE_WINDOW
    _attr_native_step = 1
    _attr_native_value = DEFAULT_CAPTURE_WINDOW
    _setting = "window"


class BroadSniffRFCustomFrequencyNumber(BroadSniffNumber):
    """The carrier used when RF frequency is set to custom.

    Disabled by default; most remotes use one of the preset carriers.
    """

    _attr_translation_key = "rf_custom_frequency"
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfFrequency.MEGAHERTZ
    _attr_native_min_value = MIN_RF_FREQUENCY
    _attr_native_max_value = MAX_RF_FREQUENCY
    _attr_native_step = 0.01
    _attr_native_value = DEFAULT_RF_CUSTOM_FREQUENCY
    _setting = "rf_custom_frequency"
