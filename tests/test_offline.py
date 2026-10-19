"""Tests for a Broadlink that stops answering.

The built-in integration keeps its entry loaded when the device stops
answering and makes its entities unavailable instead; BroadSniff follows
those entities. Opening a window asks the device first, and a window the
remote stops answering ends within a couple of requests rather than
running out its deadline as a quiet window.
"""

import logging
from typing import Any

from broadlink_async.exceptions import ConnectionClosedError, NetworkTimeoutError
from broadlink_async.remote import CapturedSignal, pulses_to_data
import pytest

from custom_components.broadsniff.capture import WINDOW_REQUEST_TIMEOUT, CaptureKind
from custom_components.broadsniff.const import DOMAIN
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from . import (
    ENTRANCE,
    GARAGE,
    POLL,
    FakeApis,
    FakeWindow,
    add_core_entities,
    add_remote,
    set_core_state,
    setup_capture,
)

RECEIVER = "infrared.garage_ir_receiver"
IR_SWITCH = "switch.garage_ir_capture"
RF_SWITCH = "switch.garage_rf_capture"
IR_PACKET = pulses_to_data([9000, 4500, 560, 560, 560, 1690, 560, 40000])
TIMEOUT = NetworkTimeoutError(-4000, "Network timeout", "No response")


@pytest.fixture(autouse=True)
def _broadlink(broadlink: None) -> None:
    """Use the built-in integration stub in every test."""


async def _setup(
    hass: HomeAssistant, fake_apis: FakeApis, **core: Any
) -> tuple[FakeWindow, list[str]]:
    """Set up both remotes, the garage with built-in entities, and BroadSniff."""
    garage = await add_remote(hass, GARAGE)
    await add_remote(hass, ENTRANCE)
    core_entities = add_core_entities(hass, garage, **core)
    await setup_capture(hass)
    return fake_apis.windows[GARAGE.mac], core_entities


async def _call(hass: HomeAssistant, domain: str, service: str, **data: Any) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await hass.async_block_till_done()


async def _multiple(hass: HomeAssistant) -> None:
    """Keep windows open after a code, so a test can poll inside one."""
    await _call(
        hass,
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        entity_id="select.garage_capture_mode",
        **{ATTR_OPTION: "multiple"},
    )


def _garage_states(hass: HomeAssistant) -> dict[str, str]:
    """Return every BroadSniff entity on the garage remote that has a state."""
    states = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform != DOMAIN or not entry.unique_id.startswith(GARAGE.mac):
            continue
        if (state := hass.states.get(entry.entity_id)) is not None:
            states[entry.entity_id] = state.state
    return states


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and record.name.startswith("custom_components.broadsniff")
    ]


# Availability follows the device


