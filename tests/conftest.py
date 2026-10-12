"""Testaufbau: Integrationsmodule ohne Home Assistant laden."""

from __future__ import annotations

from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components" / "auping_local"

# Das Paket-__init__ importiert Home Assistant. Für die Logik-Tests reicht ein
# leeres Paket, damit die relativen Importe (.const, .api, …) funktionieren.
if "auping_local" not in sys.modules:
    pkg = types.ModuleType("auping_local")
    pkg.__path__ = [str(PKG_DIR)]
    sys.modules["auping_local"] = pkg

sys.path.insert(0, str(ROOT / "tools"))
