"""Simulierte Auping-Connect-Box als HTTP-Server – zum Testen ohne echtes Bett.

    python tools/mock_box.py --port 8080 --key testtest

Endpunkte wie die echte Box (mit X-Key-Prüfung):
  GET  /api/config
  POST /api/cbu/motor/extended
Zusätzlich (ohne Schlüssel, nur für Tests):
  GET  /sim        Live-Ansicht beider Seiten
  GET  /sim/state  Positionen als JSON
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent))
from auping_sim import SimBox  # noqa: E402

SPEED = {"head": {"up": 8000, "down": 7000}, "back": {"up": 9000, "down": 8000}, "legs": {"up": 7000, "down": 6500}}
LAG = {
    "a": {m: {"up": 200, "down": 300} for m in SPEED},
    "b": {m: {"up": 800, "down": 600} for m in SPEED},
}

PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Box-Simulation</title>
<style>body{font-family:system-ui;margin:16px;background:#111;color:#eee}h1{font-size:18px}
.m{margin:14px 0}.r{display:grid;grid-template-columns:70px 1fr 60px;gap:8px;align-items:center;margin:4px 0}
.b{height:16px;background:#333;border-radius:8px;overflow:hidden}.f{height:100%;background:#4caf50}
.r.b2 .f{background:#2196f3}.d{font-size:12px;color:#aaa}</style>
<h1>Simulierte Box – echte Positionen (A grün, B blau)</h1><div id=o></div>
<script>
async function t(){const s=await (await fetch('/sim/state')).json();let h='';
for(const m of Object.keys(s.a)){h+=`<div class=m><b>${m}</b>`;for(const side of ['a','b']){const p=s[side][m];
h+=`<div class="r ${side=='b'?'b2':''}"><span>Seite ${side.toUpperCase()}</span><div class=b><div class=f style="width:${p.pos*100}%"></div></div><span>${(p.pos*100).toFixed(1)}%</span></div>`}
h+=`<div class=d>Differenz A−B: ${((s.a[m].pos-s.b[m].pos)*100).toFixed(1)} %-Punkte</div></div>`}
h+=`<div class=d>Befehle: ${s.commands}</div>`;o.innerHTML=h}
setInterval(t,150);t()</script>"""


def build_app(key: str, parallel: bool) -> web.Application:
    sim = SimBox({"a": json.loads(json.dumps(SPEED)), "b": json.loads(json.dumps(SPEED))}, parallel=parallel, lag=LAG)
    app = web.Application()

    def check(request: web.Request) -> None:
        if request.headers.get("X-Key") != key:
            raise web.HTTPForbidden()

    async def config(request: web.Request) -> web.Response:
        check(request)
        return web.json_response(sim.config())

    async def motor(request: web.Request) -> web.Response:
        check(request)
        body = await request.json()
        sim.command(body)
        print("Befehl:", json.dumps(body), flush=True)
        return web.json_response({"success": True, "msg": "ok"})

    async def state(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                **{
                    s: {m: {"pos": sim.position(s, m), "moving": sim.moving(s, m)} for m in sim.motors[s]}
                    for s in sim.motors
                },
                "commands": len(sim.log),
            }
        )

    async def page(_request: web.Request) -> web.Response:
        return web.Response(text=PAGE, content_type="text/html")

    app.router.add_get("/api/config", config)
    app.router.add_post("/api/cbu/motor/extended", motor)
    app.router.add_get("/sim/state", state)
    app.router.add_get("/sim", page)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--key", default="testtest")
    parser.add_argument("--serial", action="store_true", help="Box bedient nur eine Seite gleichzeitig")
    args = parser.parse_args()
    web.run_app(build_app(args.key, not args.serial), host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
