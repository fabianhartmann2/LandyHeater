# DFR0975-U Web-latency optimization build and deployment record

Date: 2026-11-21. Status: **live baseline diagnosed, installed firmware and
repository reconciled exactly, first optimization built reproducibly,
hash-bound app-only flash authorized, written and fully read back
byte-identical. Automated HTTP/heap/OFF gates and the real phone UI passed.
Because 10.4 seconds remained unacceptable, a second gzip/versioned-cache
candidate was built twice byte-identically, separately hash-authorized,
written app-only without a full-chip erase and fully read back byte-identical.
Its gzip/cache, browser-render, timing, heap and Requested-OFF target gates
passed.**

## Live baseline

The running controller was reachable through its station address
`192.168.36.114`. The user confirmed that the roughly 20-second first load was
also reproducible close to the access point, so radio distance alone did not
explain the symptom.

Read-only HTTP measurements showed low connection/first-byte latency followed
by slow response bodies. Representative observations were:

- `/`: 15,784 bytes in about 6 seconds;
- `/assets/ui.css`: 15,713 bytes in about 6 to 11 seconds;
- `/assets/i18n.js`: 16,357 bytes in about 12 seconds;
- `/assets/app.js`: 7,781 bytes in about 3 to 9 seconds;
- JSON API calls: about 0.3 to 1.6 seconds;
- observed body throughput: roughly 1.3 to 2.7 KiB/s.

The code path explained that profile. The cooperative server offered only 256
bytes per send action. The 10-ms product loop shared turns among AP HTTP, STA
HTTP and captive DNS, so the station writer often progressed only every third
discovery turn. Startup then serialized eight static bodies and the status,
settings and timer APIs. Every response used `Cache-Control: no-store`, so a
repeat visit transferred all static bodies again. There was no fixed 20-second
timer, external font or CDN dependency.

The live station RSSI was weak during the measurement (about -90 to -93 dBm),
which can add variance, but the bounded send cadence, serialized payload and
disabled browser reuse were sufficient independent bottlenecks and matched the
near-access-point symptom.

## Exact installed-firmware reconciliation

Before changing a source byte, all 11 live Web assets were fetched and compared
with `app.web_assets.ASSETS`; every asset was byte-identical.

With USB only and the board placed manually in ROM download mode, esptool 4.12
read 2,132,848 bytes from application offset `0x10000`. The readback was
byte-identical to the retained repository artifact:

```text
size:   2132848 bytes
sha256: 4bf2176c96842e2438c339c6d8f2f2c6e3a3c452b5878eabdf47fe2a74df5314
```

No write or erase command was used. After a reset the product returned online
with Requested OFF. Heater 12 V was not part of this gate.

## Implemented optimization

- Every `/assets/*` body now has a build-time strong SHA-256 ETag.
- Static assets use `Cache-Control: no-cache`: a browser may store them but
  must revalidate. An exact `If-None-Match` returns a bodyless `304`.
- Index, API, errors and all mutation paths remain `no-store`.
- Normal Home startup loads only `home.js` and `/api/v1/status`; Timer,
  Settings, Setup and Diagnostics modules/data load on first use.
- Direct hash links and required Setup auto-open remain supported.
- Latest navigation wins during slow loads; module binding and dialog loading
  are deduplicated.
- The normal static Home payload falls from 73,917 to 64,695 bytes and avoids
  the initial Settings and Timer API calls.
- The receive budget remains 256 bytes. The send budget is 512 bytes, still
  one socket action per cooperative step and below the 1,440-byte TCP MSS.

The larger send offer uses a `memoryview` over the existing response rather
than creating a second 512-byte body. Partial nonblocking sends and `EAGAIN`
remain supported. With the measured startup payload, the static-body send
actions are approximately halved; no material heap increase is expected.

## Host and build gates

- focused Web/cache/HTTP/socket suite: 122/122 passed;
- full suite: 1,304 run, 1,302 passed; only the two deliberately immutable
  historical Phase-11 and early Phase-13 source ledgers reported their known
  mismatch against current sources;
- current 50-file frozen-source ledger: exact;
- JavaScript syntax: application, Home and Diagnostics passed;
- changed frozen Python modules: `mpy-cross` `xtensawin` passed;
- two clean canonical-path builds: all 15 compared outputs byte-identical;
- application and bootloader: ESP32-S3 checksum and validation hash valid;
- flash header: 16 MiB, 80 MHz, DIO; ESP-IDF 5.5.1;
- bootloader and partition table: unchanged.

Application candidate subsequently authorized and deployed:

```text
offset: 0x10000
size:   2135888 bytes
sha256: 02197a7d74e6006573bd769ed1a4d34b823a470f7ce4435c02159caa204fe84b
erase:  no full-chip erase
```

The image occupies 2,135,888 of 3,145,728 application bytes and leaves
1,009,840 bytes (about 32%).

## Authorized deployment and immediate target result

The owner explicitly authorized the exact application hash above without a
full-chip erase. The board identified as the expected ESP32-S3 revision 0.1
with 8 MiB PSRAM. Esptool 4.12 wrote exactly 2,135,888 bytes at `0x10000`;
only the app-sector range `0x10000` through `0x219fff` was erased. Bootloader,
partition table, configuration storage and VFS were not written. Esptool's
write verification passed.

The complete 2,135,888-byte application was then read back before reset. Both
SHA-256 and byte comparison matched the authorized artifact exactly:

```text
02197a7d74e6006573bd769ed1a4d34b823a470f7ce4435c02159caa204fe84b
```

After a physical reset the product returned at `192.168.36.114` on the first
probe. Requested remained OFF, `control_faulted=false`, the network was online
and Setup remained complete. Actual heater communication was `error`/`unknown`
because heater 12 V was not active; no heating command was requested.

