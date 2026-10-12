"""Tests der Sync-Logik gegen eine simulierte Box.

Standardszenario wie beim echten Bett: Beide Motoren sind gleich schnell, aber
Seite B läuft später an (Totweg). Ein zweites Szenario prüft unterschiedliche
Geschwindigkeiten.
"""

from __future__ import annotations

import asyncio
import copy
import time

import pytest

import auping_local.controller as controller_mod
from auping_local.api import AupingConnectApi, normalize_xkey, parse_share_code
from auping_local.const import (
    DOWN,
    MODE_PARALLEL,
    MODE_SEQUENTIAL,
    UP,
    motors_for_bed_type,
    sides_for_cbu_type,
)
from auping_local.controller import BedController, Step, _split_long
from auping_sim import SimBox

MOTORS = ["head", "back", "legs"]
SPEED = {
    "head": {"up": 1200, "down": 1100},
    "back": {"up": 2000, "down": 1800},
    "legs": {"up": 1500, "down": 1400},
}
TRUE = {"a": copy.deepcopy(SPEED), "b": copy.deepcopy(SPEED)}
LAG = {
    "a": {"head": {"up": 0, "down": 0}, "back": {"up": 100, "down": 150}, "legs": {"up": 100, "down": 100}},
    "b": {"head": {"up": 0, "down": 0}, "back": {"up": 500, "down": 350}, "legs": {"up": 300, "down": 300}},
}
# Zweites Szenario: B ist langsamer, kein Anlaufunterschied
SLOW_B = {
    "a": copy.deepcopy(SPEED),
    "b": {
        "head": {"up": 1200, "down": 1100},
        "back": {"up": 2600, "down": 2300},
        "legs": {"up": 1700, "down": 1600},
    },
}


@pytest.fixture(autouse=True)
def short_overrun(monkeypatch):
    """Kürzerer Nachlauf, damit die Tests mit Sekunden statt Minuten auskommen."""
    monkeypatch.setattr(controller_mod, "OVERRUN_MIN_MS", 400)


class FakeApi:
    def __init__(self, sim: SimBox, latency: float = 0.02) -> None:
        self.sim = sim
        self.latency = latency
        self._builder = AupingConnectApi(None, "sim", "abcdefgh", "regular")  # type: ignore[arg-type]

    async def send_motors(self, cbu, motors):
        body = self._builder.build_motor_body(cbu, motors)
        await asyncio.sleep(self.latency / 2)
        started = time.monotonic()
        self.sim.command(body)
        await asyncio.sleep(self.latency / 2)
        return started


def make(
    mode=MODE_PARALLEL,
    parallel_box=True,
    travel=TRUE,
    lag=LAG,
    calibrate_travel=True,
    calibrate_lag=True,
    start=0.0,
):
    sim = SimBox(copy.deepcopy(travel), parallel=parallel_box, lag=copy.deepcopy(lag))
    ctl = BedController(FakeApi(sim), ["a", "b"], MOTORS, mode=mode)
    for s in ("a", "b"):
        for m in MOTORS:
            if calibrate_travel:
                ctl.calibration[s][m]["up"] = travel[s][m]["up"]
                ctl.calibration[s][m]["down"] = travel[s][m]["down"]
            if calibrate_lag:
                ctl.calibration[s][m]["lag_up"] = lag[s][m]["up"]
                ctl.calibration[s][m]["lag_down"] = lag[s][m]["down"]
            ctl.state[s][m].pos = start
            ctl.state[s][m].known = True
            sim.motors[s][m].pos = start
    return ctl, sim


