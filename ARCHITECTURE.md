# Landy Heater — Software Architecture

**Version:** 1.1  
**Status:** Phase-8 single-listener full-product target acceptance passed on
the DFR0975-U; DFR0654 remains historical capacity evidence; Phase 9 released
but not started and automatic product startup remains disabled

**Runtime:** MicroPython on ESP32

## 1. Architectural goals

The architecture is designed around four rules:

1. Heater safety and supervision are independent of the web UI.
2. Only one component owns heater-control decisions.
3. Protocol details are isolated from application behavior.
4. Hardware and future vehicle integrations remain replaceable/modular.

## 2. High-level architecture

```text
                              LANDY HEATER
                                   |
                            Web Interface
                                   |
                           REST Application
                         +---------+---------+
                         |                   |
              ConfigurationAPIGateway  ManualControlGateway -----+
                         |                                      |
                   ConfigManager                                |
                                                                |
                         Scheduler -> short-lived StartIntent ---+
                                                                |
                                         SchedulerControllerGateway
                                         (synchronous authorization)
                                                    |
                                             Requested State
                                    |
                         +----------v-----------+
                         |   HeaterController   |
                         |     State Machine    |
                         +----------+-----------+
                                    |
                         +----------v-----------+
                         |  AutotermProtocol    |
                         | Frame/CRC/Parser     |
                         +----------+-----------+
                                    |
                         +----------v-----------+
                         |   UART Transport     |
                         +----------+-----------+
                                    |
                                Autoterm

       +----------------------+     +-----------------------+
       | TemperatureManager   |     | Diagnostics/EventLog  |
       | DS18B20 x3           |     | Capture / History     |
       +----------------------+     +-----------------------+

       +----------------------+     +-----------------------+
       | RTC / TimeService    |     | NetworkManager        |
       | DS3231/NTP/Browser   |     | AP/STA/mDNS           |
       +----------------------+     +-----------------------+

                    +--------------------------------+
                    | ConfigManager / Persistent FS  |
                    +--------------------------------+
```

## 3. Project layout

Abbreviated current implemented structure:

```text
landy-heater/
├── boot.py
├── main.py
├── board_config.py
├── app/
│   ├── application_state.py
│   ├── configuration_api_gateway.py
│   ├── configuration_bootstrap.py
│   ├── heater_controller.py
│   ├── manual_control_gateway.py
│   ├── network_composition.py
│   ├── network_configuration.py
│   ├── network_manager.py
│   ├── rest_application.py
│   ├── rest_composition.py
│   ├── scheduler.py
│   ├── scheduler_controller_gateway.py
│   └── temperature_manager.py
├── adapters/
│   ├── config_file_store.py
│   ├── ds18b20_adapter.py
│   ├── ds3231_adapter.py
│   └── micropython_http_server.py
├── hardware/
│   ├── micropython_ds18b20.py
│   ├── micropython_ds3231.py
│   └── micropython_wifi.py
├── protocol/
│   ├── autoterm_protocol.py
│   ├── autoterm_frames.py
│   ├── crc16.py
│   └── uart_transport.py
├── services/
│   ├── config_manager.py
│   ├── configuration_errors.py
│   ├── configuration_storage.py
│   ├── http_protocol.py
│   ├── protocol_capture.py
│   ├── rest_rate_limiter.py
│   ├── rest_security.py
│   ├── rtc_time_bridge.py
│   ├── strict_json.py
│   └── time_service.py
├── tests/
│   ├── test_crc.py
│   ├── test_frames.py
│   ├── test_parser.py
│   ├── test_heater_controller.py
│   ├── test_scheduler.py
│   ├── test_http_protocol.py
│   ├── test_strict_json.py
│   ├── test_rest_security.py
│   ├── test_rest_rate_limiter.py
│   ├── test_rest_application.py
│   ├── test_rest_composition.py
│   ├── test_phase8_phone_http_smoke.py
│   ├── test_phase8_rest_smoke.py
│   └── test_micropython_http_server.py
├── REQUIREMENTS.md
├── ARCHITECTURE.md
├── PROTOCOL.md
└── README.md
```

Planned later modules include the static Web UI, EventLog, live protocol view,
exports and consolidated product composition. Names may be refined during
those phases, but the responsibility boundaries must remain.

## 4. Hardware abstraction

