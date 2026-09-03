# Phase 10.1 – Portal- und Heimnetz-Erreichbarkeit

Stand: 2026-09-03. Die Software ist implementiert, auf dem Host geprüft, als
44-Datei-Frozen-Closure gebunden und zweimal bytegleich gebaut. Bootloader,
Partitionstabelle und Anwendung wurden offline als gültige ESP32-S3-Images
geprüft. Ressourcenmessung und die reale Abnahme auf dem DFR0975-U stehen noch
aus. Es wurde weder auf das Board zugegriffen noch geflasht. `boot.py` und
`main.py` bleiben bis zum späteren Produkt-Composition-Gate passiv.

## Benutzerpfade

- Beim Verbinden mit dem Geräte-WLAN `Landy Heater` beantwortet ein kleiner,
  ausschließlich an `192.168.4.1:53/UDP` gebundener DNS-Dienst A-Anfragen mit
  `192.168.4.1`.
- Bekannte Portal-Prüfpfade von Android, Apple, Windows und Firefox werden nur
  am AP-Eingang per HTTP 302 zu `http://192.168.4.1/` geführt. Ob das
  Betriebssystem den Dialog tatsächlich automatisch öffnet, bleibt eine
  Entscheidung des Handys. Die direkte Adresse ist deshalb der verlässliche
  Rückfallweg.
- Nach erfolgreichem Stations-DHCP ist die bevorzugte Heimnetzadresse
  `http://heater.local`. Die schon vorhandene feste Hostidentität `heater`
  aktiviert den eingebauten mDNS-Responder des ESP32-Ports. Die aktuelle
  Stations-IP funktioniert ebenfalls direkt.
- Ohne Stations-IP bleibt mDNS bewusst nicht bereit; AP, Captive DNS und die
  direkte AP-Adresse funktionieren davon unabhängig.

DHCP-Option 114 gemäß RFC 8910 ist nicht Bestandteil dieses Schritts. Die
verwendete MicroPython-Netzwerkschnittstelle stellt dafür keine bestätigte,
begrenzte API bereit. Die verbreiteten, betriebssystemspezifischen
HTTP-Prüfpfade bleiben daher der best-effort Portalmechanismus.

## Listener- und Sicherheitsmodell

Es gibt weiterhin genau **einen HTTP-Port und einen TCP-Listener**. Dieser
Listener bindet an `0.0.0.0:80`; für jede angenommene Verbindung wird über die
lokale Zieladresse des Sockets vertrauenswürdig bestimmt, ob sie am AP oder an
der Stationsschnittstelle eingegangen ist.

- AP-Eingang: UI, Lesezugriffe und die bereits vorhandenen, durch Origin,
  CSRF und `If-Match` geschützten Mutationen.
- Stations-Eingang: UI und REST-Lesezugriffe. Mutationen bleiben serverseitig
  gesperrt, selbst wenn ein gültiger AP-CSRF-Token mitgesendet würde.
- Captive-Weiterleitungen: nur am AP, nur `GET`, nur ohne Query, Body und
  Origin und nur für eine feste Liste bekannter Prüfpfade.
- DNS: kein Forwarding, keine Speicherung angefragter Namen, maximal ein
  512-Byte-Datagramm pro Schritt und höchstens eine Antwort.

Die UI lädt ihre Leseansichten nun ohne vorab einen Mutationstoken anzufordern.
Dadurch kann sie über `heater.local` lesend starten. Eine Änderungsaktion lädt
den Token weiterhin erst bei Bedarf; über die Stationsschnittstelle lehnt der
Server sie mit „mutation API unavailable“ ab. Eine später gewünschte sichere
Fernbedienung benötigt eine eigene Authentisierungsentscheidung und gehört
nicht zu Phase 10.1.

## Kooperative Laufzeit und Grenzen

`ConfiguredDiscoveryRuntime` besitzt den HTTP- und DNS-Socket, aber weder WLAN
noch Heizung. Konstruktion und Import sind inert. `start()` wird erst nach
Funk- und REST-Sicherheitsstart aufgerufen. Jeder `step()` bedient abwechselnd
genau HTTP oder DNS; dadurch kann eine Flut eines Dienstes den anderen nicht
dauerhaft verdrängen. Beim Cleanup wird zuerst DNS und danach HTTP geschlossen.

Der neue Umfang vergrößert das Anwendungsabbild gegenüber dem zuletzt
abgenommenen Phase-10-Image um 7.344 Byte. Das 2.058.192-Byte-Abbild belegt
damit rund 65 % der 3-MiB-App-Partition; 1.087.536 Byte bleiben frei. Der
Responder hält pro DNS-Schritt höchstens ein 512-Byte-Paket und eine Antwort,
der gemeinsame HTTP-Listener behält sein bestehendes Ein-Client-Limit. Das
begrenzt die erwartete Laufzeitlast, ersetzt aber nicht die reale Heap-Messung.

## Build- und Artefaktstatus

- MicroPython v1.28.0 und ESP-IDF v5.5.1 wurden mit dem vorhandenen
  `DFR0975U_N16R8`-Profil und Octal-PSRAM-Konfiguration gebaut.
- Zwei vollständige Builds aus jeweils leerem Zielverzeichnis stimmen für
  alle 15 geprüften Ausgaben bytegenau überein.
- App-Hash: `e378b4874d162f84b224396463b5384da9a55fcdd36a119ccee08b52d6f959e0`.
- Das kombinierte Abbild endet bei `0x2067d0`, deutlich vor dem VFS-Beginn
  `0x310000`, und enthält keine VFS-Daten.
- Die genaue Toolchain, Größen, Hashes und Offline-Prüfungen stehen in
  `firmware/phase10_1_frozen/BUILD_INFO.md`.

Vor einem Flash beziehungsweise Abschluss müssen folgende Gates bestanden
sein; die ersten beiden sind erledigt:

1. **bestanden:** vollständige Regression und deterministischer Frozen-Build;
2. **bestanden:** statische Image-/Partitions-/16-MB-/Octal-PSRAM-Prüfung;
3. kombinierter Zieltest mit AP + STA-DHCP + mDNS + DNS + HTTP;
4. mindestens 32 KiB freier GC-, interner und DMA-fähiger Heap in allen
   Messpunkten;
5. Portal-Weiterleitung am AP, `heater.local`/Stations-IP lesend und eine
   nachweislich abgelehnte Stationsmutation;
6. vollständiger Socket-, REST- und Funk-Cleanup.

Ein Flash erfolgt erst nach einer neuen, exakt an
`e378b4874d162f84b224396463b5384da9a55fcdd36a119ccee08b52d6f959e0`
gebundenen Freigabe. Der engste geeignete Vorgang ist ein App-only-Flash ohne
Erase bei `0x10000`; Bootloader, Partitionstabelle und VFS bleiben dabei
unverändert.
