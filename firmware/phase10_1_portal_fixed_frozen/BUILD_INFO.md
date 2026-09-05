# Portal-corrected Phase 10.1 frozen firmware build record

Build date: 2026-09-03. Status: **host regression, exact frozen-source
closure, two byte-identical canonical-path builds, offline artifact gates,
authorized target flash and independent full application readback passed;
runtime acceptance pending**.

The real DFR0975-U target exposed a response-boundary defect in the preceding
listener-corrected candidate. Stations-DHCP and mDNS reached
`192.168.36.114`, captive DNS answered 5 of 5 requests, and the AP listener
accepted one phone connection. The response was then closed with
`response_contract_failed` before any response byte was sent. The cause was
fully local: status 302 was absent from the bounded HTTP encoder allowlist,
and the captive response duplicated the encoder-owned `Cache-Control` header.

The correction adds `302: "Found"`, removes the duplicate application header,
and exercises the complete captive response through `encode_bytes_response`.
Listener topology, radio control, security policy and heater locks are
unchanged.

The final repository run passed all 1,143 tests, including the historical
artifact checks and the new portal-corrected source and deployment gates.

## Pinned inputs

- repository baseline before this correction:
  `6ec388647f900249c404fee79ec89899a8e97071`;
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

| Portal-corrected input | SHA-256 |
| --- | --- |
| `manifest.py` | `77932018b949d3e6f6be380b437e224302d75b6ff6138d01459c9e26e2d6ad87` |
| `FROZEN_MODULES.txt` | `3fe75d59bfc4922c1733a76ab78415f6ce0b45eb9e442d332d6338a7138f3f96` |
| `CURRENT_FROZEN_SOURCES.sha256` | `5d3b7148aeb5b6a7fca52d4672f6bac834696a43eb1a688f5445c2f20a710cac` |
| `artifacts/SHA256SUMS` | `e54bfeece1718b48af6dc19a1df484dae0bde49429549148b8a5d97d9fc113ec` |

The closure excludes `boot.py`, `main.py`, board configuration, credentials,
persistent data, tests and tools.

## Reproducibility proof

Two complete builds ran from an absent canonical target directory. The first
directory was archived before the same canonical path was recreated for the
second build. All 15 compared outputs were byte-identical: bootloader,
partition table, application, combined image, UF2, final and combined
configurations, four flash-argument files, flasher JSON, frozen C, ELF and map.
The `mpy-cross` executable was explicitly pinned so the ESP build directory
was not inherited by its host build.

## Image, layout and retained artifacts

esptool 4.12 identifies the application and bootloader as valid ESP32-S3
images with valid checksums and validation hashes. The partition layout stays
NVS at `0x9000`, PHY at `0xf000`, a 3-MiB factory application at `0x10000`,
and VFS at `0x310000`. There is no OTA partition.

| Region/file | Size/result |
| --- | ---: |
| Bootloader | 19,232 B; unchanged |
| Partition table | 3,072 B; unchanged |
| Factory application | 2,058,368 B used of 3,145,728 B |
| Application margin | 1,087,360 B (about 35%) |
| Combined image | 2,123,904 B; exact end `0x206880` |

`artifacts/SHA256SUMS` binds the retained deployment subset. The app-only
candidate is:

```text
offset: 0x10000
size:   2058368 bytes
sha256: b3f16a7e4160cdd2c58cf78d25c6ebb3377a7d0438b5384054d679c19c03ad8f
erase:  no full-chip erase
```

## Authorized target flash and readback

On 2026-09-05 the owner supplied the exact hash-bound, USB-only app-flash
approval for the candidate above. The connected target was rechecked before
the write as ESP32-S3 revision 0.1 with 8 MiB embedded PSRAM and 16 MiB flash.

Only the 2,058,368-byte factory application was written at `0x10000`. The
operation erased only `0x10000` through `0x206fff`; there was no full-chip
erase and no write to the bootloader, partition table or VFS. Esptool's write
verification passed. A separate full readback of exactly 2,058,368 bytes from
`0x10000` was then compared byte-for-byte with the retained artifact. Both
files produced SHA-256
`b3f16a7e4160cdd2c58cf78d25c6ebb3377a7d0438b5384054d679c19c03ad8f`.

The next gate is the one combined AP/DHCP/mDNS/portal/security runtime test.
Automatic product startup and all heater hardware remain disabled.