`board_config.py` shall be the authoritative source for a selected board
profile. The active profile is bound to the physically confirmed DFR0975-U
V1.0 and `ESP32-S3-WROOM-1U-N16R8`; intended GPIO routes are present, while
every electrical/radio approval remains closed. Phase-7/8 target runners also
require the exact custom MicroPython machine identity. The historical DFR0654
validation branch remains available for regression checks. Its RX-only,
capture and loopback tools deliberately stay DFR0654-only because their
pin-neutralization behavior is not transferable; they reject the active S3
profile until a distinct S3 UART gate is implemented. Board-specific safety
validation remains explicit and fail-closed.

Example configuration categories:

```python
UART_ID = 1
UART_TX_PIN = ...
UART_RX_PIN = ...
UART_BAUDRATE = 9600

ONEWIRE_PIN = ...

I2C_ID = ...
I2C_SDA_PIN = ...
I2C_SCL_PIN = ...
```

Future Votronic UARTs can be added later without changing the Autoterm module.

## 5. Core application state

Use explicit state objects instead of Node-RED-style globals.

### 5.1 Requested heater state

Conceptual model:

```python
RequestedHeaterState(
    on=False,
    mode="roof_tent",
    target_temperature=20,
    power_level=4,
    runtime_minutes=60,
    source="manual"
)
```

This is intention, not hardware truth.

### 5.2 Actual heater state

Conceptual model:

```python
ActualHeaterState(
    communication="ok",
    initialized=True,
    heater_state="running",
    voltage=12.6,
    glow_plug_raw=...,
    fan_raw=...,
    last_status_ms=...
)
```

### 5.3 Session state

A session records the active operation:

```python
HeaterSession(
    id=...,
    source="quick_start|manual|timer",
    mode=...,
    target=...,
    started_at=...,
    expires_at=...,
    timer_id=None
)
```

A session is distinct from the heater’s raw state.

## 6. Single control authority

`HeaterController` is the sole authority allowed to request protocol transmission.

Forbidden architecture:

```text
HTTP POST /start ---> UART
Timer -----------> UART
Button ----------> UART
```

Required architecture:

```text
HTTP / Timer / UI
        |
        v
 Requested State
        |
        v
 HeaterController
        |
        v
 AutotermProtocol
        |
        v
 UART
```

This prevents contradictory simultaneous commands.

## 7. HeaterController responsibilities

`HeaterController` shall:

- compare requested vs. actual state
- maintain safe state transitions
- avoid duplicate Start
- avoid Start when communication is unknown
- handle shutdown requests
- handle runtime expiry
- react to active-sensor failure
- determine when external temperature should be transmitted
- manage session lifecycle
- expose human-readable status to the application

It must not:

- serve HTML
- scan Wi-Fi
- parse DS18B20 bus details
- persist raw configuration directly
- contain low-level CRC logic

## 8. Heater state machine

Known actual states:

```text
UNKNOWN
OFF
STARTING
RUNNING
SHUTTING_DOWN
TEMP_MONITORING
```

Representative decision rules:

```text
Actual OFF + Requested OFF
    -> do nothing

Actual OFF + Requested ON
    -> send START if initialized and communication OK

Actual STARTING + Requested ON
    -> do not send another START

Actual STARTING + Requested OFF
    -> request safe stop behavior according to confirmed protocol behavior

Actual RUNNING + Requested ON
    -> supervise; update temperature/session parameters

Actual RUNNING + Requested OFF
    -> send SHUTDOWN once

Actual SHUTTING_DOWN
    -> wait for OFF; no immediate restart

Actual UNKNOWN
    -> no new START; recover communication first
```

Implementation should remember recently sent control commands and use sensible retry/timeout rules to prevent command storms.

## 9. Boot sequence

Recommended boot order:

```text
1. Minimal boot / watchdog setup
2. Load board configuration
3. Load persistent application configuration
4. Start event logger
5. Start RTC
6. Start sensor manager
7. Start UART transport
8. Start Autoterm protocol service
9. Start HeaterController in UNSYNCHRONIZED state
10. Start AP / network manager
11. Start API/web service
12. Start scheduler after valid clock is available
13. Perform heater initialization/status synchronization
14. Enter normal operation
```

The web UI may be available while heater synchronization is still in progress, but it must clearly show that the heater state is not yet known.

## 10. Cold-boot safety

Persistent configuration must not contain an authoritative `heater_on=True` instruction that can blindly restart the heater.

On cold boot:

```text
requested_on = False
```

The actual heater state is then discovered.

Timers may later create a new requested session.

## 11. Autoterm protocol layer

Responsibilities:

- named command constants
- frame building
- CRC
- frame validation
- frame parsing
- known payload interpretation
- preservation of unknown bytes
- conversion from application mode to protocol mode

The protocol layer should be as deterministic and side-effect free as possible so it can be tested under CPython.

## 12. UART transport

Responsibilities:

- configure 9600 8N1 hardware UART
- collect RX bytes without blocking
- buffer partial frames
- identify complete frames
- recover from malformed input
- deliver complete raw frames to `AutotermProtocol`
- send complete frames provided by `AutotermProtocol`
- forward TX/RX copies to protocol diagnostics

It should not know what `CMD_START` means.

## 13. Receive framing

Prefer frame-length-driven parsing based on the protocol header and payload length.

An inter-byte timeout may be retained as a recovery mechanism, but should not be the sole definition of message boundaries if frame length proves reliable.

Parser behavior:

```text
Search for 0xAA
Read fixed header
Read payload length
Calculate expected total frame length
Wait for complete frame
Validate structure/CRC where possible
Emit frame
Continue parsing remaining bytes
```

Malformed data should resynchronize at the next plausible frame marker.

## 14. TemperatureManager

Responsibilities:

- scan 1-Wire
- map ROM IDs to semantic roles
- poll sensors
- validate readings
- keep last valid value
- calculate reading age
- expose status: OK / STALE / FAILED / MISSING
- emit sensor-health events

The manager does not shut down the heater directly.

It reports health to `HeaterController`, which applies the safety policy.

## 15. Sensor-health timing

Conceptual states:

```text
Fresh reading
    |
    | no new valid reading > ~30 s
    v
STALE
    |
    | age > 5 min
    v
FAILED
```

For the active control sensor:

- STALE → last valid reading may still be used
- FAILED → HeaterController requests controlled shutdown

## 16. RTC and TimeService

The DS3231 is the authoritative persistent **UTC** clock. It stores no local
offset or daylight-saving interpretation. `TimeService` alone derives local
civil time from UTC plus an explicit timezone rule. The Phase-5 core supports
fixed offsets and a versioned embedded `Europe/Zurich` rule for 2000–2099;
there is no general IANA database on the ESP32.

For `Europe/Zurich`, CEST starts on the last Sunday in March at 01:00 UTC and
ends on the last Sunday in October at 01:00 UTC. The spring 02:00–02:59 gap is
never caught up. During the repeated autumn hour, `fold=0` is start-eligible
and `fold=1` is display/diagnostic-only and can never create a heater intent,
including after a reboot without an occurrence ledger. The exact effective-
offset transition minute is a conservative Scheduler fence. Natural seasonal
changes do not increment clock, timezone or UTC revisions; Scheduler validates
and fences the effective offset and `is_dst` mapping independently. Explicit
timezone configuration changes remain revisioned fences. The canonical name
`Europe/Zurich` is reserved for this rule and CET standard offset.

DS3231 adapter, RTC bridge and `TimeService` responsibilities:

- read/write DS3231
- expose current local date/time
- sync from NTP when internet is available
- accept browser/device time correction
- record last synchronization source/time
- report RTC health

The scheduler uses this service rather than directly calling hardware RTC
APIs. The hardware-independent DS3231 register adapter validates BCD/calendar
fields, oscillator trust and canonical readback. A cooperative RTC bridge owns
read/write cadence and revision-bound correction acknowledgement. It stages a
canonical RTC value behind a durable trust marker, locks the corresponding UTC
revision against re-entrant Python corrections, and only then releases RTC
trust. Neither the scheduler nor `TimeService` opens I2C.

## 17. Scheduler

Responsibilities:

- store multiple timer definitions
- calculate next occurrence
- evaluate weekday/time match
- create a short-lived, hardware-free `StartIntent` when triggered
- track occurrence consumption, authorization, completion and override state
- mark a concrete timer occurrence as manually overridden
- avoid re-triggering the same occurrence repeatedly

The scheduler must never write UART.

The synchronous `SchedulerControllerGateway` is the only Phase-5 handoff from
an authorized `StartIntent` to `HeaterController` Requested State. It performs
authorization, Requested-State mutation, exact truth verification and
completion in one stack without yielding. It never calls `HeaterController.step`
and therefore never transmits protocol bytes. `HeaterController` stores the
intent's monotonic deadline and remains the sole authority for session runtime
and the eventual controlled shutdown. Manual stop commits Requested OFF before
the gateway records the occurrence override; ordinary OFF completion is kept
separate from an override.

