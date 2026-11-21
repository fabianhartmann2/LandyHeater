# Phase 13 temperature-control frozen firmware build record

Build date: 2026-11-21. Status: **exact 51-file frozen source closure plus two
native user-module sources, focused lifecycle/configuration/discovery tests,
two byte-identical canonical-path builds and offline image gates passed. The
`67393e2b...` application was exactly authorized, written app-only at
`0x10000` without a full-chip erase and confirmed byte-for-byte by an
independent complete application readback. Normal boot, live schema migration,
station HTTP and Bonjour target gates passed; only fresh-phone automatic
captive-portal presentation remains to be observed.**

This candidate retains periodic
external-temperature reports for roof-tent and cabin control, signed negative
DS18B20 support and bounded shutdown from `TEMP_MONITORING`, and prevents Start
and Timer cancel controls from submitting their forms. At every normal reset it
loads the trusted production stores, starts sensors and the heater protocol in
requested-OFF state, brings up the recovery AP and Web UI, and only then arms
the Scheduler. A station listener is attached dynamically after DHCP/mDNS,
without stopping the AP listener or captive DNS. Runtime WLAN, HTTP-listener
and diagnostics failures degrade those optional services without ending heater
supervision. Safety-critical failures enter the existing controlled heater
shutdown path. This revision adds persistent, independent Settings switches
for captive-portal assistance and Bonjour HTTP publication. Both default to
enabled, can be changed only over AP ingress and take effect after restart.
The direct v2-to-v3 configuration migration adds those defaults in memory
without closing the existing network or timer gates and without writing flash.

When captive assistance is disabled, no UDP/53 owner is created and captive
probe paths are not redirected. When enabled, query-string probe variants and
one structurally valid EDNS0 OPT record are accepted. Bonjour publishes only
the fixed `Landy Heater._http._tcp` service on port 80 with TXT `path=/`, and
only while station mDNS is ready. Its native owner is degradable: a service
failure cannot stop the direct HTTP listeners or heater supervision, and the
heater receives its cooperative step before a native advertisement update.

The candidate retains the deterministic gzip/cache Web path. The cooperative
send budget is 1,024 bytes; the 256-byte receive budget,
two-client/two-backlog limits and one-action-per-step contract remain
unchanged.

The private frozen `board_config.py` differs from the safe repository profile
in exactly two values: `UART_PINS_APPROVED=True` and
`UART_PROTOCOL_TX_ENABLED=True`. The owner-approved direct level shifter
remains selected. The nonexistent GPIO12 gate and I2C approval remain closed;
the Wi-Fi gate is opened only by the product factory after trusted storage has
loaded. The repository-root `main.py` remains passive and is excluded.

## Pinned inputs

- repository baseline before product autostart: `fee7faf`;
- 51 exact frozen project-source files bound by
  `CURRENT_FROZEN_SOURCES.sha256`;
- two exact native user-module sources bound by `NATIVE_SOURCES.sha256`;
- MicroPython v1.28.0 commit
  `e0e9fbb17ed6fd06bb76e266ae554784c9c80804`;
- ESP-IDF v5.5.1 commit
  `fcae32885b0296b32044cb99ecbdc50d98dddb83`;
- Python 3.14.6;
- `mpy-cross` v1.28.0, MPY format 6.3, executable SHA-256
  `d6cecfedb96e7953dbbc766175fdc540a60bac91372317983ba5a906aac7f016`;
- esptool 4.12.0;
- board `DFR0975U_N16R8`, 16-MiB flash and fail-closed Octal PSRAM;
- `SOURCE_DATE_EPOCH=1788100339`, `TZ=UTC`, `LC_ALL=C`,
  `PYTHONHASHSEED=0`, ccache disabled;
- dependency-lock SHA-256:
  `955bf85a5b28d7ec03e7a06c0b00c8d4b9b64a4fb0730b75e2f0fcaa53aef193`.

