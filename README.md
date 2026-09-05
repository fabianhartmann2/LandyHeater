# Landy Heater

Migration der vorhandenen Autoterm/Planar-Steuerung von Raspberry Pi und
Node-RED auf einen ESP32 mit MicroPython.

## Aktueller Stand

Dieses Paket folgt weiterhin dem ursprünglich festgelegten Phasenplan 0–13.
Die Komponenten von **Phase 8 – REST API** sind implementiert und unter
CPython geprüft. Der isolierte USB-only-Board-Smoke und ein enger
Frozen-AP/HTTP-Handytest bestanden auf dem DFR0654; letzterer verwendete jedoch
nur einen festen Read-only-Prüfhandler und ist keine Abnahme der gemeinsamen
`RestApplication`-/ConfigManager-/Storage-Produktclosure.

Der aktuelle Zielrunner wurde deshalb auf den architekturkonformen
Minimalpfad reduziert: AP und genau einen Handy-Client bestätigen, die volle
Produktclosure lazy laden und genau einen produktiven Listener auf Port 80 für
`GET /api/v1/status` verwenden. Port 8080, Browser-Refreshes, Redirects und
zusätzliche Probe-Requests gehören nicht mehr zur Baseline. Die fokussierte
Hostmatrix deckt 24 getrennte Erfolgs- und Fehlergrenzen ab. Auf dem DFR0654
erreichte dieser Lauf den Listenerpfad, hatte vor `listen` aber nur 32.880 Bytes
freien Heap; der folgende Pflicht-Checkpoint fiel vor READY unter 32 KiB. Es
wurde daher keine Produktanfrage zugelassen und kein Phase-8-PASS erzeugt.

Als Nachfolgeboard ist ein DFRobot FireBeetle 2 ESP32-S3-U
**DFR0975-U N16R8** mit 16 MB Flash, 8 MB PSRAM und externer Antenne ausgewählt.
Das eingetroffene V1.0-Board mit `ESP32-S3-WROOM-1U-N16R8` ist USB-/ROM-seitig
identifiziert. Das neue S3-Profil enthält geprüfte Pin-/Denylisten, lässt aber
alle Hardware- und Funkfreigaben geschlossen. MicroPython 1.28 wurde als
eigener 16-MB-/Octal-PSRAM-Build zweimal sauber und bytegleich erzeugt; die
geprüften Artefakte liegen unter `firmware/dfr0975u_n16r8/`. Das private,
geräteseitig verifizierte 16-MB-Werksbackup, die statische Artefaktprüfung und
der aktuelle vollständige Host-Test mit 1098/1098 bestandenen Tests sind
abgeschlossen.
Nach einer neuen hashgebundenen Freigabe wurde der Flash vollständig gelöscht
und das Combined-Image erfolgreich geschrieben und verifiziert. Passiver
MicroPython-1.28-USB-Boot, 8-MiB-PSRAM sowie getrennte interne und DMA-fähige
Speichergates sind bestanden. Auch manueller ROM-Recovery über `BOOT`/`RST`,
die vollständige 12,9375-MiB-VFS und ein isolierter echter A/B-Storage-
Roundtrip mit vollständigem Cleanup sind bestätigt; beide WLAN-Schnittstellen
blieben aus. Anschließend bestand das neue Board auch den getrennten
Phase-7-WLAN-/DHCP-Gate: genau ein stabiler WPA2-Handyclient erhielt
`192.168.4.2/24` mit Router `192.168.4.1`; es wurde kein HTTP-Listener geladen
und beide Funkinterfaces waren danach wieder aus. Der automatische
USB-Control-Line-Reset ist auf diesem Board hingegen nicht zuverlässig und
wird nicht als Recovery-Pfad angenommen. Der
vollständige Migrationsstand steht in `DFR0975U_MIGRATION.md`; die älteren
DFR0654-Versuche bleiben historische Evidenz in
`captures/2026-08-30-phase8-single-listener-project-state.md` und
`captures/2026-08-25-phase8-full-rest-progress.md`.
Der anschließend genau einmal ausgeführte DFR0975-U-Phase-8-Ein-Listener-Gate
bestand mit einem realen `GET /api/v1/status`, vollständiger HTTP-200-JSON-
Antwort, allen zehn GC-Heap-Grenzen, unveränderter Produktspeicherung und
vollständigem HTTP-/REST-/WLAN-Cleanup. `boot.py` und `main.py` bleiben bewusst
passiv. **Phase 8 ist damit zielseitig bestanden. Phase 9 – Web UI ist
ebenfalls abgeschlossen: Der S3-Kandidat wurde zweimal bytegleich gebaut,
statisch geprüft, hashgebunden ohne Full Erase bei `0x10000` geschrieben und
vollständig zurückgelesen. Der reale Handytest lieferte über einen einzigen
Port-80-Listener 9/9 UI-Ressourcen und 4/4 automatische API-Lesezugriffe aus;
alle Heap- und Cleanup-Gates bestanden.** Die genaue
Phase-9-Grenze ist in `PHASE9_WEB_UI.md` dokumentiert.
**Phase 10 – Setup Assistant und der reale Konfigurations-/Timer-Ablauf sind
funktional auf dem DFR0975-U abgenommen; die strikte Same-run-Wire-Prüfung
jeder einzelnen Browserressource bleibt formal offen:** Die UI erzwingt
explizite Passwort-/Open-Auswahl und schrittweise Eingabeprüfung. Der reale
Assistent speicherte in genau einer erfolgreichen isolierten Mutation den
erwarteten AP-Passwortwechsel und ein geschütztes Stations-WLAN. Nach echtem
Stations-DHCP und AP-Neuanmeldung wurden ein inaktiver Timer erstellt,
bearbeitet und über Resets dauerhaft bestätigt. Beim Löschen entdeckte der
Zieltest einen ungewollten leeren Browser-Request-Body. Die korrigierte Fassung
wurde erneut zweimal bytegleich gebaut, hashgebunden app-only geschrieben,
vollständig zurückgelesen und bestand anschließend das fortgesetzte Löschen,
den Speicher-Reload sowie den vollständigen Cleanup. Details stehen in
`PHASE10_SETUP_ASSISTANT.md`.
**Phase 10.1 – Portal- und Heimnetz-Erreichbarkeit hat zwei reale Zielbefunde
geliefert und liegt als portal-korrigierter Kandidat vor:** Der erste
Wildcard-HTTP-Ansatz scheiterte an einer im ESP32-MicroPython-Port fehlenden
Socketfunktion. Der danach geflashte Zwei-Listener-Kandidat bestätigte
Stations-DHCP, `heater.local`, Captive DNS sowie einen angenommenen AP-Request,
deckte aber zwei Wire-Encoding-Lücken der 302-Portalantwort auf. Der aktuelle
Kandidat lässt `302 Found` im begrenzten Encoder zu und entfernt den doppelten
`Cache-Control`-Header. AP- und Stationslistener bleiben ausdrücklich getrennt
und verwenden beide den einzigen benutzersichtbaren Port 80. Der neue
44-Datei-Kandidat ist durch zwei bytegleiche Builds und Offline-Artefaktprüfungen
belegt. Sein hashgebundener App-only-Flash und die vollständige unabhängige
Rücklesung sind bestanden. Das kombinierte Zielgate bestätigte danach
Stations-DHCP, mDNS, Captive DNS, automatische Portalöffnung, beide
TCP/80-Listener, lesenden Heimnetzzugriff, gesperrte Stationsmutationen,
Speichergrenzen und vollständigen Funk-Cleanup. Phase 10.1 ist angenommen.
Details stehen in `PHASE10_1_DISCOVERY.md`.
**Phase 11 – Events, Diagnose und Capture-Export ist software- und zielseitig
abgeschlossen:** Ein hardwarefreier, streng begrenzter Diagnose-Hub sammelt
die bereits vorhandenen Ereignisquellen sowie Kopien der UART-Aktivitäten.
Die neue, erst beim Öffnen geladene Diagnoseansicht aktualisiert kleine Seiten
alle zwei Sekunden, zeigt Systemzustand, Ereignisse und das Live-Protokoll und
exportiert Diagnose/Ereignisse als JSON sowie benannte Captures als JSON oder
NDJSON. Zugangsdaten und freie Treiberfehlertexte gelangen nicht in die
Puffer. Die vollständige Hostregression, zwei bytegleiche Builds, die
Offline-Artefaktprüfung, der hashgebundene App-only-Flash mit vollständiger
Rücklesung sowie der reale Diagnose-/Capture-/Export-Handyablauf sind
bestanden. Die Nachkontrolle bestätigte Funk-, RAM- und Dateibereinigung;
elektrische UART-/Heizungstests bleiben Phase 13. Details stehen in
`PHASE11_DIAGNOSTICS.md`.
Die vorgesehenen Inhalte der Phasen 0–4 sind softwareseitig vorhanden; ihre
reale elektrische und End-to-End-Abnahme gehört weiterhin zu Phase 13.
Innerhalb von Phase 5 sind TimeService, Scheduler, der DS3231-Registeradapter,
die weiterhin verriegelte MicroPython-I2C-Hülle, die RTC-/TimeService-Brücke
und das synchrone Scheduler-/Controller-Gateway softwareseitig implementiert.
Die eingebettete `Europe/Zurich`-Regel einschließlich CET/CEST, Frühlingslücke
und einmaliger Herbst-Occurrence ist ebenfalls fertig und auf dem realen
MicroPython-Ziel geprüft. Damit ist der softwareseitige Umfang von Phase 5
abgeschlossen; die reale DS3231-Abnahme bleibt gemäß Plan Phase 13. Dazu kommen
das bestätigte DFR0654-Boardprofil,
ein bewusst auszulösender Board-Loopbacktest und ein strikt getrennter
RX-only-Rohdatenpfad für den späteren passiven Mitschnitt. USB-Erkennung,
Flashgröße, MicroPython und Safe-Boot sowie die UART2-Schleife wurden auf dem
realen DFR0654 geprüft. Zusätzlich bestand der hardwarefreie Phase-5-
TimeService-/Scheduler-Smoke auf dem realen ESP32 mit nach dem Aufwärmen
stabilen 128.608 Bytes freiem Heap. Zusätzlich bestand der Phase-5-V2-
Integrationstest für DS3231-Registerlogik, RTC-Brücke, Scheduler, Gateway und
die Zürich-DST-Regel auf demselben Board mit 4/4 Durchläufen und 97.296 Bytes
freiem Heap nach dem Messlauf. Der anschließende Phase-6-Konfigurations-Smoke
bestand ebenfalls 4/4 Durchläufe mit echten, isolierten A/B-Flashdateien,
Konfigurationsgeneration 2, Ledgergeneration 4 und 64.304 Bytes freiem Heap
nach dem Messlauf. Diese Phase-5-/6-Tests öffneten keine Hardware.

