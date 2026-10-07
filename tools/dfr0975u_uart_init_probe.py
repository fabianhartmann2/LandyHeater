"""One-shot DFR0975-U Autoterm INIT diagnostic.

Importing this module is inert.  The target runner is locked to an exact
hardware confirmation, temporarily opens the three UART/TX approvals only in
RAM, emits exactly one canonical INIT request and waits for one CRC-valid INIT
response.  It contains no START, SHUTDOWN, temperature or retry path.
"""

import sys as _sys


CONFIRMATION = (
    "DFR0975U_TX_GATE_VERIFIED_RX13_TX14_GATE12_PULLDOWN_"
    "HEATER_SAFE_INIT_ONLY_V1"
)
PASS_TOKEN = "DFR0975U_UART_INIT_ONE_SHOT_PASS_V1"
EXPECTED_MACHINE = "DFRobot DFR0975-U N16R8 with ESP32S3"
EXPECTED_REQUEST = bytes((0xAA, 0x03, 0x00, 0x00, 0x04, 0x9F, 0x3D))
DEFAULT_RESPONSE_TIMEOUT_MS = 10000
POLL_INTERVAL_MS = 2


def _require(condition, message):
    if not condition:
        raise RuntimeError("DFR0975-U INIT gate failed: {}".format(message))


def validate_init_response(raw):
    """Return one canonical heater INIT response or raise."""

    from protocol.autoterm_protocol import CMD_INIT, DEVICE_HEATER, parse_frame

    parsed = parse_frame(raw)
    _require(parsed.get("device") == DEVICE_HEATER, "response is not from heater")
    _require(parsed.get("command") == CMD_INIT, "response is not INIT")
    _require(parsed.get("reserved") == 0, "response reserved byte differs")
    _require(parsed.get("crc_valid") is True, "response CRC is invalid")
    _require(parsed.get("payload_length") == len(parsed.get("payload", b"")),
             "response payload length differs")
    return parsed


def _wait_for_init_response(
    transport,
    timeout_ms,
    ticks_ms,
    ticks_diff,
    sleep_ms,
):
    started_ms = ticks_ms()
    rejected = 0
    while ticks_diff(ticks_ms(), started_ms) < timeout_ms:
        frames = transport.poll(ticks_ms())
        for raw in frames:
            try:
                return validate_init_response(raw), rejected
            except (ValueError, RuntimeError):
                rejected += 1
        sleep_ms(POLL_INTERVAL_MS)
    raise RuntimeError(
        "DFR0975-U INIT gate failed: no valid INIT response; rejected={}".format(
            rejected
        )
    )


def _radios_inactive(network_module):
    return (
        network_module.WLAN(network_module.STA_IF).active() is False
        and network_module.WLAN(network_module.AP_IF).active() is False
    )


def run(confirmation, response_timeout_ms=DEFAULT_RESPONSE_TIMEOUT_MS):
    """Transmit one INIT request after exact external hardware approval."""

    _require(confirmation == CONFIRMATION, "exact confirmation is required")
    _require(
        type(response_timeout_ms) is int
        and 1 <= response_timeout_ms <= DEFAULT_RESPONSE_TIMEOUT_MS,
        "response timeout is outside its bound",
    )
    _require(_sys.platform == "esp32", "target is not ESP32 MicroPython")

    import board_config
    import network
    from machine import Pin
    from os import uname
    from time import sleep_ms, ticks_diff, ticks_ms
    from protocol.autoterm_protocol import build_init_request
    from protocol.uart_transport import open_from_board_config

    _require(
        getattr(uname(), "machine", None) == EXPECTED_MACHINE,
        "machine identity differs",
    )
    _require(_radios_inactive(network), "a WLAN interface is active")
    _require(build_init_request() == EXPECTED_REQUEST, "INIT request differs")

    approval_names = (
        "UART_PINS_APPROVED",
        "UART_TX_GATE_APPROVED",
        "UART_PROTOCOL_TX_ENABLED",
    )
    for name in approval_names:
        _require(getattr(board_config, name, None) is False,
                 "{} must start closed".format(name))

    transport = None
    response = None
    rejected = None
    primary_error = None
    cleanup_errors = []
    try:
        for name in approval_names:
            setattr(board_config, name, True)
        transport = open_from_board_config(config_module=board_config)
        written = transport.send_frame(EXPECTED_REQUEST)
        _require(written == len(EXPECTED_REQUEST), "INIT write was incomplete")
        response, rejected = _wait_for_init_response(
            transport,
            response_timeout_ms,
            ticks_ms,
            ticks_diff,
            sleep_ms,
        )
    except BaseException as error:
        primary_error = error
    finally:
        if transport is not None:
            try:
                transport.deinit()
            except BaseException as error:
                cleanup_errors.append(error)
        try:
            Pin(board_config.UART_TX_PIN, Pin.IN, pull=None, hold=False)
            Pin(board_config.UART_TX_GATE_PIN, Pin.IN, pull=None, hold=False)
        except BaseException as error:
            cleanup_errors.append(error)
        for name in approval_names:
            setattr(board_config, name, False)

    if cleanup_errors:
        raise RuntimeError(
            "DFR0975-U INIT cleanup failed: {}".format(
                "; ".join(str(error) for error in cleanup_errors)
            )
        )
    if primary_error is not None:
        raise primary_error
    _require(_radios_inactive(network), "a WLAN interface became active")
    for name in approval_names:
        _require(getattr(board_config, name, None) is False,
                 "{} did not close".format(name))

    raw = response["raw"]
    print("request_hex={}".format(EXPECTED_REQUEST.hex()))
    print("response_hex={}".format(raw.hex()))
    print("response_payload_hex={}".format(response["payload"].hex()))
    print("rejected_frames={}".format(rejected))
    print("tx_gate_gpio12_released=True")
    print("tx_gpio14_released=True")
    print("radios_inactive=True")
    print(PASS_TOKEN)
    return {
        "request": EXPECTED_REQUEST,
        "response": bytes(raw),
        "payload": bytes(response["payload"]),
        "rejected_frames": rejected,
        "complete": True,
    }
