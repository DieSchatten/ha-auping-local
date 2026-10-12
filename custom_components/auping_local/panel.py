"""Eigenes Bedienfeld in der Home-Assistant-Seitenleiste."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, PANEL_COMPONENT, PANEL_STATIC_URL, PANEL_URL_PATH

FRONTEND_DIR = Path(__file__).parent / "frontend"


async def async_register(hass: HomeAssistant) -> None:
    flags = hass.data.setdefault(f"{DOMAIN}_panel", {"static": False, "panel": False})
    if not flags["static"]:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_STATIC_URL, str(FRONTEND_DIR), False)]
        )
        flags["static"] = True
    if flags["panel"]:
        return
    js = FRONTEND_DIR / "auping-panel.js"
    stamp = await hass.async_add_executor_job(lambda: int(js.stat().st_mtime) if js.exists() else 0)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title="Bett",
        sidebar_icon="mdi:bed-king-outline",
        module_url=f"{PANEL_STATIC_URL}/auping-panel.js?v={stamp}",
        require_admin=False,
        config={},
    )
    flags["panel"] = True


@callback
def async_unregister(hass: HomeAssistant) -> None:
    flags = hass.data.get(f"{DOMAIN}_panel")
    if flags and flags["panel"]:
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
        flags["panel"] = False
