"""Tasten: Flach, Referenzfahrt, Stopp und je Favorit eine Taste."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import AupingEntity

FAVORITE_PREFIX = "button_favorite_"


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    ctl = entry.runtime_data.controller
    async_add_entities(
        [
            AupingButton(entry, "flat", "Alles flach", "mdi:bed-empty", lambda c: c.flat()),
            AupingButton(entry, "home", "Referenzfahrt", "mdi:arrow-collapse-down", lambda c: c.home()),
            AupingButton(entry, "stop", "Stopp", "mdi:stop-circle-outline", lambda c: c.stop_all()),
        ]
    )

    registry = er.async_get(hass)
    known: dict[str, FavoriteButton] = {}

    @callback
    def sync_favorites() -> None:
        """Favoriten-Tasten anlegen bzw. entfernen, wenn sich die Liste ändert."""
        current = set(ctl.favorites)
        new = [FavoriteButton(entry, fav_id) for fav_id in ctl.favorites if fav_id not in known]
        for button in new:
            known[button.fav_id] = button
        if new:
            async_add_entities(new)
        for fav_id in list(known):
            if fav_id not in current:
                known.pop(fav_id)
                entity_id = registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_{FAVORITE_PREFIX}{fav_id}")
                if entity_id:
                    registry.async_remove(entity_id)

    # Verwaiste Tasten (z. B. leere Plätze aus Version 0.1) aufräumen
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        prefix = f"{entry.entry_id}_{FAVORITE_PREFIX}"
        if reg_entry.domain == "button" and reg_entry.unique_id.startswith(prefix):
            if reg_entry.unique_id[len(prefix) :] not in ctl.favorites:
                registry.async_remove(reg_entry.entity_id)

    sync_favorites()
    entry.async_on_unload(ctl.add_listener(sync_favorites))


class AupingButton(AupingEntity, ButtonEntity):
    def __init__(self, entry, key: str, name: str, icon: str, action) -> None:
        super().__init__(entry, f"button_{key}")
        self._attr_name = name
        self._attr_icon = icon
        self._action = action

    async def async_press(self) -> None:
        await self._action(self.controller)


class FavoriteButton(AupingEntity, ButtonEntity):
    """Fährt einen Favoriten an; der Name folgt dem Favoriten-Namen."""

    _attr_icon = "mdi:star-outline"

    def __init__(self, entry, fav_id: str) -> None:
        super().__init__(entry, f"{FAVORITE_PREFIX}{fav_id}")
        self.fav_id = fav_id

    @property
    def name(self) -> str:
        fav = self.controller.favorites.get(self.fav_id)
        return f"Favorit {fav['name']}" if fav else f"Favorit {self.fav_id}"

    def _state_key(self) -> object:
        return self.name

    async def async_press(self) -> None:
        await self.controller.goto_favorite(self.fav_id)
