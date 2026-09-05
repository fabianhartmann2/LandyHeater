# Phase 13 DS18B20 frozen firmware build record

Build date: 2026-09-05. Status: **source-mounted and frozen target
sensor-runtime gates, host regression, exact frozen-source closure, two
byte-identical canonical-path builds, offline artifact gates, authorized
app-only flash and independent full readback passed**.

This candidate adds the electrically accepted GPIO4 approval and the explicit
DS18B20 product lifecycle owner to the accepted Phase-11 application. Normal
`boot.py` and `main.py` behavior remains passive. UART, protocol TX, I2C and
both radio approvals remain closed.

## Pinned inputs

- repository baseline before the frozen candidate:
  `3bc6aa6bd3ec8298c52cbd41944cd3ff7ac604a8`;
- 47 exact project-source files bound by `CURRENT_FROZEN_SOURCES.sha256`;
- MicroPython v1.28.0 commit
  `e0e9fbb17ed6fd06bb76e266ae554784c9c80804`;
- ESP-IDF v5.5.1 commit
  `fcae32885b0296b32044cb99ecbdc50d98dddb83`;
- `mpy-cross` v1.28.0, MPY format 6.3, executable SHA-256
  `ceda0dfb2f800a3970f2be6a036aedfc3e9bcdb49579aeb3d5a0cb1a0390c849`;
- esptool 4.12.0;
- board `DFR0975U_N16R8`, 16-MiB flash and Octal PSRAM;
- `SOURCE_DATE_EPOCH=1788100339`, `TZ=UTC`, `LC_ALL=C`,
  `PYTHONHASHSEED=0`, ccache disabled;
- dependency-lock SHA-256:
  `955bf85a5b28d7ec03e7a06c0b00c8d4b9b64a4fb0730b75e2f0fcaa53aef193`.

| Phase-13 input | SHA-256 |
| --- | --- |
| `manifest.py` | `9bbd88b50c967c6525a2aab4498a16762c0e595e08891200de0a31d2fcd370b6` |
| `FROZEN_MODULES.txt` | `daa6e66f13fd79e0edada862a9eb374489155cf0fc70b97b7750b460f27e6b98` |
| `CURRENT_FROZEN_SOURCES.sha256` | `6b27b9848fecb1ceb9b00f860df6fe73e339047e51a704c43a7a7f70404f1358` |
| `artifacts/SHA256SUMS` | `d9e71be9aa009cef141e171964b00e0a3bedbd2e7bc9bca2f691f3bea5d1f2dd` |

The closure freezes `board_config.py` for the first time and includes
`app/sensor_composition.py`. It excludes `boot.py`, `main.py`, credentials,
persistent data, tests and tools.

## Reproducibility proof

Two clean builds used the same absent canonical target directory. The first
directory was moved aside before that exact path was recreated for the second
build. All 15 compared outputs were byte-identical: bootloader, partition
table, application, combined image, UF2, final and combined configurations,
four flash-argument files, flasher JSON, frozen C, ELF and map. A preliminary
build under a different directory name was not counted because ESP-IDF binds
an ELF-derived hash to the absolute build path.

## Image, layout and retained artifacts

Esptool identifies the application and bootloader as valid ESP32-S3 images
with valid checksums and validation hashes. The partition layout remains NVS
at `0x9000`, PHY at `0xf000`, a 3-MiB factory application at `0x10000`, and
VFS at `0x310000`. There is no OTA partition.

| Region/file | Size/result |
| --- | ---: |
| Bootloader | 19,232 B; byte-identical to accepted Phase 11 |
| Partition table | 3,072 B; byte-identical to accepted Phase 11 |
| Factory application | 2,098,656 B used of 3,145,728 B |
| Application growth from Phase 11 | 11,696 B |
| Application margin | 1,047,072 B (about 33%) |
| Combined image | 2,164,192 B; exact end `0x2105e0` |

`artifacts/SHA256SUMS` binds the retained deployment subset. The deployed
app-only image is:

```text
offset: 0x10000
size:   2098656 bytes
sha256: 8bf1fd20446bdedb04afe40daefd65378c671430679ee2416566136454aa6e13
erase:  no full-chip erase
```

This record is evidence only and cannot authorize a later flash.

## Target evidence and live UI gate

Before freezing, the same current source candidate was mounted over the
accepted Phase-11 image. With the soldered header and three assigned DS18B20
sensors it completed three production sampling cycles, nine valid readings,
read-only storage verification, radio-inactive checks and GPIO4 cleanup. The
exact target result was `PHASE13_SENSOR_RUNTIME_PASS_V1` and is recorded in
`../../captures/2026-09-05-dfr0975u-phase13-sensors-rtc.md`.

The owner then authorized that exact digest for an app-only write at `0x10000`
without full-chip erase. Esptool wrote 2,098,656 bytes and independently read
back the same range byte-for-byte with SHA-256
`8bf1fd20446bdedb04afe40daefd65378c671430679ee2416566136454aa6e13`.
No bootloader, partition-table or VFS write occurred.

After manual reset, a passive check confirmed MicroPython 1.28.0, the exact
DFR0975-U N16R8 identity, about 8.3 MiB free GC heap and both radios inactive.
It also found an older `/board_config.py` in VFS, which correctly retained the
Phase-11 closed 1-Wire flag and shadows `.frozen` under the default path
order. No file was changed. The bounded acceptance runner put `.frozen` first
only in RAM and proved that `board_config.py` and
`app/sensor_composition.py` resolved from the new frozen image with only the
1-Wire approval open.

The frozen runtime then completed three cycles and nine valid readings:
29.6250 °C roof tent, 29.0625 °C cabin and 32.6250 °C outside. Storage stayed
unchanged, both radios stayed inactive and GPIO4 was released. The exact
result was again `PHASE13_SENSOR_RUNTIME_PASS_V1`.

The subsequent isolated phone gate started the same frozen sensor owner, real
REST composition, embedded Web UI, captive DNS and one AP-bound port-80
listener. The captive portal loaded and the owner visually confirmed all three
temperatures in the UI. Three valid status responses reported 28.0000 °C roof
tent, 27.5000 °C cabin and 32.0625 °C outside. Production storage stayed
unchanged; isolated files and all radio/sensor/HTTP resources were cleaned up.
The exact result was `PHASE13_SENSOR_WEB_PHONE_PASS_V1`. This closes the live
DS18B20 REST/UI gate without another flash. Before any later automatic product
startup, startup ownership must either enforce `.frozen` precedence or remove
the obsolete VFS board profile through a separately authorized migration.
