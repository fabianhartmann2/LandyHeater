# Projekt: Migration einer Autoterm/Planar-Dieselheizungssteuerung von Node-RED/Raspberry Pi auf ESP32 mit MicroPython

Ich habe eine funktionierende Steuerung für eine Autoterm/Planar-Dieselstandheizung auf einem Raspberry Pi mit Node-RED entwickelt. Das Kommunikationsprotokoll des originalen Autoterm-Bedienteils wurde reverse-engineert und in Node-RED nachgebaut.

Der bestehende Node-RED-Flow ist als Datei beigefügt und ist die verbindliche Referenz für das bereits funktionierende Autoterm-Protokoll.

Migriere dieses Projekt vollständig auf einen ESP32 mit MicroPython.

## Wichtigste Grundregel

Übernimm das funktionierende Autoterm-Protokoll aus dem Node-RED-Flow so exakt wie möglich.

Ändere oder „korrigiere“ keine Protokolldetails stillschweigend.

Unterscheide bei der Analyse ausdrücklich zwischen:

- aus dem Node-RED-Flow verifizierten Eigenschaften,
- aus dem Flow abgeleiteten Annahmen,
- Verbesserungsvorschlägen.

Wenn eine Protokolleigenschaft nicht eindeutig aus dem bestehenden Flow hervorgeht, mache sie zunächst konfigurierbar oder dokumentiere die offene Annahme.

Die Softwarearchitektur außerhalb des Protokolls darf und soll modernisiert werden.

---

# 1. Zielplattform

Programmiersprache:

MicroPython

Zielplattform:

ESP32-Familie.

Das genaue ESP32-Modell steht aktuell noch nicht fest. Die Software darf deshalb keine unnötigen Board-spezifischen Annahmen enthalten.

Erstelle eine zentrale Hardwarekonfiguration, beispielsweise:

`board_config.py`

Darin müssen mindestens konfigurierbar sein:

- UART TX
- UART RX
- 1-Wire GPIO
- I2C SDA
- I2C SCL
- UART-ID
- Baudrate
- optionale zukünftige UARTs

Die endgültigen Pins werden später festgelegt.

---

# 2. Elektrische Verbindung zur Autoterm-Heizung

Die Autoterm-Kommunikation verwendet auf Heizungsseite 5-V-Signale.

Zwischen Heizung und ESP32 befindet sich ein vorhandener bidirektionaler Pegelwandler:

5 V ↔ 3,3 V

Danach geht die UART-Verbindung direkt auf ESP32-GPIOs.

Der ESP32 soll das originale Autoterm-Bedienteil vollständig ersetzen.

Es ist für Version 1 kein paralleler Betrieb mit dem originalen Bedienteil erforderlich.

---

# 3. Bestehendes Autoterm-Protokoll

Analysiere zuerst den beigefügten Node-RED-Flow vollständig und erstelle daraus eine kleine Protokollspezifikation.

Bekannte Eigenschaften aus der bestehenden Implementierung sind unter anderem:

UART:

- 9600 Baud
- 8 Datenbits
- keine Parität
- 1 Stopbit

Bekannte Command IDs:

- `0x01` Start
- `0x02` Get/Set Settings
- `0x03` Shutdown
- `0x04` Initialization
- `0x0F` Status
- `0x11` Panel/external temperature

Bekannte Requests umfassen unter anderem:

Initialization:

`AA 03 00 00 04 + CRC`

Status:

`AA 03 00 00 0F + CRC`

Shutdown:

`AA 03 00 00 03 + CRC`

Der bestehende Node-RED-Code verwendet CRC16/IBM mit Startwert `0xFFFF`.

Überprüfe die exakte CRC-Berechnung sowie insbesondere die Byte-Reihenfolge anhand des bestehenden Codes.

Nicht einfach eine Standard-CRC-Bibliothek verwenden und annehmen, dass deren Output identisch ist.

