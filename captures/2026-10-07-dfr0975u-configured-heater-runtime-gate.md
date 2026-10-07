# DFR0975-U configured heater-runtime gate — 2026-10-07

## Scope

This source-mounted Phase-13 gate exercised the real cold
`ConfiguredHeaterRuntime`, normal `HeaterController`, protocol service and UART
transport against the powered but idle Autoterm controller. It was limited to
synchronization only. It did not flash firmware, mutate configuration, enable
Wi-Fi or change any product approval flag.

Confirmed bench wiring:

- white controller TX on D10/GPIO14;
- green controller RX through 10 kOhm on D11/GPIO13;
- yellow common signal ground;
- brown low-side 3.3-V supply;
- D12/GPIO12 physically disconnected;
- existing passive level shifter retained as the explicit bench exception.

The protected hardware TX gate documented for the final product remains
absent. Consequently this result does not authorize normal product TX.

## Two-stage approvals

Arming, with heater 12 V off:

```text
DFR0975U_USB_ONLY_CONFIGURED_HEATER_RUNTIME_GREEN_RX13_WHITE_TX14_D12_DISCONNECTED_HEATER_OFF_V1
```

Triggering, after heater 12 V was enabled and the assembly remained cool:

```text
DFR0975U_CONFIGURED_HEATER_RUNTIME_12V_IDLE_INIT_STATUS_ONLY_V1
```

Arming opened UART, loaded the existing production A/B configuration and
started the runtime without calling `step()`. The target reported zero writes,
configuration generation 2, unchanged storage, inactive radios and all three
product TX flags still closed.

## Enforced transmission boundary

The source-mounted probe used two independent bounds:

1. its protocol facade rejected START and SHUTDOWN calls;
2. its UART facade accepted exactly one canonical INIT request followed by
   exactly one canonical STATUS request, rejecting every other or repeated
   write before the hardware UART.

The UART facade also required RX and TX idle-high immediately before each
write, required a complete write and waited for `UART.txdone()` with a bounded
timeout. There was no retry path.

## Result

```text
phase=ready
heater_state=off
voltage=12.1
init_writes=1
status_writes=1
rx_frames=2
tx_frames=2
configuration_generation=2
storage_unchanged=True
product_tx_flags_closed=True
radios_inactive=True
DFR0975U_CONFIGURED_HEATER_RUNTIME_OFF_PASS_V1
```

The runtime accepted the INIT response, requested STATUS, accepted the valid
STATUS response and reached synchronized `ready`/`off`. Normal runtime cleanup
was therefore permitted and the owning UART session was released. No START,
SHUTDOWN, power, target-temperature or external-temperature request was
available or sent.

## Conclusion and remaining boundary

The product lifecycle and controller synchronization path are now proven on
the real DFR0975-U with the existing read-only configuration. This proves the
software path from runtime composition through INIT/STATUS and safe OFF-only
cleanup. It does not qualify the direct level-shifter connection as the final
product TX interface.

Before normal product TX can be enabled, the documented active-high external
TX gate with physical pull-down and local bypass capacitor must be assembled
and pass its own disconnected electrical, reset-state and gated-loopback
tests. `UART_PINS_APPROVED`, `UART_TX_GATE_APPROVED` and
`UART_PROTOCOL_TX_ENABLED` remain `False`.
