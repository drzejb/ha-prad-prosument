"""Config flow and options flow for Prad."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    CONF_CONSUMERS,
    CONF_EXPORT,
    CONF_GRID_MODE,
    CONF_IMPORT,
    CONF_NET,
    CONF_PV,
    CONF_START,
    CONF_TEMP,
    DOMAIN,
    GRID_NET,
    GRID_TWO,
    RESERVED_SLUGS,
)
from .logic.zones import G11, TAURON_G12W, ZoneSchedule, make_schedule

_LOGGER = logging.getLogger(__name__)

CONF_NAME = "name"
CONF_ENTITY_ID = "entity_id"
CONF_REMOVE = "remove"

ROZKLADY: tuple[str, ...] = ("tauron_g12w", "g11", "wlasne_g12", "wlasne_g12w")
_METER_KEYS = (CONF_GRID_MODE, CONF_IMPORT, CONF_EXPORT, CONF_NET, CONF_PV, CONF_TEMP)
_ENERGY_UNITS = ("kWh", "Wh", "MWh")
_RATIO_OTHER = "inny"

_SENSOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
_HOUR = selector.NumberSelector(
    selector.NumberSelectorConfig(min=0, max=23, step=1, mode=selector.NumberSelectorMode.BOX)
)


def _optional(key: str, defaults: Mapping[str, Any]) -> vol.Optional:
    value = defaults.get(key)
    if value is not None and value != "":
        return vol.Optional(key, description={"suggested_value": value})
    return vol.Optional(key)


def meters_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Form for the meters; empty optional fields are absent from user_input."""
    return vol.Schema(
        {
            vol.Required(CONF_GRID_MODE, default=defaults.get(CONF_GRID_MODE, GRID_TWO)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[GRID_TWO, GRID_NET],
                    translation_key="tryb_sieci",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            _optional(CONF_IMPORT, defaults): _SENSOR,
            _optional(CONF_EXPORT, defaults): _SENSOR,
            _optional(CONF_NET, defaults): _SENSOR,
            _optional(CONF_PV, defaults): _SENSOR,
            _optional(CONF_TEMP, defaults): _SENSOR,
        }
    )


def validate_meters(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (data to store, errors). Only the fields of the chosen mode and PV are checked."""
    mode = user_input.get(CONF_GRID_MODE, GRID_TWO)
    errors: dict[str, str] = {}
    data: dict[str, Any] = {CONF_GRID_MODE: mode}
    if mode == GRID_NET:
        if not user_input.get(CONF_NET):
            errors["base"] = "grid_required_netto"
        grid_keys = [CONF_NET]
    else:
        if not user_input.get(CONF_IMPORT) or not user_input.get(CONF_EXPORT):
            errors["base"] = "grid_required_dwa"
        grid_keys = [CONF_IMPORT, CONF_EXPORT]
    for key in (*grid_keys, CONF_PV):
        entity_id = user_input.get(key)
        if not entity_id:
            continue
        data[key] = entity_id
        state = hass.states.get(entity_id)
        unit = state.attributes.get("unit_of_measurement") if state is not None else None
        if state is None or state.state in ("unknown", "unavailable") or not unit:
            _LOGGER.warning("Energy sensor %s has no unit yet; accepted without validation", entity_id)
            continue
        if unit not in _ENERGY_UNITS:
            errors[key] = "not_energy"
        elif key == CONF_NET and state.attributes.get("state_class") == "total_increasing":
            errors[key] = "net_state_class"
    if user_input.get(CONF_TEMP):
        data[CONF_TEMP] = user_input[CONF_TEMP]
    return data, errors


def zones_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Form for own G12/G12w hours."""
    fields: dict[Any, Any] = {}
    for i in (1, 2, 3):
        for edge in ("od", "do"):
            key = f"tanie{i}_{edge}"
            if i == 1:
                if defaults.get(key) is not None:
                    marker: vol.Marker = vol.Required(key, description={"suggested_value": defaults[key]})
                else:
                    marker = vol.Required(key)
            else:
                marker = _optional(key, defaults)
            fields[marker] = _HOUR
    fields[vol.Required("weekend", default=defaults.get("weekend", "robocze"))] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=["tania", "robocze"], translation_key="weekend", mode=selector.SelectSelectorMode.DROPDOWN
        )
    )
    fields[vol.Required("swieta", default=defaults.get("swieta", "weekend"))] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=["weekend", "robocze"], translation_key="swieta", mode=selector.SelectSelectorMode.DROPDOWN
        )
    )
    return vol.Schema(fields)


