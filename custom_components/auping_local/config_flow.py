"""Einrichtung: bestehende Kopplung aus der Auping-App übernehmen.

In der Auping-App unter "Konfiguration teilen" stehen IP-Adresse und ein
8-stelliger Code (der X-Key). Der QR-Code dort enthält "<ip>-<code>". Beides
kann hier eingegeben werden – das Bett muss nicht neu angelernt werden.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import AupingAuthError, AupingConnectApi, AupingError, parse_share_code
from .const import (
    ALL_MOTORS,
    CONF_HOST,
    CONF_MAC,
    CONF_MATTRESS,
    CONF_MAX_DIFF,
    CONF_MODE,
    CONF_MOTORS,
    CONF_NAME_A,
    CONF_NAME_B,
    CONF_REMOTE,
    CONF_SIDES,
    CONF_XKEY,
    DEFAULT_MAX_DIFF,
    DOMAIN,
    MATTRESS_DUAL,
    MATTRESS_SINGLE,
    MODE_PARALLEL,
    MODE_SEQUENTIAL,
    MOTOR_HEAD,
    MOTOR_BACK,
    MOTOR_INDEX,
    MOTOR_LEGS,
    MOTOR_NAMES,
    REMOTE_REGULAR,
    REMOTE_ROYAL,
    SIDE_A,
    SIDE_B,
    motors_for_bed_type,
    sides_for_cbu_type,
)

_LOGGER = logging.getLogger(__name__)

CONF_CODE = "code"
SIDES_ONE = "one"
SIDES_TWO = "two"
KEY_RE = re.compile(r"^[a-z0-9]{4,32}$")


async def _fetch_config(hass, host: str, key: str) -> dict[str, Any]:
    api = AupingConnectApi(async_get_clientsession(hass), host, key, REMOTE_REGULAR)
    return await api.get_config()


def _bed_schema(defaults: dict[str, Any]) -> vol.Schema:
    remote = defaults.get(CONF_REMOTE, REMOTE_REGULAR)
    motor_options = [
        SelectOptionDict(value=m, label=MOTOR_NAMES[m]) for m in ALL_MOTORS if m in MOTOR_INDEX[remote]
    ]
    return vol.Schema(
        {
            vol.Required(CONF_MOTORS, default=defaults[CONF_MOTORS]): SelectSelector(
                SelectSelectorConfig(options=motor_options, multiple=True, mode=SelectSelectorMode.LIST)
            ),
            vol.Required("sides_count", default=defaults["sides_count"]): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=SIDES_TWO, label="Zwei Seiten (Steuereinheit A und B)"),
                        SelectOptionDict(value=SIDES_ONE, label="Eine Seite (nur Steuereinheit A)"),
                    ],
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Required(CONF_MATTRESS, default=defaults[CONF_MATTRESS]): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(
                            value=MATTRESS_SINGLE, label="Eine durchgehende Matratze (Seiten begrenzt verstellbar)"
                        ),
                        SelectOptionDict(value=MATTRESS_DUAL, label="Zwei Einzelmatratzen (Seiten frei verstellbar)"),
                    ],
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Required(CONF_MAX_DIFF, default=defaults[CONF_MAX_DIFF]): NumberSelector(
                NumberSelectorConfig(min=1, max=50, step=1, unit_of_measurement="%", mode=NumberSelectorMode.SLIDER)
            ),
            vol.Required(CONF_NAME_A, default=defaults[CONF_NAME_A]): TextSelector(),
            vol.Required(CONF_NAME_B, default=defaults[CONF_NAME_B]): TextSelector(),
            vol.Required(CONF_MODE, default=defaults[CONF_MODE]): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=MODE_PARALLEL, label="Gleichzeitig (Standard)"),
                        SelectOptionDict(
                            value=MODE_SEQUENTIAL,
                            label="Nacheinander (falls die Box nicht beide Seiten zugleich bedient)",
                        ),
                    ],
                    mode=SelectSelectorMode.LIST,
                )
            ),
        }
    )


def _bed_options(user_input: dict[str, Any]) -> dict[str, Any]:
    motors = [m for m in ALL_MOTORS if m in user_input[CONF_MOTORS]]
    return {
        CONF_MOTORS: motors,
        CONF_SIDES: [SIDE_A, SIDE_B] if user_input["sides_count"] == SIDES_TWO else [SIDE_A],
        CONF_NAME_A: user_input[CONF_NAME_A].strip() or "Links",
        CONF_NAME_B: user_input[CONF_NAME_B].strip() or "Rechts",
        CONF_MODE: user_input[CONF_MODE],
        CONF_MATTRESS: user_input[CONF_MATTRESS],
        CONF_MAX_DIFF: int(user_input[CONF_MAX_DIFF]),
    }


class AupingConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._key: str | None = None
        self._config: dict[str, Any] = {}

    async def _validate(self, host: str, key: str) -> dict[str, str]:
        errors: dict[str, str] = {}
        if not KEY_RE.match(key):
            errors[CONF_CODE] = "invalid_code"
            return errors
        try:
            self._config = await _fetch_config(self.hass, host, key)
        except AupingAuthError:
            errors["base"] = "invalid_auth"
        except AupingError as err:
            _LOGGER.debug("Box %s nicht erreichbar: %s", host, err)
            errors["base"] = "cannot_connect"
        return errors

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            code_host, key = parse_share_code(user_input[CONF_CODE])
            host = (code_host or user_input.get(CONF_HOST, "")).strip()
            if not host:
                errors[CONF_HOST] = "no_host"
            else:
                errors = await self._validate(host, key)
            if not errors:
                self._host, self._key = host, key
                mac = self._config.get("mac_address")
                if mac:
                    await self.async_set_unique_id(format_mac(mac))
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_XKEY: key})
                return await self.async_step_bed()

        schema = vol.Schema(
            {
                vol.Optional(CONF_HOST, default=self._host or ""): str,
                vol.Required(CONF_CODE): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        if "auping connect" not in discovery_info.name.lower():
            return self.async_abort(reason="not_auping")
        host = discovery_info.host
        for entry in self._async_current_entries():
            if entry.data.get(CONF_HOST) == host:
                return self.async_abort(reason="already_configured")
        await self.async_set_unique_id(f"host_{host}")
        self._abort_if_unique_id_configured()
        self._host = host
        self.context["title_placeholders"] = {"name": discovery_info.name.split(".")[0]}
        return await self.async_step_user()

    async def async_step_bed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        bed_type = int(self._config.get("bed_type", 0) or 0)
        remote, left, right = motors_for_bed_type(bed_type)
        remote = remote or REMOTE_REGULAR
        if user_input is not None:
            options = _bed_options(user_input)
            if not options[CONF_MOTORS]:
                return self.async_show_form(
                    step_id="bed",
                    data_schema=_bed_schema({**user_input, CONF_REMOTE: remote}),
                    errors={CONF_MOTORS: "no_motors"},
                )
            return self.async_create_entry(
                title="Auping Bett",
                data={
                    CONF_HOST: self._host,
                    CONF_XKEY: self._key,
                    CONF_MAC: self._config.get("mac_address"),
                    CONF_REMOTE: remote,
                },
                options=options,
            )
        motors = [m for m in ALL_MOTORS if m in set(left) | set(right)] or [MOTOR_HEAD, MOTOR_BACK, MOTOR_LEGS]
        sides = sides_for_cbu_type(int(self._config.get("cbu_type", 0) or 0))
        defaults = {
            CONF_REMOTE: remote,
            CONF_MOTORS: motors,
            "sides_count": SIDES_TWO if len(sides) == 2 else SIDES_ONE,
            CONF_NAME_A: "Links",
            CONF_NAME_B: "Rechts",
            CONF_MODE: MODE_PARALLEL,
            # Die Box kennt "eine Matratze, zwei Steuereinheiten" als sync = 1.
            CONF_MATTRESS: MATTRESS_SINGLE if self._config.get("sync") == 1 else MATTRESS_DUAL,
            CONF_MAX_DIFF: DEFAULT_MAX_DIFF,
        }
        return self.async_show_form(
            step_id="bed",
            data_schema=_bed_schema(defaults),
            description_placeholders={
                "bed_type": str(bed_type),
                "remote": "Royal" if remote == REMOTE_ROYAL else "Standard",
                "firmware": str(self._config.get("firmware", "?")),
                "sync": "ja" if self._config.get("sync") == 1 else "nein",
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            code_host, key = parse_share_code(user_input[CONF_CODE])
            host = code_host or entry.data[CONF_HOST]
            errors = await self._validate(host, key)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host, CONF_XKEY: key}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AupingOptionsFlow:
        return AupingOptionsFlow()


class AupingOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        merged = {**entry.data, **entry.options}
        remote = entry.data.get(CONF_REMOTE, REMOTE_REGULAR)
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input.pop(CONF_HOST).strip()
            options = _bed_options(user_input)
            if not options[CONF_MOTORS]:
                errors[CONF_MOTORS] = "no_motors"
            else:
                if host and host != entry.data[CONF_HOST]:
                    self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: host})
                    if options == dict(entry.options):
                        # Nur die Adresse geändert: Home Assistant lädt dann nicht von selbst neu.
                        self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(data=options)
        defaults = {
            CONF_REMOTE: remote,
            CONF_MOTORS: merged.get(CONF_MOTORS, [MOTOR_HEAD, MOTOR_BACK, MOTOR_LEGS]),
            "sides_count": SIDES_TWO if len(merged.get(CONF_SIDES, [SIDE_A, SIDE_B])) == 2 else SIDES_ONE,
            CONF_NAME_A: merged.get(CONF_NAME_A, "Links"),
            CONF_NAME_B: merged.get(CONF_NAME_B, "Rechts"),
            CONF_MODE: merged.get(CONF_MODE, MODE_PARALLEL),
            CONF_MATTRESS: merged.get(CONF_MATTRESS, MATTRESS_DUAL),
            CONF_MAX_DIFF: merged.get(CONF_MAX_DIFF, DEFAULT_MAX_DIFF),
        }
        schema = vol.Schema(
            {vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str, **_bed_schema(defaults).schema}
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
