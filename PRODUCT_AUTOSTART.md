# Product autostart

## Scope

The DFR0975-U product candidate now owns the complete runtime automatically
after every normal reset. No USB-started Python runner is required. The frozen
entry point deliberately takes precedence over the retained passive VFS
`main.py`; the repository default remains a safe development profile.

## Startup order

1. Load and verify the production configuration and Scheduler safety ledger.
2. Construct the configured sensor, heater and network owners without I/O.
3. Start the three-sensor runtime.
4. Open the accepted UART path and create the controller requested OFF.
5. Start AP/STA management and wait at most 30 seconds for the fixed recovery
   AP address `192.168.4.1`.
6. Start REST security, the AP-bound port-80 UI and captive DNS.
7. Arm the Scheduler only after the recovery UI is reachable.
8. Enter one cooperative 10-ms product loop.

The AP listener remains active. When station DHCP and mDNS are ready, a
separate listener is attached to the exact station address, also on port 80.
If the station disconnects or its address changes, only that listener is
removed. `heater.local` remains the preferred home-network name and the direct
DHCP address is the fallback.

## Runtime safety

Each loop advances sensor acquisition, network state, Web discovery,
Scheduler/controller bridging, heater UART/controller state and diagnostics
once. Sensor, Scheduler or heater failures are safety-critical and leave the
normal loop through the controlled shutdown path. Optional WLAN, HTTP-listener
and diagnostics failures are marked degraded but do not stop heater
supervision.

A successful configuration mutation creates a restart fence immediately:
timers are disarmed, no further sensor/Scheduler step uses the old generation,
the heater controller continues so a running heater can shut down, and the UI
stays available. The user must then reset the board so all generation-bound
owners are rebuilt from the committed configuration.

Shutdown never abandons the UART while the controller still requests heat or
the heater is not confirmed `off`. It repeatedly requests/advances controlled
shutdown until the heater owner allows cleanup.

## Current time limitation

The replacement RTC is still absent. After every reset, timer starts remain
blocked until the Web UI supplies a fresh UTC browser-time sample and the
Scheduler has established its new baseline. This does not weaken the DS3231
requirement for the finished product.

## Acceptance state

Host tests and two reproducible firmware builds pass. The candidate still
needs a hash-bound app-only flash, complete readback and a USB-only/12-V-off
target gate before product autostart is accepted. Powered-heater operation is
not part of that first gate.
