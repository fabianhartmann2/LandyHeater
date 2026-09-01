# Phase 9 – Web UI

Status: **hostseitig und auf dem DFR0975-U abgeschlossen: reproduzierbarer
Build, statische Artefaktprüfung, hashgebundener App-Flash, vollständige
Rückleseprüfung und realer Ein-Listener-UI/API-Handytest bestanden**.

## Umfang

Die Phase-9-Oberfläche wird vollständig als eingefrorene Ressource vom ESP32
bereitgestellt. Sie verwendet keine CDN-Abhängigkeiten, Webfonts oder
Frameworks. HTML, CSS und Vanilla-JavaScript liegen lesbar unter `web/` und
werden mit `tools/build_web_assets.py` deterministisch nach
`app/web_assets.py` überführt.

Ein einziger Listener auf Port 80 bedient sowohl die Oberfläche als auch
`/api/v1`. `Phase9WebApplication` delegiert API-Anfragen unverändert an die
Phase-8-REST-Anwendung. Statische Dateien sind fest allowgelistet,
größenbegrenzt und durch Host-Prüfung, Content-Security-Policy sowie weitere
Browser-Sicherheitsheader geschützt.

Die Oberfläche enthält:

- mobile-first Home mit Istzustand, drei Temperaturen, Restlaufzeit,
  nächstem Timer, Warnungen, Quick Start und konfiguriertem Start;
- sichere Aktionen für Stop, `+15 min` und Zieltemperaturänderung;
- Timer-Liste mit Hinzufügen, Bearbeiten, Aktivieren/Deaktivieren und Löschen;
- Einstellungen für Heizung und Laufzeit sowie lesende Übersichten für
  Netzwerk, Sensoren, Zeit, System und Diagnose;
- zentrale deutsche und englische Übersetzungen;
- responsive Desktopdarstellung, Systemfonts und automatische Hell-/Dunkel-
  Darstellung.

Netzwerkzugangsdaten und Sensorzuordnungen bleiben in Phase 9 bewusst
schreibgeschützt. Diese Änderungen gehören zum Setup-Assistenten in Phase 10.

## Neue API-Erweiterung

`PATCH /api/v1/heater/session` erlaubt ausschließlich:

- eine Zieltemperatur von 5–30 °C in einer bestätigten laufenden
  Temperatursession;
- eine Laufzeitverlängerung um exakt 15 Minuten innerhalb des globalen
  Maximums.

Die Änderung benötigt dieselben Host-, Origin-, CSRF-, Konfigurations- und
Requested-State-Revisionsgrenzen wie andere Mutationen. Sie ändert nur
Requested State und die bestätigte Session. Sie sendet keinen UART-Befehl,
wechselt keinen Modus und erlaubt keine Leistungsänderung während einer
Power-Session.

## UI-/API-Abnahmegrenze

Die folgenden automatisch beim UI-Start verwendeten Lese-Endpunkte wurden auf
dem realen DFR0975-U über den gemeinsamen Port-80-Listener vollständig
ausgeliefert:

- `GET /api/v1/security-context`;
- `GET /api/v1/status`;
- `GET /api/v1/settings`;
- `GET /api/v1/timers?offset=0&limit=8`.

Die UI-Anbindung der folgenden Aktionen ist durch die Hosttests einschließlich
Routing, Payload, CSRF-/Revisionsgrenzen und Fehlerpfaden geprüft:

- `POST /api/v1/heater/start` und `/api/v1/heater/quick-start`;
- `POST /api/v1/heater/stop`;
- `PATCH /api/v1/heater/session` für `+15 min` und Zieltemperatur;
- `PATCH /api/v1/settings`;
- `POST /api/v1/timers` sowie `PUT` und `DELETE` auf Timerressourcen;
- `GET /api/v1/diagnostics` über die ausdrückliche Diagnose-Schaltfläche.

Diese schreibenden Aktionen wurden im realen Phase-9-Handygate absichtlich
nicht ausgelöst: Heizung und Produktperipherie waren unverbunden, der Gate
erlaubte ausschließlich `GET` und bestätigte null Mutationen. Damit ist die
reale UI-/API-Leseintegration abgenommen; die Schreibintegration ist
softwareseitig abgenommen, aber reale Heizungsaktionen bleiben gesperrt und
ungetestet. Auch der automatische UI-/WLAN-Start nach einem Reset bleibt
deaktiviert.

## Prüfung und Freigabegrenzen

Der Quellstand wird unter CPython über die bestehenden HTTP-, REST-,
Controller- und Sicherheitsprüfungen sowie zusätzliche Web-UI-Vertragstests
geprüft. Die Oberfläche wurde mit lokalen realistischen API-Daten bei 390 px
und 1180 px Breite in Deutsch und Englisch gerendert; Navigation und
Browserkonsole waren fehlerfrei.

Der bisherige DFR0975-U-Binärstand bleibt das unveränderte, bereits
abgenommene Phase-8-Artefakt. Der neue Phase-9-Kandidat wurde zweimal sauber
und für alle 15 verglichenen Ausgaben bytegleich gebaut. ESP32-S3-Image,
16-MB-/Octal-PSRAM-Konfiguration, Partitionen, Offsets, Größenreserve und
kombinierter Binärinhalt wurden statisch geprüft. Die separaten Artefakte und
der vollständige Nachweis liegen unter `firmware/phase9_frozen/`.

Die verbleibenden Phase-9-Gates wurden anschließend bestanden:

1. hashgebundener App-Flash ohne Full Erase bei `0x10000`:
   `a228d115cc2aba8569ddad3a46b9c038ab5f06e159bae3d4ded955345b6485e6`;
2. vollständige Rückleseprüfung aller 2.020.592 App-Bytes;
3. ein begrenzter Zieltest mit einem Port-80-Listener, 9/9 vollständig
   ausgelieferten UI-Ressourcen, 4/4 automatischen API-Lesezugriffen, null
   Mutationen, allen Heap-Gates und vollständigem Cleanup.

`boot.py` und `main.py` bleiben weiterhin passiv. Phase 9 aktiviert keinen
Auto-Start und gibt weder UART, Heizung, RTC/I2C noch 1-Wire frei. Die
sanitisierte Zielevidenz steht in
`captures/2026-09-01-dfr0975u-phase9-web-ui-gate.md`.