Für Phase 7 bestanden auf demselben ESP32 zusätzlich zwei getrennte
Abnahmen. Der Kapazitätstest schrieb und lud eine kanonische 7.888-Byte-
Konfiguration mit 32 Timern und acht bekannten WLANs über beide A/B-Slots;
der freie Heap blieb in allen sechs Messpunkten oberhalb von 32 KiB. Der
Funk-Smoke stellte anschließend den WPA2-Access-Point `Landy Heater` unter
`192.168.4.1` bereit, überwachte ihn während eines begrenzten STA-Versuchs und
bestätigte danach sowohl AP als auch STA als deaktiviert. Ein Hardware-Reset
bestätigte erneut den passiven Safe-Boot. Der anschließende echte Handytest
bestätigte genau einen WPA2-Client sowie DHCP-Adresse `192.168.4.2`, Subnetz
`255.255.255.0` und Gateway `192.168.4.1`; auch danach blieben Funk und Sperren
vor und nach Reset vollständig aus. Der neue
RX-only-Pfad ist vollständig unter CPython
getestet und sein unbeschalteter UART-Open/Poll/Deinit-Test auf dem realen Board
bestanden; die elektrische Hochohmigkeits-Gegenprobe und jede Prüfung an der
Heizung über den ESP32 stehen bewusst noch aus. Über den weiterhin
funktionierenden Node-RED-Controller wurden inzwischen eine reale
Heater-Statusantwort 22-mal byteidentisch sowie eine reale INIT-Antwort
aufgezeichnet und als verbindliche Regressionstests übernommen.

### Verbindlicher Phasenstatus

| Phase | Inhalt | Status |
|---|---|---|
| 0 | Finale Spezifikation | Baseline abgeschlossen; offene Reverse-Engineering-Punkte sind ausdrücklich dokumentiert |
| 1 | Autoterm Protocol Library | Softwareumfang abgeschlossen |
| 2 | UART Transport / Protocol Capture / Live Diagnostics | Transport-/Capture-Kern softwareseitig abgeschlossen; Browser-Live/Export bleibt Phase 11 und reale Heater-End-to-End-Abnahme Phase 13 |
| 3 | HeaterController / Requested-/Actual-State-Machine | Hardwarefreier Controller-Kern abgeschlossen; laufende Session-Updates sind in Phase 9 sicher ergänzt, produktiver Laufzeitloop und externe Temperatur bleiben offen |
| 4 | DS18B20 / Sensor Management / Failure Handling | Softwarekern und verriegelte MicroPython-Hülle abgeschlossen; reale Sensorabnahme folgt in Phase 13 |
| 5 | DS3231 + Scheduler / Multiple Timers / Runtime | Softwareumfang abgeschlossen: UTC-Zeitkern, feste Offsets, `Europe/Zurich` mit CET/CEST, Scheduler, verriegelte DS3231/I2C-Hülle, RTC-Brücke und Controller-Gateway; USB-only auf dem realen MicroPython-Ziel bestanden, reale RTC-Abnahme folgt in Phase 13 |
| 6 | Configuration Storage | Softwareumfang abgeschlossen: versionierte Konfiguration, getrenntes Scheduler-Sicherheitsledger, A/B-Flashspeicher, explizite Recovery und USB-only-Zieltest; produktive Laufzeitaktivierung bleibt später |
| 7 | Wi-Fi AP + Client + mDNS | Softwareumfang abgeschlossen: Schema v2, WPA2-AP, mehrere STA-Profile, begrenzte Reconnect-/Backoff-Logik, Direct-IP-Fallback, mDNS-Status, verriegelte MicroPython-Hülle sowie reale ESP32-Kapazitäts-, Funk- und Handy-DHCP-Tests; produktiver Auto-Start bleibt bewusst aus |
| **8** | **REST API** | **Zielabnahme bestanden: versionierte `/api/v1`, AP-only-Mutationen, generationsgebundene Konfiguration, begrenztes JSON/HTTP, Rate Limits und kooperativer Socketadapter; auf dem DFR0975-U genau ein Produktlistener auf Port 80, ein realer vollständiger HTTP-200-Status, alle zehn >=32-KiB-GC-Heap-Gates, unveränderte Produktspeicherung und vollständiger Cleanup bestätigt** |
| **9** | **Web UI** | **Abgeschlossen: eingebettete responsive Offline-UI, Deutsch/Englisch, Home/Timer/Einstellungen, ein gemeinsamer Port-80-Listener und sicher begrenztes Session-PATCH; reproduzierbarer DFR0975-U-A/B-Build, statische Artefaktprüfung, autorisierter App-Flash, vollständige Rückleseprüfung sowie realer 9-UI-/4-API-Handygate mit Heap- und Cleanup-Nachweis bestanden; kein Auto-Start** |
| **10** | **Setup Assistant** | **Funktional zielseitig abgenommen, formaler Every-route-Wire-Gate offen: 9-Schritt-UI, atomarer write-only WLAN-/Konfigurationsabschluss, explizite Passwort-/Open-Auswahl und schrittweise Browservalidierung implementiert; reale Station-DHCP- und AP-Passwort-Neuanmeldung sowie dauerhafte Timer-Erstellung, -Bearbeitung und -Löschung bestanden. Der dabei gefundene leere DELETE-Body wurde korrigiert; erneuter A/B-Build, Artefaktprüfung, autorisierter App-only-Flash, vollständige Rücklesung, Speicher-Reload und Cleanup bestanden.** |
| **11** | **Events / Diagnostics / Capture Export** | **Software- und Zielabnahme bestanden: 200er Ereignisring, begrenztes Live-Protokoll, benannte RAM-Captures, kleine Cursor-/Exportseiten, JSON/NDJSON-Export und lazy geladene zweisekündige Diagnose-UI; reproduzierbarer A/B-Build, Offline-Artefaktgate, autorisierter App-only-Flash, vollständige Rücklesung sowie realer Handyablauf mit Capture/Export und vollständigem Cleanup bestätigt. Hardwarezugriffe bleiben entkoppelt; elektrische Abnahme folgt in Phase 13.** |
| 12 | Hardening / Watchdog / Recovery / Failure Tests | Viele Failure-/OOM-/Wrap-Tests vorgezogen; Watchdog und Gesamtphase nicht abgeschlossen |
| 13 | Hardware Integration & Testing | Board-/Flash-/Safe-Boot-Vorarbeiten einschließlich DFR0975-U USB-only-Speicher-, manueller Recovery- und VFS/A-B-Storage-Gates sowie historische DFR0654-UART-Loopback-/RX-Vorarbeiten erledigt; Produktperipherie offen |

Vorgezogene Arbeiten aus Phase 11 oder 12 ändern die funktionale
Phasenzuordnung nicht. Eine Phase gilt außerdem nicht allein wegen vorhandener
Unit-Tests als hardwareseitig freigegeben.

- CRC-16 gemäß dem funktionierenden Node-RED-Flow
- bytegenaue Builder für INIT, STATUS, SHUTDOWN, START Power,
  START Temperature und externe Temperatur
- benannte Anwendungsmodes statt der alten Magic Strings `04`, `21`, `22`
- Parser für vollständige Frames
- inkrementelles Framing für Teilframes und mehrere Frames
- explizite Timeout-Resynchronisation
- non-blocking UART-Transport mit injizierbarer UART und Clock
- bestätigtes DFR0654-Boardprofil mit UART2 auf GPIO17/GPIO16
- realer DFR0654-Smoke-Test mit MicroPython 1.28.0 bestanden
- realer UART2-Loopback GPIO17→GPIO16 bytegenau bestanden
- im regulären Board-/Transportpfad gesperrte Autoterm-Protokollübertragung
- unveränderte RX-Chunks und TX-Frames für spätere Capture/Diagnose
- eigener RX-only-Transport ohne öffentliche Schreib-/Reinitialisierungs-API
- GPIO17-Neutralisierung vor und nach UART-Setup sowie bei jedem Cleanup
- begrenzter Rohdatenpuffer mit exakten Drop-Zählern
- manuelles, zeitbegrenztes NDJSON-Capture ausschließlich über USB-Ausgabe
- sichere Behandlung von `None`, Teil-Writes und I/O-Fehlern ohne Auto-Retry
- gesperrte RX-Verarbeitung bis `reset_rx()` bei wiederholt
  widersprüchlichem UART-/Framer-Zustand
- Auswertung der vier bekannten Statuspositionen
- reale Heater-RX-Vektoren für STATUS und INIT mit bestätigter Länge und CRC
- konkreter `AutotermProtocolService` zwischen UART-Transport und Controller
- geordnete RX-Pumpe mit erneutem Parsing ausschließlich aus den Rohbytes
- exakter Einmal-Sendevertrag ohne Service-Queue oder automatischen Retry
- standardmäßig zusätzlich im Service gesperrte Protokollübertragung
- sichtbarer RX-Fehlerstatus und ausschließlich explizite RX-Rücksetzung
- parameterlose Safe-Factory, die ausschließlich einen TX-gesperrten
  Board-Transport akzeptiert
- getrennte, nach außen nur als Kopie sichtbare Requested-/Actual-State-Modelle
- INIT-/STATUS-Synchronisierung nur über erwartete und fristgerechte Antworten
- erneute kanonische Frame-/CRC-Prüfung durch den injizierten Protokoll-Port
- frischer STATUS als Voraussetzung vor jeder Steuerentscheidung
- begrenzte START-/SHUTDOWN-Versuche ohne automatische Command-Storms
- explizite Verriegelung bei Replay, unerwartetem OFF und unklaren Zuständen
- wrap-sichere Session-Laufzeit mit konfigurierbarer Maximaldauer
- begrenzte Ereigniswarteschlange für Kommunikations- und Steuerfehler
- hardwarefreier TemperatureManager für `roof_tent`, `cabin` und `outside`
- semantische Sensorzuordnung über eindeutige ROM-IDs, vorbereitet für die
  spätere persistente Konfiguration
- streng geordnete, endliche Temperaturwerte im DS18B20-Bereich
- Health-Zustände `OK`, `STALE`, `FAILED` und `MISSING` mit wrap-sicherem Alter
- frischer, anwesender aktiver Sensor als Voraussetzung für Temperatur-START
- zeitlich begrenzte STALE-Weiternutzung nur für bestätigte aktive Sessions
- fail-closed OFF-Wunsch bei Ausfall des aktiven Regelsensors, ohne Direkt-TX
- pro Session eingefrorene Sensoridentität, Zuordnungsrevision und
  Fehlergeneration
- kooperativer DS18B20-Zyklus mit getrenntem Scan, Broadcast-Konvertierung,
  wrap-sicherer 750-ms-Frist und höchstens einem ROM-Read pro Aufruf
- atomare 0x28-ROM-Prüfung einschließlich Dallas-CRC und begrenzter
  Live-Diagnose auch für noch nicht zugeordnete Sensoren
- Schutz vor dem unbestätigten 85-°C-Einschaltwert, ohne ein späteres reales
  85-°C-Messergebnis zu verbieten
- eigene 1-Wire-Pinfreigabe, die auch bei eingetragener S3-Route GPIO4 mit
  `ONEWIRE_PIN_APPROVED=False` weiterhin sicher verriegelt ist
- lazy MicroPython-1.28-Hülle für `machine.Pin`, `onewire` und `ds18x20`,
  ohne Hardwarezugriff beim Import oder beim Konstruktor des Adapterkerns
- explizite Open-Drain-Freigabe mit High-Latch sowie retryfähiges
  `Pin.IN`-Cleanup bei allen Setup-Fehlern
- Prüfung der freigegebenen 1-Wire-Leitung vor und nach jeder Busaktion, damit
  ein fest auf Low hängender Bus nicht als künstliche `0 °C` akzeptiert wird
- hardwarefreier UTC-Zeitkern für 2000–2099 mit festen Offsets und einer
  versionierten, eingebetteten `Europe/Zurich`-Regel
- CET/CEST-Projektion mit letzter Sonntagsumschaltung im März/Oktober,
  übersprungener Frühlingslücke und strikt startfreier zweiter Herbststunde
- getrennte RTC-, NTP- und Browser-Korrekturquellen mit Revisions-Token für
  noch nicht in der RTC bestätigte Korrekturen
