"""Config flow and options flow for Prad."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    CONF_CONSUMERS,
    CONF_EXPORT,
    CONF_IMPORT,
    CONF_PV,
    CONF_TEMP,
    DOMAIN,
    RESERVED_SLUGS,
)

CONF_NAME = "name"
CONF_ENTITY_ID = "entity_id"
CONF_REMOVE = "remove"

_SENSOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


class PradConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup: pick the four source sensors."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PradOptionsFlow:
        return PradOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Prąd", data=user_input)
        schema = vol.Schema(
            {
                vol.Required(CONF_IMPORT): _SENSOR,
                vol.Required(CONF_EXPORT): _SENSOR,
                vol.Required(CONF_PV): _SENSOR,
                vol.Required(CONF_TEMP): _SENSOR,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)


class PradOptionsFlow(OptionsFlow):
    """Manage the list of consumers."""

    def __init__(self) -> None:
        self._consumers: list[dict[str, str]] | None = None
        self._removed: list[str] = []

    def _items(self) -> list[dict[str, str]]:
        if self._consumers is None:
            self._consumers = [dict(c) for c in self.config_entry.options.get(CONF_CONSUMERS, [])]
        return self._consumers

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = ["add_consumer"]
        if self._items():
            options.append("remove_consumer")
        options.append("finish")
        return self.async_show_menu(step_id="init", menu_options=options)

    async def async_step_add_consumer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input[CONF_NAME]).strip()
            slug = slugify(name)
            if not slug or slug in RESERVED_SLUGS or any(slugify(c["name"]) == slug for c in self._items()):
                errors[CONF_NAME] = "invalid_name"
            else:
                self._items().append({"name": name, "entity_id": user_input[CONF_ENTITY_ID]})
                return await self.async_step_init()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): selector.TextSelector(),
                vol.Required(CONF_ENTITY_ID): _SENSOR,
            }
        )
        return self.async_show_form(step_id="add_consumer", data_schema=schema, errors=errors)

    async def async_step_remove_consumer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        items = self._items()
        if user_input is not None:
            slug = user_input[CONF_REMOVE]
            self._consumers = [c for c in items if slugify(c["name"]) != slug]
            self._removed.append(slug)
            return await self.async_step_init()
        choices = [
            selector.SelectOptionDict(value=slugify(c["name"]), label=c["name"]) for c in items
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_REMOVE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=choices, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                )
            }
        )
        return self.async_show_form(step_id="remove_consumer", data_schema=schema)

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        registry = er.async_get(self.hass)
        remaining = {slugify(c["name"]) for c in self._items()}
        for slug in self._removed:
            if slug in remaining:
                continue
            entity_id = f"sensor.prad_mc_odb_{slug}"
            if registry.async_get(entity_id) is not None:
                registry.async_remove(entity_id)
        return self.async_create_entry(data={CONF_CONSUMERS: self._items()})
