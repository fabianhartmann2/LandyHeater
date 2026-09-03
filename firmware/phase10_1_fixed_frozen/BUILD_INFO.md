# Corrected Phase 10.1 frozen firmware build record

Build date: 2026-09-03. Status: **corrected host regression, frozen-source
closure, two byte-identical builds and all offline artifact gates passed**.
No serial port, board, deploy, erase, flash or write operation was used for
this corrected candidate. Runtime resource and phone/network acceptance remain
pending.

The final host run passed all 1,135 tests. The design uses two explicitly
bound TCP/80 listeners because the pinned MicroPython ESP32 socket API does
not expose accepted-socket local-address introspection.

## Pinned inputs

- repository baseline before the correction:
  `963793dc71ee31ba5c930f9a5b72ac159f9b3fb2`;
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

| Corrected Phase-10.1 input | SHA-256 |
| --- | --- |
| `manifest.py` | `c437b1465e1a7e4689b7af989584d9b5a99947ff1a16066e5e34610cae3db019` |
| `FROZEN_MODULES.txt` | `3fe75d59bfc4922c1733a76ab78415f6ce0b45eb9e442d332d6338a7138f3f96` |
| `CURRENT_FROZEN_SOURCES.sha256` | `46811a5639435d13879dae2a68d329e6623108b9f55d460d589dbbf552cc9830` |

The closure excludes `boot.py`, `main.py`, board configuration, credentials,
persistent data, tests and tools.

## Reproducibility proof

Two complete builds ran sequentially from absent target directories with the
same canonical source and build paths. All 15 compared outputs were
byte-identical: bootloader, partition table, application, combined image,
UF2, final and combined configurations, four flash-argument files, flasher
JSON, frozen C, ELF and map. ELF, map, frozen C and UF2 are intentionally not
retained in Git.

## Image, layout and memory configuration

esptool 4.12 identified bootloader and application as valid ESP32-S3 images
with valid checksums and validation hashes. The resolved configuration
contains the required 16-MiB/Octal-PSRAM fail-closed settings:

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
| Factory application | 2,058,400 B used of 3,145,728 B |
| Application margin | 1,087,328 B (about 35%) |
| Combined image | 2,123,936 B; exact end `0x2068a0` |

The corrected application is 208 B larger than the rejected first Phase-10.1
candidate and 7,552 B larger than the accepted Phase-10 image. Static
configuration proves only the intended memory setup; live GC, internal and
DMA-capable heap remain mandatory target measurements with both listeners.

## Retained artifacts and next gate

`artifacts/SHA256SUMS` binds the retained deployment subset. The app-only
candidate is:

```text
offset: 0x10000
size:   2058400 bytes
sha256: a760f73722ea4f6c5f9a85842498092b628a6e33a186b5c62179d79a1697cd18
erase:  no full-chip erase
```

The bootloader and partition table are byte-identical to Phase 10, so the
narrowest next operation is app-only. It requires a new approval naming this
exact hash, offset and erase policy. Automatic product startup and all heater
hardware remain disabled.
