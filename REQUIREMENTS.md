# Landy Heater — Requirements Specification

**Version:** 1.1  
**Status:** Phase-8 single-listener full-product target acceptance passed on
DFR0975-U; Phase 9 released but not started; automatic startup disabled

**Target:** ESP32 + MicroPython  
**Project:** Migration of the existing Raspberry Pi / Node-RED Autoterm/Planar heater controller

## 1. Purpose

The project replaces the existing Raspberry Pi / Node-RED implementation with a smaller, faster-booting, lower-power and robust ESP32-based controller written in MicroPython.

The ESP32 shall completely replace the original Autoterm control panel and the current Raspberry Pi controller.

The first release focuses on heater control, three temperature sensors, timers, local networking, a local web interface, diagnostics and protocol capture. The architecture must remain extensible for future Votronic battery-computer and B2B-charger integrations.

## 2. Design priorities

Priorities, in order:

1. Safe and predictable heater control
2. Protocol compatibility with the working Node-RED implementation
3. Robust unattended operation
4. Complete offline operation
5. Understandable Python code
6. Modular extensibility
7. Good mobile user experience
8. Low power consumption where it does not compromise availability
9. Diagnostics and maintainability

## 3. Target platform

- Language: MicroPython
- Hardware family: ESP32
- Validated prototype: DFRobot DFR0654 / classic ESP32, 4 MB flash, no PSRAM
- Selected successor under target validation: DFRobot DFR0975-U V1.0 with
  ESP32-S3-WROOM-1U-N16R8, 16 MB flash and 8 MB Octal PSRAM
- Its exact USB identity, fail-closed board profile, reproducible firmware,
  authorized first flash, passive boot, idle PSRAM/internal-memory, manual
  recovery, isolated VFS/A-B-storage, bounded WLAN/DHCP and Phase-8
  full-product target gates are verified; automatic USB control-line recovery
  remains unreliable
- DFR0975-U N16R8 is the selected continued-integration target; its S3
  UART/level interface and product peripherals still require Phase-13 gates
- Hardware-specific pins and peripheral IDs must therefore be isolated in `board_config.py`
- One UART is required for the heater in version 1
- One 1-Wire bus is required for three DS18B20 sensors
- One I2C bus is required for the DS3231 RTC

The implementation must not depend on one specific ESP32 board unless unavoidable.

## 4. Electrical integration

### 4.1 Heater

The heater is powered directly from the vehicle 12 V supply.

The Autoterm communication line currently uses a 5 V to 3.3 V level shifter before reaching GPIO-level UART. The ESP32 shall reuse the same electrical concept.

The software assumes 3.3 V UART levels on the ESP32 side.

### 4.2 ESP32 power

The ESP32 shall be powered from the vehicle 12 V system through a suitable DC/DC converter.

Version 1 keeps Wi-Fi continuously available, therefore aggressive deep-sleep operation is not required initially.

## 5. Scope of version 1

Version 1 shall include:

- Autoterm/Planar heater control
- Roof-tent temperature
- Cabin temperature
- Outside temperature
- Three DS18B20 sensors
- Multiple timers
- DS3231 RTC
- NTP time sync when internet is available
- Browser/device time sync as additional clock source
- Persistent configuration
- Wi-Fi access point
- Wi-Fi client mode for known networks
- Multiple saved Wi-Fi networks
- mDNS access via `heater.local`
- Local REST API
- Mobile-first local web interface
- Setup assistant
- Event history
- Diagnostics
- Raw UART protocol diagnostics
- Live protocol log
- Protocol-capture export
- Event-log export
- Watchdog/recovery handling
- Unit tests for pure-Python protocol and state-machine components

Not included in version 1:

- Votronic battery computer
- Votronic B2B charger
- MQTT
- Cloud or internet remote access
- Web-based firmware update
- Separate web authentication beyond WPA-protected Wi-Fi
- Pure ventilation mode as a user-facing feature
- Battery undervoltage logic in the ESP32

## 6. Heater control modes

The user-facing application shall support three modes.

### 6.1 Roof Tent Temperature

