# Phase 7 – Konfigurationskapazität auf dem ESP32

Datum: 11. August 2026  
Board: DFRobot FireBeetle 2 ESP32-E V1.0, DFR0654  
Firmware: offizielles `ESP32_GENERIC` MicroPython 1.28.0  
Compiler: offizielles `mpy-cross` MicroPython 1.28.0, mpy v6.3,
`-march=xtensawin`

## Zweck und Sicherheitsgrenze

Dieser USB-only-Test prüfte die größte praktische Phase-7-Konfiguration auf
dem realen ESP32: 32 Timer, acht bekannte WLAN-Profile, beide A/B-Slots,
frische Store-/Manager-Konstruktion und erneutes Einlesen. Der Runner öffnete
weder WLAN noch UART, GPIO, I2C oder 1-Wire. `boot.py` und `main.py` blieben
passiv.

Die inzwischen großen Laufzeitquellen wurden für diese Speicherabnahme mit dem
zum Board exakt passenden offiziellen `mpy-cross` kompiliert und unter dem
isolierten Pfad `/phase7_capacity_mpy_v1` geladen. Damit misst der Test den
vorgesehenen Produktionspfad und nicht den zusätzlichen Heapbedarf des
Quelltext-Compilers. Es wurden keine vorhandenen Laufzeitdateien ersetzt.

Wesentliche geprüfte Quellen:

```text
c2d803efb41ec05e479b661d5d77f30e64277a26c788ee5641db535458c7a607  adapters/config_file_store.py
17629113beeab4033a37ff5b9a0d04036fd221bb61061c792e7370a3d2b860c3  app/scheduler.py
d1d91e56b2cb1d769fa7cff5ea09eaf8c81859dc09026e99a01508f58bbdd8c0  services/config_manager.py
926f800181a0cf01197276e2f68b1b8ad416691ebe503b2876641dc688c24133  services/time_service.py
119487d895317f1762296f95e0b68b2603692a5e9b2f662a4756aae0d330d330  tools/phase7_config_capacity_smoke.py
```

## Ausführung und Ergebnis

Der eigentliche Aufruf nach einem Hardware-Reset war:

```text
mpremote connect PORT exec "import sys; sys.path.insert(0, '/phase7_capacity_mpy_v1'); import tools.phase7_config_capacity_smoke as s; s.run(s.SOFTWARE_ONLY_CONFIRMATION)"
```

Exakte Abschlussausgabe:

```text
PHASE 7 CONFIG CAPACITY PASS: timers=32 networks=8 bytes=7888 heap=158688/61312/51872/55488/55520/50528
PHASE7_CONFIG_CAPACITY_PASS_V1
```

Damit wurden bestätigt:

- kanonische Nutzlast: 7.888 Bytes bei einem 8-KiB-Anwendungslimit
- 32 Timer und acht bekannte WLAN-Profile
- dualer Commit von Konfiguration und Scheduler-Ledger
- vollständiger Post-Commit-Readback
- frische `ConfigManager`-/Store-Konstruktion und erneuter A/B-Reload
- alle sechs Heapmesswerte oberhalb der verbindlichen 32-KiB-Grenze

## Cleanup und Abschlusszustand

Der Runner entfernte seine sechs möglichen Dateien
`/phase7_config_capacity_v1_{config,ledger}.{a,b,tmp}`. Die unabhängige
Kontrolle ergab:

```text
CAPACITY_FILES []
MPY_DIR_PRESENT False
```

Auch alle zehn isolierten `.mpy`-Module und deren vier Verzeichnisse wurden
anschließend einzeln entfernt. Der spätere Funk-Smoke sowie der abschließende
Hardware-Reset bestätigten weiterhin den passiven Safe-Boot und deaktivierte
Funkinterfaces.

Auf dem Host bestanden danach 750/750 Tests; alle 43 Laufzeitmodule wurden
erneut mit dem offiziellen MicroPython-1.28-Compiler erfolgreich gebaut.
