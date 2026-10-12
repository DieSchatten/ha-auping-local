"""Simulierte Auping-Connect-Box mit physikalischen Motoren.

Wird von den Tests direkt benutzt und von ``mock_box.py`` als HTTP-Server
angeboten. Die "wahren" Laufzeiten der Motoren sind absichtlich je Seite
verschieden – genau das Problem, das die Kalibrierung lösen soll.
"""

from __future__ import annotations

from dataclasses import dataclass
import time

REGULAR_INDEX = {1: "head", 2: "back", 3: "legs"}
SIDES_FOR_CBU = {0: ("a", "b"), 1: ("a",), 2: ("b",)}


@dataclass
class SimMotor:
    pos: float = 0.0
    direction: int = 0  # +1 hoch, -1 runter
    move_start: float = 0.0  # Befehl + Anlaufverzögerung
    end: float = 0.0


class SimBox:
    def __init__(
        self,
        travel: dict[str, dict[str, dict[str, float]]],
        parallel: bool = True,
        clock=time.monotonic,
        lag: dict[str, dict[str, dict[str, float]]] | None = None,
    ) -> None:
        """``travel[side][motor]["up"|"down"]`` = echte Vollhub-Zeit in ms,
        ``lag`` = echte Anlaufverzögerung (Totweg) in ms."""
        self.travel = travel
        self.lag = lag or {}
        self.parallel = parallel
        self.clock = clock
        self.motors = {s: {m: SimMotor() for m in travel[s]} for s in travel}
        self.log: list[tuple[float, int, dict]] = []

    def _pos(self, side: str, motor: str, now: float) -> float:
        mo = self.motors[side][motor]
        if not mo.direction:
            return mo.pos
        t = self.travel[side][motor]["up" if mo.direction > 0 else "down"]
        elapsed = max(0.0, min(now, mo.end) - mo.move_start)
        return min(1.0, max(0.0, mo.pos + mo.direction * elapsed * 1000 / t))

    def position(self, side: str, motor: str) -> float:
        return self._pos(side, motor, self.clock())

    def moving(self, side: str, motor: str) -> bool:
        mo = self.motors[side][motor]
        now = self.clock()
        if not mo.direction or now >= mo.end:
            return False
        if now < mo.move_start:
            return True
        p = self._pos(side, motor, now)
        return not ((mo.direction > 0 and p >= 1.0) or (mo.direction < 0 and p <= 0.0))

    def _stop_motor(self, side: str, motor: str, now: float) -> None:
        mo = self.motors[side][motor]
        mo.pos = self._pos(side, motor, now)
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
                        mo.end = now + ms / 1000  # läuft weiter, kein neuer Anlauf
                        continue
                    self._stop_motor(side, motor, now)
                    key = "up" if direction > 0 else "down"
                    lag = self.lag.get(side, {}).get(motor, {}).get(key, 0)
                    mo.direction = direction
                    mo.move_start = now + lag / 1000
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