- Uses the roof-tent DS18B20 as the external control temperature
- Target range: 5–30 °C
- Sends the external temperature to the heater using the protocol behavior derived from the Node-RED implementation

### 6.2 Cabin Temperature

- Uses the cabin DS18B20 as the external control temperature
- Target range: 5–30 °C
- Otherwise behaves like Roof Tent Temperature mode

### 6.3 Power

- Nine power levels
- Valid values: 1–9
- Web UI shall mark the ends as `Low` and `High`

The existing Node-RED application-level values such as `21`, `22` and `04` must not be spread through the new code as magic strings. The new application shall use named modes and map them to protocol values centrally.

## 7. Ventilation

The heater can potentially be used in ventilation-only mode.

This feature is not currently used and shall not be exposed in version 1.

The software architecture shall allow a future `VENTILATION` mode to be added without redesigning the heater controller.

## 8. Requested state vs. actual state

A strict separation between desired state and actual heater state is mandatory.

### 8.1 Requested state

Examples:

- requested_on
- requested_mode
- requested_target_temperature
- requested_power_level
- requested_runtime
- requested_source (manual, quick start, timer)

### 8.2 Actual state

Examples:

- communication status
- initialization status
- heater state
- voltage
- glow-plug raw status
- fan/vent raw status
- last successful status time
- current active session

### 8.3 Control ownership

The following components may only change the requested/application state:

- Web interface
- REST API
- Timer scheduler
- Quick Start
- Setup/configuration logic

Only `HeaterController` may decide whether a UART command is actually sent.

No web endpoint, timer, UI handler or other service may directly send heater UART frames.

## 9. Heater state handling

Known states from the existing implementation:

- 0 — Off
- 1 — Starting
- 4 — Running
- 5 — Shutting Down
- 6 — Temperature Monitoring
- Unknown — any unrecognized or unavailable state

The implementation must avoid duplicate or contradictory commands.

Examples:

- Actual OFF + Requested ON → Start may be sent
- Actual STARTING + Requested ON → no duplicate Start
- Actual RUNNING + Requested OFF → Shutdown may be sent
- Actual SHUTTING_DOWN → no immediate restart
- Actual UNKNOWN → no new Start command

## 10. Initialization and status synchronization

After ESP32 startup:

1. Initialize local services
2. Initialize UART
3. Start Autoterm initialization sequence
4. Obtain actual heater status
5. Synchronize internal actual state
6. Only after synchronization may requested state be evaluated for control actions

The controller must never send an unconditional Start or Stop immediately after boot.

## 11. Restart and cold-boot behavior

### 11.1 ESP32 reboot while the heater is still running

The controller must first determine the actual heater state.

If a valid active session can safely be reconstructed, it may continue supervising it.

No control command may be sent until the actual state is known.

### 11.2 Full power loss / cold boot

A previously stored ON state must not automatically restart the heater.

Default requested state after a cold boot:

`OFF`

A currently valid timer may create a new start request after normal synchronization.

## 12. Communication loss

If repeated heater status requests fail:

- communication state becomes ERROR
- actual heater state becomes UNKNOWN
- no new Start command may be issued
- the controller continues attempts to re-establish communication
- a visible warning must be shown
- the event must be logged

Do not blindly send Shutdown while communication is already known to be unavailable.

After communication returns:

1. obtain fresh actual status
2. synchronize state
3. only then compare requested vs. actual state again

## 13. Temperature sensors

Three physical DS18B20 sensors are used:

- Roof Tent
- Cabin
- Outside

Each sensor shall be identified by its unique 1-Wire ROM ID.

ROM IDs shall be stored persistently and not hard-coded in application logic.

## 14. Sensor setup

The setup assistant shall:

1. scan the 1-Wire bus
2. list all detected ROM IDs
3. show live temperature for each sensor
4. let the user assign each sensor to Roof Tent, Cabin or Outside
5. save the mapping persistently

The user should be able to warm a sensor by hand to identify it.

Sensor assignments must later be editable under Settings.

## 15. Sensor-health policy