Erstelle Testvektoren, mit denen bewiesen wird, dass MicroPython exakt dieselben Frames erzeugt wie Node-RED.

---

# 4. UART-Empfang

Die Node-RED-Implementierung behandelt eingehende Daten binär und verwendet eine Inter-Byte-Trennung.

Implementiere auf dem ESP32 einen robusten non-blocking Frame-Empfänger.

Er muss insbesondere mit folgenden Situationen umgehen können:

- partieller Frame
- mehrere Frames hintereinander
- unvollständiger Frame
- falscher Header
- falsche Länge
- CRC-Fehler
- Kommunikations-Timeout

Verwende keine blockierenden langen `sleep()`-Aufrufe.

Wenn möglich, nutze Frame-Längeninformationen aus dem Protokoll; ein Inter-Byte-Timeout kann ergänzend eingesetzt werden.

---

# 5. Empfangene Statusdaten

Der bestehende Flow interpretiert unter anderem folgende Informationen aus Statusantworten:

- Heater Voltage
- Heater State
- Glow Plug
- Vent/Fan

Die bestehenden Bytepositionen müssen aus dem Flow übernommen und beim Portieren verifiziert werden.

Bekannte Heater-States sind:

- 0 = Off
- 1 = Starting
- 4 = Running
- 5 = Shutting Down
- 6 = Temperature Monitoring

Erstelle dafür klare Python-Typen/Konstanten statt Magic Numbers.

Beispiel:

`HeaterState.OFF`

`HeaterState.STARTING`

`HeaterState.RUNNING`

`HeaterState.SHUTTING_DOWN`

`HeaterState.TEMP_MONITORING`

---

# 6. Softwarearchitektur

Die Anwendung soll modular aufgebaut sein.

Vorgeschlagene Module:

- `main.py`
- `board_config.py`
- `autoterm_protocol.py`
- `heater_controller.py`
- `temperature_manager.py`
- `scheduler.py`
- `rtc_manager.py`
- `config_manager.py`
- `network_manager.py`
- `api_server.py`
- `event_logger.py`
- `diagnostics.py`

Web-Dateien beispielsweise:

- `www/index.html`
- `www/app.js`
- `www/styles.css`

Übersetzungen separat halten.

Die Module sollen möglichst lose gekoppelt sein.

---

# 7. Zentrales Soll-/Ist-Modell

Die neue Architektur soll bewusst zwischen dem gewünschten Zustand und dem tatsächlichen Zustand der Heizung unterscheiden.

Beispielsweise:

Requested:

- requested_on
- control_mode
- target_temperature
- power_level
- requested_duration

Actual:

- communication_state
- heater_state
- voltage
- glow_plug
- fan
- last_successful_status

Webinterface, REST API und Timer dürfen ausschließlich den Requested State verändern.

Nur der `HeaterController` darf UART-Kommandos senden.

---

# 8. HeaterController State Machine

Implementiere eine echte State Machine.

Sie muss mindestens folgende reale Zustände berücksichtigen:

- OFF
- STARTING
- RUNNING
- SHUTTING_DOWN
- TEMP_MONITORING
- UNKNOWN / COMMUNICATION_ERROR

Beispiele:

Requested ON + Actual OFF
→ Start senden.

Requested OFF + Actual RUNNING
→ Shutdown senden.

Actual STARTING
→ keinen zweiten Start senden.

Actual SHUTTING_DOWN
→ keinen neuen Start senden, bis der Zustand sicher ist.

Bei Kommunikationsverlust:

- Actual State → UNKNOWN
- keine neuen Startbefehle
- Kommunikation weiter versuchen
- Fehler im Webinterface anzeigen
- beim Wiederherstellen zuerst tatsächlichen Status synchronisieren

---

# 9. Verhalten nach Neustart

Nach jedem ESP32-Neustart gilt:

Niemals unmittelbar Start oder Stop senden.

Zuerst:

1. UART initialisieren.
2. Autoterm initialisieren.
3. tatsächlichen Heizungsstatus abfragen.
4. State Machine synchronisieren.

