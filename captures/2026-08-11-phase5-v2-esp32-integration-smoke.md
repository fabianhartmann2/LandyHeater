# Phase 5 V2 – ESP32 USB-only integration smoke

Date: 2026-08-11  
Board: DFRobot FireBeetle 2 ESP32-E V1.0 / DFR0654  
Firmware: MicroPython 1.28.0, `ESP32_GENERIC`  
USB port during this run: `/dev/cu.wchusbserial210`

## Safety boundary

The board was connected by USB only. No heater, level shifter, 12-V supply,
GPIO jumper, DS18B20 sensor or DS3231 RTC was connected. The test did not copy
or import `board_config`, `hardware`, `protocol`, UART or a MicroPython
`machine` factory. `boot.py` and `main.py` remained passive.

The uploaded allowlist was limited to the hardware-independent Phase-5 core:

- `app/application_state.py`
- `app/scheduler.py`
- `app/scheduler_controller_gateway.py`
- `adapters/ds3231_adapter.py`
- `services/time_service.py`
- `services/rtc_time_bridge.py`
- `tools/phase5_integration_smoke.py`
- the corresponding package `__init__.py` files plus passive `boot.py` and
  `main.py`

## What was exercised

The smoke test used an in-memory DS3231 register bus and a requested-state-only
fake controller. It exercised:

- DS3231 register decoding and the staged-write/commit trust boundary;
- RTC-to-TimeService synchronization;
- one Sunday 14:30 power-timer occurrence;
- synchronous Scheduler-to-Gateway authorization and completion;
- manual timer override and once-only occurrence handling;
- repeated allocation/cleanup behaviour and the real MicroPython tick helpers.

No GPIO, I2C peripheral, UART, 1-Wire bus or heater command was opened or used.

## Board output

```text
PHASE 5 USB-ONLY INTEGRATION SMOKE PASS: 4/4 iterations; no hardware opened
MicroPython heap free: before=151952 after_import=107056 after_warmup=108080 after=108080 bytes
PHASE5_USB_SMOKE_PASS_V2
```

The final free heap matched the post-warm-up baseline exactly. After the test,
the board was reset and the passive entry point printed:

```text
Landy Heater safe boot; UART inactive; protocol TX disabled
```

## Result

PASS. This is evidence for the hardware-independent Phase-5 integration on the
real MicroPython target. It is not an electrical DS3231, I2C, sensor, UART or
heater acceptance test; those paths remain locked for Phase 13.