## 18. Timer model

Suggested logical model:

```python
{
    "id": "uuid-or-small-id",
    "name": "Weekdays",
    "enabled": True,
    "weekdays": [0,1,2,3,4],
    "start": "06:30",
    "mode": "roof_tent",
    "target_temperature": 20,
    "power_level": None,
    "runtime_minutes": 60
}
```

## 19. Configuration storage

`ConfigManager` is the single application persistence interface. Phase 6 uses
two separate persistence domains: rarely changed static configuration and a
small Scheduler safety ledger. Both use `AtomicJSONConfigStore`, but they have
independent generations and faults.

Implemented static schema v2 (schema v1 is accepted only through the fixed,
fail-closed v1-to-v2 migration):

```json
{
  "schema_version": 2,
  "system": {"setup_complete": false},
  "heater": {
    "maximum_runtime_minutes": 120,
    "quick_start": {
      "mode": "roof_tent",
      "target_temperature": 20,
      "power_level": null,
      "runtime_minutes": 60
    }
  },
  "sensors": {
    "assignments": {"roof_tent": null, "cabin": null, "outside": null},
    "stale_after_ms": 30000,
    "failed_after_ms": 300000
  },
  "time": {
    "timezone_name": "Europe/Zurich",
    "timezone_rule": "europe_zurich",
    "timezone_rule_version": 1,
    "standard_utc_offset_minutes": 60
  },
  "timers": [],
  "network": {
    "hostname": "heater",
    "access_point": {
      "ssid": "Landy Heater",
      "password": null
    },
    "known_networks": []
  }
}
```

`password: null` is deliberately unprovisioned and cannot activate the radio.
There is no firmware-wide default AP password. A privileged configuration view
is available only to the runtime composition boundary; public snapshots expose
`password_configured` booleans and never return either AP or station secrets.
The static schema supports at most 32 timers and eight ordered station profiles
under one target-proven aggregate canonical-size limit of 8 KiB. The complete
static A/B record, including envelope fields, is bounded to 12 KiB. The
MicroPython store streams record I/O and canonical validation in bounded
segments so the target does not require a second payload-sized buffer.

The Scheduler ledger stores only schema version, a global consumed local-minute
high-water and bounded terminal `consumed`/`overridden` latches. It never stores
active authorization, Requested/Actual State, sessions, monotonic deadlines or
an armed Scheduler.

Storage invariants:

- canonical, key-sorted UTF-8 JSON with no floats or custom values
- two equal A/B slots plus a non-bootable temp file per domain
- generation, bounded payload length, CRC32 and repeated commit footer
- validation, complete encoding and allocation before publication
- flush/sync, staged readback, publish, sync and final readback
- the previously newest valid slot remains untouched until its replacement is
  fully staged
- a single slot, generation gap, equal-generation split brain, invalid newer
  slot or durability-unknown result keeps automatic timer start disabled
- initial provisioning writes both generations, with the Scheduler ledger
  provisioned before any setup-complete configuration
- a durable consumed checkpoint is verified before Gateway authorization and
  Requested ON
- explicit recovery binds inspection to reseal, preserves the highest valid
  Scheduler high-water and never creates a start request
- no write occurs for a semantic no-op and no telemetry is persisted
- migration support for future schema versions
- the `time` section stores timezone name, timezone rule, expected embedded
  rule version and `standard_utc_offset_minutes` atomically
- the seasonally effective offset is derived runtime state and is never
  persisted in place of the standard offset
- unknown/mismatched timezone rules or versions fail closed until an explicit
  configuration migration is available

## 20. Event logger

Use a bounded ring buffer, approximately 200 events.

Each event should contain:

- timestamp
- severity
- category
- event code
- concise message
- optional structured data

Example:

```json
{
  "ts": "2026-08-09T06:30:00",
  "severity": "info",
  "category": "heater",
  "code": "session_started",
  "data": {
    "source": "timer",
    "mode": "roof_tent",
    "target": 20
  }
}
```

## 21. Protocol diagnostics

Keep protocol logging separate from event history.

Use a bounded in-memory buffer for recent frames.

Frame record:

```json
{
  "time_ms": 12345678,
  "direction": "rx",
  "raw_hex": "aa04...",
  "length": 27,
  "command": 15,
  "crc_valid": true
}
```

Unknown fields must be preserved.

## 22. Protocol capture mode

Advanced Diagnostics can start a named capture.

A capture should contain:

- metadata
- start/end time
- relevant current configuration
- every recorded RX/TX frame during the capture
- parsed known fields
- unknown raw bytes
- state changes

A capture can later be exported for reverse engineering.

## 23. Live diagnostics transport

The browser live log must not introduce heavy dependencies.

Preferred options:

1. Server-Sent Events if lightweight and stable in the chosen MicroPython server
2. low-frequency HTTP polling
3. WebSocket only if the selected server implementation proves reliable and memory-efficient

The heater controller must never wait for a diagnostics client.

Phase 11 selects low-frequency HTTP polling for the first product version.
The browser requests one small combined diagnostics/event/protocol page every
two seconds, only while the diagnostics view is visible.
The view fragment itself is loaded lazily. No request can poll UART: one cold
`DiagnosticsHub` drains copies from the existing bounded event sources and
transport activity queue in a separate cooperative step. The hub keeps at
most 200 events, 64 recent protocol records and 128 capture references by
default. REST pages are capped at 16 events or four protocol/capture records.

Capture control is AP-only and uses the existing Origin/CSRF boundary. A
capture is explicitly named, is RAM-only, and records both state events and
raw protocol copies until stopped or its fixed capacity is exhausted. An
overflow marks the export incomplete instead of allocating more memory.
Diagnostic, event and capture exports are JSON; the browser can additionally
serialize a capture as line-oriented NDJSON. Credential-shaped keys and
unbounded driver messages are removed before a record enters any buffer.

## 24. NetworkManager

Responsibilities:

- start AP `Landy Heater`
- keep AP available
- connect to known station networks
- handle reconnection
- expose station/AP status
- expose IP addresses
- start/support mDNS `heater.local`
- report whether internet is likely available
- disable the ESP32 driver's unbounded station retries and own bounded,
  wrap-safe profile rotation/backoff
- supervise the AP independently and fairly while station work is pending
- keep credential-bearing driver errors out of exceptions and diagnostics
- close both singleton WLAN interfaces explicitly and verify them inactive

It must not be required for heater control.

`NetworkManager` is hardware-free and performs at most one bounded port action
per cooperative `step()`. The MicroPython WLAN port is imported lazily behind
the board lock, applies country `CH`, uses WPA2 with at most four AP clients and
never scans implicitly. A generation-bound composition object stages trusted
ConfigManager data without starting the radio. The normal `boot.py`/`main.py`
path remains passive until the later product composition milestone.

MicroPython v1.28's built-in ESP32 mDNS responder is only initialized by a
successful station-IP event. Consequently `heater.local` is reported ready
only with a confirmed STA IP and configured hostname. AP-only access remains
fully supported through the validated direct AP IPv4 address; mDNS failure is
degraded diagnostics, never an AP prerequisite.

Phase 10.1 adds a cold `ConfiguredDiscoveryRuntime`. It combines one explicit
AP TCP/80 listener, an optional explicit station TCP/80 listener after DHCP,
and one AP-bound UDP/53 captive DNS socket. It alternates their cooperative
steps fairly. Each listener carries an immutable ingress label supplied by
composition; classification is never taken from `Host`, `Origin`, a peer
subnet or accepted-socket introspection.

The original wildcard-listener design was rejected by real DFR0975-U evidence.
Although DHCP, mDNS and captive DNS worked, all HTTP clients were rejected
because MicroPython v1.28's ESP32 socket objects do not expose `getsockname()`.
Two concrete binds on the same user-visible port avoid that unavailable API
and preserve a trustworthy AP/station security boundary. A station-address
change requires the later product supervisor to replace only the station-side
listener without treating mDNS or STA health as an AP prerequisite.

The AP ingress retains the existing CSRF/Origin/ETag mutation authority. The
station ingress accepts allowlisted `heater.local` and its actual local
destination IP for reads, but always rejects mutation-token retrieval and all
mutations. Known captive-probe paths are redirected to the direct AP URL only
when they arrive through the AP. Captive DNS performs no forwarding and keeps
no query-name history. All discovery adapters remain inert until explicit
`start()` and own no WLAN or heater capability.

## 25. REST API layer

