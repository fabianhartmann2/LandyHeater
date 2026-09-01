# DFR0975-U N16R8 MicroPython firmware

Status: **reproducibly built, statically verified, fully flashed after exact
authorization, and USB-only memory-gate verified**.

This directory contains the board overlay and retained build artifacts for the
physically confirmed DFRobot FireBeetle 2 ESP32-S3-U, SKU `DFR0975-U`, PCB
revision `V1.0`, module `ESP32-S3-WROOM-1U-N16R8`. It is a separate target;
none of the classic-ESP32 DFR0654 images or offsets are transferable.

The firmware is MicroPython 1.28.0 for an ESP32-S3 with 16 MiB Quad-SPI flash
and 8 MiB Octal PSRAM. It freezes the same 40-file Phase-8 product closure as
`../phase8_frozen/manifest.py`. Product `board_config.py`, `boot.py`, `main.py`,
credentials, persistent data, tests and diagnostic tools are intentionally not
frozen.

## Retained contents

- `boards/DFR0975U_N16R8/`: exact MicroPython board overlay used by the build;
- `dependencies.lock.esp32s3`: exact ESP-IDF managed-component lockfile;
- `artifacts/`: bootloader, partition table, application, combined image,
  generated flash metadata, configurations and `SHA256SUMS`;
- `BUILD_INFO.md`: pinned toolchain, layout, A/B reproducibility evidence and
  the remaining hardware gates.

The combined `firmware.bin` ends after the application and does not contain a
VFS image. Merely retaining an image does not authorize a flash.

The retained `flash_args` and `flasher_args.json` paths match the packaged
`bootloader/` and `partition_table/` subdirectories. Generated app-only and
component-only argument files are deliberately omitted: the first flash of an
unchanged factory board must never assume that its bootloader or partition
table is compatible. The final resolved `sdkconfig` is authoritative;
`sdkconfig.combined` also contains upstream defaults that the board overlay
overrides, including generic 4-MiB values.

## Layout

| Region | Offset/range | Size |
| --- | ---: | ---: |
| Bootloader | `0x000000` | 19,232 B |
| Partition table | `0x008000` | 3,072 B |
| NVS | `0x009000–0x00F000` | 24 KiB |
| PHY | `0x00F000–0x010000` | 4 KiB |
| Factory application | `0x010000–0x310000` | 3 MiB |
| LittleFS VFS | `0x310000–0x1000000` | 12.9375 MiB |

The 1,964,688-byte application leaves 1,181,040 bytes, about 38%, in its
slot. There is one factory application and no OTA slot; recovery therefore
remains a manual USB operation.

## Offline reproduction

Use the pinned revisions in `BUILD_INFO.md`, an already populated ESP-IDF
managed-component cache and a clean canonical source path. Copy the retained
`DFR0975U_N16R8` directory into `ports/esp32/boards/` of the pinned
MicroPython checkout. Verify the project closure first:

```sh
cd /private/tmp/landy-dfr0975u-s3-canonical/project
shasum -a 256 -c firmware/phase8_frozen/CURRENT_FROZEN_SOURCES.sha256
```

Then enter the pinned ESP-IDF 5.5.1 environment and build `mpy-cross`
separately before the ESP32 target:

```sh
export SOURCE_DATE_EPOCH=1788100339
export TZ=UTC
export LC_ALL=C
export PYTHONHASHSEED=0
export IDF_CCACHE_ENABLE=0
export CCACHE_DISABLE=1

make -C /private/tmp/landy-dfr0975u-s3-canonical/micropython/mpy-cross -j4

make -C /private/tmp/landy-dfr0975u-s3-canonical/micropython/ports/esp32 \
  -j4 \
  BOARD=DFR0975U_N16R8 \
  FROZEN_MANIFEST=/private/tmp/landy-dfr0975u-s3-canonical/project/firmware/phase8_frozen/manifest.py
```

Do not pass `FROZEN_MANIFEST` while building `mpy-cross`. Exact byte
reproduction is path-bound because ESP-IDF embeds an ELF-derived hash; use the
same canonical absolute paths. The two recorded clean builds at that path
matched byte-for-byte for all 15 compared outputs.

No build instruction in this directory invokes `deploy`, `erase`, `flash` or
`write_flash`.

## First-flash result and next gates

The exact combined image was fully flashed after a fresh hash-bound approval
and esptool write verification. Passive USB identity matched MicroPython 1.28.0
and the custom DFR0975-U machine string. The corrected transient memory probe
reported 8 MiB PSRAM, 8,216,128 B GC free, 274,191 B internal free and
266,475 B internal-DMA free; both WLAN interfaces remained inactive. No
diagnostic file was persisted to the board. Exact evidence is in
`../../captures/2026-09-01-dfr0975u-first-flash-memory-gate.md`.

The retained artifact does not authorize another erase or flash. Manual
`BOOT`/`RST` ROM recovery and isolated VFS/A-B storage subsequently passed;
automatic USB control-line recovery is not reliable and physical button access
must remain available. S3 UART/level interface, functional radio/DHCP and
Phase-8 product acceptance remain separate later gates. Internal and DMA
headroom must be measured again under the bounded WLAN/product load. Exact
recovery/storage evidence is in
`../../captures/2026-09-01-dfr0975u-usb-recovery-storage-gate.md`.