A bad sensor value must never be replaced with an artificial `0 °C` value.

For an active regulation sensor:

- after approximately 30 seconds without a fresh valid reading → sensor status becomes STALE and a warning is shown
- the last valid reading may continue to be sent temporarily
- after 5 minutes without a valid reading → request controlled heater shutdown
- show the error in the UI
- add an event-history entry

If the failed sensor is not the active regulation sensor, it shall be reported as failed but shall not automatically stop the heater unless future safety rules require it.

## 16. External temperature reporting

The initial ESP32 implementation shall preserve the external-temperature reporting behavior of the working Node-RED flow as closely as practical.

The Node-RED flow effectively reports the selected external temperature approximately once per second in the relevant active temperature-control states.

The reporting interval shall be an internal named constant so it can be adjusted after real-world testing.

## 17. RTC and time sources

A DS3231 hardware RTC is required.

Clock-source strategy:

1. DS3231 is the reliable offline UTC time base
2. NTP may correct the RTC when internet is available
3. the web browser/device may provide time to correct the RTC
4. timer operation must not require internet

The DS3231 shall store UTC only. Local civil time shall be derived in
`TimeService`; timer and UI code must not reinterpret RTC register values as
local time.

Version 1 shall support fixed UTC offsets and the global vehicle timezone
`Europe/Zurich` without requiring a network timezone database. The canonical
name `Europe/Zurich` shall only be accepted with its versioned CET/CEST rule
and CET standard offset UTC+60 minutes. Its effective summer offset is UTC+120
minutes.

For `Europe/Zurich`, daylight saving time starts on the last Sunday in March
at 01:00 UTC and ends on the last Sunday in October at 01:00 UTC. Local times
02:00–02:59 in the spring gap shall never trigger and shall not be caught up.
In the repeated autumn hour, only the first occurrence (`fold=0`) may trigger;
the second occurrence (`fold=1`) shall never trigger, including after reboot.
The exact effective-offset transition minute shall be treated as a scheduler
fence and shall not start a heater occurrence.

Natural seasonal transitions shall not rewrite the RTC or change the UTC
correction revision. The Scheduler shall independently validate and fence the
effective offset/DST mapping. Browser/API time corrections shall use UTC. Any
future local-civil correction API must reject gap times and require an
explicit fold for ambiguous times.

## 18. Timers

The system must support multiple independent timers.

Each timer shall contain at least:

- unique ID
- optional name
- enabled/disabled
- weekdays
- start time
- control mode
- target temperature or power level
- runtime

Example:

- Monday–Friday
- 06:30
- Roof Tent
- 20 °C
- 60 min

The heater shall be shut down when the timer runtime expires.

## 19. Timer priority

Manual user intervention always wins.

If the user presses Stop during a timer-run session:

- stop the active session
- mark that concrete timer occurrence as manually overridden
- do not let the same timer occurrence immediately restart the heater

Future timer occurrences remain valid.

## 20. Runtime limits

A globally configurable maximum runtime is required.

It shall be adjustable through the web interface.

No timer or manual session may exceed the configured maximum.

## 21. Manual start

The configured-start UI shall offer at least:

- 30 min
- 60 min
- 90 min
- 120 min

These values are still capped by the configured global maximum runtime.

For Power mode, power level 1–9 must be selectable.

For temperature modes, target temperature 5–30 °C must be selectable.

## 22. Quick Start

Home shall prominently expose `Quick Start`.

Quick Start uses persistent defaults:

- default control mode
- default target temperature or power level
- default runtime

A secondary `Configure & Start` action opens the full start configuration.

## 23. Changes during an active session

During a running temperature-controlled session, the user may change:

- target temperature
- remaining runtime

The UI should provide a simple time extension such as `+15 min`.

Changing the complete operating mode while running is not required in version 1.

Power-level changes during an active Power session shall only be implemented if the protocol behavior is confirmed safe.

## 24. Stop behavior

`Stop Heater` does not require a confirmation dialog.

Pressing it immediately changes requested state to OFF.

The UI must continue to show the actual heater state, e.g. `Shutting Down`, until the heater reports that shutdown is complete.

