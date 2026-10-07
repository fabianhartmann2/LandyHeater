# Phase 13 heater activation frozen firmware build record

Build date: 2026-10-07. Status: **exact 48-file source closure, browser-time
host safety tests, two byte-identical canonical-path builds and offline
artifact gates passed. This new image has not been authorized, flashed or
tested on the target.**

This candidate extends the previously accepted heater lifecycle image with an
AP-protected, reboot-volatile browser-time source while retaining the existing
RTC path. Its private frozen `board_config.py` differs from the
safe repository profile in exactly two values:
`UART_PINS_APPROVED=True` and `UART_PROTOCOL_TX_ENABLED=True`. The selected
direct level-shifter approval remains explicit; the nonexistent GPIO12 gate,
I2C and radio approvals remain closed. Normal `boot.py` and `main.py` behavior
remains passive and is not part of the frozen closure.

## Pinned inputs

- repository baseline before the browser-time candidate: `f63e049`;
- 48 exact project-source files bound by `CURRENT_FROZEN_SOURCES.sha256`;
- MicroPython v1.28.0 commit
  `e0e9fbb17ed6fd06bb76e266ae554784c9c80804`;
- ESP-IDF v5.5.1 commit
  `fcae32885b0296b32044cb99ecbdc50d98dddb83`;
- Python 3.14.6;
- `mpy-cross` v1.28.0, MPY format 6, executable SHA-256
  `ceda0dfb2f800a3970f2be6a036aedfc3e9bcdb49579aeb3d5a0cb1a0390c849`;
- esptool 4.12.0;
- board `DFR0975U_N16R8`, 16-MiB flash and fail-closed Octal PSRAM;
- `SOURCE_DATE_EPOCH=1788100339`, `TZ=UTC`, `LC_ALL=C`,
  `PYTHONHASHSEED=0`, ccache disabled;
- dependency-lock SHA-256:
  `955bf85a5b28d7ec03e7a06c0b00c8d4b9b64a4fb0730b75e2f0fcaa53aef193`.

| Candidate input | SHA-256 |
| --- | --- |
| `manifest.py` | `7cfda15fe94561841d57ae9e8c6e18cd2b03c00eba29161142f13bc2d21f3f7e` |
| `FROZEN_MODULES.txt` | `d456642d89ff3c5ce7742c6e5858d3de244ae11a0b1ad2629838b2a23a6a0621` |
| `CURRENT_FROZEN_SOURCES.sha256` | `8746384554cd03934430d29178d508b1f2477ee807a0c421e5056e26941dd827` |
| `artifacts/SHA256SUMS` | `86cf1e4da1583fac966aeb4637e579a7d72843d6c58ba9ba2f0a0cc59594e8c7` |

The closure freezes the private activation profile and
`app/heater_composition.py`. It excludes `boot.py`, `main.py`, credentials,
persistent data, acceptance tools and tests. The bounded active-cycle probe is
therefore invoked explicitly over USB; it cannot run automatically at boot.

## Reproducibility proof

Two clean builds used the same canonical source and output path. The complete
first build directory was moved aside before that exact output path was
recreated. All 15 compared outputs were byte-identical: bootloader, partition
table, application, combined image, UF2, final and combined configurations,
four flash-argument files, flasher JSON, frozen C, ELF and map. The dependency
lock also matched the retained DFR0975-U lock byte-for-byte.

## Image, layout and retained artifacts

Esptool identifies the application and bootloader as valid ESP32-S3 images
with valid checksums and validation hashes, 16-MiB flash headers, 80-MHz DIO
boot mode and ESP-IDF 5.5.1. The bootloader and partition table are
byte-identical to the accepted Phase-11 artifacts. The partition layout still
contains a 3-MiB factory application at `0x10000` and VFS at `0x310000`; there
is no OTA partition.

| Region/file | Size/result |
| --- | ---: |
| Bootloader | 19,232 B; unchanged |
| Partition table | 3,072 B; unchanged |
| Factory application | 2,112,512 B used of 3,145,728 B |
| Growth from accepted heater image | 2,992 B |
| Application margin | 1,033,216 B (about 33%) |
| Combined image | 2,178,048 B; exact end `0x213c00` |

The retained deployment subset is bound by `artifacts/SHA256SUMS`. The only
image proposed for the next operation is:

```text
offset: 0x10000
size:   2112512 bytes
sha256: 741ad9f13d106035d8ffe45ed0d84e92815e3d54396cf1f80400f7d349c27d18
erase:  no full-chip erase
```

This record is evidence only and does not authorize a later flash.

## Target status and prior active-cycle evidence

The browser-time application above has not been authorized or written. Any
future app-only flash requires a fresh approval that names its exact digest,
offset `0x10000` and no-full-erase scope. Bootloader, partition table and VFS
must remain untouched.

The immediately preceding application (`f02d59e7...`) was independently
authorized, written and fully read back. After manual reset, a passive check
confirmed MicroPython 1.28.0, the exact DFR0975-U N16R8 identity, the frozen
activation profile and inactive radios.

The separately confirmed active gate then ran power level 1 for seven minutes,
observed STARTING, RUNNING and SHUTTING_DOWN, and returned to synchronized
`ready`/`off`. It required exactly one START, one SHUTDOWN and 576 STATUS
requests; final reported voltage was 12.2 V. Production storage stayed
unchanged, radios stayed inactive and UART cleanup completed normally. The
exact result was `DFR0975U_ACTIVE_CYCLE_RUNNING_TO_OFF_PASS_V1`. Evidence is
recorded in
`../../captures/2026-10-07-dfr0975u-heater-active-cycle.md`.
