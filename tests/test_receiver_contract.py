"""Tests for the IR receiver's windowed-receiver attributes.

The receiver tells other integrations, HAIR among them, that it captures
on demand: capturing says whether a window is open, capture_switch names
the switch that opens one, window_ends_at says when the open window
closes, and capture_mode is the mode the next window opens in.
"""

from typing import Any

from broadlink_async.remote import CapturedSignal, pulses_to_data
import pytest

from custom_components.broadsniff.const import DOMAIN
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from . import ENTRANCE, GARAGE, FakeApis, FakeWindow, add_remote, setup_capture

from pytest_homeassistant_custom_component.common import mock_restore_cache

RECEIVER = "infrared.garage_ir_receiver"
SWITCH = "switch.garage_ir_capture"
MODE = "select.garage_capture_mode"

IR_PACKET = pulses_to_data([9000, 4500, 560, 560, 560, 1690, 560, 40000])


@pytest.fixture(autouse=True)
def _broadlink(broadlink: None) -> None:
    """Use the built-in integration stub in every test."""


async def _setup(hass: HomeAssistant, fake_apis: FakeApis) -> FakeWindow:
    """Set up the garage remote (RM4 pro) and the entrance (RM mini 3)."""
    await add_remote(hass, GARAGE)
    await add_remote(hass, ENTRANCE)
    await setup_capture(hass)
    return fake_apis.windows[GARAGE.mac]


async def _call(hass: HomeAssistant, domain: str, service: str, **data: Any) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await hass.async_block_till_done()


async def _set_mode(hass: HomeAssistant, mode: str) -> None:
    await _call(
        hass,
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        entity_id=MODE,
        **{ATTR_OPTION: mode},
    )


def _attributes(hass: HomeAssistant, entity_id: str = RECEIVER) -> dict[str, Any]:
    return dict(hass.states.get(entity_id).attributes)


async def test_attributes_from_startup(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """All four are there before any window opens, on every remote."""
    await _setup(hass, fake_apis)

    attributes = _attributes(hass)
    assert attributes["capturing"] is False
    assert attributes["window_ends_at"] is None
    assert attributes["capture_mode"] == "sliding"
    assert attributes["capture_switch"] == SWITCH

    entrance = _attributes(hass, "infrared.entrance_ir_receiver")
    assert entrance["capturing"] is False
    assert entrance["capture_switch"] == "switch.entrance_ir_capture"


@pytest.mark.parametrize("mode", ["single", "multiple", "sliding"])
async def test_capture_mode_follows_the_select(
    hass: HomeAssistant, fake_apis: FakeApis, mode: str
) -> None:
    """A mode change reaches the receiver with no window open."""
    await _setup(hass, fake_apis)
    if mode == "sliding":
        # The default; move away first so the change back is a change.
        await _set_mode(hass, "single")
        assert _attributes(hass)["capture_mode"] == "single"

    await _set_mode(hass, mode)

    assert _attributes(hass)["capture_mode"] == mode
    assert _attributes(hass)["capturing"] is False


async def test_capture_mode_is_restored(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """After a restart the receiver reports the restored mode."""
    mock_restore_cache(hass, [State(MODE, "multiple")])
    await _setup(hass, fake_apis)

    assert _attributes(hass)["capture_mode"] == "multiple"


async def test_attributes_during_an_ir_window(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """While the IR window is open all four describe it."""
    window = await _setup(hass, fake_apis)
    await _set_mode(hass, "multiple")

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=SWITCH)

    attributes = _attributes(hass)
    assert attributes["capturing"] is True
    assert attributes["window_ends_at"] is not None
    assert attributes["capture_mode"] == "multiple"
    assert attributes["capture_switch"] == SWITCH

    window.send(CapturedSignal.from_packet(IR_PACKET))
    await hass.async_block_till_done()
    assert _attributes(hass)["capturing"] is True

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=SWITCH)
    attributes = _attributes(hass)
    assert attributes["capturing"] is False
    assert attributes["window_ends_at"] is None
    assert attributes["capture_switch"] == SWITCH


async def test_an_rf_window_is_not_capturing(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """The receiver is IR only, so it names the IR switch during RF."""
    await _setup(hass, fake_apis)

    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_rf_capture"
    )

    assert hass.states.get("switch.garage_rf_capture").state == STATE_ON
    attributes = _attributes(hass)
    assert attributes["capturing"] is False
    assert attributes["window_ends_at"] is None
    assert attributes["capture_switch"] == SWITCH

    await _call(
        hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id="switch.garage_rf_capture"
    )


async def test_renamed_switch(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """Renaming the switch reaches the receiver without a window."""
    await _setup(hass, fake_apis)
    registry = er.async_get(hass)

    registry.async_update_entity(SWITCH, new_entity_id="switch.garage_learn")
    await hass.async_block_till_done()

    assert _attributes(hass)["capture_switch"] == "switch.garage_learn"

    # The new ID still opens and closes the window, and stays reported.
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id="switch.garage_learn")
    assert _attributes(hass)["capturing"] is True
    assert _attributes(hass)["capture_switch"] == "switch.garage_learn"
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id="switch.garage_learn")
    assert _attributes(hass)["capture_switch"] == "switch.garage_learn"


