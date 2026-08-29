# Phase 8 – Wi-Fi/HTTP capacity audit on DFR0654

Date: 2026-08-11  
Board: DFRobot FireBeetle 2 ESP32-E V1.0 / DFR0654  
Firmware: MicroPython 1.28.0, `ESP32_GENERIC`  
Status: **BLOCKED – no combined phone-HTTP pass token**

## Safety scope

The board remained disconnected from the heater, level shifter, vehicle 12 V,
UART, sensors and RTC. `boot.py` and `main.py` stayed passive. Wi-Fi was opened
only by explicit test calls and was closed again after each attempt. The
temporary WPA2 credential is deliberately absent from this record, commands,
exceptions and diagnostics.

This audit separates three different facts which must not be collapsed into a
single Phase-8 pass:

1. the isolated REST closure with fake sockets passed separately;
2. the production Wi-Fi port can start an AP and observe an associated client
   when HTTP is absent;
3. the complete future product closure does not yet fit the ESP32 heap with
   the required safety margin.

## Initial eager-import attempt

The first phone-HTTP runner imported both the Wi-Fi and HTTP boundaries before
configuring the access point. Approximately 48,112 bytes remained after that
eager import. A subsequent allocation in the AP configuration path could not
complete, so the attempt stopped before the AP became ready.

This attempt emitted neither `PHASE8_PHONE_HTTP_READY_V1`,
`PHASE8_PHONE_HTTP_CLIENT_SEEN_V1` nor
`PHASE8_PHONE_HTTP_SMOKE_PASS_V1`. It therefore proves no AP, DHCP or HTTP
result. The failure motivated an AP-first, lazy-HTTP load order; it is not a
reason to lower the 32-KiB heap safety floor.

## Direct production Wi-Fi-port check

A separate, bounded WPA2 check exercised the production Wi-Fi factory without
the HTTP closure. The measured free heap was:

```text
after_factory=114304
after_ap=105984
```

The AP reported exactly one associated client. Both recorded cleanup checks
were `True/True`. Association alone does not prove that the phone obtained a
DHCP lease, and this Wi-Fi-only check served no HTTP response.

## AP-first lazy HTTP attempt

`tools/phase8_phone_http_smoke.py` was then changed to load only the production
Wi-Fi boundary first, configure and verify the AP, and import/start the
production `MicroPythonHTTPServer` only afterward. This order reached a real
simultaneous AP-and-HTTP-ready state and emitted:

```text
PHASE8_PHONE_HTTP_READY_V1
```

The run ended before it observed an AP client or a completed HTTP response. It
did **not** emit either of these success tokens:

```text
PHASE8_PHONE_HTTP_CLIENT_SEEN_V1
PHASE8_PHONE_HTTP_SMOKE_PASS_V1
```

Consequently this attempt proves only that the minimal AP-first test closure
can bring up the real AP and real HTTP listener together. It does not prove a
phone DHCP lease, peer validation, response delivery or end-to-end browser
access. The fail-safe postflight showed both WLAN interfaces inactive and the
temporary RAM Wi-Fi approval restored to `False`.

## Full product-closure capacity blocker

The import-capacity audit of the planned Configuration + Storage + REST
closure accounts for approximately 155.9 KiB of compiled code before adding
NetworkManager, heater/protocol modules and their live runtime objects. The
current monolithic product closure therefore cannot retain the required
32-KiB free-heap margin on this ESP32. Lazy HTTP loading fixes the early
AP-start ordering problem in the small phone runner, but it does not resolve
this full-product P1 capacity blocker.

After the lazy-import changes, the complete CPython regression suite passed
1000/1000 tests. That host result validates the modeled behavior but cannot
override the missing combined target acceptance.

Phase 9 is not released. Before it can start, the production composition must
be reduced or partitioned and a new combined target acceptance must prove, in
one run:

- the intended configuration/storage/application closure;
- AP startup on `192.168.4.1` followed by lazy HTTP startup;
- at least 32 KiB free heap at every binding checkpoint;
- one real AP peer, DHCP-visible phone connectivity and one fully written HTTP
  response;
- sockets closed before Wi-Fi, both interfaces inactive, temporary approval
  cleared and passive boot after reset.

## Relationship to the isolated REST pass

The historical evidence in
`captures/2026-08-11-phase8-rest-esp32-smoke.md` remains valid and unchanged:
its isolated, no-network fake-socket closure passed 4/4 iterations and 12/12
responses with a final measured heap of 42,288 bytes and the exact token
`PHASE8_USB_REST_SMOKE_PASS_V1`. That is component evidence, not proof that the
full Wi-Fi + HTTP + application product composition fits or serves a phone.

## Relevant source hashes after the lazy-load fix

```text
9020aded71f0122d9e482d29f4b701c5da6d88f3e4c2cc060397cfc0c5889f73  tools/phase8_phone_http_smoke.py
d9615fdfea89653852d444c976df66a518fcdc159d75dc185978aa5fccba966c  adapters/micropython_http_server.py
6259182ab33548f9fa9c920650f18ac7e5e6a50dd722e8be08419406d8cd4230  app/network_manager.py
3d14daf0118040796ae04d2282eb3b8d848eb3141df3fa8df48c0d567e43efdd  app/network_composition.py
daddad382af16dcef5dcb4c9f3e8881b44cd81e011dc3c9c75ad87d29d860287  app/rest_composition.py
48556ef3f3c447c1d1bc53a7569d8a281374ec79e7bfbe0dfd41b9e4bb625b02  hardware/micropython_wifi.py
76dbfba8dd69c2fdb217c546136d628291f448c753ced746a3aaa1a2cdb8a15a  tools/phase7_network_smoke.py
055ee7ad76be20b61b79ba80a2c73f6b832a072b5b344ce63bb154132135341c  board_config.py
```
