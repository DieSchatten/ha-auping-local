"""Lokale HA-Testinstanz einrichten und die Integration per Einrichtungsassistent hinzufügen.

Nur für die Testinstanz auf diesem PC (http://127.0.0.1:8123) mit der
simulierten Box (tools/mock_box.py). Erzeugte Test-Zugangsdaten landen in
.ha-test/zugang.json (nicht im Repo).
"""

from __future__ import annotations

import json
from pathlib import Path
import secrets
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8123"
CLIENT = f"{BASE}/"
STATE = Path(__file__).resolve().parents[1] / ".ha-test" / "zugang.json"


def call(method: str, path: str, data=None, token: str | None = None, form: bool = False):
    headers = {}
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode()
        return json.loads(text) if text else None


def token() -> str:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if "refresh_token" not in state:
        state["password"] = secrets.token_urlsafe(18)
        res = call(
            "POST",
            "/api/onboarding/users",
            {"client_id": CLIENT, "name": "Test", "username": "test", "password": state["password"], "language": "de"},
        )
        tok = call(
            "POST",
            "/auth/token",
            {"grant_type": "authorization_code", "code": res["auth_code"], "client_id": CLIENT},
            form=True,
        )
        state["refresh_token"] = tok["refresh_token"]
        STATE.write_text(json.dumps(state))
        STATE.chmod(0o600)
        access = tok["access_token"]
        call("POST", "/api/onboarding/core_config", {}, access)
        call("POST", "/api/onboarding/analytics", {}, access)
        call("POST", "/api/onboarding/integration", {"client_id": CLIENT, "redirect_uri": f"{BASE}/?auth_callback=1"}, access)
        return access
    tok = call(
        "POST",
        "/auth/token",
        {"grant_type": "refresh_token", "refresh_token": state["refresh_token"], "client_id": CLIENT},
        form=True,
    )
    return tok["access_token"]


def main() -> None:
    access = token()
    entries = call("GET", "/api/config/config_entries/entry?domain=auping_local", token=access)
    if entries and "--neu" not in sys.argv:
        print("Integration bereits eingerichtet:", [e["title"] for e in entries])
        return
    flow = call("POST", "/api/config/config_entries/flow", {"handler": "auping_local"}, access)
    print("Schritt:", flow.get("step_id"), flow.get("errors"))
    flow = call(
        "POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {"host": "", "code": "127.0.0.1:8080-testtest"}, access
    )
    print("Schritt:", flow.get("step_id"), flow.get("errors"), flow.get("description_placeholders"))
    if flow.get("type") != "form" or flow.get("step_id") != "bed":
        print(json.dumps(flow, indent=2, ensure_ascii=False))
        sys.exit(1)
    defaults = {f["name"]: f.get("default") for f in flow["data_schema"]}
    print("Vorbelegung:", defaults)
    flow = call("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", defaults, access)
    print("Ergebnis:", flow.get("type"), flow.get("title"))


if __name__ == "__main__":
    main()