- kanonischer DS3231-UTC-Registeradapter mit strenger BCD-/Kalenderprüfung,
  verifiziertem 7-Byte-Schreiben und dauerhaftem Fail-closed-Transaktionsmarker
- getrennte UTC-Persistenzrevision und kurzlebige Korrektursperre: Erst nach
  exakter TimeService-Bestätigung darf der gestagte RTC-Wert vertrauenswürdig
  werden; re-entrant aufgerufene Korrekturen werden sichtbar abgewiesen
- eigene I2C-Pinfreigabe sowie lazy MicroPython-Hülle; bei eingetragenem
  I2C1 SDA10/SCL11 und `I2C_PINS_APPROVED=False` bleibt jeder Hardwarezugriff
  gesperrt
- kooperative RTC-/TimeService-Brücke mit genauer Revisionsbestätigung, ohne
  dass eine alte RTC-Antwort eine neuere NTP-/Browserkorrektur überschreibt
- maximal 32 atomar validierte Wochentimer mit Montag als Wochentag `0`
- konservative Timer-Fences nach Boot, Uhr-/Offsetkorrektur und Zeitsprung,
  ohne nachträgliches Aufholen verpasster Termine
- höchstens ein kurz gültiger, hardwarefreier `StartIntent` pro Scheduler-
  Schritt; weder Controller- noch Protokoll- oder UART-Aufruf
- zweistufige, kurzlebige Timerübergabe mit kanonischem read-only Auftrag und
  exakter Rückprüfung des tatsächlich gesetzten Requested-State
- sichere Zuordnung auch dann, wenn der Controller den Zustand bereits
  geändert hat und erst danach einen Fehler meldet
- Once-only-Latch, Konfliktunterdrückung, globaler Zeit-High-Water und
  getrennt erhaltener manueller Override-Kontext
- synchrones Scheduler-/Controller-Gateway ohne Yield oder UART-Aufruf;
  abgelaufene Intents werden vor Requested-State verworfen
- Controller-seitige Startfrist, gemeinsamer manueller Stop/Override und
  getrennte normale Timer-Session-Vervollständigung
- eigener, explizit auszulösender Phase-5-Software-Smoke-Test für MicroPython,
  der nur künstliche Zeit-/Timerdaten verwendet und keine Hardware öffnet
- realer DFR0654-Phase-5-V1-Smoke für TimeService/Scheduler mit acht
  vollständigen Timer-Lebenszyklen, exaktem Abschlusstoken und unverändertem
  Heap nach dem Aufwärmlauf
- realer DFR0654-Phase-5-V2-Integrationstest für DS3231-Registeradapter,
  RTC-Brücke, Scheduler, Gateway und Zürich-DST-Grenzen mit vier vollständigen
  Durchläufen, exaktem Abschlusstoken und erholtem Heap; dabei wurden weder
  GPIO noch reales I2C, UART oder 1-Wire geöffnet
- hardwarefreier `ConfigManager` mit strengem Schema für Setupstatus,
  Quick-Start-Defaults, Sensorzuordnung/-fristen, atomare Zeitregel und maximal
  32 Timer
- zwei getrennte, selbstvalidierende A/B-Generationsspeicher für statische
  Konfiguration und das kleine Scheduler-Sicherheitsledger, jeweils mit
  kanonischem JSON, CRC32, Längenprüfung und wiederholtem Commit-Footer
- dauerhafter Consume-Checkpoint vor jeder Timer-Autorisierung sowie
  persistierter manueller Override; Requested/Actual ON, Sessions, monotone
  Fristen, Live-Sensorwerte und Events werden niemals gespeichert
- fail-closed Bootauswahl bei Einzelslot, Generation-Gap, Split-Brain,
  beschädigtem neuestem Slot oder unbekannter Schema-/Zeitregelversion
- explizite Recovery mit gebundener A/B-Dateisicht und konservativem
  High-Water; eine Recovery kann den Wiederholungsschutz niemals absenken
- cold-boot `ConfiguredRuntime` mit ungültiger Uhr, disarmtem Scheduler und
  ohne Controller-, Protokoll- oder Hardwarekonstruktion
- realer DFR0654-Phase-6-Flash-Smoke mit 4/4 Durchläufen,
  Konfigurations-/Ledgergeneration 2/4, Schreibzählern 2/4, vollständigem
  Cleanup und erneut bestätigtem Safe-Boot
- explizite Schema-v1→v2-Migration mit sicher unprovisioniertem WLAN-Zustand,
  maximal acht bekannten STA-Profilen und niemals öffentlich ausgegebenen
  Passwörtern
- kooperativer `NetworkManager` mit dauerhaft priorisiertem WPA2-AP,
  begrenzter Profilrotation, wrap-sicherem Reconnect/Backoff und voneinander
  getrennter AP-/STA-Wahrheit
- direkte AP-IPv4-Adresse als verlässlicher Offline-Zugang; `heater.local`
  wird beim festgelegten MicroPython-Port erst nach einer STA-IP als bereit
  gemeldet und ist keine Startvoraussetzung
- AP-exklusives Captive DNS auf UDP 53 und feste Betriebssystem-Prüfrouten als
  best-effort Öffnungshilfe; die direkte AP-Adresse bleibt der garantierte
  Rückfallweg
- ein gemeinsamer TCP-/Port-80-Listener für AP und STA mit verbindungsbezogen
  ermitteltem Eingang; Stationszugriff bleibt bis zu einer späteren echten
  Authentisierung strikt lesend
- lazy MicroPython-WLAN-Hülle mit CH-Ländercode, `reconnects=0`, exklusivem
  Lease, exakter v1.28-`active(bool)`-Semantik, geheimnisfreien Fehlern und
  verifiziertem AP-/STA-Cleanup
- realer DFR0654-Phase-7-Kapazitätstest mit 7.888 kanonischen Bytes,
  32 Timern, acht WLAN-Profilen, dualem A/B-Commit, frischem Reload und
  vollständigem Testdatei-Cleanup
- realer DFR0654-Phase-7-Funk-Smoke mit WPA2-AP `Landy Heater`, direkter
  AP-Adresse `192.168.4.1`, begrenztem STA-Versuch und nach Reset weiterhin
  ausgeschaltetem AP-/STA-Funk
- realer, zeitbegrenzter Handy-Assoziationstest bestanden: genau ein Client,
  drei frische Beobachtungen, 30 Sekunden stabile Verbindung und am Handy
  bestätigte DHCP-Werte `192.168.4.2/24` mit Gateway `192.168.4.1`; Pass erst
  nach verifiziertem Funk-Cleanup
- versionierte, hardwarefreie REST-Anwendung unter `/api/v1` mit
  Security-Context, Status, Diagnose, Start, Quick Start, Stop, Settings und
  vollständigem Timer-CRUD
- schreibende REST-Aufrufe ausschließlich über den AP-Eingang und nur mit
  erlaubtem `Host`, exakt passender `Origin` und einem beim expliziten
  REST-Start neu erzeugten, flüchtigen 256-Bit-CSRF-Token; ein optionaler
  STA-Eingang bleibt read-only
- Konfigurationsänderungen und Startwünsche mit `If-Match`-/Generationszaun
  beziehungsweise zusätzlicher Requested-State-Revision; öffentliche
  Antworten enthalten keine WLAN-Passwörter oder internen Protokolldaten
- strikt begrenztes HTTP/1.1 und JSON ohne Chunking, Pipelining oder
  unbeschränkte Parserstrukturen; höchstens zwei Clients und pro kooperativem
  Server-`step()` höchstens eine begrenzte Accept-/Receive-/Send-Aktion
- feste Peer-Tabelle und Rate Limits: zehn Anfragen je zehn Sekunden, zwei
  Mutationen je Sekunde und fünf Sekunden Cooldown nach erfolgreicher
  Konfigurationsänderung; der körperlose Stop-Endpunkt umgeht diese Quoten
- jeder manuelle REST-Stop läuft über `ManualControlGateway` und das bestehende
  Scheduler-/Controller-Gateway; REST, HTTP und ihre Fehlerpfade besitzen
  keinen direkten UART-, Protokoll- oder Heater-Hardware-Zugriff
- expliziter Commit-vor-Antwort-Vertrag: Geht die Verbindung nach einer
  erfolgreichen Mutation verloren, wird kein ungesicherter Rollback erfunden;
  der Client liest den Zustand erneut und darf nur idempotent wiederholen
- realer DFR0654-Phase-8-USB-Smoke mit 4/4 Durchläufen, 12/12 abgeschlossenen
  Fake-Socket-Antworten, höchstens einer Socketaktion je Schritt, sicherem
  STOP, abgewiesenem START und 42.288 Bytes freiem Heap nach dem Messlauf
- AP-first/lazy-HTTP-Reihenfolge auf der Frozen-Kapazitätsfirmware mit dem
  exakten Token `PHASE8_PHONE_HTTP_SMOKE_PASS_V1` bestätigt: ein realer
  Handy-Peer, eine validierte Anfrage, zwei vollständig geschriebene Antworten
  und geordneter Socket-/Funk-Cleanup; der Lauf verwendete einen festen
  Read-only-Prüfhandler und ist keine volle `RestApplication`-Produktabnahme
- direkter WPA2-Porttest mit 114.304 Bytes nach der Factory, 105.984 Bytes nach
  AP-Start, genau einem assoziierten Client und bestätigtem Cleanup; DHCP und
  HTTP waren nicht Bestandteil dieses Teiltests
- historischer P1-Kapazitätsbefund vor dem Frozen-Build: Configuration +
  Storage + REST umfassten ungefähr 155,9 KiB dynamisch zu ladenden
  kompilierten Code; nach dem Frozen-Build ist dieser Befund erst durch eine
  vollständige gemeinsame Produktabnahme als gelöst oder fortbestehend zu
  bewerten
- Erhaltung unbekannter Bytes und Befehle
- 1046 bestandene CPython-Tests für die derzeit implementierten Softwarepfade

`boot.py` und `main.py` aktivieren absichtlich noch keine Hardware. Der neue
Controller, Scheduler und Gateway sind unter CPython miteinander integriert,
aber weder `main.py` noch ein ESP32-Laufzeitloop rufen diese Verbindung auf.
Sie kann daher auf dem ESP32 noch keinen Heizungsbefehl auslösen. Die
MicroPython-Hardwarehüllen für DS18B20 und DS3231 sind vorbereitet, werden aber
durch ihre unveränderten Pinfreigaben vor jeder Hardwareöffnung gestoppt. Der
WLAN-Pfad ist implementiert und separat auf dem Board geprüft, wird von
`main.py` jedoch weiterhin nicht automatisch gestartet. Die REST-Composition
ist ebenfalls absichtlich kalt: Konstruktion öffnet weder Funk noch Socket,
und nur ein expliziter Aufruf erzeugt das flüchtige CSRF-Token. Auch ein
produktiver Composition-Root wird erst nach einer bestandenen gemeinsamen
Heap-, AP-, HTTP- und Cleanup-Abnahme freigegeben. Reale 1-Wire-/I2C-Abnahme
und die Weboberfläche folgen gemäß `ARCHITECTURE.md` erst danach.

## In VS Code öffnen

Den Ordner `landy-heater` über **File → Open Folder…** öffnen. Alternativ im
Terminal aus dem übergeordneten Verzeichnis:

```bash
code landy-heater
```

## Tests ausführen

Es werden keine externen Python-Pakete benötigt. Ab Python 3 im Projektordner:

