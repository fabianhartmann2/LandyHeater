# DFR0975-U N16R8 MicroPython firmware

Status: **reproducibly built and statically verified; not flashed**.

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

## Mandatory next gate

Before any write, verify `artifacts/SHA256SUMS`, the private factory backup,
the physical N16R8 identity, the partition layout and USB-only wiring, then
obtain a fresh explicit approval bound to the exact image hash and operation.
That approval must state whether the factory flash is fully erased or which
complete bootloader/partition/application/VFS regions are replaced; app-only
flashing is not an accepted first-board path.

After an approved flash, the first boot must remain USB-only and passive. Run
`tools/dfr0975u_memory_probe.py` before enabling a radio or peripheral. It
must verify the exact firmware identity and report the MicroPython GC heap,
PSRAM, general internal 8-bit heap and DMA-capable internal heap separately.
Only later, separately authorized radio/storage/safety gates may advance
Phase 8.
