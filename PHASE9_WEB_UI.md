# Phase 9 – Web UI

Status: **hostseitig implementiert und geprüft; reproduzierbarer DFR0975-U-
Build und statische Artefaktprüfung bestanden; Flashfreigabe und Zielabnahme
stehen aus**.

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

Für Phase 9 sind weiterhin erforderlich:

1. neue hashgebundene Freigabe für den App-Flash ohne Erase bei `0x10000`:
   `a228d115cc2aba8569ddad3a46b9c038ab5f06e159bae3d4ded955345b6485e6`;
2. App-Flash und vollständige Schreibverifikation; Bootloader und
   Partitionstabelle bleiben bytegleich zum abgenommenen Phase-8-Stand;
3. ein begrenzter Zieltest mit einem Port-80-Listener, realem UI- und
   API-Abruf, Heap-Gates und vollständigem Cleanup.

Bis dahin bleiben `boot.py` und `main.py` passiv. Es gibt keinen Auto-Start,
keinen Flash und keine Freigabe von UART, Heizung, RTC/I2C oder 1-Wire.