async def test_entities_follow_the_built_in_entities(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Unplugged, every BroadSniff entity goes unavailable; back, they return."""
    _window, core_entities = await _setup(hass, fake_apis)
    before = _garage_states(hass)
    assert len(before) >= 9
    assert STATE_UNAVAILABLE not in before.values()

    set_core_state(hass, core_entities, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert set(_garage_states(hass).values()) == {STATE_UNAVAILABLE}
    # The other remote is not affected.
    assert hass.states.get("switch.entrance_ir_capture").state != STATE_UNAVAILABLE

    set_core_state(hass, core_entities, "on")
    await hass.async_block_till_done()
    assert STATE_UNAVAILABLE not in _garage_states(hass).values()


async def test_one_answering_entity_is_enough(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Only all of them unavailable means the device stopped answering."""
    _window, core_entities = await _setup(hass, fake_apis)

    set_core_state(hass, core_entities[:1], STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    assert STATE_UNAVAILABLE not in _garage_states(hass).values()


async def test_disabled_built_in_entities_fall_back_to_entry_loaded(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """With nothing enabled to follow, the entry being loaded decides."""
    _window, core_entities = await _setup(hass, fake_apis, disabled=True)
    # A stale state left behind by a disabled entity is not followed.
    set_core_state(hass, core_entities, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    assert STATE_UNAVAILABLE not in _garage_states(hass).values()


async def test_built_in_entities_without_state_fall_back(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Before the built-in entities have a state, the entry decides."""
    await _setup(hass, fake_apis, state=None)

    assert STATE_UNAVAILABLE not in _garage_states(hass).values()


async def test_renamed_built_in_entity_is_still_followed(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """A rename in the registry moves the following with it."""
    _window, core_entities = await _setup(hass, fake_apis, platforms=("remote",))
    er.async_get(hass).async_update_entity(
        core_entities[0], new_entity_id="remote.workshop"
    )
    await hass.async_block_till_done()

    set_core_state(hass, ["remote.workshop"], STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    assert set(_garage_states(hass).values()) == {STATE_UNAVAILABLE}


# Opening a window asks the device first


@pytest.mark.parametrize("switch", [IR_SWITCH, RF_SWITCH])
async def test_turn_on_against_an_unreachable_remote(
    hass: HomeAssistant, fake_apis: FakeApis, switch: str
) -> None:
    """The probe times out: the call fails and no window opens."""
    window, _core = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    api.update.side_effect = TIMEOUT

    with pytest.raises(HomeAssistantError, match="Capture failed"):
        await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=switch)

    api.update.assert_awaited_once()
    assert window.calls == []
    assert hass.states.get(switch).state == STATE_OFF
    assert hass.states.get(RECEIVER).attributes["capturing"] is False
    assert hass.states.get(RECEIVER).attributes["window_ends_at"] is None


@pytest.mark.parametrize("switch", [IR_SWITCH, RF_SWITCH])
async def test_turn_on_against_an_unavailable_remote(
    hass: HomeAssistant, fake_apis: FakeApis, switch: str
) -> None:
    """An unavailable remote is refused without a request.

    Home Assistant does not call a service on an unavailable entity at
    all; the capture manager refuses on its own as well, for any caller
    that reaches it directly or in the moment before the switch is
    written.
    """
    window, core_entities = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    set_core_state(hass, core_entities, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=switch)
    assert hass.states.get(switch).state == STATE_UNAVAILABLE

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    remote = next(
        r for r in entry.runtime_data.remotes.values() if r.mac_address == GARAGE.mac
    )
    kind = CaptureKind.IR if switch == IR_SWITCH else CaptureKind.RF
    with pytest.raises(HomeAssistantError, match="Garage is not answering"):
        await remote.capture.async_start(kind)

    api.auth.assert_not_awaited()
    api.update.assert_not_awaited()
    assert window.calls == []
    assert remote.capture.kind is None


async def test_the_probe_reopens_a_forgotten_session(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """After a power loss the device refuses the old session: reauth, retry."""
    window, _core = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    api.update.side_effect = [
        ConnectionClosedError(-2, "You have been logged out", ""),
        None,
    ]

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)

    assert api.auth.await_count == 2
    assert api.update.await_count == 2
    assert len(window.calls) == 1
    assert hass.states.get(IR_SWITCH).state == STATE_ON
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=IR_SWITCH)


async def test_the_probe_refuses_when_the_reauth_does_not_help(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """One reauth, one retry, then the refusal."""
    window, _core = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    api.update.side_effect = ConnectionClosedError(-2, "You have been logged out", "")

    with pytest.raises(HomeAssistantError, match="Capture failed"):
        await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)

    assert api.update.await_count == 2
    assert window.calls == []


async def test_a_remote_that_comes_back_opens_a_new_session(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Back from unavailable, the next window authorises again."""
    window, core_entities = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=IR_SWITCH)
    assert api.auth.await_count == 1

    set_core_state(hass, core_entities, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    set_core_state(hass, core_entities, "on")
    await hass.async_block_till_done()

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)
    assert api.auth.await_count == 2
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=IR_SWITCH)


# A window that fails partway closes at once


@pytest.mark.parametrize(
    ("switch", "event"),
    [
        (IR_SWITCH, "event.garage_ir_capture"),
        (RF_SWITCH, "event.garage_rf_capture"),
    ],
)
async def test_a_window_whose_remote_stops_answering_closes(
    hass: HomeAssistant,
    fake_apis: FakeApis,
    caplog: pytest.LogCaptureFixture,
    switch: str,
    event: str,
) -> None:
    """Two unanswered requests in a row end the window and publish it."""
    window, _core = await _setup(hass, fake_apis)
    await _multiple(hass)
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=switch)
    window.send(CapturedSignal.from_packet(IR_PACKET))
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == STATE_ON

    fake_apis.apis[GARAGE.mac].raw_send.side_effect = TIMEOUT
    window.send(POLL)
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == STATE_ON  # one miss is forgiven
    window.send(POLL)
    await hass.async_block_till_done()

    assert hass.states.get(switch).state == STATE_OFF
    assert hass.states.get(switch).attributes["window_ends_at"] is None
    receiver = hass.states.get(RECEIVER).attributes
    assert receiver["capturing"] is False
    assert receiver["window_ends_at"] is None
    ended = hass.states.get(event).attributes
    assert ended["event_type"] == "window_ended"
    assert ended["signals"] == 1
    assert ended["stopped"] is False
    assert _warnings(caplog) == [
        "Capture window on Garage failed: the remote stopped answering"
    ]
    assert window.closed == 1


async def test_a_missed_request_then_an_answer_keeps_the_window(
    hass: HomeAssistant, fake_apis: FakeApis, caplog: pytest.LogCaptureFixture
) -> None:
    """Misses count only in a row."""
    window, _core = await _setup(hass, fake_apis)
    await _multiple(hass)
    raw_send = fake_apis.apis[GARAGE.mac].raw_send
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)

    for side_effect in (TIMEOUT, None, TIMEOUT, None):
        raw_send.side_effect = side_effect
        window.send(POLL)
        await hass.async_block_till_done()

    assert hass.states.get(IR_SWITCH).state == STATE_ON
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=IR_SWITCH)
    assert _warnings(caplog) == []