Wenn die Heizung bereits läuft, soll der ESP32 diesen Zustand erkennen und übernehmen.

Ein alter gespeicherter ON-Zustand darf nach einem vollständigen Cold Boot jedoch nicht einfach einen neuen Heizungsstart verursachen.

Nach einem vollständigen Stromverlust ist:

`requested_on = False`

Ausnahme:

Ein aktuell gültiger Timer darf anschließend einen neuen Start auslösen.

Wenn während eines kurzen ESP32-Reboots eine aktive Heizsession weiterläuft, darf eine noch gültige Session nach erfolgreicher Statussynchronisation übernommen werden. Vor jedem neuen UART-Befehl muss aber der tatsächliche Zustand bekannt sein.

---

# 10. Betriebsmodi

Es gibt drei Benutzer-Betriebsmodi:

1. Dachzelt-Temperatur
2. Innenraum-Temperatur
3. Power Mode

Die bisherigen internen Node-RED-Werte für die Modi müssen analysiert werden.

Im bestehenden Projekt werden beispielsweise interne Werte wie `21`, `22` und `04` verwendet.

Diese Werte sollen in der neuen Anwendung nicht als String-Magic-Numbers verteilt werden.

Verwende verständliche Konstanten bzw. Enums.

---

# 11. Temperaturmodus

Temperaturbereich:

5–30 °C

Im Temperaturbetrieb wird der ausgewählte externe DS18B20-Wert regelmäßig über das entsprechende Autoterm-Kommando an die Heizung übertragen.

Übernimm zunächst exakt die Übertragungsfrequenz bzw. das Verhalten der bestehenden Node-RED-Implementierung.

Falls die Frequenz nicht eindeutig bestimmt werden kann, dokumentiere dies und mache sie als interne Konstante konfigurierbar.

---

# 12. Power Mode

Power Mode verwendet 9 Leistungsstufen:

1–9

Im Webinterface:

`Low  1 2 3 4 5 6 7 8 9  High`

Die tatsächliche Protokollabbildung muss aus dem bestehenden Node-RED-Flow übernommen werden.

---

# 13. Ventilation

Die Autoterm-Heizung kann grundsätzlich auch als reine Ventilation ohne Heizung betrieben werden.

Diese Funktion wird aktuell noch nicht verwendet.

Für Version 1 muss sie nicht als Benutzerfunktion implementiert werden.

Die Architektur soll jedoch so gestaltet werden, dass später ein `VENTILATION`-Modus ergänzt werden kann.

---

# 14. Temperatursensoren

Es existieren drei physische DS18B20-Sensoren:

- Dachzelt
- Innenraum
- Außen

Sie hängen am 1-Wire-Bus.

Die Sensoren sollen anhand ihrer eindeutigen ROM-ID fest zugeordnet werden.

Die Zuordnung soll aber nicht hart im Python-Code stehen, sondern über den Setup-Assistenten erfolgen und persistent gespeichert werden.

---

# 15. Sensor-Setup

Beim ersten Setup:

1. Alle DS18B20 suchen.
2. ROM-ID anzeigen.
3. aktuelle Temperatur live anzeigen.
4. Benutzer weist jeden Sensor einer Rolle zu:
   - Dachzelt
   - Innenraum
   - Außen
5. Mapping persistent speichern.

Der Benutzer kann einen Sensor beispielsweise mit der Hand erwärmen, um ihn identifizieren zu können.

Sensorzuordnungen müssen später über Settings geändert werden können.

---

# 16. Sensorfehler

Ein Sensorfehler darf niemals dazu führen, dass `0 °C` oder ein anderer künstlicher Wert an die Heizung gesendet wird.

Policy:

Nach ca. 30 Sekunden ohne neuen gültigen Wert:

- Sensorstatus = STALE
- Warnung im Webinterface
- letzten gültigen Wert weiterverwenden

Bis maximal:

5 Minuten