Phase 8 implements one versioned, hardware-free application boundary under
`/api/v1`:

```text
GET    /api/v1/security-context
GET    /api/v1/status
GET    /api/v1/diagnostics
GET    /api/v1/events?after=...&limit=...
GET    /api/v1/protocol-log?after=...&limit=...
GET    /api/v1/capture
POST   /api/v1/capture
DELETE /api/v1/capture
GET    /api/v1/capture/export?offset=...&limit=...
POST   /api/v1/heater/start
POST   /api/v1/heater/quick-start
POST   /api/v1/heater/stop
GET    /api/v1/settings
PATCH  /api/v1/settings
GET    /api/v1/timers?offset=...&limit=...
POST   /api/v1/timers
GET    /api/v1/timers/{resource-id}
PUT    /api/v1/timers/{resource-id}
DELETE /api/v1/timers/{resource-id}
```

The event, protocol and capture routes shown above are Phase-11 additions.
Their reads are bounded and cursor/page based; capture mutations remain
AP-only. Arbitrary valid timer IDs are exposed through an unambiguous canonical
UTF-8-hex resource form. A timer page contains at most eight entries. Session
PATCH was added in Phase 9, while events, live protocol logs and exports were
added in Phase 11. They are later, documented extensions rather than hidden
Phase-8 scope.

The listener security model is local but explicit:

- construction is inert; `ConfiguredRestRuntime.start()` alone asks the
  injected random provider for 32 bytes and owns the resulting boot-ephemeral
  CSRF token until `deinit()` erases it;
- all reads validate `Host` and, when supplied, same-origin `Origin`;
- every mutation validates allowed `Host`, exact `Origin` and
  `X-Landy-CSRF`;
- mutation authority exists only for an AP-bound security policy; an optional
  STA-bound listener is read-only regardless of request headers;
- public DTOs are explicit allowlists and never expose Wi-Fi passwords, the
  token in diagnostics, UART bytes or protocol internals.

Configuration and control races are closed at the application boundary.
Settings and timer writes require `If-Match` for the current configuration
generation, build and validate a complete candidate, commit it through
`ConfigurationAPIGateway`, then read the durable result back. Start and Quick
Start also require that generation plus the caller's expected Requested-State
revision. Stop deliberately requires neither generation nor a JSON body and
bypasses all rate quotas, but still requires Host, Origin and CSRF. It is
delegated through `ManualControlGateway` to the existing
`SchedulerControllerGateway`; neither successful requests nor error recovery
may call a UART, protocol service or heater-hardware method directly.

The boundary is strictly finite. `strict_json` limits input/output bytes,
depth, node count and string length. `http_protocol` accepts one complete
HTTP/1.1 request with CRLF and `Content-Length`, bounded request line, target,
header count/block and a 4096-byte body. Transfer-Encoding/chunking,
pipelining, upgrades, content encoding and method overrides are rejected. The
MicroPython socket adapter is inert until explicitly started on a validated AP
IPv4 address, owns at most two clients with backlog two, and performs at most
one bounded accept, receive or send action per cooperative `step()`; receive
and send budgets are 256 bytes.

`RestRateLimiter` keeps at most four canonical IPv4 peers. Each peer gets ten
requests per ten seconds, two mutations per second and a five-second cooldown
after a confirmed configuration commit. The exact bodyless Stop route bypasses
all quotas and remains usable even if rate-limit bookkeeping is faulted.

A committed mutation and delivery of its HTTP response are separate facts. If
response encoding or the socket fails after the application commit, the server
closes the connection without manufacturing a contradictory success or 500
acknowledgement and without an unsafe rollback. The client must read back
status/resource truth and may repeat only idempotently. Failures detected by
`RestApplication` before it has produced a complete successful Start response
request Requested OFF synchronously.

On the constrained ESP32, lifecycle order is also an architectural safety
boundary. The real access point must be configured and verified first; only
then may the HTTP parser/JSON/server closure be imported lazily and bound to
the AP address. An eager Wi-Fi-plus-HTTP import left approximately 48,112 bytes
but could not complete AP configuration. AP-first lazy loading first allowed
the minimal test closure to reach a real simultaneous AP-and-HTTP-ready state.
After the 40 project modules were frozen into the custom firmware, the same
minimal lifecycle also observed one real phone peer, accepted one allowlisted
request, completely wrote its response and passed ordered HTTP/Wi-Fi cleanup.
Its measured heap points remained between 75,072 and 102,400 bytes.

