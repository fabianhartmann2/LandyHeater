# Phase 13 temperature-control frozen-module candidate

This directory defines the exact 51-file DFR0975-U N16R8 frozen closure plus
two bound native user-module sources for the integrated Landy Heater runtime.
It includes the accepted sensor, heater, REST/Web, discovery, diagnostics,
Scheduler and volatile browser-time paths, plus a private frozen `main.py`
that starts the product after every reset.

Startup is ordered and fail-closed: trusted production storage must load;
sensors and the heater protocol start requested OFF; the recovery AP, REST
security, captive portal and Web UI become live; only then is the Scheduler
armed. After station DHCP and mDNS readiness, a second port-80 listener is
attached to the station address while the AP listener stays live. Optional
WLAN, Web-listener or diagnostics failures do not end heater supervision.

Settings now expose independent `captive_portal_enabled` and
`bonjour_http_enabled` switches. Both default to enabled, are writable only
over AP ingress and require a restart. Disabling captive assistance creates no
UDP/53 owner and performs no probe redirect. The enabled path also accepts
common query-string probes and one structurally valid EDNS0 OPT record.
Bonjour advertises only `Landy Heater._http._tcp`, port 80, TXT `path=/`, and
only while station mDNS is ready. This native service is degradable and runs
after the heater's cooperative step. A trusted v2 configuration receives both
enabled defaults in memory without a migration fence or an automatic write.

The private `board_config.py` opens only the previously accepted UART pins and
protocol TX for the owner-approved direct level shifter. I2C and the absent
GPIO12 TX gate remain closed. The repository-root passive `main.py` is not in
this closure.

The Setup Assistant now reads the already running DS18B20 runtime directly,
refreshes ROM ID, temperature, health and reading age every 1.5 seconds, and
requires three distinct currently healthy sensors before accepting a reviewed
assignment. The user may instead explicitly skip sensor setup. A skipped step
preserves all existing assignments; it cannot silently clear or modify them.

The selected roof-tent or cabin DS18B20 value is reported to the heater after
fresh STATUS truth while STARTING, RUNNING and TEMP_MONITORING. Negative values
remain valid regulation input and use signed eight-bit two's-complement wire
encoding. A Stop from TEMP_MONITORING uses the same bounded maximum-two-attempt
shutdown policy as RUNNING. Both selected-sensor paths and controlled shutdown
have now been confirmed in separate real-heater runs of at least seven minutes.

This revision also fixes the target-observed Start-dialog defect where a cancel
button implicitly submitted the form and started the heater. Start and Timer
close/cancel buttons are explicit non-submit controls, with a second cancel
guard in both submit handlers.

The installed image replaces the generic “Application Service is unavailable”
response for rejected manual starts. A pending runtime activation is reported
as a required controller restart. If the heater has no power or connection,
Home displays “not reachable” and both Quick Start and configured Start show a
localized power/connection hint. Boot synchronization, sensor readiness and
other state conflicts remain distinct. All rejected starts remain Requested
OFF and produce no controller start or UART output.

The installed application splits persistent configuration mutations
from operational heater control. Quick Start, configured Start, Stop and
current-session adjustments are allowed from both the recovery AP and the
trusted station network. Settings, Setup, timers, browser-time sync and
diagnostic capture mutations remain AP-only. Host validation, exact
same-origin, boot-ephemeral CSRF, revision/ETag preconditions and rate limits
remain in force on the new control path. This corrects the preceding image's
station-side `Mutation API unavailable` result. Because CSRF is not device
authentication, station control assumes that the home LAN itself is trusted.
The silent browser-time attempt remains AP-only; its expected station rejection
is suppressed during Home startup, while a manual browser-time request remains
rejected on station ingress.

