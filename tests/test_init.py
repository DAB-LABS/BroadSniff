"""Tests for how BroadSniff attaches to the Broadlink integration."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from custom_components.broadsniff.const import (
    BROADLINK_DOMAIN,
    CONF_EXCLUDED,
    DOMAIN,
)

from . import BEDROOM, ENTRANCE, GARAGE, FakeApis, add_remote, setup_capture

GARAGE_ENTITIES = {
    "switch.garage_ir_capture",
    "switch.garage_rf_capture",
    "select.garage_capture_mode",
    "sensor.garage_codes_captured",
    "number.garage_capture_window",
    "number.garage_rf_custom_frequency",
    "select.garage_rf_frequency",
    "event.garage_ir_capture",
    "event.garage_rf_capture",
    "infrared.garage_ir_receiver",
}
ENTRANCE_ENTITIES = {
    "switch.entrance_ir_capture",
    "select.entrance_capture_mode",
    "sensor.entrance_codes_captured",
    "number.entrance_capture_window",
    "event.entrance_ir_capture",
    "infrared.entrance_ir_receiver",
}


def _capture_entities(hass: HomeAssistant, entry_id: str) -> set[str]:
    return {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
    }


def _devices(hass: HomeAssistant, mac: str) -> dict[str, dr.DeviceEntry]:
    """Return the devices with this MAC, by the domain of their config entry."""
    return {
        hass.config_entries.async_get_entry(device.config_entry_id).domain: device
        for device in dr.async_get(hass).async_get_devices(
            connections={(dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))}
        )
    }


async def test_entities_on_a_linked_device(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """Remotes get capture entities on a device linked by MAC; plugs get none."""
    for remote in (GARAGE, ENTRANCE, BEDROOM):
        await add_remote(hass, remote)
    dr.async_get(hass).async_update_device(
        _devices(hass, GARAGE.mac)[BROADLINK_DOMAIN].id, name_by_user="Garage"
    )

    entry = await setup_capture(hass)

    assert (
        _capture_entities(hass, entry.entry_id) == GARAGE_ENTITIES | ENTRANCE_ENTITIES
    )
    garage = _devices(hass, GARAGE.mac)
    assert set(garage) == {BROADLINK_DOMAIN, DOMAIN}
    ours = garage[DOMAIN]
    assert ours.config_entry_id == entry.entry_id
    assert ours.name == "Garage"
    assert ours.manufacturer == "Broadlink"
    assert ours.model == "RM4 pro"
    assert set(_devices(hass, BEDROOM.mac)) == {BROADLINK_DOMAIN}
    entity_registry = er.async_get(hass)
    for entity_id in GARAGE_ENTITIES:
        assert entity_registry.async_get(entity_id).device_id == ours.id
    # No session is opened until a window is.
    fake_apis.apis[GARAGE.mac].auth.assert_not_called()


async def test_rf_custom_frequency_disabled_by_default(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """The custom frequency entity exists but starts disabled."""
    await add_remote(hass, GARAGE)
    await setup_capture(hass)
    entity = er.async_get(hass).async_get("number.garage_rf_custom_frequency")
    assert entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get("select.garage_rf_frequency").state == "mhz_433_92"


async def test_device_starts_in_the_same_area(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """The capture device starts in the area of the Broadlink device."""
    await add_remote(hass, GARAGE)
    area = ar.async_get(hass).async_create("Workshop")
    dr.async_get(hass).async_update_device(
        _devices(hass, GARAGE.mac)[BROADLINK_DOMAIN].id, area_id=area.id
    )

    await setup_capture(hass)

    assert _devices(hass, GARAGE.mac)[DOMAIN].area_id == area.id


async def test_excluded_remote_gets_no_entities(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """A remote left out in the options gets no entities."""
    garage = await add_remote(hass, GARAGE)
    await add_remote(hass, ENTRANCE)

    entry = await setup_capture(hass, {CONF_EXCLUDED: [garage.entry_id]})

    assert _capture_entities(hass, entry.entry_id) == ENTRANCE_ENTITIES


async def test_new_remote_reloads(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """A remote added to Broadlink later gets entities without a restart."""
    await add_remote(hass, ENTRANCE)
    entry = await setup_capture(hass)
    assert _capture_entities(hass, entry.entry_id) == ENTRANCE_ENTITIES

    await add_remote(hass, GARAGE)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("switch.garage_ir_capture") is not None


async def test_options_change_reloads(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """Leaving a remote out in the options takes effect at once."""
    garage = await add_remote(hass, GARAGE)
    entry = await setup_capture(hass)
    assert hass.states.get("switch.garage_ir_capture") is not None

    hass.config_entries.async_update_entry(
        entry, options={CONF_EXCLUDED: [garage.entry_id]}
    )
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("switch.garage_ir_capture") is None


async def test_follows_broadlink_availability(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """Entities are unavailable while Broadlink has the device unloaded."""
    garage = await add_remote(hass, GARAGE)
    entry = await setup_capture(hass)

    assert await hass.config_entries.async_unload(garage.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("switch.garage_ir_capture").state == STATE_UNAVAILABLE
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_setup(garage.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("switch.garage_ir_capture").state != STATE_UNAVAILABLE


async def test_unload_closes_sessions(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """Unloading closes every capture session and leaves the device alone."""
    await add_remote(hass, GARAGE)
    entry = await setup_capture(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    fake_apis.apis[GARAGE.mac].aclose.assert_awaited_once()
    assert BROADLINK_DOMAIN in _devices(hass, GARAGE.mac)


async def test_removing_leaves_the_device(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """Removing BroadSniff removes its device, not the built-in one."""
    garage = await add_remote(hass, GARAGE)
    entry = await setup_capture(hass)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    devices = _devices(hass, GARAGE.mac)
    assert set(devices) == {BROADLINK_DOMAIN}
    assert devices[BROADLINK_DOMAIN].config_entry_id == garage.entry_id
    assert hass.states.get("switch.garage_ir_capture") is None
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_removed_remote_loses_its_device(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """A remote removed from Broadlink loses its capture device too."""
    garage = await add_remote(hass, GARAGE)
    await add_remote(hass, ENTRANCE)
    entry = await setup_capture(hass)

    assert await hass.config_entries.async_remove(garage.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert not _devices(hass, GARAGE.mac)
    assert hass.states.get("switch.garage_ir_capture") is None
    assert hass.states.get("switch.entrance_ir_capture") is not None