## 25. Persistent configuration

Persist at least:

- setup-complete state
- AP configuration
- AP password
- known Wi-Fi networks
- default control mode
- default target temperature
- default power level
- default runtime
- maximum runtime
- sensor ROM assignments
- sensor stale/failure settings
- timers
- language
- date/time settings
- UI defaults

The persisted time configuration shall store the timezone name, timezone rule,
the expected embedded rule version and `standard_utc_offset_minutes` as one
validated unit. It shall not persist the seasonally effective snapshot offset
as if it were the standard offset. Loading shall fail closed if the rule is
unknown, the stored rule version is unsupported, or the canonical timezone
name/rule/standard-offset combination is inconsistent. A migration must be
explicitly defined before a changed embedded rule version is accepted.

Do not write high-frequency telemetry to flash.

Configuration changes should use a corruption-resistant strategy such as write-temp/rename or equivalent atomic replacement where feasible.

The Phase-6 storage baseline shall use two independently validated A/B
generations for static configuration and a separate bounded Scheduler safety
ledger. A record shall contain canonical UTF-8 JSON, bounded length, generation,
CRC32 and a repeated commit footer. The temporary file is never a boot
candidate. A new generation is accepted only after flush/sync, staged readback,
publication, a second sync and final readback.

A single surviving slot, a generation gap, divergent equal generations, an
invalid newer slot or an unresolved post-publication durability error shall
keep automatic timer start disabled. Initial provisioning shall create both
slots and shall establish the Scheduler ledger before a setup-complete static
configuration may become start-authoritative.

Before a scheduled start is authorized, its reduced consumed-occurrence state
shall be durably checkpointed and read back. The ledger shall persist only a
bounded high-water and terminal consumed/overridden latches; it shall never
persist Requested ON, Actual State, a session, an active authorization,
monotonic deadlines or Scheduler armed state. On reboot, incomplete in-flight
work remains consumed and shall never be replayed as a start command.

Recovery shall be explicit and fail closed. It shall bind the inspected A/B
view to the reseal operation, never lower a semantically valid Scheduler
high-water and never create or replay a heater request. Static recovery shall
remove timers and clear setup-complete until the user confirms and commits a
new complete configuration.

Phase 7 introduces configuration schema version 2 through one explicit,
fully validated v1-to-v2 migration. The v2 document adds exactly one
`network` section with the fixed hostname `heater`, the fixed AP SSID
`Landy Heater`, one device-specific WPA2 password and an ordered list of at
most eight known station profiles. A migration shall never invent a shared AP
password: migrated configurations remain setup-incomplete and network-start
ineligible until a local provisioning flow supplies a valid individual
secret and commits the complete v2 document. Passwords are privileged inputs
and shall never appear in public configuration snapshots, events, captures or
exception text.

The canonical static application document is additionally bounded as one
aggregate to 8 KiB before storage I/O; its complete static-store record is
bounded to 12 KiB including envelope fields. This bound shall be proven on the
target ESP32 with the maximum supported timer/profile counts; an oversized
legacy document shall require explicit fail-closed recovery rather than an
unsafe or partial migration. Phase-9 UI/language settings still require a
future explicit schema migration and shall not be added as unknown or silently
defaulted keys.

## 26. Networking

### 26.1 Access point

The ESP32 shall continuously provide:

SSID: `Landy Heater`

The AP password must be configurable from the web interface.

The delivered firmware shall contain no universal/default AP password and
shall not start an open or unprovisioned AP. Once a trusted configuration with
an individual password is active, the AP shall remain available independently
of station success or internet access.

No additional web username/password is required in version 1.

### 26.2 Station mode

The ESP32 shall concurrently attempt to connect to known Wi-Fi networks.

Requirements:

- save multiple known networks
- support at most eight ordered known networks
- reconnect automatically with bounded, wrap-safe retries owned by the
  application; the driver-level unlimited retry loop shall be disabled
- AP remains available whether station connection succeeds or not
- internet is optional
- a station error, retry or long backoff shall never postpone AP supervision

