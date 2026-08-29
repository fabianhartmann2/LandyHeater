# Phase 8 frozen-module firmware

This directory defines a custom `ESP32_GENERIC` MicroPython firmware that
freezes the current Phase-8 product modules into flash. Its purpose is to
measure and recover Python heap that is otherwise consumed by dynamically
loaded `.mpy` files.

The firmware is a capacity-test candidate, not a production release. On
2026-08-11 it was flashed only after an exact private backup and explicit user
approval, then used for the bounded target checks described below. It does not
freeze or replace `boot.py`, `main.py`, or `board_config.py`, and it does not
contain credentials or persisted configuration. The existing passive boot
contract therefore remains external to the firmware.

## Pinned build inputs

- MicroPython `v1.28.0`, commit
  `e0e9fbb17ed6fd06bb76e266ae554784c9c80804`
- ESP-IDF `v5.5.1`
- board `ESP32_GENERIC`, 4 MiB flash layout
- manifest `manifest.py`, optimisation level `0`

The manifest explicitly freezes 40 files from `adapters`, `app`, `hardware`,
`protocol`, and `services`. Diagnostic-only capture modules, all tools and
tests, secrets, configuration slots, and scheduler-ledger slots are excluded.

## Offline build

After exporting a compatible ESP-IDF 5.5.1 environment:

```sh
make -C /path/to/micropython/ports/esp32 \
  BOARD=ESP32_GENERIC \
  FROZEN_MANIFEST=/absolute/path/to/landy-heater/firmware/phase8_frozen/manifest.py \
  submodules

make -C /path/to/micropython/ports/esp32 \
  BOARD=ESP32_GENERIC \
  FROZEN_MANIFEST=/absolute/path/to/landy-heater/firmware/phase8_frozen/manifest.py
```

The verified v1.28.0 build is written to the port's standard
`build-ESP32_GENERIC` directory. Do not pass a shared `BUILD` command-line
variable to this make invocation: GNU Make also forwards it to the nested
`mpy-cross` build, where it can select the wrong generated-header directory.

These commands use the current top-level source closure. Reproducing the
historical artifacts requires an isolated temporary project copy with
`build_sources_2026-08-11/` overlaid onto its corresponding top-level package
paths, as described in that snapshot's README. The live project tree must not
be overwritten for this purpose.

Neither command accesses the board. Do not run `deploy`, `erase`, `flash`, or
`write-flash` as part of the build.

## Mandatory gates for any flash

1. Preserve a confidential 4 MiB flash backup and a verified copy of the
   official MicroPython v1.28.0 rollback image.
2. Verify the custom image, build-input hashes, partition layout, and passive
   `boot.py`/`main.py` contract.
3. For a frozen-code probe, put `.frozen` ahead of filesystem package copies
   temporarily and verify the imported origins; do not activate the normal
   boot path as a side effect.
4. Flash only after explicit user approval.
5. First boot remains USB-only with heater, UART, I2C, 1-Wire, RTC, and sensors
   disconnected; verify both radios inactive before any capacity test.

These gates were completed for the explicitly approved 2026-08-11 test flash
without a full erase. They remain mandatory again for any future flash. The
standard `ESP32_GENERIC` layout has one factory application partition and no
OTA slot, so a failed custom image requires a manual USB rollback.

## Verified candidate artifacts

The historical 2026-08-11 build is preserved in `artifacts/`. Its exact source
closure is preserved separately in `build_sources_2026-08-11/` and verifies
40/40 against `FROZEN_SOURCES.sha256`. The current top-level closure verifies
against `CURRENT_FROZEN_SOURCES.sha256` and includes a later HTTP
write-deadline correction, so it must not be paired with the historical
binaries. See `BUILD_INFO.md`, `FROZEN_MODULES.txt`, and
`artifacts/SHA256SUMS` for the exact inputs, module delta, partition margin and
tests. The regenerable linker map and the downloadable official rollback
binary are intentionally not stored in the repository. The artifacts'
presence is not approval for another flash or for production use.

## 2026-08-11 postflash acceptance

The complete 1,971,248-byte application partition readback matched the
candidate application exactly. Passive first boot reported MicroPython 1.28.0
and kept both radios and all hardware approvals inactive. With `.frozen`
temporarily placed first for the probes, all 40 declared project modules
loaded from frozen storage and left 110,320 bytes free after the full import.

The isolated software-only REST closure then passed 4/4 iterations and 12/12
completed fake-socket responses. Finally, the AP-first phone runner used the
production NetworkManager/Wi-Fi port and `MicroPythonHTTPServer`, observed one
real phone peer at `192.168.4.2`, accepted one allowlisted request, completely
wrote its response and emitted:

```text
PHASE8_PHONE_HTTP_SMOKE_PASS_V1
```

Its measured heap checkpoints were 102,400, 83,184, 81,840, 76,240 and 75,072
bytes. HTTP cleanup, radio cleanup, lease release and approval restoration all
passed. The temporary probe files were removed, and a subsequent hard reset
again produced the passive safe-boot banner with both radios and every lock
inactive.

This is deliberately a narrow acceptance. The phone runner used a fixed
read-only radio-check handler, not `RestApplication`, `ConfigManager` or
configuration storage, and it did not take a separate post-response heap
sample before cleanup. The full post-frozen product composition has therefore
not yet passed its P1 target acceptance, and Phase 9 remains unreleased. See
`../../captures/2026-08-11-phase8-frozen-phone-http-esp32-smoke.md` for the
sanitized evidence and exact non-claims.

The later 2026-08-24/25 full-product work is recorded separately in
`../../captures/2026-08-25-phase8-full-rest-progress.md`. It uses the corrected
HTTP adapter and a later app-only image; that newer binary is not retained
here. The full target acceptance remains open.
