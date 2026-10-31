"""WebSocket-Schnittstelle für das eigene Bedienfeld."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_HOST,
    CONF_MODE,
    DOMAIN,
    DOWN,
    MATTRESS_DUAL,
    MATTRESS_SINGLE,
    MODE_PARALLEL,
    MODE_SEQUENTIAL,
    MOTOR_NAMES,
    UP,
)

PUSH_INTERVAL_S = 0.2
# Auch ohne Änderung regelmäßig den ganzen Stand schicken. Daran merkt das Bedienfeld, dass
# sein Abo noch lebt, und meldet sich sonst neu an.
HEARTBEAT = timedelta(seconds=20)
SUBSCRIBERS = f"{DOMAIN}_subscribers"


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_entries)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_command)


def _loaded_entries(hass: HomeAssistant):
    return [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]


def _entry(hass: HomeAssistant, entry_id: str):
    for e in _loaded_entries(hass):
        if e.entry_id == entry_id:
            return e
    return None


def _ready_entry(hass: HomeAssistant, entry_id: str):
    """Eintrag mit fertigem Controller – auch schon während des Einrichtens."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.state not in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS):
        return None
    return entry if getattr(entry, "runtime_data", None) is not None else None


@callback
def async_notify(hass: HomeAssistant, entry_id: str) -> None:
    """Alle offenen Bedienfelder dieses Betts auf den neuen Stand bringen."""
    for on_change in list(hass.data.get(SUBSCRIBERS, {}).get(entry_id, ())):
        on_change()


