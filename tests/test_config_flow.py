"""Tests for the BroadSniff config and options flows."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.broadsniff.const import CONF_EXCLUDED, DOMAIN

from . import BEDROOM, ENTRANCE, GARAGE, FakeApis, add_remote, setup_capture


async def test_user_flow(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """The flow names the remotes it will cover and creates one entry."""
    await add_remote(hass, GARAGE)
    await add_remote(hass, ENTRANCE)
    await add_remote(hass, BEDROOM)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"] == {"remotes": "Entrance, Garage"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "BroadSniff"
    assert result["data"] == {}


async def test_user_flow_without_remotes(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """The flow stops when Broadlink has no remotes."""
    await add_remote(hass, BEDROOM)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_remotes"


async def test_single_entry(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """Only one entry can be set up; it covers every remote."""
    await add_remote(hass, GARAGE)
    await setup_capture(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow(
    hass: HomeAssistant, broadlink: None, fake_apis: FakeApis
) -> None:
    """Remotes can be left out."""
    garage = await add_remote(hass, GARAGE)
    await add_remote(hass, ENTRANCE)
    entry = await setup_capture(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_EXCLUDED: [garage.entry_id]}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_EXCLUDED: [garage.entry_id]}
    assert hass.states.get("switch.garage_ir_capture") is None
    assert hass.states.get("switch.entrance_ir_capture") is not None
