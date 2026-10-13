"""Tests der Sync-Logik gegen eine simulierte Box.

Standardszenario wie beim echten Bett: Beide Motoren sind gleich schnell, aber
der Motor von Seite B läuft beim Hochfahren aus der flachen Lage länger leer
(Leerweg), bevor er den Rost anhebt. Ein zweites Szenario prüft unterschiedliche
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
    MATTRESS_DUAL,
    MATTRESS_SINGLE,
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
# Leerweg unten in ms
GAP = {"a": {"head": 0, "back": 100, "legs": 100}, "b": {"head": 0, "back": 500, "legs": 300}}
# Zweites Szenario: B ist langsamer, kein Leerweg
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
    gap=GAP,
    calibrate_travel=True,
    calibrate_gap=True,
    start=0.0,
):
    sim = SimBox(copy.deepcopy(travel), parallel=parallel_box, gap=copy.deepcopy(gap))
    ctl = BedController(FakeApi(sim), ["a", "b"], MOTORS, mode=mode)
    for s in ("a", "b"):
        for m in MOTORS:
            if calibrate_travel:
                ctl.calibration[s][m]["up"] = travel[s][m]["up"]
                ctl.calibration[s][m]["down"] = travel[s][m]["down"]
            if calibrate_gap:
                ctl.calibration[s][m]["gap"] = gap.get(s, {}).get(m, 0)
            set_pos(ctl, sim, s, m, start)
    return ctl, sim


def set_pos(ctl: BedController, sim: SimBox, side: str, motor: str, pos: float) -> None:
    """Simuliertes Bett und Schätzung auf dieselbe Lage setzen (unten: Leerweg offen)."""
    st = ctl.state[side][motor]
    st.pos, st.known, st.direction = pos, True, 0
    st.at_end = DOWN if pos <= 0 else UP if pos >= 1 else 0
    st.slack = ctl.gap_ms(side, motor) if pos <= 0 else 0.0
    sim.set_position(side, motor, pos)


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
    ctl, sim = make(calibrate_gap=False)
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
    ctl, sim = make(travel=SLOW_B, gap={}, calibrate_gap=False)
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
    sim.set_position("b", "back", 0.7)
    ctl.state["b"]["back"].known = False
    await ctl.home()
    await settle(ctl, sim)
    assert sim.position("b", "back") == 0.0
    assert ctl.state["b"]["back"].known
    await ctl.async_shutdown()


async def test_favorites_drive_relative_to_current_position():
    ctl, sim = make()
    await ctl.move_to({"a": {"back": 0.4, "legs": 0.3}, "b": {"back": 0.7, "legs": 0.3}})
    await settle(ctl, sim)
    lesen = ctl.save_favorite("Lesen")
    await ctl.move_to({"a": {"back": 0.2, "legs": 0.0}, "b": {"back": 0.2, "legs": 0.0}})
    await settle(ctl, sim)
    schlafen = ctl.save_favorite("Schlafen")
    assert [f["name"] for f in ctl.snapshot()["favorites"]] == ["Lesen", "Schlafen"]
    assert ctl.active_favorite() == schlafen

    assert await ctl.goto_favorite("lesen")  # Name, Groß/klein egal
    await settle(ctl, sim)
    assert abs(sim.position("a", "back") - 0.4) < 0.03
    assert abs(sim.position("b", "back") - 0.7) < 0.03
    assert ctl.active_favorite() == lesen

    # Zweites Antippen: kein einziger Befehl an die Box
    sent = len(sim.log)
    assert await ctl.goto_favorite(lesen)
    await settle(ctl, sim)
    assert len(sim.log) == sent

    # Vom höheren Favoriten zum niedrigeren: es geht abwärts, nicht "x nach oben"
    assert await ctl.goto_favorite("Schlafen")
    await asyncio.wait_for(ctl.wait_idle(), 5)
    actions = {b["motor_2"]["Action"] for _, cbu, b in sim.log[sent:]}
    assert actions == {2}
    await settle(ctl, sim)
    assert abs(sim.position("a", "back") - 0.2) < 0.03
    assert abs(sim.position("b", "back") - 0.2) < 0.03
    assert not await ctl.goto_favorite("gibt es nicht")
    await ctl.async_shutdown()


async def test_favorite_for_one_side_only():
    ctl, sim = make()
    await ctl.move_to({"a": {"back": 0.5}, "b": {"back": 0.3}})
    await settle(ctl, sim)
    fav = ctl.save_favorite("Fernsehen")
    await ctl.flat()
    await settle(ctl, sim)
    await ctl.goto_favorite(fav, sides=["a"])
    await settle(ctl, sim)
    assert abs(sim.position("a", "back") - 0.5) < 0.03
    assert sim.position("b", "back") == 0.0
    await ctl.async_shutdown()


def test_favorite_management():
    ctl, _ = make()
    first = ctl.save_favorite("Lesen")
    second = ctl.save_favorite()
    assert ctl.favorites[second]["name"] == f"Favorit {second}"
    with pytest.raises(ValueError):
        ctl.save_favorite("  lesen ")
    with pytest.raises(ValueError):
        ctl.rename_favorite(second, "")
    ctl.rename_favorite(second, "Schlafen")
    ctl.move_favorite(second, -1)
    assert list(ctl.favorites) == [second, first]
    ctl.save_favorite(fav_id=first)  # überschreiben behält den Namen
    assert ctl.favorites[first]["name"] == "Lesen"
    ctl.delete_favorite(first)
    assert list(ctl.favorites) == [second]
    third = ctl.save_favorite("Neu")
    assert int(third) > int(second)
    for i in range(20 - len(ctl.favorites)):
        ctl.save_favorite(f"F{i}")
    with pytest.raises(ValueError):
        ctl.save_favorite("zu viel")


def test_old_favorite_format_is_migrated():
    ctl, _ = make()
    ctl.import_state(
        {"favorites": {"1": {"a": {"back": 0.4}, "b": {"back": 0.5}}, "2": None, "3": None}}
    )
    assert ctl.favorites == {
        "1": {"name": "Favorit 1", "positions": {"a": {"back": 0.4}, "b": {"back": 0.5}}}
    }
    data = ctl.export_state()
    ctl2, _ = make(calibrate_travel=False, calibrate_gap=False)
    ctl2.import_state(data)
    assert ctl2.favorites == ctl.favorites
    assert ctl2.calibration == ctl.calibration


async def test_endpoint_not_driven_again():
    """Steht ein Motor nachweislich am Endschalter, gibt es keinen weiteren Befehl."""
    ctl, sim = make(start=0.5)
    await ctl.flat()
    await settle(ctl, sim)
    sent = len(sim.log)
    await ctl.flat()
    await settle(ctl, sim)
    assert len(sim.log) == sent
    # Referenzfahrt fährt trotzdem bewusst runter
    await ctl.home()
    await asyncio.wait_for(ctl.wait_idle(), 5)
    assert len(sim.log) > sent
    await ctl.stop_all()
    await settle(ctl, sim)
    await ctl.async_shutdown()


# --------------------------------------------------------------------------- Matratzenmodus


def set_both(ctl, sim, motor, a, b):
    for side, pos in (("a", a), ("b", b)):
        set_pos(ctl, sim, side, motor, pos)


async def test_single_mattress_limits_one_side():
    ctl, sim = make(start=0.3)
    ctl.mattress, ctl.max_diff = MATTRESS_SINGLE, 0.10
    await ctl.start_move(["a"], "back", UP)  # Taste lange halten
    await asyncio.sleep(1.6)
    await ctl.stop_motor(["a"], "back")
    await settle(ctl, sim)
    assert abs(sim.position("a", "back") - 0.4) < 0.03
    assert sim.position("b", "back") == pytest.approx(0.3)
    assert ctl.snapshot()["limit_hit"]
    await ctl.move_to({"a": {"back": 0.9}})
    await settle(ctl, sim)
    assert abs(sim.position("a", "back") - 0.4) < 0.03
    # Beide zusammen dürfen frei fahren
    await ctl.move_to({"a": {"back": 0.8}, "b": {"back": 0.8}})
    await settle(ctl, sim)
    assert abs(sim.position("b", "back") - 0.8) < 0.03
    await ctl.async_shutdown()


async def test_single_mattress_never_reverses_and_allows_closing_gap():
    ctl, sim = make()
    ctl.mattress, ctl.max_diff = MATTRESS_SINGLE, 0.10
    set_both(ctl, sim, "back", 0.6, 0.2)  # schon zu weit auseinander
    sent = len(sim.log)
    notes = []
    ctl.add_listener(lambda: notes.append(ctl.snapshot()["limit_hit"]))
    await ctl.start_move(["a"], "back", UP)  # würde den Abstand vergrößern
    await settle(ctl, sim)
    assert len(sim.log) == sent and sim.position("a", "back") == pytest.approx(0.6)
    assert any(notes), "Begrenzung muss gemeldet werden, auch ohne Fahrt"
    await ctl.start_move(["a"], "back", DOWN)  # Richtung B ist erlaubt, bis 10 % unter B
    await asyncio.sleep(1.5)
    await ctl.stop_motor(["a"], "back")
    await settle(ctl, sim)
    assert abs(sim.position("a", "back") - 0.1) < 0.03
    await ctl.async_shutdown()


async def test_single_mattress_limits_favorites_and_timed_moves():
    ctl, sim = make()
    await ctl.move_to({"a": {"back": 0.7}, "b": {"back": 0.3}})
    await settle(ctl, sim)
    fav = ctl.save_favorite("Ungleich")
    await ctl.flat()
    await settle(ctl, sim)
    ctl.mattress, ctl.max_diff = MATTRESS_SINGLE, 0.10
    await ctl.goto_favorite(fav)
    await settle(ctl, sim)
    pa, pb = sim.position("a", "back"), sim.position("b", "back")
    assert abs(pa - pb - 0.10) < 0.03 and abs((pa + pb) / 2 - 0.5) < 0.03
    await ctl.nudge("b", "back", DOWN, 1500)  # Dienst "Fahren für Zeit"
    await settle(ctl, sim)
    assert pa - sim.position("b", "back") < 0.13
    await ctl.async_shutdown()


async def test_two_mattresses_are_free():
    ctl, sim = make(start=0.3)
    ctl.mattress = MATTRESS_DUAL
    await ctl.move_to({"a": {"back": 0.8}})
    await settle(ctl, sim)
    assert abs(sim.position("a", "back") - 0.8) < 0.03
    assert ctl.snapshot()["limit_hit"] is None
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


def at_bottom(ctl: BedController, sim: SimBox, motor: str = "back") -> None:
    for s in ("a", "b"):
        set_pos(ctl, sim, s, motor, 0.0)


async def hold(ctl: BedController, sides, motor: str, direction: int, seconds: float) -> None:
    await ctl.start_move(sides, motor, direction)
    await asyncio.sleep(seconds)
    await ctl.stop_motor(sides, motor)


async def test_adjust_learns_gap():
    """Ablauf wie vom Benutzer gewünscht: flach -> beide hoch -> zurückliegende Seite nachfahren -> passt."""
    ctl, sim = make(calibrate_gap=False)
    at_bottom(ctl, sim)
    with pytest.raises(ValueError):
        ctl.adjust_start("back")  # noch nicht gefahren
    await hold(ctl, ["a", "b"], "back", UP, 1.2)
    await settle(ctl, sim)
    assert diff(sim) > 0.15  # B (längerer Leerweg) liegt zurück
    ctl.adjust_start("back")
    # Benutzer fährt B aus der Zwischenlage nach – dort gibt es keinen Leerweg
    await hold(ctl, ["b"], "back", UP, diff(sim) * 2000 / 1000)
    await settle(ctl, sim)
    assert abs(diff(sim)) < 0.02
    result = await ctl.adjust_finish(apply=True)
    found = ctl.gap_ms("b", "back") - ctl.gap_ms("a", "back")
    assert result["side"] == "b" and abs(found - 400) < 50, result
    assert abs(ctl.position("a", "back") - ctl.position("b", "back")) < 0.001
    # Aus der Zwischenlage weiter hoch: kein Ausgleich nötig, beide bleiben gleich
    await hold(ctl, ["a", "b"], "back", UP, 0.3)
    await settle(ctl, sim)
    assert abs(diff(sim)) < 0.02
    # Und aus der flachen Lage heben beide jetzt gleichzeitig an
    await ctl.flat()
    await settle(ctl, sim)
    await hold(ctl, ["a", "b"], "back", UP, 1.0)
    await settle(ctl, sim)
    assert 0.2 < sim.position("a", "back") < 0.6
    assert abs(diff(sim)) < 0.02
    await ctl.async_shutdown()


async def test_midway_restart_needs_no_offset():
    """Hochfahren aus einer Zwischenlage: beide Motoren liegen an, keiner wird verzögert."""
    ctl, sim = make()
    await ctl.move_to({"a": {"back": 0.3}, "b": {"back": 0.3}})
    await settle(ctl, sim)
    assert abs(diff(sim)) < 0.02
    sent = len(sim.log)
    await ctl.move_to({"a": {"back": 0.6}, "b": {"back": 0.6}})
    await settle(ctl, sim)
    starts = [ts for ts, _, b in sim.log[sent:] if b["motor_2"]["Action"] == 1]
    assert max(starts) - min(starts) < 0.1, "kein künstlicher Startversatz in der Zwischenlage"
    assert abs(sim.position("a", "back") - 0.6) < 0.02 and abs(diff(sim)) < 0.02
    # Runter und wieder hoch aus der Zwischenlage: ebenfalls gleich
    await hold(ctl, ["a", "b"], "back", DOWN, 0.4)
    await settle(ctl, sim)
    await hold(ctl, ["a", "b"], "back", UP, 0.4)
    await settle(ctl, sim)
    assert abs(diff(sim)) < 0.02
    await ctl.async_shutdown()


async def test_adjust_after_flat_even_during_overrun():
    """Nach „Alles flach“ sofort hochfahren, obwohl der Nachlauf noch läuft – Bezug muss stimmen."""
    ctl, sim = make(calibrate_gap=False, start=0.5)
    await ctl.flat()
    await asyncio.sleep(1.05)  # unten angekommen (0,9 s), Nachlauf läuft noch bis 1,3 s
    assert ctl.any_moving()
    await hold(ctl, ["a", "b"], "back", UP, 1.4)
    await settle(ctl, sim)
    ctl.adjust_start("back")  # darf nicht abgelehnt werden
    await ctl.adjust_finish(apply=False)
    await ctl.async_shutdown()


async def test_adjust_from_top_explains_flat():
    """Von oben gibt es keinen Leerweg – Nachjustieren sagt, wie es richtig geht."""
    ctl, sim = make(calibrate_gap=False, start=1.0)
    await hold(ctl, ["a", "b"], "back", DOWN, 0.6)
    await settle(ctl, sim)
    ctl.adjust_start("back")
    with pytest.raises(ValueError, match="flachen Lage"):
        await ctl.adjust_finish(apply=True)
    await ctl.async_shutdown()


async def test_adjust_needs_level_reference():
    ctl, sim = make(calibrate_gap=False, start=0.4)
    for s in ("a", "b"):
        ctl.state[s]["back"].at_end = 0
    await hold(ctl, ["a", "b"], "back", UP, 0.5)
    await settle(ctl, sim)
    with pytest.raises(ValueError, match="gleich hoch"):
        ctl.adjust_start("back")
    await ctl.async_shutdown()


async def test_adjust_cancel_changes_nothing():
    ctl, sim = make(calibrate_gap=False)
    at_bottom(ctl, sim)
    await hold(ctl, ["a", "b"], "back", UP, 0.8)
    await settle(ctl, sim)
    ctl.adjust_start("back")
    assert ctl.snapshot()["adjust"] == {"motor": "back"}
    assert await ctl.adjust_finish(apply=False) == {}
    assert ctl.gap_ms("b", "back") == 0
    await ctl.async_shutdown()


async def test_fine_tune_and_test_move():
    ctl, sim = make(start=0.3)
    with pytest.raises(ValueError, match="flachen Lage"):
        await ctl.calib_test_move("back")
    ctl.shift_gap("back", 100)  # "Links fuhr zuerst los": B bekommt mehr Leerweg
    assert ctl.gap_ms("b", "back") - ctl.gap_ms("a", "back") == 500
    ctl.shift_gap("back", -100)
    await ctl.flat()
    await settle(ctl, sim)
    await ctl.calib_test_move("back", ms=600)
    await settle(ctl, sim)
    assert abs(sim.position("a", "back") - 0.3) < 0.03
    assert abs(diff(sim)) < 0.02
    await ctl.async_shutdown()


async def test_measure_skips_relevel_when_already_there():
    ctl, sim = make(calibrate_travel=False)
    at_bottom(ctl, sim)
    for s in ("a", "b"):
        ctl.calibration[s]["back"].update(up=3000, down=3000)
    sent = len(sim.log)
    await ctl.calib_measure_start("back", UP)
    await asyncio.sleep(0.2)
    assert ctl.measure["phase"] == "running"
    assert all(b["motor_2"]["Action"] == 1 for _, _, b in sim.log[sent:])  # kein Runterfahren
    await ctl.calib_measure_finish(apply=False)
    await settle(ctl, sim)
    await ctl.async_shutdown()


async def test_relevel_can_be_skipped():
    ctl, sim = make(calibrate_travel=False, start=0.0)
    for s in ("a", "b"):
        ctl.state[s]["back"].known = False  # Lage unbekannt -> würde lange runterfahren
        ctl.state[s]["back"].at_end = 0
    await ctl.calib_measure_start("back", UP)
    await asyncio.sleep(0.3)
    assert ctl.snapshot()["relevel"] and ctl.snapshot()["motion_remaining"] > 10
    assert ctl.relevel_skip()
    await asyncio.sleep(0.3)
    assert ctl.measure["phase"] == "running"
    assert ctl.state["a"]["back"].at_end == 0  # misst schon wieder hoch
    await ctl.calib_measure_finish(apply=False)
    await settle(ctl, sim)
    await ctl.async_shutdown()


def test_marking_flat_counts_as_end_position():
    ctl, _ = make(start=0.2)
    for s in ("a", "b"):
        ctl.set_position(s, "back", 0.0)
    assert ctl.at_end("back", DOWN)


async def test_command_log():
    ctl, sim = make()
    await ctl.move_to({"a": {"back": 0.2}})
    await settle(ctl, sim)
    log = ctl.snapshot()["commands"]
    assert log[-1]["cbu"] == 1 and log[-1]["ok"] and log[-1]["motors"]["back"][0] == UP
    await ctl.async_shutdown()
