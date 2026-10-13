![Bett lokal – Auping Connect in Home Assistant](https://raw.githubusercontent.com/DieSchatten/ha-auping-local/main/docs/banner.png)

# Auping Bett (lokal) – Home-Assistant-Integration

Steuert Auping-Betten mit der WLAN-Box **Auping Connect** direkt im Heimnetz – ohne Auping-Cloud, ohne
Auping-App. Dazu ein **Sync-Ausgleich für zwei Lattenroste**: Läuft ein Motor beim Hochfahren aus der
flachen Lage länger leer als der andere, bevor er den Rost anhebt (Leerweg), fahren beide Seiten
trotzdem im Gleichschritt und kommen auf derselben Höhe an.

> Inoffizielles Projekt, keine Verbindung zu Auping. Das Protokoll wurde zur Interoperabilität aus der
> Auping-Connect-App 2.4.4 nachvollzogen (siehe [PROTOKOLL.md](PROTOKOLL.md)). Benutzung auf eigene Gefahr.

## Was es kann

- **Bestehende Kopplung übernehmen** – kein Neu-Anlernen. Der Code aus „Konfiguration teilen“ in der
  Auping-App reicht.
- **Eigenes Bedienfeld „Bett“** in der Seitenleiste (auch in der Home-Assistant-App auf dem Handy):
  Tasten zum Gedrückthalten je Motor, Auswahl Links / Beide synchron / Rechts, Positionsbalken,
  „Alles flach“, Referenzfahrt, Stopp. Die Namen der Seiten lassen sich im Bedienfeld ändern.
- **Favoriten mit Namen** (bis zu 20): speichern die *Lage* beider Seiten, nicht eine Fahrzeit. Beim
  Antippen fährt jeder Motor von seiner aktuellen Lage gezielt hoch oder runter – ein zweites Antippen
  bewegt nichts mehr. Umbenennen, überschreiben, sortieren, löschen. Ist im Bedienfeld nur eine Seite
  gewählt, fährt auch nur diese Seite in ihren Favoriten.
- **Matratzenmodus**: Bei *einer durchgehenden Matratze* darf eine Seite allein höchstens einen
  einstellbaren Abstand (Standard 10 % des Verstellwegs) von der anderen haben – hält man länger, bleibt
  der Motor an der Grenze stehen. Bei *zwei Einzelmatratzen* sind die Seiten frei verstellbar.
- **Nachjustieren** statt Stoppuhr: „Alles flach“, mit „Beide“ ein Stück hochfahren, die zurückliegende
  Seite einzeln nachfahren, „Passt jetzt“ – daraus berechnet die Integration den Leerweg-Unterschied.
  Eine Runde reicht.
- **Schonender Richtungswechsel**: Wie die Original-App schickt die Integration vor jedem Richtungswechsel
  erst einen Stopp und fährt nach einer halben Sekunde neu los – die Steuereinheiten nehmen eine direkte
  Umkehr nicht zuverlässig an.
- **Entitäten für Automationen**: je Motor eine Abdeckung (`cover`) pro Seite und eine für beide Seiten
  synchron, mit Position in Prozent; Tasten (`button`) für Flach, Referenzfahrt, Stopp und je Favorit;
  Auswahl `Favorit` (zeigt den gerade eingestellten Favoriten) und `Matratze`; Kalibrierwerte und
  maximaler Seitenunterschied als `number`; Verbindung und Firmware als Diagnose.
- **Dienste** `auping_local.move_to`, `move`, `stop`, `flat`, `home`, `goto_favorite`, `save_favorite`,
  `delete_favorite`.
- **Fehlersuche**: Das Bedienfeld zeigt die letzten Befehle an die Box (Einstellungen), dazu gibt es
  den Diagnose-Download von Home Assistant (Schlüssel und Adressen geschwärzt).
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
   kurz nacheinander je einen eigenen Befehl. Laufen beide los, bleibt „Gleichzeitig“ eingestellt (bei der
   Auping-Connect-Box ist das der Normalfall). Stoppt eine Seite, sobald die andere startet, „Nacheinander“.
2. **Laufzeit messen** (Kalibrieren → „Hochfahrt messen“): einmal von ganz unten nach ganz oben, tippen, wenn
   der Motor am Anschlag stehen bleibt. Ohne Messung nimmt die Integration 25 s für den ganzen Weg an –
   dann stimmen die Prozentwerte nur ungefähr, und bei deutlich längeren Motoren erreicht „Alles flach“
   den Anschlag eventuell nicht ganz.
3. **Leerweg nachjustieren** – je Motor:
   1. „Alles flach“.
   2. Mit „Beide“ ein Stück hochfahren.
   3. Beim Motor auf „Nachjustieren“ tippen, die zurückliegende Seite mit ihren eigenen Tasten nachfahren,
      bis beide gleich hoch sind, „Passt jetzt“.
   Zum Prüfen gibt es unter *Kalibrieren* eine Testfahrt aus der flachen Lage und Knöpfe für
   Feinkorrekturen in 0,1-s-Schritten.

## Wie der Ausgleich funktioniert

Die Box kennt keine Positionen, nur „Motor X für N Millisekunden hoch/runter“ – je Steuereinheit (Seite A
und B) getrennt. Die Integration rechnet die Position mit und kennt je Seite und Motor:

- **Leerweg unten** – In der flachen Lage liegt der Rost auf, der Motor fährt aber noch weiter zurück.
  Beim Hochfahren läuft er diesen Weg erst leer, bevor er schiebt. Aus einer Zwischenlage und beim
  Runterfahren gibt es keinen Leerweg, denn dort liegt der Rost am Motor an.
- **Laufzeit** – Dauer von ganz unten bis ganz oben (bzw. zurück) bei anliegendem Motor.

Fahren beide Seiten aus der flachen Lage hoch, startet die Seite mit dem kürzeren Leerweg um genau die
Differenz später; jede Seite bekommt `Leerweg + Strecke × Laufzeit`. Beide Roste heben dadurch gleichzeitig
an und halten gleichzeitig an. Lässt du die Taste los, hält die vorne liegende Seite sofort an, eine
eventuell zurückliegende holt noch auf.

Fahrten nach ganz unten bekommen Nachlauf plus Leerweg, damit der Motor immer ganz zurückfährt und der
Leerweg beim nächsten Hochfahren gleich groß ist. Während dieses Nachlaufs zeigt die Anzeige schon 0 %,
der Motor läuft aber noch – das Bedienfeld zeigt dann **„sichert Endlage …“**. Fährst du in dieser Zeit
wieder hoch, wird erst angehalten und dann neu gestartet.

### Grenzen

- Positionen sind geschätzt. Nach Benutzung der **Funk-Fernbedienung** kennt die Integration die Lage
  nicht mehr – einmal *Referenzfahrt* oder *Alles flach* stellt das wieder her.
- Die App schickt nie Befehle über 30 s; längere Fahrten werden automatisch nachgeschoben.
- Zum **Nachjustieren** muss „Alles flach“ ganz zu Ende gelaufen sein (kein „sichert Endlage“ mehr),
  sonst ist der Leerweg noch nicht vollständig offen und das Ergebnis wäre ungenau.

## Automationen – Beispiele

```yaml
action: auping_local.move_to
data:
  side: both
  motor: back
  position: 40
```

```yaml
action: auping_local.goto_favorite
data:
  favorite: Lesen
```

## Entwicklung

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Die Tests laufen gegen eine simulierte Box (`tools/auping_sim.py`), die sich wie das echte Bett verhält:
gleich schnelle Motoren mit unterschiedlichem Leerweg unten, und eine direkte Richtungsumkehr ohne Stopp
wird ignoriert. `tools/mock_box.py` bietet dieselbe Simulation als HTTP-Server mit Live-Ansicht unter
`/sim` – damit lässt sich die Integration in einer Test-Instanz ohne echtes Bett ausprobieren
(Code `127.0.0.1:8080-testtest`).

## Lizenz

MIT
