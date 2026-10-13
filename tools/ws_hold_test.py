"""Gedrückthalten über die HA-WebSocket-API testen und echte Positionen der Simulation mitschreiben.

Nur für die lokale Testinstanz (tools/ha_test_setup.py) mit tools/mock_box.py.
    python tools/ws_hold_test.py back up 2.0 both
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time

import aiohttp

sys.path.insert(0, str(Path(__file__).parent))
from ha_test_setup import token  # noqa: E402

SIM = "http://127.0.0.1:8080/sim/state"


async def main() -> None:
    motor, direction, hold, side = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
    access = token()
    async with aiohttp.ClientSession() as http, http.ws_connect("ws://127.0.0.1:8123/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": access})
        assert (await ws.receive_json())["type"] == "auth_ok"
        msg_id = 1

        async def call(payload):
            nonlocal msg_id
            msg_id += 1
            await ws.send_json({"id": msg_id, **payload})
            while True:
                res = await ws.receive_json()
                if res.get("id") == msg_id and res["type"] == "result":
                    return res

        entries = (await call({"type": "auping_local/entries"}))["result"]
        entry = entries[0]["entry_id"]

        async def sim():
            async with http.get(SIM) as r:
                s = await r.json()
            return round(s["a"][motor]["pos"] * 100, 1), round(s["b"][motor]["pos"] * 100, 1)

        def show(label, pos):
            print(f"{label:>16}: A={pos[0]:5.1f}%  B={pos[1]:5.1f}%  A-B={pos[0] - pos[1]:+5.1f}")

        show("vorher", await sim())
        t0 = time.monotonic()
        await call({"type": "auping_local/command", "entry_id": entry, "action": "start_move",
                    "data": {"side": side, "motor": motor, "direction": direction}})
        while (left := hold - (time.monotonic() - t0)) > 0:
            await asyncio.sleep(min(0.4, left))
            if hold - (time.monotonic() - t0) > 0.05:
                show(f"{time.monotonic() - t0:.1f} s", await sim())
        await call({"type": "auping_local/command", "entry_id": entry, "action": "stop_motor",
                    "data": {"side": side, "motor": motor}})
        await asyncio.sleep(2.5)
        show("nach Loslassen", await sim())


if __name__ == "__main__":
    asyncio.run(main())