```bash
python3 -m unittest discover -s tests -v
```

## Umfang des Controller-Kerns

`app/heater_controller.py` arbeitet ausschließlich mit einem injizierten
Protokoll-Port. Dieser Port muss eingehende Frames aus ihren unveränderten
Rohbytes erneut kanonisch parsen und validieren; frei zusammengesetzte
Status-Dictionaries sind keine Vertrauensquelle. Eine Steuerentscheidung
benötigt einen höchstens eine Heartbeat-Periode alten, konkret angeforderten
STATUS.

Die aktuelle konservative Standardpolitik lautet:

- Heartbeat 1 s, Antwortfrist 10 s und Control-Settle 200 ms
- höchstens zwei automatisch bestätigungsabhängige Versuche je Request-
  Generation
- Standard-Maximallaufzeit 120 min, im Konstruktor verkürzbar
- drei wiederholt auftretende ungültige Frames führen zu Communication ERROR
- bleibt ein angeforderter Stop 5 min in STARTING hängen, entsteht ein
  sichtbarer Policy-Fault; ein unbestätigter SHUTDOWN wird nicht geraten
- TEMP_MONITORING mit Stop-Wunsch wird ebenfalls sichtbar verriegelt, bis ein
  bestätigter shutdown-fähiger Zustand vorliegt
- unerwartetes OFF oder ein Replay nach abgeschlossenem Shutdown startet keine
  neue automatische Befehlsserie

Ein ausgeschöpfter Control-Fault bleibt verriegelt. Nur der ausdrücklich
aufgerufene Controller-Einstieg `retry_control_fault(now_ms)` eröffnet nach
einem neuen gültigen STATUS eine weitere begrenzte Versuchsgeneration. Das ist
keine Transport-Wiederholung und noch nicht mit einer Benutzeroberfläche
verbunden.

## Umfang des Sensor-/Health-Kerns

`app/temperature_manager.py` ist ein reiner, hardwarefreier Zustandskern. Er
öffnet keinen 1-Wire-Bus und importiert weder `machine`, `onewire`, `ds18x20`
noch das Heizungsprotokoll. Der ebenfalls hardwarefreie Adapterkern liefert
ihm lediglich Discovery-Ergebnisse, gültige Messwerte und Lesefehler.
Ungültige Werte ersetzen niemals den letzten gültigen Messwert und werden
insbesondere nicht durch künstliche `0 °C` ersetzt.

Die deterministische Standardpolitik lautet:

- Alter unter 30 s: `OK`
- Alter ab 30 s und unter 5 min: `STALE`
- Alter ab exakt 5 min: `FAILED`
- zugewiesen, aber noch nie gültig gelesen: zunächst `MISSING`, nach 5 min
  ohne gültigen Wert `FAILED`
- Wertebereich einschließlich Grenzwerte: `-55 … 125 °C`; Bool, NaN,
  Unendlich und Werte außerhalb des Bereichs werden abgewiesen
- spätere oder gleichzeitige Daten dürfen verarbeitet werden; zeitlich ältere
  Messungen, Fehler und Discovery-Ergebnisse verändern den Zustand nicht

Für Dachzelt- und Kabinentemperaturmodus wählt der Controller ausschließlich
den jeweils passenden Regelsensor. Der Außensensor ist in diesem Meilenstein
nur Diagnosequelle; der Power-Modus hat keinen Regelsensor. Ein neuer
Temperatur-START benötigt einen frischen, anwesenden Sensor. `STALE` darf nur
eine bereits bestätigte aktive Session vorübergehend fortführen. `FAILED`,
`MISSING`, eine geänderte Zuordnung oder eine zwischen zwei Controllerzyklen
überschrittene Fehlerfrist verriegeln den OFF-Wunsch. Die bestehende
Zustandsmaschine entscheidet danach, wann ein bestätigter SHUTDOWN zulässig
ist; der TemperatureManager sendet selbst nie ein Kommando. Eine Erholung
setzt den Wunsch nicht automatisch wieder auf EIN.

Negative Temperaturen bleiben als reale Sensordaten erhalten. Die
Übertragung externer Temperaturwerte an die Heizung ist bewusst noch nicht
angeschlossen, weil die negative Wire-Codierung und das Verhalten in
`TEMP_MONITORING` nicht bestätigt sind. Es wird weder geklemmt noch ein
Ersatzwert gesendet.

## Umfang des DS18B20-Adapters und seiner MicroPython-Hülle

`adapters/ds18b20_adapter.py` ist eine kooperative, vollständig injizierbare
Zustandsmaschine. Sie kennt nur einen schmalen Bus-Port mit `scan()`,
`start_conversion()`, `read_celsius(rom)` und `deinit()`. Das Modul importiert
keine Board- oder MicroPython-Hardwarebibliothek und führt beim Import oder im
Konstruktor keine Busaktion aus.

Ein Aufruf von `step(now_ms)` führt höchstens eine synchrone Busaktion aus:
zuerst einen atomaren Scan, in einem späteren Aufruf genau eine busweite
Konvertierung und erst nach mindestens 750 ms höchstens einen Sensor-Read. Drei
Sensoren werden deshalb über mehrere kurze Mainloop-Durchläufe gelesen. Es gibt
weder ein blockierendes Sleep noch eine Aufholschleife nach verspätetem Polling.
Alle Fristen verwenden die wrap-sichere MicroPython-Tick-Semantik. Wird ein
Read verspätet ausgeführt, bleibt sein Messzeitpunkt die Konvertierungsfrist;
ein alter Scratchpadwert wird dadurch nicht fälschlich wieder frisch.

Der Adapter akzeptiert ausschließlich acht Byte lange DS18B20-ROMs der Familie
`0x28` mit gültigem Dallas-CRC. Ein fehlerhafter, doppelter oder übergroßer Scan
wird vollständig abgewiesen; ein echter leerer Scan bleibt davon unterscheidbar.
Der offizielle MicroPython-Treiber kann zusätzlich Familien `0x10` und `0x22`
melden. Die Hülle verbirgt solche Geräte nicht: In diesem ausdrücklich als
reiner DS18B20-Bus qualifizierten Projekt führt eine fremde Familie zu einem
sichtbaren, atomar verworfenen Scan, statt unbemerkt die Hardwarekonfiguration
zu verändern.
Read-/CRC-/Busfehler werden als Fehler an den `TemperatureManager` gemeldet,
ohne jemals künstliche `0 °C` zu erzeugen. Ein erstes `85 °C` nach Adapterstart,
bestätigtem Wiedererscheinen oder einem Bus-/Lesefehler wird konservativ als
möglicher DS18B20-Einschaltwert behandelt. Nach einem anderen gültigen
Messwert darf ein reales `85 °C` normal gespeichert werden.

Ein begrenzter, nur als Kopie ausgegebener `devices`-Snapshot hält auch die
Live-Werte noch nicht zugeordneter Sensoren für einen späteren Setup-Assistenten
bereit. Die sicherheitsrelevante Rollenwahrheit bleibt ausschließlich im
`TemperatureManager`; der Adapter ordnet keine Sensorrolle selbst zu und kennt
weder Controller noch UART oder Heizungsbefehle.

`hardware/micropython_ds18b20.py` bindet diesen Port exakt an die in
MicroPython 1.28 enthaltenen Klassen `OneWire` und `DS18X20`. Nur erwartete
Leitungsfehler, `OSError` und der pinngenaue Treiberfall
`Exception("CRC error")` werden als recoverable Busfehler übersetzt.
Speicherfehler, Abbruchsignale und unbekannte Programmfehler bleiben sichtbar
und verriegeln den Adapter. Der GPIO wird vor `OneWire` als Open Drain mit
High-Latch freigegeben und beim Cleanup als Eingang ohne Pull zurückgelassen.
Vor und nach Scan, Konvertierung und Read muss die freigegebene Leitung High
sein; dadurch kann der bekannte All-zero/Stuck-low-Fall des Originaltreibers
nicht als reale `0.0 °C` in den Zustandskern gelangen.

Eine reale Hardware-Factory ist damit vorhanden, ihre öffentliche Signatur
enthält aber weder Pin- noch Freigabe-Override. Sie prüft zuerst
`board_config.require_onewire_configuration()` und lädt erst danach
`machine`, `onewire` und `ds18x20`. `ONEWIRE_PIN` bleibt `None` und
`ONEWIRE_PIN_APPROVED` bleibt `False`; `main.py` ruft die Factory ebenfalls
nicht auf. Somit öffnet das ausgelieferte System weiterhin keinen 1-Wire-Pin.

## Umfang des TimeService- und Scheduler-Kerns

`services/time_service.py` hält eine validierte UTC-Zeitbasis zwischen 2000
und 2099 und führt sie ausschließlich mit wrap-sicheren monotonen Ticks fort.
Das Modul liest weder RTC noch Netzwerk oder Browser. Ein späterer Adapter
liefert explizite RTC-, NTP- oder Browser-Zeitsamples. NTP-/Browser-Korrekturen
gelten erst dann als RTC-verankert, wenn genau ihre Revision bestätigt wurde;
eine verspätete Bestätigung kann keine neuere Korrektur freigeben. Dafür führt
der Dienst eine von Zeitzonenänderungen unabhängige UTC-Persistenzrevision.
Während der abschließenden RTC-Vertrauensfreigabe sperrt er re-entrant
aufgerufene Zeit-, Zeitzonen- und Invalidierungsänderungen kurz und meldet die
Uhr zugleich als Holdover; ein solcher Aufrufer muss seine Korrektur danach
erneut anbieten. Ein
periodischer, zur laufenden Zeit passender RTC-Refresh verändert die
Clock-Revision dagegen nicht. Echte Korrekturen tun dies immer.

Der Zeitkern unterstützt zwei explizite Regeln: einen festen UTC-Offset und
die eingebettete, versionierte Fahrzeugzone `Europe/Zurich`. Der kanonische
Zürich-Name darf nur zusammen mit dieser Regel und dem CET-Standardoffset
`UTC+60` verwendet werden; im Sommer liefert der Snapshot den effektiven
Offset `UTC+120` zusätzlich zum getrennten Feld
`standard_utc_offset_minutes=60`. Es gibt bewusst keine allgemeine IANA-
Datenbank im ESP32.

Für `Europe/Zurich` gilt von 2000 bis 2099 die EU-Regel: Beginn der Sommerzeit
am letzten Sonntag im März um 01:00 UTC, Ende am letzten Sonntag im Oktober um
01:00 UTC. Die lokale Frühlingsstunde 02:00–02:59 existiert nicht und wird nie
nachgeholt. In der doppelten Herbststunde ist nur die erste Ausprägung
`fold=0` startberechtigt; `fold=1` bleibt für Anzeige und Diagnose gültig,
erzeugt aber auch nach einem Neustart niemals einen Timerstart. Die exakte
Offset-Übergangsminute wird konservativ als Scheduler-Fence übernommen. Ein
natürlicher Saisonwechsel ändert keine UTC-, Uhr- oder Zeitzonenrevision; der
Scheduler erkennt ihn anhand des neu projizierten effektiven Offsets und
`is_dst`. Explizite Regel-/Offsetänderungen erhöhen dagegen Clock- und
Timezone-Revision. Browser-/API-Korrekturen müssen UTC liefern; eine spätere
lokale Eingabe muss Lücken ablehnen und bei doppelter Zeit den Fold explizit
angeben.