@callback
def async_attach(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Controller und Coordinator eines (neu) eingerichteten Eintrags mit den Abos verbinden.

    Die Abos hängen am Eintrag, nicht an einer Controller-Instanz: Nach dem Neuladen der
    Integration (Konfigurieren, Neustart der Integration, neue IP) laufen offene Bedienfelder
    auf anderen Geräten einfach weiter, statt auf dem alten Stand stehen zu bleiben.
    """
    data = entry.runtime_data

    @callback
    def notify() -> None:
        async_notify(hass, entry.entry_id)

    entry.async_on_unload(data.controller.add_listener(notify))
    entry.async_on_unload(data.coordinator.async_add_listener(notify))
    notify()


def _payload(hass: HomeAssistant, entry) -> dict[str, Any]:
    data = entry.runtime_data
    snap = data.controller.snapshot()
    snap["names"] = data.names
    snap["motor_names"] = {m: MOTOR_NAMES[m] for m in data.controller.motors}
    snap["title"] = entry.title
    snap["host"] = entry.data[CONF_HOST]
    snap["box"] = {
        k: (data.coordinator.data or {}).get(k) for k in ("firmware", "name", "mac_address", "sync", "bed_type")
    }
    snap["box_online"] = data.coordinator.last_update_success
    snap["rtt_ms"] = None if data.api.last_rtt_ms is None else round(data.api.last_rtt_ms)
    snap["version"] = data.version
    return snap


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/entries"})
@callback
def ws_entries(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    # Auch Einträge, die gerade noch eingerichtet werden (Bedienfeld offen während HA startet).
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.disabled_by is None]
    connection.send_result(msg["id"], [{"entry_id": e.entry_id, "title": e.title} for e in entries])


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/subscribe", vol.Required("entry_id"): str}
)
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    entry_id = msg["entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "not_found", "Bett nicht gefunden")
        return
    state = {"last": 0.0, "scheduled": None}

    @callback
    def push(*_: Any) -> None:
        state["scheduled"] = None
        state["last"] = hass.loop.time()
        current = _ready_entry(hass, entry_id)  # immer frisch: nach dem Neuladen ein neuer Controller
        if current is not None:
            connection.send_message(websocket_api.event_message(msg["id"], _payload(hass, current)))

    @callback
    def on_change() -> None:
        if state["scheduled"] is not None:
            return
        wait = PUSH_INTERVAL_S - (hass.loop.time() - state["last"])
        if wait <= 0:
            push()
        else:
            state["scheduled"] = hass.loop.call_later(wait, push)

    subscribers = hass.data.setdefault(SUBSCRIBERS, {}).setdefault(entry_id, set())
    subscribers.add(on_change)
    remove_heartbeat = async_track_time_interval(hass, push, HEARTBEAT)

    @callback
    def unsubscribe() -> None:
        subscribers.discard(on_change)
        remove_heartbeat()
        if state["scheduled"] is not None:
            state["scheduled"].cancel()

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    push()


def _dir(value: str) -> int:
    return UP if value == "up" else DOWN


def _sides(controller, side: str) -> list[str]:
    return list(controller.sides) if side == "both" else [side]


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/command",
        vol.Required("entry_id"): str,
        vol.Required("action"): str,
        vol.Optional("data", default={}): dict,
    }
)
@websocket_api.async_response
async def ws_command(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Bett nicht gefunden")
        return
    ctl = entry.runtime_data.controller
    d = msg["data"]
    action = msg["action"]
    result: Any = None
    try:
        if action == "start_move":
            await ctl.start_move(_sides(ctl, d["side"]), d["motor"], _dir(d["direction"]))
        elif action == "stop_motor":
            await ctl.stop_motor(_sides(ctl, d["side"]), d["motor"])
        elif action == "move_to":
            await ctl.move_to(
                {s: {d["motor"]: float(d["position"])} for s in _sides(ctl, d["side"])}
            )
        elif action == "flat":
            await ctl.flat()
        elif action == "home":
            await ctl.home()
        elif action == "stop_all":
            await ctl.stop_all()
        elif action == "goto_favorite":
            result = await ctl.goto_favorite(str(d["id"]), _sides(ctl, d.get("side", "both")))
        elif action == "save_favorite":
            result = ctl.save_favorite(d.get("name"), d.get("id"))
        elif action == "rename_favorite":
            ctl.rename_favorite(str(d["id"]), d["name"])
        elif action == "delete_favorite":
            ctl.delete_favorite(str(d["id"]))
        elif action == "move_favorite":
            ctl.move_favorite(str(d["id"]), int(d["offset"]))
        elif action == "set_mattress":
            from . import apply_mattress  # erst zur Laufzeit, sonst Kreisimport

            mode = d.get("mode")
            if mode is not None and mode not in (MATTRESS_SINGLE, MATTRESS_DUAL):
                raise ValueError("unbekannter Matratzenmodus")
            apply_mattress(hass, entry, mode=mode, max_diff=d.get("max_diff"))
        elif action == "nudge":
            await ctl.nudge(d["side"], d["motor"], _dir(d["direction"]), int(d["ms"]))
        elif action == "set_calibration":
            if d["key"] not in ("up", "down", "gap"):
                raise ValueError("unbekannter Kalibrierwert")
            ctl.set_calibration(d["side"], d["motor"], d["key"], float(d["value"]))
        elif action == "set_position":
            ctl.set_position(d["side"], d["motor"], float(d["position"]), known=True)
        elif action == "set_mode":
            mode = d["mode"]
            if mode not in (MODE_PARALLEL, MODE_SEQUENTIAL):
                raise ValueError("unbekannter Modus")
            ctl.mode = mode
            hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_MODE: mode})
            ctl._notify()  # noqa: SLF001
        elif action == "measure_start":
            await ctl.calib_measure_start(d["motor"], _dir(d["direction"]))
        elif action == "measure_mark":
            for side in _sides(ctl, d["side"]):
                ctl.calib_mark(side)
        elif action == "measure_finish":
            result = await ctl.calib_measure_finish(bool(d.get("apply", True)))
        elif action == "relevel_skip":
            result = ctl.relevel_skip()
        elif action == "adjust_start":
            ctl.adjust_start(d["motor"])
        elif action == "adjust_finish":
            result = await ctl.adjust_finish(bool(d.get("apply", True)))
        elif action == "test_move":
            await ctl.calib_test_move(d["motor"])
        elif action == "shift_gap":
            ctl.shift_gap(d["motor"], float(d["delta"]))
        elif action == "set_names":
            from . import apply_names  # erst zur Laufzeit, sonst Kreisimport

            apply_names(hass, entry, d.get("name_a"), d.get("name_b"))
        elif action == "calib_cancel":
            ctl.calib_cancel()
            await ctl.stop_all()
        elif action == "test_parallel":
            # Beide Seiten kurz einzeln anstoßen: laufen beide los, kann die Box "gleichzeitig".
            motor = d["motor"]
            for side in ctl.sides:
                await ctl.nudge(side, motor, UP, int(d.get("ms", 2500)))
        else:
            connection.send_error(msg["id"], "unknown_action", f"Unbekannte Aktion {action}")
            return
    except (KeyError, ValueError, TypeError) as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], result)