The Web UI now emits deterministic identity and precompressed gzip
representations. The revalidated index references only full-bundle-hash asset
URLs; the current version is immutable for one year, while an old version
prefix returns `404` rather than new bytes under an old cache key. Identity and
gzip have separate strong ETags and `Vary: Accept-Encoding`. CSS and
translations load together before the application core, then Home and status
load together. Timer, Settings, Setup and Diagnostics remain on demand. Across
all 11 assets, gzip reduces 106,593 bytes to 32,842 bytes; the gzip index is
3,758 bytes. The cooperative HTTP receive budget remains 256 bytes while the
reviewed send budget is 1,024 bytes, below the 1,440-byte TCP MSS. Client and
backlog limits are unchanged.

`BUILD_INFO.md` records inputs, both source ledgers, two byte-identical builds,
artifact checks and target evidence. The installed application is 2,185,376
bytes with SHA-256
`a4d5a2f114c15db24ce215a88d5549e57a67e0ac8c332fb6da4e465613695af4`.
It was exactly authorized, written app-only without a full-chip erase and
confirmed byte-for-byte by a complete readback. Its Web bundle version is
`14e033cf0a1a19f2c3adf16c9e05d1138482ac4789e5ec4b5cf6cce9f9dd819f`.

Immediately before that write, the complete 2,183,648-byte application
readback confirmed the previously installed SHA-256
`e73a28ed5e08db193446c6936f9808e39f194e99e7f7d9394d500d5975220fc6`.
Esptool 4.12 erased only `0x10000` through `0x225fff`; bootloader, partition
table and VFS were not written. After a manual reset the station was online at
`192.168.36.114`, Requested state remained OFF and the control path remained
unfaulted. Stored and runtime generation both reported 11,
`restart_required` was false, the Scheduler was armed and its one preserved
timer remained present. The station control context returned HTTP 200 with a
valid boot-ephemeral CSRF token, while the persistent mutation context remained
station-blocked with HTTP 503 `mutation_security_unavailable`. The live root
and `app.js` served the final Web bundle and its separate control path. Heater
communication and all three sensors were missing as expected while the heater
hardware was disconnected. No heater start or other mutation was used during
this deployment gate.

Three staged cold Home paths measured 4.157, 4.228 and 4.519 seconds
(median 4.228 seconds); three normal cached reopens measured 0.848, 0.866 and
1.319 seconds (median 0.866 seconds) at weak station RSSI. A fresh headless
Chrome profile rendered the connected Home view. The gzip index, immutable
versioned assets, empty `304` revalidation and stale-version `404` all passed
on target. Requested state remained OFF, control and REST stayed unfaulted,
and 7,946,400 bytes of GC heap remained free after the load and browser gates.
The missing-sensor warnings are expected with 12-V/sensor hardware absent.
Longer internal/DMA-heap and heater-fairness soaking remains open.

The preceding 2,135,888-byte first-stage image with SHA-256
`02197a7d74e6006573bd769ed1a4d34b823a470f7ce4435c02159caa204fe84b`
was retained before replacement. It measured about 10.4 seconds for a strictly
sequential cold path and 3.6 seconds for a revalidated repeat at weak station
RSSI. The private device credential is not stored in Git. No file in this
directory authorizes another board flash.

The device-proven bootstrap corrects the earlier target-observed UI request
burst without raising the bounded server's client or backlog limits. It
retains bounded retry and permits only two-way overlap, exactly matching that
limit. The installed gzip/versioned build reached connected state in Chrome,
kept Requested OFF and passed its static-cache and measured latency gates.

For offline reproduction, verify both ledgers and pass the native module to
the same pinned canonical-path build:

```sh
shasum -a 256 -c firmware/phase13_heater_frozen/CURRENT_FROZEN_SOURCES.sha256
shasum -a 256 -c firmware/phase13_heater_frozen/NATIVE_SOURCES.sha256
make -C /private/tmp/landy-phase13-heater-canonical/micropython/ports/esp32 \
  -j4 BOARD=DFR0975U_N16R8 \
  FROZEN_MANIFEST=/private/tmp/landy-phase13-heater-canonical/project/firmware/phase13_heater_frozen/manifest.py \
  USER_C_MODULES=/private/tmp/landy-phase13-heater-canonical/project/native/landy_mdns/micropython.cmake
```

This command builds only. It does not authorize or perform a board flash.