That result is not product acceptance. It used the production NetworkManager,
MicroPython Wi-Fi port and `MicroPythonHTTPServer`, but a fixed read-only
radio-check handler rather than `RestApplication`, `ConfigManager` and
configuration storage. It also did not take the separately required
post-response heap sample before cleanup. Before the frozen build, the planned
Configuration + Storage + REST closure alone accounted for approximately
155.9 KiB of dynamically loaded compiled code before NetworkManager,
heater/protocol modules and live objects. Freezing 40 project modules changes
that premise: the historical estimate no longer proves that the post-frozen
composition cannot fit. The P1 product acceptance remains open until one
combined run demonstrates at least 32 KiB free heap throughout import, AP
start, HTTP start, request handling and cleanup. The successful narrow frozen
run is recorded in
`captures/2026-08-11-phase8-frozen-phone-http-esp32-smoke.md`; the earlier
eager-import and ready-only failures remain historical evidence in
`captures/2026-08-11-phase8-wifi-http-capacity-blocked.md`.

The normal `boot.py`/`main.py` path remains passive. REST composition does not
start Wi-Fi, generate a token, bind a socket or create a heater/protocol port;
the future product composition must perform and unwind these lifecycle steps
explicitly.

## 26. Web architecture

Keep the UI static and lightweight:

- HTML
- CSS
- vanilla JS
- local translation dictionaries
- REST calls
- optional SSE/polling for live status

No React/Vue/Angular required.

No external dependencies are required for normal operation.

## 27. Web information architecture

### Home

Primary operational view.

### Timers

List/edit multiple timers.

### Settings

Subsections:

- Heater
- Network
- Temperature Sensors
- Timers & Runtime
- Date & Time
- System
- Diagnostics

### Event History

May be presented as a Settings/Diagnostics subpage rather than a permanent main-navigation tab.

## 28. UI state handling

The browser should render application state, not infer heater truth from the last button press.

For example, after pressing Stop:

```text
Requested: OFF
Actual: SHUTTING_DOWN
```

The UI shows `Shutting Down`, not immediately `Off`.

## 29. Internationalization

A translation-key model is preferred.

Example:

```json
{
  "home.quickStart": "Quick Start",
  "heater.state.running": "Running"
}
```

Application logic and CSS must not depend on specific translated text.

## 30. Watchdog strategy

Feed the watchdog from a central health mechanism only when critical tasks are making progress.

Avoid a design where every task blindly feeds the watchdog, because that can hide a stalled heater-control task.

At minimum monitor progress of:

- main event loop
- HeaterController
- UART receive/control cycle

## 31. Failure containment

Failures should be contained by layer.

Examples:

- UI JS error → no heater-control effect
- station Wi-Fi loss → AP and heater continue
- NTP failure → DS3231 and timers continue
- protocol-log client disconnect → no effect
- REST response/socket loss after a mutation → close, then read back and retry
  only idempotently; never infer rollback from the missing response
- REST traffic pressure → bounded peers/clients; authenticated Stop remains
  outside rate quotas
- combined product closure exceeds target capacity → abort before product
  activation, keep `main.py` passive and do not treat separate Wi-Fi/REST
  passes as joint acceptance
- one non-active DS18B20 fails → warning only
- active control DS18B20 fails > 5 min → controlled shutdown request
- heater communication fails → state UNKNOWN, no new start

## 32. Testing architecture

Keep hardware-independent logic isolated.

CPython-testable:

- CRC
- frame builder
- parser
- state-machine decision logic
- TimeService calendar, revision and clock-trust logic
- DS3231 register validation and the RTC bridge handshake
- timer occurrence, intent and manual-override logic
- synchronous SchedulerControllerGateway truth reconciliation
- config validation
- strict bounded JSON encode/decode and HTTP/1.1 parsing/encoding
- REST Host/Origin/CSRF ingress policy and ephemeral-token lifecycle
- REST per-peer quotas, configuration cooldown and unconditional Stop bypass
- REST routing, generation/revision fences and secret-free DTOs
- cooperative two-client socket lifecycle, deadlines and one-action steps
- AP-first lazy HTTP import on the real target, with free-heap checks before
  and after AP setup, HTTP import/bind, request completion and cleanup
- one combined production-closure run proving a real AP peer and completely
  written HTTP response; component-only Wi-Fi and fake-socket passes are not a
  substitute

