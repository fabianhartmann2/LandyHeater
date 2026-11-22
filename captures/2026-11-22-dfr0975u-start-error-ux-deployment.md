# DFR0975-U start-error UX deployment and station-control correction

Date: 2026-11-22
Board: DFRobot FireBeetle 2 ESP32-S3-U, DFR0975-U N16R8
Device MAC: `34:85:18:92:23:c0`
Station address during the deployment gate: `192.168.36.114`

## Authorized start-error UX deployment

The owner authorized exactly:

```text
Flash e73a28ed5e08db193446c6936f9808e39f194e99e7f7d9394d500d5975220fc6 ohne Erase freigegeben.
```

Before the write, a complete 2,181,024-byte application readback from
`0x10000` had SHA-256
`67393e2b052d88139e9a8533b8f8b2cc73cd4c8a0ef14fa41168473d7478313d`
and matched the preceding repository artifact byte-for-byte.

Esptool 4.12 wrote only the 2,183,648-byte application at `0x10000`. It erased
only application sectors through `0x225fff` and reported the data hash
verified. No full-chip erase and no bootloader, partition-table or VFS write
occurred. A separate complete post-write application readback was byte-equal
to the authorized file and had SHA-256
`e73a28ed5e08db193446c6936f9808e39f194e99e7f7d9394d500d5975220fc6`.

The normal boot emitted `LANDY_HEATER_PRODUCT_AUTOSTART_V1`. Station HTTP came
online at `192.168.36.114`; `heater.local` resolved to that address. Requested
state remained OFF, `control_faulted` was false, stored and runtime generation
both reported 9, and `restart_required` was false. The Scheduler was armed and
the preserved timer remained present. Heater communication and all three
sensors reported missing/error as expected while the heater hardware was
disconnected. The served Web bundle was
`f34244d58c7bee06a733582902b7388980294667728e5594134d9ec0f971bef6`,
and live `home.js` was byte-identical to source. No manual start mutation was
sent during this safety gate.

This installed revision replaces the generic start failure with localized
messages for required restart, unavailable/not-ready heater, unavailable
sensor and other state conflicts. Rejected starts remain Requested OFF and do
not emit controller start or UART output.

## Station-control defect found after deployment

The installed image deliberately exposes its persistent mutation security
context only on AP ingress. The station listener is therefore read-only. The
Home UI used that same context for Quick Start and failed before reaching the
heater route, showing `Mutation API unavailable`.

The intended boundary is narrower than enabling general station mutations:

- configured Start, Quick Start, Stop and current-session adjustments may use
  the recovery AP or the trusted station network;
- Settings, Setup, timers, browser-time sync and diagnostic capture mutations
  remain AP-only;
- Host validation, exact same-origin, a boot-ephemeral CSRF token,
  revision/ETag preconditions and route rate limits remain mandatory;
- Stop is available wherever Start is available.

This assumes a trusted home LAN. CSRF protects a browser from foreign Web
origins, but does not authenticate a client already present on that LAN.

The silent automatic browser-time attempt remains AP-only. Its expected
station-side `mutation_security_unavailable` response is ignored during Home
startup so it cannot produce a misleading technical toast. A manually
triggered browser-time sync continues to report its AP-only failure.

## Corrected retained and installed image

The correction was built twice at the same canonical path after moving the
first complete build aside. All 15 compared outputs were byte-identical:
bootloader, partition table, application, combined image, UF2, final and
combined configurations, four flash-argument files, flasher JSON, frozen C,
ELF and map.

```text
application offset:  0x10000
application size:    2185376 bytes
application SHA-256: a4d5a2f114c15db24ce215a88d5549e57a67e0ac8c332fb6da4e465613695af4
application end:     0x2258a0
application margin:  960352 bytes

combined size:       2250912 bytes
combined SHA-256:    37ff1676b79e3e4a1650716892aeab8195d58ac8b910062a52d07289b87844cf
Web bundle:          14e033cf0a1a19f2c3adf16c9e05d1138482ac4789e5ec4b5cf6cce9f9dd819f
```

The application grows by 1,728 bytes from the previously installed
`e73a28ed...` image and remains within the 3-MiB factory partition.

Repository binding hashes:

```text
CURRENT_FROZEN_SOURCES.sha256:
e1b7aa3787e3d9b04b655be5a7c7c54beac4c52e484f383721d4e67daf21ced0

artifacts/SHA256SUMS:
bd36c9da447468c7a70eff84819d643d3b21b46bc8dcad7255f1d22299353df8
```

The focused station-security, REST, composition, product-runtime and Web
matrix passed all 127 tests. The complete repository suite ran 1,347 tests:
1,345 passed, and only the two deliberately immutable historical Phase-11 and
early Phase-13 source-ledger checks reported their known mismatch. No new
regression was found.

## Authorized station-control deployment and live gate

The owner authorized exactly:

```text
Flash a4d5a2f114c15db24ce215a88d5549e57a67e0ac8c332fb6da4e465613695af4 ohne Erase freigegeben.
```

The target at `/dev/cu.usbmodem1101` was confirmed as the 16-MiB ESP32-S3,
MAC `34:85:18:92:23:c0`. Before writing, a complete 2,183,648-byte application
readback from `0x10000` had SHA-256
`e73a28ed5e08db193446c6936f9808e39f194e99e7f7d9394d500d5975220fc6`,
matching the expected installed baseline.

Esptool 4.12 wrote only the 2,185,376-byte application at `0x10000`, erased
only `0x10000` through `0x225fff` and reported the data hash verified. It did
not perform a full-chip erase and did not write the bootloader, partition table
or VFS. A separate complete application readback had SHA-256
`a4d5a2f114c15db24ce215a88d5549e57a67e0ac8c332fb6da4e465613695af4`
and was byte-for-byte identical to the retained artifact.

After a manual reset, the product returned online at `192.168.36.114`.
Requested state was OFF with request revision 0; no session or control
transition was active and `control_faulted` was false. Stored and runtime
generation both reported 11, `restart_required` was false, the Scheduler was
armed and its one preserved timer remained present. The station was online
with DHCP and mDNS ready. Heater communication and all three sensors reported
missing/error as expected with the heater hardware disconnected.

The read-only station gate confirmed:

- `/api/v1/control-context`: HTTP 200, `control_api_available=true` and a valid
  boot-ephemeral 64-hex CSRF token;
- `/api/v1/security-context`: HTTP 503 `mutation_security_unavailable`, so the
  persistent mutation context remains unavailable on station ingress;
- root/index and the versioned `app.js`: HTTP 200 with final Web bundle
  `14e033cf0a1a19f2c3adf16c9e05d1138482ac4789e5ec4b5cf6cce9f9dd819f`,
  the separate control client and the silent automatic-time guard.

No heater start or other mutation was sent. A real phone Quick Start remains
the final user-visible target check; it must still pass the normal heater
safety gate and requires separately prepared heater hardware for a powered
start.