`app/scheduler.py` verwaltet höchstens 32 atomar validierte Wochentimer.
Wochentage verwenden `0=Montag` bis `6=Sonntag`; Startzeiten haben exakt das
Format `HH:MM`. Der Scheduler ist beim Start disarmed. Nach `arm()` stellt die
erste gültige Zeit nur eine Baseline her. Uhrkorrekturen, Offset-/Quellenwechsel,
Rücksprünge, größere Vorwärtssprünge und Timeränderungen werden ebenfalls nur
als Fence übernommen. Verpasste oder gerade offene Termine werden nie
nachträglich gestartet.

Bei einer natürlichen künftigen Minutenkante liefert der Kern höchstens einen
read-only `StartIntent` mit Modus, Ziel beziehungsweise Power, Laufzeit,
Occurrence-ID und einer kurzen monotonen Ablaufgrenze. Zwei gleichzeitig
fällige Timer werden beide als Konflikt verbraucht und erzeugen kein Intent.
Ist die Steuerung durch einen manuellen oder laufenden Vorgang nicht
verfügbar, wird die Occurrence ebenfalls ohne spätere Wiederholung
unterdrückt. Unmittelbar vor einer späteren Übergabe muss
`authorize_intent()` Timerrevision, Uhrvertrauen, Control-Verfügbarkeit,
Ablaufgrenze und die aktuelle Vertrauensgeneration erneut erfolgreich prüfen.
Es liefert dafür einen frisch aus der kanonischen Timerdefinition aufgebauten,
read-only `AuthorizedStartIntent`. Nach dem synchronen Requested-State-Aufruf
gleicht `complete_intent()` dessen tatsächlichen Snapshot byte- und typgenau
mit diesem Auftrag ab. Ein sauber abgewiesener Aufruf erzeugt keinen aktiven
Timer; wurde Requested-State trotz eines nachfolgenden Fehlers bereits gesetzt,
bleibt die Occurrence für einen manuellen Stop zugeordnet und der Scheduler
verriegelt sich sichtbar. Eine akzeptierte Occurrence bleibt separat erhalten,
damit ein manueller Stop sie auch nach einer Timeränderung eindeutig
überschreiben kann.

Der Scheduler selbst ruft weiterhin weder `HeaterController` noch Protokoll
oder UART auf. `app/scheduler_controller_gateway.py` übernimmt ausschließlich
die synchrone Anwendung eines frisch autorisierten Intents auf Requested-State.
Zwischen Autorisierung, Controller-Aufruf, Wahrheitsprüfung und Abschluss gibt
es kein Yield. Der Controller speichert die monotone Ablaufgrenze zusätzlich
und verwirft einen noch nicht gesendeten Timerstart nach Fristablauf. Ein
manueller Stop setzt zuerst Requested OFF und markiert erst danach genau die
aktive Timer-Occurrence als überschrieben; ein normal beendeter Lauf wird
separat abgeschlossen.

`adapters/ds3231_adapter.py` behandelt den DS3231 als persistente **UTC**-Uhr.
Lokale Zeit, Standardoffset und saisonal effektiver Offset bleiben
ausschließlich im `TimeService`. Der Adapter prüft BCD, Kalender,
12-/24-Stunden-Leseformat,
Oszillatorstatus und den vollständigen kanonischen 7-Byte-Readback. Vor einem
Zeitregister-Schreibvorgang setzt er einen persistenten EOSC-
Transaktionsmarker. Nach vollständigem kanonischem Readback bleibt dieser
Marker zunächst gesetzt. Erst wenn die RTC-Brücke genau die zugehörige
UTC-Persistenzrevision im TimeService bestätigt und gegen Python-Rückrufe
verriegelt hat, wird der Marker entfernt und das OSF-Vertrauensflag kontrolliert
gelöscht. Ein unterbrochener oder von einer neueren Revision überholter
Schreibvorgang bleibt dadurch auch nach Neustart fail-closed sichtbar.

`services/rtc_time_bridge.py` liest oder schreibt pro fälligem Schritt höchstens
eine RTC-Transaktion. NTP-/Browserkorrekturen werden nur mit der exakt
zugehörigen UTC-Persistenzrevision bestätigt. Eine während eines I2C-Aufrufs neuere
Korrektur kann von einer alten RTC-Antwort weder überschrieben noch bestätigt
werden. Persistente Exactly-once-/Override-Daten werden inzwischen durch den
Phase-6-Konfigurationspfad bereitgestellt; der aktive Mainloop und reale
I2C-Test bleiben spätere Integrationsschritte.

## Umfang des Configuration-Storage-Kerns

`services/config_manager.py` ist die einzige Anwendungsschnittstelle zur
persistenten Konfiguration. Das aktuelle Schema v2 validiert das vollständige
Dokument vor jeder Änderung und speichert ausschließlich Setupstatus,
Maximallaufzeit/Quick-Start-Defaults, drei Sensorzuordnungen samt Healthfristen,
die atomare Zeitzonenregel, höchstens 32 Timer sowie die streng begrenzte
Netzwerkkonfiguration. Die Migration von v1 nach v2 validiert zuerst das
vollständige alte Dokument und erzeugt absichtlich keinen voreingestellten oder
offenen Access Point: Das neue AP-Passwort bleibt bis zur lokalen
Provisionierung `null`. Hardware-Pins,
Freigabeschalter, Requested/Actual-State, aktive Sessions, monotone Zeitwerte,
RTC-Vertrauen, Live-Sensorwerte und allgemeine Events gehören ausdrücklich
nicht in diese Datei. Ein kalter Aufbau bleibt deshalb Requested OFF, mit
ungültiger Uhr und disarmtem Scheduler.

`adapters/config_file_store.py` verwaltet je Domäne zwei gleichberechtigte
A/B-Slots und eine nie bootfähige Temp-Datei. Jeder Slot enthält eine
Generation, begrenztes kanonisches JSON mit sortierten Schlüsseln, CRC32,
Länge und einen wiederholten Footer. Ein Commit schreibt den inaktiven Slot,
synchronisiert und liest ihn vollständig zurück, bevor die neue Generation
als bestätigt gilt. Ein einzelner Slot, eine Generationslücke, widersprüchliche
gleiche Generationen, ein beschädigter neuerer Slot oder unklare Haltbarkeit
öffnen den Timer-Gate niemals. Die Erstprovisionierung schreibt beide Domänen
dual; das Scheduler-Ledger muss vor einer startfähigen Konfiguration
vertrauenswürdig sein.

Die kanonische Anwendungskonfiguration ist auf 8 KiB begrenzt; der statische
Speicherumschlag lässt einschließlich Header, CRC und Footer höchstens 12 KiB
zu. Der MicroPython-Pfad schreibt und liest in 256-Byte-Segmenten und prüft
kanonisches JSON beim Laden ohne eine zweite vollständige Textkopie. Dadurch
bestand auf dem realen ESP32 auch die maximale praktische Phase-7-Form mit
32 Timern, acht WLAN-Profilen und 7.888 kanonischen Bytes. Die A/B-Dateien
wurden anschließend durch einen frisch konstruierten Manager erneut geladen
und danach vollständig entfernt.

Der Scheduler exportiert nur einen begrenzten Wiederholungsschutz: globales
lokales High-Water und höchstens einen terminalen Consume-/Override-Latch je
Timer. Das synchrone Gateway bestätigt diesen Ledgerstand dauerhaft und liest
ihn zurück, bevor ein Intent autorisiert oder Requested ON gesetzt werden
darf. Schreib- oder Rücklesefehler unterdrücken den Start. Eine explizite
Recovery bindet ihre Vorprüfung an exakt dieselbe A/B-Sicht, bewahrt das
höchste noch semantisch gültige High-Water und erzeugt niemals automatisch
einen Startauftrag.

Der USB-only-Phase-6-Smoke führte diesen Pfad am 11. August 2026 viermal auf
dem realen ESP32 aus. Er nutzte isolierte Testdateien, prüfte echte
Flash-Schreib-/Rücklesevorgänge, Reboot-Restore, No-op, beschädigte neueste
Slots, dauerhaften Consume/Override, UTF-8 sowie MicroPython-Ticks und Heap.
Der Lauf endete mit `PHASE6_USB_CONFIG_SMOKE_PASS_V1`; alle sechs Testdateien
waren danach entfernt und der passive Safe-Boot blieb unverändert. Die
vollständige Evidenz steht in
`captures/2026-08-11-phase6-config-esp32-smoke.md`.

## Umfang des Netzwerk-Kerns

`app/network_manager.py` ist ein hardwarefreier, kooperativer Zustandsautomat.
Er hält den WPA2-Access-Point nach erfolgreicher lokaler Provisionierung
unabhängig vom Clientpfad verfügbar, versucht maximal acht bekannte WLANs in
begrenzter Rotation und verwendet wrap-sichere Timeouts und Backoff. Jeder
`step()` führt höchstens eine Portaktion aus; fällige AP-Kontrolle und
STA-Arbeit werden fair abgewechselt. Ein verlorenes oder fehlerhaftes
Clientnetz darf den AP weder abschalten noch dessen Wiederherstellung
verhungern lassen. Status-Snapshots trennen AP- und STA-Wahrheit, räumen alte
IP-/mDNS-/Internetangaben bei unklaren Treiberantworten sofort und enthalten
niemals Passwörter.

`hardware/micropython_wifi.py` kapselt die ESP32-Singleton-Interfaces hinter
einer exklusiven Lease und wird erst durch die explizite Board-Factory
geöffnet. Der aktuelle ausgelieferte Boardzustand bleibt mit
`WIFI_RADIO_APPROVED=False` verriegelt. Der manuelle Phase-7-Smoke darf diese
Freigabe nur RAM-lokal und nur für seinen begrenzten Lauf setzen. Die Hülle
konfiguriert den Ländercode `CH`, einen WPA2-AP mit höchstens vier Clients und
deaktiviert die unbegrenzten Treiber-Reconnects. Für MicroPython 1.28 ist die
auf dem realen Board bestätigte Reihenfolge bindend: AP-Konfiguration vor
AP-Aktivierung, beim STA dagegen Aktivierung und Readback vor
`config(reconnects=0)`. Jeder Abschluss prüft beide Funkinterfaces erneut als
inaktiv; Credential-tragende Treiberfehler werden ohne Geheimnistext
weitergegeben.

Die direkte AP-Adresse `192.168.4.1` ist der verlässliche Offline-Zugang.
MicroPython 1.28 initialisiert seinen eingebauten mDNS-Responder auf ESP32 erst
nach einer erfolgreichen STA-IP; deshalb meldet der Kern `heater.local` im
reinen AP-Betrieb bewusst nicht als bereit. mDNS ist Komfort, niemals
Startbedingung. Der reale Funk-Smoke bestätigte AP-vor-STA, Direct-IP,
AP-Überwachung während eines begrenzten STA-Versuchs, vollständiges Cleanup
und den unveränderten Safe-Boot. Die Evidenz steht in
`captures/2026-08-11-phase7-wifi-radio-esp32-smoke.md`; der getrennte
Kapazitätslauf steht in
`captures/2026-08-11-phase7-config-capacity-esp32-smoke.md`. Die echte
Handy-Assoziation einschließlich DHCP-Werten ist in
`captures/2026-08-11-phase7-phone-ap-esp32-smoke.md` festgehalten.

## Umfang der REST API

`app/rest_application.py` stellt die hardwarefreie Version `/api/v1` bereit:

- `GET /api/v1/security-context`
- `GET /api/v1/status` und `GET /api/v1/diagnostics`
- `POST /api/v1/heater/start`, `POST /api/v1/heater/quick-start` und
  `POST /api/v1/heater/stop`
