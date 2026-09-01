# Phase 10 – Setup Assistant

Stand: 2026-09-01. Der softwareseitige Setup-Assistent ist implementiert und
hostseitig geprüft. Das DFR0975-U-Firmwareartefakt wurde zweimal sauber und
bytegleich gebaut und statisch geprüft. Flash und Zieltest sind noch nicht
erfolgt und benötigen eine eigene hashgebundene Freigabe.

## Geführter Ablauf

Die Oberfläche bildet den festgelegten Ablauf mit neun Schritten ab:

1. Sprache (Deutsch/Englisch, lokal im Browser gespeichert)
2. Datum, Uhrzeit und bereits vorliegender RTC-Zustand
3. bis zu acht bekannte WLANs
4. individuelles Passwort des festen AP `Landy Heater`
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
- JavaScript-Syntax der Setup-, App- und Übersetzungsdateien.

Die historische Phase-9-Quellledger bleibt ein unveränderlicher Buildnachweis.
Ihr Test vergleicht sie deshalb nicht mehr irrtümlich mit den weiterentwickelten
Phase-10-Arbeitsdateien, sondern prüft Vollständigkeit, Hashformat und die im
Phase-9-Buildbericht fixierte Ledger-SHA-256.

## Firmwareartefakt und noch offene Abnahme

Die Phase-10-Frozen-Closure bindet 42 Quelldateien. Beide sauberen Builds
lieferten für alle 15 verglichenen Ausgaben identische Bytes. Bootloader und
Partitionstabelle sind unverändert gegenüber Phase 9; das geprüfte
Anwendungsimage ist 2.044.496 Bytes groß und hat SHA-256
`8c8d0bca7b6d3311c20f1e5878619a898147dcdf645305dc12fcbb575278fc5d`.
Image-, 16-MB-/Octal-PSRAM-, Partitionierungs-, Größen- und Combined-Layout-
Gates sind bestanden. Details und retained artifacts stehen unter
`firmware/phase10_frozen/`.

Vor dem Zieltest sind noch notwendig:

1. neue, exakt auf Hash, Offset `0x10000` und **ohne Erase** gebundene
   Flashfreigabe;
2. vollständige Rückleseprüfung des Anwendungsbereichs;
3. ein begrenzter Handytest des 9-Schritt-Assistenten mit demselben bekannten
   Testpasswort; kein wiederholter Funk-/Status-Test ohne neue Evidenzfrage.