def schedule_from_form(rozklad: str, user_input: Mapping[str, Any] | None) -> ZoneSchedule:
    """Build the zone schedule for the chosen layout; ValueError on bad hours."""
    if rozklad == "tauron_g12w":
        return TAURON_G12W
    if rozklad == "g11":
        return G11
    data = user_input or {}
    ranges: list[tuple[int, int]] = []
    for i in (1, 2, 3):
        od, do = data.get(f"tanie{i}_od"), data.get(f"tanie{i}_do")
        if od is None and do is None:
            continue
        if od is None or do is None:
            raise ValueError("Podaj początek i koniec przedziału")
        ranges.append((int(od), int(do)))
    taryfa = "G12w" if rozklad == "wlasne_g12w" else "G12"
    return make_schedule(
        "wlasne", "Własne godziny", taryfa, ranges,
        data.get("weekend", "robocze"), data.get("swieta", "weekend"),
    )


def _taryfa_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("rozklad", default=defaults.get("rozklad", "tauron_g12w")): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(ROZKLADY), translation_key="rozklad", mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            # No translation_key: option values with a dot cannot be translation keys.
            vol.Required("opust", default=defaults.get("opust", "0.8")): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="0.8", label="0,8"),
                        selector.SelectOptionDict(value="0.7", label="0,7"),
                        selector.SelectOptionDict(value=_RATIO_OTHER, label="Inny"),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            _optional("opust_inny", defaults): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=1, step=0.01, mode=selector.NumberSelectorMode.BOX)
            ),
        }
    )


class PradConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup: meters, then tariff layout and net-metering ratio."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._meters: dict[str, Any] = {}
        self._start: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PradOptionsFlow:
        return PradOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        defaults: Mapping[str, Any] = {}
        if user_input is not None:
            data, errors = validate_meters(self.hass, user_input)
            if not errors:
                self._meters = data
                return await self.async_step_taryfa()
            defaults = user_input
        return self.async_show_form(step_id="user", data_schema=meters_schema(defaults), errors=errors)

    async def async_step_taryfa(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        defaults: Mapping[str, Any] = {}
        if user_input is not None:
            opust = user_input["opust"]
            if opust == _RATIO_OTHER:
                if user_input.get("opust_inny") is None:
                    errors["opust_inny"] = "ratio_required"
                else:
                    opust = str(Decimal(str(user_input["opust_inny"])))
            if not errors:
                rozklad = user_input["rozklad"]
                self._start = {"rozklad": rozklad, "strefy": None, "opust": opust}
                if rozklad.startswith("wlasne"):
                    return await self.async_step_strefy()
                return self._create()
            defaults = user_input
        return self.async_show_form(step_id="taryfa", data_schema=_taryfa_schema(defaults), errors=errors)

    async def async_step_strefy(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        defaults: dict[str, Any] = {
            "weekend": "tania" if self._start["rozklad"] == "wlasne_g12w" else "robocze",
        }
        if user_input is not None:
            try:
                schedule = schedule_from_form(self._start["rozklad"], user_input)
            except ValueError as err:
                errors["base"] = "zones_invalid"
                placeholders["blad"] = str(err)
                defaults = dict(user_input)
            else:
                self._start["strefy"] = schedule.to_dict()
                return self._create()
        return self.async_show_form(
            step_id="strefy", data_schema=zones_schema(defaults), errors=errors,
            description_placeholders=placeholders,
        )

    def _create(self) -> ConfigFlowResult:
        return self.async_create_entry(title="Prąd", data={**self._meters, CONF_START: dict(self._start)})


class PradOptionsFlow(OptionsFlow):
    """Installation settings and the list of consumers."""

    def __init__(self) -> None:
        self._consumers: list[dict[str, str]] | None = None
        self._removed: list[str] = []

    def _items(self) -> list[dict[str, str]]:
        if self._consumers is None:
            self._consumers = [dict(c) for c in self.config_entry.options.get(CONF_CONSUMERS, [])]
        return self._consumers

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = ["instalacja", "add_consumer"]
        if self._items():
            options.append("remove_consumer")
        options.append("finish")
        return self.async_show_menu(step_id="init", menu_options=options)

    async def async_step_instalacja(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="instalacja", menu_options=["liczniki"])

    async def async_step_liczniki(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        defaults: Mapping[str, Any] = self.config_entry.data
        if user_input is not None:
            data, errors = validate_meters(self.hass, user_input)
            if not errors:
                new_data = {k: v for k, v in self.config_entry.data.items() if k not in _METER_KEYS}
                new_data.update(data)
                self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                return self._finish()
            defaults = user_input
        return self.async_show_form(step_id="liczniki", data_schema=meters_schema(defaults), errors=errors)

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
        return self._finish()

    def _finish(self) -> ConfigFlowResult:
        registry = er.async_get(self.hass)
        remaining = {slugify(c["name"]) for c in self._items()}
        for slug in self._removed:
            if slug in remaining:
                continue
            entity_id = f"sensor.prad_mc_odb_{slug}"
            if registry.async_get(entity_id) is not None:
                registry.async_remove(entity_id)
        return self.async_create_entry(data={**self.config_entry.options, CONF_CONSUMERS: self._items()})