async def test_a_deadline_reached_without_an_answer_is_a_failure(
    hass: HomeAssistant, fake_apis: FakeApis, caplog: pytest.LogCaptureFixture
) -> None:
    """The library ends such a window quietly; BroadSniff says why."""
    window, _core = await _setup(hass, fake_apis)
    await _multiple(hass)
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)

    fake_apis.apis[GARAGE.mac].raw_send.side_effect = TIMEOUT
    window.send(POLL)
    window.send(None)  # the window's own deadline
    await hass.async_block_till_done()

    assert hass.states.get(IR_SWITCH).state == STATE_OFF
    assert hass.states.get("event.garage_ir_capture").attributes["stopped"] is False
    assert _warnings(caplog) == [
        "Capture window on Garage failed: the remote stopped answering"
    ]


async def test_a_quiet_window_is_not_a_failure(
    hass: HomeAssistant, fake_apis: FakeApis, caplog: pytest.LogCaptureFixture
) -> None:
    """Every request answered and no code: an ordinary empty window."""
    window, _core = await _setup(hass, fake_apis)
    await _multiple(hass)
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)

    window.send(POLL)
    window.send(None)
    await hass.async_block_till_done()

    assert hass.states.get(IR_SWITCH).state == STATE_OFF
    assert _warnings(caplog) == []


async def test_going_unavailable_closes_an_open_window(
    hass: HomeAssistant, fake_apis: FakeApis, caplog: pytest.LogCaptureFixture
) -> None:
    """The built-in entities go unavailable: the window closes at once."""
    window, core_entities = await _setup(hass, fake_apis)
    await _multiple(hass)
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)

    set_core_state(hass, core_entities, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    assert window.closed == 1
    assert hass.states.get(IR_SWITCH).state == STATE_UNAVAILABLE
    # The event entity is unavailable too; the window's end is shown once
    # the remote is back.
    set_core_state(hass, core_entities, "on")
    await hass.async_block_till_done()
    assert hass.states.get(IR_SWITCH).state == STATE_OFF
    assert hass.states.get(RECEIVER).attributes["capturing"] is False
    ended = hass.states.get("event.garage_ir_capture").attributes
    assert ended["event_type"] == "window_ended"
    assert ended["stopped"] is False
    assert _warnings(caplog) == [
        "Capture window on Garage failed: the remote is unavailable"
    ]


async def test_the_window_uses_a_short_request_timeout(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Short while a window is open; the configured timeout otherwise."""
    window, _core = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    assert api.timeout == 10

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)
    assert api.timeout == WINDOW_REQUEST_TIMEOUT

    window.send(None)
    await hass.async_block_till_done()
    assert api.timeout == 10
