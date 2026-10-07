# DFR0975-U disconnected UART RX-only gate — 2026-10-07

## Scope and physical boundary

The physically confirmed DFR0975-U V1.0 N16R8 target was powered only over
USB. The owner confirmed D10, D11 and D12 were physically disconnected.
Heater, level shifter and vehicle 12 V were not connected. No image was
flashed and no persistent product data was changed.

The diagnostic used the source-mounted candidate only for one bounded
two-second run. Its exact confirmation token was
`DFR0975U_USB_ONLY_D10_D12_DISCONNECTED_D11_RX_ONLY_V1`.

## Safety properties checked before UART open

- exact DFR0975-U N16R8 MicroPython machine identity;
- UART2 at 9600 baud, 8N1, TX14/D10 and RX13/D11;
- active-high future TX gate on GPIO12/D12;
- `UART_PINS_APPROVED`, `UART_PROTOCOL_TX_ENABLED` and
  `UART_TX_GATE_APPROVED` all exactly `False`;
- station and access-point radios inactive;
- receive-only facade with no write/send/init method.

MicroPython necessarily assigns a TX route when constructing the UART. The
board-specific factory therefore required D10 to remain physically open and
set both GPIO14 and GPIO12 to input/no-pull before construction, immediately
after construction, on every setup failure and after driver deinitialization.

## Target result

The bounded run completed successfully:

```text
elapsed_ms=2001
rx_bytes=1
rx_chunks=1
dropped_bytes=0
dropped_chunks=0
read_errors=0
rx_faulted=false
complete=true
tx_gpio14_released=true
tx_gate_gpio12_released=true
radios_inactive=true
DFR0975U_UART_RX_ONLY_DISCONNECTED_PASS_V1
```

The one captured byte was `00`. With D11/RX13 physically open, this is
accepted only as a floating-input disturbance and not as heater traffic or a
protocol frame. An independent post-run check again set GPIO14 and GPIO12 to
inputs and confirmed both Wi-Fi interfaces inactive.

## Accepted conclusion and remaining boundary

This closes only the unconnected S3 UART-driver and pin-neutralization gate.
It does not approve UART pins for the product, enable protocol transmission,
validate the level shifter or accept any heater message. All UART/TX approval
flags remain closed.

## Passive level-shifter follow-up

The owner identified the four 3.3-V-side conductors as green `RX`, white `TX`,
yellow `GND` and brown `3.3V`; brown is not supplied elsewhere. With the
heater 12-V supply off, the verified receive-only path completed once with
green through 10 kOhm to D11/GPIO13 and once with white through 10 kOhm to
D11/GPIO13. Both runs captured zero bytes and remained cool.

With 12 V applied but the heater not started, green produced one isolated
`00` immediately after UART open and no valid frame during ten seconds. White
produced zero bytes in two separately power-cycled ten-second runs. Every run
had zero read errors and drops, released GPIO14/GPIO12 and left both radios
inactive. No TX conductor was connected to the ESP32 and no frame was sent.

These idle captures cannot identify signal direction. The historical
Node-RED controller was the one-second master: it sent INIT until initialized
and then sent STATUS requests. The heater answered those requests rather than
spontaneously publishing one status frame per second. With that controller no
longer present, silence on both candidates is expected. The owner-confirmed
controller-side labels therefore remain the governing mapping: green is the
controller RX path and white is the controller TX path.

The receive mapping is now recorded, but it cannot be functionally confirmed
until the heater is queried through an independently gated TX path. Green may
reach D11/GPIO13 only through the 10-kOhm series resistor. D10/GPIO14 and
D12/GPIO12 remain physically disconnected until the TX interface in
`PHASE13_HEATER_UART.md` is assembled and electrically approved.
