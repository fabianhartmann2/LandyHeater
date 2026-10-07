# Phase 13 heater UART bring-up

## Current status

The DFR0975-U receive-only driver and its pin cleanup passed on the real board
on 2026-10-07. The passive level-shifter checks with heater power off and in
powered idle were electrically quiet and produced no valid frame. This is
expected because the removed Node-RED controller was the protocol master; the
heater responds to INIT/STATUS requests and was not shown to publish status
unsolicited.

The active product profile remains closed:

```text
UART_PINS_APPROVED = False
UART_TX_GATE_APPROVED = False
UART_PROTOCOL_TX_ENABLED = False
```

No heater command has been transmitted by the DFR0975-U.

## Confirmed controller-side conductors

| Conductor | Meaning | DFR0975-U route now |
| --- | --- | --- |
| green | controller RX, heater-to-controller | through 10 kOhm to D11/GPIO13 |
| white | controller TX, controller-to-heater | disconnected and insulated |
| yellow | signal ground | GND |
| brown | low-side level supply | 3.3 V; not supplied elsewhere |

D10/GPIO14 and D12/GPIO12 remain physically disconnected until the TX gate
below is assembled and electrically checked.

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
9600 baud, and the short powered-idle checks were electrically quiet. It may
therefore remain for the bounded bench bring-up, but real edge quality and a
CRC-valid response are still required before it is accepted for UART. The
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

## Remaining acceptance order

1. Heater 12 V and USB off; restore green through 10 kOhm to D11 and insulate
   white until the new interface is ready.
2. Assemble the documented TX gate with 10-kOhm OE pull-down and local 100-nF
   bypass capacitor.
3. With the heater disconnected, verify continuity, no shorts, correct 3.3-V
   and regulated 5-V rails, OE low at reset and heater-facing output high-Z.
4. USB-only target test: verify `UART.txdone()` exists, D12 remains low through
   reset/boot and one isolated logic-side loopback can be gated cleanly.
5. Obtain a fresh explicit approval for the exact live INIT diagnostic. Open
   the three flags only in RAM, transmit exactly one canonical INIT frame, wait
   for one CRC-valid INIT response, then close and release all pins. The inert
   source candidate is `tools/dfr0975u_uart_init_probe.py`; it has no START,
   SHUTDOWN, external-temperature or retry path and must not run before this
   approval.
6. Only after INIT passes, repeat with exactly one STATUS request. START,
   SHUTDOWN and external-temperature commands remain blocked.
