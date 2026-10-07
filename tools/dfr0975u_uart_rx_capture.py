"""Bounded DFR0975-U USB/passive UART receive-only diagnostic.

Importing this module is inert.  The diagnostic never exposes a write method,
never writes the filesystem and requires GPIO14/D10 plus GPIO12/D12 to remain
physically disconnected.  Its first target gate is run with GPIO13/D11 also
disconnected.  A second, separate confirmation covers only the verified
3.3-V RX output and signal ground connected while heater power remains off.
"""

import sys as _sys


CONFIRMATION = "DFR0975U_USB_ONLY_D10_D12_DISCONNECTED_D11_RX_ONLY_V1"
UNPOWERED_LEVEL_SHIFTER_CONFIRMATION = (
    "DFR0975U_USB_ONLY_LEVEL_SHIFTER_RX13_CONNECTED_"
    "HEATER_OFF_TX14_GATE12_DISCONNECTED_V1"
)
POWERED_IDLE_CONFIRMATION = (
    "DFR0975U_LEVEL_SHIFTER_RX13_CONNECTED_HEATER_12V_IDLE_"
    "TX14_GATE12_DISCONNECTED_V1"
)
PASS_TOKEN = "DFR0975U_UART_RX_ONLY_DISCONNECTED_PASS_V1"
UNPOWERED_LEVEL_SHIFTER_PASS_TOKEN = (
    "DFR0975U_UART_RX_ONLY_UNPOWERED_LEVEL_SHIFTER_PASS_V1"
)
POWERED_IDLE_PASS_TOKEN = "DFR0975U_UART_RX_ONLY_POWERED_IDLE_PASS_V1"
EXPECTED_MACHINE = "DFRobot DFR0975-U N16R8 with ESP32S3"
DEFAULT_DURATION_MS = 2000


def _require(condition, message):
    if not condition:
        raise RuntimeError("DFR0975-U RX-only gate failed: {}".format(message))


def _radios_inactive(network_module):
    return (
        network_module.WLAN(network_module.STA_IF).active() is False
        and network_module.WLAN(network_module.AP_IF).active() is False
    )


def run(confirmation, duration_ms=DEFAULT_DURATION_MS, label=None):
    """Open/poll/close the exact S3 UART path without a connected TX route."""

    modes = {
        CONFIRMATION: ("disconnected", PASS_TOKEN),
        UNPOWERED_LEVEL_SHIFTER_CONFIRMATION: (
            "unpowered_level_shifter",
            UNPOWERED_LEVEL_SHIFTER_PASS_TOKEN,
        ),
        POWERED_IDLE_CONFIRMATION: (
            "powered_idle",
            POWERED_IDLE_PASS_TOKEN,
        ),
    }
    _require(confirmation in modes, "exact confirmation is required")
    expected_label, pass_token = modes[confirmation]
    if label is None:
        label = expected_label
    _require(label == expected_label, "capture label differs from confirmation")
    _require(_sys.platform == "esp32", "target is not ESP32 MicroPython")

    import board_config
    import network
    from machine import Pin
    from os import uname
    from protocol.rx_only_transport import (
        open_dfr0975u_rx_only_from_board_config,
    )
    from tools.uart_rx_capture import RX_ONLY_CONFIRMATION, run as capture

    _require(
        getattr(uname(), "machine", None) == EXPECTED_MACHINE,
        "machine identity differs",
    )
    _require(_radios_inactive(network), "a WLAN interface is active")
    for name in (
        "UART_PINS_APPROVED",
        "UART_PROTOCOL_TX_ENABLED",
        "UART_TX_GATE_APPROVED",
    ):
        _require(getattr(board_config, name, None) is False,
                 "{} is not closed".format(name))

    result = None
    primary = None
    cleanup_error = None
    try:
        result = capture(
            RX_ONLY_CONFIRMATION,
            duration_ms=duration_ms,
            label=label,
            config_module=board_config,
            transport_factory=lambda: (
                open_dfr0975u_rx_only_from_board_config(
                    config_module=board_config
                )
            ),
        )
    except BaseException as error:
        primary = error
    finally:
        try:
            Pin(board_config.UART_TX_PIN, Pin.IN, pull=None, hold=False)
            Pin(board_config.UART_TX_GATE_PIN, Pin.IN, pull=None, hold=False)
        except BaseException as error:
            cleanup_error = error

    if primary is not None:
        raise primary
    if cleanup_error is not None:
        raise cleanup_error
    _require(_radios_inactive(network), "a WLAN interface became active")
    for name in (
        "UART_PINS_APPROVED",
        "UART_PROTOCOL_TX_ENABLED",
        "UART_TX_GATE_APPROVED",
    ):
        _require(getattr(board_config, name, None) is False,
                 "{} changed".format(name))
    _require(type(result) is dict and result.get("complete") is True,
             "capture did not complete cleanly")
    print("rx_bytes={}".format(result.get("rx_bytes")))
    print("rx_chunks={}".format(result.get("rx_chunks")))
    print("tx_gpio14_released=True")
    print("tx_gate_gpio12_released=True")
    print("radios_inactive=True")
    print(pass_token)
    return result
