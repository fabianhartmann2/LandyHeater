# DFR0975-U N16R8 build record

Build date: 2026-09-01. Status: **offline artifact gate passed; never
flashed**.

## Pinned inputs

- project source: `cb27b4f39954d44ed7553d13f63d6d5166129540`;
- MicroPython v1.28.0:
  `e0e9fbb17ed6fd06bb76e266ae554784c9c80804`;
- ESP-IDF v5.5.1:
  `fcae32885b0296b32044cb99ecbdc50d98dddb83`;
- Python 3.12.7;
- `xtensa-esp-elf-gcc` 14.2.0, Espressif
  `esp-14.2.0_20241119`;
- `mpy-cross` v1.28.0, MPY format 6.3;
- esptool 4.12.0;
- `SOURCE_DATE_EPOCH=1788100339`, `TZ=UTC`, `LC_ALL=C`,
  `PYTHONHASHSEED=0`, ccache disabled;
- retained ESP32-S3 dependency lock SHA-256:
  `955bf85a5b28d7ec03e7a06c0b00c8d4b9b64a4fb0730b75e2f0fcaa53aef193`;
- dependency manifest hash:
  `40b684ab14058130e675aab422296e4ad9d87ee39c5aa46d7b3df55c245e14f5`.

The lock pins `mdns` 1.1.0 with component hash
`46ee81d32fbf850462d8af1e83303389602f6a6a9eddd2a55104cb4c063858ed`
and TinyUSB commit `e4c0ec3caab3d9c25374de7047653b9ced8f14ff` with
component hash
`ee1c962cff61eb975d508258d509974d58031cc27ff0d6c4117a67a613a49594`.

## Frozen closure

The build verified 40/40 project sources against
`../phase8_frozen/CURRENT_FROZEN_SOURCES.sha256` before compilation.

| Input | SHA-256 |
| --- | --- |
| `../phase8_frozen/manifest.py` | `c51c9848aef816edfd4b3cc300bd9f4645d46b1e887df1afd1a04bc662b417a8` |
| `../phase8_frozen/FROZEN_MODULES.txt` | `c5484c8284856f8e267d05aa1b0709859b99335a527d5b48ce84a6bdd9485d5e` |
| `../phase8_frozen/CURRENT_FROZEN_SOURCES.sha256` | `5a1f1df35de2ea972e9f9781a683bb2de850980f325d3396116799cda895f2d0` |

The generated closure contains 28 upstream plus 40 project modules. The
project paths are 5 under `adapters`, 15 under `app`, 4 under `hardware`, 6
under `protocol` and 10 under `services`. `board_config.py`, safe-boot files,
credentials, persistence, tests and tools remain external.

## Board-overlay provenance

| File | SHA-256 |
| --- | --- |
| `mpconfigboard.cmake` | `cb2f44ca90a167611436facb4ed4e6b25347f3370ebe444c040318e5ed4756d6` |
| `mpconfigboard.h` | `5f115c9a4f52893cd9983a267ac6df49aad4ff3822c955bdf05524a40e5de379` |
| `sdkconfig.board` | `a770602b9664c87c8cf12ae5bf87cebe1b1e018a6281783dc68100364b7167a6` |
| `partitions-16MiB.csv` | `f291f7dc5e7fd02497a827b92b9f1bc496086816875c9aa6f89bac97f32e495e` |

The standard `ESP32_GENERIC_S3` `SPIRAM_OCT` build was inspected but rejected
as the final target because it retains a 4-MiB image header and the generic
`partitions-4MiBplus.csv` app boundary. The custom board makes the received
N16R8 flash and fail-closed Octal-PSRAM assumptions explicit.

## A/B reproducibility proof

Two complete clean builds ran sequentially at the same canonical absolute
path. All 15 compared outputs were byte-identical, including bootloader,
partition table, app, combined image, UF2, both configurations, all generated
flash arguments, frozen C, ELF and map.

- proof A:
  `/private/tmp/landy-dfr0975u-s3-canonical-pass1/micropython/ports/esp32/build-DFR0975U_N16R8`;
- proof B/final:
  `/private/tmp/landy-dfr0975u-s3-canonical/micropython/ports/esp32/build-DFR0975U_N16R8`.

Builds at different absolute source paths initially differed only in embedded
32-byte ELF-hash fields. The fixed path is therefore part of this exact-byte
reproduction contract. Managed components were pre-populated from the pinned
lock and no network fetch was needed for the final pair.

## Image and partition gates