async def test_failed_lookup_omits_the_switch(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """With no registry entry the attribute is left out, not left stale."""
    await _setup(hass, fake_apis)

    er.async_get(hass).async_remove(SWITCH)
    await hass.async_block_till_done()

    attributes = _attributes(hass)
    assert "capture_switch" not in attributes
    assert attributes["capturing"] is False
    assert attributes["window_ends_at"] is None
    assert attributes["capture_mode"] == "sliding"

    # The switch comes back with the integration and so does the attribute.
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _attributes(hass)["capture_switch"] == SWITCH


async def test_disabled_switch_is_left_out(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """A disabled switch cannot be turned on, so it is not offered."""
    await _setup(hass, fake_apis)
    registry = er.async_get(hass)

    registry.async_update_entity(SWITCH, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()

    attributes = _attributes(hass)
    assert "capture_switch" not in attributes
    assert attributes["capturing"] is False

    registry.async_update_entity(SWITCH, disabled_by=None)
    await hass.async_block_till_done()

    assert _attributes(hass)["capture_switch"] == SWITCH


async def test_hidden_switch_is_kept(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """A hidden switch still has state and still works."""
    await _setup(hass, fake_apis)

    er.async_get(hass).async_update_entity(SWITCH, hidden_by=er.RegistryEntryHider.USER)
    await hass.async_block_till_done()

    assert _attributes(hass)["capture_switch"] == SWITCH


async def test_other_registry_changes_leave_the_receiver_alone(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Renaming another remote's switch does not write this receiver."""
    await _setup(hass, fake_apis)
    before = hass.states.get(RECEIVER)

    er.async_get(hass).async_update_entity(
        "switch.entrance_ir_capture", new_entity_id="switch.entrance_learn"
    )
    await hass.async_block_till_done()

    assert hass.states.get(RECEIVER).last_updated == before.last_updated
    assert (
        _attributes(hass, "infrared.entrance_ir_receiver")["capture_switch"]
        == "switch.entrance_learn"
    )


async def test_unique_ids_are_unchanged(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Every entity keeps its 0.1.0 unique ID, so an install upgrades in place."""
    await _setup(hass, fake_apis)

    unique_ids = {
        entry.unique_id
        for entry in er.async_get(hass).entities.values()
        if entry.platform == DOMAIN
    }

    garage = [
        "ir_capture",
        "rf_capture",
        "ir_capture_event",
        "rf_capture_event",
        "receiver",
        "codes_captured",
        "capture_window",
        "capture_mode",
        "rf_frequency",
        "rf_custom_frequency",
    ]
    entrance = [
        "ir_capture",
        "ir_capture_event",
        "receiver",
        "codes_captured",
        "capture_window",
        "capture_mode",
    ]
    assert unique_ids == {f"{GARAGE.mac}-{suffix}" for suffix in garage} | {
        f"{ENTRANCE.mac}-{suffix}" for suffix in entrance
    }
