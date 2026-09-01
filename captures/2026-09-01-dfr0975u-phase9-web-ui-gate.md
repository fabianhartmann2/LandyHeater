# DFR0975-U Phase-9 Web-UI gate — 2026-09-01

## Status

**PASS for the Phase-9 frozen application flash and single-listener Web-UI
target acceptance on the DFR0975-U N16R8.**

The frozen browser application and its read-only boot API set were delivered
over one real phone connection and the production port-80 listener. All
hardware locks stayed closed and the run completed its ordered cleanup.

## Authorized app-only flash

The board remained USB-only. Heater, vehicle power, UART, RTC/I2C, 1-Wire and
sensors were disconnected. The owner explicitly authorized an app-only write
without full erase at `0x10000`, bound to SHA-256:

`a228d115cc2aba8569ddad3a46b9c038ab5f06e159bae3d4ded955345b6485e6`

Immediately before writing, the retained `micropython.bin` was 2,020,592
bytes and matched that digest. esptool 4.12 again identified the connected
target as ESP32-S3 revision 0.1 with 16-MiB flash and 8-MiB embedded PSRAM.
It wrote only `0x10000–0x1FD4EF`; there was no full-chip erase and no write to
the bootloader, partition table or VFS.

The write-time hash verification passed. A subsequent independent read of all
2,020,592 application bytes returned the exact authorized SHA-256. The
passive restart then reported MicroPython 1.28.0 and machine
`DFRobot DFR0975-U N16R8 with ESP32S3`; station and AP interfaces were both
inactive.

## Bounded phone gate

The target runner reused the already accepted Phase-8 AP-first configuration,
isolated A/B storage, full REST composition, socket ownership and cleanup
seams. Its Phase-9 proof layer allowed GET only. The phone loaded exactly the
root UI, all eight referenced CSS/JavaScript resources and the four automatic
application reads:

- `/api/v1/security-context`;
- `/api/v1/status`;
- `/api/v1/settings`;
- `/api/v1/timers?offset=0&limit=8`.

The completed target result was:

```text
ui_assets_completed=9
api_reads_completed=4
mutation_requests=0
http_rest_radio_cleanup_confirmed=True
PHASE9_WEB_PHONE_SMOKE_PASS_V1
PHASE9_HOST_RESULT 9 80
```

All inherited and Phase-9 heap checkpoints were enforced at or above 32 KiB.
The exact values were not emitted, so this record claims the enforced gates,
not unrecorded numbers. Requested State remained OFF, its revision stayed
zero, the protocol tripwire remained at zero calls, isolated storage had no
additional writes and production storage was unchanged.

The first preflight stopped before radio activation because the transient USB
mount preceded `.frozen`; the frozen-first guard worked as designed. After
correcting only that order, one phone attempt did not complete and cleaned up
safely, but its initial diagnostic surface was insufficient to distinguish a
browser abort from transport validation. Bounded stage and counter diagnostics
were therefore added and host-tested before the one justified repeat. No
functional UI, API or radio code changed between those phone attempts. The
repeat above passed.

## Independent cleanup check

After PASS, without another HTTP request, USB reported:

```text
PHASE9_POSTCHECK_STA False
PHASE9_POSTCHECK_AP False
PHASE9_POSTCHECK_HEAP 8319520
PHASE9_POSTCHECK_FILES ()
```

The WLAN disappearing from the phone immediately after the page completed was
the expected cleanup. No smoke A/B file remained. `boot.py` and `main.py`
remain passive; this acceptance does not authorize automatic startup, heater
traffic or any electrical peripheral.

## Host evidence

The Phase-9 target layer has five focused host cases. The complete suite after
the diagnostic addition passed 1,070/1,070 tests.
