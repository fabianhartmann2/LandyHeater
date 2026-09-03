# DFR0975-U Phase-10 integration and timer-delete correction — 2026-09-03

## Status

**PASS for real station DHCP, AP-password replacement/re-authentication and
durable timer create/edit/delete. The browser request-shape correction passed
1,098 host tests, two clean byte-identical firmware builds, every offline
artifact gate, an authorized app-only flash with complete readback, and the
resumed real-phone target gate.**

## Live integration evidence

The two-stage phone gate used disposable configuration and scheduler A/B paths
and did not touch the production stores. All heater and peripheral boundaries
remained forbidden. No real station SSID or password was printed, exported or
retained in this report.

The setup stage accepted exactly one protected station profile and replaced
the device AP password. After a physical reset, the DFR0975-U joined that
station network and obtained the expected DHCP configuration. The phone then
re-authenticated to the device AP with the replacement password.

The UI created an inactive Monday timer for 07:15, power level 3 and 15
minutes. It then durably changed the timer to 07:30, power level 4 and the name
`Integrationstest bearbeitet`. Repeated physical resets and reloads preserved
the edited timer. No timer execution was enabled.

## Delete failure and root cause

The delete action returned HTTP 422 with error code
`request_body_not_allowed`; the timer and generation remained unchanged. The
REST boundary behaved as designed: timer deletion is a bodyless `DELETE` and
rejects any supplied body.

The common browser mutation helper initialized an empty string and always
passed a `body` property to `fetch`, even when its caller supplied no payload.
The correction now creates the fetch options without a body and adds JSON
headers and `options.body` only when a payload is actually present. This also
corrects every other intentionally bodyless browser mutation.

A regression test verifies that the served frozen JavaScript contains the
conditional body assignment and cannot return to either form of the old
unconditional empty-body request.

## Corrected preflash candidate

Pinned MicroPython 1.28.0 and ESP-IDF 5.5.1 inputs were restored at the
canonical source paths. The regenerated 42-file source ledger verified in
full. Two clean builds from absent build directories produced 15/15
byte-identical outputs.

Offline checks passed for ESP32-S3 image headers, checksums and validation
hashes; the N16R8 fail-closed Octal-PSRAM settings; the 16-MiB partition table;
the component-only combined image; and the complete retained artifact ledger.
The complete host regression passed 1,098/1,098 tests.

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| application | 2,050,848 B | `9912c86513cc08e5b36f18c0705bf41bb3b6d592342b475170d3fce2b3780b63` |
| combined image | 2,116,384 B | `e9cd0ea68a16740134ba5680da99fdeeff7b2f1bccb4bb0269bf73655b850f7e` |

Bootloader and partition-table bytes were unchanged from the already verified
target. The narrow authorized continuation was therefore an application-only
write at `0x10000` without a full erase. Before that authorization, the
isolated configuration and inactive edited timer remained retained so the
phone exercise could resume directly at deletion. The artifact record itself
did not imply flash authorization.

## Authorized flash and readback

The owner subsequently authorized USB-only operation and the exact
2,050,848-byte application hash above at `0x10000`, explicitly without a
full-chip erase. Automatic ROM entry received no serial data and performed no
write. After manual `BOOT`/`RST` entry, esptool identified the expected
ESP32-S3 revision 0.1 and embedded 8-MiB PSRAM.

Only application sectors `0x10000–0x204fff` were erased and written. The
write-time hash verification passed. A separate full read of all 2,050,848
application bytes then matched the authorized artifact byte-for-byte and
returned SHA-256
`9912c86513cc08e5b36f18c0705bf41bb3b6d592342b475170d3fce2b3780b63`.
Bootloader, partition table and VFS were not written.

After a physical reset, passive USB inspection reported MicroPython 1.28.0,
machine `DFRobot DFR0975-U N16R8 with ESP32S3`, and both WLAN interfaces
inactive.

## Resumed target result

The retained isolated state resumed at timer stage 2. The board rejoined the
stored station network with DHCP truth, exposed the reconfigured AP and
accepted exactly one phone client authenticated with the replacement AP key.
The UI was opened afresh and issued the timer deletion once.

The target emitted `PHASE10_INTEGRATION_TIMER_DELETED_V1` only after the real
API returned success and a fresh manager reload proved that the timer no
longer existed. The exact remaining generation increment passed. Requested
State remained OFF, the heater-protocol tripwire stayed at zero and production
storage signatures were unchanged.

The runner then removed all six isolated A/B/temp paths and shut down HTTP,
REST and both radios. Independent passive USB postcheck:

```text
PHASE10_DELETEFIX_POSTCHECK False False () 8319056
```

The empty tuple proves no owned integration file remained. This closes the
timer create/edit/delete portion of the Phase-10 real-phone integration gate.
