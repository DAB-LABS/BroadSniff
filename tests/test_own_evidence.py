"""Tests for going offline on BroadSniff's own evidence (round 3).

The built-in integration takes minutes to give up on an unplugged
remote. When BroadSniff's own request goes unanswered, before a window or
inside one, a confirmation probe asks again 3 seconds later at the
entry's full timeout; only a second silence takes the remote offline, so
one dropped exchange on Wi-Fi does not. Offline, a recovery probe asks
every 15 seconds until the remote answers or the built-in integration
says it is back. Every other request uses the short timeout, so a refused
turn-on is quick and says why.
"""

import json
from pathlib import Path
from typing import Any

from broadlink_async.exceptions import (
    AuthenticationError,
    ConnectionClosedError,
    NetworkTimeoutError,
)
from broadlink_async.remote import CapturedSignal, pulses_to_data
import pytest

from custom_components.broadsniff.capture import REQUEST_TIMEOUT
from custom_components.broadsniff.const import DOMAIN
from custom_components.broadsniff.device import CONFIRM_DELAY, RECOVERY_PROBE_INTERVAL
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    CONF_TIMEOUT,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

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

from pytest_homeassistant_custom_component.common import async_fire_time_changed

RECEIVER = "infrared.garage_ir_receiver"
IR_SWITCH = "switch.garage_ir_capture"
RF_SWITCH = "switch.garage_rf_capture"
IR_PACKET = pulses_to_data([9000, 4500, 560, 560, 560, 1690, 560, 40000])
TIMEOUT = NetworkTimeoutError(-4000, "Network timeout", "No response")


@pytest.fixture(autouse=True)
def _broadlink(broadlink: None) -> None:
    """Use the built-in integration stub in every test."""


async def _setup(
    hass: HomeAssistant, fake_apis: FakeApis, timeout: int | None = None
) -> tuple[FakeWindow, list[str]]:
    """Both remotes, built-in entities on the garage, then BroadSniff."""
    garage = await add_remote(hass, GARAGE)
    await add_remote(hass, ENTRANCE)
    if timeout is not None:
        hass.config_entries.async_update_entry(
            garage, data={**garage.data, CONF_TIMEOUT: timeout}
        )
    core_entities = add_core_entities(hass, garage)
    await setup_capture(hass)
    return fake_apis.windows[GARAGE.mac], core_entities


async def _call(hass: HomeAssistant, domain: str, service: str, **data: Any) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await hass.async_block_till_done()


async def _tick(hass: HomeAssistant) -> None:
    """Let one recovery interval pass."""
    async_fire_time_changed(hass, dt_util.utcnow() + RECOVERY_PROBE_INTERVAL)
    await hass.async_block_till_done()


async def _confirm(hass: HomeAssistant) -> None:
    """Let the confirmation delay pass, so the confirmation probe runs."""
    async_fire_time_changed(hass, dt_util.utcnow() + CONFIRM_DELAY)
    await hass.async_block_till_done()


def _garage_states(hass: HomeAssistant) -> set[str]:
    """Return the states of every BroadSniff entity on the garage remote."""
    states = set()
    for entry in er.async_get(hass).entities.values():
        if entry.platform != DOMAIN or not entry.unique_id.startswith(GARAGE.mac):
            continue
        if (state := hass.states.get(entry.entity_id)) is not None:
            states.add(state.state)
    return states


async def _refused(hass: HomeAssistant, switch: str = IR_SWITCH) -> HomeAssistantError:
    """Turn capture on and return the error it was refused with."""
    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_ON, {"entity_id": switch}, blocking=True
        )
    await hass.async_block_till_done()
    return refused.value


async def _go_offline(hass: HomeAssistant, fake_apis: FakeApis) -> Any:
    """Take the garage offline: a refused probe, then a failed confirmation."""
    api = fake_apis.apis[GARAGE.mac]
    api.update.side_effect = TIMEOUT
    await _refused(hass)
    await _confirm(hass)
    assert api.update.await_count == 2
    assert _garage_states(hass) == {STATE_UNAVAILABLE}
    return api


# 1. Offline on BroadSniff's own evidence


@pytest.mark.parametrize("switch", [IR_SWITCH, RF_SWITCH])
async def test_two_failures_take_the_remote_offline(
    hass: HomeAssistant, fake_apis: FakeApis, switch: str
) -> None:
    """A refused probe, then a failed confirmation: offline, before the
    built-in integration has noticed anything."""
    window, core_entities = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    api.update.side_effect = TIMEOUT

    await _refused(hass, switch)
    assert STATE_UNAVAILABLE not in _garage_states(hass)

    await _confirm(hass)

    assert api.update.await_count == 2
    assert hass.states.get(core_entities[0]).state == "on"
    assert _garage_states(hass) == {STATE_UNAVAILABLE}
    assert hass.states.get(RECEIVER).state == STATE_UNAVAILABLE
    assert window.calls == []
    assert hass.states.get("switch.entrance_ir_capture").state == STATE_OFF