### 26.3 Offline operation

Heater control, timers, sensors, setup, REST API and web UI must remain functional without internet access.

### 26.4 mDNS

Preferred local URL:

`http://heater.local`

Direct IP access must also remain possible.

On the pinned MicroPython ESP32 port the built-in mDNS responder becomes ready
only after the station interface has obtained an IP address. Therefore
`heater.local` is best-effort and shall never be treated as AP health or as a
boot/start gate. In AP-only/offline operation the AP IPv4 address is the
authoritative recovery URL and must remain visible in status.

### 26.5 Captive portal and station web access

Joining the product AP should trigger the operating system's captive-portal
assistant where supported. This is best-effort: the authoritative fallback is
always `http://192.168.4.1/`.

The captive DNS responder shall bind only to the explicit AP address on UDP
port 53, shall not forward queries or retain queried names, and shall process
at most one bounded 512-byte datagram per cooperative step. Captive HTTP
responses shall be limited to known probe paths and AP ingress.

AP and station traffic shall share one TCP listener on port 80. The ingress
shall be derived from the accepted socket's local destination address, never
from an HTTP header or the peer subnet. Station access through
`http://heater.local` or the current station IP is read-only. Mutation
authority remains AP-only until a separate authenticated remote-access design
is approved.

## 27. REST API

The web UI shall use a local REST API.

### 27.1 Implemented Phase-8 resources

The implemented version namespace is `/api/v1`:

- `GET /api/v1/security-context`
- `GET /api/v1/status`
- `GET /api/v1/diagnostics`
- `POST /api/v1/heater/start`
- `POST /api/v1/heater/quick-start`
- `POST /api/v1/heater/stop`
- `GET /api/v1/settings`
- `PATCH /api/v1/settings`
- `GET /api/v1/timers`
- `POST /api/v1/timers`
- `GET /api/v1/timers/{resource-id}`
- `PUT /api/v1/timers/{resource-id}`
- `DELETE /api/v1/timers/{resource-id}`

Timer listing shall be bounded and paged, with at most eight timers returned in
one response. Resource paths shall address every timer ID accepted by the
persistent configuration schema without ambiguity.

Events, live protocol logs and export endpoints remain deliberately deferred
beyond Phase 8. Their eventual version-1 implementation remains in scope, but
they shall not weaken or bypass this boundary.

### 27.2 Mutation security and listener ingress

The REST construction path shall perform no random, network, socket, UART or
other hardware operation. Before serving requests, the owner shall explicitly
start its security lifecycle and generate a new 32-byte random CSRF token. The
token shall be ephemeral, shall be erased on `deinit()` and shall never appear
in ordinary status or diagnostics.

Reads shall accept only an allowlisted `Host`; an included `Origin` shall match
that host exactly. Every mutation shall require all of:

- an allowlisted `Host`;
- an exact same-origin `Origin` using HTTP and that host;
- the current 64-lowercase-hex-character token in `X-Landy-CSRF`.

Mutation authority shall be available only on the listener explicitly bound to
the access-point ingress. A listener composed for station ingress shall be
read-only regardless of supplied headers. Direct AP-IP access must remain
supported.

### 27.3 Concurrency and state fences

Every configuration mutation and each Start/Quick-Start operation shall carry
`If-Match` for the configuration generation last read by the client. A stale
or missing generation shall not commit. Start and Quick Start shall also carry
the expected Requested-State revision so a concurrent manual or timer action
cannot be overwritten.

Settings and timer writes shall construct and validate a complete candidate,
commit through the existing configuration manager and return the confirmed
generation. Public response objects shall use explicit allowlists and shall not
contain WLAN passwords, CSRF internals, raw protocol traffic or other secrets.

Stop shall require an empty body but neither `If-Match` nor a Requested-State
revision. It shall flow through the existing manual-control and
SchedulerControllerGateway path, request Requested OFF, and remain available
when application mutation bookkeeping is faulted. The HTTP/REST layer and all
its error paths shall never call UART, Autoterm protocol or heater-hardware
methods directly.

### 27.4 Bounded wire format and cooperative service

