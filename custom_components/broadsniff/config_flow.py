"""Config flow for BroadSniff."""

from __future__ import annotations

from typing import Any, override

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .const import CONF_EXCLUDED, DOMAIN
from .device import find_device, remote_entries


def _remote_options(hass: HomeAssistant) -> list[SelectOptionDict]:
    """Return the remotes in the Broadlink integration, by name."""
    options = []
    for core_entry in remote_entries(hass):
        device = find_device(hass, core_entry)
        name = (device.name_by_user or device.name) if device else None
        options.append(
            SelectOptionDict(value=core_entry.entry_id, label=name or core_entry.title)
        )
    return sorted(options, key=lambda option: option["label"].casefold())


class BroadSniffConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add capture to the remotes in the Broadlink integration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, once at least one remote is set up in Broadlink."""
        remotes = _remote_options(self.hass)
        if not remotes:
            return self.async_abort(reason="no_remotes")
        if user_input is not None:
            return self.async_create_entry(title="BroadSniff", data={})
        return self.async_show_form(
            step_id="user",
            description_placeholders={
                "remotes": ", ".join(remote["label"] for remote in remotes)
            },
        )

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return BroadSniffOptionsFlow()


class BroadSniffOptionsFlow(OptionsFlow):
    """Choose remotes to leave out."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the remotes that should not get capture entities."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = _remote_options(self.hass)
        known = {option["value"] for option in options}
        excluded = [
            entry_id
            for entry_id in self.config_entry.options.get(CONF_EXCLUDED, [])
            if entry_id in known
        ]
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_EXCLUDED, default=excluded): SelectSelector(
                        SelectSelectorConfig(options=options, multiple=True)
                    )
                }
            ),
        )