Wenn nach 5 Minuten noch kein gültiger Wert vorhanden ist und dieser Sensor der aktive Regelsensor ist:

- keine falsche Temperatur senden
- HeaterController fordert kontrollierten Shutdown an
- Fehler im Webinterface
- Event in History schreiben

---

# 17. DS3231 RTC

Verwende eine DS3231 als Hardware-RTC über I2C.

Sie ist die lokale zuverlässige Zeitbasis für Timer.

Die Uhrzeit soll zusätzlich korrigiert werden können durch:

1. NTP, wenn Internet vorhanden ist
2. den Browser/Smartphone beim Öffnen des Webinterfaces

Priorität:

verlässliche lokale RTC auch ohne Internet.

Das System muss vollständig ohne Internet funktionieren.

---

# 18. Scheduler

Mehrere Timer müssen gleichzeitig definierbar sein.

Ein Timer enthält mindestens:

- optionaler Name
- enabled
- Wochentage
- Startzeit
- Betriebsmodus
- Zieltemperatur oder Power-Level
- Laufzeit

Beispiel:

Montag–Freitag  
06:30  
Dachzelt  
20 °C  
60 Minuten

Nach Ablauf der Laufzeit wird die Heizung automatisch kontrolliert ausgeschaltet.

---

# 19. Timer-Priorität

Manueller Eingriff hat immer Priorität.

Wenn ein Benutzer während einer Timer-Session `Stop` drückt:

- Session sofort beenden
- Timer darf die Heizung für diese konkrete Ausführung nicht erneut einschalten

---

# 20. Laufzeitbegrenzung

Es gibt eine global konfigurierbare maximale Heizungsbetriebszeit.

Sie kann über das Webinterface geändert werden.

Kein manueller Start oder Timer darf diese maximale Laufzeit überschreiten.

---

# 21. Manuelle Startdauer

Beim manuellen Start stehen als Schnellwahl mindestens zur Verfügung:

- 30 Minuten
- 60 Minuten
- 90 Minuten
- 120 Minuten

Sie sind zusätzlich durch die globale maximale Laufzeit begrenzt.

---

# 22. Quick Start

Home besitzt einen prominenten:

`Quick Start`

Button.

Quick Start verwendet persistent konfigurierte Defaults:

- Default Mode
- Default Target Temperature oder Power-Level
- Default Runtime

Darunter gibt es:

`Configure & Start`

für einen individuell konfigurierten Start.

---

# 23. Während des Heizbetriebs

Während die Heizung läuft dürfen Benutzer ändern:

- Zieltemperatur
- verbleibende Laufzeit

Die Laufzeit soll einfach verlängert werden können, z. B. `+15 min`.

Der Betriebsmodus soll während einer laufenden Session zunächst nicht direkt gewechselt werden.

Power-Level-Änderungen sollen nur dort angeboten werden, wo sie mit dem bestehenden Autoterm-Protokoll sicher unterstützt werden.

---

# 24. Stop

`Stop Heater`

benötigt keine zusätzliche Bestätigung.

Der Klick setzt unmittelbar den Requested State auf OFF.

Der tatsächliche Status wird anschließend korrekt als beispielsweise:

`Shutting Down`

angezeigt, bis die Autoterm ihren Shutdown abgeschlossen hat.

---

# 25. Persistente Konfiguration

Persistiere unter anderem:

- Hotspot-Konfiguration
- bekannte WLANs
- RTC-/Zeiteinstellungen
- Timer
- Standardmodus
- Standardtemperatur
- Standard-Power-Level
- Standardlaufzeit
- maximale Laufzeit
- Sensor-ROM-Zuordnung
- Sensor-Timeout
- letzte UI-Werte

Schreibe nicht sekündlich in den Flash.

Verwende möglichst atomare Updates, damit ein Stromverlust nicht leicht eine Konfigurationsdatei zerstört.

---

# 26. Netzwerk

Der ESP32 betreibt dauerhaft einen eigenen Access Point:

SSID:

`Landy Heater`

Das Passwort ist über das Webinterface konfigurierbar.

Das Webinterface benötigt keine zusätzliche Benutzeranmeldung.

Der WPA-geschützte Hotspot ist für Version 1 der Zugriffsschutz.

---

# 27. AP + Station Mode

Der ESP32 soll gleichzeitig:

- eigenen Access Point bereitstellen
- sich als Client mit bekannten WLANs verbinden können

Mehrere bekannte WLANs müssen speicherbar sein.

Wenn ein bekanntes WLAN verfügbar ist:

→ verbinden.

Wenn keines verfügbar ist:

→ der lokale Hotspot funktioniert weiterhin unverändert.

Internet darf niemals Voraussetzung für Heizung, Sensoren, Timer oder Webinterface sein.

---

# 28. mDNS

Das Webinterface soll möglichst erreichbar sein unter:

`http://heater.local`

Die IP-Adresse muss zusätzlich weiter funktionieren.

---

# 29. REST API

Webinterface und Backend kommunizieren über eine klar definierte REST API.

Beispiele:

`GET /api/status`

`POST /api/heater/start`

`POST /api/heater/stop`

`PATCH /api/heater/session`

`GET /api/timers`

`POST /api/timers`

`PUT /api/timers/{id}`

`DELETE /api/timers/{id}`

`GET /api/settings`

`PATCH /api/settings`

`GET /api/events`

`GET /api/diagnostics`

Definiere eine konsistente JSON-Struktur und dokumentiere die API.

Keine API darf UART direkt steuern. Sie verändert nur den Application/Requested State.

---

# 30. Webinterface

Das Webinterface muss:

- vollständig lokal auf dem ESP32 liegen
- keine CDN-Abhängigkeiten haben
- offline funktionieren
- mobile-first sein
- auch auf Desktop funktionieren
- systemeigene Fonts verwenden
- responsiv sein

Design:

- reduziert
- modern
- großzügige Abstände
- klare Typografie
- abgerundete Cards
- wenige Farben

Dark/Light Mode automatisch über:

`prefers-color-scheme`

---

# 31. Hauptnavigation

Version 1:

- Home
- Timers
- Settings

Später soll die Architektur zu einem allgemeinen Landy-Control-System erweitert werden können, beispielsweise mit:

- Heater
- Energy
- Battery
- Charging

Das heutige Heizungsprojekt soll deshalb nicht unnötig monolithisch entwickelt werden.

---

# 32. Home

Home zeigt mindestens:

- tatsächlichen Heater State
- Dachzelt-Temperatur
- Innenraum-Temperatur
- Außentemperatur
- verbleibende Laufzeit
- nächsten Timer
- aktuelle Warnungen/Fehler

Wenn Heizung OFF:

- großer `Quick Start`
- `Configure & Start`

Wenn Heizung läuft:

- Mode
- Target Temperature bzw. Power
- aktuelle Regelsensor-Temperatur
- verbleibende Zeit
- `+15 min`
- Temperaturänderung
- `Stop Heater`

Technische Details wie Glow Plug sollen nicht die Hauptansicht überladen.

---

# 33. Configure & Start

Auswahl:

Mode:

- Dachzelt
- Innenraum
- Power

Temperaturmodus:

- 5–30 °C

Power:

- Stufe 1–9 mit Low/High-Kennzeichnung

Duration:

- 30
- 60
- 90
- 120 Minuten

Nur Werte zulassen, die innerhalb der globalen Max Runtime liegen.

---

# 34. Timers UI

Timer als übersichtliche Cards/List darstellen.

Jeder Timer zeigt beispielsweise:

- Name
- Zeit
- Wochentage
- Modus
- Temperatur/Power
- Laufzeit
- Enabled

Funktionen:

- Add
- Edit
- Enable/Disable
- Delete

Mehrere Timer müssen unterstützt werden.

---

# 35. Settings

