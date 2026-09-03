# Phase 10 – Setup Assistant

Stand: 2026-09-03. **Phase 10 ist einschließlich des realen
Konfigurations-/Timer-Ablaufs funktional auf dem DFR0975-U abgenommen; die
strikte Same-run-Prüfung jeder einzelnen HTTP-Ressource bleibt formal offen.**
Der reale Assistent akzeptierte genau eine gültige Mutation und bestätigte im
isolierten Speicher den exakten AP-Passwortwechsel sowie genau ein geschütztes
Stations-WLAN. Stations-DHCP, Anmeldung mit dem neuen AP-Passwort und die
dauerhafte Erstellung, Bearbeitung und Löschung eines inaktiven Timers wurden
anschließend auf demselben Board bestätigt. Der dabei entdeckte ungewollte
leere `DELETE`-Body wurde korrigiert; das neue Image wurde hashgebunden
app-only geschrieben, vollständig zurückgelesen und bestand den fortgesetzten
Zieltest samt Cleanup.

## Geführter Ablauf

Die Oberfläche bildet den festgelegten Ablauf mit neun Schritten ab:

1. Sprache (Deutsch/Englisch, lokal im Browser gespeichert)
2. Datum, Uhrzeit und bereits vorliegender RTC-Zustand
3. bis zu acht bekannte WLANs mit ausdrücklicher Passwort-/Sicherheitsauswahl
4. individuelles Passwort des festen AP `Landy Heater` mit ausdrücklicher
   Auswahl **beibehalten** oder **ersetzen**
5. vorhandene DS18B20-ROM-IDs und Rollenzuordnung
6. bereits beobachteter Autoterm-Kommunikationszustand
7. Quick-Start-Standardwerte und maximale Laufzeit
8. Zusammenfassung ohne Geheimnisse
9. ausdrückliche Bestätigung und atomarer Abschluss

Bei `system.setup_complete == false` öffnet die UI den Assistenten nach dem
ersten erfolgreichen Laden automatisch. Unter **Einstellungen → System** kann
er jederzeit manuell neu gestartet werden, ohne vorher die Konfiguration zu
löschen.

## API und Sicherheitsgrenzen

| Methode | Endpunkt | Wirkung |
|---|---|---|
| `GET` | `/api/v1/setup` | Nur vorhandene öffentliche Konfiguration und passive Laufzeitbeobachtungen lesen |
| `PUT` | `/api/v1/setup` | Vollständigen Assistentenabschluss in genau einem generationsgeschützten Commit speichern |

`PUT` ist wie alle Mutationen nur am AP-Eingang mit gültigem Origin, CSRF-Token
und aktuellem `If-Match` möglich. Die allgemeine
`PATCH /api/v1/settings`-Grenze bleibt für Netzwerkdaten geschlossen.

Passwörter sind ausschließlich write-only. Der Browser kann ein neues
Passwort übergeben oder ein schon vorhandenes Passwort durch die Aktion
`keep` unverändert übernehmen. Das privilegierte
`ConfigurationAPIGateway` kopiert es intern in den vollständigen Kandidaten.
Weder `GET`, die erfolgreiche `PUT`-Antwort, Diagnose noch Fehlertexte geben
Passwortfelder oder Passwortwerte zurück. AP-SSID `Landy Heater` und Hostname
`heater` bleiben unveränderliche Produktidentitäten.

## Eingabeprüfung im Browser

Der Assistent prüft nun jeden betroffenen Schritt vor **Weiter** und den
vollständigen Eingabesatz erneut vor dem Speichern. Dabei gelten dieselben
Grenzen wie im Server:

- SSID erforderlich, höchstens 32 UTF-8-Bytes, keine doppelte SSID;
- maximal acht Stationsprofile;
- geschütztes WLAN nur mit ausdrücklich gewähltem Passwortpfad;
- AP- und normale WLAN-Passwörter mit 8–63 druckbaren ASCII-Zeichen;
- für Stations-WLAN zusätzlich ein exakter 64-stelliger Hex-PSK;
- AP-Wiederholung muss übereinstimmen;
- Quick-Start-Modus, maximale Laufzeit 1–120 Minuten, Standardlaufzeit
  innerhalb dieses Maximums, Temperatur 5–30 °C beziehungsweise Leistung
  1–9;
- eindeutige Sensorrollen und ausdrückliche Bestätigung der Zusammenfassung.

