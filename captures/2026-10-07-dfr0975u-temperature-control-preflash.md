# DFR0975-U temperature-control preflash gate — 2026-10-07

## Scope

This gate follows the accepted production Web-UI Power-1 cycle. It adds the
selected roof-tent or cabin DS18B20 report to the Autoterm `0x11` command,
bounded Stop handling in `TEMP_MONITORING`, and signed negative-temperature
support. No heater operation or flash occurred during this gate.

## Negative temperatures

Negative DS18B20 readings are valid regulation input and do not block START or
latch shutdown. The existing `Math.ceil` compatibility rule is applied first;
the integer is then represented as one signed two's-complement byte. Examples:

- `-1.2 °C` becomes `-1` and payload `0xFF`;
- `-55 °C` becomes payload `0xC9`.

The full DS18B20 input range remains `-55…125 °C`. Values outside the signed
byte representation are rejected by the protocol builder.

## Verification

- Focused protocol/controller/composition tests: 141 passed.
- Full repository suite: 1,294 tests; 1,292 passed. The only two failures are
  the deliberately immutable historical Phase-11 and early Phase-13 source
  ledgers.
- Current 50-file Phase-13 source closure: passed.
- Two clean canonical-path builds: all 15 retained comparison outputs were
  byte-identical.
- Application partition usage: 2,132,464 of 3,145,728 bytes; 1,013,264 bytes
  remain.

## Candidate

```text
offset: 0x10000
size:   2132464 bytes
sha256: 84eb394f7be14a853283d52db6d80c6d2f6ea14bc6e22704864acdb5d5a45881
erase:  no full-chip erase
```

This record is evidence only. It does not authorize a flash. The first target
run must remain bounded and must confirm periodic reporting, STARTING/RUNNING,
TEMP_MONITORING behavior where reached, UI Stop and CRC-confirmed OFF.

## Subsequent result

This exact candidate was later authorized, app-only flashed, fully read back
byte-identical and accepted in separate real-heater roof-tent and cabin runs.
The cabin run also exposed a Start-dialog cancel-submit defect. Its corrected,
not-yet-flashed successor and the complete target evidence are recorded in
`2026-10-07-dfr0975u-temperature-ui-e2e.md`.
