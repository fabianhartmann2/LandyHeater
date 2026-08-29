# Phase 6 – Configuration Storage USB-only ESP32 smoke

Datum: 11. August 2026  
Board-Port: `/dev/cu.wchusbserial210`  
Ziel: `ESP32_GENERIC`, MicroPython `v1.28.0`, mpy `v6.3`

## Sicherheitsgrenze

- ausschließlich USB verbunden
- keine Heizung, kein Pegelwandler, kein UART-Jumper
- keine DS18B20- oder DS3231-Verbindung
- `boot.py`, `main.py` und `board_config.py` nicht überschrieben
- keine Imports aus `machine`, `board_config`, `hardware` oder `protocol`
- keine GPIO-, UART-, I2C- oder 1-Wire-Operation
- ausschließlich die sechs isolierten Smoke-Dateien
  `/phase6_usb_config_smoke_v1_{config,ledger}.{a,b,tmp}` verwendet

Der import-inerte Vorabtest ergab:

```text
PHASE6_IMPORT_PASS () ()
```

## Übertragene Runtime-Dateien

```text
adapters/__init__.py
adapters/config_file_store.py
app/__init__.py
app/application_state.py
app/configuration_bootstrap.py
app/scheduler.py
app/scheduler_controller_gateway.py
app/temperature_manager.py
services/__init__.py
services/config_manager.py
services/time_service.py
tools/__init__.py
tools/phase5_integration_smoke.py
tools/phase6_config_smoke.py
```

SHA-256 der funktionalen Phase-6-Allowlist:

```text
871e97a5bad847195fc5c11b73fd83acc09871477d31d8ab9e3b43e95b323ecc  adapters/config_file_store.py
3c1ed988367e5b139d001236aed82f17c84ebeefd608fb41734c00f21c8fc4a0  app/application_state.py
e90e505bd220bc4ac198908e9ba8a4e654ea55f6e0cb52d9f440fa33ebd72875  app/configuration_bootstrap.py
17629113beeab4033a37ff5b9a0d04036fd221bb61061c792e7370a3d2b860c3  app/scheduler.py
9b0fc5a1f097c9b200ed61fe8283515e3cff8fcb07990f83cea81124e8926d83  app/scheduler_controller_gateway.py
7278e6d2172d19a52438cbfe2d875e4ae5f67ad6d99e5a30b1f3c6e2adb27c27  app/temperature_manager.py
fde554dc7147582480a7fb55196a4580cb86c7c3781a05e202063b54bf5cb264  services/config_manager.py
926f800181a0cf01197276e2f68b1b8ad416691ebe503b2876641dc688c24133  services/time_service.py
0e9ef00f13f7ca03f5722e46e0342d2451fb9b2595f19084b8d89b1c4fc1bdb1  tools/phase6_config_smoke.py
```

## Finaler Lauf

Bewusst gestarteter Aufruf:

```python
from tools.phase6_config_smoke import run, SOFTWARE_ONLY_CONFIRMATION
run(SOFTWARE_ONLY_CONFIRMATION)
```

Vollständige Abschlussausgabe:

```text
PHASE 6 USB-ONLY CONFIG SMOKE PASS: 4/4
configuration_generation=2
ledger_generation=4
flash_config_writes=2
flash_ledger_writes=4
memory_before=149312
memory_after_import=67056
memory_after_warmup=64288
memory_after=64304
PHASE6_USB_CONFIG_SMOKE_PASS_V1
```

Damit sind auf dem realen Ziel unter anderem belegt:

- duale A/B-Erstprovisionierung von Konfiguration und Scheduler-Ledger
- CRC-/Footer-/Generation-Rückprüfung nach echten Flash-Schreibvorgängen
- semantischer No-op ohne weiteren Flash-Commit
- dauerhafter Consume-Checkpoint vor Timer-Autorisierung
- dauerhafter manueller Override und Reboot-Restore ohne Wiederholungsstart
- fail-closed Verhalten bei beschädigtem neuesten Konfigurations- und
  Ledger-Slot
- plattformunabhängig kanonisches JSON mit sortierten Schlüsseln, Steuerzeichen
  und UTF-8 (`Zürich`)
- erholter Heap nach Warmup und vier vollständigen Durchläufen

Zwei frühere Zwischenläufe gaben absichtlich keinen Pass-Token aus: Der erste
deckte MicroPythons nicht garantierte Dictionary-Reihenfolge auf, der zweite
eine unnötige 8194-Byte-Spitzenallokation beim Lesen. Beide Fehler wurden
fail-closed sichtbar, anschließend behoben und durch Host- sowie Boardtests
abgesichert.

## Cleanup und Neustart

Die Kontrolle nach dem finalen Lauf ergab:

```text
PHASE6_CLEANUP_PASS []
```

Nach einem Soft-Reset erschien erneut der passive Einstiegspunkt:

```text
MPY: soft reboot
Landy Heater safe boot; UART inactive; protocol TX disabled
MicroPython v1.28.0 on 2026-04-06; Generic ESP32 module with ESP32
```

Es wurde kein produktiver Konfigurationspfad aktiviert. Hardwarefreigaben und
der aktive Laufzeit-Composition-Root bleiben unverändert gesperrt.
