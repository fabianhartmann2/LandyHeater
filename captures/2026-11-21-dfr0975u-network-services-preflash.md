# DFR0975-U network-service switches – preflash record

Date: 2026-11-21
Board: DFRobot FireBeetle 2 ESP32-S3-U / DFR0975-U N16R8
State: preflash record completed; candidate was subsequently flashed and
verified exactly

## Live baseline

Before changing the retained candidate, the installed product remained
reachable at `http://192.168.36.114/`. Its status reported Requested OFF,
station network online, mDNS ready, no control fault, and configuration,
storage and runtime generation 7. `GET /api/v1/settings` still reported schema
version 2 and contained no `network_services` group. This is the exact released
migration source covered by the new host tests. No device write occurred.

## Implemented behavior

The persistent schema-v3 `network_services` group contains two strict Boolean
settings, both defaulting to `true`:

- `captive_portal_enabled`
- `bonjour_http_enabled`

Settings mutations remain AP-only and return restart-required semantics. A
direct trusted v2-to-v3 load adds both enabled defaults in memory without a
migration fence, generation change or flash commit. Older or multi-step
migrations retain the existing fail-closed fence.

With captive assistance disabled, product composition creates no UDP/53 owner
and performs no captive-probe redirect; direct AP UI access remains available.
The enabled path accepts known probe routes with query strings and either no
additional DNS record or one structurally validated EDNS0 OPT record.

Bonjour uses a fixed native MicroPython user module. It can only publish or
remove `Landy Heater._http._tcp`, port 80, TXT `path=/`. Publication follows
station mDNS readiness and is removed on disconnect. It is a degradable
comfort service: failure does not stop the direct HTTP listeners or heater
supervision, and the heater receives its cooperative step before a native
service update. Automatic captive-portal presentation remains a phone-OS
decision; a remembered SSID may still require forgetting and rejoining the
network for a clean test.

## Host and build evidence

- feature-focused matrix: 214 tests passed;
- complete repository suite: 1,341 tests run, 1,339 passed; only the two
  deliberately immutable historical source ledgers failed as expected;
- all 51 frozen Python modules compiled for `xtensawin`;
- exact 51-file frozen ledger plus exact two-file native ledger passed;
- JavaScript syntax and diff checks passed;
- two clean canonical-path builds matched byte-for-byte for all 15 compared
  outputs;
- bootloader and partition table are unchanged;
- application: 2,181,024 bytes, 964,704-byte partition margin;
- application SHA-256:
  `67393e2b052d88139e9a8533b8f8b2cc73cd4c8a0ef14fa41168473d7478313d`;
- combined image: 2,246,560 bytes, SHA-256
  `89f9af87e6cc19f6ee59b12ef8c0370a0d1f2d3f83eeb111418d65da4aec6518`;
- Web bundle version:
  `728dc6163ddf88c828ead9be62c56f762d69cbf64685e431da52c416abfddbc1`;
- Web representations total 104,093 raw bytes and 32,099 gzip bytes; the gzip
  index is 3,761 bytes.

At the time of this preflash record, the prior one-time authorization for
application
`a25e32266e93d921db3b8cc5dd311ffb955b976ccc55af7221859f0344c29d76`
did not authorize the new application. No erase, flash write or target
runtime mutation had been performed. The subsequent, separately authorized
deployment is recorded below.

## Deployment and target evidence

The exact authorization was:

```text
Flash 67393e2b052d88139e9a8533b8f8b2cc73cd4c8a0ef14fa41168473d7478313d ohne Erase freigegeben.
```

Before writing, a complete 2,171,728-byte application readback from `0x10000`
matched the installed baseline SHA-256
`a25e32266e93d921db3b8cc5dd311ffb955b976ccc55af7221859f0344c29d76`.
Esptool 4.12 then wrote only the 2,181,024-byte application at `0x10000` and
reported the data hash verified. No full-chip erase and no bootloader,
partition-table or VFS write was performed. A separate complete postflash
application readback had SHA-256
`67393e2b052d88139e9a8533b8f8b2cc73cd4c8a0ef14fa41168473d7478313d`
and compared byte-for-byte equal to the retained candidate.

After a normal boot, the product was reachable at
`http://192.168.36.114/`. Requested state remained OFF,
`control_faulted` was false and the station reported online. Stored and runtime
generation both remained 7. `GET /api/v1/settings` reported schema version 3
and both `captive_portal_enabled` and `bonjour_http_enabled` as true, proving
the direct migration defaults without a generation-changing configuration
commit. The Web UI served bundle version
`728dc6163ddf88c828ead9be62c56f762d69cbf64685e431da52c416abfddbc1`.

Bonjour browsing found `Landy Heater._http._tcp`. Service resolution returned
`heater.local:80` with TXT `path=/`, and `heater.local` resolved to
`192.168.36.114`. The only remaining target observation for this feature is
the visible automatic captive-portal presentation after a phone forgets and
freshly rejoins the recovery AP.
