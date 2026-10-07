# DFR0975-U live Setup-sensor UI preflash finding

Date: 2026-10-07. Heater 12 V remained off; the board was USB-only.

## Authorized image and write

The owner authorized exactly the 2,130,032-byte application image with
SHA-256 `f28059c847a725fda41bfce19d353318bf7d64d7bd2bf0e4f8b16ce601fed6a6`
for an app-only write at `0x10000` without full erase. Esptool identified the
target as ESP32-S3 revision 0.1 with embedded 8-MiB PSRAM. It erased only
application sectors `0x10000–0x218fff`, verified the write, and stayed in the
bootloader. An independent read of exactly 2,130,032 bytes from `0x10000` was
byte-identical and had the authorized digest.

## Target observation

After a physical reset the phone joined the unchanged protected `Landy Heater`
AP and the captive portal opened automatically. The Web UI appeared, but its
connection badge remained orange until a direct browser load completed about
50 seconds later. Both direct endpoints returned valid JSON:

- `/api/v1/status` returned the normal product status;
- `/api/v1/setup` returned generation 4, `setup_complete=true`, the three
  preserved sensor assignments, active sensor probing and an empty discovered
  list for the then-current physical sensor state.

Even after a second full load of about 50 seconds, the manual **Setup Assistant
starten** button remained inert. This rejects that image as a usable UI
candidate despite its correct API and verified flash.

## Root cause and correction

The page requested five CSS files and seven JavaScript files in parallel. The
product HTTP service intentionally permits only two active clients and backlog
two. The browser's initial burst therefore overflowed the bounded acceptance
surface, causing long retry delays and leaving `setup.js` unavailable when the
application booted.

The successor candidate preserves the server limits and instead reduces the
browser burst:

- the five stylesheets are served as one 15,713-byte `/assets/ui.css`;
- only `/assets/i18n.js` and `/assets/app.js` are initial scripts;
- Home, Timers and Settings load sequentially;
- Setup and Diagnostics load on demand with a retryable module loader;
- Setup binding is idempotent.

The new initial subresource count is three rather than twelve. The correction
passed the current Web/Setup/API tests, the complete current frozen closure,
JavaScript syntax/validation, two clean byte-identical firmware builds and
offline image/artifact checks.

## Serialized-core target result

The owner then authorized the 2,130,592-byte serialized-core image with
SHA-256 `5f84832e5595368452b25e00a71d7be4e8e7903d1b0a9cf7e6a42f1da9d3878a`.
It was written app-only at `0x10000` without full erase and independently read
back byte-identical. The protected AP joined successfully. A manual UI load
completed in about five seconds, improving the earlier 50-second result, but
the badge remained orange after an additional 20 seconds and the tabs were
inert. A direct request for `/assets/app.js` returned the expected source.

This rejects the serialized-core image: the remaining parallel stylesheet,
translation and application requests can still overlap the phone's captive-
portal probes and lose a required startup resource.

## Fully serialized bootstrap correction

The next candidate keeps the two-client/two-backlog server boundary. The HTML
requests only a 942-byte `/assets/boot.js`. That bootstrap loads
`/assets/ui.css`, `/assets/i18n.js` and `/assets/app.js` strictly in order, with
at most three bounded attempts per resource. The app then loads Home, Timers
and Settings sequentially and also performs the initial status, settings and
timer API reads sequentially. Setup and Diagnostics remain on demand.

The replacement application is 2,131,568 bytes with SHA-256
`c344bb3ca6f4f7540b1925e8d65a7a8cdc93328c230a8a640fe9c7c6b153aae9`.
Two clean canonical builds were byte-identical. This record does not authorize
its flash; a new exact hash-bound authorization is required.
