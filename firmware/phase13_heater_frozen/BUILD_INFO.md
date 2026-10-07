# Phase 13 live Setup-sensor frozen firmware build record

Build date: 2026-10-07. Status: **exact 50-file source closure, host lifecycle
and resilience tests, two byte-identical canonical-path builds and offline
artifact gates passed. The exact application was flashed app-only, read back
byte-identical and accepted on target for serialized UI startup and the
explicit sensor-setup skip path. Physical live-sensor assignment remains
open.**

This candidate extends the accepted product-autostart image with live
DS18B20 readings in the sensor-assignment step and an explicit, fail-closed
option to skip that step. At every normal reset it
loads the trusted production stores, starts sensors and the heater protocol in
requested-OFF state, brings up the recovery AP and Web UI, and only then arms
the Scheduler. A station listener is attached dynamically after DHCP/mDNS,
without stopping the AP listener or captive DNS. Runtime WLAN, HTTP-listener
and diagnostics failures degrade those optional services without ending heater
supervision. Safety-critical failures enter the existing controlled heater
shutdown path.

The private frozen `board_config.py` differs from the safe repository profile
in exactly two values: `UART_PINS_APPROVED=True` and
`UART_PROTOCOL_TX_ENABLED=True`. The owner-approved direct level shifter
remains selected. The nonexistent GPIO12 gate and I2C approval remain closed;
the Wi-Fi gate is opened only by the product factory after trusted storage has
loaded. The repository-root `main.py` remains passive and is excluded.

## Pinned inputs

- repository baseline before product autostart: `fee7faf`;
- 50 exact project-source files bound by `CURRENT_FROZEN_SOURCES.sha256`;
- MicroPython v1.28.0 commit
  `e0e9fbb17ed6fd06bb76e266ae554784c9c80804`;
- ESP-IDF v5.5.1 commit
  `fcae32885b0296b32044cb99ecbdc50d98dddb83`;
- Python 3.14.6;
- `mpy-cross` v1.28.0, MPY format 6, executable SHA-256
  `ceda0dfb2f800a3970f2be6a036aedfc3e9bcdb49579aeb3d5a0cb1a0390c849`;
- esptool 4.12.0;
- board `DFR0975U_N16R8`, 16-MiB flash and fail-closed Octal PSRAM;
- `SOURCE_DATE_EPOCH=1788100339`, `TZ=UTC`, `LC_ALL=C`,
  `PYTHONHASHSEED=0`, ccache disabled;
- dependency-lock SHA-256:
  `955bf85a5b28d7ec03e7a06c0b00c8d4b9b64a4fb0730b75e2f0fcaa53aef193`.

| Candidate input | SHA-256 |
| --- | --- |
| `manifest.py` | `a27ccfd4a4ea059a6979a4f2535872601ba22295bb1cc01560d22a25ef47ed01` |
| `FROZEN_MODULES.txt` | `ab30f9421528d97ed68fc4d9aed78d659b18b1f3a292e826acd1f6a6722321c2` |
| `CURRENT_FROZEN_SOURCES.sha256` | `001fdf2c497ff4b469c74441a7dac9438ebaf47bf2659a4b5b41944f7f827791` |
| `artifacts/SHA256SUMS` | `93256209d2000ff5ad475072769eeb122888e7f9875fb91804a41c4911ff2a3a` |

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

The focused sensor, REST, configuration, Web, smoke-runner and frozen-artifact
matrix passed 122/122 tests, and the browser Setup validation runner passed.
The complete
repository suite ran 1,289 tests: 1,287 passed and only the two deliberately
immutable historical Phase-11 and early Phase-13 source ledgers reported the
expected mismatch against today's evolved working sources. The current
50-file closure and retained artifact ledgers both passed exactly. `git diff
--check` passed.

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
| Factory application | 2,131,568 B used of 3,145,728 B |
| Growth from accepted product-autostart image | 9,968 B |
| Application margin | 1,014,160 B (about 32%) |
| Combined image | 2,197,104 B; exact end `0x218670` |

The retained deployment subset is bound by `artifacts/SHA256SUMS`. The only
image proposed for the next operation is:

```text
offset: 0x10000
size:   2131568 bytes
sha256: c344bb3ca6f4f7540b1925e8d65a7a8cdc93328c230a8a640fe9c7c6b153aae9
erase:  no full-chip erase
```

This record is evidence only and does not authorize a board flash.

## Target status and inherited evidence

The fully serialized-bootstrap candidate above was separately authorized by
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
