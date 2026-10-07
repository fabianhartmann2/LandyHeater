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

The existing unknown bidirectional converter is not accepted for active TX
because its four exposed low-side wires provide no independent output-enable.
The TX side needs a known fixed-direction, three-state level translator whose
heater-facing output is high impedance while disabled.

The selected electrical function is the
[SN74LV1T126-Q1](https://www.ti.com/product/SN74LV1T126-Q1), or the catalog
SN74LV1T126 for a bench prototype. It provides 3.3-V-to-5-V translation when
powered from regulated 5 V, and its output is high impedance while active-high
`OE` is low. The Q1 variant is AEC-Q100 qualified; system-level automotive
power/transient qualification remains separate.

Required connections:

| Gate pin/function | Connection |
| --- | --- |
| `VCC` | regulated logic 5 V, never raw vehicle 12 V |
| `GND` | common signal ground / yellow |
| `A` | D10/GPIO14 |
| `OE` | D12/GPIO12 plus external 10-kOhm pull-down to GND |
| `Y` | heater RX input: the heater-side line that formerly received controller TX |

A 100-nF ceramic bypass capacitor must sit directly between gate `VCC` and
`GND`. A small series resistor at `Y` may be selected during the schematic
review, but must not be guessed during wiring. The current converter's RX
channel may be retained only after its green output is reconfirmed as a safe
3.3-V signal. Its TX channel is bypassed by the new known gate.

Because this requires access to the heater-side TX line and regulated 5 V, the
SN74LV1T126 must not simply be inserted between D10 and the exposed white
low-side wire without identifying the converter topology. The shrink-wrapped
assembly must be opened or replaced with a documented interface assembly.

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