The JSON and HTTP boundary shall have fixed byte, string, depth, node, request
line, target, header and body limits suitable for the ESP32. Requests shall be
strict HTTP/1.1 with CRLF, one request per connection and `Content-Length` for
bodies. Chunked/transfer encoding, pipelining, upgrades, compressed request
bodies and method overrides shall be rejected.

The MicroPython socket adapter shall bind only to an explicitly supplied AP
IPv4 address, own at most two clients with a backlog of two and perform at most
one accept, receive or send action per cooperative `step()`. Receive and send
actions shall each be limited to 256 bytes and guarded by finite idle and
absolute deadlines.

### 27.5 Rate limits and recovery path

The application shall retain a fixed table of at most four canonical IPv4
peers. Per peer it shall allow at most ten requests in ten seconds and two
mutations in one second. A successful durable settings/timer change shall
start a five-second configuration-write cooldown.

The exact bodyless `POST /api/v1/heater/stop` route shall bypass request,
mutation and cooldown quotas. Host, Origin and CSRF validation remain mandatory
for it.

### 27.6 Commit and response-loss contract

An application mutation may be committed before the complete HTTP response is
delivered. If response encoding or sending fails after commit, the server shall
close the connection without inventing a success/500 acknowledgement and
without attempting an unverified rollback or direct UART action. The client
shall read the authoritative status/resource again and only then perform an
idempotent retry.

If the REST application itself encounters an error before completing a
successful Start response after changing Requested State to ON, it shall
synchronously request Requested OFF through the normal application gateway.

The implemented REST runtime and socket adapter shall remain inactive in the
normal product `boot.py` and `main.py` until a later explicitly approved
composition milestone.

### 27.7 Target composition and heap acceptance

On the ESP32 target the access-point path shall be loaded, configured and
verified before importing and starting the HTTP parser/JSON/socket closure.
HTTP shall be loaded lazily only after the AP has a confirmed direct IP. This
AP-first order is required, but is not alone sufficient for acceptance.

The Phase-8 full-product target acceptance shall use exactly one HTTP listener
bound to `192.168.4.1:80`. AP readiness and one associated client shall be
proven without a diagnostic HTTP listener. The single deliberate
`GET /api/v1/status` request shall provide the IPv4/TCP, routing,
RestApplication, encoded-wire and clean-close proof. Port 8080, redirects,
Refresh navigation and additional link-check requests are not acceptance
prerequisites.

At least 32 KiB free heap shall remain at every measured checkpoint: after the
intended product imports, after Wi-Fi factory construction, after AP readiness,
after client association, after configuration adoption, before HTTP start,
after proof composition but before listen, after HTTP bind/listen, after a
complete request/response, and after ordered cleanup. HTTP sockets shall close
before Wi-Fi owners. Both interfaces, the temporary approval and all leases
shall be inactive afterward.

Separate Wi-Fi-only and REST-with-fake-socket passes shall never be combined on
paper into a joint pass. One target run shall use the intended Configuration,
Storage, REST, NetworkManager and HTTP composition, observe a real AP peer and
fully write at least one HTTP response. No temporary WPA2 password, CSRF token
or other secret may appear in evidence.

On the custom frozen firmware, the AP-first minimal runner reached real
AP-and-HTTP READY, observed one phone peer, completed an allowlisted response
and passed ordered cleanup while every measured heap point remained above
32 KiB. That runner used a fixed read-only handler rather than
`RestApplication`, `ConfigManager` and configuration storage, and it did not
sample heap separately between the completed response and cleanup. Freezing
the project modules changes the premise of the earlier dynamic-import heap
failure, but does not by itself prove the full composition. Its P1 target
acceptance remained open at that point and did not enable `main.py` or release
Phase 9.

The subsequent single-listener DFR0654 run measured 32,880 bytes before listen
and fell below the same 32-KiB floor at the following checkpoint before READY.
It therefore remains a failed capacity measurement, not a product acceptance.

