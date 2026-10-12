# Auping Bett (lokal) – Home-Assistant-Integration

Steuert Auping-Betten mit der WLAN-Box **Auping Connect** direkt im Heimnetz – ohne Auping-Cloud, ohne
Auping-App. Dazu ein **Sync-Ausgleich für zwei Lattenroste**: Wenn ein Motor später anläuft als der
andere (Totweg), fahren beide Seiten trotzdem im Gleichschritt und kommen auf derselben Höhe an.

> Inoffizielles Projekt, keine Verbindung zu Auping. Das Protokoll wurde zur Interoperabilität aus der
> Auping-Connect-App 2.4.4 nachvollzogen (siehe [PROTOKOLL.md](PROTOKOLL.md)). Benutzung auf eigene Gefahr.

## Was es kann

- **Bestehende Kopplung übernehmen** – kein Neu-Anlernen. Der Code aus „Konfiguration teilen“ in der
  Auping-App reicht.
- **Eigenes Bedienfeld „Bett“** in der Seitenleiste (auch in der Home-Assistant-App auf dem Handy):
  Tasten zum Gedrückthalten je Motor, Auswahl Links / Beide synchron / Rechts, Positionsbalken,
  „Alles flach“, Referenzfahrt, Stopp, drei Favoriten.
- **Kalibrier-Assistent** ohne Stoppuhr: kurze Testfahrt ab der Endlage, du sagst „links höher“,
  „rechts höher“ oder „gleich hoch“ – der Startversatz wird schrittweise eingegrenzt.
- **Entitäten für Automationen**: je Motor eine Abdeckung (`cover`) pro Seite und eine für beide Seiten
  synchron, mit Position in Prozent; Tasten (`button`) für Flach, Referenzfahrt, Stopp, Favoriten;
  Kalibrierwerte als `number`; Verbindung und Firmware als Diagnose.
- **Dienste** `auping_local.move_to`, `move`, `stop`, `flat`, `home`, `goto_favorite`, `save_favorite`.
- Rein lokal: HTTP zur Box im eigenen WLAN, sonst nichts.

Nicht unterstützt: die neuere **SmartBase** (Bluetooth, Gerätename `AUPING_CM`) – nur die WLAN-Box.

## Installation

### Über HACS (empfohlen)

1. HACS → Menü (⋮) → **Benutzerdefinierte Repositories** →
   `https://github.com/DieSchatten/ha-auping-local`, Typ **Integration**.
2. „Auping Bett (lokal)“ herunterladen, Home Assistant neu starten.

### Von Hand

Ordner `custom_components/auping_local` nach `/config/custom_components/` kopieren und Home Assistant
neu starten.

## Einrichtung

1. In der **Auping-App**: Menü → **Konfiguration teilen**. Dort stehen die IP-Adresse der Box und ein
   8-stelliger Code (der QR-Code enthält `IP-Code`).
2. In Home Assistant: **Einstellungen → Geräte & Dienste → Integration hinzufügen → Auping Bett (lokal)**.
   IP-Adresse und Code eingeben (oder den QR-Text einfügen).
3. Bett-Typ, Motoren und Seiten werden aus der Box gelesen und vorgeschlagen; Namen der Seiten anpassen.

Tipp: Der Box im Router eine feste IP-Adresse geben. Ändert sie sich doch, lässt sie sich in den Optionen
der Integration anpassen.

## Erste Schritte nach der Einrichtung

1. **Gleichzeitig oder nacheinander?** Bedienfeld → Einstellungen → „Test starten“. Beide Seiten bekommen
   kurz nacheinander je einen eigenen Befehl. Laufen beide los, bleibt „Gleichzeitig“ eingestellt.
   Stoppt eine Seite, sobald die andere startet, „Nacheinander“ wählen.
2. **Referenzfahrt** (Fernbedienung → Referenzfahrt). Danach kennt die Integration die Lage.
3. **Kalibrieren** – je Motor, den du synchron fahren willst:
   1. *Startversatz hoch abgleichen* und *runter abgleichen* (je 3–8 Runden).
   2. Optional *Laufzeit messen*, damit die Prozentanzeige stimmt (Tippen, wenn der Motor am Endschalter
      stehen bleibt).

## Wie der Ausgleich funktioniert

Die Box kennt keine Positionen, nur „Motor X für N Millisekunden hoch/runter“ – je Steuereinheit (Seite A
und B) getrennt. Die Integration rechnet die Position mit und kennt je Seite, Motor und Richtung:

- **Anlauf** – Zeit vom Befehl bis der Rost sich tatsächlich bewegt (Totweg),
- **Laufzeit** – Dauer von ganz unten bis ganz oben bei laufendem Motor.

Bei einer Synchronfahrt startet die Seite mit dem kürzeren Anlauf um genau die Differenz später, und jede
Seite bekommt `Anlauf + Strecke × Laufzeit`. Beide Roste bewegen sich dadurch gleichzeitig und halten
gleichzeitig an. Lässt du die Taste los, holt eine eventuell zurückliegende Seite noch auf. Fahrten in eine
Endlage bekommen etwas Nachlauf, damit der Endschalter sicher erreicht wird – dabei wird die Position neu
genullt.

### Grenzen

- Positionen sind geschätzt. Nach Benutzung der **Funk-Fernbedienung** kennt die Integration die Lage
  nicht mehr – einmal *Referenzfahrt* oder *Alles flach* stellt das wieder her.
- Die App schickt nie Befehle über 30 s; längere Fahrten werden automatisch nachgeschoben.

## Automationen – Beispiel

```yaml
action: auping_local.move_to
data:
  side: both
  motor: back
  position: 40
```

## Entwicklung

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Die Tests laufen gegen eine simulierte Box (`tools/auping_sim.py`), deren Motoren gleich schnell sind,
aber unterschiedlich spät anlaufen. `tools/mock_box.py` bietet dieselbe Simulation als HTTP-Server mit
Live-Ansicht unter `/sim` – damit lässt sich die Integration in einer Test-Instanz ohne echtes Bett
ausprobieren (Code `127.0.0.1:8080-testtest`).

## Lizenz

MIT
