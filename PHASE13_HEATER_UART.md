# Phase 13 heater UART bring-up

## Current status

The bounded active heater gate passed on the real DFR0975-U on 2026-10-07.
The reviewed application was written app-only at `0x10000`, independently
read back byte-for-byte, and then ran exactly one power-level-1 cycle for seven
minutes. The controller observed STARTING, RUNNING and SHUTTING_DOWN and
returned to synchronized `ready`/`off` at 12.2 V with one START, one SHUTDOWN,
576 STATUS requests, unchanged production storage and inactive radios. Exact
evidence is in `captures/2026-10-07-dfr0975u-heater-active-cycle.md`.

The DFR0975-U receive-only driver and its pin cleanup passed on the real board
on 2026-10-07. Passive checks produced no unsolicited frame, as expected for
the heater's master/request-response protocol. A separately approved direct
bench exception then proved UART2 and the existing passive level shifter at
9600/8N1: request `AA030000049F3D` received the CRC-valid INIT response
`AA04050004128A003DD6CBA6` on the first attempt. No START, SHUTDOWN, external
temperature or power-setting command was sent.

The separately approved follow-up also passed on its first attempt. After one
valid INIT exchange, STATUS request `AA0300000F587C` received CRC-valid frame
`AA0413000F000100107F007A0121000000000000000000601A48`. The established
parser reported heater state `off`, supply voltage `12.2 V`, glow-plug raw
value `33` and fan raw value `0`. Both rejected-frame counters remained zero;
UART and GPIOs were released immediately afterward.

The first received response exposed a target-only parser defect: MicroPython's
`bytearray` does not support item or slice deletion. `RawFrameStreamParser`
now rebinds sliced buffers instead. The known INIT response was parsed on the
real target with one frame and an empty remainder, and the subsequent live
INIT gate passed. Earlier no-response runs were caused by intermittent/wrong
pin contact; the final successful wiring is recorded below.

The active source profile records the accepted direct interface while product
transmission remains closed:

```text
UART_TX_INTERFACE = "direct_level_shifter"
UART_DIRECT_TX_APPROVED = True
UART_PINS_APPROVED = False
UART_TX_GATE_APPROVED = False
UART_PROTOCOL_TX_ENABLED = False
```

These bench probes do not open the product path. The product profile cannot
transmit while pin approval and protocol TX remain closed, and the direct
probes are inert unless source-mounted and invoked with their exact
confirmations.

The cold source-level product integration now exists as
`ConfiguredHeaterRuntime`. It owns the protocol/controller lifecycle, polls RX
before every controller step, begins Requested OFF and refuses normal cleanup
after I/O until a valid STATUS confirms OFF. Its parameterless product factory
still requires approved pins, the selected interface approval and protocol
TX, so this implementation does not change the deployed image or yet authorize
normal product transmission.

A source-mounted target gate on 2026-10-07 confirmed the production factory's
closed state on the real DFR0975-U: flags were `False/False/False`, the factory
rejected before opening UART, D10 and D11 remained input-high, D12 remained
input-low, and no frame was transmitted. The next live milestone is therefore
a separately approved, source-mounted `ConfiguredHeaterRuntime` synchronization
run using the already proven bench interface; it is not automatic boot.

That bounded gate is implemented in
`tools/phase13_heater_runtime_probe.py`. Arming requires heater 12 V off,
loads the existing production A/B configuration read-only, opens the proven
bench UART and starts the normal configured runtime without calling `step()`;
therefore it sends nothing. A separate exact confirmation permits one INIT
followed by one STATUS only. Both the protocol facade and the UART whitelist
reject START, SHUTDOWN, temperature and repeated synchronization commands.
Success requires a valid synchronized OFF state, unchanged storage, inactive
radios and all three product TX flags still closed.

The gate passed on the real board on 2026-10-07. The runtime reached
`ready`/`off`, reported 12.1 V, and exchanged exactly one INIT request/response
and one STATUS request/response. Configuration generation 2 and every A/B file
signature remained unchanged. Evidence is recorded in
`captures/2026-10-07-dfr0975u-configured-heater-runtime-gate.md`.

## Confirmed controller-side conductors

