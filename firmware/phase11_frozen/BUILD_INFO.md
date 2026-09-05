# Phase 11 frozen firmware build record

Build date: 2026-09-05. Status: **host regression, exact frozen-source
closure, two byte-identical canonical-path builds, offline artifact gates,
authorized app-only flash, independent full readback and bounded target
runtime acceptance passed**.

The candidate adds the bounded Phase-11 event, diagnostics and capture path to
the accepted Phase-10.1 image. It does not enable automatic product startup,
heater UART, I2C or 1-Wire hardware.

## Pinned inputs

- repository baseline before Phase 11:
  `fe314cae4abdb4a1040628a24eb7c69939fac30d`;
- 45 exact project-source files bound by `CURRENT_FROZEN_SOURCES.sha256`;
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

| Phase-11 input | SHA-256 |
| --- | --- |
| `manifest.py` | `1320291544c4252734735bb27cdee8f786c93a930f8f7aabf3e7b5b8a0730cdc` |
| `FROZEN_MODULES.txt` | `c2639641f3a4d1890e28903608023b6e503b378b8a0448a850f04d3141eab034` |
| `CURRENT_FROZEN_SOURCES.sha256` | `3240e2d2a023a9b0a87661c9869a6e1cfa7f4ef7a6b6648f05a0e81d1aa0a03f` |
| `artifacts/SHA256SUMS` | `ff49a63c890860a0055eb465aa78613152970725acc6cef2dfdfb03f821a90d1` |

The closure excludes `boot.py`, `main.py`, board configuration, credentials,
persistent data, tests and tools.

## Reproducibility proof

Two clean builds used the same absent canonical target directory. The first
directory was moved aside before that canonical path was recreated for the
second build. All 15 compared outputs were byte-identical: bootloader,
partition table, application, combined image, UF2, final and combined
configurations, four flash-argument files, flasher JSON, frozen C, ELF and map.
The already-built, pinned `mpy-cross` executable was supplied explicitly.

## Image, layout and retained artifacts

Esptool identifies application and bootloader as valid ESP32-S3 images with
valid checksums and validation hashes. The partition layout stays NVS at
`0x9000`, PHY at `0xf000`, a 3-MiB factory application at `0x10000`, and VFS
at `0x310000`. There is no OTA partition.

| Region/file | Size/result |
| --- | ---: |
| Bootloader | 19,232 B; byte-identical to accepted Phase 10.1 |
| Partition table | 3,072 B; byte-identical to accepted Phase 10.1 |
| Factory application | 2,086,960 B used of 3,145,728 B |
| Application growth from Phase 10.1 | 28,592 B |
| Application margin | 1,058,768 B (about 34%) |
| Combined image | 2,152,496 B; exact end `0x20d830` |

`artifacts/SHA256SUMS` binds the retained deployment subset. The unflashed
app-only candidate is:

```text
offset: 0x10000
size:   2086960 bytes
sha256: 274234961f43551526b843ca7b27b3ead594cb5e93bf079b39f4ea838ab2c566
erase:  no full-chip erase
```

This record is evidence only. It is not flash authorization.

## Target evidence

The owner authorized this exact application digest for an app-only write at
`0x10000` without full-chip erase. Esptool wrote 2,086,960 bytes and an
independent read of the same range was byte-identical with SHA-256
`274234961f43551526b843ca7b27b3ead594cb5e93bf079b39f4ea838ab2c566`.
No bootloader, partition-table or VFS write occurred.

After manual reset, a passive USB gate confirmed MicroPython 1.28.0, the
DFR0975-U N16R8 identity, both radios inactive, about 8.3 MiB free GC heap and
the frozen diagnostics hub's event/protocol/redaction/cleanup paths. The
bounded phone gate then delivered the lazy diagnostics view, 91 combined live
reads, one named RAM-capture start/stop and one validated JSON export with
synthetic event/protocol data. It completed 162 HTTP requests without parser
or server faults. The final USB postcheck reported both radios inactive,
8,320,176 free heap bytes and no isolated test files.

The target runner initially emitted a false-negative terminal verdict after
all functional criteria had passed because its inherited wire observer names
GET requests only while the completion predicate also required a named
POST/DELETE target. The predicate and the initially incorrect diagnostics
fragment expectation are corrected and regression-covered in the repository.
See `captures/2026-09-05-dfr0975u-phase11-diagnostics-gate.md`. Real heater
UART and electrical peripheral testing remain Phase 13.