The subsequent single DFR0975-U run passed this exact acceptance in one AP
lifetime with one phone peer, one port-80 listener and one real
`GET /api/v1/status`. It proved a complete HTTP 200 JSON wire, all ten
specified >=32-KiB GC-heap checkpoints, unchanged production storage, no
heater/protocol activity and ordered HTTP/REST/radio/file cleanup. Evidence is
in `captures/2026-09-01-dfr0975u-phase8-full-rest-gate.md`. This releases
Phase 9 but does not enable automatic startup or any electrical peripheral.

### 27.8 Phase-9 session extension

Phase 9 adds `PATCH /api/v1/heater/session` for a same-mode target-temperature
change and/or an exact `+15 min` extension. It is subject to the Phase-8
mutation boundary, the current configuration generation and the exact
Requested-State revision. It may act only on a confirmed, non-expired session,
must respect the configured maximum runtime and must not perform protocol I/O.

## 28. Web interface

Requirements:

- entirely hosted on the ESP32
- no CDN dependencies
- no external webfonts
- HTML/CSS/vanilla JavaScript preferred
- mobile-first
- responsive desktop support
- system fonts
- automatic light/dark theme using device preference
- minimal, modern, low-clutter visual design
- large, readable status values
- touch-friendly controls

Main navigation in version 1:

- Home
- Timers
- Settings

## 29. Home screen

Home shall show:

- actual heater state
- roof-tent temperature
- cabin temperature
- outside temperature
- remaining runtime
- next timer
- current warnings/errors

When OFF:

- prominent `Quick Start`
- secondary `Configure & Start`

When active:

- mode
- target temperature or power level
- active-regulation sensor temperature
- remaining runtime
- `+15 min`
- target-temperature adjustment
- `Stop Heater`

Low-level details such as glow plug and fan raw values belong under Diagnostics, not Home.

## 30. Timers UI

Timers shall be shown as readable cards/list entries.

Each entry shows:

- name
- time
- weekdays
- mode
- target temperature or power
- runtime
- enabled state

Actions:

- Add
- Edit
- Enable/Disable
- Delete

## 31. Settings

At minimum:

- Heater
- Network
- Temperature Sensors
- Timers & Runtime
- Date & Time
- System
- Diagnostics

Heater settings include Quick Start defaults and maximum runtime.

## 32. Internationalization

The UI shall be internationalization-ready from the start.

UI strings must be centralized in translation dictionaries/files.

The setup assistant includes language selection.

The implementation shall support adding additional languages without changing application logic.

## 33. Setup assistant

On first boot, show a guided setup.

Recommended flow:

1. Language
2. Date/time and RTC status
3. Known Wi-Fi networks
4. `Landy Heater` AP password
5. DS18B20 discovery and role assignment
6. Autoterm UART connection test
7. Quick Start defaults
8. Summary
9. Complete setup

The wizard must be manually restartable from Settings.

## 34. Event history

Maintain a bounded ring buffer of approximately the last 200 meaningful events.

Examples:

- boot
- heater start requested
- heater started
- heater stop requested
- heater stopped
- timer triggered
- manual timer override
- sensor stale
- sensor failure
- heater communication lost
- heater communication restored
- Autoterm error
- configuration changed
- RTC synchronized

Do not persist one-second telemetry as event history.

## 35. Diagnostics

Normal diagnostics shall include:

- heater communication state
- last successful status timestamp
- heater state
- heater voltage
- glow-plug raw value
- fan/vent raw value
- each DS18B20 ROM ID and status
- sensor reading age
- RTC state/time
- AP state
- station state
- IP addresses
- uptime
- free memory

## 36. Advanced protocol diagnostics

Provide:

- raw TX/RX frames
- timestamp
- direction
- length
- command
- CRC result when known
- live log in the browser
- bounded in-memory log
- protocol-capture mode
- exportable capture file

Protocol diagnostics must never block the heater controller.

## 37. Protocol reverse-engineering support

The system shall help improve the partial protocol specification over time.

Useful diagnostic capabilities:

- frame comparison
- changed-byte highlighting between status responses
- capture labels such as `Cold start - Roof Tent - 20C`
- preservation of unknown payload bytes
- export for later analysis

