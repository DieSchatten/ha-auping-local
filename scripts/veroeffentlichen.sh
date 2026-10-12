#!/usr/bin/env bash
# Neue Version auf GitHub veröffentlichen – HACS zeigt sie danach in Home Assistant als Update an.
#
# Ablauf: Tests -> Privat-Check -> Push -> GitHub-Release v<version aus manifest.json>.
# Vorher die Version in custom_components/auping_local/manifest.json erhöhen und committen.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[ -x "$PY" ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt; }
VERSION=$(python3 -c "import json;print(json.load(open('custom_components/auping_local/manifest.json'))['version'])")

echo "== Tests"
"$PY" -m pytest -q

echo "== Privat-Check"
# Muster stückeln, damit dieses Skript sich nicht selbst findet.
PRIVAT="192\.168\.19""1\.|schwarz""alex|myds""\.me|/home/""alex"
if git ls-files -z | xargs -0 grep -n -I -E "$PRIVAT"; then
  echo "Private Angaben gefunden – Abbruch."; exit 1
fi
if git log --format='%ae' | grep -v -q 'users.noreply.github.com'; then
  echo "Commit mit privater E-Mail-Adresse im Verlauf – Abbruch."; exit 1
fi
if git ls-files | grep -E -q '\.(apk|apks|xapk)$|^\.ha-test/|version\.json$'; then
  echo "APK, Testinstanz oder version.json im Repo – Abbruch."; exit 1
fi

echo "== Arbeitsstand"
git diff --quiet && git diff --cached --quiet || { echo "Es gibt nicht committete Änderungen – erst committen."; exit 1; }

echo "== Push"
git push origin main

if gh release view "v$VERSION" >/dev/null 2>&1; then
  echo "Release v$VERSION gibt es schon – für ein Update die Version in manifest.json erhöhen."
else
  gh release create "v$VERSION" --title "v$VERSION" --generate-notes
  echo "Veröffentlicht: v$VERSION – HACS meldet das Update innerhalb einiger Stunden (oder sofort über 'Aktualisieren')."
fi
