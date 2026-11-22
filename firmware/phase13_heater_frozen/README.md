# Phase 13 temperature-control frozen firmware

This directory defines the exact 51-file DFR0975-U N16R8 frozen closure plus
two bound native user-module sources for the integrated Landy Heater runtime.
It includes the accepted sensor, heater, REST/Web, discovery, diagnostics,
Scheduler and volatile browser-time paths, plus a private frozen `main.py`
that starts the product after every reset.

The current installed application extends the local OpenAPI 3.0.3 description
at `/openapi.json` with per-operation cURL examples
and an explicitly gated browser tester in Settings. It documents
all 24 semantic REST operations plus the three deprecated timer-segment
aliases, including AP-only versus AP-and-station
access, CSRF contexts, generation ETags, request schemas, query bounds, rate
limits and the STOP safety exception. The explorer and tester are loaded only
after explicit clicks and have no external dependency. Merely opening an
example never calls the API. Browser tests remain strictly same-origin;
security tokens, the current configuration ETag and heater request revision
are obtained only immediately before a confirmed execution. Every mutation
requires an acknowledgement checkbox, and Start or Quick Start additionally
requires the exact confirmation word `START`. Mutations are never retried
automatically, and cURL examples contain placeholders rather than live tokens.

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

The currently installed Web UI emits deterministic identity and precompressed gzip
representations. The revalidated index references only full-bundle-hash asset
URLs; the current version is immutable for one year, while an old version
prefix returns `404` rather than new bytes under an old cache key. Identity and
gzip have separate strong ETags and `Vary: Accept-Encoding`. CSS and
translations load together before the application core, then Home and status
load together. Timer, Settings, Setup and Diagnostics remain on demand. Across
all 15 installed assets, gzip reduces 174,416 bytes to 49,613 bytes. The
cooperative HTTP receive budget remains 256 bytes while the
reviewed send budget is 1,024 bytes, below the 1,440-byte TCP MSS. Client and
backlog limits are unchanged.

`BUILD_INFO.md` records inputs, both source ledgers, two byte-identical builds,
artifact checks and target evidence. The installed application is 2,271,296
bytes with SHA-256
`0711bbe572c9f1e00fee1af5c055961eac134e2649f8b03a1ef83e0670a5cd07`.
Its 15-asset Web bundle version is
`66178bd9a37ee7dd69af1e7b4a76d4da1a833254016bb2703a0f954bf29558bb`;
174,416 identity bytes compress deterministically to 49,613 gzip bytes. Two
clean canonical-path builds produced byte-identical results. The application
was exactly authorized, written app-only at `0x10000` without a full-chip erase
and confirmed byte-for-byte by a complete postflash readback. Esptool erased
only the application sectors from `0x10000` through `0x23afff`; bootloader,
partition table and VFS were not written. A watchdog reset returned the
controller to the application, after which `http://192.168.36.114/` returned
HTTP 200.

Immediately before this API-tester application was written, a complete
2,245,552-byte readback confirmed the installed `824b63bc...` baseline and was
byte-identical to its retained application artifact. The separate complete
2,271,296-byte postflash readback had SHA-256 `0711bbe5...` and was
byte-identical to the newly authorized application. No heater command or other
API mutation was used for this deployment gate.

Immediately before the preceding OpenAPI image was written, a complete
2,185,376-byte readback confirmed the `a4d5a2f1...` baseline. That deployment
then wrote only the 2,245,552-byte application at
`0x10000`. Esptool 4.12 erased only `0x10000` through `0x234fff`; bootloader,
partition table and VFS were not written. A separate complete readback was
byte-identical to the authorized `824b63bc...` application. No heater command
or other API mutation was used for that deployment gate.

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
