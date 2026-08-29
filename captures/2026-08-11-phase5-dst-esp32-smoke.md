# Phase 5 – Europe/Zurich DST smoke on ESP32

Date: 2026-08-11  
Board: DFRobot FireBeetle 2 ESP32-E V1.0 / DFR0654  
Firmware: MicroPython 1.28.0, `ESP32_GENERIC`  
USB port during this run: `/dev/cu.wchusbserial210`

## Safety boundary

The board was connected by USB only. No heater, level shifter, 12-V supply,
GPIO jumper, DS18B20 sensor or DS3231 RTC was connected. Only the updated
hardware-independent files `services/time_service.py`, `app/scheduler.py` and
`tools/phase5_integration_smoke.py` were copied for this run. The smoke path
does not import `board_config`, `hardware`, `protocol` or `machine` and opens
no GPIO, I2C, UART or 1-Wire peripheral.

The delivered locks remained unchanged:

- `UART_PROTOCOL_TX_ENABLED = False`
- `ONEWIRE_PIN = None`
- `ONEWIRE_PIN_APPROVED = False`
- `I2C_SDA_PIN = None`
- `I2C_SCL_PIN = None`
- `I2C_PINS_APPROVED = False`

## What was exercised

In addition to the existing in-memory DS3231, RTC bridge, Scheduler/Gateway
and manual-override lifecycle, every iteration verified:

- the 2026 CET-to-CEST boundary immediately before and after 01:00 UTC;
- rejection of the non-existent local spring time 02:30;
- both UTC mappings of the repeated autumn local time 02:30;
- `fold=0` as the only start-eligible repeated time;
- a fresh Scheduler booting in `fold=1` produces no timer intent;
- `next_occurrence()` selects the following Sunday rather than the second
  repeated hour.

The canonical name `Europe/Zurich` is bidirectionally bound to the embedded
rule and CET standard offset, preventing a fixed UTC+1 clock from masquerading
as Zurich in summer.

## Board output

```text
PHASE 5 USB-ONLY INTEGRATION SMOKE PASS: 4/4 iterations; no hardware opened
MicroPython heap free: before=149104 after_import=96928 after_warmup=97616 after=97296 bytes
PHASE5_USB_SMOKE_PASS_V2
```

After a hardware reset, the board emitted:

```text
Landy Heater safe boot; UART inactive; protocol TX disabled
MicroPython v1.28.0 on 2026-04-06; Generic ESP32 module with ESP32
```

## Result

PASS. The Phase-5 UTC/timezone/Scheduler software path, including the embedded
Europe/Zurich DST policy, ran on the real MicroPython target with bounded heap
recovery and without opening hardware. This remains a software-only result;
electrical DS3231, I2C, sensor, UART and heater acceptance stays in Phase 13.
