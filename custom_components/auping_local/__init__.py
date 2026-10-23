"""Auping Bett (lokal): Steuerung der Auping-Connect-Box ohne Cloud, mit Sync-Kalibrierung."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import json
import logging
from pathlib import Path
import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components import persistent_notification
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import panel, websocket
from .discovery import find_box
from .api import AupingAuthError, AupingConnectApi, AupingError
from .const import (
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
    DOWN,
    MATTRESS_DUAL,
    MODE_PARALLEL,
    MOTOR_NAMES,
    REMOTE_REGULAR,
    SIDE_A,
    SIDE_B,
    STORAGE_VERSION,
    UP,
)
from .controller import BedController

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.COVER,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class AupingData:
    api: AupingConnectApi
    controller: BedController
    coordinator: DataUpdateCoordinator[dict[str, Any]]
    names: dict[str, str]
    device_info: DeviceInfo
    version: dict[str, Any]


type AupingConfigEntry = ConfigEntry[AupingData]


def entry_settings(entry: ConfigEntry) -> dict[str, Any]:
    merged = {**entry.data, **entry.options}
    return {
        CONF_SIDES: merged.get(CONF_SIDES, [SIDE_A, SIDE_B]),
        CONF_MOTORS: merged.get(CONF_MOTORS, ["head", "back", "legs"]),
        CONF_MODE: merged.get(CONF_MODE, MODE_PARALLEL),
        CONF_NAME_A: merged.get(CONF_NAME_A, "Links"),
        CONF_NAME_B: merged.get(CONF_NAME_B, "Rechts"),
        CONF_MATTRESS: merged.get(CONF_MATTRESS, MATTRESS_DUAL),
        CONF_MAX_DIFF: merged.get(CONF_MAX_DIFF, DEFAULT_MAX_DIFF),
    }


def apply_names(hass: HomeAssistant, entry: ConfigEntry, name_a: str | None, name_b: str | None) -> None:
    """Namen der Seiten sofort übernehmen (auch in den Entitäten) und in den Optionen merken."""
    names = entry.runtime_data.names
    options = dict(entry.options)
    for side, key, value in ((SIDE_A, CONF_NAME_A, name_a), (SIDE_B, CONF_NAME_B, name_b)):
        if value is None:
            continue
        value = " ".join(str(value).split())[:20]
        if not value:
            raise ValueError("Der Name darf nicht leer sein")
        names[side] = options[key] = value
    hass.config_entries.async_update_entry(entry, options=options)
    entry.runtime_data.controller._notify()  # noqa: SLF001


def apply_mattress(
    hass: HomeAssistant, entry: ConfigEntry, mode: str | None = None, max_diff: float | None = None
) -> None:
    """Matratzenmodus sofort übernehmen und in den Optionen merken (ohne Neuladen)."""
    ctl = entry.runtime_data.controller
    options = dict(entry.options)
    if mode is not None:
        ctl.mattress = options[CONF_MATTRESS] = mode
    if max_diff is not None:
        options[CONF_MAX_DIFF] = max(1, min(50, round(max_diff)))
        ctl.max_diff = options[CONF_MAX_DIFF] / 100
    hass.config_entries.async_update_entry(entry, options=options)
    ctl._notify()  # noqa: SLF001


class BoxFinder:
    """Sucht die Box im Heimnetz, wenn sie unter der gespeicherten Adresse nicht antwortet.

    Läuft nur nach Fehlern. Bleibt die Suche erfolglos (Box aus, Stecker gezogen),
    werden die Pausen bis zur nächsten Suche immer länger.
    """

    PAUSES_S = (60, 300, 900, 1800, 3600)

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: AupingConnectApi) -> None:
        self.hass = hass
        self.entry = entry
        self.api = api
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0
        self._failures = 0

    def reset(self) -> None:
        """Box antwortet wieder: beim nächsten Ausfall sofort suchen."""
        self._failures = 0
        self._next_allowed = 0.0

    async def search(self) -> bool:
        """True, wenn die Box unter einer neuen Adresse gefunden und übernommen wurde."""
        if self._lock.locked() or time.monotonic() < self._next_allowed:
            return False
        async with self._lock:
            extra: tuple[str, ...] = ()
            try:
                from homeassistant.components import network  # noqa: PLC0415

                extra = (await network.async_get_source_ip(self.hass),)
            except Exception:  # noqa: BLE001 – ohne Netzwerk-Info nur um die alte Adresse suchen
                pass
            old = self.api.host
            new = await find_box(
                async_get_clientsession(self.hass), old, self.entry.data[CONF_XKEY], self.entry.data.get(CONF_MAC), extra
            )
            if not new or new == old:
                pause = self.PAUSES_S[min(self._failures, len(self.PAUSES_S) - 1)]
                self._failures += 1
                self._next_allowed = time.monotonic() + pause
                _LOGGER.info("Auping-Box nicht gefunden – nächste Suche frühestens in %d s", pause)
                return False
            self.reset()
            self.api.host = new
            self.hass.config_entries.async_update_entry(self.entry, data={**self.entry.data, CONF_HOST: new})
            registry = dr.async_get(self.hass)
            for device in dr.async_entries_for_config_entry(registry, self.entry.entry_id):
                registry.async_update_device(device.id, configuration_url=f"http://{new}")
            persistent_notification.async_create(
                self.hass,
                f"Die Auping-Box hatte eine neue IP-Adresse und wurde unter **{new}** wiedergefunden "
                f"(vorher {old}). Die neue Adresse ist übernommen.\n\n"
                "Tipp: Im Router eine feste IP-Adresse für die Box vergeben.",
                title="Auping Bett: neue Adresse übernommen",
                notification_id=f"{DOMAIN}_{self.entry.entry_id}_new_host",
            )
            return True


def read_version() -> dict[str, Any]:
    """Version aus manifest.json und (falls vom Installationsskript erzeugt) version.json."""
    base = Path(__file__).parent
    info: dict[str, Any] = {}
    try:
        info["version"] = json.loads((base / "manifest.json").read_text())["version"]
        version_file = base / "version.json"
        if version_file.exists():
            info.update(json.loads(version_file.read_text()))
    except (OSError, ValueError, KeyError):
        pass
    return info


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    websocket.async_register(hass)
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AupingConfigEntry) -> bool:
    settings = entry_settings(entry)
    api = AupingConnectApi(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_XKEY],
        entry.data.get(CONF_REMOTE, REMOTE_REGULAR),
    )
    controller = BedController(
        api,
        settings[CONF_SIDES],
        settings[CONF_MOTORS],
        settings[CONF_MODE],
        mattress=settings[CONF_MATTRESS],
        max_diff=settings[CONF_MAX_DIFF] / 100,
    )
    store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    controller.import_state(await store.async_load())
    controller.set_save_callback(lambda: store.async_delay_save(controller.export_state, 2.0))
    controller.start()

    finder = BoxFinder(hass, entry, api)

    async def _update() -> dict[str, Any]:
        try:
            data = await api.get_config()
        except AupingAuthError as err:
            raise ConfigEntryAuthFailed("Der Kopplungsschlüssel wurde abgelehnt") from err
        except AupingError as err:
            # Nur jetzt (Box antwortet nicht) im Netz nach einer neuen Adresse suchen.
            if await finder.search():
                try:
                    return await api.get_config()
                except AupingError:
                    pass
            raise UpdateFailed(f"Box nicht erreichbar: {err}") from err
        finder.reset()
        return data

    coordinator: DataUpdateCoordinator[dict[str, Any]] = DataUpdateCoordinator(
        hass,
        _LOGGER,
        config_entry=entry,
        name=f"{DOMAIN}_{entry.entry_id}",
        update_method=_update,
        update_interval=timedelta(minutes=2),
    )
    # Schlägt ein Fahrbefehl fehl, sofort Status prüfen (und ggf. die Box suchen).
    controller.set_error_callback(lambda: hass.async_create_task(coordinator.async_request_refresh()))
    # Nicht blockieren: Ist die Box gerade aus, startet die Integration trotzdem.
    await coordinator.async_refresh()

    mac = entry.data.get(CONF_MAC)
    firmware = (coordinator.data or {}).get("firmware")
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        connections={(CONNECTION_NETWORK_MAC, mac)} if mac else set(),
        manufacturer="Auping",
        model="Connect (lokal gesteuert)",
        name=entry.title,
        sw_version=firmware,
        configuration_url=f"http://{entry.data[CONF_HOST]}",
    )
    entry.runtime_data = AupingData(
        api=api,
        controller=controller,
        coordinator=coordinator,
        names={SIDE_A: settings[CONF_NAME_A], SIDE_B: settings[CONF_NAME_B]},
        device_info=device_info,
        version=await hass.async_add_executor_job(read_version),
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await panel.async_register(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AupingConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.controller.async_shutdown()
        others = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id and e.state is ConfigEntryState.LOADED
        ]
        if not others:
            panel.async_unregister(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


# --------------------------------------------------------------------------- Dienste

SIDE_CHOICES = ["a", "b", "both"]
ENTRY_FIELD = vol.Optional("config_entry_id")


def get_entry(hass: HomeAssistant, entry_id: str | None) -> AupingConfigEntry:
    entries = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
    if len(entries) != 1:
        raise ServiceValidationError(
            "Bitte config_entry_id angeben" if entries else "Kein Auping-Bett geladen"
        )
    return entries[0]


def resolve_sides(controller: BedController, side: str) -> list[str]:
    if side == "both":
        return list(controller.sides)
    if side not in controller.sides:
        raise ServiceValidationError(f"Seite {side} ist nicht eingerichtet")
    return [side]


def _check_motor(controller: BedController, motor: str) -> None:
    if motor not in controller.motors:
        raise ServiceValidationError(
            f"Motor {motor} ist nicht eingerichtet (verfügbar: {', '.join(controller.motors)})"
        )


def _register_services(hass: HomeAssistant) -> None:
    motor_field = vol.In(list(MOTOR_NAMES))

    async def move_to(call: ServiceCall) -> None:
        ctl = get_entry(hass, call.data.get("config_entry_id")).runtime_data.controller
        _check_motor(ctl, call.data["motor"])
        x = call.data["position"] / 100
        await ctl.move_to({s: {call.data["motor"]: x} for s in resolve_sides(ctl, call.data["side"])})

    async def move(call: ServiceCall) -> None:
        ctl = get_entry(hass, call.data.get("config_entry_id")).runtime_data.controller
        _check_motor(ctl, call.data["motor"])
        direction = UP if call.data["direction"] == "up" else DOWN
        for side in resolve_sides(ctl, call.data["side"]):
            await ctl.nudge(side, call.data["motor"], direction, call.data["duration"])

    async def simple(call: ServiceCall) -> None:
        ctl = get_entry(hass, call.data.get("config_entry_id")).runtime_data.controller
        await {"stop": ctl.stop_all, "flat": ctl.flat, "home": ctl.home}[call.service]()

    async def goto_favorite(call: ServiceCall) -> None:
        ctl = get_entry(hass, call.data.get("config_entry_id")).runtime_data.controller
        if not await ctl.goto_favorite(call.data["favorite"], resolve_sides(ctl, call.data["side"])):
            raise ServiceValidationError(f"Favorit „{call.data['favorite']}“ gibt es nicht")

    async def save_favorite(call: ServiceCall) -> None:
        """Aktuelle Lage unter diesem Namen speichern (vorhandener Favorit wird überschrieben)."""
        ctl = get_entry(hass, call.data.get("config_entry_id")).runtime_data.controller
        try:
            ctl.save_favorite(call.data["name"], ctl.find_favorite(call.data["name"]))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def delete_favorite(call: ServiceCall) -> None:
        ctl = get_entry(hass, call.data.get("config_entry_id")).runtime_data.controller
        fav_id = ctl.find_favorite(call.data["favorite"])
        if fav_id is None:
            raise ServiceValidationError(f"Favorit „{call.data['favorite']}“ gibt es nicht")
        ctl.delete_favorite(fav_id)

    hass.services.async_register(
        DOMAIN,
        "move_to",
        move_to,
        vol.Schema(
            {
                ENTRY_FIELD: cv.string,
                vol.Required("side", default="both"): vol.In(SIDE_CHOICES),
                vol.Required("motor"): motor_field,
                vol.Required("position"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "move",
        move,
        vol.Schema(
            {
                ENTRY_FIELD: cv.string,
                vol.Required("side"): vol.In(SIDE_CHOICES),
                vol.Required("motor"): motor_field,
                vol.Required("direction"): vol.In(["up", "down"]),
                vol.Required("duration"): vol.All(vol.Coerce(int), vol.Range(min=50, max=60000)),
            }
        ),
    )
    for name in ("stop", "flat", "home"):
        hass.services.async_register(DOMAIN, name, simple, vol.Schema({ENTRY_FIELD: cv.string}))
    hass.services.async_register(
        DOMAIN,
        "goto_favorite",
        goto_favorite,
        vol.Schema(
            {
                ENTRY_FIELD: cv.string,
                vol.Required("favorite"): cv.string,
                vol.Optional("side", default="both"): vol.In(SIDE_CHOICES),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN, "save_favorite", save_favorite, vol.Schema({ENTRY_FIELD: cv.string, vol.Required("name"): cv.string})
    )
    hass.services.async_register(
        DOMAIN,
        "delete_favorite",
        delete_favorite,
        vol.Schema({ENTRY_FIELD: cv.string, vol.Required("favorite"): cv.string}),
    )
