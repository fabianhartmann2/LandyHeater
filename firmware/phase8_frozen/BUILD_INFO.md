# Phase 8 frozen firmware build record

Status: **flashed capacity-test candidate; minimal frozen AP/HTTP phone probe
passed; not approved as production firmware**.

Build completed on 2026-08-11 at approximately 20:06 UTC. No serial port or
board was accessed while preparing or verifying these artifacts. The later
board flash and target checks were separately authorized and are recorded
below; they do not change that build-time fact.

## Pinned inputs

- MicroPython `v1.28.0`, commit
  `e0e9fbb17ed6fd06bb76e266ae554784c9c80804`
- ESP-IDF `v5.5.1`, commit
  `fcae32885b0296b32044cb99ecbdc50d98dddb83`
- Python `3.12.7`
- `xtensa-esp-elf-gcc 14.2.0` from
  `esp-14.2.0_20241119`
- `mpy-cross` v1.28.0, MPY format 6.3, `xtensawin`
- Board `ESP32_GENERIC`, 4 MiB, no SPIRAM, no OTA slot
- Frozen manifest SHA-256:
  `c51c9848aef816edfd4b3cc300bd9f4645d46b1e887df1afd1a04bc662b417a8`

The local ESP-IDF 6.0.2 installation was not used.

## Frozen-module proof

- Standard ESP32 manifest: 29 modules
- Candidate: 69 modules
- Difference: exactly the 40 paths in `FROZEN_MODULES.txt`
- Missing standard modules: 0
- Extra paths not declared by the project manifest: 0

`FROZEN_SOURCES.sha256` binds the source bytes used by the successful build.
Those exact 40 files are retained under `build_sources_2026-08-11/`, where the
ledger verifies 40/40. `CURRENT_FROZEN_SOURCES.sha256` instead binds the
current top-level closure. The two ledgers differ only for
`adapters/micropython_http_server.py`, which received a later write-deadline
fix and is not part of the retained 2026-08-11 binaries. The manifest excludes
`boot.py`, `main.py`, `board_config.py`, tools, tests, captures, persistent
configuration, and credential files.

## Layout and size

| Region | Offset | Size/result |
| --- | ---: | ---: |
| Bootloader | `0x1000` | 22,864 B; 5,808 B region margin |
| Partition table | `0x8000` | 3,072 B |
| Factory application | `0x10000` | 1,971,248 B used of 2,031,616 B |
| Factory application margin | — | 60,368 B (`0xEBD0`, about 3%) |
| VFS boundary | `0x200000` | not crossed |

The partition table contains NVS (24 KiB), PHY (4 KiB), and one factory app
(1,984 KiB). Secure Boot and Flash Encryption are disabled in this candidate.
There is no automatic OTA rollback slot.

ESP-IDF's final size report records 1,296,028 B flash code, 531,792 B flash
data, 120,215 B IRAM used (91.72%, 10,857 B remaining), and 54,512 B static
DRAM used (43.76%, 70,068 B remaining). These link-time figures did not prove
runtime Python heap. The later narrow target probes passed, while the complete
post-frozen product composition still requires its combined acceptance.

Artifact hashes are recorded in `artifacts/SHA256SUMS`. In particular:

- combined `firmware.bin`, 2,032,688 B:
  `9fb71019028d35863775bbc0beb1add9a41825ee280e98e4515caa4fc44d90b3`
- factory `micropython.bin`, 1,971,248 B:
  `d46b7a2c71db493483adcdeb92a33a508696a7d99aaa051e21957032ae17fc5b`

The official generic v1.28.0 rollback image was downloaded from the official
MicroPython release page and verified locally. The binary is intentionally
excluded from this repository; `rollback/SHA256SUMS` retains its verification
record. Its verified size is 1,760,192 B and its SHA-256 is
`cd7820d02c35d34dd403b44263129c6a511b350aea8446c229890753fe240784`.

