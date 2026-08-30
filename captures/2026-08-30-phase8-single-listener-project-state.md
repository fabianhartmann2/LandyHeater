# Phase 8 – single-listener project state (2026-08-30)

## Status

This record freezes the current development state before migrating from the
DFRobot DFR0654 to the selected DFR0975-U. It is a source and host-test
checkpoint, **not** a Phase-8 target acceptance.

- Phase 8 full-product target acceptance remains open.
- Phase 9 remains blocked.
- `boot.py` and `main.py` remain passive.
- Heater control, protocol TX, I2C and 1-Wire remain disabled and were not
  exercised by this work.
- The old DFR0654 was not accessed while preparing this record.

The repository base before this checkpoint was commit `5597346`.

## Why the target harness was simplified

The earlier diagnostic runner opened a link-check listener on port 8080 and
then a product listener on port 80. Browser preconnections, repeated
navigations and the extra resident listener made failures harder to attribute
and consumed scarce target memory. The temporary Refresh/browser-check work
was experimental and is deliberately not part of this baseline.

The current runner follows the Phase-8 composition requirement directly:

1. Start the real AP through the production NetworkManager/Wi-Fi path.
2. Verify AP address `192.168.4.1` and exactly one associated client.
3. Keep HTTP/parser/JSON/product modules cold until that association exists.
4. Load the real configuration, isolated persistent storage, ConfigManager,
   NetworkManager, RestApplication, REST security and production HTTP adapter.
5. Start exactly one listener, bound to `192.168.4.1:80`.
6. Accept one deliberate `GET /api/v1/status` request from the associated AP
   peer.
7. Return the unmodified product response and observationally prove parsing,
   RestApplication entry/return, status validation, encoding, successful send
   calls, accepted byte count, full wire completion and clean close.
8. Require every defined heap checkpoint to retain at least 32 KiB.
9. Clean up in the order HTTP sockets/server, observer and gate, REST security,
   Wi-Fi ownership and isolated files.

There is no port-8080 prerequisite, redirect, Refresh header, proof response
header or additional browser request in this design. The production HTTP
adapter is unchanged by the single-listener rewrite.

## Observational failure diagnostics

The runner retains bounded, secret-free counters for:

- AP association and heap checkpoints;
- listener factory, setblocking, bind and listen completion plus numeric errno;
- accept, receive and send actions;
- parsed request and RestApplication entry/return;
- `_status_data()` completion and validator result;
- response encoding and encoded length;
- send attempts, successful sends, accepted bytes and would-block events;
- peer EOF, zero-byte send, write timeout and completed response;
- ordered cleanup truth.

These values only observe the test. They do not add retries, timeout changes,
browser workarounds or product behavior.

## Host evidence

The focused single-listener suite contains 24 cases. It separately covers:

- complete one-listener full-product success;
- accept and receive failures;
- RestApplication failure and validator rejection;
- encode failure;
- zero-byte send and peer EOF;
- partial response;
- repeated would-block followed by success;
- write timeout;
- all 32-KiB heap boundaries;
- exact wire/target ownership, listener gating and cleanup ordering;
- cold AP-stage imports, isolated upload closure and diagnostics;
- configuration mismatch, ownership salvage and mutation rejection.

At this checkpoint the focused module, Python compilation and Git whitespace
check pass. Host evidence proves the harness boundaries; it does not substitute
for a real ESP32 response.

The source checkpoint is bound by:

| File | SHA-256 |
| --- | --- |
| `tools/phase8_full_rest_phone_smoke.py` | `66e994f8dfe9e476b9a7266f4c432bb60ea6ad95fdc22faa3e73ae5f3ce12567` |
| `tools/phase8_full_rest_phone_stage1.py` | `da25268971fcfcb48b7548b48018ae60de99497f5316acba83bfd744c1996b67` |
| `tools/phase8_full_rest_phone_stage2.py` | `ac7b17caedf4d48c460722a58abe22c0ceb91329bc025c49efe2c0eb16a63e08` |
| `tools/phase8_full_rest_phone_stage2_diagnostics.py` | `b92806a70086c507dc7715cd4c6b6fb1605d70e0497807ce5ac6840bcd68f8bf` |
| `tools/phase8_full_rest_phone_stage2_prepare.py` | `8a767783094b59f753e15ced98bc4c71a144fad473f24ebc65166c812189e865` |
| `tools/phase8_full_rest_phone_stage2_seam.py` | `12104d6b66c552e90f855f6aebe095da1f534eb5ef68e152600e47e5fccff30d` |
| `tests/test_phase8_full_rest_phone_smoke.py` | `9e65b1faf17b32855f1b1cc66a01993e538c8cc3b11f94b1ca9cbc798c44fa01` |

## Last DFR0654 target evidence

The production AP/DHCP path remains proven on the old board. The latest phone
observation used automatic configuration and reported:

```text
address  192.168.4.2
mask     255.255.255.0
gateway  192.168.4.1
```

The minimal single-listener Phase-8 run did not reach READY. The port-80
listener path bound and listened, but the measured
`memory_after_proof_before_listen` was only 32,880 bytes—112 bytes above the
mandatory 32-KiB floor. The next post-listen/bind heap checkpoint fell below
that floor before READY. Consequently no product request was admitted and no
Phase-8 PASS was produced.

This is a capacity-gate failure on the DFR0654, not evidence that
`GET /api/v1/status` or the phone failed in that run. Older multi-listener
accept/write observations remain historical diagnostics in
`2026-08-25-phase8-full-rest-progress.md`; they are not combined into a pass
and are not used as proof for the simplified harness.

## Selected successor board

The selected replacement is the **DFRobot FireBeetle 2 ESP32-S3-U,
DFR0975-U, N16R8**:

- ESP32-S3;
- 16 MB flash;
- 8 MB Octal PSRAM;
- external 2.4-GHz antenna connector.

The external antenna is intended to improve placement options in a vehicle or
metal enclosure. It does not solve the demonstrated heap problem; the 8 MB
PSRAM is the relevant capacity improvement.

This is not a drop-in firmware or pin-compatible replacement. Migration shall
require:

1. a distinct DFR0975-U board profile and S3-specific GPIO allow/deny lists;
2. explicit reassignment and validation of UART, I2C and 1-Wire pins;
3. a new MicroPython 1.28 `ESP32_GENERIC_S3` Octal-SPIRAM build;
4. newly generated bootloader, partition table, application, rollback image,
   hashes and flash offsets;
5. proof that PSRAM is detected and used while adequate internal/native heap
   remains available to Wi-Fi/lwIP;
6. fresh USB recovery, passive-boot, radio, storage and safety checks;
7. one new Phase-8 target run with the same >=32-KiB checkpoints and a complete
   real `/api/v1/status` response.

No image or full-flash backup from the classic ESP32 may be restored to the
ESP32-S3. Vehicle installation still requires protected power conversion,
level protection and an appropriate 2.4-GHz 50-ohm external antenna.

## Next work

No additional Phase-8 socket patch is pending. When the DFR0975-U arrives:

1. identify the exact board revision, module and PSRAM mode;
2. establish passive USB bring-up and recovery;
3. add the separate board profile without removing DFR0654 support;
4. build and verify the new frozen S3 firmware;
5. repeat only the relevant board and focused host gates;
6. run exactly one bounded single-listener full-product acceptance.

Only a complete real HTTP 200 JSON response, all heap/safety checks and the
final post-cleanup PASS token may close Phase 8.
