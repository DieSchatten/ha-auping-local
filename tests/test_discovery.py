"""Wiederfinden der Box nach einem Adresswechsel – gegen echte kleine HTTP-Server auf 127.0.0.x."""

from __future__ import annotations

import aiohttp
from aiohttp import web

from auping_local.discovery import candidates_around, find_box, normalize_mac

KEY = "abcdefgh"
MAC = "3C:E1:A1:37:15:7A"


async def start(ip: str, port: int, handler) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/api/config", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, ip, port).start()
    return runner


async def box_handler(request: web.Request) -> web.Response:
    if request.headers.get("X-Key") != KEY:
        raise web.HTTPForbidden()
    return web.json_response({"mac_address": "3c:e1:a1:37:15:7a", "firmware": "1.05.6"})


async def stranger_handler(request: web.Request) -> web.Response:
    raise web.HTTPForbidden()  # anderes Gerät, das ebenfalls 403 meldet


def test_candidates_and_mac():
    cands = candidates_around("192.168.1.35")
    assert len(cands) == 253 and "192.168.1.35" not in cands and "192.168.1.48" in cands
    assert candidates_around("auping.local") == []
    assert len(candidates_around("10.0.0.5:8080", "10.0.0.9")) == 252
    assert normalize_mac("3C:E1:A1:37:15:7A") == normalize_mac("3c-e1-a1-37-15-7a")


async def test_finds_box_under_new_address():
    probe = await start("127.0.0.1", 0, stranger_handler)
    port = probe.addresses[0][1]
    box = await start("127.0.0.2", port, box_handler)
    try:
        async with aiohttp.ClientSession() as session:
            # Box war unter .77 eingetragen, ist jetzt unter .2 – .1 ist ein fremdes Gerät
            assert await find_box(session, f"127.0.0.77:{port}", KEY, MAC) == f"127.0.0.2:{port}"
            # Falsche MAC: nichts übernehmen
            assert await find_box(session, f"127.0.0.77:{port}", KEY, "02:00:00:00:00:01") is None
            # Falscher Schlüssel: nichts übernehmen
            assert await find_box(session, f"127.0.0.77:{port}", "falsch", MAC) is None
    finally:
        await box.cleanup()
        await probe.cleanup()