async def settle(ctl: BedController, sim: SimBox, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    await asyncio.wait_for(ctl.wait_idle(), timeout)
    while time.monotonic() < end:
        if not ctl.any_moving() and not any(
            sim.moving(s, m) for s in ("a", "b") for m in MOTORS
        ):
            break
        await asyncio.sleep(0.05)
    await asyncio.wait_for(ctl.wait_idle(), timeout)
    await asyncio.sleep(0.05)


def diff(sim: SimBox, motor: str = "back") -> float:
    return sim.position("a", motor) - sim.position("b", motor)


# --------------------------------------------------------------------------- Protokoll


def test_body_matches_app_format():
    api = AupingConnectApi(None, "h", "AbCd EfGh", "regular")  # type: ignore[arg-type]
    assert api._xkey == "abcdefgh"
    assert api.build_motor_body(1, {"back": (UP, 1234)}) == {
        "CBU": 1,
        "motor_1": {"Action": 0, "TimeInMs": 0},
        "motor_2": {"Action": 1, "TimeInMs": 1234},
        "motor_3": {"Action": 0, "TimeInMs": 0},
    }
    royal = AupingConnectApi(None, "h", "x", "royal")  # type: ignore[arg-type]
    body = royal.build_motor_body(0, {"lower_back": (DOWN, 500), "legs": (UP, 700)})
    assert body["motor_3"] == {"Action": 2, "TimeInMs": 500}
    assert body["motor_4"] == {"Action": 1, "TimeInMs": 700}
    assert body["motor_5"] == {"Action": 0, "TimeInMs": 0}


def test_share_code_parsing():
    assert parse_share_code("192.168.1.50-abcdefgh") == ("192.168.1.50", "abcdefgh")
    assert parse_share_code(" ABCD efgh ") == (None, "abcdefgh")
    assert normalize_xkey("Ab cD") == "abcd"


def test_bed_type_mapping():
    remote, left, right = motors_for_bed_type(41)
    assert remote == "regular"
    assert left == right == ["head", "back", "legs"]
    assert motors_for_bed_type(23)[0] == "royal"
    assert motors_for_bed_type(5) == (None, [], [])
    assert sides_for_cbu_type(33) == ["a", "b"]
    assert sides_for_cbu_type(31) == ["a"]


def test_split_long_commands():
    steps = _split_long(Step(0.0, 1, ("a",), {"back": (UP, 70000), "legs": (UP, 5000)}))
    assert [round(s.at) for s in steps] == [0, 29, 58]
    assert steps[0].motors == {"back": (UP, 30000), "legs": (UP, 5000)}
    assert steps[1].motors == {"back": (UP, 30000)}
    assert steps[2].motors == {"back": (UP, 12000)}
    stop = _split_long(Step(0.0, 0, ("a", "b"), {}))
    assert len(stop) == 1 and stop[0].motors == {}


# --------------------------------------------------------------------------- Sync


async def test_without_compensation_late_side_stays_behind():
    """Ausgangsproblem: B läuft später an und bleibt ohne Ausgleich zurück."""
    ctl, sim = make(calibrate_lag=False)
    await ctl.move_to({"a": {"back": 0.5}, "b": {"back": 0.5}})
    await settle(ctl, sim)
    assert diff(sim) > 0.15
    await ctl.async_shutdown()


@pytest.mark.parametrize(
    "mode,parallel_box",
    [(MODE_PARALLEL, True), (MODE_SEQUENTIAL, False)],
)
async def test_sync_move_reaches_same_height(mode, parallel_box):
    ctl, sim = make(mode, parallel_box)
    await ctl.move_to({"a": {"back": 0.5, "legs": 0.3}, "b": {"back": 0.5, "legs": 0.3}})
    await settle(ctl, sim)
    assert abs(sim.position("a", "back") - 0.5) < 0.03
    assert abs(diff(sim)) < 0.02
    assert abs(diff(sim, "legs")) < 0.03
    assert abs(ctl.position("a", "back") - sim.position("a", "back")) < 0.03
    await ctl.async_shutdown()


async def test_lockstep_during_motion():
    """Mit Startversatz laufen beide Seiten schon während der Fahrt gleich hoch."""
    ctl, sim = make()
    await ctl.move_to({"a": {"back": 0.9}, "b": {"back": 0.9}})
    await asyncio.sleep(1.0)
    assert 0.2 < sim.position("a", "back") < 0.8
    assert abs(diff(sim)) < 0.03
    await settle(ctl, sim)
    assert abs(diff(sim)) < 0.02
    await ctl.async_shutdown()


async def test_parallel_mode_on_serial_box_fails():
    """Kann die Box nur eine Seite gleichzeitig, braucht es den Modus 'nacheinander'."""
    ctl, sim = make(MODE_PARALLEL, parallel_box=False)
    await ctl.move_to({"a": {"back": 0.5}, "b": {"back": 0.5}})
    await settle(ctl, sim)
    assert abs(diff(sim)) > 0.2
    await ctl.async_shutdown()


@pytest.mark.parametrize("mode", [MODE_PARALLEL, MODE_SEQUENTIAL])
async def test_hold_and_release_both_sides_stay_level(mode):
    ctl, sim = make(mode, parallel_box=(mode == MODE_PARALLEL))
    await ctl.start_move(["a", "b"], "back", UP)
    await asyncio.sleep(1.2)
    await ctl.stop_motor(["a", "b"], "back")
    await settle(ctl, sim)
    assert 0.2 < sim.position("a", "back") < 0.7
    assert abs(diff(sim)) < 0.025
    await ctl.async_shutdown()


async def test_quick_tap_before_late_side_started():
    """Sehr kurz getippt: die spät startende Seite hat sich noch nicht bewegt."""
    ctl, sim = make()
    await ctl.start_move(["a", "b"], "back", UP)
    await asyncio.sleep(0.15)
    await ctl.stop_motor(["a", "b"], "back")
    await settle(ctl, sim)
    assert abs(diff(sim)) < 0.025
    await ctl.async_shutdown()


async def test_hold_down_from_top():
    ctl, sim = make(start=1.0)
    await ctl.start_move(["a", "b"], "back", DOWN)
    await asyncio.sleep(1.0)
    await ctl.stop_motor(["a", "b"], "back")
    await settle(ctl, sim)
    assert 0.3 < sim.position("a", "back") < 0.85
    assert abs(diff(sim)) < 0.025
    await ctl.async_shutdown()


async def test_different_speeds_are_compensated_too():
    ctl, sim = make(travel=SLOW_B, lag={}, calibrate_lag=False)
    await ctl.move_to({"a": {"back": 0.6}, "b": {"back": 0.6}})
    await settle(ctl, sim)
    assert abs(diff(sim)) < 0.02
    await ctl.async_shutdown()


async def test_single_side_release_stops_only_that_motor():
    ctl, sim = make()
    await ctl.start_move(["a"], "legs", UP)
    await asyncio.sleep(0.3)
    await ctl.start_move(["a"], "back", UP)
    await asyncio.sleep(0.3)
    assert sim.moving("a", "legs"), "Beinteil muss beim zweiten Befehl weiterlaufen"
    await ctl.stop_motor(["a"], "back")
    await asyncio.wait_for(ctl.wait_idle(), 5)
    await asyncio.sleep(0.05)
    assert not sim.moving("a", "back")
    assert sim.moving("a", "legs")
    assert not sim.moving("b", "back")
    await ctl.stop_all()
    await settle(ctl, sim)
    await ctl.async_shutdown()


async def test_flat_and_home():
    ctl, sim = make(start=0.6)
    await ctl.flat()
    await settle(ctl, sim)
    for s in ("a", "b"):
        for m in MOTORS:
            assert sim.position(s, m) == 0.0
            assert ctl.position(s, m) == 0.0
            assert ctl.state[s][m].known
    # Unbekannte Lage: Schätzung sagt 0, in Wahrheit steht B auf 0.7
    sim.motors["b"]["back"].pos = 0.7
    ctl.state["b"]["back"].known = False
    await ctl.home()
    await settle(ctl, sim)
    assert sim.position("b", "back") == 0.0
    assert ctl.state["b"]["back"].known
    await ctl.async_shutdown()


async def test_favorites_roundtrip():
    ctl, sim = make()
    await ctl.move_to({"a": {"back": 0.4}, "b": {"back": 0.7}})
    await settle(ctl, sim)
    ctl.save_favorite("1")
    await ctl.flat()
    await settle(ctl, sim)
    assert await ctl.goto_favorite("1")
    await settle(ctl, sim)
    assert abs(sim.position("a", "back") - 0.4) < 0.03
    assert abs(sim.position("b", "back") - 0.7) < 0.03
    assert not await ctl.goto_favorite("2")
    data = ctl.export_state()
    ctl2, _ = make(calibrate_travel=False, calibrate_lag=False)
    ctl2.import_state(data)
    assert ctl2.calibration == ctl.calibration
    assert ctl2.favorites["1"] == ctl.favorites["1"]
    await ctl.async_shutdown()


# --------------------------------------------------------------------------- Kalibrierung


async def test_measure_full_travel():
    ctl, sim = make(calibrate_travel=False, start=0.4)
    for s in ("a", "b"):  # falsch geschätzt, aber kurz, damit der Test schnell bleibt
        ctl.calibration[s]["back"].update(up=3000, down=3000)
    await ctl.calib_measure_start("back", UP)
    deadline = time.monotonic() + 15
    while ctl.measure is None or ctl.measure["phase"] != "running":
        assert time.monotonic() < deadline
        await asyncio.sleep(0.02)
    assert sim.position("a", "back") == 0.0 and sim.position("b", "back") == 0.0
    marked: set[str] = set()
    while len(marked) < 2 and time.monotonic() < deadline:
        for s in ("a", "b"):
            if s not in marked and sim.position(s, "back") >= 1.0:
                assert ctl.calib_mark(s) is not None
                marked.add(s)
        await asyncio.sleep(0.01)
    result = await ctl.calib_measure_finish(apply=True)
    assert set(result) == {"a", "b"}
    for s in ("a", "b"):
        assert abs(ctl.calibration[s]["back"]["up"] - 2000) < 80
    assert ctl.position("a", "back") == 1.0 and ctl.state["a"]["back"].known
    await settle(ctl, sim)
    await ctl.async_shutdown()


async def answer_offset_rounds(ctl: BedController, sim: SimBox, max_rounds: int = 14) -> dict:
    """Spielt den Benutzer: schaut hin und sagt, welche Seite höher steht."""
    for _ in range(max_rounds):
        for _ in range(400):
            if ctl.offset and ctl.offset["phase"] == "feedback":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("Testfahrt wurde nicht fertig")
        d = diff(sim)
        if d > 0.008:
            answer = "a_higher"
        elif d < -0.008:
            answer = "b_higher"
        else:
            answer = "equal"
        result = await ctl.calib_offset_feedback(answer, "big")
        if result.get("done"):
            return result
    raise AssertionError("Versatz-Abgleich konvergiert nicht")


@pytest.mark.parametrize("direction,expected", [(UP, 400), (DOWN, 200)])
async def test_offset_calibration_finds_lag_difference(direction, expected):
    ctl, sim = make(calibrate_lag=False, start=0.3)
    ctl.offset_test_ms = 700
    await ctl.calib_offset_start("back", direction)
    result = await answer_offset_rounds(ctl, sim)
    key = "lag_up" if direction == UP else "lag_down"
    found = ctl.calibration["b"]["back"][key] - ctl.calibration["a"]["back"][key]
    assert abs(found - expected) < 40, result
    # Danach fährt das Bett in dieser Richtung synchron
    target = 0.85 if direction == UP else 0.15
    await ctl.move_to({"a": {"back": target}, "b": {"back": target}})
    await settle(ctl, sim)
    assert abs(diff(sim)) < 0.02
    await ctl.async_shutdown()


async def test_offset_calibration_cancel():
    ctl, sim = make(calibrate_lag=False)
    await ctl.calib_offset_start("back", UP)
    await asyncio.sleep(0.2)
    ctl.calib_cancel()
    assert ctl.offset is None
    await ctl.stop_all()
    await settle(ctl, sim)
    await ctl.async_shutdown()
