# DFR0975-U first flash and memory gate — 2026-09-01

## Status

**PASS for the authorized first project flash, passive USB firmware identity,
separate PSRAM/internal-memory measurements and radio-off check.**

Storage, UART, product peripherals, functional radio/DHCP and Phase-8 HTTP
acceptance remain separate later gates.

## Safety boundary and authorization

The board remained USB-only with the external 2.4-GHz antenna connected.
Heater, vehicle power, UART, RTC/I2C, 1-Wire, sensors and loopback wiring were
disconnected. The owner explicitly authorized a full-chip erase and flash of
the combined image bound to SHA-256:

`4c0ed87982e8d643351034903acc0850acbceff6bcdceab622472eaf0c53b226`

Immediately before the write, the retained input image matched that digest,
was exactly 2,030,224 bytes, and the connected target again identified as an
ESP32-S3 revision 0.1 with 16-MiB Quad-SPI flash and 8-MiB embedded PSRAM.

## Flash result

esptool.py 4.12.0 completed the authorized full-chip erase, then wrote the
combined image at `0x000000` using the retained 16-MB/80-MHz/DIO flash
arguments. It wrote all 2,030,224 input bytes, verified the written data hash
and performed a hard reset. The combined image contains the compatible
bootloader, partition table and application; the preceding full erase left the
VFS region clean. No app-only or classic-ESP32 artifact was used.

The private 16-MiB factory recovery image remains unchanged outside Git. This
successful write does not authorize a later erase, reflash or restore.

## Passive boot identity

The first USB query used the raw REPL without a soft reset and returned:

- MicroPython `v1.28.0`;
- `sys.platform == "esp32"`;
- release `1.28.0`;
- machine `DFRobot DFR0975-U N16R8 with ESP32S3`.

No project `boot.py` or `main.py` was installed, no peripheral was opened and
no persistent diagnostic file was copied to the board.

## Separate memory measurements

The first transient probe attempt correctly failed closed before sampling
because it expected both native APIs in `esp32`. MicroPython 1.28 actually
provides physical `flash_size()` in `esp` and `idf_heap_info()` in `esp32`.
The probe and its focused host test were corrected to preserve that API
boundary; no firmware reflash was needed.

The corrected USB-only probe emitted the exact pass token and values:

```text
DFR0975U_MEMORY_PROBE_PASS_V1 16777216 8216128 274191 196608 266475 196608 8388608
```

| Measurement | Result | Gate |
| --- | ---: | ---: |
| physical flash | 16,777,216 B | exactly 16 MiB |
| MicroPython GC free | 8,216,128 B | at least 32 KiB |
| internal 8-bit heap free | 274,191 B | at least 32 KiB |
| largest internal 8-bit block | 196,608 B | at least 32 KiB |
| internal DMA-capable heap free | 266,475 B | at least 32 KiB |
| largest internal DMA-capable block | 196,608 B | at least 32 KiB |
| registered PSRAM | 8,388,608 B | 7–8 MiB, nominal 8 MiB |

The general internal and DMA-capable heaps were queried with distinct IDF
capability masks and PSRAM with its own mask. This proves the idle USB-only
memory gate; internal/DMA headroom must still be measured again under the later
bounded WLAN/product load.

## Radio and remaining boundary

After the memory measurement, read-only state queries reported both station
and access-point interfaces inactive:

```text
RADIO_STA False
RADIO_AP False
```

This is only a radio-off safety check, not permission or evidence for a radio
test. VFS/storage, USB recovery, S3 UART/level interface, I2C, 1-Wire,
functional WLAN/DHCP and the single-listener Phase-8 product run remain open
and require their own bounded plans and approvals where they write or activate
hardware.
