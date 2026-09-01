# Phase 10 – Setup Assistant

Stand: 2026-09-01. **Phase 10 ist softwareseitig korrigiert; die erneute
DFR0975-U-Zielabnahme ist offen.** Der erste Zieltest hat die Transport-,
Speicher- und Cleanup-Grenzen bestanden, aber nur ein vorhandenes AP-Passwort
beibehalten und keine Stations-WLAN-Eingabe geprüft. Er war daher keine
vollständige Abnahme des Benutzerablaufs. Die korrigierte UI ist hostseitig
geprüft und als neues, zweimal bytegleiches Firmwareartefakt verfügbar. Vor
Flash und Handytest ist eine neue hashgebundene Freigabe erforderlich.

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
  geschütztes Stations-WLAN in isoliertem Testspeicher.

Die historische Phase-9-Quellledger bleibt ein unveränderlicher Buildnachweis.
Ihr Test vergleicht sie deshalb nicht mehr irrtümlich mit den weiterentwickelten
Phase-10-Arbeitsdateien, sondern prüft Vollständigkeit, Hashformat und die im
Phase-9-Buildbericht fixierte Ledger-SHA-256.

## Firmwareartefakt und Zielabnahme

Die Phase-10-Frozen-Closure bindet 42 Quelldateien. Beide sauberen Builds der
korrigierten Fassung lieferten für alle 15 verglichenen Ausgaben identische
Bytes. Bootloader und Partitionstabelle sind unverändert gegenüber Phase 9;
das neue geprüfte Anwendungsimage ist 2.050.848 Bytes groß und hat SHA-256
`d8fb33c0e43081d95744816cbbedf7b77281292d3c8458d14b1f50cf27f7b9ef`.
Image-, 16-MB-/Octal-PSRAM-, Partitionierungs-, Größen- und Combined-Layout-
Gates sind bestanden. Details und retained artifacts stehen unter
`firmware/phase10_frozen/`.

Dieses neue Image wurde noch nicht geflasht. Der frühere app-only Flash des
Hashes `8c8d0bca...` und sein Handytest bleiben als historische Baseline
gültig: 11/11 UI-Ressourcen, 5/5 API-Lesewege, genau ein isolierter Commit
sowie alle Heap-, Requested-State-, Protokoll-, Produktstorage- und
Cleanup-Gates waren bestanden. Weil dieser Lauf das AP-Passwort nur
beibehielt und die Stations-WLAN-Liste leer ließ, ersetzt er nicht den noch
ausstehenden echten Eingabe- und Validierungsgate.

Nach neuer Freigabe muss der einmalige Zieltest das neue Image vollständig
zurücklesen und in isoliertem Speicher genau einen AP-Passwortwechsel sowie
genau ein geschütztes Stations-WLAN speichern. Das Live-Testpasswort bleibt
unverändert; alle Testzugangsdaten bleiben aus Logs und Antworten redigiert
und der Testspeicher wird anschließend entfernt.

Die aktive elektrische Abnahme von RTC, Sensoren und Autoterm bleibt bewusst
Phase 13. Der historische Baseline-Zielnachweis steht in
`captures/2026-09-01-dfr0975u-phase10-setup-assistant-gate.md`; der neue
Credential-Gate wird erst nach Flash und realer Abnahme ergänzt.
