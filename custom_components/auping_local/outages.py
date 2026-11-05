"""Merkt sich, wann die Box nicht erreichbar war und woran man das gesehen hat."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

KIND_UNREACHABLE = "unreachable"
KIND_REFUSED = "refused"
KIND_TIMEOUT = "timeout"
KIND_DROPPED = "dropped"
KIND_OTHER = "other"
KIND_MOVED = "moved"

LABELS = {
    KIND_UNREACHABLE: "Nicht im Netz",
    KIND_REFUSED: "Webserver lehnt ab",
    KIND_TIMEOUT: "Keine Antwort",
    KIND_DROPPED: "Verbindung abgebrochen",
    KIND_OTHER: "Anderer Fehler",
    KIND_MOVED: "Neue IP-Adresse",
}

MAX_ENTRIES = 20


class OutageLog:
    """Liste der letzten Ausfälle: Beginn, Ende und alle Fehlerarten in der Reihenfolge ihres Auftretens."""

    def __init__(self, entries: Any = None, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self.entries: list[dict[str, Any]] = []
        for raw in entries if isinstance(entries, list) else []:
            if not (isinstance(raw, dict) and isinstance(raw.get("start"), (int, float))):
                continue
            end = raw.get("end")
            kinds = raw.get("kinds")
            self.entries.append(
                {
                    "start": raw["start"],
                    "end": end if isinstance(end, (int, float)) else None,
                    "kinds": [k for k in kinds if k in LABELS] if isinstance(kinds, list) else [],
                }
            )
        self.entries = self.entries[-MAX_ENTRIES:]

    @property
    def current(self) -> dict[str, Any] | None:
        """Der laufende Ausfall, falls es einen gibt."""
        if self.entries and self.entries[-1]["end"] is None:
            return self.entries[-1]
        return None

    def failure(self, kind: str) -> bool:
        """Fehlschlag vermerken. True, wenn sich die Liste geändert hat."""
        entry = self.current
        if entry is None:
            self.entries.append({"start": self._clock(), "end": None, "kinds": [kind]})
            self.entries = self.entries[-MAX_ENTRIES:]
            return True
        if kind not in entry["kinds"]:
            entry["kinds"].append(kind)
            return True
        return False

    def recovered(self) -> dict[str, Any] | None:
        """Box antwortet wieder. Gibt den gerade beendeten Ausfall zurück."""
        entry = self.current
        if entry is not None:
            entry["end"] = self._clock()
        return entry

    def export(self) -> list[dict[str, Any]]:
        return [{**entry, "kinds": list(entry["kinds"])} for entry in self.entries]

    def as_list(self) -> list[dict[str, Any]]:
        """Für Bedienfeld und Diagnose: neueste zuerst, mit lesbaren Fehlerarten."""
        return [
            {"start": e["start"], "end": e["end"], "labels": [LABELS[k] for k in e["kinds"]]}
            for e in reversed(self.entries)
        ]
