# Phase 13 live Setup-sensor frozen-module candidate

This directory defines the exact 50-file DFR0975-U N16R8 closure for the
integrated Landy Heater runtime. It includes the accepted sensor, heater,
REST/Web, discovery, diagnostics, Scheduler and volatile browser-time paths,
plus a private frozen `main.py` that starts the product after every reset.

Startup is ordered and fail-closed: trusted production storage must load;
sensors and the heater protocol start requested OFF; the recovery AP, REST
security, captive portal and Web UI become live; only then is the Scheduler
armed. After station DHCP and mDNS readiness, a second port-80 listener is
attached to the station address while the AP listener stays live. Optional
WLAN, Web-listener or diagnostics failures do not end heater supervision.

The private `board_config.py` opens only the previously accepted UART pins and
protocol TX for the owner-approved direct level shifter. I2C and the absent
GPIO12 TX gate remain closed. The repository-root passive `main.py` is not in
this closure.

The Setup Assistant now reads the already running DS18B20 runtime directly,
refreshes ROM ID, temperature, health and reading age every 1.5 seconds, and
requires three distinct currently healthy sensors before accepting a reviewed
assignment. The user may instead explicitly skip sensor setup. A skipped step
preserves all existing assignments; it cannot silently clear or modify them.

`BUILD_INFO.md` records inputs, source ledger, two byte-identical builds,
artifact checks and inherited target evidence. The new app-only image is
2,131,568 bytes with SHA-256
`c344bb3ca6f4f7540b1925e8d65a7a8cdc93328c230a8a640fe9c7c6b153aae9`
at `0x10000` without full erase. It was flashed app-only, read back
byte-identical and accepted for serialized UI startup and the explicit sensor-
setup skip path. Physical live-sensor assignment remains open.
The private device credential is not stored in Git. No file in this directory
authorizes a future board flash.

This candidate also corrects the residual target-observed UI request burst
without raising the bounded server's client or backlog limits. The HTML starts
only a 942-byte bootstrap. It loads the combined stylesheet, translations and
application core strictly in order with bounded retries. Core modules and the
initial API reads are also sequential; Setup and Diagnostics remain on demand.
The target UI reached connected state, all tabs worked and the Setup Assistant
opened. With no sensors attached, its explicit skip choice advanced correctly.
