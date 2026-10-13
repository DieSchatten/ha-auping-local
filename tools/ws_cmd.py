"""Einen Bedienfeld-Befehl an die lokale HA-Testinstanz schicken (nur Testumgebung).

    python tools/ws_cmd.py set_calibration '{"side": "b", "motor": "back", "key": "lag_up", "value": 0}'
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import aiohttp

sys.path.insert(0, str(Path(__file__).parent))
from ha_test_setup import token  # noqa: E402


async def main() -> None:
    action = sys.argv[1]
    data = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    async with aiohttp.ClientSession() as http, http.ws_connect("ws://127.0.0.1:8123/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": token()})
        assert (await ws.receive_json())["type"] == "auth_ok"
        await ws.send_json({"id": 1, "type": "auping_local/entries"})
        entry = (await ws.receive_json())["result"][0]["entry_id"]
        await ws.send_json({"id": 2, "type": "auping_local/command", "entry_id": entry, "action": action, "data": data})
        res = await ws.receive_json()
        print(json.dumps(res.get("result") if res.get("success") else res.get("error"), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