async def test_one_failure_and_a_good_confirmation_stay_online(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """A dropped exchange: refused once, but the remote stays available."""
    window, _core = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    api.update.side_effect = [TIMEOUT, None]

    await _refused(hass)
    await _confirm(hass)

    assert api.update.await_count == 2
    assert STATE_UNAVAILABLE not in _garage_states(hass)
    # No recovery probe runs for a remote that stayed online.
    for _ in range(2):
        await _tick(hass)
    assert api.update.await_count == 2
    # And the next turn-on works.
    api.update.side_effect = None
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)
    assert len(window.calls) == 1
    window.send(None)
    await hass.async_block_till_done()


async def test_the_refusal_does_not_wait_for_the_confirmation(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """The caller hears at once; the confirmation runs 3 seconds later."""
    await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    api.update.side_effect = TIMEOUT

    refused = await _refused(hass)

    assert str(refused) == "Capture failed: Garage appears disconnected"
    assert api.update.await_count == 1  # the confirmation has not run yet
    await _confirm(hass)
    assert api.update.await_count == 2


async def test_the_confirmation_uses_the_entry_timeout(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """The second asking is patient: the built-in entry's own timeout."""
    await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    seen: list[float] = []

    async def silent() -> None:
        seen.append(api.timeout)
        raise TIMEOUT

    api.update.side_effect = silent
    await _refused(hass)
    await _confirm(hass)

    assert seen == [REQUEST_TIMEOUT, 10]
    assert api.timeout == REQUEST_TIMEOUT


@pytest.mark.parametrize("switch", [IR_SWITCH, RF_SWITCH])
@pytest.mark.parametrize("silent", [True, False], ids=["silent", "answers"])
async def test_a_window_that_loses_the_remote(
    hass: HomeAssistant,
    fake_apis: FakeApis,
    caplog: pytest.LogCaptureFixture,
    switch: str,
    silent: bool,
) -> None:
    """The window ends and reports failed either way; only a silent
    confirmation takes the remote offline."""
    window, _core = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    await _call(
        hass,
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        entity_id="select.garage_capture_mode",
        **{ATTR_OPTION: "multiple"},
    )
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=switch)
    window.send(CapturedSignal.from_packet(IR_PACKET))
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == STATE_ON

    api.raw_send.side_effect = TIMEOUT
    window.send(POLL)
    window.send(POLL)
    await hass.async_block_till_done()

    assert window.closed == 1
    assert hass.states.get(switch).state == STATE_OFF
    assert "Capture window on Garage failed: the remote stopped answering" in (
        caplog.text
    )
    if silent:
        api.update.side_effect = TIMEOUT
    await _confirm(hass)
    if silent:
        assert _garage_states(hass) == {STATE_UNAVAILABLE}
        assert hass.states.get(RECEIVER).state == STATE_UNAVAILABLE
    else:
        assert STATE_UNAVAILABLE not in _garage_states(hass)
        assert hass.states.get(RECEIVER).attributes["capturing"] is False


async def test_a_refusal_the_remote_answered_does_not_take_it_offline(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """A locked remote refuses the session: it answers, so it is online."""
    await _setup(hass, fake_apis)
    fake_apis.apis[GARAGE.mac].auth.side_effect = AuthenticationError(
        -1, "Authentication failed", "Locked"
    )

    refused = await _refused(hass)

    # A genuine device error keeps capture_failed and its own text.
    assert refused.translation_key == "capture_failed"
    assert "Authentication failed" in str(refused)
    assert STATE_UNAVAILABLE not in _garage_states(hass)


async def test_the_built_in_going_unavailable_is_not_own_evidence(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """No recovery probe runs on the built-in integration's word alone."""
    _window, core_entities = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    set_core_state(hass, core_entities, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    for _ in range(3):
        await _tick(hass)

    api.update.assert_not_awaited()


# 2. Come back sooner


async def test_the_recovery_probe_asks_every_15_seconds(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Silence keeps it offline; the first answer brings it back."""
    await _setup(hass, fake_apis)
    api = await _go_offline(hass, fake_apis)
    assert api.update.await_count == 2

    await _tick(hass)
    assert api.update.await_count == 3
    assert _garage_states(hass) == {STATE_UNAVAILABLE}

    api.update.side_effect = None
    await _tick(hass)
    assert api.update.await_count == 4
    assert STATE_UNAVAILABLE not in _garage_states(hass)

    # Back online: no more probing.
    for _ in range(3):
        await _tick(hass)
    assert api.update.await_count == 4


async def test_an_answer_that_is_an_error_still_counts(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """A remote that refuses the old session answers; the next window logs in."""
    window, _core = await _setup(hass, fake_apis)
    api = await _go_offline(hass, fake_apis)
    auths = api.auth.await_count

    api.update.side_effect = ConnectionClosedError(-2, "You have been logged out", "")
    await _tick(hass)
    assert STATE_UNAVAILABLE not in _garage_states(hass)

    api.update.side_effect = None
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)
    assert api.auth.await_count == auths + 1
    assert len(window.calls) == 1
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, entity_id=IR_SWITCH)


async def test_the_built_in_recovery_also_brings_it_back(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Whichever comes first: here the built-in integration says it is back."""
    _window, core_entities = await _setup(hass, fake_apis)
    api = await _go_offline(hass, fake_apis)

    set_core_state(hass, core_entities, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    set_core_state(hass, core_entities, "on")
    await hass.async_block_till_done()

    assert STATE_UNAVAILABLE not in _garage_states(hass)
    calls = api.update.await_count
    for _ in range(3):
        await _tick(hass)
    assert api.update.await_count == calls  # the probe stopped


async def test_no_probing_while_online(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """An answering remote is never asked on a timer."""
    await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]

    for _ in range(4):
        await _tick(hass)

    api.update.assert_not_awaited()


async def test_the_recovery_probe_uses_the_short_timeout(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """The probe that brings a remote back is as quick as the others."""
    await _setup(hass, fake_apis)
    api = await _go_offline(hass, fake_apis)
    seen: list[float] = []

    async def answer() -> None:
        seen.append(api.timeout)

    api.update.side_effect = answer
    await _tick(hass)

    assert seen == [REQUEST_TIMEOUT]


# 3. A faster, visible refusal


async def test_the_probe_before_a_window_uses_the_short_timeout(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """1.5 seconds, not the built-in entry's own timeout (10 here)."""
    window, _core = await _setup(hass, fake_apis)
    api = fake_apis.apis[GARAGE.mac]
    seen: list[float] = []

    async def answer() -> None:
        seen.append(api.timeout)

    api.update.side_effect = answer
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, entity_id=IR_SWITCH)

    assert seen == [REQUEST_TIMEOUT]
    window.send(None)
    await hass.async_block_till_done()


async def test_a_shorter_entry_timeout_is_kept(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """An entry timeout below 1.5 seconds is not raised to it."""
    await _setup(hass, fake_apis, timeout=1)

    assert fake_apis.apis[GARAGE.mac].timeout == 1


async def test_the_refusal_says_why(hass: HomeAssistant, fake_apis: FakeApis) -> None:
    """A translated refusal carrying only the remote's name, not a raw error."""
    await _setup(hass, fake_apis)
    fake_apis.apis[GARAGE.mac].update.side_effect = TIMEOUT

    refused = await _refused(hass)

    assert refused.translation_domain == DOMAIN
    assert refused.translation_key == "capture_disconnected"
    assert refused.translation_placeholders == {"name": "Garage"}
    assert str(refused) == "Capture failed: Garage appears disconnected"


async def test_offline_refuses_with_its_own_wording(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Once offline the switch is unavailable; a direct caller is told so."""
    await _setup(hass, fake_apis)
    await _go_offline(hass, fake_apis)

    assert hass.states.get(IR_SWITCH).state == STATE_UNAVAILABLE


async def test_while_offline_capture_is_refused_without_a_request(
    hass: HomeAssistant, fake_apis: FakeApis
) -> None:
    """Offline on its own evidence, the remote is not asked again on turn-on."""
    window, _core = await _setup(hass, fake_apis)
    api = await _go_offline(hass, fake_apis)
    calls = api.update.await_count

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    remote = next(
        r for r in entry.runtime_data.remotes.values() if r.mac_address == GARAGE.mac
    )
    with pytest.raises(HomeAssistantError) as refused:
        await remote.capture.async_start(remote.capture_kinds[0])

    # The same refusal as the probe's: one translation key, the name only.
    assert refused.value.translation_key == "capture_disconnected"
    assert refused.value.translation_placeholders == {"name": "Garage"}
    assert str(refused.value) == "Capture failed: Garage appears disconnected"
    assert api.update.await_count == calls
    assert window.calls == []


def test_strings_json_matches_the_english_translation() -> None:
    """strings.json and translations/en.json say the same thing.

    The integration ships both; the refusal wording lives in them, not in
    code, so a translator works from one and a reader can trust the other.
    """
    base = Path(__file__).parent.parent / "custom_components" / DOMAIN
    strings = json.loads((base / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (base / "translations" / "en.json").read_text(encoding="utf-8")
    )
    assert strings == english
    assert english["exceptions"]["capture_disconnected"]["message"] == (
        "Capture failed: {name} appears disconnected"
    )
    assert english["exceptions"]["capture_failed"]["message"] == (
        "Capture failed: {error}"
    )
