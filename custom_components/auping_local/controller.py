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
from collections import deque
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
    MAX_FAVORITES,
    MAX_COMMAND_MS,
    MAX_LAG_MS,
    MATTRESS_DUAL,
    MATTRESS_SINGLE,
    MAX_TRAVEL_MS,
    MIN_TRAVEL_MS,
    MODE_SEQUENTIAL,
    MOTOR_NAMES,
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
TRAIL_MAX = 200  # gemerkte Fahrten seit der letzten "gleich hoch"-Lage je Seite und Motor
TEST_MOVE_MS = 3000  # Testfahrt zum Prüfen des Versatzes
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
    at_end: int = 0  # UP/DOWN: letzte Fahrt ist sicher an dieser Endlage angekommen
    # Fahrten seit der letzten Lage, in der beide Seiten gleich hoch standen:
    # [Richtung, Befehlszeitpunkt, Abschaltzeitpunkt]
    trail: list[list[float]] = field(default_factory=list)


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
        mattress: str = MATTRESS_DUAL,
        max_diff: float = 0.10,
    ) -> None:
        self.api = api
        self.sides = list(sides)
        self.motors = list(motors)
        self.mode = mode
        self._clock = clock
        self.mattress = mattress
        self.max_diff = max_diff  # Anteil 0..1
        self._limit_hit: tuple[float, str] | None = None
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
        # Favoriten: id -> {"name": str, "positions": {seite: {motor: 0..1}}}
        self.favorites: dict[str, dict[str, Any]] = {}
        self.available = True
        self.last_error: str | None = None
        self.measure: dict[str, Any] | None = None
        self.adjust: dict[str, Any] | None = None
        # Je Motor: Lage, in der beide Seiten zuletzt sicher gleich hoch standen (oder None)
        self.level_ref: dict[str, float | None] = {m: None for m in self.motors}
        self.command_log: deque[dict[str, Any]] = deque(maxlen=40)
        self._skip_event: asyncio.Event | None = None
        self._last_stop_at = 0.0

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
                    m: {"pos": round(self.position(s, m, now), 4), "known": st.known, "at_end": st.at_end}
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
                        at_end=int(vals.get("at_end", 0)),
                    )
        for fav_id, fav in (data.get("favorites") or {}).items():
            if not fav:
                continue  # leerer Platz aus Version 0.1
            if "positions" not in fav:
                fav = {"name": f"Favorit {fav_id}", "positions": fav}  # Format aus Version 0.1
            self.favorites[str(fav_id)] = {"name": str(fav["name"]), "positions": fav["positions"]}

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
        if st.trail and st.trail[-1][2] > now:
            st.trail[-1][2] = now  # vorzeitig angehalten
        if finished and st.endpoint:
            st.pos = 1.0 if st.direction == UP else 0.0
            st.known = True
            st.at_end = st.direction
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

    def _end_state(self, side: str, motor: str, now: float) -> int:
        """UP/DOWN, wenn die Seite an dieser Endlage steht – auch schon während des Nachlaufs."""
        st = self.state[side][motor]
        if st.direction and now < st.end:
            raw = self._raw_position(side, motor, now)
            if st.endpoint and ((st.direction == DOWN and raw <= 0.0) or (st.direction == UP and raw >= 1.0)):
                return st.direction  # Endlage erreicht, Motor läuft nur noch gegen den Anschlag
            return 0
        return st.at_end

    def _level_at_end(self, motor: str, now: float | None = None) -> int:
        """UP/DOWN, wenn alle Seiten nachweislich an derselben Endlage stehen, sonst 0."""
        now = self._clock() if now is None else now
        ends = {self._end_state(s, motor, now) for s in self.sides}
        return ends.pop() if len(ends) == 1 and 0 not in ends else 0

    def _set_level_ref(self, motor: str, pos: float) -> None:
        self.level_ref[motor] = pos
        for s in self.sides:
            self.state[s][motor].trail.clear()

    def _apply_step(self, step: Step, started: float) -> None:
        """Box-Semantik nachbilden: ein Befehl setzt alle Motoren der Ziel-CBU(s)."""
        for motor, (direction, ms) in step.motors.items():
            if direction and ms > 0 and motor in self.motors:
                end = self._level_at_end(motor, started)
                if end:
                    self._set_level_ref(motor, 1.0 if end == UP else 0.0)
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
                        if st.trail:
                            st.trail[-1][2] = st.end
                        continue
                    self._settle(side, motor, started)
                    st.at_end = 0
                    st.direction = direction
                    st.start = started
                    st.move_start = started + self.lag_ms(side, motor, direction) / 1000
                    st.end = started + ms / 1000
                    st.endpoint = step.endpoints.get(motor, False)
                    st.trail.append([direction, started, st.end])
                    if len(st.trail) > TRAIL_MAX:
                        self.level_ref[motor] = None  # zu lange her, keine sichere Bezugslage mehr
                        st.trail.clear()
                else:
                    self._settle(side, motor, started)
        if not step.motors:
            self._last_stop_at = started
        meas = self.measure
        if meas and meas.get("phase") == "running" and step.motors:
            for side in step.sides:
                if side in meas["t0"] and meas["t0"][side] is None:
                    meas["t0"][side] = started
        self._ensure_ticker()

    # ------------------------------------------------------------------ Planung

    def _plan_side(
        self, side: str, targets: dict[str, float], now: float, keep_others: bool = True, hold: bool = False
    ) -> SidePlan:
        """``hold``: Taste wird gehalten – Endlagen-Ziele mit vollem Hub schicken (das Loslassen
        beendet die Fahrt), damit eine falsche Schätzung die Fahrt nicht zu früh abbricht."""
        plan: SidePlan = {}
        for motor in self.motors:
            if motor in targets:
                x = targets[motor]
                p = self.position(side, motor, now)
                st = self.state[side][motor]
                at_target_end = not hold and not self.is_moving(side, motor, now) and (
                    (x <= 0.0 and st.at_end == DOWN) or (x >= 1.0 and st.at_end == UP)
                )
                if at_target_end:
                    continue  # steht nachweislich schon am Endschalter
                if x <= 0.0:
                    t = self.travel_ms(side, motor, DOWN)
                    plan[motor] = (DOWN, (1.0 if hold else p) * t + overrun_ms(t), True)
                elif x >= 1.0:
                    t = self.travel_ms(side, motor, UP)
                    plan[motor] = (UP, (1.0 if hold else 1.0 - p) * t + overrun_ms(t), True)
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
            entry: dict[str, Any] = {
                "time": time.time(),
                "cbu": step.cbu,
                "motors": {m: [d, ms] for m, (d, ms) in step.motors.items()},
            }
            self.command_log.append(entry)
            try:
                started = await self.api.send_motors(step.cbu, step.motors)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Befehl an die Box fehlgeschlagen: %s", err)
                entry.update(ok=False, error=str(err))
                self.available = False
                self.last_error = str(err)
                self._notify()
                continue
            rtt = getattr(self.api, "last_rtt_ms", None)
            entry.update(ok=True, rtt=None if rtt is None else round(rtt))
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

    # ------------------------------------------------------------------ Matratzenmodus

    @property
    def side_limit(self) -> float | None:
        """Erlaubter Höhenunterschied der Seiten oder None (frei)."""
        if self.mattress == MATTRESS_SINGLE and len(self.sides) == 2:
            return self.max_diff
        return None

    def _note_limit(self, motor: str, now: float) -> None:
        self._limit_hit = (now, f"{MOTOR_NAMES.get(motor, motor)}: begrenzt durch Matratzenmodus")

    def _limit_targets(
        self, targets: dict[str, dict[str, float]], now: float
    ) -> dict[str, dict[str, float]]:
        """Ziele so begrenzen, dass die Seiten höchstens ``side_limit`` auseinanderliegen.

        Bewegungen, die den Abstand verkleinern, sind immer erlaubt. Würde eine Begrenzung
        die Fahrtrichtung umdrehen, bleibt der Motor stattdessen stehen.
        """
        d = self.side_limit
        if d is None:
            return targets
        out = {s: dict(t) for s, t in targets.items()}
        for m in self.motors:
            ta = out.get(SIDE_A, {}).get(m)
            tb = out.get(SIDE_B, {}).get(m)
            if ta is not None and tb is not None:
                if abs(ta - tb) > d + 1e-6:
                    mid = min(1.0 - d / 2, max(d / 2, (ta + tb) / 2))
                    lo, hi = mid - d / 2, mid + d / 2
                    out[SIDE_A][m], out[SIDE_B][m] = (hi, lo) if ta > tb else (lo, hi)
                    self._note_limit(m, now)
                continue
            if ta is None and tb is None:
                continue
            side, other = (SIDE_A, SIDE_B) if ta is not None else (SIDE_B, SIDE_A)
            x = out[side][m]
            p = self.position(side, m, now)
            ref = self.position(other, m, now)
            limited = min(ref + d, max(ref - d, x))
            if abs(limited - x) > 1e-6:
                self._note_limit(m, now)
                if (x - p) * (limited - p) <= 0:
                    limited = p  # Richtung würde sich umdrehen: stehen bleiben
                out[side][m] = limited
        return out

    async def move_to(self, targets: dict[str, dict[str, float]], hold: bool = False) -> None:
        """Ziele je Seite und Motor (0..1) anfahren – jede Seite mit eigener Laufzeit."""
        now = self._clock()
        self._settle_finished(now)
        limit_before = self._limit_hit
        targets = self._limit_targets(targets, now)
        plans = {
            side: self._plan_side(side, motor_targets, now, hold=hold)
            for side, motor_targets in targets.items()
            if side in self.sides
        }
        steps = self._build_steps(plans, now)
        if steps:
            self._schedule(steps)
        if self._limit_hit is not limit_before:
            self._notify()  # Hinweis auch dann zeigen, wenn gar nicht gefahren wird

    async def start_move(self, sides: list[str], motor: str, direction: int) -> None:
        """Halten: Richtung Endlage losfahren (wird mit stop_motor beendet)."""
        target = 1.0 if direction == UP else 0.0
        await self.move_to({s: {motor: target} for s in sides}, hold=True)

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
        if self.side_limit is not None:
            delay = self._start_delay_ms(side, motor, direction, now)
            travel = self.travel_ms(side, motor, direction)
            p = self.position(side, motor, now)
            wanted = p + direction * max(0.0, ms - delay) / travel
            allowed = self._limit_targets({side: {motor: wanted}}, now)[side][motor]
            if abs(allowed - wanted) > 1e-6:
                if (allowed - p) * direction <= 0:
                    self._notify()
                    return
                ms = round(delay + abs(allowed - p) * travel)
        others, eps = self._command(side, self._plan_side(side, {}, now), now)
        others[motor] = (direction, int(ms))
        eps[motor] = False
        self._schedule(_split_long(Step(0.0, CBU_FOR_SIDE[side], (side,), others, eps)))

    def _stop_now(self) -> None:
        now = self._clock()
        self._settle_finished(now)
        if len(self.sides) == 2:
            steps = [Step(0.0, CBU_BOTH, tuple(self.sides), {})]
        else:
            steps = [Step(0.0, CBU_FOR_SIDE[s], (s,), {}) for s in self.sides]
        self._schedule(steps)

    async def stop_all(self) -> None:
        self._cancel_calibration()
        self._stop_now()

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

    def find_favorite(self, key: str) -> str | None:
        """Favorit über id oder Namen (ohne Groß/klein) finden."""
        key = str(key).strip()
        if key in self.favorites:
            return key
        for fav_id, fav in self.favorites.items():
            if fav["name"].casefold() == key.casefold():
                return fav_id
        return None

    def _check_name(self, name: str, own_id: str | None = None) -> str:
        name = " ".join(str(name).split())
        if not name:
            raise ValueError("Der Name darf nicht leer sein")
        if len(name) > 40:
            raise ValueError("Der Name ist zu lang (höchstens 40 Zeichen)")
        other = self.find_favorite(name)
        if other is not None and other != own_id:
            raise ValueError(f"Es gibt schon einen Favoriten „{name}“")
        return name

    def _current_positions(self) -> dict[str, dict[str, float]]:
        now = self._clock()
        return {s: {m: round(self.position(s, m, now), 4) for m in self.motors} for s in self.sides}

    def save_favorite(self, name: str | None = None, fav_id: str | None = None) -> str:
        """Aktuelle Lage beider Seiten speichern – neu oder über einen bestehenden Favoriten."""
        if fav_id is not None and fav_id in self.favorites:
            fav = self.favorites[fav_id]
            if name:
                fav["name"] = self._check_name(name, fav_id)
            fav["positions"] = self._current_positions()
        else:
            if len(self.favorites) >= MAX_FAVORITES:
                raise ValueError(f"Höchstens {MAX_FAVORITES} Favoriten")
            fav_id = str(max((int(i) for i in self.favorites if i.isdigit()), default=0) + 1)
            name = self._check_name(name or f"Favorit {fav_id}")
            self.favorites[fav_id] = {"name": name, "positions": self._current_positions()}
        self._save()
        self._notify()
        return fav_id

    def rename_favorite(self, fav_id: str, name: str) -> None:
        if fav_id not in self.favorites:
            raise ValueError("Favorit nicht gefunden")
        self.favorites[fav_id]["name"] = self._check_name(name, fav_id)
        self._save()
        self._notify()

    def delete_favorite(self, fav_id: str) -> None:
        if self.favorites.pop(fav_id, None) is None:
            raise ValueError("Favorit nicht gefunden")
        self._save()
        self._notify()

    def move_favorite(self, fav_id: str, offset: int) -> None:
        """Reihenfolge ändern (-1 = nach vorne, +1 = nach hinten)."""
        ids = list(self.favorites)
        if fav_id not in ids:
            raise ValueError("Favorit nicht gefunden")
        i = ids.index(fav_id)
        j = min(len(ids) - 1, max(0, i + offset))
        ids.insert(j, ids.pop(i))
        self.favorites = {k: self.favorites[k] for k in ids}
        self._save()
        self._notify()

    async def goto_favorite(self, key: str, sides: list[str] | None = None) -> bool:
        """Favorit anfahren: jeder Motor fährt gezielt von seiner aktuellen Lage hoch oder runter."""
        fav_id = self.find_favorite(key)
        if fav_id is None:
            return False
        positions = self.favorites[fav_id]["positions"]
        wanted = sides or self.sides
        targets = {
            s: {m: float(v) for m, v in motors.items() if m in self.motors}
            for s, motors in positions.items()
            if s in self.sides and s in wanted
        }
        await self.move_to(targets)
        return True

    def active_favorite(self, tolerance: float = 0.02) -> str | None:
        """Favorit, der gerade eingestellt ist (alle Motoren beider Seiten innerhalb der Toleranz)."""
        now = self._clock()
        if self.any_moving(now):
            return None
        for fav_id, fav in self.favorites.items():
            pos = fav["positions"]
            if all(
                abs(self.position(s, m, now) - float(pos.get(s, {}).get(m, -9))) <= tolerance
                for s in self.sides
                for m in self.motors
            ):
                return fav_id
        return None

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
        # "Steht ganz unten/oben" vom Benutzer: dann muss dort nichts mehr angefahren werden.
        st.at_end = (DOWN if st.pos <= 0.0 else UP if st.pos >= 1.0 else 0) if known and not st.direction else 0
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
        self._skip_event = None

    def at_end(self, motor: str, direction: int) -> bool:
        """Stehen alle Seiten nachweislich an dieser Endlage?"""
        now = self._clock()
        return all(
            self.state[s][motor].at_end == direction and not self.is_moving(s, motor, now) for s in self.sides
        )

    async def _relevel(self, motor: str, direction: int) -> None:
        """Beide Seiten an die Endlage in ``direction`` fahren.

        Entfällt, wenn sie nachweislich schon dort stehen. Mit ``relevel_skip`` kann der
        Benutzer abkürzen ("steht schon unten").
        """
        if self.at_end(motor, direction):
            return
        now = self._clock()
        self._settle_finished(now)
        plans: dict[str, SidePlan] = {}
        for s in self.sides:
            t = self.travel_ms(s, motor, direction)
            st = self.state[s][motor]
            p = self.position(s, motor, now)
            dist = (1.0 - p if direction == UP else p) if st.known else 1.0
            # Zuschläge decken eine noch ungenaue Laufzeit und einen unbekannten Anlauf ab.
            plans[s] = {motor: (direction, dist * t * 1.1 + overrun_ms(t) + RELEVEL_EXTRA_MS, True)}
        self._schedule(self._build_steps(plans, now))
        self._skip_event = asyncio.Event()
        self._notify()
        motion = asyncio.ensure_future(self.wait_motion())
        skip = asyncio.ensure_future(self._skip_event.wait())
        try:
            done, _ = await asyncio.wait({motion, skip}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            motion.cancel()
            skip.cancel()
            self._skip_event = None
        if skip in done:
            self._stop_now()
            await self.wait_idle()
            for s in self.sides:
                st = self.state[s][motor]
                st.pos = 1.0 if direction == UP else 0.0
                st.known = True
                st.at_end = direction
            self._notify()

    def relevel_skip(self) -> bool:
        """Benutzer bestätigt: das Bett steht schon an der Endlage."""
        if self._skip_event is None:
            return False
        self._skip_event.set()
        return True

    # --- Nachjustieren: Benutzer fährt die zurückliegende Seite nach, bis beide gleich hoch sind

    def adjust_start(self, motor: str) -> None:
        if len(self.sides) < 2:
            raise ValueError("Nachjustieren ist nur bei zwei Seiten nötig")
        if self.level_ref.get(motor) is None and not self._level_at_end(motor):
            raise ValueError(
                "Dafür müssen beide Seiten vorher gleich hoch gestanden haben: erst „Alles flach“ "
                "(für runter: ganz hoch), dann mit „Beide“ ein Stück fahren und nachjustieren."
            )
        if not any(self.state[s][motor].trail for s in self.sides):
            raise ValueError("Erst mit „Beide“ ein Stück fahren, dann nachjustieren.")
        self.adjust = {"motor": motor}
        self._notify()

    async def adjust_finish(self, apply: bool) -> dict[str, Any]:
        """``apply``: beide Seiten stehen jetzt gleich hoch – Versatz daraus berechnen."""
        adj = self.adjust
        self.adjust = None
        if not adj or not apply:
            self._notify()
            return {}
        motor = adj["motor"]
        await self.wait_motion()
        try:
            result = self._solve_lag(motor)
        finally:
            self._save()
            self._notify()
        return result

    def _trail_moved(self, side: str, motor: str, direction: int, lag: float | None, now: float) -> float:
        """Gefahrener Weg (Anteil) einer Seite seit der Bezugslage, mit ``lag`` für ``direction``."""
        total = 0.0
        for d, start, end in self.state[side][motor].trail:
            d = int(d)
            used = lag if (lag is not None and d == direction) else self.lag_ms(side, motor, d)
            moved = max(0.0, min(end, now) - (start + used / 1000))
            total += d * moved * 1000 / self.travel_ms(side, motor, d)
        return total

    def _solve_lag(self, motor: str) -> dict[str, Any]:
        """Anlauf so bestimmen, dass beide Seiten seit der Bezugslage gleich weit gefahren sind."""
        now = self._clock()
        ref = self.level_ref.get(motor)
        if ref is None:
            end = self._level_at_end(motor)
            ref = 1.0 if end == UP else 0.0
        segments = [(seg, s) for s in self.sides for seg in self.state[s][motor].trail]
        if not segments:
            raise ValueError("Keine Fahrt seit der Bezugslage gefunden")
        last_seg, corrected = max(segments, key=lambda x: x[0][1])
        direction = int(last_seg[0])
        key = _lkey(direction)
        result: dict[str, Any] | None = None
        for side in (corrected, *[s for s in self.sides if s != corrected]):
            other = next(s for s in self.sides if s != side)
            target = self._trail_moved(other, motor, direction, None, now)

            def diff(lag: float) -> float:
                return self._trail_moved(side, motor, direction, lag, now) - target

            lo, hi = 0.0, float(MAX_LAG_MS)
            if diff(lo) * diff(hi) > 0:
                continue  # mit dieser Seite nicht lösbar, andere probieren
            for _ in range(40):
                mid = (lo + hi) / 2
                if diff(lo) * diff(mid) <= 0:
                    hi = mid
                else:
                    lo = mid
            self.calibration[side][motor][key] = _clamp_lag((lo + hi) / 2)
            result = {"motor": motor, "direction": direction, "side": side}
            break
        if result is None:
            raise ValueError(
                "Daraus lässt sich kein Versatz bestimmen. Bitte von „Alles flach“ aus neu anfangen."
            )
        # Beide stehen jetzt gleich hoch: das ist die neue Bezugslage.
        level = sum(
            min(1.0, max(0.0, ref + self._trail_moved(s, motor, direction, None, now))) for s in self.sides
        ) / len(self.sides)
        for s in self.sides:
            st = self.state[s][motor]
            st.pos, st.known, st.direction, st.at_end = level, True, 0, 0
        self._set_level_ref(motor, level)
        result["lags"] = {s: self.lag_ms(s, motor, direction) for s in self.sides}
        return result

    # --- Prüfen und feinjustieren

    def _blocked(self, motor: str, direction: int) -> str | None:
        """Grund, warum in diese Richtung nicht gefahren werden kann (Endlage), sonst None."""
        now = self._clock()
        for s in self.sides:
            st = self.state[s][motor]
            p = self.position(s, motor, now)
            if direction == UP and (st.at_end == UP or (st.known and p >= 0.97)):
                return "Das Bett steht schon ganz oben – erst ein Stück runterfahren."
            if direction == DOWN and (st.at_end == DOWN or (st.known and p <= 0.03)):
                return "Das Bett steht schon ganz unten – erst ein Stück hochfahren."
        return None

    async def calib_test_move(self, motor: str, direction: int, ms: int = TEST_MOVE_MS) -> None:
        """Kurze Fahrt beider Seiten mit Ausgleich – zum Beobachten, ob sie gleichzeitig loslaufen."""
        self._cancel_calibration()
        now = self._clock()
        self._settle_finished(now)
        reason = self._blocked(motor, direction)
        if reason:
            raise ValueError(reason)
        targets = {
            s: {
                motor: min(
                    1.0, max(0.0, self.position(s, motor, now) + direction * ms / self.travel_ms(s, motor, direction))
                )
            }
            for s in self.sides
        }
        await self.move_to(targets)

    def shift_lag(self, motor: str, direction: int, delta_ms: float) -> None:
        """Versatz nachjustieren: positiv = Seite A startet später (B bekommt mehr Vorlauf)."""
        self._shift_lag(motor, direction, delta_ms)
        self._save()
        self._notify()

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

    # --- Vollhub-Laufzeit messen

    async def calib_measure_start(self, motor: str, direction: int) -> None:
        """Vollhub messen: erst an die Gegen-Endlage (entfällt, wenn sie schon dort stehen),
        dann bis zum Endschalter fahren; der Benutzer tippt je Seite "angekommen"."""
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
        await self._relevel(motor, -direction)
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
        if not self.measure or self.measure["t0"].get(side) is None or side in self.measure["marks"]:
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
        self._cancel_calibration()
        self._stop_now()
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
                "phase": "relevel" if self._skip_event else self.measure["phase"],
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
            "favorites": [{"id": k, **v} for k, v in self.favorites.items()],
            "mattress": self.mattress,
            "max_diff": self.max_diff,
            "limit_hit": self._limit_hit[1]
            if self._limit_hit and now - self._limit_hit[0] < 4
            else None,
            "active_favorite": self.active_favorite(),
            "measure": measure,
            "adjust": self.adjust,
            "level_ref": {m: self.level_ref[m] is not None or bool(self._level_at_end(m)) for m in self.motors},
            "relevel": self._skip_event is not None,
            "motion_remaining": self._motion_remaining(now),
            "commands": list(self.command_log)[-15:],
        }

    def _motion_remaining(self, now: float) -> float | None:
        ends = [
            self.state[s][m].end - now for s in self.sides for m in self.motors if self.is_moving(s, m, now)
        ]
        return round(max(ends), 1) if ends else None


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
