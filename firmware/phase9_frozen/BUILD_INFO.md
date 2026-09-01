# Phase 9 frozen firmware build record

Build date: 2026-09-01. Status: **offline reproducibility and artifact gates,
authorized app-only flash, complete readback and Phase-9 target gate passed**.

No serial port, board, deploy, erase or write operation was used while
building or verifying this candidate. A flash requires a new authorization
that names the exact artifact, offset and erase policy.

## Pinned inputs

- repository baseline before the Phase-9 changes:
  `f5119bb0bec51053d92d852fe16806ea98046563`;
- the actual 42 project-source bytes are bound by
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

| Phase-9 input | SHA-256 |
| --- | --- |
| `manifest.py` | `a2a33c99989d048c548d71738bfb8a9f48fdab9b4fdc40a08327233da01a7fbc` |
| `FROZEN_MODULES.txt` | `e399f83a69d8a598b61d3c50114f34a3a3d4cad0681c37d670c88607f36cbcec` |
| `CURRENT_FROZEN_SOURCES.sha256` | `2bb7dc78e9b64e4e659328f7b7afdb333cadc5377cd84525d57f38b519ef0e76` |

The closure contains exactly 42 project files, including the Phase-9 web
application and generated web assets. It excludes `boot.py`, `main.py`, board
configuration, credentials, persistent data, tests and tools.

## A/B reproducibility proof

Two complete builds ran sequentially from an absent build directory at the
same canonical absolute path. All 15 compared outputs were byte-identical:
bootloader, partition table, application, combined image, UF2, final and
combined configurations, four flash-argument files, flasher JSON, frozen C,
ELF and map.

- proof A:
  `/private/tmp/landy-dfr0975u-s3-canonical/micropython/ports/esp32/build-DFR0975U_N16R8-PHASE9-PASS-A`;
- proof B/final:
  `/private/tmp/landy-dfr0975u-s3-canonical/micropython/ports/esp32/build-DFR0975U_N16R8`.

The final hashes of all 15 compared outputs were recorded during the build
gate. The larger ELF, map, frozen C and duplicate UF2 remain reproducible and
are intentionally not retained in Git.

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
| Factory application | 2,020,592 B used of 3,145,728 B |
| Application margin | 1,125,136 B (about 36%) |
| Combined image | 2,086,128 B; exact end `0x1FD4F0` |

The static settings prove only the intended memory configuration. Runtime
PSRAM, internal/DMA heap and loaded-product heap remain part of the narrow
post-flash target gate.

## Retained artifacts

Verify the retained set from `artifacts/` with:

```sh
shasum -a 256 -c SHA256SUMS
```

| File | Size | SHA-256 |
| --- | ---: | --- |
| `bootloader/bootloader.bin` | 19,232 B | `be074941dbcff048d22552208c318f53e9749142d77744ccfcad3744e5185985` |
| `partition_table/partition-table.bin` | 3,072 B | `518d9ab5063af998cce24c461cfccc51ed7c6f4084c12e2fc93cd5bbb3ccf979` |
| `micropython.bin` | 2,020,592 B | `a228d115cc2aba8569ddad3a46b9c038ab5f06e159bae3d4ded955345b6485e6` |
| `firmware.bin` | 2,086,128 B | `dcc7f68cb2e6ccfd57d3e436760d2400cac9748264ef97652a0c9102fdbfe49c` |

The Phase-8 binaries under `../dfr0975u_n16r8/artifacts/` remain unchanged.
Its bootloader and partition-table bytes are identical to the Phase-9
candidate. The least-destructive next operation is therefore an app-only
write of `micropython.bin` at `0x10000` without erase, bound to SHA-256
`a228d115cc2aba8569ddad3a46b9c038ab5f06e159bae3d4ded955345b6485e6`.
That exact operation was subsequently authorized and completed. It did not
enable automatic startup, UART, heater control, RTC/I2C or 1-Wire.

## Post-flash verification and target acceptance

The authorized operation wrote only the 2,020,592-byte application at
`0x10000` without a full erase. esptool's write verification passed, followed
by an independent complete application readback with the exact retained
SHA-256. Bootloader, partition table and VFS were not written.

The passive boot returned MicroPython 1.28.0 and the exact DFR0975-U machine
identity with both WLAN interfaces inactive. The subsequent bounded Phase-9
phone gate used one AP lifetime and one port-80 product listener. It completed
all nine UI resources and the four automatic read-only API requests, observed
no mutation or protocol call, enforced every GC-heap checkpoint at or above
32 KiB, preserved product storage and completed HTTP/REST/radio/file cleanup.
The independent post-check found both WLAN interfaces inactive, no isolated
test file and 8,319,520 bytes free GC heap.

Sanitized evidence is retained in
`../../captures/2026-09-01-dfr0975u-phase9-web-ui-gate.md`.
