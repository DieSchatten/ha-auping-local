# Auping Bett (lokal) – Home-Assistant-Integration

Steuert Auping-Betten mit der WLAN-Box **Auping Connect** direkt im Heimnetz, ohne Cloud. Läuft ein Motor
beim Hochfahren aus der flachen Lage länger leer als der andere, gleicht die Integration das aus, sodass
zwei Lattenroste im Gleichschritt fahren.

> Inoffizielles Projekt, keine Verbindung zu Auping. Benutzung auf eigene Gefahr.

Nicht unterstützt: die neuere **SmartBase** (Bluetooth), nur die WLAN-Box.

## Was es kann

- **Bedienfeld „Bett“** in der Seitenleiste, auch in der Home-Assistant-App: Tasten zum Gedrückthalten je
  Motor, Auswahl Links / Beide / Rechts, Lage in Prozent, „Alles flach“, Referenzfahrt, Stopp.
- **Favoriten mit Namen** (bis zu 20). Sie speichern die Lage, nicht eine Fahrzeit.
- **Matratzenmodus:** Bei einer durchgehenden Matratze bleiben die Seiten höchstens einen einstellbaren
  Abstand auseinander (Standard 10 %), bei zwei Einzelmatratzen sind sie frei.
- **Nachjustieren:** Gleicht unterschiedlichen Leerweg der Motoren aus.
- **Entitäten für Automationen:** Abdeckungen (`cover`) je Motor und Seite, Tasten (`button`) für Flach,
  Referenzfahrt, Stopp und je Favorit, Auswahl `Favorit` und `Matratze`.
- **Dienste** `auping_local.move_to`, `move`, `stop`, `flat`, `home`, `goto_favorite`, `save_favorite`,
  `delete_favorite`.
- **Ausfall-Liste:** Unter Bett → Einstellungen stehen die letzten Befehle und die letzten 20 Ausfälle.

Die Integration spricht nur beim Start von Home Assistant und bei Fahrbefehlen mit der Box. Ein Ausfall
fällt deshalb erst beim nächsten Befehl auf.

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
   8-stelliger Code.
2. In Home Assistant: **Einstellungen → Geräte & Dienste → Integration hinzufügen → Auping Bett (lokal)**.
   IP-Adresse und Code eingeben.
3. Namen der Seiten prüfen; Bett-Typ, Motoren und Seiten werden aus der Box gelesen.

Vergib der Box am besten im Router eine feste IP-Adresse. Bekommt sie doch eine neue, sucht die Integration
sie beim nächsten Fahrbefehl im Heimnetz (dieser eine Befehl geht verloren).

## Erste Schritte

1. **Gleichzeitig oder nacheinander?** Bedienfeld → Einstellungen → „Test starten“. Laufen beide Seiten los,
   bleibt „Gleichzeitig“ eingestellt. Stoppt eine Seite, sobald die andere startet, „Nacheinander“ wählen.
2. **Laufzeit messen:** Kalibrieren → „Hochfahrt messen“. Einmal von ganz unten nach ganz oben fahren und
   tippen, wenn der Motor am Anschlag stehen bleibt. Ohne Messung nimmt die Integration 25 s an.
3. **Leerweg nachjustieren**, je Motor: „Alles flach“, mit „Beide“ ein Stück hochfahren, auf „Nachjustieren“
   tippen, die zurückliegende Seite nachfahren, bis beide gleich hoch sind, „Passt jetzt“.

## Grenzen

- Die Lage ist geschätzt, die Box meldet keine Position. Nach Benutzung der **Funk-Fernbedienung** kennt die
  Integration die Lage nicht mehr; *Referenzfahrt* oder *Alles flach* stellt sie wieder her.
- Zum **Nachjustieren** muss „Alles flach“ ganz zu Ende gelaufen sein (kein „sichert Endlage“ mehr).

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

## Lizenz

MIT
