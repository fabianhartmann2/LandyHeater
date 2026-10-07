# Phase 13 heater UART bring-up

## Current status

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

The active product profile remains closed:

```text
UART_PINS_APPROVED = False
UART_TX_GATE_APPROVED = False
UART_PROTOCOL_TX_ENABLED = False
```

These bench probes do not open the product path. The product profile cannot
transmit while the three flags remain closed, and the direct probes are inert
unless source-mounted and invoked with their exact confirmations.

The cold source-level product integration now exists as
`ConfiguredHeaterRuntime`. It owns the protocol/controller lifecycle, polls RX
before every controller step, begins Requested OFF and refuses normal cleanup
after I/O until a valid STATUS confirms OFF. Its parameterless product factory
still requires all three closed flags above, so this implementation neither
changes the deployed image nor authorizes the direct bench wiring for normal
operation.

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

D12/GPIO12 remained physically disconnected throughout. The direct D10 bench
connection is a temporary diagnostic exception based on the converter's known
prior Raspberry-Pi use; it does not replace the required product TX gate.

## Required active-high TX gate

The photographed converter is the common four-channel MOSFET topology with
`HV`, `LV`, four paired `HVx`/`LVx` channels, one MOSFET per channel and
`103` (10-kOhm) pull-ups on both sides. This is a BSS138-style passive,
bidirectional I2C level shifter; the exact transistor marking is not legible,
so BSS138 is a topology identification rather than a component traceability
claim. It has no `OE`/`EN` input and therefore cannot provide the required TX
lock by itself. The topology and intended I2C use correspond to NXP AN10441's
pass-MOSFET level-shifting circuit.

The converter previously worked with the Raspberry/Node-RED controller at
9600 baud. It has now also carried CRC-valid INIT and STATUS exchanges on the
DFR0975-U, confirming the bounded bench path and its edge quality for those
exchanges. This does not qualify it as the final protected product interface. The
additional gate is inserted on the 3.3-V side between D10 and the exposed
white TX conductor. When disabled, the gate output is high impedance and the
converter's existing pull-ups hold both sides at UART idle-high; critically,
no low start bit from D10 can reach the heater.

The selected gate function is the
[SN74LV1T126-Q1](https://www.ti.com/product/SN74LV1T126-Q1), or the catalog
SN74LV1T126 for a bench prototype. Here it is powered from 3.3 V and used as
an active-high-enabled three-state buffer; the existing MOSFET board continues
to perform the 3.3-V/5-V translation. The Q1 variant is AEC-Q100 qualified;
system-level automotive power/transient qualification remains separate.

Required connections:

| Gate pin/function | Connection |
| --- | --- |
| `VCC` | brown / ESP 3.3 V |
| `GND` | common signal ground / yellow |
| `A` | D10/GPIO14 |
| `OE` | D12/GPIO12 plus external 10-kOhm pull-down to GND |
| `Y` | exposed white TX conductor into the existing converter's LV channel |

A 100-nF ceramic bypass capacitor must sit directly between gate `VCC` and
`GND`. The current converter's green RX channel remains connected through the
existing 10-kOhm series resistor to D11. The white wire must be cut or left
detached at the ESP end and routed only through gate `Y`; it must never also
have a direct D10 connection. No change to the converter's heater-side `HV`
or channel wiring is required for this prototype arrangement.

The SN74LV1T126 is normally supplied as a small SMD part. The exact purchased
part or breakout and its pinout must be reviewed before wiring; package pin
numbers are deliberately not inferred here.

## Software gate

The source candidate now owns D12 before opening an authorized TX transport.
It drives the active-high gate low, enables it for exactly one UART write,
waits for MicroPython `UART.txdone()` with a bounded timeout, then drives it
low again. Short writes, driver exceptions, drain timeouts and interrupts all
force disable; failure to drive low falls back to releasing D12 as an input so
the physical pull-down owns the safe state. Cleanup deinitializes UART and
releases the gate. There is no automatic retry.

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
4. Assemble the documented TX gate with 10-kOhm OE pull-down and local 100-nF
   bypass capacitor.
5. With the heater disconnected, verify continuity, no shorts, correct 3.3-V
   and regulated 5-V rails, OE low at reset and heater-facing output high-Z.
6. USB-only target test: verify `UART.txdone()` exists, D12 remains low through
   reset/boot and one isolated logic-side loopback can be gated cleanly.
7. Only after the protected gate passes, consider opening the three product
   flags in a separately reviewed source candidate. START, SHUTDOWN and
   external-temperature commands remain blocked until their own later gates.