| Candidate input | SHA-256 |
| --- | --- |
| `manifest.py` | `44a9d350dfa2ba63f77571772c53c06f4457f2232f735332381dedb2cdcacb5b` |
| `FROZEN_MODULES.txt` | `45c437fcdde6dbafd5f40812cbc728695b9ae86f1b71e26167d12a1b2ed88771` |
| `CURRENT_FROZEN_SOURCES.sha256` | `4829f442e06552795cc5c9a7f5a8b4dd70d9ed5e575448e2b48faa7951c09ede` |
| `NATIVE_SOURCES.sha256` | `da5570143e9612bb354e552d205181f0cc56e8277abe5c6cf43414f0fa5e2d50` |
| `artifacts/SHA256SUMS` | `bc42c9fffc92f8f4b2351567c06eed10234d62ff46767bfd29c5a5db4363ad7e` |

The closure excludes credentials, persistent data, acceptance tools and tests.
The VFS copy of `main.py` cannot override the private frozen entry point because
that entry point moves `.frozen` to the front before importing product modules.

## Reproducibility proof

Two clean builds used the same canonical source and output path. The complete
first build directory was moved aside before that exact output path was
recreated. All 15 compared outputs were byte-identical: bootloader, partition
table, application, combined image, UF2, final and combined configurations,
four flash-argument files, flasher JSON, frozen C, ELF and map. The dependency
lock matched the retained DFR0975-U lock byte-for-byte.

The feature-focused configuration, REST, captive DNS, discovery, Bonjour,
product-runtime and Web matrix passed 214 tests. The current 51-file frozen
closure, two-file native ledger and retained artifact ledger pass exactly.
All 51 frozen modules compile for `xtensawin`; the changed JavaScript passes
syntax checking. Both retained ESP32-S3 images have valid checksums and
validation hashes, and `git diff --check` passes. The complete repository
suite ran 1,341 tests: 1,339 passed; only the two deliberately immutable
historical Phase-11 and early Phase-13 source ledgers reported their expected
mismatch against current sources. Those ledgers remain historical evidence
rather than being rewritten.

## Image, layout and retained artifacts

Esptool identifies the application and bootloader as valid ESP32-S3 images
with valid checksums and validation hashes, 16-MiB flash headers, 80-MHz DIO
boot mode and ESP-IDF 5.5.1. The bootloader and partition table are
byte-identical to the accepted Phase-11 artifacts. The partition layout still
contains a 3-MiB factory application at `0x10000` and VFS at `0x310000`; there
is no OTA partition.

| Region/file | Size/result |
| --- | ---: |
| Bootloader | 19,232 B; unchanged |
| Partition table | 3,072 B; unchanged |
| Factory application | 2,181,024 B used of 3,145,728 B |
| Growth from prior installed `a25e3226...` image | 9,296 B |
| Application margin | 964,704 B (about 31%) |
| Combined image | 2,246,560 B; exact end `0x2247a0` |

The retained deployment subset is bound by `artifacts/SHA256SUMS`. The
installed application is:

```text
offset: 0x10000
size:   2181024 bytes
sha256: 67393e2b052d88139e9a8533b8f8b2cc73cd4c8a0ef14fa41168473d7478313d
flash:  exactly authorized; app-only at 0x10000; no full-chip erase
```

The combined retained image is 2,246,560 bytes with SHA-256
`89f9af87e6cc19f6ee59b12ef8c0370a0d1f2d3f83eeb111418d65da4aec6518`.
The exact authorization was
`Flash 67393e2b052d88139e9a8533b8f8b2cc73cd4c8a0ef14fa41168473d7478313d ohne Erase freigegeben.`
Immediately before the write, a complete 2,171,728-byte readback matched the
installed `a25e3226...` baseline. Esptool 4.12 then wrote only the application
at `0x10000` and reported its data hash verified; no full-chip erase,
bootloader write, partition-table write or VFS write was performed. A separate
complete 2,181,024-byte postflash readback had SHA-256
`67393e2b052d88139e9a8533b8f8b2cc73cd4c8a0ef14fa41168473d7478313d`
and compared byte-for-byte equal to the retained application. The retained
bootloader and partition table remain byte-identical to the accepted Phase-11
artifacts.

After a normal boot, `http://192.168.36.114/` was reachable, Requested state
was OFF, `control_faulted` was false and the station was online. Settings
reported schema 3 with `captive_portal_enabled` and `bonjour_http_enabled`
both true; stored and runtime generation remained 7, confirming the direct
migration without a configuration commit. The UI served bundle version
`728dc6163ddf88c828ead9be62c56f762d69cbf64685e431da52c416abfddbc1`.
Bonjour browsing found `Landy Heater._http._tcp`; resolution returned
`heater.local:80`, TXT `path=/`, and the hostname resolved to
`192.168.36.114`. Only visible automatic captive-portal presentation on a
freshly joined phone remains open for this revision.

