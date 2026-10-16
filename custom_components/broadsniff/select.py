"""Capture settings chosen from a list, for Broadlink remotes."""

from __future__ import annotations

from enum import StrEnum
from typing import override

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import BroadSniffConfigEntry
from .capture import (
    DEFAULT_CAPTURE_MODE,
    DEFAULT_RF_FREQUENCY,
    CaptureMode,
    RFFrequency,
)
from .device import BroadlinkRemote
from .entity import BroadSniffEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BroadSniffConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the capture mode and RF frequency selects."""
    selects: list[BroadSniffSelect] = []
    for remote in entry.runtime_data.remotes.values():
        selects.append(BroadSniffCaptureModeSelect(remote))
        if remote.supports_rf:
            selects.append(BroadSniffRFFrequencySelect(remote))
    async_add_entities(selects)


class BroadSniffSelect(BroadSniffEntity, SelectEntity, RestoreEntity):
    """A capture setting chosen from a list, restored across restarts."""

    _attr_entity_category = EntityCategory.CONFIG
    _choices: type[StrEnum]
    _default: StrEnum
    _setting: str

    def __init__(self, device: BroadlinkRemote) -> None:
        """Initialize the select."""
        super().__init__(device)
        self._attr_unique_id = f"{device.unique_id}-{self._attr_translation_key}"
        self._attr_options = [choice.value for choice in self._choices]
        self._attr_current_option = self._default.value

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last choice and hand it to the capture manager."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self.options:
            self._apply(last_state.state)

    @override
    async def async_select_option(self, option: str) -> None:
        """Use the option for the next window."""
        self._apply(option)
        self.async_write_ha_state()

    def _apply(self, option: str) -> None:
        """Set the choice on the capture manager and on the entity."""
        setattr(self._device.capture, self._setting, self._choices(option))
        self._attr_current_option = option


class BroadSniffCaptureModeSelect(BroadSniffSelect):
    """Whether a capture window takes one code or several."""

    _attr_translation_key = "capture_mode"
    _choices = CaptureMode
    _default = DEFAULT_CAPTURE_MODE
    _setting = "mode"


class BroadSniffRFFrequencySelect(BroadSniffSelect):
    """The carrier an RF capture window listens on.

    Naming the carrier is faster and more reliable than sweeping for it;
    custom uses the value on the RF custom frequency entity.
    """

    _attr_translation_key = "rf_frequency"
    _choices = RFFrequency
    _default = DEFAULT_RF_FREQUENCY
    _setting = "rf_frequency"
