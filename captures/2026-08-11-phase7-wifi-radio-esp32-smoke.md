# Phase 7 – WLAN-Funk-Smoke auf dem ESP32

Datum: 11. August 2026  
Board: DFRobot FireBeetle 2 ESP32-E V1.0, DFR0654  
Firmware: offizielles `ESP32_GENERIC` MicroPython 1.28.0

## Zweck und Begrenzung

Dieser ausdrücklich ausgelöste Test öffnete ausschließlich die beiden
ESP32-WLAN-Interfaces. UART-Protokoll-TX, 1-Wire und I2C blieben verriegelt;
HeaterController, Protokoll und Sensorhardware waren nicht beteiligt. Die
dauerhafte Boardkonfiguration blieb `WIFI_RADIO_APPROVED=False`; nur der Runner
setzte diese Freigabe RAM-lokal für genau einen begrenzten Durchlauf.

Geprüfte Quellen:

```text
6259182ab33548f9fa9c920650f18ac7e5e6a50dd722e8be08419406d8cd4230  app/network_manager.py
48556ef3f3c447c1d1bc53a7569d8a281374ec79e7bfbe0dfd41b9e4bb625b02  hardware/micropython_wifi.py
76dbfba8dd69c2fdb217c546136d628291f448c753ced746a3aaa1a2cdb8a15a  tools/phase7_network_smoke.py
055ee7ad76be20b61b79ba80a2c73f6b832a072b5b344ce63bb154132135341c  board_config.py
```

## Ausführung und Ergebnis

Aufruf:

```text
mpremote connect PORT exec "import tools.phase7_network_smoke as s; s.run(s.RADIO_SMOKE_CONFIRMATION, 1)"
```

Exakte Ausgabe:

```text
PHASE 7 WIFI RADIO SMOKE PASS: 1/1
ap_ssid=Landy Heater
ap_ip=192.168.4.1
station_attempts=1
radio_cleanup_confirmed=True
PHASE7_WIFI_RADIO_SMOKE_PASS_V1
```

Der Produktionspfad stellte zuerst einen kurzlebigen WPA2-AP bereit, bestätigte
die direkte AP-Adresse und überwachte den AP während genau eines Versuchs zu
einem absichtlich nicht vorhandenen STA-Netz. Zugangsdaten erschienen weder in
Ergebnis noch Diagnose oder Exception. Im reinen AP-Betrieb wurde mDNS bewusst
nicht als bereit behauptet.

Unmittelbar nach dem Runner bestätigte eine unabhängige Kontrolle:

```text
RADIO_STATE False False
WIFI_LOCK False False False
```

Die drei Werte der zweiten Zeile sind ausgelieferte RAM-Freigabe, globale
Lease und Poison-Latch; alle waren `False`.

## Hardware-Reset

Nach einem echten Reset wurden beide Singleton-Interfaces und die Sperren
erneut kontrolliert:

```text
POST_RESET_RADIO_STATE False False
POST_RESET_WIFI_LOCK False False False
Landy Heater safe boot; UART inactive; protocol TX disabled
```

Damit sind Funk-Cleanup, Lease-Freigabe und passiver Boot für den finalen
Phase-7-Stand bestätigt. Der Test belegt keine Reichweite und keine Verbindung
zu einem realen Heimnetz; diese Punkte bleiben produktive Integration.
