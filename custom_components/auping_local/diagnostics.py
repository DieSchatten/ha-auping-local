"""Diagnose-Download (Geräteseite → Diagnose herunterladen) – Schlüssel und Adressen geschwärzt."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, CONF_MAC, CONF_XKEY

TO_REDACT = {CONF_XKEY, CONF_HOST, CONF_MAC, "mac_address", "ip", "hostname", "stored_ap", "name", "name_a", "name_b"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry) -> dict[str, Any]:
    data = entry.runtime_data
    snapshot = data.controller.snapshot()
    snapshot.pop("favorites", None)  # Namen der Favoriten sind privat und hier unnötig
    return {
        "version": data.version,
        "entry": async_redact_data({"data": dict(entry.data), "options": dict(entry.options)}, TO_REDACT),
        "box": async_redact_data(dict(data.coordinator.data or {}), TO_REDACT),
        "box_online": data.coordinator.last_update_success,
        "controller": snapshot,
        "commands": list(data.controller.command_log),
    }
