# Phase 13 product-autostart frozen-module candidate

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

`BUILD_INFO.md` records inputs, source ledger, two byte-identical builds,
artifact checks and inherited target evidence. The new app-only image is
2,121,600 bytes with SHA-256
`66c0799515b45334b2f852d66a4c347614a65b2109883c9f89f253df00a68a5f`
at `0x10000` without full erase. It has not yet been authorized or target-
tested. No file in this directory authorizes a board flash.