- `GET`/`PATCH /api/v1/settings`
- `GET`/`POST /api/v1/timers` sowie `GET`/`PUT`/`DELETE` auf einer
  Timer-Ressource

Der Security-Context liefert das beim expliziten Runtime-Start aus 32
Zufallsbytes erzeugte, nur bis zum `deinit()` gültige CSRF-Token. Jeder
schreibende Aufruf benötigt einen erlaubten `Host`, die exakt dazu passende
HTTP-`Origin` und `X-Landy-CSRF`; die Security-Policy erlaubt Mutationen nur
am AP-Eingang. Ein separat zusammengesetzter STA-Eingang bleibt read-only.
Konfigurationsänderungen sowie Start/Quick Start benötigen zusätzlich einen
passenden `If-Match`-ETag der aktuellen Konfigurationsgeneration. Startwünsche
sind außerdem an die gelesene Requested-State-Revision gebunden. Der Stop ist
absichtlich körperlos und bleibt als sicherer OFF-Pfad unabhängig von
Generation und Rate-Limit verfügbar, verliert aber nie die Host-, Origin- oder
CSRF-Prüfung.

`ConfigurationAPIGateway` baut vor jeder Änderung einen vollständigen
Kandidaten und bestätigt die dauerhaft gespeicherte Generation. Secrets werden
nicht in öffentliche DTOs übernommen. `ManualControlGateway` leitet Start,
Quick Start und Stop ausschließlich über die bestehenden Anwendungsmodelle;
insbesondere läuft Stop durch das Scheduler-/Controller-Gateway. Kein REST-
oder HTTP-Baustein importiert oder ruft UART, Autoterm-Protokoll oder
Heater-Hardware direkt auf. Session-Änderungen, Events, Live-Protokolllog und
Exporte sind bewusst nicht Teil von Phase 8 und bleiben späteren Phasen
zugeordnet.

`services/strict_json.py` und `services/http_protocol.py` begrenzen Bytes,
Zeilen, Header, Knoten, Verschachtelung und Antwortgröße. Der Parser akzeptiert
pro Verbindung genau einen vollständigen HTTP/1.1-Request mit `Content-Length`;
Chunking, Pipelining, Upgrades und Method-Override sind ausgeschlossen. Der
kooperative Socketadapter hält höchstens zwei Clients und führt je `step()`
höchstens eine Accept-, Receive- oder Send-Aktion aus; Receive und Send sind
jeweils auf 256 Bytes begrenzt.
Pro IPv4-Peer gelten zehn Requests je zehn Sekunden, zwei Mutationen je Sekunde
und nach einem dauerhaften Config-Commit fünf Sekunden Schreib-Cooldown. Stop
umgeht die Quoten.

Eine Mutation kann bereits sicher bestätigt und gespeichert sein, bevor das
letzte Antwortbyte das Handy erreicht. Geht die Verbindung danach verloren,
erfindet der Socketlayer weder Erfolgsmeldung noch Rollback. Der Client muss
Status beziehungsweise Ressource erneut lesen und nur auf Grundlage dieser
Wahrheit idempotent wiederholen. Die Softwarepfade sind unter CPython geprüft;
der getrennte USB-only-Phase-8-Smoke bestand auf dem DFR0654 mit dem exakten
Token `PHASE8_USB_REST_SMOKE_PASS_V1`. Dieser Lauf importierte kein WLAN und
verwendete Fake-Sockets; seine unveränderte Komponenten-Evidenz steht in
`captures/2026-08-11-phase8-rest-esp32-smoke.md`.

Für die reale Kombination gilt AP-first mit lazy HTTP: Erst den AP öffnen und
bestätigen, danach Parser/JSON/Socketserver laden und binden. Auf der
Frozen-Kapazitätsfirmware sah der kleine Handy-Runner damit einen echten Peer
unter `192.168.4.2`, validierte eine Anfrage, schrieb zwei Antworten vollständig
und emittierte `PHASE8_PHONE_HTTP_SMOKE_PASS_V1`. Er verwendete jedoch einen
festen Read-only-Radio-Check statt `RestApplication`, ConfigManager und
Storage. Er war deshalb noch keine vollständige P1-Produktzielabnahme. Der
enge historische Pass steht in
`captures/2026-08-11-phase8-frozen-phone-http-esp32-smoke.md`; die früheren
eager-/ready-only-Nicht-Abnahmen bleiben unverändert in
`captures/2026-08-11-phase8-wifi-http-capacity-blocked.md`. `boot.py` und
`main.py` starten weiterhin weder REST noch Socket oder Funk automatisch.

Der aktuelle Full-Product-Runner verwendet bewusst nur noch **einen**
HTTP-Listener auf `192.168.4.1:80`. Seine erste Stufe bestätigt ausschließlich
AP-Adresse und Clientassoziation und öffnet keinen Socket. Erst danach werden
die vollständige Produktclosure und der Produktionsadapter geladen; der eine
reale `GET /api/v1/status` ist zugleich IPv4-/TCP-, Routing-,
RestApplication-, Wire- und Close-Beweis. Auf dem DFR0654 blieb dieser Aufbau
vor `listen` mit 32.880 Bytes nur 112 Bytes über dem 32-KiB-Gate und fiel am
folgenden Pflicht-Checkpoint vor READY darunter. Der nach der Migration genau
einmal ausgeführte DFR0975-U-Lauf bestand dagegen die vollständige
Ein-Listener-Produktzielabnahme: ein echter Statusrequest, komplette
HTTP-200-JSON-Antwort, alle zehn GC-Heap-Gates und geordnetes Cleanup. Phase 8
ist damit bestanden. Die neue Evidenz steht in
`captures/2026-09-01-dfr0975u-phase8-full-rest-gate.md`; die historische
DFR0654-Grenze bleibt in
`captures/2026-08-30-phase8-single-listener-project-state.md`.

## Umfang des Protokoll-Service

`protocol/autoterm_service.py` ist der konkrete Adapter zwischen dem rohen
UART-Transport und `HeaterController`. `poll_inbound(now_ms)` übernimmt
vollständige Raw-Frames in ihrer Reihenfolge und parst sie ohne globales
Command- oder CRC-Filtering. `validate_inbound_frame()` verwirft sämtliche
mitgelieferten Metadaten und baut sie ausschließlich aus den unveränderten
Rohbytes erneut auf. Damit können ein gefälschtes `crc_valid=True` oder frei
zusammengesetzte Statuswerte keine Steuerentscheidung autorisieren. Pro Poll
werden höchstens 80 abgegrenzte Frames akzeptiert; größere, fehlerhafte
Transport-Batches werden vor der semantischen Vervielfachung abgewiesen.

Jede der vier Controller-Anforderungen INIT, STATUS, START und SHUTDOWN baut
genau einen kanonischen Frame und ruft höchstens einmal den injizierten
Transport auf. Nur die exakte ganzzahlige Bestätigung der vollständigen
Frame-Länge gilt als Service-Erfolg. `None`, `False`, Bool-Werte, Teil-Writes,
Überlängen und Transportfehler werden fail-closed nach oben gemeldet; der
Service wiederholt nie selbständig. Eine generische Raw-Sendefunktion, ein
TX-Toggle oder ein öffentlicher Transportzugriff existieren nicht.

Auch die öffentliche Service-Konstruktion ist standardmäßig TX-gesperrt und
besitzt kein öffentliches Freigabeargument. Der aktuelle sichere
Composition-Pfad erteilt keine interne Sendeberechtigung – selbst ein
fehlerhaft als TX-fähig gemeldeter injizierter Transport kann über diesen
Service daher keinen Frame schreiben. `transport_status()` liefert nur eine
abgelöste Diagnosekopie. Ein verriegelter RX-Pfad wird ausschließlich durch
den expliziten Aufruf `reset_inbound()` zurückgesetzt und anschließend erneut
auf `rx_faulted=False` geprüft.

`app/composition.py` enthält für den aktuellen Meilenstein ausschließlich die
parameterlose Factory `open_tx_locked_protocol_service()`. Sie verweigert den
Start, sobald `UART_PROTOCOL_TX_ENABLED` nicht exakt `False` ist, und prüft
zusätzlich den erzeugten Transport. Importieren öffnet keine Hardware; erst
ein ausdrücklicher Factory-Aufruf würde UART2 öffnen. `main.py` führt diesen
Aufruf nicht aus. Scheitert die Prüfung nach dem Öffnen, wird der Transport in
einem begrenzten, retryfähigen Cleanup wieder geschlossen; ein bleibender
Cleanup-Fehler wird sichtbar gemeldet.

## Verifizierter Boardstatus

Am 9. August 2026 wurde das bestätigte DFR0654 ausschließlich über USB
geprüft:

- ESP32-D0WD-V3 Revision 3.0 und 4 MB Flash erkannt
- vollständiges 4-MB-Backup vor der Installation erstellt und geprüft
- offizielles `ESP32_GENERIC` MicroPython 1.28.0 bei `0x1000` geschrieben
- Flashinhalt anschließend erfolgreich gegen das Firmware-Image verifiziert
- alle damaligen 14 Laufzeitdateien per SHA-256 auf dem Board abgeglichen
- DFR0654-Profil `(UART2, TX=17, RX=16)` und deaktiviertes Protokoll-TX geprüft
- Safe-Boot ohne UART-Initialisierung und ohne Heizungsbefehl bestanden
- manueller UART2-Loopback mit exakt 16 Bytes ohne Zusatzbytes bestanden;
  Jumper anschließend entfernt und Safe-Boot erneut geprüft
- RX-only-Factory auf dem unbeschalteten Board geöffnet, gepollt und sauber
  deaktiviert; GPIO17 anschließend erneut explizit als Eingang gesetzt

Der frei schwebende, unbeschaltete GPIO16 lieferte bei diesen RX-only-Tests
einzelne Störbytes (`00` beziehungsweise `04`). Das ist erwartbar, wurde
korrekt roh erhalten und ist kein Heizungsdatenpunkt. Die Tests meldeten keine
Fehler oder Drops; es wurde kein UART-Schreibvorgang ausgelöst.

Während dieser Prüfungen waren Heizung, Pegelwandler, 12 V und GPIO-Jumper
nicht angeschlossen.

Die später hinzugekommenen Controller-, Sensor-, Adapter-, Hardwarehüllen-,
Composition- und Service-Dateien waren bei diesem historischen Boardabgleich
noch nicht Bestandteil des Uploads und wurden bewusst noch nicht auf das
FireBeetle übertragen.

Am 11. August 2026 wurde anschließend eine ausdrücklich begrenzte,
hardwarefreie Phase-5-V2-Allowlist übertragen. Sie enthielt den
DS3231-Registeradapter, RTC-Brücke, TimeService, Scheduler, Gateway und den
V2-Runner, aber weder `board_config`, `hardware` noch `protocol`. Der Test
arbeitete mit Speicher-I2C und Fake-Controller, bestand 4/4 Durchläufe und
öffnete keine Hardware. Nach dem Reset wurde der passive Safe-Boot erneut
bestätigt.

## Projektstruktur

