"""Die Box wiederfinden, wenn sie eine neue IP-Adresse bekommen hat.

Die Connect-Box kündigt sich nicht zuverlässig per mDNS an. Bekommt sie vom Router
eine neue Adresse, sucht die Integration sie im eigenen Netz:

1. Alle Adressen im /24-Netz der bisherigen Adresse ohne Schlüssel abfragen. Die Box
   antwortet darauf mit HTTP 403 – das sind die Kandidaten.
2. Nur diesen Kandidaten wird der Schlüssel gezeigt. Treffer ist, wer mit dem
   Schlüssel antwortet und dieselbe MAC-Adresse meldet wie bei der Einrichtung.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re

import aiohttp

from .api import AupingConnectApi, AupingError
from .const import REMOTE_REGULAR

_LOGGER = logging.getLogger(__name__)

PROBE_TIMEOUT = aiohttp.ClientTimeout(total=2.5)
PARALLEL = 48


def normalize_mac(mac: str | None) -> str:
    return re.sub(r"[^0-9a-f]", "", (mac or "").lower())


def split_host(host: str) -> tuple[str, str]:
    """``"192.168.1.5:8080"`` -> ``("192.168.1.5", ":8080")``."""
    if host.count(":") == 1:
        ip, port = host.split(":")
        return ip, f":{port}"
    return host, ""


def candidates_around(*hosts: str | None) -> list[str]:
    """Alle Adressen in den /24-Netzen der angegebenen IPv4-Adressen (ohne die Adressen selbst)."""
    seen: set[str] = set()
    out: list[str] = []
    skip = set()
    for host in hosts:
        if not host:
            continue
        ip, _ = split_host(host)
        try:
            addr = ipaddress.IPv4Address(ip)
        except ValueError:
            continue  # Hostname statt IP: nichts zu durchsuchen
        skip.add(str(addr))
        for cand in ipaddress.IPv4Network(f"{addr}/24", strict=False).hosts():
            if str(cand) not in seen:
                seen.add(str(cand))
                out.append(str(cand))
    return [c for c in out if c not in skip]


async def find_box(
    session: aiohttp.ClientSession,
    old_host: str,
    xkey: str,
    mac: str | None,
    extra_hosts: tuple[str, ...] = (),
) -> str | None:
    """Neue Adresse der Box suchen. Gibt ``host`` (ggf. mit Port) zurück oder None."""
    _, port = split_host(old_host)
    want = normalize_mac(mac)
    sem = asyncio.Semaphore(PARALLEL)
    found: list[str] = []

    async def probe(ip: str) -> None:
        async with sem:
            try:
                async with session.get(
                    f"http://{ip}{port}/api/config", timeout=PROBE_TIMEOUT, headers={"Connection": "close"}
                ) as resp:
                    if resp.status == 403:
                        found.append(ip)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                pass

    await asyncio.gather(*(probe(ip) for ip in candidates_around(old_host, *extra_hosts)))
    for ip in found:
        host = f"{ip}{port}"
        try:
            config = await AupingConnectApi(session, host, xkey, REMOTE_REGULAR).get_config()
        except AupingError:
            continue
        if not want or normalize_mac(config.get("mac_address")) == want:
            _LOGGER.info("Auping-Box unter neuer Adresse gefunden: %s", host)
            return host
    _LOGGER.debug("Auping-Box nicht gefunden (%d Kandidaten mit 403)", len(found))
    return None
