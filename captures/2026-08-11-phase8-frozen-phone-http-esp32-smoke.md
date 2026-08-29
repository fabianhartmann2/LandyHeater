# Phase 8 – Frozen AP-first phone HTTP smoke on DFR0654

Date: 2026-08-11  
Board: DFRobot FireBeetle 2 ESP32-E V1.0 / DFR0654  
Firmware: custom MicroPython 1.28.0, `ESP32_GENERIC`, 40 project modules frozen  
Status: **PASS for the minimal production Wi-Fi/HTTP path; full product
composition not accepted**

## Safety scope

Only USB was connected. The heater, level shifter, vehicle 12 V, UART,
sensors, RTC, I2C and 1-Wire remained disconnected. `boot.py` and `main.py`
stayed passive. Wi-Fi was opened only by the explicitly confirmed runner and
was closed before the runner returned.

The temporary WPA2 credential is deliberately absent from this record. It was
not printed in the runner result, postflight diagnostics or source-hash
evidence.

## Frozen candidate and test boundary

The board ran the custom frozen candidate whose artifacts are recorded under
`firmware/phase8_frozen/`:

```text
9fb71019028d35863775bbc0beb1add9a41825ee280e98e4515caa4fc44d90b3  firmware.bin
d46b7a2c71db493483adcdeb92a33a508696a7d99aaa051e21957032ae17fc5b  micropython.bin
```

The complete 1,971,248-byte application partition readback matched
`micropython.bin`. For the explicit probes, `.frozen` was placed ahead of
filesystem package copies without changing normal startup. All 40 project
modules in the manifest were then independently imported from `.frozen`. The
temporary runner and its support package were uploaded as three isolated
`.mpy` files and removed after the test.

The exercised production path was:

```text
NetworkManager -> MicroPython Wi-Fi port -> real WPA2 AP
                                      |
                                      +-> MicroPythonHTTPServer
                                          -> fixed read-only radio-check handler
```

The AP was configured first. Only after `192.168.4.1` was confirmed did the
runner lazily import and bind the production HTTP adapter. The handler accepted
only `GET /api/v1/phase8-radio-check` from a peer in the AP subnet and returned
a fixed, read-only document with heater, UART and sensor buses disabled.

This boundary intentionally did **not** construct `RestApplication`,
`ConfigManager`, configuration storage, the CSRF/security and rate-limit
composition, or heater/protocol runtime objects.

## Real phone result

The phone associated with `Landy Heater`, appeared to the HTTP server as peer
`192.168.4.2`, and opened the exact direct-IP URL. The sanitized runner output
contained:

```text
PHASE8_PHONE_HTTP_READY_V1
PHASE8_PHONE_HTTP_CLIENT_SEEN_V1
clients=1
http_response_completed=True
http_cleanup_confirmed=True
radio_cleanup_confirmed=True
PHASE8_PHONE_HTTP_SMOKE_PASS_V1
```

The result reported one validated request from `192.168.4.2`, one rejected
request and two completely written responses. The accepted request used the
required AP IP in `Host` and the exact allowlisted path. No parser timeout,
socket error or transport fault was accepted by the runner.

## Heap checkpoints

| Checkpoint | Free heap |
| --- | ---: |
| Before runner imports | 102,400 B |
| After production Wi-Fi import | 83,184 B |
| After AP ready | 81,840 B |
| After lazy HTTP import | 76,240 B |
| After ordered HTTP/Wi-Fi cleanup | 75,072 B |

Every measured checkpoint remained above the 32-KiB floor. The runner did not
take a separate heap sample immediately after the completed response; the
post-cleanup sample followed it. Therefore these numbers must not be presented
as the complete section-27.7 product checkpoint set.

## Cleanup and reset

The runner first closed the HTTP listener and all client sockets, then closed
NetworkManager/the Wi-Fi port, released the singleton lease and restored the
temporary RAM approval. Its own HTTP and radio cleanup checks were both true.

An independent USB postflight then confirmed:

```text
RADIO_STATE False False
WIFI/UART/I2C/1Wire approvals False
HEAP 106608
PHONE_PROBE_ABSENT True
```

The exact three temporary `.mpy` files and their two test directories were
absent. A hard reset produced the unchanged banner:

```text
Landy Heater safe boot; UART inactive; protocol TX disabled
```

After reset MicroPython still reported v1.28.0, both WLAN interfaces and every
hardware approval/lease lock were false, and free heap was 149,856 bytes.

## Exact conclusion and remaining blocker

This pass supersedes only the earlier narrow conclusion that the minimal
AP-first runner had not yet served a phone. It proves that the frozen firmware
can keep the production NetworkManager/Wi-Fi and HTTP adapter resident
together long enough to validate a real AP peer and fully write the fixed
radio-check response, then clean up safely.

It does not prove that the full Configuration + Storage + `RestApplication` +
NetworkManager + HTTP product composition fits, nor that any implemented
settings, timer or heater-control REST route works over real Wi-Fi. Freezing
40 project modules changes the earlier dynamic-import premise, so the old
155.9-KiB estimate does not by itself prove a post-frozen failure. The P1
product acceptance remains open until that exact combined run passes; Phase 9
remains unreleased. The earlier failed eager-import and ready-only attempts
remain historical evidence in
`captures/2026-08-11-phase8-wifi-http-capacity-blocked.md`.

## Runner hashes

```text
9020aded71f0122d9e482d29f4b701c5da6d88f3e4c2cc060397cfc0c5889f73  tools/phase8_phone_http_smoke.py
4fdc23236e8eb769a8cafd3b35e3763eaa3aad064ab1c27bed925344986a8988  tools/__init__.mpy
b3729a8ab904464ee36f915653e43299969773a8009baba19877532bcabc9012  tools/phase7_network_smoke.mpy
c67efa5d9657cfcd62f436b3146539acf11eae1a7dfd91b738a7a182ebbecf3f  tools/phase8_phone_http_smoke.mpy
```