Image inspection passed for ESP32-S3 chip ID 9, 16-MiB header, 80-MHz flash,
checksums and validation hashes, ESP-IDF 5.5.1, 64-KiB MMU pages and secure
version 0. The app ELF hash matched the built ELF.

Generated flash metadata contains:

```text
--flash_mode dio --flash_freq 80m --flash_size 16MB
0x0 bootloader/bootloader.bin
0x10000 micropython.bin
0x8000 partition_table/partition-table.bin
```

Those referenced paths exist exactly as packaged under `artifacts/`. The
generated app-only and component-only argument files were verified in A/B but
are not retained, because a first flash over unknown factory layout must use a
complete compatible bootloader, partition table and application operation.

The DIO image header is expected for the QIO configuration: the ESP-IDF
bootloader starts in DIO and changes the flash controller to Quad-I/O during
initialization.

The combined image was independently split and compared at offsets `0x0`,
`0x8000` and `0x10000`; every gap byte was `0xff`, and the file ended exactly
at app offset plus app size. It contains no VFS bytes.

The factory app slot is 3 MiB. The app uses 1,964,688 bytes and leaves
1,181,040 bytes, about 38%. The explicit VFS is `0xCF0000` bytes. There is no
OTA partition.

## PSRAM and internal-memory build gates

The final resolved `sdkconfig` contains:

```text
CONFIG_IDF_TARGET="esp32s3"
CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y
CONFIG_SPIRAM=y
CONFIG_SPIRAM_MODE_OCT=y
CONFIG_SPIRAM_TYPE_AUTO=y
CONFIG_SPIRAM_SPEED_80M=y
CONFIG_SPIRAM_BOOT_INIT=y
# CONFIG_SPIRAM_IGNORE_NOTFOUND is not set
CONFIG_SPIRAM_USE_MALLOC=y
CONFIG_SPIRAM_MEMTEST=y
CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=8192
CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=32768
CONFIG_FREERTOS_TASK_CREATE_ALLOW_EXT_MEM=y
CONFIG_SPIRAM_ALLOW_STACK_EXTERNAL_MEMORY=y
CONFIG_MBEDTLS_DEFAULT_MEM_ALLOC=y
```

`sdkconfig.combined` is retained only as generated input provenance. It still
lists upstream defaults such as generic 4-MiB flash and permissive PSRAM
handling which are overridden later; it must not be interpreted without the
final `sdkconfig`.

MicroPython starts with a 64-KiB GC heap and uses split-heap automatic growth.
Allocations above 8 KiB prefer PSRAM while 32 KiB remain reserved internally.
Missing Octal PSRAM fails the boot configuration instead of being ignored.
The linker report showed 192,633 bytes free DIRAM; this is only a static link
fact, not runtime heap proof.

The postflash USB-only gate subsequently confirmed 8-MiB PSRAM registration,
8,216,128 bytes free MicroPython GC capacity, 274,191 bytes free internal IDF
heap and 266,475 bytes free DMA-capable internal IDF heap. Both largest
internal blocks were 196,608 bytes. The corrected
`../../tools/dfr0975u_memory_probe.py` measures the physical flash through
`esp` and the distinct IDF heaps through `esp32`; it enabled no radio or
product peripheral. Loaded WLAN headroom remains a later target gate.

## Retained artifacts

The repository retains the artifacts required for inspection and a later
explicitly approved USB flash. Verify them from `artifacts/` with:

```sh
shasum -a 256 -c SHA256SUMS
```

Main results:

| File | Size | SHA-256 |
| --- | ---: | --- |
| `bootloader/bootloader.bin` | 19,232 B | `be074941dbcff048d22552208c318f53e9749142d77744ccfcad3744e5185985` |
| `partition_table/partition-table.bin` | 3,072 B | `518d9ab5063af998cce24c461cfccc51ed7c6f4084c12e2fc93cd5bbb3ccf979` |
| `micropython.bin` | 1,964,688 B | `917d8d28eaab54245fe0518606a0bc69a8f73f1fe66aab27ee6e1daa229b2291` |
| `firmware.bin` | 2,030,224 B | `4c0ed87982e8d643351034903acc0850acbceff6bcdceab622472eaf0c53b226` |

The much larger ELF, map, generated frozen C and duplicate UF2 were verified
in both builds but are reproducible and intentionally not retained. Their
proof hashes are recorded in the preflash capture.

No serial port, board, `deploy`, erase or write operation was used during any
build or static verification step.
