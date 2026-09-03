# Phase 10.1 – Portal- und Heimnetz-Erreichbarkeit

Stand: 2026-09-03. Der erste Wildcard-Listener-Kandidat wurde im Zieltest
verworfen. Der danach geflashte und vollständig zurückgelesene
Zwei-Listener-Kandidat startete nach dem vorgesehenen Hard-Restart beide
Listener, bestätigte Stations-DHCP, mDNS, 5/5 Captive-DNS-Antworten und einen
angenommenen AP-Request. Seine 302-Portalantwort legte anschließend zwei
Wire-Encoding-Fehler offen. Der portal-korrigierte Nachfolger ist als neue
44-Datei-Frozen-Closure gebunden, zweimal bytegleich gebaut und offline
geprüft; Flash und finale Laufzeitabnahme stehen noch aus. `boot.py` und
`main.py` bleiben bis zum späteren Produkt-Composition-Gate passiv.

## Benutzerpfade

- Beim Verbinden mit dem Geräte-WLAN `Landy Heater` beantwortet ein kleiner,
  ausschließlich an `192.168.4.1:53/UDP` gebundener DNS-Dienst A-Anfragen mit
  `192.168.4.1`.
- Bekannte Portal-Prüfpfade von Android, Apple, Windows und Firefox werden nur
  am AP-Eingang per HTTP 302 zu `http://192.168.4.1/` geführt. Ob das
  Betriebssystem den Dialog tatsächlich automatisch öffnet, bleibt eine
  Entscheidung des Handys. Die direkte Adresse ist der verlässliche
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

## Korrigiertes Listener- und Sicherheitsmodell

Für den Benutzer gibt es weiterhin nur **Port 80 ohne Portangabe**. Intern
besitzt jede aktive Netzwerkschnittstelle einen ausdrücklich gebundenen
TCP-Listener auf diesem Port:

- AP-Listener `192.168.4.1:80`: UI, Lesezugriffe und die bereits vorhandenen,
  durch Origin, CSRF und `If-Match` geschützten Mutationen;
- Stations-Listener `<DHCP-IP>:80`: UI und REST-Lesezugriffe; Tokenausgabe und
  sämtliche Mutationen bleiben serverseitig gesperrt;
- Captive DNS `192.168.4.1:53/UDP`: kein Forwarding, keine Speicherung
  angefragter Namen, maximal ein 512-Byte-Datagramm pro Schritt.

Die Eingangsart ist eine unveränderliche Eigenschaft des konkret gebundenen
Listeners. Sie wird nie aus `Host`, `Origin`, Peer-Subnetz oder anderen
Browserdaten abgeleitet. AP-only-Betrieb verwendet nur AP-HTTP und DNS. Nach
bestätigtem DHCP wird der Stationslistener an genau diese Adresse gebunden.
Eine spätere Änderung der DHCP-Adresse verlangt im Produkt-Supervisor den
gezielten Neuaufbau des Stationslisteners; sie darf den AP nicht abschalten.

Die UI lädt ihre Leseansichten ohne vorab einen Mutationstoken anzufordern.
Dadurch kann sie über `heater.local` lesend starten. Eine Änderungsaktion lädt
den Token erst bei Bedarf; über die Stationsschnittstelle lehnt der Server sie
ab. Eine sichere Fernbedienung braucht ein separates Authentisierungskonzept
und gehört nicht zu Phase 10.1.

## Warum der erste Kandidat verworfen wurde

Das zuerst gebaute Abbild mit SHA-256
`e378b4874d162f84b224396463b5384da9a55fcdd36a119ccee08b52d6f959e0`
verwendete einen Wildcard-Listener `0.0.0.0:80`. Die lokale Zieladresse eines
angenommenen Sockets sollte AP und Station unterscheiden. Der reale Test
lieferte eine eindeutige Teilbilanz:

- Firmware-Write und unabhängige Rücklesung: bytegleich;
- 16 MiB Flash, 8 MiB Octal-PSRAM sowie interner und DMA-Heap: bestanden;
- Stations-DHCP: `192.168.36.114`;
- mDNS: `heater.local` wurde zu dieser Adresse aufgelöst;
- Captive DNS: 58 von 58 Anfragen beantwortet, 0 Fehler;
- HTTP: 0 Verbindungen angenommen, 14 Socketfehler,
  `accepted_socket_rejected`.

