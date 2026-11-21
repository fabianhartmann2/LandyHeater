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

The Web UI now emits deterministic identity and precompressed gzip
representations. The revalidated index references only full-bundle-hash asset
URLs; the current version is immutable for one year, while an old version
prefix returns `404` rather than new bytes under an old cache key. Identity and
gzip have separate strong ETags and `Vary: Accept-Encoding`. CSS and
translations load together before the application core, then Home and status
load together. Timer, Settings, Setup and Diagnostics remain on demand. Across
all 11 assets, gzip reduces 104,093 bytes to 32,099 bytes; the gzip index is
3,761 bytes. The cooperative HTTP receive budget remains 256 bytes while the
reviewed send budget is 1,024 bytes, below the 1,440-byte TCP MSS. Client and
backlog limits are unchanged.

`BUILD_INFO.md` records inputs, both source ledgers, two byte-identical builds,
artifact checks and target evidence. The installed 2,181,024-byte application
has SHA-256
`67393e2b052d88139e9a8533b8f8b2cc73cd4c8a0ef14fa41168473d7478313d`.
That exact hash was authorized, written app-only at `0x10000` without a
full-chip erase and confirmed byte-for-byte by a complete independent
application readback. Immediately before the write, the previously installed
2,171,728-byte application with SHA-256
`a25e32266e93d921db3b8cc5dd311ffb955b976ccc55af7221859f0344c29d76`
was also confirmed by a complete readback. After a normal boot the station was
online at `192.168.36.114`, Requested state remained OFF and the control path
remained unfaulted. Settings reported schema 3 with both network-service
switches enabled while stored and runtime generation remained 7. Bonjour
browse and resolve found `Landy Heater._http._tcp`, `heater.local:80`, TXT
`path=/` and address `192.168.36.114`. The Web UI served bundle version
`728dc6163ddf88c828ead9be62c56f762d69cbf64685e431da52c416abfddbc1`.
Only the visible automatic captive-portal presentation on a freshly joined
phone remains to be observed for this revision.

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