Unterteile Settings mindestens in:

- Heater
- Network
- Temperature Sensors
- Timers & Runtime
- Date & Time
- System
- Diagnostics

Heater enthält beispielsweise:

- Quick Start Mode
- Default Temperature
- Default Power
- Default Runtime
- Maximum Runtime

---

# 36. Mehrsprachigkeit

Baue das Webinterface von Anfang an internationalisierbar.

Keine UI-Texte überall hart codieren.

Verwende zentrale Translation Dictionaries/Dateien.

Deutsch soll als Standardsprache problemlos unterstützt werden.

Die Architektur muss das einfache Hinzufügen weiterer Sprachen erlauben.

Wenn die endgültige Sprachauswahl noch nicht feststeht, darf dies die Implementierung nicht blockieren.

---

# 37. Setup Assistant

Beim ersten Start soll ein Setup-Assistent erscheinen.

Vorgeschlagener Ablauf:

1. Sprache
2. Datum/Uhrzeit / RTC
3. bekannte WLANs
4. Passwort für `Landy Heater`
5. DS18B20 erkennen und Rollen zuweisen
6. Autoterm UART-Verbindung testen
7. Quick-Start-Defaults definieren
8. Zusammenfassung
9. Setup abschließen

Setup-Status persistent speichern.

Der Setup-Assistent muss später manuell erneut gestartet werden können.

---

# 38. Event History

Speichere ungefähr die letzten 200 relevanten Ereignisse als Ringbuffer.

Beispiele:

- ESP32 boot
- Heater start requested
- Heater started
- Heater stop requested
- Heater stopped
- Timer triggered
- Sensor stale
- Sensor failed
- Heater communication lost
- Heater communication restored
- Autoterm error
- Configuration changed
- RTC synchronized

Keine sekündlichen Telemetriedaten permanent in den Flash schreiben.

---

# 39. Diagnostics

Normale Diagnostics sollen mindestens zeigen:

- Heater communication OK/failed
- letzter erfolgreicher Status
- Heater State
- Heater Voltage
- Glow Plug
- Fan/Vent
- alle DS18B20
- ROM IDs
- Sensor age
- RTC time/status
- Wi-Fi AP
- Wi-Fi Station
- IP-Adressen
- Uptime
- Memory

---

# 40. Advanced Diagnostics

Advanced Diagnostics sollen rohe Autoterm-Kommunikation anzeigen.

Pro Eintrag mindestens:

- Timestamp
- RX/TX
- kompletter Hex-Frame
- Länge
- CRC valid/invalid
- erkannter Command

Beispiel:

`12:31:04.152 TX AA0300000F....`

`12:31:04.198 RX AA04.... CRC OK STATUS`

Implementiere einen browserbasierten Live-Log.

Wähle dafür eine robuste, ressourcenschonende Technik. WebSockets sind nicht zwingend notwendig; Polling/SSE darf verwendet werden, wenn es auf MicroPython robuster ist.

Der Live-Log darf die eigentliche Heizungssteuerung nicht blockieren.

---

# 41. Log Export

Event History und Diagnoseinformationen sollen als Datei über das Webinterface exportierbar sein.

Beispielsweise:

- JSON
- Text

Der Export soll spätere Fehlersuche erleichtern.

---

# 42. Kommunikationsfehler

Wenn mehrere Statusabfragen hintereinander fehlschlagen:

- Kommunikation = ERROR
- actual state = UNKNOWN
- keine neuen Startbefehle
- Wiederverbindungsversuche fortsetzen
- Warnung im Webinterface
- Event loggen

Nicht blind Shutdown-Befehle senden, wenn die Kommunikation bereits als verloren gilt.

Nach Wiederherstellung:

- zuerst Status synchronisieren
- erst danach Requested und Actual State wieder vergleichen

---

# 43. Watchdog und Robustheit

Verwende den ESP32-Watchdog sinnvoll.

Die Heizungssteuerung muss auch weiter funktionieren, wenn:

- kein Browser verbunden ist
- Webserver einen Fehler hat
- Internet fehlt
- NTP nicht funktioniert
- Station-WLAN verschwindet

Webserver und Benutzeroberfläche sind niemals Voraussetzung für die eigentliche Heizungsregelung.

---

# 44. Concurrency

Nutze nach Möglichkeit eine übersichtliche `uasyncio`-Architektur.

Trenne beispielsweise:

- UART RX
- Heater Controller
- Sensor polling
- Scheduler
- Web/API
- Netzwerk
- Logging

Vermeide Busy Loops.

Vermeide lange blockierende Sleeps.

Achte auf begrenzten RAM und unnötige Speicherallokationen.

---

# 45. Web-Technik

Bevorzuge eine ressourcenschonende Lösung.

Keine schweren JavaScript-Frameworks wie React/Vue/Angular auf dem ESP32.

HTML + CSS + Vanilla JavaScript sind ausreichend.

Verwende keine externen Cloud-Ressourcen oder Webfonts.

---

# 46. Zukünftige Erweiterbarkeit

Die Architektur soll später Module aufnehmen können für beispielsweise:

- Votronic Batteriecomputer
- Votronic B2B-Laderegler
- weitere Fahrzeugtelemetrie
- möglicherweise MQTT
- möglicherweise Remote Access

Diese Funktionen gehören nicht zu Version 1.

Entwickle aber beispielsweise eine saubere Service-/Module-Struktur, damit später ein `EnergyService` ergänzt werden kann.

---

# 47. Nicht Teil von Version 1

Nicht implementieren:

- Votronic Shunt
- Votronic B2B
- MQTT
- Cloud Remote Access
- Internetabhängigkeit
- Web-Firmware-Update
- reine Ventilationssteuerung als User Feature
- zusätzliche Web-Authentifizierung

---

# 48. Tests

Erstelle Tests für alle Protokollteile, die ohne echte Hardware getestet werden können.

Insbesondere:

## CRC

Testvektoren aus der existierenden Node-RED-Implementierung.

## Frame Builder

Exakte Binärausgabe für:

- Init
- Status
- Shutdown
- Start Power Mode
- Start Temperature Mode
- external temperature

## Parser

- korrekter Frame
- falscher CRC
- falsche Länge
- Teilframe
- mehrere Frames
- unbekannter Command

## State Machine

Mindestens:

OFF + Requested ON → Start

RUNNING + Requested OFF → Stop

STARTING + Requested ON → kein doppelter Start

SHUTTING_DOWN + Requested ON → kein sofortiger Restart

UNKNOWN → kein neuer Start

Sensor stale < 5 min → letzter gültiger Wert

Sensor stale > 5 min → Shutdown Request

## Scheduler

- Wochentage
- Timer start
- Timer stop
- manuelle Übersteuerung
- Max Runtime

Wenn möglich, die reinen Python-Komponenten so schreiben, dass Tests auch mit normalem CPython auf einem Entwicklungsrechner laufen können.

Hardwarezugriffe dafür abstrahieren.

---

# 49. Projektlieferumfang

Liefere kein einzelnes langes `main.py`.

Erstelle ein vollständiges, strukturiertes Projekt.

Mindestens:

- vollständiger Sourcecode
- Webinterface
- REST API
- Setup Assistant
- Autoterm Protocol
- Heater Controller
- Sensor Manager
- Scheduler
- RTC
- Config Storage
- Network Manager
- Event Logger
- Diagnostics
- Tests
- Requirements/Dependencies
- README

---

# 50. README

README muss mindestens erklären:

## Hardware

- ESP32
- 5 V ↔ 3,3 V Level Shifter
- Autoterm UART
- 3 × DS18B20
- DS3231
- DC/DC-Versorgung 12 V → ESP32

## Wiring

Mit Platzhaltern für aktuell unbekannte GPIOs.

## Flashing

Wie MicroPython auf den ESP32 installiert wird.

