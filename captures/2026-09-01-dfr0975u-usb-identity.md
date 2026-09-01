# DFR0975-U USB identity gate — 2026-09-01

## Status

**PASS for non-writing board identity. No firmware was flashed.**

This capture establishes the received successor-board identity before any S3
profile, firmware or peripheral work. It is not a functional bring-up, radio
test or Phase-8 acceptance.

## Safety boundary

- USB data/power only;
- external 2.4-GHz antenna physically connected;
- heater, vehicle 12 V, battery, UART and level converter disconnected;
- RTC, I2C, 1-Wire and all sensors disconnected;
- no loopback jumper;
- no REPL command, filesystem write, flash erase or flash write;
- no Wi-Fi, Bluetooth, HTTP or GPIO test.

Repository state before the query was clean `main` at `cb27b4f`, equal to
`origin/main`.

## Physical identity

- board: DFRobot FireBeetle 2 ESP32-S3-U;
- SKU: `DFR0975-U`;
- PCB revision: `V1.0`;
- module marking: `ESP32-S3-WROOM-1U-N16R8`;
- external antenna present and connected.

The `1U` marking, rather than USB identity, establishes the external-antenna
module variant.

## Read-only ROM evidence

Host enumeration exposed one new Espressif native USB Serial/JTAG port with
VID `0x303a` and PID `0x1001`. Its unique serial/MAC value is intentionally not
recorded in the repository.

The board was queried once with esptool.py 4.7.0, explicit chip `esp32s3`,
115200 baud, `--no-stub flash-id`, default reset before the query and hard
reset afterwards. The bounded result was:

- chip: ESP32-S3 QFN56;
- silicon revision: 0.1;
- crystal: 40 MHz;
- embedded PSRAM reported: 8 MB, 3.3-V interface;
- flash manufacturer/device: `c8:4018`;
- detected flash size: 16 MB;
- flash mode from eFuse: Quad, four data lines.

No flash byte was written or erased. The serial number/MAC is omitted because
it is unnecessary for reproducibility.

## What this proves

The physical and ROM evidence agree with the expected DFR0975-U N16R8
hardware: ESP32-S3, external-antenna `1U` module, 16 MB flash and 8 MB embedded
PSRAM.

## What this does not prove

- that MicroPython initializes and uses Octal PSRAM correctly;
- the available internal/DMA-capable heap under Wi-Fi/lwIP load;
- safe or correct UART, I2C or 1-Wire GPIO assignments;
- the contents, safety or suitability of the factory firmware;
- any AP, DHCP, HTTP, REST or Phase-8 behavior;
- permission to flash the new board.

The old DFR0654 bootloader, partition table, application, combined image,
rollback image and offsets remain non-transferable. The next gates are a
confidential factory backup, a separate fail-closed DFR0975-U profile, a
reproducible `ESP32_GENERIC_S3` Octal-PSRAM build and an explicit hash-bound
flash approval.