Ein neues Stationsprofil startet bewusst als **geschützt / neues Passwort**.
Ein leeres Passwort wird nicht mehr stillschweigend als offenes WLAN
interpretiert; **offenes WLAN** muss ausdrücklich gewählt werden. Die
WLAN-Auswahl ist in diesem Stand manuell. Ein aktiver Scan ist nicht Teil
dieser Korrektur und benötigt später eine eigene Funk- und UX-Abnahme.

Der Abschluss ersetzt `heater`, `sensors`, `time` und `network` gemeinsam und
setzt erst danach `system.setup_complete = true`. Der normale
ConfigManager-Validator erzwingt unter anderem das individuelle AP-Passwort,
eindeutige ROM-IDs, höchstens acht eindeutige WLAN-Profile und gültige
Quick-Start-Grenzen. Ein persistierter Wechsel sperrt den alten Scheduler und
meldet `restart_required`.

## Hardwaregrenze in diesem Stand

Das DFR0975-U ist weiterhin ohne RTC, 1-Wire-Sensoren, Level-Interface oder
Heizung angeschlossen. Deshalb führt Phase 10 in diesem Stand bewusst keinen
aktiven 1-Wire-Scan und keinen UART-Test aus. `GET /api/v1/setup` zeigt nur
bereits im Laufzeitmodell vorhandene ROM-IDs, Temperaturen, RTC- und
Kommunikationswerte und kennzeichnet `active_probe_performed: false`.

Der Assistent speichert dafür ausschließlich die Zustände `reviewed` oder
`deferred`; er akzeptiert keine Behauptung `passed`. Im aktuellen Aufbau wird
in der Zusammenfassung jeweils **zurückgestellt** angezeigt. Das bedeutet:

- kein Hardwaretest wird fälschlich als bestanden dokumentiert;
- Phase 10 öffnet weder I2C/RTC, 1-Wire noch UART;
- die elektrische Prüfung, aktive Discovery und reale Autoterm-Abnahme bleiben
  explizite Phase-13-Gates;
- Sensorrollen können gespeichert oder später erneut bearbeitet werden, sobald
  freigegebene ROM-IDs im Laufzeitmodell vorliegen.

Diese bewusste Zurückstellung verhindert den Konfigurationsabschluss nicht:
`setup_complete` bezeichnet die abgeschlossene Benutzerkonfiguration, nicht
die Hardwareabnahme des Gesamtsystems.

## Verifikation

Die gezielten Tests decken ab:

- atomaren Abschluss und Scheduler-Sperre;
- Ersetzen und geheimes Beibehalten von AP-/Stationspasswörtern;
- vollständige Geheimnisredaktion in Erfolg und Fehlerfall;
- Ablehnung falscher Hardware-Statusbehauptungen;
- AP-only-Mutationsschutz, CSRF und Generation/ETag;
- passiven Setup-Read ohne Hardwareprobe;
- die beiden neuen eingebetteten UI-Ressourcen und deren deterministische
  Generierung;
- JavaScript-Syntax der Setup-, App- und Übersetzungsdateien;
- reine JavaScript-Grenztests für SSID, Passwort, Passwortaktion und
  Quick-Start-Werte;
- lokale Browserprüfung, dass ungültige Werte den jeweiligen Schritt
  blockieren und die Zusammenfassung WLAN/Sicherheitsart ohne Geheimnisse
  anzeigt;
- Zielrunner für genau einen echten Ersatz des AP-Passworts und genau ein
  geschütztes Stations-WLAN in isoliertem Testspeicher;
- einen zweistufigen, fortsetzbaren Realtest für Stations-DHCP,
  AP-Neuanmeldung und inaktive Timer-Erstellung/-Bearbeitung/-Löschung;
- die Browser-Requestform bodyloser Mutationen, sodass `DELETE` und andere
  payloadlose Aktionen weder `Content-Type` noch einen leeren Body senden.

Die historische Phase-9-Quellledger bleibt ein unveränderlicher Buildnachweis.
Ihr Test vergleicht sie deshalb nicht mehr irrtümlich mit den weiterentwickelten
Phase-10-Arbeitsdateien, sondern prüft Vollständigkeit, Hashformat und die im
Phase-9-Buildbericht fixierte Ledger-SHA-256.

## Firmwareartefakt und Zielabnahme