## 38. Logging and export

Users shall be able to export:

- event history
- diagnostic snapshot
- protocol capture

Prefer JSON and/or plain text.

## 39. Watchdog and resilience

A watchdog shall be used appropriately.

Heater supervision must continue even when:

- no browser is connected
- internet is unavailable
- station Wi-Fi disappears
- NTP fails
- the web interface is unused
- a non-critical web/API task encounters an error

No optional service may be a prerequisite for heater state supervision.

## 40. Concurrency model

Prefer `uasyncio`.

Separate responsibilities into cooperative tasks such as:

- UART RX
- protocol processing
- heater controller
- sensor polling
- scheduler
- RTC synchronization
- networking
- API/web service
- event logging

Avoid busy loops and long blocking sleeps.

## 41. Extensibility

The architecture shall allow later addition of:

- Votronic battery computer
- Votronic B2B charger
- energy dashboard
- additional vehicle telemetry
- optional MQTT
- optional remote-access integration

Version 1 shall not contain unused Votronic logic.

## 42. Verification and testing

Pure Python components should be testable with CPython where practical.

Tests shall cover at least:

- CRC vectors
- frame builders
- parser validation
- invalid CRC
- invalid length
- partial frames
- multiple frames
- known/unknown commands
- state-machine transitions
- no duplicate Start
- no Start while actual state is UNKNOWN
- requested OFF behavior
- sensor stale < 5 min
- sensor failed > 5 min
- timer start/stop
- manual timer override
- maximum runtime
- strict JSON UTF-8, duplicate-key, depth, node and byte bounds
- strict HTTP line/header/body bounds and rejection of chunking/pipelining
- AP-only Host/Origin/CSRF mutation authorization and STA read-only behavior
- ephemeral CSRF lifecycle without secret-bearing diagnostics
- configuration-generation and Requested-State-revision conflicts
- secret-free status, diagnostics, settings and timer responses
- per-peer request/mutation quotas, config cooldown and Stop bypass
- cooperative HTTP lifecycle with at most two clients and one action per step
- response loss after commit without false acknowledgement or direct rollback

The Phase-8 REST paths shall additionally have a USB-only MicroPython component
smoke with an explicit confirmation string, exact final pass token and an
upload allowlist that excludes `boot.py`, `main.py`, `board_config.py`, the
MicroPython `network` module, `hardware/` and `protocol/`. That isolated test
passed and is recorded in `captures/2026-08-11-phase8-rest-esp32-smoke.md`.
Because it used fake sockets and imported no WLAN, it is REST component
evidence only.

A separate combined product target-capacity test shall enforce section 27.7.
The frozen AP-first runner now passed its narrower production Wi-Fi/HTTP probe
with a real phone, as recorded in
`captures/2026-08-11-phase8-frozen-phone-http-esp32-smoke.md`; it did not load
the required application/configuration/storage composition and is not that
acceptance. The preceding eager-import, ready-only and Wi-Fi-only measurements
remain historical evidence in
`captures/2026-08-11-phase8-wifi-http-capacity-blocked.md`. Host tests and the
narrow phone probe did not override the previously missing acceptance; the
later DFR0975-U single-listener run now supplies that full-product evidence in
`captures/2026-09-01-dfr0975u-phase8-full-rest-gate.md`.

## 43. Acceptance criteria

Version 1 is complete when all of the following work:

- Autoterm initialization
- status reading
- start
- shutdown
- power mode
- roof-tent temperature mode
- cabin temperature mode
- external temperature reporting
- three DS18B20 sensors
- sensor failure handling
- DS3231 RTC
- multiple timers
- manual override
- maximum runtime
- persistent configuration
- AP `Landy Heater`
- multiple known Wi-Fi networks
- offline operation
- `heater.local`
- REST API
- AP and production HTTP concurrently within the required heap margin, with a
  real AP peer, one completely delivered response and ordered cleanup
- responsive local web interface
- Quick Start
- setup assistant
- internationalization framework
- event history
- diagnostics
- live protocol log
- protocol export
- tests
- installation/documentation
