# ESP32 board options for Landy Heater

## Ausgangslage

Der bisherige DFR0654 besitzt 4 MB Flash und keine PSRAM. Der vollständige
Phase-8-Aufbau erreichte vor dem Listener nur 32.880 Bytes freien Heap und fiel
am folgenden Pflicht-Checkpoint unter 32 KiB. Ein Board mit lediglich mehr
Flash behebt diesen Laufzeitspeicherengpass nicht. Für den Nachfolger ist daher
**8 MB bestückte und von der Firmware aktivierte PSRAM** das wichtigste
Auswahlkriterium.

Die nachfolgende Auswahl ist eine Entwicklungsentscheidung, keine
Hardwarefreigabe. Jedes neue Board benötigt ein eigenes Profil, eine passende
Firmware und die vollständige Sicherheits- und Zielabnahme.

## Verglichene Boards

| Board | Flash / PSRAM | Antenne | Stärken | Grenzen | Projekturteil |
| --- | ---: | --- | --- | --- | --- |
| [DFRobot FireBeetle 2 ESP32-S3-U, DFR0975-U](https://wiki.dfrobot.com/dfr0975-u/) | 16 MB / 8 MB | Extern über WROOM-1U-Anschluss | FireBeetle-Format, viel Speicher, USB-C, Akku-/Power-Management, externe Antenne für Fahrzeuggehäuse | Neue S3-Firmware und vollständig neue Pinprüfung erforderlich; Antenne separat passend auswählen | **Ausgewähltes Zielboard** |
| [DFRobot FireBeetle 2 ESP32-S3, DFR0975](https://wiki.dfrobot.com/dfr0975/) | 16 MB / 8 MB | Integrierte PCB-Antenne | Gleicher Speicher und ähnliche Bauform wie DFR0975-U; kein Antennenkabel nötig | In oder hinter Metall schlechter platzierbar | Beste Alternative bei Kunststoffgehäuse/offenem Einbau |
| [Espressif ESP32-S3-DevKitC-1-N8R8](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32s3/esp32-s3-devkitc-1/user_guide_v1.1.html) | 8 MB / 8 MB | Modulantenne | Offizielles Espressif-Referenzboard, viele Header, USB-UART plus natives USB, sehr gute Recovery-/Messplattform | Größer, kein FireBeetle-Power-/Akkukonzept; GPIO35–37 sind bei Octal-Speicher intern belegt | **Beste optionale Entwicklungsplatine** |
| [Unexpected Maker ProS3](https://esp32s3.com/pros3.html) | 16 MB / 8 MB | Boardabhängige integrierte RF-Lösung | Kompakt, viele GPIOs, USB-C/natives USB, LiPo-Unterstützung, eigener [MicroPython-Build](https://micropython.org/download/UM_PROS3/) | Andere Bauform und Pinbelegung; konkrete Hardware-Revision vor Kauf und Portierung prüfen | Gute kompakte Premium-Alternative |
| [Seeed Studio XIAO ESP32S3 Plus](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/) | 16 MB / 8 MB | Variantenabhängig | Sehr klein, USB-C, ausreichend Flash/PSRAM | Weniger komfortable Header-, Mess- und Recovery-Reserve; knapper für UART, I2C, 1-Wire und Erweiterungen | Nur wenn minimale Baugröße höchste Priorität hat |
| [DFRobot FireBeetle 2 ESP32-E N16R2, DFR1139](https://wiki.dfrobot.com/dfr1139/) | 16 MB / 2 MB | Integriert | Klassischer ESP32 und bekannte FireBeetle-Familie | Nur 2 MB PSRAM; aktuelle UART-RX-Annahme auf GPIO16 ist bei dieser Variante nicht übertragbar | Nicht als Lösung für den Phase-8-Engpass empfohlen |

Bestandsangaben, Preise und Boardrevisionen können sich ändern. Vor Bestellung
und besonders vor dem Flashen ist die **exakte SKU** auf Bestellung, Verpackung
und Modul zu vergleichen.

## Verbindliche Auswahlkriterien

### 1. PSRAM statt nur größerem Flash

- Mindestens 8 MB PSRAM, tatsächlich auf dem Modul bestückt.
- Bei Espressif-Bezeichnungen auf `R8` achten:
  - `N8R8` = 8 MB Flash + 8 MB PSRAM;
  - `N16R8` = 16 MB Flash + 8 MB PSRAM.
- Ein `N8`-Board ohne `R8` ist kein gleichwertiger Ersatz.
- PSRAM muss im MicroPython-Build erkannt und genutzt werden. Beim DFR0975-U
  ist dafür der `ESP32_GENERIC_S3`-Build mit Octal-SPIRAM (`spiram-oct`)
  vorgesehen. Siehe [MicroPython ESP32_GENERIC_S3](https://micropython.org/download/ESP32_GENERIC_S3/).

PSRAM entlastet den Python-/Produktheap, aber nicht jede native Wi-Fi/lwIP-
Allokation. Interner und DMA-fähiger Speicher müssen weiterhin gemessen werden;
die Spezifikation allein ersetzt keinen Zieltest.

### 2. GPIO-Reserve

Das Projekt benötigt mindestens:

- UART TX und RX;
- I2C SDA und SCL;
- einen 1-Wire-Pin;
- zusätzliche Reserve für sichere Diagnose und spätere Erweiterungen.

USB-, Boot-Strapping-, Flash-/PSRAM-, Antennen- und boardintern belegte Pins
dürfen nicht als freie GPIOs angenommen werden. Die alte DFR0654-Belegung
GPIO17/GPIO16 wird nicht ungeprüft übernommen.

### 3. USB und Recovery

BOOT- und RESET-Zugriff sowie ein stabiler serieller Recovery-Pfad sind
Pflicht. Ein separates USB-UART-Interface zusätzlich zu nativem USB ist im
Labor besonders hilfreich; deshalb bleibt das DevKitC-1-N8R8 die bevorzugte
optionale Entwicklungsplatine.

### 4. Antenne und Einbauort

Für ein Kunststoffgehäuse und kurze Distanz kann die integrierte Antenne des
DFR0975 genügen. In einem Fahrzeug oder Metallgehäuse ist der DFR0975-U besser
platzierbar. Die externe Antenne soll 2,4 GHz und 50 Ohm unterstützen und zum
U.FL/IPEX/MHF-I-Anschluss passen. Espressif empfiehlt für die bestehende
Zertifizierungsbasis höchstens 2,33 dBi Gewinn; abweichende Antennen können
zusätzliche EMV-/Zulassungsprüfungen erfordern. Siehe
[ESP32-S3-WROOM-1/1U-Datenblatt](https://documentation.espressif.com/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf).

### 5. Fahrzeuggrenze

Keines dieser Entwicklungsboards darf direkt am 12-V-Bordnetz oder ungeprüft
an der Autoterm-Signalleitung betrieben werden. Erforderlich bleiben:

- geschützter DC/DC-Wandler;
- Verpolungs- und Transientenschutz;
- geeigneter UART-Pegel- beziehungsweise Isolationsschutz;
- Masse-/EMV-Prüfung und mechanische Zugentlastung.

## Entscheidung

Das **DFR0975-U N16R8** ist das ausgewählte Zielboard, weil es die erforderliche
PSRAM-Reserve, 16 MB Flash, die vertraute FireBeetle-Bauform und eine extern
platzierbare Antenne verbindet.

Falls zusätzlich ein reines Entwicklungsboard beschafft wird, ist das
**ESP32-S3-DevKitC-1-N8R8** die bevorzugte Ergänzung für Firmware-Portierung,
Messungen und Recovery.

Der verbindliche Portierungs- und Bring-up-Plan steht in
`DFR0975U_MIGRATION.md`. Phase 8 und die Auswahl als endgültiges
Produktionsboard bleiben bis zur vollständigen Realzielabnahme offen.
