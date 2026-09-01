# DFR0975-U Phase-8 full-product REST gate — 2026-09-01

## Status

**PASS for the Phase-8 single-listener full-product target acceptance on the
DFR0975-U N16R8.**

One real phone request reached the production `RestApplication` through the
production Wi-Fi, configuration, storage, security and HTTP composition. The
complete HTTP 200 JSON response was validated and written before ordered
cleanup. Phase 9 is therefore no longer blocked by the Phase-8 target gate;
automatic startup remains deliberately disabled.

## Safety boundary

- USB power/data only;
- external 2.4-GHz antenna connected;
- heater, vehicle power, UART, RTC/I2C, 1-Wire, sensors and loopback wiring
  disconnected;
- no erase, flash, firmware upload or persistent product configuration;
- the existing test WLAN credential was used unchanged and is not copied into
  this evidence;
- `board_config.py` and the inert test owner were supplied transiently over
  USB, while every required product module loaded from the frozen firmware
  closure;
- one AP lifetime, exactly one listener at `192.168.4.1:80` and one deliberate
  `GET /api/v1/status` request;
- no port 8080, redirect, refresh, diagnostic request or mutation route.

## Preflight

The one focused host module completed 26/26 cases. The board-origin check then
confirmed:

```text
PHASE8_MOUNT_PREFLIGHT .frozen DFRobot DFR0975-U N16R8 with ESP32S3 \
app/network_manager.py adapters/micropython_http_server.py \
False False False 8298096
```

Thus the exact custom machine identity, frozen-first path, frozen production
network/HTTP origins, closed radio approval, inactive STA/AP and 8,298,096
bytes free GC heap were present before the run. The first direct preflight
without the transient USB mount stopped harmlessly at the intentionally
external `board_config.py` import, before any radio or storage action. It was
not a target attempt.

## Single target run

The first stage emitted AP READY without importing HTTP. After the phone
associated as the sole client, the runner adopted the same AP lifetime,
provisioned its isolated A/B stores, composed the complete product and opened
the sole listener:

```text
PHASE8_FULL_REST_PHONE_AP_READY_V1
PHASE8_FULL_REST_PHONE_CLIENT_SEEN_V1
clients=1
PHASE8_FULL_REST_PHONE_READY_V1
url=http://192.168.4.1/api/v1/status
window_seconds=300
```

The owner opened that exact URL once. The observational gate proved one valid
status request, `RestApplication` entry and return, completed status-data and
JSON validation, canonical HTTP 200 encoding, complete wire delivery and
client close. It also proved that no mutation route was exposed, no heater or
protocol call occurred, production storage was unchanged and the isolated A/B
files were removed. The target returned:

```text
full_rest_status_response_completed=True
mutation_routes_exposed=False
mutation_api_available_after_cleanup=False
isolated_files_removed=True
http_rest_radio_cleanup_confirmed=True
PHASE8_FULL_REST_PHONE_SMOKE_PASS_V1
PHASE8_HOST_RESULT 8 80
```

The PASS contract requires all ten section-27.7 GC-heap checkpoints to be at
least 32 KiB: product imports, configuration adoption, Wi-Fi factory, AP
ready, client association, before HTTP start, proof before listen, after
bind/listen, after response and after ordered cleanup. The exact values were
not emitted by the outer host wrapper, so this record claims the enforced
thresholds, not unrecorded numbers.

## Independent post-check

Without repeating the request, a separate USB check returned:

```text
PHASE8_POSTCHECK_STA False
PHASE8_POSTCHECK_AP False
PHASE8_POSTCHECK_HEAP 8319600
PHASE8_POSTCHECK_VFS (4096, 4096, 3312, 3309, 3309, 0, 0, 0, 0, 255)
PHASE8_POSTCHECK_FILES ()
```

The VFS geometry/free-block signature exactly matches the pre-Phase-8 state.
The runner's own pre-PASS safe-state check additionally confirmed all Wi-Fi,
UART/TX-gate, I2C and 1-Wire locks restored, both interfaces inactive, the
Wi-Fi lease released, REST security stopped and every socket closed.

## Boundary after PASS

This closes the Phase-8 target gate only. It does not enable `boot.py` or
`main.py`, implement the Phase-9 web UI, approve S3 UART/level hardware, or
authorize heater, I2C, 1-Wire, vehicle-power or peripheral testing. A numeric
internal/DMA heap sample under a future broader sustained workload remains a
separate robustness measurement; it is not one of the section-27.7 GC-heap
acceptance checkpoints proven here.