```text
landy-heater/
├── REQUIREMENTS.md
├── ARCHITECTURE.md
├── PROTOCOL.md
├── README.md
├── FIREBEETLE_BRINGUP.md
├── DFR0975U_MIGRATION.md
├── ESP32_BOARD_OPTIONS.md
├── board_config.py
├── boot.py
├── main.py
├── app/
│   ├── __init__.py
│   ├── composition.py
│   ├── application_state.py
│   ├── configuration_api_gateway.py
│   ├── configuration_bootstrap.py
│   ├── heater_controller.py
│   ├── manual_control_gateway.py
│   ├── network_composition.py
│   ├── network_configuration.py
│   ├── network_manager.py
│   ├── rest_application.py
│   ├── rest_composition.py
│   ├── scheduler.py
│   ├── scheduler_controller_gateway.py
│   └── temperature_manager.py
├── adapters/
│   ├── __init__.py
│   ├── config_file_store.py
│   ├── ds18b20_adapter.py
│   ├── ds3231_adapter.py
│   └── micropython_http_server.py
├── hardware/
│   ├── __init__.py
│   ├── micropython_ds18b20.py
│   ├── micropython_ds3231.py
│   └── micropython_wifi.py
├── captures/
│   ├── 2026-08-10-phase5-esp32-software-smoke.md
│   ├── 2026-08-11-phase5-v2-esp32-integration-smoke.md
│   ├── 2026-08-11-phase6-config-esp32-smoke.md
│   ├── 2026-08-11-phase7-config-capacity-esp32-smoke.md
│   ├── 2026-08-11-phase7-phone-ap-esp32-smoke.md
│   ├── 2026-08-11-phase7-wifi-radio-esp32-smoke.md
│   ├── 2026-08-11-phase8-frozen-phone-http-esp32-smoke.md
│   ├── 2026-08-11-phase8-rest-esp32-smoke.md
│   ├── 2026-08-11-phase8-wifi-http-capacity-blocked.md
│   ├── 2026-08-25-phase8-full-rest-progress.md
│   ├── 2026-08-30-phase8-single-listener-project-state.md
│   ├── 2026-09-01-dfr0975u-preflash-gate.md
│   ├── 2026-09-01-dfr0975u-first-flash-memory-gate.md
│   ├── 2026-09-01-dfr0975u-usb-identity.md
│   ├── 2026-09-01-dfr0975u-usb-recovery-storage-gate.md
│   ├── 2026-09-01-dfr0975u-wlan-dhcp-gate.md
│   ├── 2026-09-01-dfr0975u-phase8-full-rest-gate.md
│   ├── 2026-08-09-heater-off-status.md
│   └── 2026-08-09-heater-init-response.md
├── firmware/
│   └── dfr0975u_n16r8/
│       ├── artifacts/
│       ├── boards/DFR0975U_N16R8/
│       ├── BUILD_INFO.md
│       ├── README.md
│       └── dependencies.lock.esp32s3
├── protocol/
│   ├── __init__.py
│   ├── crc16.py
│   ├── autoterm_frames.py
│   ├── autoterm_protocol.py
│   ├── autoterm_service.py
│   ├── rx_only_transport.py
│   └── uart_transport.py
├── services/
│   ├── __init__.py
│   ├── config_manager.py
│   ├── configuration_errors.py
│   ├── configuration_storage.py
│   ├── http_protocol.py
│   ├── protocol_capture.py
│   ├── rest_rate_limiter.py
│   ├── rest_security.py
│   ├── rtc_time_bridge.py
│   ├── strict_json.py
│   └── time_service.py
├── tools/
│   ├── __init__.py
│   ├── dfr0975u_memory_probe.py
│   ├── phase5_integration_smoke.py
│   ├── phase5_software_smoke.py
│   ├── phase6_config_smoke.py
│   ├── phase7_config_capacity_smoke.py
│   ├── phase7_network_smoke.py
│   ├── phase7_phone_ap_smoke.py
│   ├── phase8_phone_http_smoke.py
│   ├── phase8_rest_smoke.py
│   ├── uart_loopback_smoke.py
│   └── uart_rx_capture.py
├── tests/
│   ├── test_autoterm_service.py
│   ├── test_board_config.py
│   ├── test_crc.py
│   ├── test_config_file_store.py
│   ├── test_config_manager.py
│   ├── test_configuration_api_gateway.py
│   ├── test_configuration_bootstrap.py
│   ├── test_configuration_storage.py
│   ├── test_dfr0975u_firmware_artifacts.py
│   ├── test_dfr0975u_memory_probe.py
│   ├── test_ds18b20_adapter.py
│   ├── test_ds3231_adapter.py
│   ├── test_frames.py
│   ├── test_heater_controller.py
│   ├── test_http_protocol.py
│   ├── test_manual_control_gateway.py
│   ├── test_micropython_ds18b20.py
│   ├── test_micropython_ds3231.py
│   ├── test_micropython_http_server.py
│   ├── test_micropython_wifi.py
│   ├── test_network_composition.py
│   ├── test_network_manager.py
│   ├── test_parser.py
│   ├── test_phase5_integration_smoke.py
│   ├── test_phase5_software_smoke.py
│   ├── test_phase6_config_smoke.py
│   ├── test_phase7_config_capacity_smoke.py
│   ├── test_phase7_network_smoke.py
│   ├── test_phase7_phone_ap_smoke.py
│   ├── test_phase8_phone_http_smoke.py
│   ├── test_phase8_rest_smoke.py
│   ├── test_protocol_capture.py
│   ├── test_rest_application.py
│   ├── test_rest_composition.py
│   ├── test_rest_rate_limiter.py
│   ├── test_rest_security.py
│   ├── test_rx_only_transport.py
│   ├── test_rtc_time_bridge.py
│   ├── test_safe_boot.py
│   ├── test_scheduler.py
│   ├── test_scheduler_controller_gateway.py
│   ├── test_scheduler_persistence.py
│   ├── test_scheduler_persistence_gateway.py
│   ├── test_sensor_controller.py
│   ├── test_strict_json.py
│   ├── test_temperature_manager.py
│   ├── test_time_service.py
│   ├── test_uart_loopback_smoke.py
│   ├── test_uart_rx_capture.py
│   └── test_uart_transport.py
└── reference/
    ├── ChatGPT Migrationskonzept.md
    ├── 20220918_NodeRed_Flow.json
    └── README.md
```

## Hardware (Phase 13 – Integration & Testing)

Bestätigt beziehungsweise vorgesehen sind:

- historisch bestätigter Prototyp: DFRobot FireBeetle 2 ESP32-E V1.0,
  DFR0654 mit `ESP32_GENERIC` MicroPython 1.28.0
- aktives Nachfolgeprofil: eingetroffenes DFRobot FireBeetle 2 ESP32-S3-U
  V1.0, DFR0975-U mit `ESP32-S3-WROOM-1U-N16R8`, 16 MB Flash, 8 MB Octal-
  PSRAM und angeschlossener externer Antenne
- neuer, zweifach bytegleich erzeugter MicroPython-1.28-Build
  `DFR0975U_N16R8` auf Basis `ESP32_GENERIC_S3`/`SPIRAM_OCT`; statisch
  verifiziert, vollständig geflasht und mit bestandenem USB-only-Speichergate
- geplante S3-Routen: UART2 TX14/RX13, active-high TX-Gate GPIO12 mit späterem
  externem Pull-down, I2C1 SDA10/SCL11 und 1-Wire GPIO4; alle elektrischen
  Freigaben, Protokoll-TX und WLAN bleiben `False`
- geeigneter 5-V-↔-3,3-V-Pegelwandler für die Autoterm-UART
- drei DS18B20-Sensoren
- DS3231-RTC
- geeigneter DC/DC-Wandler vom 12-V-Bordnetz

Die DFR0975-U-Auswahl und die zwingend getrennte S3-Firmware-, Pin-, PSRAM-,
Recovery- und Abnahmefolge sind in `DFR0975U_MIGRATION.md` festgehalten. Die
genauen Builddaten und Artefakte liegen unter `firmware/dfr0975u_n16r8/`.
Die geprüften Alternativboards und Auswahlkriterien stehen in
`ESP32_BOARD_OPTIONS.md`.

`board_config.py` ist auf die physisch bestätigte S3-Identität gebunden. Für
1-Wire muss später zusätzlich `ONEWIRE_PIN_APPROVED=True`, der externe 4,7-kΩ-
Pull-up und die konfliktfreie reale Verdrahtung bestätigt sein; der Validator
sperrt PMIC-, UART0-, USB-, Flash/PSRAM-, Boot-Strapping-, LED-, Taster-,
Kamera- und JTAG-Routen.
Für I2C gilt eine getrennte, ebenso strikte Freigabe: SDA/SCL müssen
verschieden, ausgangsfähig und konfliktfrei sein; ID, 100-kHz-Frequenz,
Timeout und DS3231-Adresse `0x68` sind fest validiert. GPIO1/2 bleiben wegen
des V1.0-AXP313A-PMIC-Busses ausgeschlossen. Im ausgelieferten Stand bleiben
SDA10/SCL11 zugeordnet, aber `I2C_PINS_APPROVED=False`; die DS3231-Factory
scheitert deshalb vor dem Import von `machine` und öffnet keinen GPIO oder Bus.
Beim festgelegten ESP32_GENERIC_S3/MicroPython-1.28-Stand besitzt `machine.I2C`
außerdem kein nutzbares `deinit()`: Die Hülle schließt daher logisch, gibt SDA
und SCL als Eingänge ohne Pull frei und verhindert eine weitere Nutzung ihres
Ports. Der Legacy-I2C-Treiber kann einzelne Transaktionen trotz konfiguriertem
50-ms-Peripherie-Timeout deutlich länger blockieren; diese reale Worst-Case-
Laufzeit muss in Phase 13 gemessen werden und ist noch keine Timingfreigabe.
Zusätzlich blockiert die ausgelieferte Einstellung
`UART_PROTOCOL_TX_ENABLED = False` jede Übertragung über den regulären
Protokolltransport. Dessen Low-Level-Factory besitzt keinen direkten
`tx_enabled`-Override, und die Status-Property ist schreibgeschützt. Das ist
jedoch keine unveränderliche Python- oder physische Sicherheitsgrenze: Eine
bewusst geänderte beziehungsweise ersetzte Low-Level-Konfiguration mit
`UART_PROTOCOL_TX_ENABLED = True` kann diesen Transport für einen späteren
Meilenstein autorisieren. Die neue laufzeitseitige Safe-Factory akzeptiert
diesen Zustand dagegen ausdrücklich nicht und räumt einen unerwartet
TX-fähigen Transport wieder auf. Auf dem DFR0975-U erfordert ein späteres TX
zusätzlich das freigegebene active-high Hardwaregate an GPIO12 mit physischem
Pull-down und geeigneter Tri-State-/Pegelstufe. Im aktuellen Projekt bleiben
sowohl Protokoll-TX als auch Gate- und UART-Pinfreigabe `False`. `main.py`
importiert weder Protokoll noch `machine` und öffnet keine Hardware.

Der passive Capture- und Loopbackpfad bleibt ausdrücklich DFR0654-spezifisch.
Dort ordnet die Factory UART2 während ihrer Erzeugung kurz GPIO17 als TX zu
und neutralisiert ihn anschließend als `Pin.IN` ohne Pull. Diese Annahme ist
nicht auf den S3 übertragen: Die alten Werkzeuge lehnen das aktive DFR0975-U-
Profil ab. Ein neuer RX-/Loopbacktest folgt erst nach einem eigenen,
unbeschalteten S3-Gate; bis dahin wird keine UART-Hardware geöffnet.

Der schrittweise USB- und Loopback-Ablauf steht in
[FIREBEETLE_BRINGUP.md](FIREBEETLE_BRINGUP.md) und gilt als historische
DFR0654-Anleitung; die S3-Abfolge steht in
[DFR0975U_MIGRATION.md](DFR0975U_MIGRATION.md).