Die Phase-10-Frozen-Closure bindet 42 Quelldateien. Beide sauberen Builds der
korrigierten Fassung lieferten für alle 15 verglichenen Ausgaben identische
Bytes. Bootloader und Partitionstabelle sind unverändert gegenüber Phase 9;
das neue geprüfte Anwendungsimage ist 2.050.848 Bytes groß und hat SHA-256
`9912c86513cc08e5b36f18c0705bf41bb3b6d592342b475170d3fce2b3780b63`.
Image-, 16-MB-/Octal-PSRAM-, Partitionierungs-, Größen- und Combined-Layout-
Gates sind bestanden. Details und retained artifacts stehen unter
`firmware/phase10_frozen/`.

Der app-only Flash bei `0x10000` wurde exakt für diesen Hash freigegeben. Er
löschte nur die zugehörigen App-Sektoren `0x10000–0x204fff`; Bootloader,
Partitionstabelle und VFS blieben unberührt. Schreibprüfung und unabhängige
Rücklesung aller 2.050.848 Bytes ergaben exakt den freigegebenen SHA-256. Der
passive Boot bestätigte MicroPython 1.28.0, die DFR0975-U-Identität, 11 Frozen-
Ressourcen und beide Funkinterfaces aus.

Im vorausgehenden realen Credential-Lauf erreichte der Browser die neue UI, wählte ein
geschütztes Stations-WLAN, ersetzte beide write-only Passwörter, bestätigte die
Zusammenfassung und schloss ab. Der privilegierte Ziel-Gateway zählte 59
gültige Pflichtantworten, keine Ablehnung, genau einen Mutationsversuch und
genau eine erfolgreiche Mutation. 62 Verbindungen wurden angenommen und
geschlossen; kein Observer-Fehler trat auf. Die erfolgreiche Mutation wird
nur gezählt, wenn der isolierte privilegierte Readback alle erwarteten Werte
exakt bestätigt.

Der formale Gesamtgate lief dennoch bis zum Zeitende, weil seine kombinierte
Every-route-Bedingung nicht erfüllt war. Die damalige Diagnose nannte nur
Summenzähler; die genaue fehlende Anwendungs- oder Wire-Route lässt sich nach
dem Cleanup nicht rekonstruieren. Cache-Wiederverwendung oder eine abgebrochene
redundante Anfrage sind mit den Daten vereinbar, aber nicht bewiesen. Der
Runner protokolliert künftig getrennt fehlende Routen und Serverzähler. Der
bereits erfolgreich absolvierte Benutzerablauf wird nur für bessere Diagnose
nicht wiederholt.

Der anschließende zweistufige Integrationstest verwendete ausschließlich
eigene A/B-Pfade. Er bestätigte nach einem physischen Reset die echte
Stationsverbindung mit DHCP und die Neuanmeldung eines Handyclients am
unveränderten AP `Landy Heater` mit dem zuvor gesetzten Passwort. Ein inaktiver
Montagstimer wurde erstellt, über Reload bestätigt, bearbeitet und erneut über
mehrere Resets bestätigt. Beim ersten Löschen antwortete die API korrekt mit
HTTP 422 und `request_body_not_allowed`, weil der gemeinsame UI-Helfer auch bei
payloadlosen Aufrufen `body: ""` an `fetch` übergab.

Die korrigierte UI fügt Body und JSON-Header nur noch bei tatsächlich
vorhandenem Payload hinzu. Nach neuem 1.098/1.098-Hosttest, zwei sauberen
byteidentischen Builds, Artefaktprüfung, separater Hashfreigabe, app-only Flash
und vollständigem Readback wurde der Test direkt beim vorhandenen Timer
fortgesetzt. `DELETE` war erfolgreich; ein frischer ConfigManager-Reload
bestätigte den leeren Timersatz und genau eine weitere Generation. Requested
State blieb OFF, der Heizungs-Protokolltripwire blieb null und die
Produktionsspeicher-Signaturen waren unverändert. Der PASS-Pfad entfernte alle
isolierten Dateien und deaktivierte beide Funkinterfaces.

Die aktive elektrische Abnahme von RTC, Sensoren und Autoterm bleibt bewusst
Phase 13. Historische Baseline und neuer Credential-Nachweis stehen in
`captures/2026-09-01-dfr0975u-phase10-setup-assistant-gate.md` und
`captures/2026-09-03-dfr0975u-phase10-credential-gate.md`. Der vollständige
Integrations-, Fehler-, Korrektur-, Flash- und Cleanup-Nachweis steht in
`captures/2026-09-03-dfr0975u-phase10-integration-delete-preflash.md`.
