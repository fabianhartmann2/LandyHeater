# Phase 10 frozen firmware build record

Build date: 2026-09-01. Status: **the credential-validation follow-up passed
offline reproducibility and artifact gates; its app-only flash, readback and
target gate are pending a new hash-bound authorization**. The earlier,
superseded baseline image passed its narrower keep-password/empty-WLAN target
gate and remains documented in the historical capture.

No serial port, board, deploy, erase or write operation was used while
building or verifying this candidate. The later flash was performed only
after a separate authorization naming the exact application hash, offset and
erase policy.

## Pinned inputs

- repository baseline before the Phase-10 changes:
  `e8cd4bdbb9df07da8872d46df6f763563602738a`;
- the exact 42 project-source bytes are bound by
  `CURRENT_FROZEN_SOURCES.sha256`;
- MicroPython v1.28.0, commit
  `e0e9fbb17ed6fd06bb76e266ae554784c9c80804`;
- ESP-IDF v5.5.1, commit
  `fcae32885b0296b32044cb99ecbdc50d98dddb83`;
- `mpy-cross` v1.28.0, MPY format 6.3;
- esptool 4.12.0;
- board `DFR0975U_N16R8`, based on `ESP32_GENERIC_S3` with explicit
  16-MiB flash and Octal-PSRAM settings;
- `SOURCE_DATE_EPOCH=1788100339`, `TZ=UTC`, `LC_ALL=C`,
  `PYTHONHASHSEED=0`, ccache disabled;
- dependency-lock SHA-256:
  `955bf85a5b28d7ec03e7a06c0b00c8d4b9b64a4fb0730b75e2f0fcaa53aef193`.

| Phase-10 input | SHA-256 |
| --- | --- |
| `manifest.py` | `fbc93cab00860d4c3f589e966748afd5871b5c45eefe2e7aaea2dddb20a10df3` |
| `FROZEN_MODULES.txt` | `e399f83a69d8a598b61d3c50114f34a3a3d4cad0681c37d670c88607f36cbcec` |
| `CURRENT_FROZEN_SOURCES.sha256` | `298816981c2e688fe402be3330378a2770b753bc293c9db32a88006877f0bd11` |

The closure contains exactly 42 project files, including the Phase-10 Setup
Assistant API, UI and generated web assets. It excludes `boot.py`, `main.py`,
board configuration, credentials, persistent data, tests and tools.

## A/B reproducibility proof

Two complete builds ran sequentially from an absent build directory at the
same canonical absolute path ending in
`build-DFR0975U_N16R8-PHASE10-B`. The first completed directory was retained
as `build-DFR0975U_N16R8-PHASE10-VALIDATION-PASS-1` before recreating the
canonical path.
All 15 compared outputs were byte-identical: bootloader, partition table,
application, combined image, UF2, final and combined configurations, four
flash-argument files, flasher JSON, frozen C, ELF and map.

The larger ELF, map, frozen C and duplicate UF2 remain reproducible and are
intentionally not retained in Git.

## Image, layout and memory gates

esptool 4.12 identified both bootloader and application as valid ESP32-S3
images with valid checksums and validation hashes. The final configuration
contains the required gates:

```text
CONFIG_IDF_TARGET="esp32s3"
CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y
CONFIG_SPIRAM=y
CONFIG_SPIRAM_MODE_OCT=y
CONFIG_SPIRAM_SPEED_80M=y
CONFIG_SPIRAM_BOOT_INIT=y
CONFIG_SPIRAM_MEMTEST=y
# CONFIG_SPIRAM_IGNORE_NOTFOUND is not set
CONFIG_SPIRAM_USE_MALLOC=y
CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=8192
CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=32768
```

Generated flash metadata is exactly:

```text
--flash_mode dio --flash_freq 80m --flash_size 16MB
0x0 bootloader/bootloader.bin
0x10000 micropython.bin
0x8000 partition_table/partition-table.bin
```

The decoded partition table contains NVS at `0x9000`, PHY at `0xf000`, one
3-MiB factory application at `0x10000`, and VFS at `0x310000` with size
`0xCF0000`. There is no OTA partition.

The combined image was independently compared with its three components at
`0x0`, `0x8000` and `0x10000`. Every gap byte is `0xff`; the file ends exactly
at `0x10000 + application size` and therefore contains no VFS bytes.

| Region/file | Size/result |
| --- | ---: |
| Bootloader | 19,232 B |
| Partition table | 3,072 B |
| Factory application | 2,050,848 B used of 3,145,728 B |
| Application margin | 1,094,880 B (about 35%) |
| Combined image | 2,116,384 B; exact end `0x204b20` |

The static settings prove only the intended memory configuration. Runtime
PSRAM, internal/DMA heap and Setup Assistant behaviour remain part of the
narrow post-flash target gate.

## Retained artifacts

Verify the retained set from `artifacts/` with:

```sh
shasum -a 256 -c SHA256SUMS
```

| File | Size | SHA-256 |
| --- | ---: | --- |
| `bootloader/bootloader.bin` | 19,232 B | `be074941dbcff048d22552208c318f53e9749142d77744ccfcad3744e5185985` |
| `partition_table/partition-table.bin` | 3,072 B | `518d9ab5063af998cce24c461cfccc51ed7c6f4084c12e2fc93cd5bbb3ccf979` |
| `micropython.bin` | 2,050,848 B | `d8fb33c0e43081d95744816cbbedf7b77281292d3c8458d14b1f50cf27f7b9ef` |
| `firmware.bin` | 2,116,384 B | `648256cc2fca0430cd53d721544d971ccd5c563f6fb8b0441682aafac465f4d1` |

The Phase-9 binaries remain unchanged. Its bootloader and partition-table
bytes are identical to this candidate. Consequently the narrowest next
operation is an app-only write of `micropython.bin` at `0x10000` without a
full-chip erase, bound to SHA-256
`d8fb33c0e43081d95744816cbbedf7b77281292d3c8458d14b1f50cf27f7b9ef`.
That operation has not yet been authorized or performed. Building and
verification did not access the board and do not enable automatic startup,
UART, heater control, RTC/I2C or 1-Wire.

## Target status

The earlier `8c8d0b...` baseline was authorized, fully read back and passed a
bounded phone gate that preserved the existing AP credential and submitted no
station WLAN. That remains valid evidence for the original transport,
single-listener, storage and cleanup seams, but it did not exercise the user
credential flow and is therefore no longer the final Phase-10 closure gate.

The new target gate must use the retained `d8fb33c0...` application and must
exercise one explicit AP-password replacement plus one explicit protected
station-WLAN profile in disposable configuration storage. It must also retain
the established one-listener, one-mutation, secret-redaction, heap, safety and
ordered-cleanup gates. Until the new hash is authorized, flashed, completely
read back and accepted on the DFR0975-U, Phase 10 remains target-pending.

Sanitized evidence is retained in
`../../captures/2026-09-01-dfr0975u-phase10-setup-assistant-gate.md`.
