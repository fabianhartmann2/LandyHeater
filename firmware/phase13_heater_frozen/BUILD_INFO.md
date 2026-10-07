# Phase 13 product-autostart frozen firmware build record

Build date: 2026-10-07. Status: **exact 50-file source closure, host lifecycle
and resilience tests, two byte-identical canonical-path builds and offline
artifact gates passed. The exact product-autostart application was authorized,
written app-only, completely read back byte-identical and accepted through the
real protected AP/captive-portal/Setup-Assistant path with heater 12 V off.**

This candidate extends the accepted heater and browser-time image with a
private frozen `main.py` and `app/product_runtime.py`. At every normal reset it
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
| `CURRENT_FROZEN_SOURCES.sha256` | `9e8b2bd70a8c2a9ffa54aa12ac094f13387780f2883499d796860f6f689926b3` |
| `artifacts/SHA256SUMS` | `efa11800656d74f440256a074b19d4b6a5dce0be3f48922beae625d3d773fa8f` |

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
| Factory application | 2,121,600 B used of 3,145,728 B |
| Growth from accepted browser-time image | 9,088 B |
| Application margin | 1,024,128 B (about 33%) |
| Combined image | 2,187,136 B; exact end `0x215f80` |

The retained deployment subset is bound by `artifacts/SHA256SUMS`. The only
image proposed for the next operation is:

```text
offset: 0x10000
size:   2121600 bytes
sha256: 66c0799515b45334b2f852d66a4c347614a65b2109883c9f89f253df00a68a5f
erase:  no full-chip erase
```

This record is evidence only and does not authorize a board flash.

## Target status and inherited evidence

The product-autostart image was authorized and written only at `0x10000`; no
full erase occurred. The independent complete 2,121,600-byte readback was
byte-identical and matched the application digest above. Heater 12 V remained
off for the target gate.

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