## Second-stage latency evidence

The deterministic asset bundle version is
`e1b9b5f8cad6278ccdc139fbf47f76460e873a36a05b735f88b024746a154dce`.
Across all 11 static representations, build-time gzip reduces 102,231 raw
bytes to 31,556 bytes. The normal Home static path is 19,341 body bytes and
23,114 complete wire bytes including security headers, requiring 26 bounded
send actions at 1,024 bytes instead of 70 actions for its identity fallback.
The gzip index `304` is 546 wire bytes and one send action; normally cached
versioned assets make no reload requests.

On the target, three staged cold Home paths measured 4.157, 4.228 and 4.519
seconds, with a 4.228-second median. Three normal cached reopens, consisting
of index revalidation plus fresh status, measured 0.848, 0.866 and 1.319
seconds, with a 0.866-second median. The measured cold path transferred 21,829
body bytes. The root gzip representation was 3,633 bytes. The current
versioned asset returned immutable caching and distinct identity/gzip ETags;
exact gzip revalidation returned a bodyless `304`, and an old bundle version
returned `404` with `no-store`.

The separately authorized app-only write covered only the 2,171,728-byte
application at `0x10000`, without a full-chip erase. Esptool verified the
write, and a complete independent readback was byte-identical with SHA-256
`a25e32266e93d921db3b8cc5dd311ffb955b976ccc55af7221859f0344c29d76`.
After reset, USB emitted `LANDY_HEATER_PRODUCT_AUTOSTART_V1`; the product was
reachable at `192.168.36.114` with MAC `34:85:18:92:23:c0`. A fresh headless
Chrome profile rendered the connected Home view. Requested state stayed OFF,
`control_faulted` was false, the network was online, and REST and
configuration remained unfaulted. After the repeated load and Chrome gates,
7,946,400 bytes of GC heap were free at -83 dBm station RSSI. Sensor warnings
were expected because 12-V/sensor hardware was not connected. Longer
internal/DMA-heap and heater-controller fairness soaking remains open.

## Installed first-stage baseline and inherited evidence

Before the change, the complete 2,132,848-byte application region on the board
was read from `0x10000` over USB and compared with the retained repository
baseline. The readback was byte-identical with SHA-256
`4bf2176c96842e2438c339c6d8f2f2c6e3a3c452b5878eabdf47fe2a74df5314`.
The running Web assets were also fetched over the station interface and all
11 matched the corresponding frozen repository assets byte-for-byte. No erase
or write occurred during this reconciliation. The latency measurements,
reconciliation, optimization and pending target gate are recorded in
`../../captures/2026-11-21-dfr0975u-web-latency-optimization-preflash.md`.

The owner then explicitly authorized the exact new application hash. Esptool
4.12 wrote only its 2,135,888-byte app region at `0x10000` without a full-chip
erase. A complete post-write readback was byte-identical with SHA-256
`02197a7d74e6006573bd769ed1a4d34b823a470f7ce4435c02159caa204fe84b`.
After reset the product returned online on its first probe with Requested OFF.
At about -92 dBm, the sequential cold Home components totaled about 10.4
seconds; a repeat with five empty `304` asset responses totaled about 3.6
seconds. All ten asset validators produced empty `304` responses, GC heap free
was 7,889,872 bytes, and REST reported no fault or last error. Phone-observed
numeric timing was not supplied, but the owner confirmed that the requested
phone UI, reload and lazy views work. A post-phone read retained Requested OFF,
network online, no control or REST fault and 7,826,000 free GC heap bytes.

Negative DS18B20 values are preserved as regulation input and encoded after the
existing ceiling operation as signed eight-bit two's-complement (`-1` as
`0xFF`, `-55` as `0xC9`). Separate roof-tent and cabin sessions reached
`TEMP_MONITORING`, heated for at least seven minutes, updated the selected live
temperature, retained the UI connection and returned through controlled Stop
to confirmed OFF. The cabin run exposed the cancel-submit defect corrected by
this candidate. Detailed evidence is in
`../../captures/2026-10-07-dfr0975u-temperature-ui-e2e.md`.