## Deployment

Wie die Projektdateien übertragen werden.

## First Boot

Wie `Landy Heater` verbunden und Setup ausgeführt wird.

## Tests

Wie Protocol Unit Tests ausgeführt werden.

## Diagnostics

Wie Logs exportiert werden.

---

# 51. Vorgehensweise bei der Umsetzung

Arbeite in klaren Phasen.

## Phase 1 – Analyse

Analysiere zuerst den vollständigen Node-RED-Flow.

Erstelle:

- Protocol Map
- Command Map
- Frame Format
- CRC-Verhalten
- Statusfelder
- State Mapping
- relevante Timer-/Sensorlogik

Zeige offene Annahmen explizit.

## Phase 2 – Protocol Library

Implementiere und teste:

- CRC
- Frame Builder
- Frame Parser
- UART Transport

Noch ohne Webinterface.

## Phase 3 – Heater Controller

Implementiere:

- Requested/Actual State
- State Machine
- Start/Stop
- Temperaturübertragung
- Fehlerbehandlung

## Phase 4 – Sensors / RTC / Scheduler

Implementiere:

- DS18B20
- DS3231
- Sensor Failure Policy
- mehrere Timer
- Max Runtime

## Phase 5 – Network / REST

Implementiere:

- AP `Landy Heater`
- Station Mode
- mehrere bekannte WLANs
- mDNS `heater.local`
- REST API

## Phase 6 – Web UI

Implementiere:

- Home
- Quick Start
- Configure & Start
- Running
- Timers
- Settings
- Events
- Diagnostics
- Dark/Light
- i18n
- Setup Wizard

## Phase 7 – Hardening

Implementiere:

- Watchdog
- Recovery
- Atomic config writes
- logging limits
- communication timeout handling
- sensor timeout handling

## Phase 8 – Documentation

README, Pin-Konfiguration, Installation und Tests.

---

# 52. Entwicklungsprinzip

Prioritäten in dieser Reihenfolge:

1. sichere Heizungssteuerung
2. korrekte Protokollimplementierung
3. Robustheit
4. Offline-Funktion
5. Verständlichkeit des Python-Codes
6. Erweiterbarkeit
7. Webinterface
8. Optimierung

Der Python-Code soll für jemanden verständlich bleiben, der Python deutlich besser versteht als C++.

Vermeide unnötig komplizierte Design Patterns.

Kommentiere insbesondere Reverse-Engineering-/Protokollteile ausführlich.

---

# 53. Verbesserungsvorschläge

Wenn du während der Umsetzung erkennst, dass sich gegenüber Node-RED etwas sinnvoll verbessern lässt:

1. erkläre die Verbesserung,
2. erkläre den Vorteil,
3. ändere keine kritische Protokollfunktion stillschweigend.

Bei ungefährlichen Softwarearchitekturverbesserungen darfst du eine sinnvolle Standardentscheidung treffen und sie dokumentieren.

---

# 54. Abschlusskriterium

Das Projekt gilt erst dann als vollständig migriert, wenn:

- Autoterm initialisiert werden kann
- Status gelesen werden kann
- Start funktioniert
- Shutdown funktioniert
- Power Mode funktioniert
- beide Temperaturmodi funktionieren
- externe Temperaturen korrekt übertragen werden
- Sensorausfall sicher behandelt wird
- mehrere Timer funktionieren
- Max Runtime funktioniert
- AP funktioniert
- bekannte WLANs funktionieren
- System offline funktioniert
- REST API funktioniert
- Webinterface funktioniert
- Setup Wizard funktioniert
- RTC funktioniert
- Event History funktioniert
- Live Diagnostics funktionieren
- Tests vorhanden sind
- Installation dokumentiert ist

Beginne jetzt mit Phase 1: Analysiere den beigefügten Node-RED-Flow vollständig und erstelle daraus zunächst eine präzise Protokoll- und Funktionsübersicht. Anschließend setze die Migration phasenweise um.