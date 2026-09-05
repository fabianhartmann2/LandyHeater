# Phase 11 – Events, Diagnose und Capture-Export

Stand: 2026-09-05. **Phase 11 ist software- und zielseitig abgenommen.** Der
Softwareumfang ist durch die vollständige Host-Testmatrix geprüft. Zwei
saubere Firmware-Builds sind in 15 Ausgaben bytegleich; das zurückbehaltene
Artefakt bestand die Offline-Gates. Der autorisierte App-only-Flash, die
vollständige Rücklesung, der passive USB-Starttest und der reale Handyablauf
für Diagnose, Capture und Export sind bestanden.

## Funktionsumfang

`DiagnosticsHub` ist ein hardwarefreier, injizierter Dienst. Er sammelt in
einem kooperativen Schritt Kopien aus bis zu acht vorhandenen Ereignisquellen
und aus der bereits begrenzten UART-Aktivitätsqueue. Der Aufzeichnungspfad
ruft weder `poll()` noch `send_frame()` auf und besitzt keine Hardware.

Die festen Standardgrenzen sind:

- 200 Ereignisse;
- 64 aktuelle Protokollframes;
- 128 Einträge je benanntem Capture;
- höchstens 16 Ereignisse oder vier Protokoll-/Capture-Einträge je Antwort;
- höchstens vier UART-Aktivitäten je Sammelschritt und 512 Rohbytes je Frame.

Bei Überlauf wird der älteste Ringpuffereintrag verworfen beziehungsweise ein
Capture als unvollständig markiert. Es gibt kein dynamisches Hochskalieren.
Speicher- oder Quellenfehler erhöhen begrenzte Zähler und verlassen den
Diagnosepfad nicht in Richtung Heizungssteuerung.

## Datenschutz- und Sicherheitsgrenze

Ereignisse übernehmen nur begrenzte, JSON-kompatible Daten. Schlüssel für
Passwörter, Zugangsdaten, Secrets, Tokens sowie freie Meldungs-, Fehler- und
Begründungstexte werden vor dem Speichern verworfen. Captures liegen nur im
RAM und werden beim Deinitialisieren entfernt.

Lesezugriffe funktionieren über AP und den schreibgeschützten
Stationslistener. Start und Stopp eines Captures bleiben AP-only und benötigen
dieselben Host-, Origin- und CSRF-Prüfungen wie andere Mutationen. Ein
Stationsclient kann weder einen Capture starten noch stoppen.

## REST-Erweiterungen

```text
GET    /api/v1/events?after=...&limit=...
GET    /api/v1/protocol-log?after=...&limit=...
GET    /api/v1/capture
POST   /api/v1/capture
DELETE /api/v1/capture
GET    /api/v1/capture/export?offset=...&limit=...
```

Ereignis- und Protokollseiten verwenden monotone Sequenzen. Ein Client erkennt
am `gap`-Merkmal, ob sein Cursor hinter dem inzwischen überschriebenen
ältesten Eintrag liegt. Capture-Export ist erst nach dem Stoppen möglich und
enthält Schema, Version, Bezeichnung, Start-/Endzeit, öffentliche
Konfigurationsmetadaten, Drop-Status sowie Ereignis- und Protokolldatensätze.

`GET /api/v1/diagnostics` enthält zusätzlich Laufzeit, freien Heap und einen
Phase-11-Snapshot mit Kapazitäten, Füllständen, Drop-/Fehlerzählern und
Capture-Zustand. Der bestehende DTO-Pfad bleibt frei von CSRF-Token,
WLAN-Passwörtern und UART-Rohdaten.

## Browseroberfläche und Export

Die responsive Diagnoseansicht wird erst beim ersten Öffnen nachgeladen. Nur
solange sie sichtbar ist, wird alle zwei Sekunden eine kleine kombinierte
Leseanfrage ausgeführt. Bewusst gibt es weder WebSocket noch parallele
Anfragen. Mehrseitige Exporte pausieren die Hintergrundabfragen und warten
zwischen den Seiten, damit der bestehende REST-Anfrageschutz nicht ausgelöst
wird. Die Oberfläche verwendet ausschließlich lokale Assets und setzt externe
Werte nur über `textContent` ein.

