# DFR0975-U Phase-10 credential-input gate — 2026-09-03

## Status

**PASS for the authorized application flash, complete readback and real
credential-input behaviour. The strict same-run every-route transport closure
remains inconclusive because at least one browser resource was not observed on
the wire before the bounded window ended.**

This distinction is deliberate: the Setup Assistant accepted and verified the
new functionality on the real target, but the runner did not emit its formal
PASS token and must not be reported as though it did.

## Authorized app-only flash

The owner confirmed USB-only operation and explicitly authorized the retained
2,050,848-byte application image at `0x10000`, without a full-chip erase, bound
to SHA-256:

`d8fb33c0e43081d95744816cbbedf7b77281292d3c8458d14b1f50cf27f7b9ef`

Automatic ROM entry returned no serial data and performed no write. After
manual `BOOT`/`RST` entry, esptool 4.12 identified the expected ESP32-S3
revision 0.1 and 8-MiB embedded PSRAM. It erased only the application sectors
`0x10000–0x204fff`, wrote the exact authorized image and passed its write-time
hash verification. Bootloader, partition table and VFS were not written.

An independent read of all 2,050,848 application bytes returned the exact
authorized SHA-256 and compared byte-for-byte equal with the retained image.
After physical `RST`, passive USB checks reported MicroPython 1.28.0, machine
`DFRobot DFR0975-U N16R8 with ESP32S3`, 11 frozen web resources and both WLAN
interfaces inactive.

## Real credential-input result

The final functional run used the production frozen UI/API with disposable
A/B configuration storage and the established single listener on port 80. The
phone traversed the corrected assistant and explicitly selected a protected
station profile, entered a new station password, selected AP-password
replacement, entered and confirmed the replacement, reviewed the summary and
submitted once.

The target gateway recorded:

```text
validated_required_responses=59
rejected_requests=0
setup_mutation_attempts=1
successful_setup_mutations=1
accepted_connections=62
closed_connections=62
open_connections=0
observer_faulted=False
```

`successful_setup_mutations=1` is emitted only after privileged readback proves
all of the following in the isolated configuration: `setup_complete=true`, the
exact expected replacement AP password, exactly one expected station SSID and
the exact expected station password. Every handled response also rechecked
that Requested State remained OFF and the heater protocol tripwire remained at
zero. Responses were checked against all live and replacement secrets; no
secret appeared in a response representation. No credential value is retained
in this report.

## Formal runner shortfall

The runner waited for at least one application-validated and transport-observed
response for each of 11 UI resources, five initial API reads and the one setup
mutation. The browser completed the new UI and mutation, but the combined
every-route predicate was still false at the end of the bounded window. The
old diagnostic output recorded only aggregate counters, so the exact omitted
route cannot be reconstructed after cleanup. A browser cache reuse or a
cancelled redundant request is consistent with the evidence, but remains an
inference rather than a proven cause.

The target runner was subsequently corrected to print separate missing
application routes, missing wire routes, the sanitized error text and the full
server counter summary on future failures. The functional user flow will not
be repeated merely to improve that diagnostic record.

## Cleanup

The fail-closed fallback removed disposable storage and shut down HTTP, REST
and both radios. Independent passive USB postcheck:

```text
PHASE10_POSTCHECK_RADIO False False
PHASE10_POSTCHECK_FILES ()
PHASE10_POSTCHECK_HEAP 8319360
```

Product startup remains disabled. RTC/I2C, 1-Wire, UART and heater hardware
were neither connected nor activated.
