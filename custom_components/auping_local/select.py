"""Auswahllisten: Favorit anfahren (zeigt den gerade eingestellten) und Matratzenmodus."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import apply_mattress
from .const import MATTRESS_DUAL, MATTRESS_SINGLE
from .entity import AupingEntity

MATTRESS_LABELS = {
    MATTRESS_SINGLE: "Eine Matratze (Seiten begrenzt)",
    MATTRESS_DUAL: "Zwei Einzelmatratzen (frei)",
}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    entities: list[SelectEntity] = [FavoriteSelect(entry)]
    if len(entry.runtime_data.controller.sides) == 2:
        entities.append(MattressSelect(entry))
    async_add_entities(entities)


class FavoriteSelect(AupingEntity, SelectEntity):
    _attr_name = "Favorit"
    _attr_icon = "mdi:star"

    def __init__(self, entry) -> None:
        super().__init__(entry, "select_favorite")

    def _state_key(self) -> object:
        return (tuple(self.options), self.current_option)

    @property
    def options(self) -> list[str]:
        return [fav["name"] for fav in self.controller.favorites.values()]

    @property
    def current_option(self) -> str | None:
        fav_id = self.controller.active_favorite()
        return self.controller.favorites[fav_id]["name"] if fav_id else None

    async def async_select_option(self, option: str) -> None:
        await self.controller.goto_favorite(option)


class MattressSelect(AupingEntity, SelectEntity):
    _attr_name = "Matratze"
    _attr_icon = "mdi:bed-double-outline"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(MATTRESS_LABELS.values())

    def __init__(self, entry) -> None:
        super().__init__(entry, "select_mattress")

    def _state_key(self) -> object:
        return self.current_option

    @property
    def current_option(self) -> str:
        return MATTRESS_LABELS[self.controller.mattress]

    async def async_select_option(self, option: str) -> None:
        mode = next(k for k, v in MATTRESS_LABELS.items() if v == option)
        apply_mattress(self.hass, self._entry, mode=mode)