Read-only sequential station measurements at about -92 dBm were:

- first root: 15,784 bytes in 3.513 s (1.863 s to first byte);
- boot: 942 bytes in 0.467 s;
- CSS: 15,713 bytes in 1.829 s;
- translations: 16,357 bytes in 1.970 s;
- application core: 8,944 bytes in 1.122 s;
- Home module: 6,955 bytes in 0.888 s;
- status: 2,388 bytes in 0.642 s.

The strictly sequential cold Home sum was about 10.4 seconds, approximately
half the reported baseline. On a repeat pass, root took 1.924 s, the five
initial asset revalidations together took 0.988 s with empty `304` bodies, and
status took 0.642 s: about 3.6 seconds total. Every one of the ten
`/assets/*` routes separately returned an empty `304` for its exact ETag; all
ten revalidations completed in 1.988 s. Diagnostics reported 7,889,872 free GC
heap bytes, `rest.faulted=false`, no REST error and Requested OFF.

The owner then opened the UI on the phone and exercised the requested Home,
Timer, Settings and Diagnostics flow plus reload, reporting that it worked.
No numeric phone timings were supplied. A post-phone status/diagnostics read
still showed Requested OFF, `control_faulted=false`, network `online`, no REST
fault or last error and 7,826,000 free GC heap bytes.

## Remaining target gate

The exact flash and readback, automated HTTP timings, ETag/304 behavior, GC
heap and Requested-OFF checks are complete. Remaining checks are:

1. numeric phone-observed first Home and cached reload timings, if desired;
2. internal and DMA-capable heap plus explicit server snapshot evidence;
3. continued controller/UART fairness during a broader repeated-load soak.

No further flash is authorized by this record.

## Second-stage latency deployment

The remaining transfer bottleneck was addressed without changing heater or
storage behavior:

- all 11 identity representations are accompanied by deterministic build-time
  gzip bytes; no runtime compression occurs;
- identity and gzip have separate strong SHA-256 ETags and
  `Vary: Accept-Encoding`;
- the revalidated index references only
  `/assets/v-<full-bundle-sha256>/...` URLs;
- only the exact current prefix receives a one-year immutable cache policy;
  stale prefixes return `404` and cannot resolve to current bytes;
- CSS and translations load concurrently, then the application core;
- Home and status load concurrently, with at most two clients as before;
- the send offer is 1,024 bytes, below the 1,440-byte TCP MSS; receive remains
  256 bytes and one `step()` still performs at most one socket action.

The generated bundle version is
`e1b9b5f8cad6278ccdc139fbf47f76460e873a36a05b735f88b024746a154dce`.
All static payloads total 102,231 identity bytes or 31,556 gzip bytes. The
normal cold Home static path is 19,341 body bytes and 23,114 complete wire
bytes, requiring 26 bounded sends instead of 70 for the identity fallback.
An index gzip `304` is 546 wire bytes and one send; normally reused versioned
assets require no network requests.

Offline gates passed:

- focused Web/cache/HTTP/socket suite: 144/144;
- complete suite: 1,312 run, 1,310 passed; only the two immutable historical
  source ledgers failed as expected;
- current 50-file source ledger and retained artifact ledger: exact;
- changed frozen modules: `mpy-cross -march=xtensawin` passed;
- two clean builds at the same canonical path: all 15 outputs byte-identical;
- application and bootloader checksums and validation hashes: valid;
- bootloader and partition table: unchanged;
- application margin: 974,000 bytes.

Exact installed application:

```text
offset: 0x10000
size:   2171728 bytes
sha256: a25e32266e93d921db3b8cc5dd311ffb955b976ccc55af7221859f0344c29d76
erase:  no full-chip erase; exact app-only write separately authorized
```

The owner explicitly authorized this exact SHA-256 without an erase. Esptool
wrote only the 2,171,728-byte application at `0x10000`; its sector erase was
limited to `0x10000` through `0x222fff`. Write verification passed. Before
reset, the complete application was independently read back and matched the
authorized artifact byte-for-byte with the same SHA-256.

After reset, USB emitted `LANDY_HEATER_PRODUCT_AUTOSTART_V1`. The controller
returned at `192.168.36.114`; ARP reported the expected MAC
`34:85:18:92:23:c0`. Requested remained OFF, `control_faulted=false`, the
station network was online, and REST and configuration were not faulted. The
initial connection delay was the normal station-WLAN rebuild after reset, not
an address change.

Target HTTP gates passed:

- `/` returned the 3,633-byte gzip representation with `no-cache`;
- the exact bundle path returned gzip, distinct identity/gzip ETags and
  `public, max-age=31536000, immutable`;
- exact gzip ETag revalidation returned a bodyless `304`;
- a stale full-version prefix returned `404` with `no-store`;
- gzip decoded byte-identically to the identity representation.

Three complete staged cold Home paths measured 4.519, 4.228 and 4.157 seconds
(median 4.228 seconds) and transferred 21,829 body bytes each. Three normal
cached reopens, consisting of index revalidation plus fresh status, measured
1.319, 0.848 and 0.866 seconds (median 0.866 seconds). A fresh headless Chrome
profile rendered the active Home view with the connection state online. After
the repeated network and browser gates, 7,946,400 bytes of GC heap remained
free at a weak station RSSI of -83 dBm. The reported sensor failures were
expected because heater 12 V and the external sensors were not part of this
gate.

Numeric phone-observed timing remains optional. A longer internal/DMA-capable
heap and heater-controller/UART fairness soak is still open. No further flash
is authorized by this record.
