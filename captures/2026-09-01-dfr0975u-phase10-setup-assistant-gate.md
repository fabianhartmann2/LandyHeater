# DFR0975-U Phase-10 Setup Assistant gate — 2026-09-01

## Status

**PASS for the Phase-10 application flash, full readback and bounded real-phone
Setup Assistant target acceptance on the DFR0975-U N16R8.**

The gate used the production frozen Setup Assistant, REST application,
configuration gateway, A/B storage and the established sole port-80 listener.
Product storage, heater state and all electrical peripheral locks remained
untouched.

## Authorized app-only flash

The board remained USB-only. Heater, vehicle power, UART, RTC/I2C, 1-Wire and
sensors were disconnected. The owner explicitly authorized an app-only write
at `0x10000` without a full-chip erase, bound to SHA-256:

`8c8d0bca7b6d3311c20f1e5878619a898147dcdf645305dc12fcbb575278fc5d`

Immediately before writing, the retained `micropython.bin` was 2,044,496
bytes and matched that digest. The automatic ROM connection returned no
serial data and performed no write. After the documented manual `BOOT`/`RST`
entry, esptool 4.12 identified the ESP32-S3 revision 0.1 with 8-MiB embedded
PSRAM. It erased only the application sectors covering
`0x10000–0x203fff`, wrote the authorized 2,044,496 application bytes and
passed its write-time hash verification. There was no full-chip erase and no
write to bootloader, partition table or VFS.

A subsequent independent read of exactly 2,044,496 bytes from `0x10000`
returned the exact authorized SHA-256. The known unreliable automatic reset
did not restore the REPL; one physical `RST` press did. Passive USB checks
then reported MicroPython 1.28.0, machine
`DFRobot DFR0975-U N16R8 with ESP32S3`, both WLAN interfaces inactive and all
11 frozen web resources importable.

## Bounded Setup Assistant phone gate

The target runner reused the accepted AP-first, isolated-storage,
full-REST/socket and ordered-cleanup seams. It permitted the complete browser
read surface and exactly one `PUT /api/v1/setup`; every second or unrelated
mutation was fail-closed. The test kept the established AP credential without
displaying or logging it, left known station networks empty and marked the
unconnected sensor and Autoterm checks as deferred.

Two preflights stopped before radio activation because the local USB mount was
ahead of `.frozen` in `sys.path`. The frozen-origin guard worked as intended.
A passive USB-only preflight confirmed the corrected order `.frozen` then
`/remote`, all hardware locks and both radio interfaces inactive before the
single functional run. No firmware or product code changed between these
preflights and the accepted run.

The user connected one phone, opened `http://192.168.4.1/` once, traversed the
nine-step assistant and submitted once. The completed target result was:

```text
ui_assets_completed=11
api_reads_completed=5
setup_mutations_completed=1
isolated_commits=1
http_rest_radio_cleanup_confirmed=True
PHASE10_SETUP_PHONE_SMOKE_PASS_V1
```

The five required API reads were security context, status, settings, paged
timers and the passive setup projection. The setup projection performed no
active sensor or UART test. The successful mutation used the production CSRF,
Origin and generation/ETag boundaries, persisted `setup_complete=true` in
exactly one disposable A/B configuration commit and preserved the existing
write-only AP credential. No response contained a password field or value.

Every inherited GC-heap checkpoint remained at or above 32 KiB. Requested
State and its revision remained OFF/zero, the protocol tripwire remained at
zero calls, production storage signatures were unchanged, and disposable
storage was removed during ordered HTTP/REST/radio cleanup.

## Independent cleanup check

After PASS, without another HTTP request, USB reported:

```text
PHASE10_POSTCHECK_RADIO False False
PHASE10_POSTCHECK_HEAP 8320160
PHASE10_POSTCHECK_FILES ()
```

`boot.py` and `main.py` remain passive. Phase 10 does not authorize automatic
startup, UART/heater traffic, RTC/I2C, 1-Wire or real peripheral acceptance;
those electrical gates remain assigned to Phase 13.
