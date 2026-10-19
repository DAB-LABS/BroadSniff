"""BroadSniff: capture entities for remotes in the Broadlink integration."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import logging

from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
    ConfigEntryState,
)
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_TIMEOUT, CONF_TYPE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import BROADLINK_DOMAIN, CONF_EXCLUDED, PLATFORMS
from .device import BroadlinkRemote, find_device, remote_entries

_LOGGER = logging.getLogger(__name__)


@dataclass
class BroadSniffData:
    """The remotes this entry adds capture to, by built-in entry id."""

    remotes: dict[str, BroadlinkRemote]


type BroadSniffConfigEntry = ConfigEntry[BroadSniffData]


def _snapshot(
    hass: HomeAssistant, entry: ConfigEntry
) -> tuple[tuple[object, ...], ...]:
    """Return what the set of remotes depends on, to spot changes."""
    excluded = set(entry.options.get(CONF_EXCLUDED, []))
    return tuple(
        sorted(
            (
                core_entry.entry_id,
                core_entry.data.get(CONF_HOST),
                core_entry.data.get(CONF_MAC),
                core_entry.data.get(CONF_TYPE),
                core_entry.data.get(CONF_TIMEOUT),
                core_entry.entry_id in excluded,
                find_device(hass, core_entry) is not None,
            )
            for core_entry in remote_entries(hass)
        )
    )


async def async_setup_entry(hass: HomeAssistant, entry: BroadSniffConfigEntry) -> bool:
    """Add capture to every remote the built-in integration has set up."""
    excluded = set(entry.options.get(CONF_EXCLUDED, []))
    remotes: dict[str, BroadlinkRemote] = {}
    for core_entry in remote_entries(hass):
        if core_entry.entry_id in excluded:
            continue
        # The built-in integration registers the device the first time the
        # entry loads. Until then there is nothing to attach entities to;
        # the entry is reloaded when that happens.
        if (device := find_device(hass, core_entry)) is None:
            _LOGGER.debug("%s is not registered yet", core_entry.title)
            continue
        remotes[core_entry.entry_id] = BroadlinkRemote(
            hass, entry, core_entry, device.id
        )
    entry.runtime_data = BroadSniffData(remotes)
    _async_remove_stale_devices(hass, entry, remotes.values())
    for remote in remotes.values():
        remote.async_start()
        entry.async_on_unload(remote.async_stop_following)

    snapshot = _snapshot(hass, entry)

    @callback
    def _on_entry_changed(change: ConfigEntryChange, changed: ConfigEntry) -> None:
        """Reload when remotes are added, removed, moved or first registered."""
        if changed.domain != BROADLINK_DOMAIN:
            return
        if entry.state is not ConfigEntryState.LOADED:
            return
        if _snapshot(hass, entry) != snapshot:
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _on_entry_changed)
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def _async_remove_stale_devices(
    hass: HomeAssistant,
    entry: BroadSniffConfigEntry,
    remotes: Iterable[BroadlinkRemote],
) -> None:
    """Remove the devices of remotes that were left out or removed."""
    device_registry = dr.async_get(hass)
    covered = {
        (dr.CONNECTION_NETWORK_MAC, dr.format_mac(remote.mac_address))
        for remote in remotes
    }
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if not device.connections & covered:
            device_registry.async_remove_device(device.id)


async def _async_options_updated(
    hass: HomeAssistant, entry: BroadSniffConfigEntry
) -> None:
    """Reload after the excluded remotes change."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BroadSniffConfigEntry) -> bool:
    """Close every capture session."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for remote in entry.runtime_data.remotes.values():
            await remote.async_unload()
    return unloaded