The preceding fully serialized-bootstrap candidate was separately authorized by
its exact hash, written app-only at `0x10000` and independently read back
byte-identical. After reset the phone joined the protected AP with the
unchanged credential. The remembered network did not automatically reopen the
captive portal, so the root URL was opened manually. The UI reached connected
state, all tabs worked, the Setup Assistant opened and, with no sensors
physically attached, the explicit skip choice advanced to the Autoterm step.
Heater 12 V remained off. Live updates and physical assignment of all three
sensors remain open until the board returns to the vehicle.

The preceding serialized-core image with SHA-256
`5f84832e5595368452b25e00a71d7be4e8e7903d1b0a9cf7e6a42f1da9d3878a`
was authorized, written app-only and independently read back byte-identical.
The protected AP joined successfully and a manual UI load fell to about five
seconds, but the connection badge stayed orange and all tabs remained inert.
`/assets/app.js` was directly reachable. This showed that the remaining three
initial subresources could still collide with captive-portal probes. The new
candidate starts only `/assets/boot.js`; that bootstrap serializes CSS,
translations and the app core with bounded retries, and the app serializes its
initial API reads. Server client and backlog limits remain unchanged.

The immediately preceding live Setup-sensor image with SHA-256
`f28059c847a725fda41bfce19d353318bf7d64d7bd2bf0e4f8b16ce601fed6a6`
was authorized, written app-only and completely read back byte-identical. Its
real captive portal opened automatically, and both `/api/v1/status` and
`/api/v1/setup` returned valid JSON. It was rejected because the browser's
parallel request burst exceeded the intentionally bounded two-client/two-
backlog HTTP service: full UI load took about 50 seconds and `setup.js` was not
activated, leaving the Setup button inert. The correction retains the same
server bounds, combines the five stylesheets into one 15,713-byte asset, loads
only `i18n.js` and `app.js` initially, then loads the core modules sequentially
and Setup/Diagnostics on demand. Initial subresource fan-out falls from twelve
requests to three. Detailed evidence is in
`../../captures/2026-10-07-dfr0975u-live-setup-ui-preflash.md`.

The preceding product-autostart image with SHA-256
`66c0799515b45334b2f852d66a4c347614a65b2109883c9f89f253df00a68a5f`
was authorized and written only at `0x10000`; no
full erase occurred. The independent complete 2,121,600-byte readback was
byte-identical and matched that preceding digest. Heater 12 V remained off for
the target gate.

The first normal reset correctly failed closed because production storage had
no AP password, Setup was incomplete and no home-network profile existed. The
owner then explicitly authorized a one-time USB provisioning mutation that set
only a private AP credential, retained `setup_complete=false` and retained zero
home-network profiles. The credential is neither embedded here nor recorded in
Git. Fresh storage readback confirmed network startup allowed, timer startup
blocked and no fault.

After the next normal reset, USB output contained
`LANDY_HEATER_PRODUCT_AUTOSTART_V1` without a subsequent fault. The phone saw
and joined the protected `Landy Heater` AP, the captive portal opened
automatically and the incomplete Setup Assistant became fully visible. This
accepts the normal-reset entry point and recovery UI path. Sensor display,
browser-time synchronization, home-network/mDNS behavior, live Setup sensor
assignment and powered-heater operation were outside this narrow gate. Detailed
evidence is in
`../../captures/2026-10-07-dfr0975u-product-autostart-gate.md`.

The immediately preceding browser-time application
`741ad9f13d106035d8ffe45ed0d84e92815e3d54396cf1f80400f7d349c27d18`
was independently authorized, written, fully read back and accepted through
the real AP/captive-portal/Web-UI path. Its phone supplied one reboot-volatile
UTC sample and timer time became trusted without an RTC write.

The preceding heater image also passed the separately controlled seven-minute
power-1 run: STARTING, RUNNING and SHUTTING_DOWN returned to synchronized
`ready`/`off` with exactly one START and one SHUTDOWN. Production storage was
unchanged. Evidence remains in
`../../captures/2026-10-07-dfr0975u-heater-active-cycle.md`.
