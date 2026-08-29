# Phase 7 – Reale Handy-Assoziation am ESP32-Access-Point

Datum: 11. August 2026  
Board: DFRobot FireBeetle 2 ESP32-E V1.0, DFR0654  
Firmware: offizielles `ESP32_GENERIC` MicroPython 1.28.0

## Zweck und Sicherheitsgrenze

Dieser ausdrücklich ausgelöste Test ergänzte den automatischen Phase-7-
Funk-Smoke um eine reale WPA2-Assoziation und den am Handy sichtbaren
DHCP-Nachweis. Für die Verbindung lief ein begrenztes
150-Sekunden-Verbindungsfenster. Nach der Erkennung verlangte der Runner drei
frische Client-Beobachtungen und anschließend weitere 30 Sekunden stabile
Verbindung.

Das Passwort wurde nur als einmaliges Laufzeitargument übergeben. Es wurde
nicht gespeichert, ausgegeben oder in diese Capture-Datei übernommen.
UART-Protokoll-TX, I2C und 1-Wire blieben verriegelt; `boot.py` und `main.py`
blieben passiv. Es lief kein HTTP-Server und kein Captive Portal.

Geprüfter Runner:

```text
e428f90deed8611c2cb23980b4e3271feb23f6e8af5855986b3bae04699961f0  tools/phase7_phone_ap_smoke.py
```

Lokale und Board-Prüfsumme waren byteidentisch; die Boarddatei hatte 12.094
Bytes. Vor dem Start bestätigte der import-inerte Preflight:

```text
PHONE_TEST_READY (False, False) (False, False) False 30000
```

Damit waren STA/AP vor und nach dem Import inaktiv, die dauerhafte
WLAN-Freigabe `False` und der Stabilitäts-Hold exakt 30.000 ms.

## Automatisches Ergebnis

Der vollständige sichere Ausgabeweg lautete:

```text
PHASE7_PHONE_AP_READY_V1
ssid=Landy Heater
ap_ip=192.168.4.1
window_seconds=150
Connect the phone now; no web page is expected.
PHASE7_PHONE_AP_CLIENT_SEEN_V1
clients=1
PHONE_CLIENT_CONFIRMED clients=1
radio_cleanup_confirmed=True
PHASE7_PHONE_AP_SMOKE_PASS_V1
```

Der Pass-Token erschien erst nach Manager-/Port-Cleanup, Wiederherstellung der
RAM-Freigabe, unabhängiger Radio-off-Prüfung und Heapkontrolle.

## Manuell am Handy bestätigte DHCP-Werte

```text
IP-Adresse: 192.168.4.2
Subnetzmaske: 255.255.255.0
Router/Gateway: 192.168.4.1
```

Damit sind sowohl die WPA2-Assoziation durch den ESP32-Clientzähler als auch
die erwartete DHCP-/Gateway-Konfiguration am echten Endgerät bestätigt.
`heater.local` und eine Webseite waren im AP-only-Test ausdrücklich nicht zu
erwarten.

## Cleanup und Reset

Die unabhängige Kontrolle unmittelbar nach dem Test ergab:

```text
RADIO_STATE False False
WIFI_LOCK False False False
```

Nach Hardware-Reset:

```text
POST_RESET_RADIO_STATE False False
POST_RESET_WIFI_LOCK False False False
Landy Heater safe boot; UART inactive; protocol TX disabled
```

AP, STA, RAM-Freigabe, globale Lease und Poison-Latch waren damit vor und nach
Reset vollständig inaktiv. Der temporäre WLAN-Eintrag wird am Handy wieder
vergessen und ist keine Produktkonfiguration.

Der zugehörige Hoststand bestand 760/760 CPython-Tests; alle 44 Laufzeitmodule
kompilierten mit dem offiziellen MicroPython-1.28-`mpy-cross` für
`xtensawin`.