Hardware adapters can be mocked.

This allows development and regression testing before connecting the real heater.

## 33. Future Energy module

A future Votronic integration should fit beside Heater rather than inside it.

Conceptual future extension:

```text
Application
├── HeaterService
├── EnergyService
│   ├── VotronicBattery
│   └── VotronicB2B
├── Scheduler
└── Web/API
```

The future UI may evolve from `Landy Heater` into a broader Landy Control dashboard without replacing the heater architecture.

## 34. Implementation sequence

Recommended dependency order:

```text
Protocol specification/tests
        ↓
CRC + frame builders + parser
        ↓
UART transport
        ↓
HeaterController
        ↓
TemperatureManager
        ↓
RTC/TimeService + Scheduler
        ↓
ConfigManager
        ↓
NetworkManager
        ↓
REST API
        ↓
Web UI
        ↓
Setup Assistant
        ↓
Diagnostics / protocol capture
        ↓
Watchdog / hardening / integration tests
```

No later phase should be allowed to bypass the responsibility boundaries established above.

### Phase-10 setup boundary

The Setup Assistant is a client-side nine-step transaction over a dedicated
configuration boundary. `GET /api/v1/setup` projects public configuration and
already-held runtime observations only; it never initiates radio, 1-Wire, I2C
or UART work. `PUT /api/v1/setup` accepts the complete candidate and commits it
once through `ConfigurationAPIGateway`, with the same generation fence,
scheduler disarm and verified readback as every durable settings mutation.

Network secrets use write-only actions (`keep`, `replace`, and, for station
profiles only, `open`). Only the privileged gateway may resolve `keep` against
the secret snapshot. The general settings endpoint remains unable to mutate
network data. Hardware observations are not durable acceptance evidence:
`reviewed` and `deferred` are setup acknowledgements, while real peripheral
acceptance remains under the Phase-13 electrical and target gates.

The REST components are implemented, and the isolated Phase-8 USB-only smoke
passed on the DFR0654 with four bounded iterations before its closure was
removed. On the frozen candidate, a minimal AP-first/lazy-HTTP run also served
one valid request from a real phone and passed cleanup. Because that runner
used a fixed probe handler instead of the complete application/configuration
closure, it was not the full P1 product target acceptance. Product
`boot.py`/`main.py` stayed passive.

The active full-product acceptance harness now uses exactly one HTTP listener.
Its first stage starts the production AP and confirms direct address plus one
associated client without importing HTTP or opening a socket. The complete
product closure then binds one production server to `192.168.4.1:80`; proof
and the fail-closed request gate are composed after raw bind and before
`listen`/`accept`. One deliberate `GET /api/v1/status` is the only IPv4/TCP
and product-response proof. There is no separate port-8080 reachability
listener, browser redirect, Refresh flow or diagnostic HTTP response, and the
real RestApplication response is not modified by the observer.

The latest DFR0654 run reached the single-listener path but retained only
32,880 bytes at the proof-before-listen checkpoint and fell below the required
32 KiB at the following checkpoint before READY. This is not a Phase-8 pass.
The selected DFR0975-U N16R8 successor now has a separate fail-closed board
profile and reproducibly verified ESP32-S3/Octal-PSRAM artifact set. Its
authorized complete first flash, passive MicroPython identity and separate
idle GC/PSRAM/internal/internal-DMA gates passed. No classic-ESP32 image or pin
assumption transfers. Manual physical-button ROM recovery and isolated VFS/A-B
storage passed; unattended USB control-line recovery is not reliable. Radio
association and DHCP subsequently passed in a separate no-listener gate with
complete cleanup. The subsequent single DFR0975-U full-product run served and
validated one real `GET /api/v1/status` through the sole port-80 listener,
kept all ten specified GC-heap checkpoints at or above 32 KiB, preserved
product storage and passed ordered HTTP/REST/radio cleanup. Phase-8 target
acceptance is complete and Phase 9 is released, while automatic startup and
all electrical peripheral approvals remain closed. A numeric internal/DMA
sample under a broader sustained workload remains a separate robustness gate.
The bounded state and migration boundary are recorded in
`captures/2026-08-30-phase8-single-listener-project-state.md` and
`DFR0975U_MIGRATION.md`; the successful target evidence is in
`captures/2026-09-01-dfr0975u-phase8-full-rest-gate.md`.
