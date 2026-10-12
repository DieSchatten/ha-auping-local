"""Steuerlogik: Positionsschätzung, Synchronfahrt beider Seiten und Kalibrierung.

Bewusst ohne Home-Assistant-Abhängigkeiten, damit sich alles mit pytest gegen
eine simulierte Box testen lässt.

Modell je Seite, Motor und Richtung
-----------------------------------
* ``lag``    – Anlaufverzögerung: so lange dauert es nach dem Befehl, bis sich
               der Lattenrost tatsächlich bewegt (Totweg/Totzeit).
* ``travel`` – Vollhub-Laufzeit bei laufendem Motor (0 % -> 100 %).

Die Connect-Box kennt keine Positionen, nur "Motor X für N ms hoch/runter".
Positionen werden deshalb als Anteil 0..1 mitgerechnet. Bei einer Synchronfahrt
startet die Seite mit der kürzeren Anlaufverzögerung um die Differenz später,
und jede Seite bekommt ``lag + Strecke · travel`` als Laufzeit. So bewegen sich
beide Seiten im Gleichschritt und kommen auf derselben Höhe an.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Protocol

from .const import (
    CBU_BOTH,
    CBU_FOR_SIDE,
    DEFAULT_TRAVEL_MS,
    DOWN,
    FAVORITE_SLOTS,
    MAX_COMMAND_MS,
    MAX_LAG_MS,
    MAX_TRAVEL_MS,
    MIN_TRAVEL_MS,
    MODE_SEQUENTIAL,
    OVERRUN_FACTOR,
    OVERRUN_MIN_MS,
    REFRESH_BEFORE_MS,
    SIDE_A,
    SIDE_B,
    UP,
)

_LOGGER = logging.getLogger(__name__)

MIN_MOVE_MS = 120  # kürzere Korrekturen lohnen sich nicht (Funk-Latenz)
SEQ_GAP_S = 0.3  # Pause zwischen Schritten im Modus "nacheinander"
TICK_S = 0.25
MEASURE_COMMAND_MS = 60000  # Messfahrt: lang genug für jeden Motor, Endschalter stoppt
OFFSET_TEST_MS = 4000  # Bewegungsdauer der Versatz-Testfahrt
OFFSET_STEP_BIG = 300
OFFSET_STEP_SMALL = 100
OFFSET_STEP_MIN = 20
RELEVEL_EXTRA_MS = 1500


class MotorApi(Protocol):
    """Was der Controller von der Box braucht."""

    async def send_motors(self, cbu: int, motors: dict[str, tuple[int, int]]) -> float: ...


def overrun_ms(travel_ms: float) -> float:
    return max(OVERRUN_MIN_MS, travel_ms * OVERRUN_FACTOR)


def _dkey(direction: int) -> str:
    return "up" if direction == UP else "down"


def _lkey(direction: int) -> str:
    return "lag_up" if direction == UP else "lag_down"


@dataclass
class MotorState:
    pos: float = 0.0  # Position am Ankerzeitpunkt, 0 = unten, 1 = oben
    known: bool = False  # True, sobald einmal sicher eine Endlage angefahren wurde
    direction: int = 0  # befohlene Richtung (auch während der Anlaufverzögerung)
    start: float = 0.0  # Zeitpunkt des Befehls
    move_start: float = 0.0  # ab hier bewegt sich der Rost wirklich
    end: float = 0.0  # hier schaltet die Box ab
    endpoint: bool = False  # Fahrt zielt mit Nachlauf auf eine Endlage


@dataclass
class Step:
    """Ein Befehl an die Box. ``motors`` legt den Zustand ALLER Motoren der CBU fest."""

    at: float  # Sekunden nach Beginn der Operation
    cbu: int
    sides: tuple[str, ...]
    motors: dict[str, tuple[int, int]]
    endpoints: dict[str, bool] = field(default_factory=dict)


# Plan je Seite: Motor -> (Richtung, reine Bewegungszeit in ms, Endlage?)
SidePlan = dict[str, tuple[int, float, bool]]


class BedController:
    """Hält den Zustand eines Betts und plant alle Fahrbefehle."""

    def __init__(
        self,
        api: MotorApi,
        sides: list[str],
        motors: list[str],
        mode: str = "parallel",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api = api
        self.sides = list(sides)
        self.motors = list(motors)
        self.mode = mode
        self._clock = clock
        self.calibration: dict[str, dict[str, dict[str, float]]] = {
            s: {
                m: {"up": DEFAULT_TRAVEL_MS, "down": DEFAULT_TRAVEL_MS, "lag_up": 0.0, "lag_down": 0.0}
                for m in self.motors
            }
            for s in self.sides
        }
        self.state: dict[str, dict[str, MotorState]] = {
            s: {m: MotorState() for m in self.motors} for s in self.sides
        }
        self.favorites: dict[str, dict[str, dict[str, float]] | None] = {
            str(i): None for i in range(1, FAVORITE_SLOTS + 1)
        }
        self.available = True
        self.last_error: str | None = None
        self.measure: dict[str, Any] | None = None
        self.offset: dict[str, Any] | None = None
        self.offset_test_ms = OFFSET_TEST_MS

        self._pending: list[tuple[float, int, Step]] = []
        self._seq = 0
        self._wake = asyncio.Event()
        self._runner: asyncio.Task | None = None
        self._ticker: asyncio.Task | None = None
        self._calib_task: asyncio.Task | None = None
        self._listeners: list[Callable[[], None]] = []
        self._save_cb: Callable[[], None] | None = None
        self._idle = asyncio.Event()
        self._idle.set()

    # ------------------------------------------------------------------ Lebenszyklus

    def start(self) -> None:
        if self._runner is None or self._runner.done():
            self._runner = asyncio.get_running_loop().create_task(self._run())

    async def async_shutdown(self) -> None:
        for task in (self._calib_task, self._runner, self._ticker):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._runner = self._ticker = self._calib_task = None

    def set_save_callback(self, cb: Callable[[], None]) -> None:
        self._save_cb = cb

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        def remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return remove

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001 – ein defekter Zuhörer darf nichts blockieren
                _LOGGER.exception("Fehler im Listener")

    def _save(self) -> None:
        if self._save_cb:
            self._save_cb()

    # ------------------------------------------------------------------ Persistenz

    def export_state(self) -> dict[str, Any]:
        now = self._clock()
        return {
            "calibration": self.calibration,
            "positions": {
                s: {
                    m: {"pos": round(self.position(s, m, now), 4), "known": st.known}
                    for m, st in motors.items()
                }
                for s, motors in self.state.items()
            },
            "favorites": self.favorites,
        }

    def import_state(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        for s, motors in (data.get("calibration") or {}).items():
            for m, vals in motors.items():
                if s in self.calibration and m in self.calibration[s]:
                    for key in ("up", "down"):
                        if key in vals:
                            self.calibration[s][m][key] = _clamp_travel(vals[key])
                    for key in ("lag_up", "lag_down"):
                        if key in vals:
                            self.calibration[s][m][key] = _clamp_lag(vals[key])
        for s, motors in (data.get("positions") or {}).items():
            for m, vals in motors.items():
                if s in self.state and m in self.state[s]:
                    self.state[s][m] = MotorState(
                        pos=min(1.0, max(0.0, float(vals.get("pos", 0.0)))),
                        known=bool(vals.get("known", False)),
                    )
        for slot, fav in (data.get("favorites") or {}).items():
            if slot in self.favorites:
                self.favorites[slot] = fav

    # ------------------------------------------------------------------ Modell

    def travel_ms(self, side: str, motor: str, direction: int) -> float:
        return self.calibration[side][motor][_dkey(direction)]

    def lag_ms(self, side: str, motor: str, direction: int) -> float:
        return self.calibration[side][motor][_lkey(direction)]

    def _raw_position(self, side: str, motor: str, now: float) -> float:
        st = self.state[side][motor]
        if st.direction == 0:
            return st.pos
        moved = max(0.0, min(now, st.end) - st.move_start)
        return st.pos + st.direction * moved * 1000 / self.travel_ms(side, motor, st.direction)

    def position(self, side: str, motor: str, now: float | None = None) -> float:
        now = self._clock() if now is None else now
        return min(1.0, max(0.0, self._raw_position(side, motor, now)))

    def is_moving(self, side: str, motor: str, now: float | None = None) -> int:
        """Befohlene Richtung (0 = steht). Zählt auch während der Anlaufverzögerung."""
        now = self._clock() if now is None else now
        st = self.state[side][motor]
        if st.direction and now < st.end:
            return st.direction
        return 0

    def any_moving(self, now: float | None = None) -> bool:
        now = self._clock() if now is None else now
        return any(self.is_moving(s, m, now) for s in self.sides for m in self.motors)

    def _start_delay_ms(self, side: str, motor: str, direction: int, at: float) -> float:
        """Wie lange nach einem Befehl zum Zeitpunkt ``at`` vergeht, bis der Rost fährt."""
        st = self.state[side][motor]
        if st.direction == direction and at < st.end:
            return max(0.0, (st.move_start - at) * 1000)  # läuft schon: kein neuer Anlauf
        return self.lag_ms(side, motor, direction)

    def _settle(self, side: str, motor: str, now: float) -> None:
        st = self.state[side][motor]
        if st.direction == 0:
            return
        finished = now >= st.end
        st.pos = self.position(side, motor, now)
        if finished and st.endpoint:
            st.pos = 1.0 if st.direction == UP else 0.0
            st.known = True
        st.direction = 0
        st.endpoint = False

    def _settle_finished(self, now: float) -> bool:
        changed = False
        for s in self.sides:
            for m in self.motors:
                st = self.state[s][m]
                if st.direction and now >= st.end:
                    self._settle(s, m, now)
                    changed = True
        return changed

    def _reanchor(self, side: str, motor: str, now: float) -> None:
        """Laufende Fahrt bis jetzt verbuchen, ohne sie zu beenden."""
        st = self.state[side][motor]
        if st.direction and now < st.end:
            st.pos = self.position(side, motor, now)
            st.start = now
            st.move_start = max(st.move_start, now)
        else:
            self._settle(side, motor, now)

    def _apply_step(self, step: Step, started: float) -> None:
        """Box-Semantik nachbilden: ein Befehl setzt alle Motoren der Ziel-CBU(s)."""
        if self.mode == MODE_SEQUENTIAL and step.cbu != CBU_BOTH:
            # Annahme im Modus "nacheinander": ein Befehl an eine Seite unterbricht die andere.
            for side in self.sides:
                if side not in step.sides:
                    for motor in self.motors:
                        self._settle(side, motor, started)
        for side in step.sides:
            for motor in self.motors:
                st = self.state[side][motor]
                direction, ms = step.motors.get(motor, (0, 0))
                if direction and ms > 0:
                    if st.direction == direction and started < st.end:
                        # Gleiche Richtung, Motor läuft noch: nur die Abschaltzeit ändert sich.
                        st.end = started + ms / 1000
                        st.endpoint = step.endpoints.get(motor, False)
                        continue
                    self._settle(side, motor, started)
                    st.direction = direction
                    st.start = started
                    st.move_start = started + self.lag_ms(side, motor, direction) / 1000
                    st.end = started + ms / 1000
                    st.endpoint = step.endpoints.get(motor, False)
                else:
                    self._settle(side, motor, started)
        if self.measure:
            for side in step.sides:
                if side in self.measure["t0"] and self.measure["t0"][side] is None:
                    self.measure["t0"][side] = started
        self._ensure_ticker()

    # ------------------------------------------------------------------ Planung

    def _plan_side(
        self, side: str, targets: dict[str, float], now: float, keep_others: bool = True
    ) -> SidePlan:
        plan: SidePlan = {}
        for motor in self.motors:
            if motor in targets:
                x = targets[motor]
                p = self.position(side, motor, now)
                if x <= 0.0:
                    t = self.travel_ms(side, motor, DOWN)
                    plan[motor] = (DOWN, p * t + overrun_ms(t), True)
                elif x >= 1.0:
                    t = self.travel_ms(side, motor, UP)
                    plan[motor] = (UP, (1.0 - p) * t + overrun_ms(t), True)
                else:
                    delta = x - p
                    direction = UP if delta > 0 else DOWN
                    ms = abs(delta) * self.travel_ms(side, motor, direction)
                    if ms >= MIN_MOVE_MS:
                        plan[motor] = (direction, ms, False)
            elif keep_others:
                st = self.state[side][motor]
                if st.direction and st.end - now > MIN_MOVE_MS / 1000:
                    remaining = (st.end - max(now, st.move_start)) * 1000
                    plan[motor] = (st.direction, max(0.0, remaining), st.endpoint)
        return plan

    def _side_moving(self, side: str, now: float) -> bool:
        return any(self.is_moving(side, m, now) for m in self.motors)

    def _command(self, side: str, plan: SidePlan, at: float, restart: bool = False):
        """Bewegungszeiten in Befehlszeiten umrechnen (Anlaufverzögerung dazu)."""
        motors: dict[str, tuple[int, int]] = {}
        endpoints: dict[str, bool] = {}
        for m, (d, move_ms, ep) in plan.items():
            delay = self.lag_ms(side, m, d) if restart else self._start_delay_ms(side, m, d, at)
            motors[m] = (d, round(move_ms + delay))
            endpoints[m] = ep
        return motors, endpoints

    def _build_steps(self, plans: dict[str, SidePlan], now: float) -> list[Step]:
        sides = [s for s in self.sides if s in plans]
        if self.mode == MODE_SEQUENTIAL and len(sides) == 2:
            steps = self._build_sequential(plans, now)
        else:
            offsets = self._start_offsets(plans, now) if len(sides) == 2 else {}
            steps = []
            for s in sides:
                if not plans[s] and not self._side_moving(s, now):
                    continue
                at = offsets.get(s, 0.0)
                motors, eps = self._command(s, plans[s], now + at)
                steps.append(Step(at, CBU_FOR_SIDE[s], (s,), motors, eps))
        out: list[Step] = []
        for step in steps:
            out.extend(_split_long(step))
        return out

    def _start_offsets(self, plans: dict[str, SidePlan], now: float) -> dict[str, float]:
        """Seite mit kürzerem Anlauf später starten, damit beide gleichzeitig losfahren."""
        pa, pb = plans[SIDE_A], plans[SIDE_B]
        shared = [m for m in self.motors if m in pa and m in pb and pa[m][0] == pb[m][0]]
        if not shared:
            return {}
        primary = max(shared, key=lambda m: max(pa[m][1], pb[m][1]))
        direction = pa[primary][0]
        if any(self.is_moving(s, primary, now) for s in (SIDE_A, SIDE_B)):
            return {}  # läuft schon – nichts mehr zu versetzen
        lags = {s: self.lag_ms(s, primary, direction) for s in (SIDE_A, SIDE_B)}
        latest = max(lags.values())
        return {s: (latest - lag) / 1000 for s, lag in lags.items()}

    def _build_sequential(self, plans: dict[str, SidePlan], now: float) -> list[Step]:
        """Für Boxen, die A und B nicht gleichzeitig bedienen: gemeinsamer Teil per CBU 0,
        danach holt jede Seite einzeln auf (mit erneutem Anlauf)."""
        pa, pb = plans[SIDE_A], plans[SIDE_B]
        common: dict[str, tuple[int, int]] = {}
        moved: dict[str, dict[str, float]] = {SIDE_A: {}, SIDE_B: {}}
        for m in self.motors:
            if m in pa and m in pb and pa[m][0] == pb[m][0]:
                d = pa[m][0]
                need = {
                    s: plans[s][m][1] + self._start_delay_ms(s, m, d, now) for s in (SIDE_A, SIDE_B)
                }
                c = min(need.values())
                common[m] = (d, round(c))
                for s in (SIDE_A, SIDE_B):
                    moved[s][m] = max(0.0, c - self._start_delay_ms(s, m, d, now))
        steps: list[Step] = []
        t = 0.0
        if common:
            ep = {m: pa[m][2] and pb[m][2] for m in common}
            steps.append(Step(t, CBU_BOTH, (SIDE_A, SIDE_B), common, ep))
            t += max(ms for _, ms in common.values()) / 1000 + SEQ_GAP_S
        rest: dict[str, SidePlan] = {}
        for side, plan in ((SIDE_A, pa), (SIDE_B, pb)):
            rest[side] = {}
            for m, (d, ms, ep) in plan.items():
                left = ms - moved[side].get(m, 0.0)
                if left >= MIN_MOVE_MS:
                    rest[side][m] = (d, left, ep)
        # Reine Stopps zuerst, dann die Nachläufe nacheinander. Nach dem ersten Befehl
        # muss jede Seite neu anlaufen (der vorige Befehl hat sie unterbrochen).
        for side in sorted((SIDE_A, SIDE_B), key=lambda s: bool(rest[s])):
            if rest[side]:
                motors, eps = self._command(side, rest[side], now + t, restart=bool(steps))
                steps.append(Step(t, CBU_FOR_SIDE[side], (side,), motors, eps))
                t += max(ms for _, ms in motors.values()) / 1000 + SEQ_GAP_S
            elif not common and self._side_moving(side, now):
                steps.append(Step(t, CBU_FOR_SIDE[side], (side,), {}))
        if not steps:
            steps.append(Step(0.0, CBU_BOTH, (SIDE_A, SIDE_B), {}))
        return steps

    # ------------------------------------------------------------------ Ablaufsteuerung

    def _schedule(self, steps: list[Step]) -> None:
        now = self._clock()
        touched = {s for step in steps for s in step.sides}
        self._pending = [p for p in self._pending if not touched.intersection(p[2].sides)]
        for step in steps:
            self._seq += 1
            self._pending.append((now + step.at, self._seq, step))
        self._pending.sort(key=lambda p: (p[0], p[1]))
        self._idle.clear()
        self._wake.set()
        self.start()

    async def _run(self) -> None:
        while True:
            if not self._pending:
                self._idle.set()
                self._wake.clear()
                await self._wake.wait()
                continue
            due, _, step = self._pending[0]
            delay = due - self._clock()
            if delay > 0:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                continue
            self._pending.pop(0)
            try:
                started = await self.api.send_motors(step.cbu, step.motors)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Befehl an die Box fehlgeschlagen: %s", err)
                self.available = False
                self.last_error = str(err)
                self._notify()
                continue
            self.available = True
            self.last_error = None
            self._apply_step(step, started)
            self._notify()

    def _ensure_ticker(self) -> None:
        if self._ticker is None or self._ticker.done():
            self._ticker = asyncio.get_running_loop().create_task(self._tick())

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            now = self._clock()
            self._settle_finished(now)
            moving = self.any_moving(now)
            self._notify()
            if not moving and not self._pending:
                self._save()
                return

    async def wait_idle(self) -> None:
        """Warten, bis alle geplanten Befehle verschickt sind."""
        await self._idle.wait()

    async def wait_motion(self) -> None:
        """Warten, bis alle Befehle verschickt sind und kein Motor mehr läuft."""
        while True:
            await self._idle.wait()
            now = self._clock()
            if not self.any_moving(now):
                self._settle_finished(now)
                return
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------------ Öffentliche Befehle

    async def move_to(self, targets: dict[str, dict[str, float]]) -> None:
        """Ziele je Seite und Motor (0..1) anfahren – jede Seite mit eigener Laufzeit."""
        now = self._clock()
        self._settle_finished(now)
        plans = {
            side: self._plan_side(side, motor_targets, now)
            for side, motor_targets in targets.items()
            if side in self.sides
        }
        self._schedule(self._build_steps(plans, now))

    async def start_move(self, sides: list[str], motor: str, direction: int) -> None:
        """Halten: Richtung Endlage losfahren (wird mit stop_motor beendet)."""
        target = 1.0 if direction == UP else 0.0
        await self.move_to({s: {motor: target} for s in sides})

    async def stop_motor(self, sides: list[str], motor: str) -> None:
        """Loslassen: bei beiden Seiten holt eine zurückliegende Seite auf."""
        now = self._clock()
        self._settle_finished(now)
        sides = [s for s in sides if s in self.sides]
        dirs = {self.is_moving(s, motor, now) for s in sides} - {0}
        if len(sides) == 2 and len(dirs) == 1:
            direction = dirs.pop()
            pos = [self.position(s, motor, now) for s in sides]
            x = max(pos) if direction == UP else min(pos)
            await self.move_to({s: {motor: x} for s in sides})
            return
        # Einzelne Seite: nur diesen Motor anhalten, andere laufende Motoren behalten.
        plans = {}
        for s in sides:
            plan = self._plan_side(s, {}, now)
            plan.pop(motor, None)
            plans[s] = plan
        self._schedule(self._build_steps(plans, now))

    async def nudge(self, side: str, motor: str, direction: int, ms: int) -> None:
        """Eine Seite für eine feste Befehlszeit fahren (Dienst "move")."""
        now = self._clock()
        self._settle_finished(now)
        others, eps = self._command(side, self._plan_side(side, {}, now), now)
        others[motor] = (direction, int(ms))
        eps[motor] = False
        self._schedule(_split_long(Step(0.0, CBU_FOR_SIDE[side], (side,), others, eps)))

    async def stop_all(self) -> None:
        self._cancel_calibration()
        now = self._clock()
        self._settle_finished(now)
        if len(self.sides) == 2:
            steps = [Step(0.0, CBU_BOTH, tuple(self.sides), {})]
        else:
            steps = [Step(0.0, CBU_FOR_SIDE[s], (s,), {}) for s in self.sides]
        self._schedule(steps)

    async def flat(self) -> None:
        await self.move_to({s: {m: 0.0 for m in self.motors} for s in self.sides})

    async def home(self) -> None:
        """Referenzfahrt: alles sicher bis zum Endschalter runter, egal was die Schätzung sagt."""
        now = self._clock()
        self._settle_finished(now)
        plans: dict[str, SidePlan] = {}
        for s in self.sides:
            plans[s] = {}
            for m in self.motors:
                t = self.travel_ms(s, m, DOWN)
                plans[s][m] = (DOWN, t + overrun_ms(t), True)
        self._schedule(self._build_steps(plans, now))

    async def goto_favorite(self, slot: str) -> bool:
        fav = self.favorites.get(str(slot))
        if not fav:
            return False
        targets = {
            s: {m: float(v) for m, v in motors.items() if m in self.motors}
            for s, motors in fav.items()
            if s in self.sides
        }
        await self.move_to(targets)
        return True

    def save_favorite(self, slot: str) -> None:
        now = self._clock()
        self.favorites[str(slot)] = {
            s: {m: round(self.position(s, m, now), 4) for m in self.motors} for s in self.sides
        }
        self._save()
        self._notify()

    def set_calibration(self, side: str, motor: str, key: str, ms: float) -> None:
        """Einen Kalibrierwert setzen (``up``, ``down``, ``lag_up``, ``lag_down``)."""
        # Bisherige Strecke mit den alten Werten verbuchen, damit die Position nicht springt.
        self._reanchor(side, motor, self._clock())
        if key in ("up", "down"):
            self.calibration[side][motor][key] = _clamp_travel(ms)
        else:
            self.calibration[side][motor][key] = _clamp_lag(ms)
        self._save()
        self._notify()

    def set_position(self, side: str, motor: str, pos: float, known: bool = True) -> None:
        now = self._clock()
        self._reanchor(side, motor, now)
        st = self.state[side][motor]
        st.pos = min(1.0, max(0.0, pos))
        if st.direction:
            st.start = now
            st.move_start = max(st.move_start, now)
        st.known = known
        self._save()
        self._notify()

    # ------------------------------------------------------------------ Kalibrierung

    def _cancel_calibration(self) -> None:
        if self._calib_task and not self._calib_task.done():
            self._calib_task.cancel()
        self._calib_task = None
        self.measure = None
        self.offset = None

    async def _relevel(self, motor: str, direction: int, full: bool) -> None:
        """Beide Seiten sicher an die Endlage in ``direction`` fahren.

        ``full``: unabhängig von der Schätzung mehr als einen Vollhub fahren
        (für den Anfang, wenn Position oder Laufzeit noch nicht stimmen).
        """
        now = self._clock()
        self._settle_finished(now)
        plans: dict[str, SidePlan] = {}
        for s in self.sides:
            t = self.travel_ms(s, motor, direction)
            p = self.position(s, motor, now)
            dist = 1.3 if full else (1.0 - p if direction == UP else p) * 1.1
            # Zuschlag deckt eine noch nicht kalibrierte Anlaufverzögerung ab.
            plans[s] = {motor: (direction, dist * t + overrun_ms(t) + RELEVEL_EXTRA_MS, True)}
        self._schedule(self._build_steps(plans, now))
        await self.wait_motion()

    async def calib_measure_start(self, motor: str, direction: int) -> None:
        """Vollhub messen: erst an die Gegen-Endlage, dann beide Seiten bis zum
        Endschalter fahren; der Benutzer tippt je Seite "angekommen"."""
        self._cancel_calibration()
        self.measure = {
            "motor": motor,
            "direction": direction,
            "phase": "relevel",
            "t0": {s: None for s in self.sides},
            "marks": {},
        }
        self._notify()
        self._calib_task = asyncio.get_running_loop().create_task(self._measure_run())

    async def _measure_run(self) -> None:
        m = self.measure
        if not m:
            return
        motor, direction = m["motor"], m["direction"]
        known = all(self.state[s][motor].known for s in self.sides)
        # t0 erst nach dem Anfahren der Gegen-Endlage erfassen.
        self.measure = None
        await self._relevel(motor, -direction, full=not known)
        self.measure = m
        m["phase"] = "running"
        now = self._clock()
        if len(self.sides) == 2 and self.mode == MODE_SEQUENTIAL:
            steps = [Step(0.0, CBU_BOTH, tuple(self.sides), {motor: (direction, MEASURE_COMMAND_MS)})]
        else:
            steps = [
                Step(0.0, CBU_FOR_SIDE[s], (s,), {motor: (direction, MEASURE_COMMAND_MS)})
                for s in self.sides
            ]
        out = []
        for step in steps:
            out.extend(_split_long(step))
        self._settle_finished(now)
        self._schedule(out)
        self._notify()

    def calib_mark(self, side: str) -> float | None:
        if not self.measure or self.measure["t0"].get(side) is None:
            return None
        elapsed = (self._clock() - self.measure["t0"][side]) * 1000
        self.measure["marks"][side] = round(elapsed)
        self._notify()
        return elapsed

    async def calib_measure_finish(self, apply: bool) -> dict[str, float]:
        m = self.measure
        if not m:
            return {}
        marks = dict(m["marks"])
        motor, direction = m["motor"], m["direction"]
        await self.stop_all()
        await self.wait_idle()
        result = {}
        if apply:
            for side, elapsed in marks.items():
                travel = elapsed - self.lag_ms(side, motor, direction)
                self.calibration[side][motor][_dkey(direction)] = _clamp_travel(travel)
                result[side] = self.calibration[side][motor][_dkey(direction)]
        for side in marks:
            self.set_position(side, motor, 1.0 if direction == UP else 0.0, known=True)
        self._save()
        self._notify()
        return result

    async def calib_offset_start(self, motor: str, direction: int) -> None:
        """Versatz-Abgleich starten: Endlage anfahren, Testfahrt, dann Rückmeldung abwarten."""
        self._cancel_calibration()
        self.offset = {
            "motor": motor,
            "direction": direction,
            "phase": "relevel",
            "step": None,
            "last": None,
            "rounds": 0,
        }
        self._start_offset_round()

    def _start_offset_round(self) -> None:
        if self.offset:
            self.offset["phase"] = "relevel"
        self._calib_task = asyncio.get_running_loop().create_task(self._offset_round())

    async def _offset_round(self) -> None:
        ofs = self.offset
        if not ofs:
            return
        motor, direction = ofs["motor"], ofs["direction"]
        both = list(self.sides)
        # 1. Beide Seiten an die Endlage: dort stehen sie garantiert gleich hoch.
        ofs["phase"] = "relevel"
        self._notify()
        base = 0.0 if direction == UP else 1.0
        await self._relevel(motor, -direction, full=ofs["rounds"] == 0)
        # 2. Testfahrt: gleiche Strecke für beide Seiten mit aktueller Kompensation.
        ofs["phase"] = "test"
        self._notify()
        travel = min(self.travel_ms(s, motor, direction) for s in both)
        distance = min(0.45, self.offset_test_ms / travel)
        target = base + distance if direction == UP else base - distance
        await self.move_to({s: {motor: target} for s in both})
        await self.wait_motion()
        ofs["phase"] = "feedback"
        ofs["rounds"] += 1
        self._notify()

    async def calib_offset_feedback(self, answer: str, size: str = "small") -> dict[str, Any]:
        """``answer``: "a_higher" | "b_higher" | "equal". ``size``: "big" | "small"."""
        ofs = self.offset
        if not ofs or ofs["phase"] != "feedback":
            return {}
        motor, direction = ofs["motor"], ofs["direction"]
        if answer == "equal":
            lags = {s: self.lag_ms(s, motor, direction) for s in self.sides}
            self.offset = None
            self._save()
            self._notify()
            return {"done": True, "lags": lags}
        step = ofs["step"]
        if step is None:
            step = OFFSET_STEP_BIG if size == "big" else OFFSET_STEP_SMALL
        elif ofs["last"] is not None and ofs["last"] != answer:
            step = max(OFFSET_STEP_MIN, step / 2)  # Vorzeichenwechsel: Schritt halbieren
        ofs["step"] = step
        ofs["last"] = answer
        # Hoch: höhere Seite ist zu viel gefahren -> die andere braucht mehr Vorlauf.
        # Runter: höhere Seite ist zu wenig gefahren -> sie braucht mehr Vorlauf.
        b_needs_more = (answer == "a_higher") == (direction == UP)
        self._shift_lag(motor, direction, step if b_needs_more else -step)
        self._start_offset_round()
        return {"done": False, "step": step, "lags": {s: self.lag_ms(s, motor, direction) for s in self.sides}}

    def _shift_lag(self, motor: str, direction: int, delta: float) -> None:
        """Differenz lag_b - lag_a um ``delta`` verschieben; die kleinere bleibt die Basis."""
        if SIDE_B not in self.sides:
            return
        key = _lkey(direction)
        la = self.calibration[SIDE_A][motor][key]
        lb = self.calibration[SIDE_B][motor][key]
        base = min(la, lb)
        diff = lb - la + delta
        new = {SIDE_A: base + max(0.0, -diff), SIDE_B: base + max(0.0, diff)}
        for side, value in new.items():
            self.calibration[side][motor][key] = _clamp_lag(value)

    def calib_cancel(self) -> None:
        self._cancel_calibration()
        self._notify()

    # ------------------------------------------------------------------ Anzeige

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        measure = None
        if self.measure:
            measure = {
                "motor": self.measure["motor"],
                "direction": self.measure["direction"],
                "phase": self.measure["phase"],
                "elapsed": {
                    s: None if t0 is None else round((now - t0) * 1000)
                    for s, t0 in self.measure["t0"].items()
                },
                "marks": self.measure["marks"],
            }
        return {
            "sides": self.sides,
            "motors": self.motors,
            "mode": self.mode,
            "available": self.available,
            "last_error": self.last_error,
            "positions": {
                s: {
                    m: {
                        "pos": round(self.position(s, m, now), 4),
                        "known": self.state[s][m].known,
                        "moving": self.is_moving(s, m, now),
                    }
                    for m in self.motors
                }
                for s in self.sides
            },
            "calibration": self.calibration,
            "favorites": self.favorites,
            "measure": measure,
            "offset": None
            if not self.offset
            else {k: self.offset[k] for k in ("motor", "direction", "phase", "step", "rounds")},
        }


def _clamp_travel(ms: float) -> float:
    return float(min(MAX_TRAVEL_MS, max(MIN_TRAVEL_MS, round(float(ms)))))


def _clamp_lag(ms: float) -> float:
    return float(min(MAX_LAG_MS, max(0, round(float(ms)))))


def _split_long(step: Step) -> list[Step]:
    """Befehle über 30 s in mehrere Befehle aufteilen (die App schickt nie mehr).

    Kurz vor Ablauf wird die Restzeit nachgeschickt. Motoren, die bis dahin schon
    fertig sind, fehlen im Folgebefehl – die Box stoppt sie dann ohnehin.
    """
    out: list[Step] = []
    at = step.at
    motors = dict(step.motors)
    run = MAX_COMMAND_MS - REFRESH_BEFORE_MS
    while any(ms > MAX_COMMAND_MS for _, ms in motors.values()):
        first = {m: (d, min(ms, MAX_COMMAND_MS)) for m, (d, ms) in motors.items()}
        out.append(Step(at, step.cbu, step.sides, first, step.endpoints))
        at += run / 1000
        motors = {m: (d, ms - run) for m, (d, ms) in motors.items() if ms - run >= MIN_MOVE_MS}
    if motors or not out:
        out.append(Step(at, step.cbu, step.sides, motors, step.endpoints))
    return out
