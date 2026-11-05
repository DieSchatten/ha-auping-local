"""Je Motor eine Abdeckung pro Seite und eine für beide Seiten synchron."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import ATTR_POSITION, CoverEntity, CoverEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOWN, MOTOR_NAMES, UP
from .entity import AupingEntity

ICONS = {
    "head": "mdi:account-arrow-up",
    "back": "mdi:seat-recline-extra",
    "lower_back": "mdi:seat-recline-normal",
    "legs": "mdi:seat-legroom-extra",
    "feet": "mdi:shoe-print",
}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    ctl = entry.runtime_data.controller
    entities: list[AupingCover] = []
    for motor in ctl.motors:
        if len(ctl.sides) == 2:
            entities.append(AupingCover(entry, motor, list(ctl.sides), "both"))
        for side in ctl.sides:
            entities.append(AupingCover(entry, motor, [side], side))
    async_add_entities(entities)


class AupingCover(AupingEntity, CoverEntity):
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, entry, motor: str, sides: list[str], key: str) -> None:
        super().__init__(entry, f"cover_{motor}_{key}")
        self._motor = motor
        self._sides = sides
        self._attr_icon = ICONS.get(motor, "mdi:bed")

    @property
    def name(self) -> str:
        """Folgt den im Bedienfeld geänderten Seitennamen."""
        if len(self._sides) > 1:
            return f"{MOTOR_NAMES[self._motor]} beide"
        if len(self.controller.sides) == 1:
            return MOTOR_NAMES[self._motor]
        return f"{MOTOR_NAMES[self._motor]} {self._data.names[self._sides[0]]}"

    def _positions(self) -> list[float]:
        return [self.controller.position(s, self._motor) for s in self._sides]

    def _directions(self) -> set[int]:
        return {self.controller.is_moving(s, self._motor) for s in self._sides} - {0}

    def _state_key(self) -> object:
        # Richtungswechsel sofort, Positionsänderungen während der Fahrt gedrosselt.
        dirs = tuple(sorted(self._directions()))
        return (dirs, None if dirs else self.current_cover_position)

    def _is_changing(self) -> bool:
        return bool(self._directions())

    @property
    def current_cover_position(self) -> int:
        pos = self._positions()
        return round(sum(pos) / len(pos) * 100)

    @property
    def is_closed(self) -> bool:
        return all(p <= 0.005 for p in self._positions())

    @property
    def is_opening(self) -> bool:
        return UP in self._directions()

    @property
    def is_closing(self) -> bool:
        return DOWN in self._directions()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        names = self._data.names
        attrs: dict[str, Any] = {
            "position_bekannt": all(self.controller.state[s][self._motor].known for s in self._sides)
        }
        if len(self._sides) > 1:
            for s in self._sides:
                attrs[f"position_{names[s].lower()}"] = round(self.controller.position(s, self._motor) * 100)
        return attrs

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.controller.move_to({s: {self._motor: 1.0} for s in self._sides})

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.controller.move_to({s: {self._motor: 0.0} for s in self._sides})

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        x = kwargs[ATTR_POSITION] / 100
        await self.controller.move_to({s: {self._motor: x} for s in self._sides})

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self.controller.stop_motor(self._sides, self._motor)