Die UART-Anbindung orientiert sich an der stabilen MicroPython-1.28-API:

- [machine.UART](https://docs.micropython.org/en/v1.28.0/library/machine.UART.html)
- [machine.Pin](https://docs.micropython.org/en/v1.28.0/library/machine.Pin.html)
- [ESP32 UART Quick Reference](https://docs.micropython.org/en/v1.28.0/esp32/quickref.html#uart-serial-bus)

Der DS18B20-Adaptervertrag orientiert sich ebenfalls an MicroPython 1.28:

- [ESP32 OneWire Quick Reference](https://docs.micropython.org/en/v1.28.0/esp32/quickref.html#onewire-driver)
- [OneWire-/DS18X20-Ablauf mit 750-ms-Konvertierungszeit](https://docs.micropython.org/en/v1.28.0/esp8266/tutorial/onewire.html)

Für die spätere reale Verkabelung werden dreiadrig versorgte Sensoren, eine
gemeinsame Signalmasse und ein externer Pull-up von ungefähr 4,7 kΩ nach 3,3 V
vorausgesetzt. Parasitäre Versorgung und ein Strong-Pull-up-Pfad sind nicht
freigegeben.

`UART_DRIVER_TIMEOUT_MS` und `UART_DRIVER_TIMEOUT_CHAR_MS` sind explizit in
`board_config.py` abgelegt; `UART_INVERT = 0` erzwingt normale, nicht
invertierte UART-Signale. Die getrennte 200-ms-Altersprüfung erfolgt nur
beim nichtblockierenden Polling und verwendet weder Sleep noch Timer-Callback.
Der spätere Hauptloop muss `poll()` deutlich häufiger als alle 200 ms
aufrufen. Diese zeitliche Vorgabe muss in einem späteren, eigenen
UARTTransport-Timingtest auf dem ESP32 gemessen werden; der reine
GPIO-Loopback prüft sie noch nicht.
Drei unmittelbar aufeinanderfolgende Fälle von „Daten gemeldet, aber keine
Bytes gelesen“ sperren die RX-Verarbeitung als `rx_faulted`, bis `reset_rx()`
erfolgreich ausgeführt wurde. Ein dazwischenliegender leerer Poll setzt diese
Anomalieserie zurück.

## Sicherheit und offene Protokollpunkte

Dieses Paket ist noch **keine einsatzbereite Heizungssteuerung**. Die realen
STATUS- und INIT-Vektoren bestätigen Startmarker, Längenregel und
high-byte-first CRC für Kommandos `0x0F` und `0x04`. Ob dieselben Regeln
ausnahmslos für andere Antworttypen gelten, ist noch offen; ebenso negative
Temperaturcodierung und große Teile der Statusantwort. Ein CRC-Fehler wird
deshalb aktuell erkannt und gemeldet, aber im Parser nicht global automatisch
verworfen. Der Controller-Kern verlangt für INIT und STATUS eine erneute
kanonische Prüfung durch seinen Protokoll-Port, die exakt bestätigte Frameform
und einen gültigen CRC. Nur eine zuvor angeforderte und innerhalb der
Antwortfrist empfangene STATUS-Antwort darf eine Steuerentscheidung
autorisieren.

Vor der ersten Verbindung mit der realen Heizung folgen das boardspezifische
UART-Bring-up sowie Capture-/Diagnosetests ohne Startbefehl. Kritische
Protokolldetails werden nicht stillschweigend geraten.

Der erste Mitschnitt erfolgt nach Möglichkeit passiv parallel zum weiterhin
funktionierenden Altcontroller. Bevorzugt wird dafür der eindeutig bestätigte
3,3-V-Knoten des **bereits funktionierenden** Wandlers angezapft, der zum
Raspberry-UART-RX führt. Ein zusätzlicher, unbekannter Wandler wird nicht
parallel eingesetzt. Der hochohmige Tap kann nach elektrischer Prüfung über
einen 10-kΩ-Serienwiderstand zu GPIO16/D11 geführt werden; dazu kommt nur die
gemeinsame Signalmasse. FireBeetle-3V3 und -5V werden nicht mit den
Versorgungsschienen des bestehenden Wandlers verbunden. GPIO17/D10 bleibt
physisch vollständig frei.

Ist nur die 5-V-Seite zugänglich, braucht es stattdessen einen exakt
identifizierten und elektrisch geprüften, fest gerichteten 5→3,3-V-RX-Pfad.
Ein nur als „4-Kanal 5V/3.3V“ beschriftetes Modul ist dafür nicht freigegeben.
INIT und STATUS vom ESP32 sind ebenfalls noch nicht freigegeben; sie wären erst
ein späterer, separat autorisierter Bring-up-Meilenstein. START und SHUTDOWN
bleiben darüber hinaus gesperrt. Das passive Auslesen im bestehenden Node-RED
hat keine solche ESP32-Übertragung ausgelöst.

Der Parser enthält nun unveränderte echte STATUS- und INIT-RX-Testvektoren aus
dem Node-RED-Knoten `Input Diagnostics`. Für diese beiden Antworttypen ist der
längenbasierte RX-Framer bestätigt; für andere Antworttypen bleibt die
Verallgemeinerung eine abgeleitete Annahme. Der reguläre UART-Transport legt
die unveränderten Treiber-Chunks und abgegrenzten Frames in einen begrenzten
Diagnosepuffer. Der RX-only-Pfad hält dagegen ausschließlich rohe
Treiber-Chunks vor und gibt sie über `services/protocol_capture.py` als NDJSON
aus; der Frame-Parser ist daran nicht beteiligt.
Bei Überlauf bleiben die neuesten Ereignisse erhalten; der Drop-Zähler macht
den Mitschnitt jedoch zwingend als unvollständig erkennbar.
Die Zähler sind kumulativ. Der Capture-Service vergleicht deshalb ihre Werte
am Anfang und Ende jedes einzelnen Mitschnitts.

Der optionale Parsermodus `require_valid_crc_for_framing=True` kann einen
fehlerhaften Kandidaten verwerfen und innerhalb des Datenstroms am nächsten
Marker weitersuchen. Er bleibt global standardmäßig aus, damit unbekannte oder
noch nicht erfasste Antworttypen nicht aufgrund einer Verallgemeinerung aus der
Rohdiagnose verschwinden.

Der alte Node-RED-Flow ließ an zwei UI-Slidern auch den Wert `0` zu. Die finale
`REQUIREMENTS.md` legt für die neue Bedienoberfläche bewusst Power `1–9` und
Zieltemperatur `5–30 °C` fest; die Builder folgen dieser Baseline.

## Nächster Schritt

1. DFR0975-U weiter ausschließlich über USB betreiben; Heizung, Fahrzeugstrom,
   UART, RTC/I2C, 1-Wire und Sensoren bleiben unverbunden.
2. Der vertrauliche 16-MB-Werksbackup-Read, geräteseitige Digest und die
   Offline-Layoutprüfung sind bestanden; das Image bleibt außerhalb von Git.
3. Artefaktprüfung, ausdrückliche hashgebundene Freigabe, vollständiges
   Löschen, Combined-Flash und Schreibverifikation sind abgeschlossen.
4. Passiver USB-Boot und exakte MicroPython-/Boardidentität sind bestätigt.
   GC, 8-MiB-PSRAM, interner 8-Bit-Heap und DMA-fähiger interner Heap haben
   ihre getrennten Gates bestanden; STA und AP blieben deaktiviert.
5. Manueller ROM-Recovery über `BOOT`/`RST` und der isolierte
   VFS/A-B-Storage-Gate sind bestanden. Automatischer USB-Control-Line-Recovery
   gilt als unzuverlässig; physischer Tastenzugang bleibt zwingend.
6. Der getrennte Funk-/DHCP-Gate ist bestanden. Der S3-UART-/Level-Interface-
   Gate bleibt separat offen; der alte DFR0654-RX-/Loopbackpfad wird nicht
   übernommen.
7. Der begrenzte Phase-8-Ein-Listener-Lauf auf Port 80 mit realem
   `GET /api/v1/status`, HTTP 200, vollständigem JSON, allen GC-Heap-/Safety-
   Gates und komplettem Cleanup ist bestanden.
8. Phase 9 ist vollständig bestanden: A/B-Build, Artefaktprüfung, autorisierter
   App-Flash ohne Full Erase, komplette Rückleseprüfung sowie der reale
   Ein-Listener-Handygate mit 9/9 UI-Ressourcen, 4/4 API-Lesezugriffen, null
   Mutationen und vollständigem Cleanup. Produktiver Auto-Start bleibt aus.
9. Phase 10 ist softwareseitig korrigiert und in
   `PHASE10_SETUP_ASSISTANT.md` dokumentiert. Netzwerkzugangsdaten sind
   write-only, Passwort-/Open-Auswahl ist ausdrücklich, ungültige Eingaben
   blockieren den jeweiligen Schritt, und die allgemeine Settings-API bleibt
   für Netzwerkdaten geschlossen. Aktive Sensor-/UART-Prüfungen bleiben
   sichtbar zurückgestellt.
10. Frozen-Closure, reproduzierbarer DFR0975-U-A/B-Build, statische
    Artefaktprüfung, autorisierter app-only Flash ohne Full Erase und komplette
    Rücklesung des korrigierten Images sind bestanden. Der reale Credential-
    Lauf bestätigte einen AP-Passwortwechsel, ein geschütztes Stations-WLAN und
    genau eine erfolgreiche isolierte Mutation. Der anschließende Ablauf
    bestätigte Stations-DHCP, AP-Neuanmeldung sowie dauerhafte Erstellung,
    Bearbeitung und Löschung eines inaktiven Timers; Produktionsspeicher,
    Hardwaregrenzen und Funk-Cleanup blieben intakt. Offen bleibt der formale
    Same-run-Nachweis jeder einzelnen Browserroute; der Runner protokolliert
    einen künftigen Fehler dafür jetzt routengenau.
11. Der erste Phase-10.1-Kandidat wurde nach erfolgreichem Flash/Readback im
    Zieltest verworfen: DHCP, mDNS und 58/58 Captive-DNS-Antworten bestanden,
    aber die im Design angenommene Socketfunktion fehlt in MicroPython 1.28.
    Der danach geflashte Zwei-Listener-Kandidat erreichte DHCP, mDNS, 5/5
    Captive-DNS-Antworten und einen angenommenen AP-Request. Dessen 302-Antwort
    scheiterte an einer fehlenden Encoder-Freigabe plus doppeltem
    `Cache-Control`. Der portal-korrigierte 44-Datei-Kandidat ist gebaut,
    offline geprüft, hashgebunden app-only geschrieben und vollständig
    bytegleich zurückgelesen. Das kombinierte AP-/STA-/Portal-/Heap-/Cleanup-
    Zielgate bestand mit automatischer Portalöffnung am Handy, 10 DNS-Antworten,
    drei 302-Weiterleitungen, lesender Heimnetz-UI, gesperrter Stationsmutation,
    fünf bestandenen Speicherpunkten und vollständigem Funk-Cleanup.

Erst nach zusätzlichen echten RX-Captures und der elektrischen Prüfung wird
der reguläre Kommunikationsablauf freigegeben. Die abstrakten START- und
SHUTDOWN-Entscheidungen sind unter CPython getestet; reale Übertragungen bleiben
bis zu einem separat autorisierten Hardware-Meilenstein gesperrt.
