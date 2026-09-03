# Phase 10 frozen firmware build record

Build date and target update: 2026-09-03. Status: **the bodyless-DELETE
correction passed the complete host regression, two byte-identical firmware
builds, all offline artifact gates, an authorized app-only flash with complete
readback, and the resumed real-phone timer-deletion gate**. The previous
credential-validation image remains historical target evidence. Its real-phone
flow reached timer deletion, where the browser exposed the corrected empty
request-body defect.

No serial port, board, deploy, erase or write operation was used while
building or verifying the candidate. It was flashed only after a new
authorization named the exact application hash, offset and erase policy. The
app-only operation preserved VFS until the resumed target gate completed and
removed its own isolated records.

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
| `CURRENT_FROZEN_SOURCES.sha256` | `6f87e8226d9370364921ec63a3fd1a6ce099f6251a83607c28b275092a52c649` |

The closure contains exactly 42 project files, including the Phase-10 Setup
Assistant API, UI and generated web assets. It excludes `boot.py`, `main.py`,
board configuration, credentials, persistent data, tests and tools.

## A/B reproducibility proof

Two complete builds ran sequentially from an absent build directory at the
same canonical absolute path ending in
`build-DFR0975U_N16R8-PHASE10-DELETEFIX`. The first completed directory was
retained as
`build-DFR0975U_N16R8-PHASE10-DELETEFIX-VALIDATION-PASS-1` before recreating
the canonical path.
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
| `micropython.bin` | 2,050,848 B | `9912c86513cc08e5b36f18c0705bf41bb3b6d592342b475170d3fce2b3780b63` |
| `firmware.bin` | 2,116,384 B | `e9cd0ea68a16740134ba5680da99fdeeff7b2f1bccb4bb0269bf73655b850f7e` |

The Phase-9 binaries remain unchanged. Its bootloader and partition-table
bytes are identical to this candidate. Consequently the narrowest possible
next operation is an app-only write of `micropython.bin` at `0x10000` without
a full-chip erase, bound to SHA-256
`9912c86513cc08e5b36f18c0705bf41bb3b6d592342b475170d3fce2b3780b63`.
That operation was subsequently authorized and performed. Only application
sectors `0x10000–0x204fff` were erased and written; bootloader, partition table
and VFS were untouched. Building and flashing did not enable automatic
startup, UART, heater control, RTC/I2C or 1-Wire.

## Target status

The earlier `8c8d0b...` baseline was authorized, fully read back and passed a
bounded phone gate that preserved the existing AP credential and submitted no
station WLAN. That remains valid evidence for the original transport,
single-listener, storage and cleanup seams, but it did not exercise the user
credential flow and is therefore no longer the final Phase-10 closure gate.

The previously retained `d8fb33c0...` application passed its write-time hash
check and an independent read of all 2,050,848 bytes. Passive boot returned
MicroPython 1.28.0, the exact DFR0975-U machine identity, 11 frozen assets and
both radios inactive.

The real phone flow explicitly replaced the AP password and added one
protected station-WLAN profile in disposable configuration storage. The
privileged target gateway recorded no rejected request, exactly one setup
mutation and exactly one successful mutation; success required exact
privileged readback of both expected write-only credentials. It observed 59
valid required responses and 62 accepted/closed connections with no observer
fault. The strict combined gate still timed out because at least one required
route lacked either its application or wire observation in that same run. The
old aggregate diagnostics cannot identify which route after cleanup, so the
runner now emits separate missing-route lists and sanitized server counters.
The functional phone flow will not be repeated merely to improve diagnostics.

The subsequent end-to-end integration exercise used disposable A/B paths and
never touched production configuration. It proved a protected station-WLAN
profile, DHCP after a physical reset, AP-password replacement and re-login,
plus durable timer creation and editing. Timer deletion returned HTTP 422 with
`request_body_not_allowed`: the UI helper always attached `body: ""` even for
a bodyless `DELETE`, while the REST boundary correctly rejects every DELETE
body. The helper now omits both `Content-Type` and the `body` property whenever
no payload is supplied. A regression test locks that browser request shape.
The isolated configuration and edited inactive timer were retained at that
failure point so the corrected image could resume directly at deletion after
a separately authorized app-only flash.

That exact app-only flash later passed both its write-time verification and an
independent byte-for-byte read of all 2,050,848 application bytes. After a
physical reset, passive USB identity matched MicroPython 1.28.0 and the exact
DFR0975-U machine string; both radios were inactive. The retained stage-2
exercise then proved station DHCP, replacement-key AP re-authentication and a
successful bodyless timer deletion. Fresh storage reload proved the timer was
absent with exactly one remaining generation increment. Requested State stayed
OFF, no heater-protocol call occurred and production storage was unchanged.
The PASS path removed every isolated record and shut both radios down; an
independent postcheck reported no isolated filename and 8,319,056 bytes of
free GC heap.

Sanitized baseline and credential evidence are retained in
`../../captures/2026-09-01-dfr0975u-phase10-setup-assistant-gate.md` and
`../../captures/2026-09-03-dfr0975u-phase10-credential-gate.md`. The real
integration sequence, deletion diagnosis and corrected preflash gate are in
`../../captures/2026-09-03-dfr0975u-phase10-integration-delete-preflash.md`.
