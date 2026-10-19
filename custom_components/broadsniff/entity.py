"""Base entity for BroadSniff."""

from __future__ import annotations

from typing import override

from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .device import BroadlinkRemote


class BroadSniffEntity(Entity):
    """An entity on a remote that the built-in integration set up.

    Each integration has its own device in the device registry. This one
    shares the MAC address with the built-in integration's device, so Home
    Assistant lists the two as linked devices, and starts with its name and
    area. The entity is available while the built-in integration has the
    device loaded and its entities on the device are not all unavailable,
    which is how the built-in integration says the device stopped
    answering.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: BroadlinkRemote) -> None:
        """Initialize the entity."""
        self._device = device
        self._attr_device_info = DeviceInfo(
            connections={(dr.CONNECTION_NETWORK_MAC, device.mac_address)},
            manufacturer=device.api.manufacturer,
            model=device.api.model,
            name=device.name,
            suggested_area=device.area_name,
        )

    @property
    @override
    def available(self) -> bool:
        """Return True while the device answers the built-in integration."""
        return self._device.available

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the built-in config entry and the device's availability."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._on_entry_changed
            )
        )
        self.async_on_remove(
            self._device.async_add_availability_listener(self.async_write_ha_state)
        )

    @callback
    def _on_entry_changed(self, change: ConfigEntryChange, entry: ConfigEntry) -> None:
        """Update availability when the built-in entry loads or unloads."""
        if (
            change is ConfigEntryChange.UPDATED
            and entry.entry_id == self._device.core_entry.entry_id
        ):
            self.async_write_ha_state()


class BroadSniffCaptureEntity(BroadSniffEntity):
    """An entity that follows the device's capture windows."""

    @override
    async def async_added_to_hass(self) -> None:
        """Follow window changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._device.capture.async_add_state_listener(self.async_write_ha_state)
        )
