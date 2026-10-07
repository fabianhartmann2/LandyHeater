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
2,130,032 bytes with SHA-256
`f28059c847a725fda41bfce19d353318bf7d64d7bd2bf0e4f8b16ce601fed6a6`
at `0x10000` without full erase. It has not been flashed or target-accepted.
The preceding product-autostart image remains the last target-accepted image.
The private device credential is not stored in Git. No file in this directory
authorizes a future board flash.
