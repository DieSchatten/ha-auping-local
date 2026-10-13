"""Konstanten für die lokale Auping-Connect-Integration.

Alle Protokolldetails stammen aus der Auping-App 2.4.4 (nl.auping.connect),
siehe PROTOKOLL.md im Projektordner.
"""

from __future__ import annotations

DOMAIN = "auping_local"

CONF_HOST = "host"
CONF_XKEY = "xkey"
CONF_MAC = "mac"
CONF_REMOTE = "remote"  # "regular" | "royal"
CONF_SIDES = "sides"  # ["a"] oder ["a", "b"]
CONF_MOTORS = "motors"  # Liste von Motor-Schlüsseln, z. B. ["head", "back", "legs"]
CONF_NAME_A = "name_a"
CONF_NAME_B = "name_b"
CONF_MODE = "mode"  # "parallel" | "sequential"
CONF_MATTRESS = "mattress"  # "single" | "dual"
CONF_MAX_DIFF = "max_diff"  # erlaubter Höhenunterschied der Seiten in % (nur bei einer Matratze)

MATTRESS_SINGLE = "single"  # eine durchgehende Matratze über beide Lattenroste
MATTRESS_DUAL = "dual"  # zwei Einzelmatratzen, Seiten frei verfahrbar
DEFAULT_MAX_DIFF = 10

MODE_PARALLEL = "parallel"
MODE_SEQUENTIAL = "sequential"

REMOTE_REGULAR = "regular"
REMOTE_ROYAL = "royal"

SIDE_A = "a"
SIDE_B = "b"
SIDES = (SIDE_A, SIDE_B)

# CBU-Werte im Request ("CBU": …) laut CbuType_e
CBU_BOTH = 0
CBU_FOR_SIDE = {SIDE_A: 1, SIDE_B: 2}

# Action-Werte laut MoveBedDirection_e
ACTION_STOP = 0
ACTION_UP = 1
ACTION_DOWN = 2

UP = 1
DOWN = -1

# Motoren in der Reihenfolge der App (MotorId: HEAD=1, BACK=2, LEGS=3, LOWER_BACK=4, FEET=5)
MOTOR_HEAD = "head"
MOTOR_BACK = "back"
MOTOR_LEGS = "legs"
MOTOR_LOWER_BACK = "lower_back"
MOTOR_FEET = "feet"
ALL_MOTORS = (MOTOR_HEAD, MOTOR_BACK, MOTOR_LOWER_BACK, MOTOR_LEGS, MOTOR_FEET)

MOTOR_NAMES = {
    MOTOR_HEAD: "Kopfteil",
    MOTOR_BACK: "Rückenteil",
    MOTOR_LOWER_BACK: "Lendenteil",
    MOTOR_LEGS: "Beinteil",
    MOTOR_FEET: "Fußteil",
}

# JSON-Schlüssel "motor_N" je Fernbedienungstyp (CMoveBedRequest.getIndexBy)
MOTOR_INDEX = {
    REMOTE_REGULAR: {MOTOR_HEAD: 1, MOTOR_BACK: 2, MOTOR_LEGS: 3},
    REMOTE_ROYAL: {
        MOTOR_HEAD: 1,
        MOTOR_BACK: 2,
        MOTOR_LOWER_BACK: 3,
        MOTOR_LEGS: 4,
        MOTOR_FEET: 5,
    },
}

# Die App schickt nie mehr als 30 s pro Befehl (auch Favoriten sind darauf begrenzt).
MAX_COMMAND_MS = 30000
# Längere Fahrten werden kurz vor Ablauf mit der Restzeit erneuert.
REFRESH_BEFORE_MS = 1000

DEFAULT_TRAVEL_MS = 25000
MIN_TRAVEL_MS = 1000
MAX_TRAVEL_MS = 120000
MAX_GAP_MS = 8000  # Leerweg unten: so lange läuft ein Motor höchstens, bevor er den Rost anhebt

# Nachlauf an den Endlagen, damit der Motor sicher den Endschalter erreicht.
OVERRUN_FACTOR = 0.15
OVERRUN_MIN_MS = 1500

STORAGE_VERSION = 1
MAX_FAVORITES = 20

PANEL_URL_PATH = "auping-bett"
PANEL_STATIC_URL = "/auping_local_static"
PANEL_COMPONENT = "auping-panel"


def motors_for_bed_type(bed_type: int) -> tuple[str | None, list[str], list[str]]:
    """Fernbedienungstyp und Motoren links/rechts aus bed_type ableiten.

    Nachgebaut aus CConnectConfigResponse.getRemoteType / getNumberOfMotorsLeft/Right.
    """
    if bed_type >= 30:
        remote = REMOTE_REGULAR
    elif bed_type >= 20:
        remote = REMOTE_ROYAL
    else:
        return None, [], []

    one = [MOTOR_BACK]
    two = [MOTOR_BACK, MOTOR_LEGS]
    three = [MOTOR_HEAD, MOTOR_BACK, MOTOR_LEGS]
    royal_two = [MOTOR_BACK, MOTOR_LEGS]
    royal_five = [MOTOR_HEAD, MOTOR_BACK, MOTOR_LOWER_BACK, MOTOR_LEGS, MOTOR_FEET]

    if remote == REMOTE_ROYAL:
        left = {20: royal_two, 22: royal_two, 23: royal_two, 21: royal_five, 24: royal_five, 25: royal_five}
        right = {22: royal_two, 24: royal_two, 23: royal_five, 25: royal_five}
    else:
        left = {30: one, 33: one, 34: one, 35: one, 31: two, 36: two, 37: two, 38: two, 32: three, 39: three, 40: three, 41: three}
        right = {33: one, 36: one, 39: one, 34: two, 37: two, 40: two, 35: three, 38: three, 41: three}
    return remote, left.get(bed_type, []), right.get(bed_type, [])


def sides_for_cbu_type(cbu_type: int) -> list[str]:
    """Anzahl Bettseiten aus cbu_type (CConnectConfigResponse.getNumberOfBedsides)."""
    if cbu_type in (23, 33):
        return [SIDE_A, SIDE_B]
    if cbu_type in (21, 22, 31):
        return [SIDE_A]
    return [SIDE_A, SIDE_B]