| Conductor | Meaning | DFR0975-U route now |
| --- | --- | --- |
| green | controller RX, heater-to-controller | through 10 kOhm to D11/GPIO13 for the approved bench probe |
| white | controller TX, controller-to-heater | D10/GPIO14 only for the approved bench probe |
| yellow | signal ground | GND |
| brown | low-side level supply | 3.3 V; not supplied elsewhere |

D12/GPIO12 remained physically disconnected throughout. By explicit owner
decision on 2026-10-07, the proven direct D10 connection through the existing
level shifter is now the selected prototype/product interface rather than a
temporary diagnostic exception.

## Accepted direct level-shifter topology

The photographed converter is the common four-channel MOSFET topology with
`HV`, `LV`, four paired `HVx`/`LVx` channels, one MOSFET per channel and
`103` (10-kOhm) pull-ups on both sides. This is a BSS138-style passive,
bidirectional I2C level shifter; the exact transistor marking is not legible,
so BSS138 is a topology identification rather than a component traceability
claim. It has no `OE`/`EN` input and is therefore permanently connected. The
topology and intended I2C use correspond to NXP AN10441's pass-MOSFET
level-shifting circuit.

The converter previously worked with the Raspberry/Node-RED controller at
9600 baud. It has now also carried CRC-valid INIT and STATUS exchanges on the
DFR0975-U, confirming the bounded bench path and its edge quality for those
exchanges and the normal configured-runtime synchronization. The owner accepts
the residual risk that reset, boot or faulty firmware is not independently
isolated from the heater TX conductor. Pull-ups, UART idle-high, structured CRC
frames, the non-strapping GPIO14 route and software authority boundaries are
retained mitigations, not a physical safety disconnect. The full decision is
recorded in
`captures/2026-10-07-dfr0975u-direct-tx-architecture-decision.md`.

## Direct-TX software boundary

The source now distinguishes `direct_level_shifter` from the optional historic
`active_high_gate` path. Enabling generic protocol TX is insufficient unless
the selected direct topology has its own explicit approval. The direct UART
facade waits for MicroPython `UART.txdone()` after every complete write with a
bounded timeout. Short writes, driver exceptions and drain timeouts fail
closed at the protocol/controller boundary, and there is no automatic retry.

The implementation follows MicroPython 1.28's documented `machine.UART`
completion API and the ESP32 implementation's non-blocking `txdone()` result.
The product flags remain closed, so this code cannot transmit from the current
board profile.

## Acceptance progress and remaining order

1. **Complete:** RX-only checks, powered idle checks and bounded direct INIT
   and STATUS exchanges on the existing level shifter.
2. **Complete:** the source-mounted status diagnostic sent exactly one INIT
   and one STATUS request, accepted both CRC-valid responses, rejected no
   frames, and released UART and GPIOs. It has no retry or dangerous-command
   surface.
3. **Complete:** the normal configured heater runtime loaded production
   configuration generation 2 read-only, synchronized through exactly one
   INIT and one STATUS exchange, reached `ready`/`off` at 12.1 V and closed
   normally. Product flags remained closed and radios inactive.
4. **Complete:** owner decision accepts the proven permanently connected level
   shifter and its documented residual reset/software risk; D12 remains
   disconnected and `UART_TX_GATE_APPROVED=False`.
5. **Complete:** the active-cycle gate used power level 1
   for seven minutes from START, requires observed STARTING and RUNNING, then
   supervises automatic controlled SHUTDOWN until confirmed OFF. This gives
   the heater at least five minutes to start and settle plus roughly two
   minutes stable operation. It has no external-temperature or raw-send path,
   caps START/SHUTDOWN at two attempts and requires an accessible 12-V cutoff.
6. **Complete:** the separate 48-file frozen candidate changes only
   `UART_PINS_APPROVED` and `UART_PROTOCOL_TX_ENABLED` from false to true,
   passed focused safety tests, produced two byte-identical 15-output builds
   and passed image/layout gates. Its app-only image is 2,109,520 bytes with
   SHA-256 `f02d59e7d8c501c387e837cbbbc38ec9725387e8ac9db18c1395df084298ca3d`.
   It was authorized and flashed app-only at `0x10000`; a full independent
   readback was byte-identical.
7. **Complete:** one supervised START/SHUTDOWN cycle returned to confirmed
   `ready`/`off` with one START and one SHUTDOWN. No repeated exploratory
   command runs were performed or are authorized by this result.
