# Protokoll der Auping-Connect-Box

Nachvollzogen aus der Android-App *Auping Connect* 2.4.4 (`nl.auping.connect`, versionCode 216) zum
Zweck der Interoperabilität. Hier steht nur, was für eine eigene Steuerung nötig ist – kein App-Code.

## Verbindung

- Die Box hängt im Heim-WLAN und spricht **HTTP auf Port 80** (vor der WLAN-Einrichtung als eigener
  Access Point unter `https://192.168.0.1`).
- Sie meldet sich per mDNS als `_http._tcp` mit einem Dienstnamen, der `Auping Connect` enthält.
- **Jede Anfrage** braucht den Header `X-Key: <schlüssel>`. Fehlt er oder ist er falsch, antwortet die
  Box mit `403` und leerem Körper.
- Die App nutzt keine wiederverwendeten Verbindungen (Verbindungs-Pool 0) und nur HTTP/1.1.

### Der Schlüssel (X-Key)

- Erzeugt die App beim ersten Einrichten selbst: **8 zufällige Kleinbuchstaben** `a–z`.
- Wird der Box beim Einrichten über `GET /api/config?key=…` mitgeteilt (siehe unten).
- Weitere Geräte übernehmen ihn über **„Konfiguration teilen“**: Anzeige als Text (in zwei Hälften,
  z. B. `abcd efgh`) und als QR-Code mit dem Inhalt `<ip>-<schlüssel>`. Manuell eingegeben wird er
  kleingeschrieben und ohne Leerzeichen verwendet.

## Konfiguration lesen

`GET /api/config` → JSON, u. a.:

| Feld | Bedeutung |
|---|---|
| `name`, `hostname`, `ip`, `mac_address`, `firmware` | Kenndaten der Box |
| `bed_type` | Fernbedienungstyp und Motoren je Seite (Tabelle unten) |
| `cbu_type` | Anzahl Seiten: `21`, `22`, `31` = eine, `23`, `33` = zwei |
| `sync` | `1` = beide Seiten bewegen sich gemeinsam (eine Matratze, zwei Steuereinheiten) |
| `cbu_a_command`, `cbu_b_command` | zuletzt ausgeführter Befehl je Steuereinheit |
| `alarm_*`, `utc`, `current_system_time` | Weckfunktion der Box |
| `stored_ap`, `wifi_configured` | WLAN-Status |

### `bed_type`

`>= 30` Standard-Fernbedienung, `20–29` Royal, darunter nicht konfiguriert.

| bed_type | Seite A (links) | Seite B (rechts) |
|---|---|---|
| 30 / 31 / 32 | 1 / 2 / 3 Motoren | – |
| 33 / 34 / 35 | 1 Motor | 1 / 2 / 3 Motoren |
| 36 / 37 / 38 | 2 Motoren | 1 / 2 / 3 Motoren |
| 39 / 40 / 41 | 3 Motoren | 1 / 2 / 3 Motoren |
| 20 | Royal 2 Motoren | – |
| 21 | Royal 5 Motoren | – |
| 22 / 23 | Royal 2 Motoren | Royal 2 / 5 Motoren |
| 24 / 25 | Royal 5 Motoren | Royal 2 / 5 Motoren |

Die App zeigt bei 1 Motor das Rückenteil, bei 3 Motoren Kopf-, Rücken- und Beinteil.

## Motoren steuern

`POST /api/cbu/motor/extended`, `Content-Type: application/json`

```json
{
  "CBU": 1,
  "motor_1": { "Action": 0, "TimeInMs": 0 },
  "motor_2": { "Action": 1, "TimeInMs": 30000 },
  "motor_3": { "Action": 0, "TimeInMs": 0 }
}
```

- `CBU`: `0` = beide Seiten, `1` = Seite A, `2` = Seite B.
- `Action`: `0` = Stopp, `1` = hoch, `2` = runter.
- `TimeInMs`: nach dieser Zeit schaltet die Box den Motor selbst ab. Die App schickt nie mehr als
  `30000` (auch gespeicherte Favoriten sind auf 30 s begrenzt).
- Ein Befehl legt den Zustand **aller** Motoren der angesprochenen Steuereinheit fest; nicht genannte
  bzw. mit `Action 0` gesendete Motoren werden angehalten.
- Die App sendet für jeden Motor des Fernbedienungstyps einen Eintrag:

| Motor (App intern) | Standard | Royal |
|---|---|---|
| Kopfteil (HEAD = 1) | `motor_1` | `motor_1` |
| Rückenteil (BACK = 2) | `motor_2` | `motor_2` |
| Beinteil (LEGS = 3) | `motor_3` | `motor_4` |
| Lendenteil (LOWER_BACK = 4) | – | `motor_3` |
| Fußteil (FEET = 5) | – | `motor_5` |

Verhalten der App: Taste gedrückt = Motor mit `30000` ms losschicken, Taste losgelassen = Stopp
(alle Motoren `Action 0`). „Alles hoch/runter“ = alle Motoren mit `30000` ms. Antwort: `2xx` = ok,
`403` = Schlüssel falsch.

Die Positionen der Lattenroste meldet die Box nicht – die App merkt sich Favoriten als Fahrzeiten
in Millisekunden ab „ganz unten“.

## Weitere Endpunkte (hier nicht benutzt)

| Endpunkt | Zweck |
|---|---|
| `GET /api/config?key=&bed_type=&cbu_type=&sync=` | Ersteinrichtung: Schlüssel und Bett-Typ setzen |
| `GET /api/cbu/rf/copy?cbu=` | Funk-Fernbedienung anlernen |
| `GET /api/config/alarm?utc=&time=&cbu=&motor=` | Weckfunktion (Bett fährt zur Weckzeit) |
| `GET /api/config/wifi`, `/api/config/wifi/scan`, `/api/config/wifi/connect`, `/api/config/stopap` | WLAN |
| `GET /api/config/reset` | **Werksreset** |
| `GET /api/config/restart` | Neustart |
| `POST /upgrade_chunk.html`, `GET /api/upgrade/start` | Firmware-Update (aktuell 1.05.6) |

## SmartBase (Bluetooth) – nur zur Einordnung

Die neuere SmartBase wird per Bluetooth LE gesteuert (Gerätename enthält `AUPING_CM`, Kopplung über das
Betriebssystem). Dienst `53653241-7570-696E-6743-6F6E6E656374`, Seite A („Hive“) über Merkmal
`43323141-…`, Seite B („Drone“) über `43323241-…`. 8 Byte: Zielposition 0–100 % für bis zu vier Motoren,
dann Geschwindigkeit 0–100 % je Motor; alles 0 = Stopp. Von dieser Integration nicht unterstützt.
