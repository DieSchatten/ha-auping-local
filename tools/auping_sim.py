"""Simulierte Auping-Connect-Box mit physikalischen Motoren.

Wird von den Tests direkt benutzt und von ``mock_box.py`` als HTTP-Server
angeboten. Modell wie beim echten Bett: Der Motor schiebt den Lattenrost nur.
In der flachen Lage fährt er weiter zurück als der aufliegende Rost – beim
Hochfahren läuft er diesen Leerweg (``gap``) erst leer, bevor er anhebt. Aus einer
Zwischenlage und beim Runterfahren gibt es keinen Leerweg. Der Leerweg ist je
Seite verschieden – genau das Problem, das die Kalibrierung lösen soll.
"""

from __future__ import annotations

from dataclasses import dataclass
import time

REGULAR_INDEX = {1: "head", 2: "back", 3: "legs"}
SIDES_FOR_CBU = {0: ("a", "b"), 1: ("a",), 2: ("b",)}


@dataclass
class SimMotor:
    pos: float = 0.0  # Lage des Rosts 0..1
    slack: float = 0.0  # offener Leerweg in ms (nur unten)
    direction: int = 0  # +1 hoch, -1 runter
    cmd_start: float = 0.0
    end: float = 0.0


class SimBox:
    def __init__(
        self,
        travel: dict[str, dict[str, dict[str, float]]],
        parallel: bool = True,
        clock=time.monotonic,
        gap: dict[str, dict[str, float]] | None = None,
    ) -> None:
        """``travel[side][motor]["up"|"down"]`` = echte Vollhub-Zeit in ms,
        ``gap[side][motor]`` = echter Leerweg unten in ms."""
        self.travel = travel
        self.gap = gap or {}
        self.parallel = parallel
        self.clock = clock
        self.motors = {
            s: {m: SimMotor(slack=self._gap(s, m)) for m in travel[s]} for s in travel
        }
        self.log: list[tuple[float, int, dict]] = []

    def _gap(self, side: str, motor: str) -> float:
        return float(self.gap.get(side, {}).get(motor, 0))

    def _state_at(self, side: str, motor: str, now: float) -> tuple[float, float]:
        mo = self.motors[side][motor]
        if not mo.direction:
            return mo.pos, mo.slack
        t = max(0.0, min(now, mo.end) - mo.cmd_start) * 1000
        if mo.direction > 0:
            used = min(mo.slack, t)
            return min(1.0, mo.pos + (t - used) / self.travel[side][motor]["up"]), mo.slack - used
        pos = mo.pos - t / self.travel[side][motor]["down"]
        if pos < 0:
            return 0.0, min(self._gap(side, motor), mo.slack + (-pos) * self.travel[side][motor]["down"])
        return pos, mo.slack

    def position(self, side: str, motor: str) -> float:
        return self._state_at(side, motor, self.clock())[0]

    def set_position(self, side: str, motor: str, pos: float) -> None:
        mo = self.motors[side][motor]
        mo.pos, mo.direction = pos, 0
        mo.slack = self._gap(side, motor) if pos <= 0 else 0.0

    def moving(self, side: str, motor: str) -> bool:
        """Läuft der Motor noch sichtbar oder im Leerweg?"""
        mo = self.motors[side][motor]
        now = self.clock()
        if not mo.direction or now >= mo.end:
            return False
        pos, slack = self._state_at(side, motor, now)
        if mo.direction > 0:
            return pos < 1.0
        return pos > 0.0 or slack < self._gap(side, motor)

    def _stop_motor(self, side: str, motor: str, now: float) -> None:
        mo = self.motors[side][motor]
        mo.pos, mo.slack = self._state_at(side, motor, now)
        mo.direction = 0

    def _stop_side(self, side: str, now: float) -> None:
        for motor in self.motors[side]:
            self._stop_motor(side, motor, now)

    def command(self, body: dict) -> None:
        now = self.clock()
        cbu = int(body["CBU"])
        self.log.append((now, cbu, body))
        sides = SIDES_FOR_CBU[cbu]
        if not self.parallel and cbu != 0:
            # Box, die nur eine Seite gleichzeitig bedienen kann.
            for other in ("a", "b"):
                if other not in sides and other in self.motors:
                    self._stop_side(other, now)
        for side in sides:
            if side not in self.motors:
                continue
            for index, motor in REGULAR_INDEX.items():
                if motor not in self.motors[side]:
                    continue
                cmd = body.get(f"motor_{index}") or {}
                action, ms = int(cmd.get("Action", 0)), int(cmd.get("TimeInMs", 0))
                mo = self.motors[side][motor]
                if action in (1, 2) and ms > 0:
                    direction = 1 if action == 1 else -1
                    if mo.direction == direction and now < mo.end:
                        mo.end = now + ms / 1000  # läuft einfach weiter
                        continue
                    self._stop_motor(side, motor, now)
                    mo.direction = direction
                    mo.cmd_start = now
                    mo.end = now + ms / 1000
                else:
                    self._stop_motor(side, motor, now)

    def config(self) -> dict:
        return {
            "name": "Auping Connect SIM",
            "interface": "wlan0",
            "hostname": "auping-connect-sim",
            "ip": "127.0.0.1",
            "wifi_configured": 1,
            "stored_ap": "SIM",
            "mac_address": "02:00:00:a0:b1:c2",
            "cbu_type": 33,
            "sync": 0,
            "bed_type": 41,
            "alarm_a_utc": 0,
            "alarm_a_cbu": 0,
            "alarm_a_command": 0,
            "alarm_a_duration": 0,
            "alarm_b_utc": 0,
            "alarm_b_cbu": 0,
            "alarm_b_command": 0,
            "alarm_b_duration": 0,
            "utc": int(time.time()),
            "current_system_time": int(time.time()),
            "time_last_command ": 0,
            "cbu_a_command": 0,
            "cbu_b_command": 0,
            "firmware": "1.05.6-sim",
        }
