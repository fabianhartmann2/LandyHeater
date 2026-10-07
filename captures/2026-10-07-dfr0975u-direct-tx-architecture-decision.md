# DFR0975-U direct-TX architecture decision — 2026-10-07

## Decision

The project owner explicitly chose to retain the existing passive level
shifter as a permanently connected DFR0975-U heater UART interface and to omit
the previously planned physical TX output-enable gate.

This is an architecture change, not evidence that the old gate exists.
`board_config.py` therefore identifies the selected interface as
`direct_level_shifter`, records a separate `UART_DIRECT_TX_APPROVED` decision
and keeps `UART_TX_GATE_APPROVED=False`. D12/GPIO12 remains physically
disconnected and is not used by the selected interface.

## Evidence supporting the decision

- The same level shifter previously operated with the Raspberry-Pi controller.
- DFR0975-U TX14/RX13 repeatedly remained UART idle-high.
- A live INIT exchange and a live STATUS exchange passed with valid CRC.
- The normal `ConfiguredHeaterRuntime` then exchanged exactly one INIT and one
  STATUS, reached synchronized `ready`/`off` at 12.1 V and closed normally.
- The Autoterm protocol accepts structured CRC-protected frames rather than a
  single static logic level.

## Explicitly accepted residual risk

The level shifter has no `OE`/`EN` input. Consequently it cannot independently
isolate the ESP TX pin during reset, boot, firmware faults or incorrect UART
configuration. Pull-ups hold the proven idle state high and GPIO14 is not a
selected boot-strapping pin, but these are mitigations rather than physical
isolation. Software faults capable of constructing a valid control frame also
remain inside the trusted control boundary.

This direct topology is accepted for the project by owner decision. It does
not turn the ESP/level-shifter assembly into an independent functional-safety
system or an automotive-qualified interface.

## Retained software controls

- Product transmission remains disabled until both
  `UART_PINS_APPROVED=True` and `UART_PROTOCOL_TX_ENABLED=True` are reviewed in
  a dedicated source candidate.
- The direct-topology approval is a third, separately validated condition; the
  generic TX flag alone cannot select an unapproved direct path.
- The production factory has no public unlock argument.
- Every direct UART write must be complete and `UART.txdone()` must confirm
  physical drain within a bounded timeout; writes are never retried silently.
- `HeaterController` remains the sole authority for INIT, STATUS, START and
  controlled SHUTDOWN decisions.
- Active testing remains supervised with an accessible 12-V cutoff.

## Next gate

The bounded START/controlled-SHUTDOWN acceptance tool is now prepared and
software-tested. It fixes the first cycle at power level 1 for seven minutes
from START, requires observed STARTING and RUNNING, then supervises automatic
controlled shutdown until confirmed OFF. Only after reproducible artifact
generation, hash-bound approval and an initial OFF synchronization may this
single supervised active cycle be attempted.
