"""HTTP-Client für die Auping-Connect-Box im lokalen Netz.

Die Box spricht einfaches HTTP auf Port 80. Jede Anfrage braucht den Header
``X-Key`` mit dem Kopplungsschlüssel (8 Kleinbuchstaben), den die Auping-App
unter "Konfiguration teilen" anzeigt. Ohne gültigen Schlüssel antwortet die
Box mit 403.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import re
import time
from typing import Any

import aiohttp

from .const import ACTION_DOWN, ACTION_STOP, ACTION_UP, DOWN, MOTOR_INDEX, UP
from .outages import KIND_DROPPED, KIND_OTHER, KIND_REFUSED, KIND_TIMEOUT, KIND_UNREACHABLE

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=8)
RETRY_DELAY_S = 0.2


class AupingError(Exception):
    """Allgemeiner Fehler bei der Kommunikation mit der Box."""

    def __init__(self, message: str = "", kind: str = KIND_OTHER) -> None:
        super().__init__(message)
        self.kind = kind


class AupingAuthError(AupingError):
    """Die Box hat den Schlüssel abgelehnt (HTTP 403)."""


def error_kind(err: aiohttp.ClientError) -> str:
    """Woran eine Verbindung gescheitert ist - für das Protokoll der Ausfälle."""
    code = getattr(err, "errno", None)
    if code in (errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN):
        return KIND_UNREACHABLE
    if code == errno.ECONNREFUSED:
        return KIND_REFUSED
    if code == errno.ETIMEDOUT:
        return KIND_TIMEOUT
    if isinstance(err, aiohttp.ClientConnectionError):
        return KIND_DROPPED
    return KIND_OTHER


def normalize_xkey(value: str) -> str:
    """Schlüssel normalisieren: Leerzeichen raus, klein schreiben."""
    return re.sub(r"\s+", "", value or "").lower()


def parse_share_code(value: str) -> tuple[str | None, str]:
    """Inhalt des Teilen-QR-Codes ("<ip>-<xkey>") oder reinen Schlüssel zerlegen."""
    value = (value or "").strip()
    if "-" in value:
        host, _, key = value.rpartition("-")
        return host.strip() or None, normalize_xkey(key)
    return None, normalize_xkey(value)


class AupingConnectApi:
    """Minimaler, serialisierter Client für die Connect-Box."""

    def __init__(self, session: aiohttp.ClientSession, host: str, xkey: str, remote: str) -> None:
        self.session = session
        self.host = host
        self._xkey = normalize_xkey(xkey)
        self.remote = remote
        # Die Box ist ein kleiner Mikrocontroller: Anfragen nie überlappen lassen.
        self._lock = asyncio.Lock()
        self.last_rtt_ms: float | None = None
        self.last_attempts = 1

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, str, float]:
        """Eine Anfrage (Aufrufer hält den Lock). Gibt Status, Antwort und Sendezeitpunkt zurück.

        Bricht die Verbindung ab, ohne dass eine Antwort kam, gibt es einen zweiten Versuch:
        Die Box ist ein kleiner Mikrocontroller und lehnt eine Verbindung direkt nach der
        vorigen gelegentlich ab. Ohne zweiten Versuch bliebe dann eine Seite stehen – oder
        bekäme ihren Stopp nicht. Bei einer Zeitüberschreitung (8 s) wird nicht wiederholt.
        """
        for attempt in (1, 2):
            sent = time.monotonic()
            try:
                async with self.session.request(
                    method, f"http://{self.host}{path}", headers=self._headers, json=body, timeout=TIMEOUT
                ) as resp:
                    text = await resp.text()
                    self.last_attempts = attempt
                    return resp.status, text, sent
            except asyncio.TimeoutError as err:
                raise AupingError("Box antwortet nicht (Zeitüberschreitung)", KIND_TIMEOUT) from err
            except aiohttp.ClientConnectionError as err:
                if attempt == 2:
                    raise AupingError(str(err) or type(err).__name__, error_kind(err)) from err
                _LOGGER.debug("Verbindung zur Box abgebrochen (%s) – zweiter Versuch", err)
                await asyncio.sleep(RETRY_DELAY_S)
            except aiohttp.ClientError as err:
                raise AupingError(str(err) or type(err).__name__, error_kind(err)) from err
        raise AssertionError("unreachable")

    @property
    def _headers(self) -> dict[str, str]:
        # Mit "Connection: close" schließt die Box die Verbindung nach der Antwort selbst.
        return {"X-Key": self._xkey, "Connection": "close"}

    async def get_config(self) -> dict[str, Any]:
        """GET /api/config – liefert Bett-Typ, Seiten, Firmware, MAC usw."""
        async with self._lock:
            status, text, _ = await self._request("GET", "/api/config")
        if status == 403:
            raise AupingAuthError("Schlüssel abgelehnt")
        if status != 200:
            raise AupingError(f"HTTP {status}")
        try:
            return json.loads(text)
        except ValueError as err:
            raise AupingError(f"Unerwartete Antwort: {text[:200]}") from err

    def build_motor_body(self, cbu: int, motors: dict[str, tuple[int, int]]) -> dict[str, Any]:
        """Request-Body bauen.

        ``motors``: Motor-Schlüssel -> (Richtung UP/DOWN/0, Zeit in ms). Motoren,
        die nicht enthalten sind, werden mit Action 0 gestoppt.
        """
        body: dict[str, Any] = {"CBU": cbu}
        for motor, index in sorted(MOTOR_INDEX[self.remote].items(), key=lambda kv: kv[1]):
            direction, ms = motors.get(motor, (0, 0))
            if direction == UP and ms > 0:
                action = ACTION_UP
            elif direction == DOWN and ms > 0:
                action = ACTION_DOWN
            else:
                action, ms = ACTION_STOP, 0
            body[f"motor_{index}"] = {"Action": action, "TimeInMs": int(ms)}
        return body

    async def send_motors(self, cbu: int, motors: dict[str, tuple[int, int]]) -> float:
        """POST /api/cbu/motor/extended. Gibt den geschätzten Startzeitpunkt (monotonic) zurück."""
        body = self.build_motor_body(cbu, motors)
        async with self._lock:
            status, _, sent = await self._request("POST", "/api/cbu/motor/extended", body)
            done = time.monotonic()
        if status == 403:
            raise AupingAuthError("Schlüssel abgelehnt")
        if status >= 300:
            raise AupingError(f"HTTP {status}")
        rtt = done - sent
        self.last_rtt_ms = rtt * 1000
        _LOGGER.debug("Motorbefehl %s (%.0f ms)", body, rtt * 1000)
        # Die Box startet den Motor etwa in der Mitte der Laufzeit der Anfrage.
        return sent + rtt / 2
