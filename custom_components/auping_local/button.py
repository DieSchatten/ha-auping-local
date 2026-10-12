"""Tasten: Flach, Referenzfahrt, Stopp, Favoriten."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import FAVORITE_SLOTS
from .entity import AupingEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    entities = [
        AupingButton(entry, "flat", "Alles flach", "mdi:bed-empty", lambda c: c.flat()),
        AupingButton(entry, "home", "Referenzfahrt", "mdi:arrow-collapse-down", lambda c: c.home()),
        AupingButton(entry, "stop", "Stopp", "mdi:stop-circle-outline", lambda c: c.stop_all()),
    ]
    for slot in range(1, FAVORITE_SLOTS + 1):
        entities.append(
            AupingButton(
                entry,
                f"favorite_{slot}",
                f"Favorit {slot}",
                "mdi:star-outline",
                lambda c, slot=slot: c.goto_favorite(str(slot)),
            )
        )
    async_add_entities(entities)


class AupingButton(AupingEntity, ButtonEntity):
    def __init__(self, entry, key: str, name: str, icon: str, action) -> None:
        super().__init__(entry, f"button_{key}")
        self._attr_name = name
        self._attr_icon = icon
        self._action = action

    async def async_press(self) -> None:
        await self._action(self.controller)
