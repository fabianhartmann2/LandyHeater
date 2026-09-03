# Phase 10.1 frozen firmware build record

Build date: 2026-09-03. Historical status: **flashed and read back correctly,
then rejected by real-target HTTP evidence**. This record intentionally pins
the original bytes. The corrected candidate is retained separately under
`firmware/phase10_1_fixed_frozen/`.

The final host run passed all 1,120 tests, including artifact-ledger, combined
image, ingress-isolation, captive-DNS and cooperative-cleanup checks.

## Pinned inputs

- repository baseline before Phase 10.1:
  `517b2e02753f99d80bc2ad9afdda6572dd45d12d`;
- 44 exact project-source files bound by `CURRENT_FROZEN_SOURCES.sha256`;
- MicroPython v1.28.0 commit
  `e0e9fbb17ed6fd06bb76e266ae554784c9c80804`;
- ESP-IDF v5.5.1 commit
  `fcae32885b0296b32044cb99ecbdc50d98dddb83`;
- `mpy-cross` v1.28.0, MPY format 6.3;
- esptool 4.12.0;
- board `DFR0975U_N16R8`, 16-MiB flash and Octal PSRAM;
- `SOURCE_DATE_EPOCH=1788100339`, `TZ=UTC`, `LC_ALL=C`,
  `PYTHONHASHSEED=0`, ccache disabled;
- dependency-lock SHA-256:
  `955bf85a5b28d7ec03e7a06c0b00c8d4b9b64a4fb0730b75e2f0fcaa53aef193`.

| Phase-10.1 input | SHA-256 |
| --- | --- |
| `manifest.py` | `19eae10212df5ede4b31727fd6fb2c7e9c207b6c06cc744c5eb78e6f09fa484d` |
| `FROZEN_MODULES.txt` | `3fe75d59bfc4922c1733a76ab78415f6ce0b45eb9e442d332d6338a7138f3f96` |
| `CURRENT_FROZEN_SOURCES.sha256` | `26e1b4b3224d74e770b8843fddc4960ad99793a868bd98f5f4ebe5a17a911a26` |

The closure adds the bounded captive-DNS adapter and fair discovery
composition to the historical Phase-10 application. It excludes `boot.py`,
`main.py`, board configuration, credentials, persistent data, tests and tools.

## Reproducibility proof

Two complete builds ran sequentially from absent target directories with the
same canonical source and build paths. The already-built `mpy-cross` binary was
passed explicitly so the ESP32 build variables could not leak into its host
build. All 15 compared outputs were byte-identical: bootloader, partition
table, application, combined image, UF2, final and combined configurations,
four flash-argument files, flasher JSON, frozen C, ELF and map.

ELF, map, frozen C and UF2 are intentionally not retained in Git.

## Image, layout and memory configuration

esptool 4.12 identified bootloader and application as valid ESP32-S3 images
with valid checksums and validation hashes. The resolved configuration contains
the required 16-MiB/Octal-PSRAM fail-closed settings:

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

The decoded layout remains NVS at `0x9000`, PHY at `0xf000`, one 3-MiB
factory application at `0x10000`, and VFS at `0x310000` with size `0xCF0000`.
There is no OTA partition. The combined image was compared byte-for-byte with
all three components and contains only `0xff` in its gaps.

| Region/file | Size/result |
| --- | ---: |
| Bootloader | 19,232 B |
| Partition table | 3,072 B |
| Factory application | 2,058,192 B used of 3,145,728 B |
| Application margin | 1,087,536 B (about 35%) |
| Combined image | 2,123,728 B; exact end `0x2067d0` |

The new application is 7,344 B larger than the last accepted Phase-10 image.
The static configuration proves only the intended memory setup; live GC,
internal and DMA-capable heap remain mandatory target measurements.

## Retained artifacts and rejection result

`artifacts/SHA256SUMS` binds the retained deployment subset. The app-only
candidate is:

```text
offset: 0x10000
size:   2058192 bytes
sha256: e378b4874d162f84b224396463b5384da9a55fcdd36a119ccee08b52d6f959e0
erase:  no full-chip erase
```

The authorized app-only flash and independent readback matched this hash.
Station DHCP, `heater.local`, memory gates and captive DNS succeeded; captive
DNS answered 58 of 58 observed queries without an error. HTTP rejected every
accepted socket before dispatch (`accepted=0`, `socket_errors=14`) because the
pinned MicroPython ESP32 port does not expose the assumed `getsockname()`
method. This image must not be flashed again. See
`captures/2026-09-03-dfr0975u-phase10-1-listener-correction.md`.