Die Prüfung des exakt gepinnten MicroPython-v1.28-ESP32-Quellcodes bestätigte
anschließend die Ursache: ESP32-Socketobjekte stellen `getsockname()` nicht
bereit. Die Wildcard-Annahme war deshalb auf diesem Ziel grundsätzlich
unbrauchbar. Ein Rückfall auf den HTTP-Hostheader wurde aus Sicherheitsgründen
nicht verwendet. Das Board wurde danach bereinigt: isolierte Zugangsdaten und
temporäre Testdateien wurden gelöscht, AP und Station ausgeschaltet.

## Warum der Zwei-Listener-Kandidat nochmals korrigiert wurde

Nach dem isolierten Setup wurde `192.168.4.1:80` zunächst zu früh erneut
gebunden. Der daraus folgende `listener_start_failed` verschwand nach dem für
den Reload-Nachweis vorgesehenen Hard-Restart. Danach lief die eigentliche
Topologie korrekt: Stations-DHCP und mDNS waren bereit, Captive DNS beantwortete
5 von 5 Anfragen, beide Listener blieben ohne Socketfehler und der AP-Listener
nahm den ersten Handyrequest an.

Die Antwort scheiterte mit `response_contract_failed`, weil der gemeinsame
HTTP-Encoder Status 302 noch nicht in seiner Status-Allowlist führte. Außerdem
fügte die Webanwendung `Cache-Control: no-store` hinzu, obwohl der Encoder
diesen Sicherheitsheader bereits selbst besitzt. Der neue Kandidat ergänzt
`302 Found`, entfernt den doppelten Header und führt Portalantworten im
Regressionstest vollständig durch den echten Wire-Encoder.

## Kooperative Laufzeit und Grenzen

`ConfiguredDiscoveryRuntime` besitzt AP-HTTP, optional Stations-HTTP und DNS,
aber weder WLAN noch Heizung. Konstruktion und Import sind inert. Jeder
`step()` bedient reihum genau einen Socketbesitzer. Beim Cleanup wird in
umgekehrter Reihenfolge DNS, Stations-HTTP und AP-HTTP geschlossen.

Jeder HTTP-Listener behält die bestehende Grenze von höchstens zwei Clients;
bei gleichzeitigem AP- und Stationsbetrieb sind daher höchstens vier
HTTP-Clients vorgesehen. Der zweite Listener vergrößert das eingefrorene
Anwendungsabbild gegenüber dem verworfenen Kandidaten um 208 Byte. Der
portal-korrigierte Nachfolger ist 2.058.368 Byte groß, belegt rund 65 % der
3-MiB-App-Partition und lässt 1.087.360 Byte frei. Die statische Grenze ersetzt
nicht die erneute reale Heap-Messung mit beiden TCP-Listenern.

## Aktueller Build- und Artefaktstatus

- MicroPython v1.28.0 und ESP-IDF v5.5.1;
- Boardprofil `DFR0975U_N16R8`, 16 MiB Flash, Octal-PSRAM;
- 44 exakt gebundene Projektdateien;
- 1.143 Hosttests einschließlich historischer und neuer Artefaktgates bestanden;
- zwei saubere Builds, alle 15 geprüften Ausgaben bytegleich;
- App-Größe 2.058.368 Byte;
- App-SHA-256:
  `b3f16a7e4160cdd2c58cf78d25c6ebb3377a7d0438b5384054d679c19c03ad8f`;
- kombiniertes Abbild endet bei `0x206880`, vor VFS `0x310000`;
- Bootloader und Partitionstabelle sind gegenüber Phase 10 bytegleich.

Die vollständige Buildakte steht in
`firmware/phase10_1_portal_fixed_frozen/BUILD_INFO.md`. Beide vorherigen
Abbilder bleiben unter `firmware/phase10_1_frozen/` und
`firmware/phase10_1_fixed_frozen/` nachvollziehbar erhalten.

## Noch offenes Zielgate

Vor einem Abschluss fehlen nur die minimalen echten Zielprüfungen:

1. neuer exakt hashgebundener App-only-Flash und vollständige unabhängige
   Rücklesung des portal-korrigierten Kandidaten;
2. ein gemeinsamer Lauf mit AP, Stations-DHCP, zwei expliziten TCP/80-Binds,
   mDNS und AP-DNS;
3. Portal-Weiterleitung am AP sowie lesender Zugriff über `heater.local`;
4. eine nachweislich abgelehnte Stationsmutation;
5. mindestens 32 KiB freier GC-, interner und DMA-fähiger Heap;
6. vollständiger Socket-, REST- und Funk-Cleanup.

Eine neue exakte Flashfreigabe ist erforderlich, weil sich der App-Hash
geändert hat. Der engste Vorgang bleibt ein App-only-Write bei `0x10000` ohne
Full Erase; Hardwareausgänge bleiben weiterhin gesperrt.
