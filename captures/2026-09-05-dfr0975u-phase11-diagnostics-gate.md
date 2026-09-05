# DFR0975-U Phase-11 diagnostics target gate — 2026-09-05

## Result

**PASS for the Phase-11 application flash/readback, passive USB runtime gate
and bounded real-phone diagnostics/capture/export flow.**

The test used the DFR0975-U V1.0 with external antenna. Heater, vehicle power,
UART, RTC/I2C, 1-Wire and sensors remained disconnected. Automatic product
startup and all electrical hardware approvals stayed closed.

## Authorized app-only flash and readback

The owner explicitly authorized an app-only write at `0x10000` without full
erase, bound to SHA-256:

`274234961f43551526b843ca7b27b3ead594cb5e93bf079b39f4ea838ab2c566`

The retained `firmware/phase11_frozen/artifacts/micropython.bin` was 2,086,960
bytes and matched that digest immediately before writing. Esptool identified
the ESP32-S3 revision 0.1 with 8-MiB embedded PSRAM and 16-MiB flash. It erased
only the application sectors covering `0x10000–0x20dfff`, wrote the authorized
application and passed write-time verification. It did not write bootloader,
partition table or VFS.

An independent read of exactly 2,086,960 bytes from `0x10000` was
byte-identical and returned the exact authorized SHA-256.

## Passive USB boot gate

After manual `RST`, USB reported:

```text
PHASE11_USB_BOOT micropython (1, 28, 0, '') esp32 DFRobot DFR0975-U N16R8 with ESP32S3
PHASE11_RADIO_OFF False False
PHASE11_HEAP 8314848 6688
PHASE11_DIAGNOSTICS True 1 1 0 {'value': 7} aa5502013300
PHASE11_USB_PASS True 8316496
```

The synthetic event contained a password-shaped field before recording; only
the public `value` remained. The diagnostic service was deinitialized and its
RAM records were removed. No WLAN or peripheral was opened.

## Bounded phone gate

The target runner reused the accepted AP-only, isolated A/B storage, full REST
and single port-80 listener seams. It marked only its disposable profile as
setup-complete, so the already accepted Setup Assistant did not obscure the
diagnostics test. The AP remained `Landy Heater` with the established test
credential; the credential was neither printed nor changed.

One phone loaded the responsive diagnostics view. The server validated:

- all three diagnostics assets after one browser-cache reload;
- 91 successful combined diagnostics/event/protocol live responses;
- exactly one successful named RAM-capture start;
- one synthetic redacted event and one synthetic RX protocol frame;
- exactly one successful capture stop;
- one valid JSON capture export with at least four records and no redaction
  sentinel;
- 162 completed HTTP requests with zero parse errors and no server fault;
- Requested State OFF with revision zero and zero heater-protocol calls;
- no production or post-baseline isolated storage write.

No real UART frame was received or transmitted. The raw frame in the export
was synthetic and existed only to exercise the Phase-11 presentation path.

## Harness corrections and verdict provenance

The first preparation exposed the required Setup Assistant because the
disposable profile had not been marked complete. That precondition was
corrected without touching product storage or firmware. An interrupted retry
left port 80 unavailable until a hard board reset; the runner failed closed
and both radios were verified inactive.

During the final functional run, the initial expected fragment path was
`/diagnostics.html` instead of the real `/assets/diagnostics.html`; this was
corrected. After every functional criterion was visibly and server-side
confirmed, the runner still emitted a false-negative terminal verdict at its
timeout. Its inherited transport observer records a target only for GET, but
the completion predicate also required it to name the successful POST/DELETE
capture target. The independent gateway counters had already confirmed both
mutations and the validated export. The impossible wire condition was removed
and regression tests now prove that mutation truth comes from the gateway
while static/live/export GET responses remain wire-bound.

The PASS conclusion is based on the complete functional counters above plus
the independent cleanup check, not on the superseded false-negative terminal
token. Repeating the already observed phone actions would add no missing
evidence.

## Independent cleanup

After the runner's ordered fallback cleanup, a separate USB-only check
reported:

```text
PHASE11_POSTCHECK_RADIO False False
PHASE11_POSTCHECK_HEAP 8320176
PHASE11_POSTCHECK_FILES ()
```

The diagnostics hub had been deinitialized by the runtime cleanup. Both WLAN
interfaces were inactive and all disposable A/B files were absent. Phase 11
does not authorize product autostart or electrical heater/peripheral testing;
those gates remain assigned to Phase 13.
