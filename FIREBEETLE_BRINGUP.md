# FireBeetle DFR0654 – sicherer Bring-up

> **Historischer DFR0654-Pfad:** Diese Anleitung, ihre Pins und ihre Firmware
> gelten ausschließlich für den klassischen ESP32 DFR0654. Als Nachfolgeboard
> ist ein DFR0975-U N16R8 ausgewählt; dessen noch ausstehender S3-Bring-up ist
> getrennt in `DFR0975U_MIGRATION.md` geplant. Keine DFR0654-Firmware und keine
> Pinannahme auf den ESP32-S3 übertragen.

## Bestätigtes Boardprofil

Das Board wurde über den vorhandenen Pin `D11/16` als **DFRobot DFR0654,
FireBeetle 2 ESP32-E V1.0** bestätigt. Das ähnliche DFR1139 trägt an dieser
Position `NC` und ist ausdrücklich nicht dieses Profil.

| Funktion | Board-Aufdruck | GPIO | Projektstatus |
|---|---:|---:|---|
| UART2 TX | `D10/17` | 17 | konfiguriert, Protokoll-TX gesperrt |
| UART2 RX | `D11/16` | 16 | konfiguriert |
| USB/REPL TX | `TXD/1` | 1 | reserviert, nicht verwenden |
| USB/REPL RX | `RXD/3` | 3 | reserviert, nicht verwenden |
| Onboard-LED | `D9/2` | 2 | nicht für UART verwenden |

DFRobot dokumentiert UART2 mit TX=GPIO17 und RX=GPIO16. Das passende
MicroPython-Ziel ist `ESP32_GENERIC`; für dieses Projekt ist die stabile
Version 1.28.0 festgelegt.

Offizielle Quellen:

- [DFR0654 Board und Pinout](https://wiki.dfrobot.com/dfr0654/)
- [DFR0654 UART2](https://wiki.dfrobot.com/dfr0654/docs/23254)
- [DFRobot MicroPython-Zuordnung](https://wiki.dfrobot.com/tutorial/22586)
- [MicroPython ESP32_GENERIC v1.28.0](https://micropython.org/download/ESP32_GENERIC/)

## Stufe A – nur USB und Identifikation

Voraussetzungen:

- Heizung vollständig getrennt
- kein Pegelwandler, kein 12-V-Anschluss, keine Sensoren
- kein Jumper zwischen GPIO17 und GPIO16
- Board ausschließlich über ein USB-Datenkabel verbunden

Im VS-Code-Terminal im Projektordner:

```bash
python3 -m venv .venv-tools
source .venv-tools/bin/activate
python -m pip install --upgrade esptool mpremote
mpremote connect list
```

Falls kein Anschluss erscheint oder die Verbindung fehlschlägt: **stoppen**,
alle seriellen Monitore schließen, USB-Datenkabel und USB-Anschluss prüfen,
das Board neu verbinden und den Befehl wiederholen. Erst wenn weiterhin kein
Port erscheint, die CH340-Hinweise und Treiber auf der offiziellen
[DFR0654-Seite](https://wiki.dfrobot.com/dfr0654/) verwenden. Keine
Boot-Pins überbrücken und nichts an der Heizung anschließen.

Den angezeigten Anschluss als `PORT` einsetzen und zunächst nur lesend prüfen:

```bash
python -m esptool --chip esp32 --port PORT flash-id
```

Erwartet werden die Chipfamilie **ESP32** und **4 MB Flash**. `flash-id`
identifiziert dabei den Chip, nicht zwingend den Modulnamen `WROOM-32E`. Bei
16 MB oder einer anderen Chipfamilie/Flashgröße stoppen und nichts löschen.

## Stufe B – Backup und MicroPython

Diese Befehle erst nach erfolgreicher Stufe A ausführen. Zuerst den gesamten
vorhandenen Flash sichern:

```bash
python -m esptool --chip esp32 --port PORT read-flash 0 ALL firebeetle-original-backup.bin
```

Das Backup muss danach exakt **4.194.304 Bytes** groß sein. Vor dem Löschen
Größe und Prüfsumme kontrollieren und die Datei zusätzlich an einem zweiten,
sicheren Ort ablegen. Anschließend dort dieselbe SHA-256-Prüfsumme erneut
berechnen und mit dem ersten Wert vergleichen:

```bash
wc -c < firebeetle-original-backup.bin
shasum -a 256 firebeetle-original-backup.bin
```

Ein vollständiges Flash-Abbild kann Zugangsdaten oder andere vertrauliche
Informationen enthalten. Nicht veröffentlichen und nicht in ein Repository
hochladen.

Danach die stabile Datei `ESP32_GENERIC-20260406-v1.28.0.bin` von der oben
verlinkten offiziellen MicroPython-Seite direkt in den geöffneten
Projektordner laden beziehungsweise dorthin verschieben. Die offizielle Datei
ist 1.760.192 Bytes groß und hat SHA-256
`cd7820d02c35d34dd403b44263129c6a511b350aea8446c229890753fe240784`.
Vor dem Flashen prüfen:

```bash
wc -c < ESP32_GENERIC-20260406-v1.28.0.bin
shasum -a 256 ESP32_GENERIC-20260406-v1.28.0.bin
```

Die nächsten beiden Befehle löschen beziehungsweise überschreiben den
Board-Flash:

```bash
python -m esptool --chip esp32 --port PORT erase-flash
python -m esptool --chip esp32 --port PORT --baud 460800 write-flash 0x1000 ESP32_GENERIC-20260406-v1.28.0.bin
```

Falls das Schreiben bei 460800 Baud scheitert, den Befehl ohne
`--baud 460800` wiederholen. Keinesfalls `--force` verwenden.

Anschließend Reset drücken und MicroPython rein lesend prüfen:

```bash
mpremote connect PORT exec "import sys, os, machine; print(sys.implementation); print(os.uname()); print(machine.freq())"
```

## Stufe B.1 – Phase-5-Softwaretest, weiterhin nur USB

Dieser Test darf direkt nach Stufe B oder später unabhängig von den UART-
Stufen ausgeführt werden. Voraussetzungen:

- Board ausschließlich über USB verbunden
- kein Jumper zwischen GPIO17 und GPIO16
- sämtliche GPIOs frei
- keine Heizung, kein Pegelwandler, keine Sensoren und keine RTC verbunden
- kein 12-V- oder Bordnetzanschluss

Das Werkzeug arbeitet nur mit künstlichen Zeit- und Timerdaten. Es importiert
weder `board_config` noch Protokoll, Controller, Hardwareadapter oder
Composition und öffnet insbesondere weder UART, Pin, I2C noch 1-Wire. Es läuft
beim Booten und beim Import nicht automatisch.

Bei einem später wiederholten Test zuerst Anschluss und MicroPython-Ziel neu
bestätigen; dabei wird noch keine Hardware geöffnet:

```bash
mpremote connect list
mpremote connect PORT exec "import sys, os; print(sys.implementation); print(os.uname())"
```

Nur die ausdrücklich benötigten hardwarefreien Dateien übertragen:

```bash
mpremote connect PORT cp boot.py main.py :
mpremote connect PORT mkdir :app
mpremote connect PORT mkdir :services
mpremote connect PORT mkdir :tools
mpremote connect PORT cp app/__init__.py app/application_state.py app/scheduler.py :app/
mpremote connect PORT cp services/__init__.py services/time_service.py :services/
mpremote connect PORT cp tools/__init__.py tools/phase5_software_smoke.py :tools/
```

Nur die exakte „Verzeichnis existiert bereits“-Meldung der drei `mkdir`-
Befehle darf ignoriert werden; sie ist kein Grund, etwas zu löschen. Bei
jedem Fehler eines `cp`-Befehls dagegen sofort stoppen, weil sonst eine alte
Dateiversion getestet werden könnte. Keine Tests, `.pyc`-Dateien oder
kompletten Verzeichnisse rekursiv übertragen.

Zuerst ausschließlich den import-inerten Runner prüfen; die großen
Softwaremodule werden bis zur exakten USB-Bestätigung noch nicht geladen:

```bash
mpremote connect PORT exec "import tools.phase5_software_smoke; print('Phase 5 smoke runner import PASS; core deferred')"
```

Danach den begrenzten Test mit der exakten USB-Bestätigung auslösen:

```bash
mpremote connect PORT exec "from tools.phase5_software_smoke import run, SOFTWARE_ONLY_CONFIRMATION; run(SOFTWARE_ONLY_CONFIRMATION)"
```

Der Ablauf simuliert Sonntag, den 9. August 2026, von 14:29:59 bis zur
Timerkante 14:30:00. Er erwartet genau einen Timerauftrag, autorisiert und
bestätigt ihn ausschließlich in-memory, prüft die Once-only-Sperre sowie den
manuellen Override und verwirft zusätzlich einen absichtlich abgelaufenen
Auftrag. Es wird kein `HeaterController` aufgerufen.

Erwartete Abschlussausgabe:

```text
PHASE 5 SOFTWARE-ONLY SMOKE PASS: 8/8 iterations; no hardware opened
MicroPython heap free: before=... after_import=... after_warmup=... after=... bytes
PHASE5_USB_SMOKE_PASS_V1
```

Die Speicherwerte bilden zugleich die erste reale DFR0654-Baseline. Der Test
verlangt nach Core-Import, Aufwärmlauf und Messlauf jeweils mindestens 32 KiB
freien Heap. Nach der Speicherbereinigung darf der Messlauf höchstens 4 KiB
oder 2 Prozent des Aufwärm-Baselinewerts verloren haben – maßgeblich ist der
größere der beiden Werte. Nur der letzte, exakte Token bestätigt einen vollständig
bestandenen Lauf. Bei einem Traceback, Neustart oder fehlenden Token abbrechen,
die vollständige Ausgabe aufbewahren und weiterhin keine Hardware anschließen.

Abschließend das Board neu starten. Falls der Anschluss kurz verschwindet,
mit `mpremote connect list` erneut bestimmen und erst dann den passiven
Einstiegspunkt nochmals manuell prüfen:

```bash
mpremote connect PORT reset
mpremote connect PORT exec "import main; main.main()"
```

Erwartet wird beim passiven Einstiegspunkt weiterhin:

```text
Landy Heater safe boot; UART inactive; protocol TX disabled
```

Der reale DFR0654-Lauf vom 10. August 2026 bestand mit dem Abschlusstoken
`PHASE5_USB_SMOKE_PASS_V1`. Der freie Heap betrug vor dem Core-Import
152.896 Bytes, danach 127.456 Bytes und nach Aufwärm- sowie Messlauf jeweils
exakt 128.608 Bytes. Die vollständige Evidenz steht in
`captures/2026-08-10-phase5-esp32-software-smoke.md`.

## Stufe B.2 – Phase-5-V2-Integrationstest, weiterhin nur USB

Dieser zweite USB-only-Test ergänzt Stufe B.1 um den hardwarefreien
DS3231-Registeradapter, die RTC-/TimeService-Brücke und das synchrone
Scheduler-/Controller-Gateway. Sämtliche Voraussetzungen aus Stufe B.1 gelten
unverändert: alle GPIOs bleiben frei; weder RTC, Sensor, UART, Heizung,
Pegelwandler noch 12 V sind angeschlossen.

Zuerst Anschluss und MicroPython-Ziel erneut prüfen:

```bash
mpremote connect list
mpremote connect PORT exec "import sys, os; print(sys.implementation); print(os.uname())"
```

Nur die folgende Allowlist übertragen. Insbesondere `board_config.py`,
`hardware/`, `protocol/`, Tests und komplette Verzeichnisse werden nicht
kopiert:

```bash
mpremote connect PORT cp boot.py main.py :
mpremote connect PORT mkdir :app
mpremote connect PORT mkdir :adapters
mpremote connect PORT mkdir :services
mpremote connect PORT mkdir :tools
mpremote connect PORT cp app/__init__.py app/application_state.py app/scheduler.py app/scheduler_controller_gateway.py :app/
mpremote connect PORT cp adapters/__init__.py adapters/ds3231_adapter.py :adapters/
mpremote connect PORT cp services/__init__.py services/time_service.py services/rtc_time_bridge.py :services/
mpremote connect PORT cp tools/__init__.py tools/phase5_integration_smoke.py :tools/
```

Nur die exakte „Verzeichnis existiert bereits“-Meldung eines `mkdir`-Befehls
darf ignoriert werden. Bei jedem `cp`-Fehler sofort stoppen. Danach zuerst den
import-inerten Runner prüfen:

```bash
mpremote connect PORT exec "import tools.phase5_integration_smoke; print('Phase 5 V2 runner import PASS; core deferred')"
```

Den Test anschließend ausdrücklich starten:

```bash
mpremote connect PORT exec "from tools.phase5_integration_smoke import run, SOFTWARE_ONLY_CONFIRMATION; run(SOFTWARE_ONLY_CONFIRMATION)"
```

Der Test verwendet ausschließlich einen Speicher-I2C und einen künstlichen
Requested-State-Controller. Er prüft den gestagten DS3231-Schreibpfad, die
RTC-Brücke, eine Timer-Occurrence, Gateway-Autorisierung/-Vervollständigung,
manuellen Override, Heap-Erholung, die realen MicroPython-Tickhelfer sowie die
eingebetteten `Europe/Zurich`-Grenzen für Frühlingslücke und beide
Herbststunden. Insbesondere erzeugt ein Neustart in `fold=1` keinen
Timer-Intent. Er öffnet weder `machine`, GPIO, reales I2C, UART noch 1-Wire.

Erwartete Abschlussausgabe:

```text
PHASE 5 USB-ONLY INTEGRATION SMOKE PASS: 4/4 iterations; no hardware opened
MicroPython heap free: before=... after_import=... after_warmup=... after=... bytes
PHASE5_USB_SMOKE_PASS_V2
```

Nur der letzte exakte Token bestätigt einen vollständig bestandenen Lauf.
Danach das Board wie in Stufe B.1 zurücksetzen und den passiven Einstiegspunkt
erneut prüfen.

Der finale um DST erweiterte DFR0654-Lauf vom 11. August 2026 bestand mit 4/4
Durchläufen und dem Abschlusstoken `PHASE5_USB_SMOKE_PASS_V2`. Der freie Heap
betrug vor dem Import 149.104 Bytes, danach 96.928 Bytes, nach dem Aufwärmlauf
97.616 Bytes und nach dem Messlauf 97.296 Bytes. Nach dem Reset meldete der
passive Einstiegspunkt erneut
`Landy Heater safe boot; UART inactive; protocol TX disabled`. Die vollständige
Evidenz steht in `captures/2026-08-11-phase5-dst-esp32-smoke.md`.

## Stufe B.3 – Phase-6-Konfigurationsspeicher, weiterhin nur USB

Diese Stufe prüft den hardwarefreien Phase-6-Persistenzpfad einschließlich
echter, isolierter Flashdateien. Sämtliche Voraussetzungen aus Stufe B.1 gelten
weiterhin: nur USB, alle GPIOs frei, kein UART-Jumper, keine Heizung, keine
Sensoren, keine RTC, kein Pegelwandler und kein 12-V-Anschluss.

Zuerst Ziel und Anschluss erneut bestätigen:

```bash
mpremote connect list
mpremote connect PORT exec "import sys, os; print(sys.implementation); print(os.uname())"
```

Nur die folgende Allowlist übertragen. `boot.py`, `main.py`, `board_config.py`,
`hardware/`, `protocol/`, Controller/Composition und Tests bleiben unangetastet:

```bash
mpremote connect PORT mkdir :adapters
mpremote connect PORT mkdir :app
mpremote connect PORT mkdir :services
mpremote connect PORT mkdir :tools
mpremote connect PORT cp adapters/__init__.py adapters/config_file_store.py :adapters/
mpremote connect PORT cp app/__init__.py app/application_state.py app/configuration_bootstrap.py app/scheduler.py app/scheduler_controller_gateway.py app/temperature_manager.py :app/
mpremote connect PORT cp services/__init__.py services/config_manager.py services/time_service.py :services/
mpremote connect PORT cp tools/__init__.py tools/phase5_integration_smoke.py tools/phase6_config_smoke.py :tools/
```

Nur eine exakte „Verzeichnis existiert bereits“-Meldung darf bei `mkdir`
ignoriert werden; bei jedem `cp`-Fehler sofort stoppen. Anschließend den
import-inerten Runner prüfen. Vor und nach dem Import dürfen keine gesperrten
Hardwaremodule erscheinen:

```bash
mpremote connect PORT exec "import sys; before=tuple(k for k in sys.modules if k=='machine' or k=='board_config' or k.startswith('hardware') or k.startswith('protocol')); import tools.phase6_config_smoke; after=tuple(k for k in sys.modules if k=='machine' or k=='board_config' or k.startswith('hardware') or k.startswith('protocol')); print('PHASE6_IMPORT_PASS', before, after)"
```

Erwartet:

```text
PHASE6_IMPORT_PASS () ()
```

Den eigentlichen Lauf nur mit der exakten Bestätigung starten:

```bash
mpremote connect PORT exec "from tools.phase6_config_smoke import run, SOFTWARE_ONLY_CONFIRMATION; run(SOFTWARE_ONLY_CONFIRMATION)"
```

Der Test provisioniert Konfiguration und Scheduler-Ledger dual, prüft
No-op-Schreibfreiheit, Cold-Boot, einen dauerhaft konsumierten Timerstart,
manuellen Override, Restore ohne Replay und beschädigte neueste Slots. Nur ein
Durchlauf verwendet echte Dateien; alle übrigen Wiederholungen nutzen ein
Speicherdateisystem. Die sechs möglichen Flashdateien heißen ausschließlich
`/phase6_usb_config_smoke_v1_{config,ledger}.{a,b,tmp}` und werden vor sowie
nach dem Test entfernt. Der Runner prüft zusätzlich UTF-8/Steuerzeichen,
MicroPython-Ticks und Heap-Erholung.

Erwartete Abschlussform:

```text
PHASE 6 USB-ONLY CONFIG SMOKE PASS: 4/4
configuration_generation=2
ledger_generation=4
flash_config_writes=2
flash_ledger_writes=4
memory_before=...
memory_after_import=...
memory_after_warmup=...
memory_after=...
PHASE6_USB_CONFIG_SMOKE_PASS_V1
```

Nur der letzte exakte Token gilt als Erfolg. Danach kontrollieren, dass keine
Smoke-Datei übrig ist:

```bash
mpremote connect PORT exec "import os; remaining=sorted(x for x in os.listdir('/') if x.startswith('phase6_usb_config_smoke_v1_')); print('PHASE6_CLEANUP_PASS', remaining)"
```

Erwartet wird `PHASE6_CLEANUP_PASS []`. Abschließend per Reset beziehungsweise
`Ctrl-D` den echten passiven Boot erneut beobachten. Es muss weiterhin heißen:

```text
Landy Heater safe boot; UART inactive; protocol TX disabled
```

Der finale DFR0654-Lauf vom 11. August 2026 bestand 4/4. Die Generationen waren
2/4, die Schreibzähler 2/4 und der freie Heap betrug 149.312 Bytes vor dem
Import, 67.056 Bytes danach, 64.288 Bytes nach dem Aufwärmlauf und 64.304 Bytes
nach dem Messlauf. Cleanup und Safe-Boot wurden bestätigt. Die vollständige
Evidenz steht in `captures/2026-08-11-phase6-config-esp32-smoke.md`.

## Stufe B.4 – Phase-7-Konfigurationskapazität, weiterhin nur USB

Diese Stufe prüft die praktische Schema-v2-Obergrenze mit 32 Timern, acht
bekannten WLAN-Profilen, dualem A/B-Commit und frischem Reload. Sie öffnet
keinen Funk und keine andere Hardware. Sämtliche Voraussetzungen aus Stufe B.1
gelten weiter.

Die Laufzeitmodule sind inzwischen zu groß, um ihre `.py`-Quellen zusammen mit
den maximalen Testdaten sinnvoll im kleinen ESP32-Heap zu kompilieren. Deshalb
wird für diese Abnahme der **offizielle, exakt zu MicroPython 1.28.0 passende**
`mpy-cross` mit `-march=xtensawin` verwendet. Ein anders versionierter Compiler
ist nicht zulässig. Die folgenden zehn Module werden in ein isoliertes lokales
Verzeichnis kompiliert:

```bash
mkdir -p phase7_capacity_mpy_v1/adapters phase7_capacity_mpy_v1/app phase7_capacity_mpy_v1/services
MPY_CROSS=/EXAKTER/PFAD/ZU/v1.28.0/mpy-cross
for SOURCE in adapters/__init__.py adapters/config_file_store.py app/__init__.py app/application_state.py app/network_configuration.py app/scheduler.py app/temperature_manager.py services/__init__.py services/config_manager.py services/time_service.py; do
  TARGET="phase7_capacity_mpy_v1/${SOURCE%.py}.mpy"
  "$MPY_CROSS" -march=xtensawin -o "$TARGET" "$SOURCE" || exit 1
done
"$MPY_CROSS" --version
```

Erwartet werden MicroPython 1.28.0 und mpy v6.3. Danach Ziel und Anschluss
erneut prüfen, nur den import-inerten Runner sowie die isolierte Closure
übertragen und bei jedem Kopierfehler sofort stoppen:

```bash
mpremote connect list
mpremote connect PORT exec "import sys, os; print(sys.implementation); print(os.uname())"
mpremote connect PORT mkdir :phase7_capacity_mpy_v1
mpremote connect PORT mkdir :phase7_capacity_mpy_v1/adapters
mpremote connect PORT mkdir :phase7_capacity_mpy_v1/app
mpremote connect PORT mkdir :phase7_capacity_mpy_v1/services
mpremote connect PORT mkdir :tools
mpremote connect PORT cp tools/__init__.py tools/phase7_config_capacity_smoke.py :tools/
mpremote connect PORT cp phase7_capacity_mpy_v1/adapters/*.mpy :phase7_capacity_mpy_v1/adapters/
mpremote connect PORT cp phase7_capacity_mpy_v1/app/*.mpy :phase7_capacity_mpy_v1/app/
mpremote connect PORT cp phase7_capacity_mpy_v1/services/*.mpy :phase7_capacity_mpy_v1/services/
```

Nur die exakte „Verzeichnis existiert bereits“-Meldung eines `mkdir`-Befehls
darf ignoriert werden. Nach einem Reset den isolierten Pfad vor den normalen
Quellpfad setzen und den Test exakt einmal starten:

```bash
mpremote connect PORT reset
mpremote connect PORT exec "import sys; sys.path.insert(0, '/phase7_capacity_mpy_v1'); import tools.phase7_config_capacity_smoke as s; s.run(s.SOFTWARE_ONLY_CONFIRMATION)"
```

Erwartete Abschlussform:

```text
PHASE 7 CONFIG CAPACITY PASS: timers=32 networks=8 bytes=7888 heap=.../.../.../.../.../...
PHASE7_CONFIG_CAPACITY_PASS_V1
```

Alle sechs Heapwerte müssen mindestens 32 KiB betragen. Nur der letzte exakte
Token gilt als Erfolg. Danach zuerst die sechs Testdateien als abwesend
bestätigen und anschließend **nur** die oben einzeln übertragenen `.mpy`-
Dateien sowie deren leere isolierte Verzeichnisse entfernen. Keine bestehenden
Projektdateien und keine A/B-Produktionsdateien löschen.

Der reale Lauf vom 11. August 2026 bestand mit 7.888 Bytes, 32 Timern und acht
WLAN-Profilen. Die Heapwerte waren
`158688/61312/51872/55488/55520/50528`. Testdateien und isolierte Closure waren
danach vollständig abwesend. Die Evidenz steht in
`captures/2026-08-11-phase7-config-capacity-esp32-smoke.md`.

## Stufe B.5 – Phase-7-WLAN-Funk, weiterhin ohne weitere Hardware

Diese Stufe ist der erste ausdrücklich funkaktive Test. Voraussetzungen:

- Board nur über USB versorgt
- keine Heizung, kein Pegelwandler, kein UART-Jumper, keine Sensoren und keine
  RTC angeschlossen
- 1-Wire, I2C und Autoterm-Protokoll-TX bleiben verriegelt
- der kurzlebige Test-AP darf für wenige Sekunden sichtbar sein

Nur die folgende Allowlist übertragen. `main.py` bleibt passiv und wird nicht
ersetzt:

```bash
mpremote connect PORT mkdir :app
mpremote connect PORT mkdir :hardware
mpremote connect PORT mkdir :tools
mpremote connect PORT cp board_config.py :
mpremote connect PORT cp app/__init__.py app/network_configuration.py app/network_manager.py :app/
mpremote connect PORT cp hardware/__init__.py hardware/micropython_wifi.py :hardware/
mpremote connect PORT cp tools/__init__.py tools/phase7_network_smoke.py :tools/
```

Bei jedem Kopierfehler stoppen. Der Import allein darf keinen Funk öffnen:

```bash
mpremote connect PORT reset
mpremote connect PORT exec "import network; import tools.phase7_network_smoke; print('RADIO_BEFORE', network.WLAN(network.WLAN.IF_STA).active(), network.WLAN(network.WLAN.IF_AP).active())"
```

Erwartet wird `RADIO_BEFORE False False`. Den begrenzten Lauf danach nur mit
der exakten Bestätigung starten:

```bash
mpremote connect PORT exec "import tools.phase7_network_smoke as s; s.run(s.RADIO_SMOKE_CONFIRMATION, 1)"
```

Erwartete Abschlussausgabe:

```text
PHASE 7 WIFI RADIO SMOKE PASS: 1/1
ap_ssid=Landy Heater
ap_ip=192.168.4.1
station_attempts=1
radio_cleanup_confirmed=True
PHASE7_WIFI_RADIO_SMOKE_PASS_V1
```

Der Runner setzt die WLAN-Freigabe ausschließlich RAM-lokal, öffnet zuerst
den WPA2-AP, versucht genau ein absichtlich nicht vorhandenes STA-Netz und
prüft in `finally` beide Interfaces als ausgeschaltet. Zugangsdaten dürfen in
keiner Ausgabe erscheinen. Danach unabhängig kontrollieren:

```bash
mpremote connect PORT exec "import network, board_config, hardware.micropython_wifi as w; print('RADIO_STATE', network.WLAN(network.WLAN.IF_STA).active(), network.WLAN(network.WLAN.IF_AP).active()); print('WIFI_LOCK', board_config.WIFI_RADIO_APPROVED, w._WIFI_LEASED, w._WIFI_LEASE_POISONED)"
mpremote connect PORT reset
```

Erwartet werden zweimal `False` für den Funk und dreimal `False` für Freigabe,
Lease und Poison-Latch. Nach dem Reset müssen der passive Safe-Boot und erneut
beide inaktiven Interfaces bestätigt werden.

Der reale DFR0654-Lauf vom 11. August 2026 bestand 1/1 mit der direkten
AP-Adresse `192.168.4.1`; Funk und Sperren waren vor und nach Reset vollständig
aus. Die Evidenz steht in
`captures/2026-08-11-phase7-wifi-radio-esp32-smoke.md`.

## Stufe B.6 – Manueller Phase-7-Handytest

Diese optionale Abnahme ergänzt Stufe B.5 um eine echte WPA2-Assoziation und
die am Handy sichtbare DHCP-Konfiguration. Alle Voraussetzungen aus B.5 gelten
unverändert. `boot.py` und `main.py` bleiben passiv; es wird weder HTTP noch ein
Captive Portal gestartet.

Nur den zusätzlichen import-inerten Runner übertragen:

```bash
mpremote connect PORT cp tools/phase7_phone_ap_smoke.py :tools/
mpremote connect PORT reset
mpremote connect PORT exec "import network, board_config; before=(network.WLAN(network.WLAN.IF_STA).active(), network.WLAN(network.WLAN.IF_AP).active()); import tools.phase7_phone_ap_smoke; after=(network.WLAN(network.WLAN.IF_STA).active(), network.WLAN(network.WLAN.IF_AP).active()); print('PHONE_SMOKE_IMPORT_PASS', before, after, board_config.WIFI_RADIO_APPROVED)"
```

Erwartet wird:

```text
PHONE_SMOKE_IMPORT_PASS (False, False) (False, False) False
```

Am Handy ein früher gespeichertes Netz `Landy Heater` zuerst vergessen und die
WLAN-Einstellungen geöffnet lassen. Das temporäre Passwort muss 12–63
druckbare ASCII-Zeichen lang, nur für diesen Lauf bestimmt und von jedem
Produktpasswort verschieden sein. Es wird ausdrücklich als Argument
übergeben, nicht gespeichert und niemals ausgegeben. Den folgenden Befehl erst
starten, wenn das Handy zur Auswahl des Netzes bereit ist; `TEMPORARY_PASSWORD`
durch das einmalige Testpasswort ersetzen:

```bash
mpremote connect PORT exec "from tools.phase7_phone_ap_smoke import run, PHONE_AP_CONFIRMATION; run(PHONE_AP_CONFIRMATION, 'TEMPORARY_PASSWORD', 150)"
```

Nach

```text
PHASE7_PHONE_AP_READY_V1
ssid=Landy Heater
ap_ip=192.168.4.1
```

am Handy `Landy Heater` auswählen, das temporäre Passwort eingeben und eine
Warnung „Kein Internet“ beziehungsweise „Trotzdem verbunden bleiben“
bestätigen. Erwartete Netzwerkdetails am Handy:

- IPv4-Adresse `192.168.4.x`, aber nicht `.1`
- Subnetzmaske `255.255.255.0` beziehungsweise `/24`
- Router/Gateway `192.168.4.1`

Keine Webseite aufrufen: Dieser Phase-7-Runner startet weiterhin keinen
HTTP-/REST-Server. Die inzwischen implementierte Phase-8-Software wird davon
nicht automatisch aktiviert und hat eine eigene USB-only-Stufe B.7.
`heater.local` ist im AP-only-Betrieb mit MicroPython 1.28 ebenfalls nicht zu
erwarten. Der Runner verlangt genau einen Client über drei frische AP-Messungen
und anschließend weitere 30 Sekunden stabile Verbindung. Erst nach
vollständigem AP-/STA-Cleanup darf die letzte Zeile lauten:

```text
PHASE7_PHONE_AP_CLIENT_SEEN_V1
clients=1
PHONE_CLIENT_CONFIRMED clients=1
radio_cleanup_confirmed=True
PHASE7_PHONE_AP_SMOKE_PASS_V1
```

Bei Timeout, zusätzlichem Client, AP-Ausfall, Abbruch oder Cleanup-Fehler darf
nur `PHASE7_PHONE_AP_SMOKE_FAIL_V1`, niemals der Pass-Token erscheinen. Danach
unabhängig beide Interfaces und Sperren wie in B.5 prüfen, Hardware-Reset
ausführen und Safe-Boot bestätigen. Das temporäre WLAN am Handy anschließend
wieder vergessen.

Der reale DFR0654-Lauf vom 11. August 2026 bestand. Der ESP32 bestätigte genau
einen Client über drei frische Prüfungen und weitere 30 Sekunden; das Handy
zeigte `192.168.4.2`, Subnetz `255.255.255.0` und Router `192.168.4.1`.
Anschließend waren AP, STA, Freigabe, Lease und Poison-Latch vor und nach Reset
jeweils `False`; der passive Safe-Boot blieb unverändert. Die vollständige
Evidenz steht in
`captures/2026-08-11-phase7-phone-ap-esp32-smoke.md`.

## Stufe B.7 – Phase-8-REST, weiterhin nur USB

**Status: am 11. August 2026 auf dem realen DFR0654 bestanden.** Diese
Stufe prüft den hardwarefreien JSON-/HTTP-/Security-/Rate-Limit-/REST- und
Fake-Socket-Pfad. Sie startet weder den echten WLAN-Funk noch einen echten
Listener und kann keinen Heizungsbefehl senden. Es gelten unverändert alle
Voraussetzungen aus B.1: Board ausschließlich über USB, alle GPIOs frei, kein
UART-Jumper, keine Heizung, keine Sensoren, keine RTC, kein Pegelwandler und
kein 12-V-Anschluss. `boot.py` und `main.py` bleiben passiv und werden nicht
ersetzt.

Wie in B.4 wird ausschließlich der offizielle, exakt zu MicroPython 1.28.0
passende `mpy-cross` mit `-march=xtensawin` verwendet. Die Closure kommt in das
neue, isolierte Boardverzeichnis `/phase8_usb_rest_mpy_v1`. Existiert dieses
Verzeichnis vor dem Lauf bereits, stoppen und zuerst seinen Inhalt prüfen;
nicht mit einer alten oder unvollständigen Closure mischen.

Im lokalen Projektordner eine temporäre Build-Ablage anlegen und nur die
folgende Allowlist kompilieren:

```bash
PHASE8_BUILD_DIR="$(mktemp -d)"
MPY_CROSS=/EXAKTER/PFAD/ZU/v1.28.0/mpy-cross
PHASE8_SOURCES=(
  adapters/__init__.py
  adapters/micropython_http_server.py
  app/__init__.py
  app/application_state.py
  app/configuration_api_gateway.py
  app/manual_control_gateway.py
  app/network_configuration.py
  app/rest_application.py
  app/rest_composition.py
  app/scheduler.py
  app/temperature_manager.py
  services/__init__.py
  services/config_manager.py
  services/configuration_errors.py
  services/http_protocol.py
  services/rest_rate_limiter.py
  services/rest_security.py
  services/strict_json.py
  services/time_service.py
  tools/__init__.py
  tools/phase8_rest_smoke.py
)
for SOURCE in "${PHASE8_SOURCES[@]}"; do
  TARGET="$PHASE8_BUILD_DIR/${SOURCE%.py}.mpy"
  mkdir -p "$(dirname "$TARGET")"
  "$MPY_CROSS" -march=xtensawin -o "$TARGET" "$SOURCE" || exit 1
done
"$MPY_CROSS" --version
```

Erwartet werden MicroPython 1.28.0 und mpy v6.3. In dieser Allowlist befinden
sich weder `board_config.py`, `boot.py`, `main.py`, `hardware/` noch
`protocol/`. `app/network_configuration.py` ist lediglich der hardwarefreie
Schema-Validator; `app/network_manager.py`, die MicroPython-`network`-API und
der Funkadapter werden nicht übertragen oder importiert.

Ziel und Anschluss erneut bestätigen, dann ausschließlich die isolierte
Closure übertragen:

```bash
mpremote connect list
mpremote connect PORT exec "import sys, os; print(sys.implementation); print(os.uname())"
mpremote connect PORT mkdir :phase8_usb_rest_mpy_v1
mpremote connect PORT mkdir :phase8_usb_rest_mpy_v1/adapters
mpremote connect PORT mkdir :phase8_usb_rest_mpy_v1/app
mpremote connect PORT mkdir :phase8_usb_rest_mpy_v1/services
mpremote connect PORT mkdir :phase8_usb_rest_mpy_v1/tools
mpremote connect PORT cp "$PHASE8_BUILD_DIR"/adapters/*.mpy :phase8_usb_rest_mpy_v1/adapters/
mpremote connect PORT cp "$PHASE8_BUILD_DIR"/app/*.mpy :phase8_usb_rest_mpy_v1/app/
mpremote connect PORT cp "$PHASE8_BUILD_DIR"/services/*.mpy :phase8_usb_rest_mpy_v1/services/
mpremote connect PORT cp "$PHASE8_BUILD_DIR"/tools/*.mpy :phase8_usb_rest_mpy_v1/tools/
```

Bei jedem `mkdir`- oder `cp`-Fehler sofort stoppen. Nach einem Reset den
isolierten Pfad voranstellen und zuerst nur die Import-Inertheit prüfen. Die
verbotenen Hardwaremodule dürfen durch den Import nicht neu hinzukommen:

```bash
mpremote connect PORT reset
mpremote connect PORT exec "import sys; sys.path.insert(0, '/phase8_usb_rest_mpy_v1'); forbidden=lambda: tuple(sorted(k for k in sys.modules if k in ('machine','network','board_config') or k.startswith('hardware') or k.startswith('protocol'))); before=forbidden(); import tools.phase8_rest_smoke as s; after=forbidden(); print('PHASE8_REST_IMPORT_PASS', tuple(k for k in after if k not in before), s.SOFTWARE_ONLY_CONFIRMATION)"
```

Erwartet werden keine neu importierten verbotenen Module und die Confirmation
`PHASE8_USB_REST_SMOKE_CONFIRM_V1`. Erst danach den Test bewusst mit der
exportierten Konstante starten:

```bash
mpremote connect PORT exec "import sys; sys.path.insert(0, '/phase8_usb_rest_mpy_v1'); import tools.phase8_rest_smoke as s; s.run(s.SOFTWARE_ONLY_CONFIRMATION)"
```

Der Runner verwendet ausschließlich künstliche Anwendungsmodelle und Fake-
Sockets. Er prüft die JSON-String- und HTTP-Body-Grenzen, die echte
REST-Composition, AP-Peer-Weitergabe, den CSRF-geschützten körperlosen Stop samt
Rate-Limit-Ausnahme, einen fail-closed abgewiesenen manuellen Start, die
256-Byte-Socketbudgets, höchstens eine Socketaktion je `step()` sowie das
vollständige Server-/CSRF-Cleanup. Der Standardlauf verwendet vier
Wiederholungen; seine Abschlussform lautet:

```text
PHASE 8 USB REST PASS: iterations=4 requests=12 completed=12 peer_count=1 step_actions=1 heap=141152/43984/42272/42288
PHASE8_USB_REST_SMOKE_PASS_V1
```

Der Confirmation-Text allein ist kein Erfolg. Der reale Lauf gab den exakten
Pass-Token erst nach verifiziertem Server-/CSRF-Cleanup aus. Die vollständige
Evidenz, einschließlich der zwei zuvor fail-closed entdeckten MicroPython-
Import-/Exception-Kompatibilitätslücken, steht in
`captures/2026-08-11-phase8-rest-esp32-smoke.md`.

Nach Pass oder Fehler genau die hochgeladene Allowlist und anschließend ihre
leeren isolierten Verzeichnisse entfernen:

```bash
for SOURCE in "${PHASE8_SOURCES[@]}"; do
  mpremote connect PORT rm ":phase8_usb_rest_mpy_v1/${SOURCE%.py}.mpy" || exit 1
done
mpremote connect PORT rmdir :phase8_usb_rest_mpy_v1/adapters
mpremote connect PORT rmdir :phase8_usb_rest_mpy_v1/app
mpremote connect PORT rmdir :phase8_usb_rest_mpy_v1/services
mpremote connect PORT rmdir :phase8_usb_rest_mpy_v1/tools
mpremote connect PORT rmdir :phase8_usb_rest_mpy_v1
mpremote connect PORT exec "import os; print('PHASE8_REST_CLEANUP_PASS', 'phase8_usb_rest_mpy_v1' not in os.listdir('/'))"
mpremote connect PORT reset
```

Erwartet werden `PHASE8_REST_CLEANUP_PASS True` und danach unverändert:

```text
Landy Heater safe boot; UART inactive; protocol TX disabled
```

Auch der bestandene B.7-Lauf aktiviert REST nicht produktiv. Er importierte
kein WLAN und verwendete Fake-Sockets; deshalb bleibt er unverändert gültige
Komponenten-, aber keine gemeinsame Wi-Fi-/HTTP-Produktabnahme.

## Stufe B.8 – Gemeinsame Wi-Fi-/HTTP-Kapazität und Handytest

**Status: Minimaler Frozen-AP/HTTP-Handytest BESTANDEN; vollständige
Produktzielabnahme weiterhin OFFEN.** Zugangsdaten werden in dieser
Dokumentation bewusst weder wiederholt noch rekonstruiert.

Der erste, historische kombinierte Runner lud Wi-Fi und HTTP vor der
AP-Konfiguration. Nach diesem eager Import waren ungefähr 48.112 Bytes frei;
eine folgende Allokation im AP-Konfigurationspfad konnte nicht abgeschlossen
werden. Dieser Lauf erreichte weder READY noch CLIENT oder PASS und belegt
keinen AP-, DHCP- oder HTTP-Erfolg.

Die sichere Reihenfolge lautet deshalb AP-first und lazy HTTP:

1. nur Wi-Fi-Hülle und NetworkManager laden;
2. Wi-Fi-Factory öffnen, AP konfigurieren und `192.168.4.1` bestätigen;
3. erst danach HTTP-Parser, JSON und Socketserver importieren und an die
   bestätigte AP-Adresse binden;
4. bei jedem Fehler zuerst den HTTP-Eigentümer, danach NetworkManager/Port
   schließen und zuletzt die RAM-Freigabe zurücknehmen.

Ein historischer direkter Produktions-Porttest maß 114.304 Bytes freien Heap
nach der Factory und 105.984 Bytes nach AP-Start. Der AP sah genau einen
assoziierten Client, und die beiden protokollierten Cleanup-Prüfungen waren
`True/True`. Dieser Teiltest bestätigte weder DHCP noch HTTP.

Nach der Lazy-Load-Korrektur erreichte der minimale kombinierte Runner einen
echten AP und echten HTTP-Listener und gab aus:

```text
PHASE8_PHONE_HTTP_READY_V1
```

Dieser frühere Lauf endete noch ohne CLIENT- oder PASS-Token und bleibt als
Nicht-Abnahme dokumentiert. Nach dem ausdrücklich freigegebenen Flash der
Custom-Frozen-Firmware wurde derselbe enge AP-first-Ablauf wiederholt. Für die
Prüfung hatte `.frozen` Vorrang vor den vorhandenen Dateisystemkopien; der
normale passive Bootpfad wurde dadurch nicht aktiviert. Diesmal erschienen:

```text
PHASE8_PHONE_HTTP_READY_V1
PHASE8_PHONE_HTTP_CLIENT_SEEN_V1
clients=1
http_response_completed=True
http_cleanup_confirmed=True
radio_cleanup_confirmed=True
PHASE8_PHONE_HTTP_SMOKE_PASS_V1
```

Das Handy wurde als Peer `192.168.4.2` validiert. Eine Anfrage auf den festen
Read-only-Radio-Check war gültig, eine weitere wurde abgewiesen und beide
Antworten wurden vollständig geschrieben. Die freien Heapwerte betrugen
102.400 Bytes vor den Imports, 83.184 nach dem Wi-Fi-Import, 81.840 nach
AP-Ready, 76.240 nach dem lazy HTTP-Import und 75.072 nach dem geordneten
Cleanup. Ein eigener Messpunkt unmittelbar nach der Antwort und vor dem
Cleanup wurde nicht erhoben.

Der Runner schloss zuerst HTTP/Sockets und danach NetworkManager/WLAN-Port,
gab Lease und temporäre RAM-Freigabe frei und bestätigte AP und STA als
inaktiv. Eine unabhängige Nachprüfung entfernte die drei temporären `.mpy`-
Dateien vollständig. Der folgende Hard Reset zeigte erneut exakt:

```text
Landy Heater safe boot; UART inactive; protocol TX disabled
```

Vor dem Frozen-Build war die vollständige Produktclosure P1-blockiert: Bereits
Configuration + Storage + REST umfassten ungefähr 155,9 KiB dynamisch zu
ladenden kompilierten Code, noch bevor NetworkManager, Heater/Protocol und
ihre Liveobjekte hinzukamen. Das Einfrieren der 40 Projektmodule ändert diese
Ausgangslage; der historische Befund allein beweist deshalb keinen
post-frozen Kapazitätsfehler.

Der bestandene Handytest verwendete zwar den produktiven NetworkManager, den
MicroPython-WLAN-Port und `MicroPythonHTTPServer`, aber einen festen
Read-only-Prüfhandler statt `RestApplication`, ConfigManager und Storage. Er
ist daher keine gemeinsame Produktabnahme. Die Host-Regressionssuite bestand
1000/1000, kann diese fehlende Zielabnahme ebenfalls nicht ersetzen. `main.py`
bleibt passiv und Phase 9 ist nicht freigegeben.

Als Nächstes muss diese Closure in ihrer vorgesehenen Form gemeinsam geprüft
und nur bei einem neuen Kapazitätsfehler weiter reduziert oder partitioniert
werden. Die Abnahme muss in einem einzigen Lauf mindestens 32 KiB freien Heap
an allen Import-/AP-/HTTP-/Request-/Cleanup-Checkpoints, die
vorgesehene Configuration-/Storage-/`RestApplication`-Zusammensetzung, einen
realen AP-Peer, eine vollständig geschriebene produktive HTTP-Antwort und den
geordneten Cleanup bestätigen. Der enge Frozen-Pass steht in
`captures/2026-08-11-phase8-frozen-phone-http-esp32-smoke.md`. Die früheren
eager-/ready-only-Fehlschläge und der historische Produktblocker bleiben in
`captures/2026-08-11-phase8-wifi-http-capacity-blocked.md` erhalten.

## Stufe C – UART2-Loopback ohne Heizung

Erst nach erfolgreichem MicroPython-Smoke-Test:

1. USB trennen.
2. Ausschließlich `D10/17` mit `D11/16` verbinden.
3. Nochmals sicherstellen, dass keine Heizung und keine andere Schaltung
   angeschlossen ist.
4. USB-Datenkabel wieder verbinden und den Anschluss erneut mit
   `mpremote connect list` kontrollieren. Falls sich sein Name geändert hat,
   ab jetzt den neuen Wert als `PORT` verwenden.
5. Nur die benötigten `.py`-Dateien auf das Board übertragen. Dadurch gelangen
   keine lokalen `__pycache__`-Ordner oder CPython-`.pyc`-Dateien auf das Board:

```bash
mpremote connect PORT cp board_config.py boot.py main.py :
mpremote connect PORT mkdir :protocol :tools
mpremote connect PORT cp protocol/__init__.py protocol/crc16.py protocol/autoterm_frames.py protocol/autoterm_protocol.py protocol/uart_transport.py :protocol/
mpremote connect PORT cp tools/__init__.py tools/uart_loopback_smoke.py :tools/
```

`mkdir` ist für den ersten Lauf auf dem frisch geflashten Board gedacht. Falls
ein abgebrochener Ablauf wiederholt wird und `protocol` oder `tools` bereits
existiert, ist die entsprechende „already exists“-Meldung kein Grund, etwas
zu löschen; mit den beiden `cp`-Befehlen fortfahren.

6. Den Test bewusst starten:

```bash
mpremote connect PORT exec "from tools.uart_loopback_smoke import run, LOOPBACK_CONFIRMATION; run(LOOPBACK_CONFIRMATION)"
```

Alternativ zuerst die REPL öffnen:

```bash
mpremote connect PORT repl
```

Dann dort aufrufen:

```python
from tools.uart_loopback_smoke import run, LOOPBACK_CONFIRMATION
run(LOOPBACK_CONFIRMATION)
```

Erwartete Ausgabe:

```text
DFR0654 UART2 loopback PASS: 16 bytes
```

Das verwendete ASCII-Muster beginnt nicht mit dem Autoterm-Marker `0xAA`.
Der Test läuft niemals beim Import oder beim Booten automatisch. Der reguläre
UART-Transport bleibt durch `UART_PROTOCOL_TX_ENABLED = False` weiterhin für
jede Protokollübertragung gesperrt.

Der Bestätigungstext ist nur eine bewusste Bedienhandlung; er kann die reale
Verdrahtung nicht erkennen. Nach `PASS` oder einem Fehler USB wieder trennen,
den Jumper zwischen GPIO17 und GPIO16 sofort entfernen und erst danach das
Board erneut verbinden. Der Jumper darf niemals in eine spätere
Heizungs-/Capture-Phase übernommen werden.

## Noch nicht freigegeben

- direkte Verbindung mit der Heizung
- 5-V-Signale ohne exakt identifizierten und elektrisch geprüften
  5→3,3-V-RX-Pfad
- Verbindung mit 12 V oder Bordnetz
- INIT, STATUS, START oder SHUTDOWN
- Sensor- und RTC-Pins
- passiver Heizungsmitschnitt, solange der vorhandene 3,3-V-RX-Knoten und die
  gemeinsame Signalmasse nicht eindeutig identifiziert und gemessen sind

## Stufe D – RX-only-Softwaretest, weiterhin nur USB

Diese Stufe darf ausschließlich nach entferntem Loopback-Jumper durchgeführt
werden. Heizung, Pegelwandler, 12 V und sämtliche GPIO-Leitungen bleiben
getrennt. Das Board zuerst per Reset neu starten, damit UART2 exklusiv und in
einem bekannten Zustand übernommen wird.

Die neuen Dateien ohne lokale Cache-Artefakte übertragen:

```bash
mpremote connect PORT mkdir :services
mpremote connect PORT cp board_config.py :
mpremote connect PORT cp protocol/__init__.py protocol/rx_only_transport.py :protocol/
mpremote connect PORT cp services/__init__.py services/protocol_capture.py :services/
mpremote connect PORT cp tools/uart_rx_capture.py :tools/
```

Falls `services` bereits existiert, ist die entsprechende Meldung kein Grund,
etwas zu löschen; mit den `cp`-Befehlen fortfahren. Danach zunächst nur den
hardwarefreien Import prüfen:

```bash
mpremote connect PORT exec "import board_config; import protocol.rx_only_transport; import services.protocol_capture; import tools.uart_rx_capture; print('RX-only modules import PASS')"
```

Der eigentliche RX-only-Factory-Smoke-Test wird anschließend kontrolliert
ausgeführt. Er öffnet UART2 ohne öffentliche Schreib-API, neutralisiert GPIO17
sofort wieder als Eingang, pollt kurz und deaktiviert den UART erneut. Ein
unbeschalteter GPIO16 ist elektrisch frei schwebend und kann dabei einzelne
Störbytes liefern; entscheidend sind der fehlerfreie Cleanup, keine Drops und
kein UART-Schreibvorgang beziehungsweise Datenframe. GPIO17 wird anschließend
wieder als Eingang gesetzt; seine tatsächliche elektrische Inaktivität wäre
nur messbar. Ein echter Heizungsmitschnitt bleibt bis zur Prüfung der
vorhandenen Verkabelung gesperrt.

Wichtig: Der Bestätigungstext des späteren Capture-Werkzeugs ist nur ein
Bedien-Guard. Er kann nicht erkennen, ob GPIO17 wirklich frei ist. Die
physische Trennung ist die primäre Schutzbarriere.