The linker map is also intentionally excluded because it is regenerable,
large when expanded, and contains local temporary build paths.

## Verification

- Complete frozen C generation and ESP32 link: PASS
- ESP image checksum and validation hash: PASS
- Frozen manifest delta: exact 40/40 match
- Temperature-manager focused tests: 23/23 PASS
- Full host suite after the compatibility correction: 1,000/1,000 PASS
- Production-source scan: no non-finite or Binary32-overflowing float literal
- Candidate byte scan: no local home/project/temp-build path and no known old
  radio-test password

The build initially exposed an unnecessary `1e300` validation sentinel in
`app/temperature_manager.py`. The constructor now validates directly against
the same DS18B20 limits it already enforced afterwards (`-55.0` to `125.0`).
Public behavior is unchanged; extreme-value and valid-custom-range tests were
added. The subsequent full frozen build passed.

## Pre-flash gates completed on 2026-08-11

Before the test flash, the exact ESP32 and 4-MiB layout were re-identified; a
confidential, verified 4,194,304-byte flash backup and logical VFS backup were
made; only USB remained connected; and explicit approval was received for the
custom-firmware flash without a full erase. The official rollback image was
already verified. These facts authorize only that completed operation, not a
future flash.

After flashing, the normal boot contract stayed passive. `.frozen` was placed
ahead of filesystem package copies only inside the explicit probes, so their
module origins could be verified without changing normal startup.

## Postflash verification and narrow acceptance

- Flash programming and image verification: PASS
- Complete 1,971,248-byte application readback: exact match to
  `micropython.bin`
- Bootloader, partition table and PHY readback: exact matches
- Whole combined-file difference: 83 bytes confined to the NVS region after
  boot; no application byte differed
- Passive first boot: MicroPython 1.28.0, both radios inactive, all hardware
  approvals false
- Frozen imports: exact 40/40 project modules from `.frozen`; 110,320 bytes
  free after the full import
- Isolated software-only REST smoke: 4/4 iterations, 12/12 completed
  fake-socket responses, exact `PHASE8_USB_REST_SMOKE_PASS_V1`
- Real AP-first phone probe: one AP peer at `192.168.4.2`, one allowlisted
  request, two completely written responses, exact
  `PHASE8_PHONE_HTTP_SMOKE_PASS_V1`
- Phone-probe heap checkpoints: 102,400 / 83,184 / 81,840 / 76,240 / 75,072
  bytes; every measured point above 32 KiB
- Ordered HTTP/Wi-Fi cleanup, lease release, approval restoration, temporary
  probe removal and passive hard-reset boot: PASS

The phone probe used the production NetworkManager/Wi-Fi path and
`MicroPythonHTTPServer`, but a fixed read-only radio-check handler. It did not
construct the full `RestApplication` + `ConfigManager` + configuration-storage
product composition and did not sample heap separately immediately after the
completed response. The full post-frozen P1 target acceptance therefore
remains open; the earlier 155.9-KiB dynamic-import estimate is historical and
does not alone prove a post-frozen capacity failure. Phase 9 remains
unreleased.

The sanitized phone evidence is recorded in
`../../captures/2026-08-11-phase8-frozen-phone-http-esp32-smoke.md`. No
credential or private backup location is included there or here.

## Later 2026-08-24/25 diagnostic update

A later app-only image incorporated the HTTP write-deadline correction and was
flashed at `0x10000` without erasing or replacing the bootloader or partition
table. Its 1,971,296-byte application readback matched SHA-256
`81f46473f41ed3fbd28e6686adaf36fa4c0ef0fa9995c5d7e20c057f86ffd080`.
That newer binary is not retained in this repository, so the files under
`artifacts/` remain explicitly the historical 2026-08-11 build. The current
source ledger includes the correction. See
`../../captures/2026-08-25-phase8-full-rest-progress.md` for the sanitized
status and remaining acceptance boundary.