Verfügbar sind:

- Systemübersicht mit Kommunikation, Heizzustand, letztem Status, Station,
  Laufzeit, freiem Speicher und Pufferständen;
- aktuelle Ereignisse und RX/TX-Protokollframes;
- Start/Stopp eines benannten Captures;
- Diagnose- und Ereignisexport als JSON;
- Capture-Export als JSON und zeilenorientiertes NDJSON.

Der Browser sammelt den Export aus begrenzten Seiten. Der Server erzeugt nie
eine unbeschränkt große Antwort.

## Abnahmegrenze

Hostseitig werden Ringüberlauf, Cursorlücken, Kopiertrennung, Redaction,
Quellenrotation, UART-Entkopplung, Capture-Lifecycle, unvollständige Captures,
Response-Größen, Security-/Methodengrenzen, OOM-Verhalten, Assetgrößen und die
lokale UI geprüft.

Das spätere DFR0975-U-Zielgate darf ohne angeschlossene Heizung nur Speicher,
UI/API, künstlich eingespeiste Testereignisse und vollständigen Cleanup
prüfen. Ein echter elektrischer UART-/Heizungs-Capture bleibt gemäß Phasenplan
Phase 13. Automatischer Produktstart und Hardwareausgänge bleiben gesperrt.

Der vorbereitete App-only-Kandidat liegt unter
`firmware/phase11_frozen/artifacts/micropython.bin`: 2.086.960 Byte ab
`0x10000`, SHA-256
`274234961f43551526b843ca7b27b3ead594cb5e93bf079b39f4ea838ab2c566`.
Diese Angabe dokumentiert das Artefakt und ist keine erneute Flashfreigabe.

## Reale Zielabnahme

Der Eigentümer gab exakt dieses Artefakt für einen App-only-Flash bei
`0x10000` ohne Full Erase frei. Es wurden 2.086.960 Bytes geschrieben; die
anschließende unabhängige Rücklesung war byteidentisch und hatte erneut den
freigegebenen SHA-256. Bootloader, Partitionstabelle und VFS wurden nicht
geschrieben.

Nach manuellem `RST` bestätigte der passive USB-Test MicroPython 1.28.0, die
DFR0975-U-N16R8-Identität, beide WLAN-Schnittstellen als inaktiv, rund 8,3 MB
freien Heap sowie Ereignis-/Protokollpuffer einschließlich Passwort-Redaction
und vollständiger RAM-Bereinigung.

Der anschließende AP-only-Handytest verwendete unverändert `Landy Heater` und
das vereinbarte Testpasswort. Die responsive Diagnoseansicht, 91 kombinierte
Live-Antworten, ein benannter Capture, ein künstliches Ereignis, ein
künstlicher RX-Rahmen, Stop und JSON-Export wurden erfolgreich verarbeitet.
Alle 162 HTTP-Anfragen wurden abgeschlossen; Parser- und Serverfehler blieben
null. Ein Gestaltungselement kam zunächst aus dem Browsercache und wurde beim
Reload ebenfalls übertragen.

Der funktional vollständige Lauf endete mit einem falschen negativen Urteil
des Zielrunners: Dessen wiederverwendeter Wire-Beobachter benennt nur
GET-Anfragen, die Abschlussbedingung verlangte jedoch zusätzlich einen
benannten POST/DELETE-Pfad. Die bereits separat gezählten und erfolgreichen
Capture-Mutationen waren davon nicht betroffen. Die falsche Bedingung und ein
anfänglich falscher Fragment-Sollpfad wurden korrigiert und durch neue
Regressionstests abgedeckt. Eine Wiederholung der bereits vollständig
beobachteten Handyaktionen ist nicht erforderlich.

Die unabhängige USB-Nachkontrolle meldete beide WLAN-Schnittstellen inaktiv,
8.320.176 Bytes freien Heap und keine verbliebenen isolierten Testdateien.
Heater-UART, I2C, 1-Wire, Produktstart und elektrische Peripherie blieben
gesperrt. Der vollständige Nachweis steht in
`captures/2026-09-05-dfr0975u-phase11-diagnostics-gate.md`.
