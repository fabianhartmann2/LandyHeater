"""One INIT followed by one STATUS request for the proven bench wiring.

This source-mounted Phase-13 diagnostic is inert on import.  It reuses the
strict DFR0975-U arm/cleanup gate from the direct INIT probe, keeps all product
approval flags closed and has no START, SHUTDOWN or temperature command path.
"""

import sys as _sys

from tools import dfr0975u_uart_direct_init_probe as _init_probe


ARM_CONFIRMATION = (
    "DFR0975U_USB_ONLY_DIRECT_LEVEL_SHIFTER_GREEN_RX13_WHITE_TX14_"
    "D12_DISCONNECTED_HEATER_OFF_STATUS_V1"
)
TRIGGER_CONFIRMATION = (
    "DFR0975U_DIRECT_LEVEL_SHIFTER_HEATER_12V_IDLE_INIT_THEN_STATUS_ONCE_V1"
)
ARMED_TOKEN = "DFR0975U_DIRECT_UART_STATUS_ARMED_NO_WRITE_V1"
PASS_TOKEN = "DFR0975U_DIRECT_UART_INIT_THEN_STATUS_PASS_V1"
EXPECTED_INIT_REQUEST = bytes((0xAA, 0x03, 0x00, 0x00, 0x04, 0x9F, 0x3D))
EXPECTED_STATUS_REQUEST = bytes((0xAA, 0x03, 0x00, 0x00, 0x0F, 0x58, 0x7C))
RESPONSE_TIMEOUT_MS = 10000
TX_DRAIN_TIMEOUT_MS = 500
POLL_INTERVAL_MS = 2


def _require(condition, message):
    if not condition:
        raise RuntimeError("DFR0975-U direct STATUS gate failed: {}".format(message))


def validate_status_response(raw):
    from protocol.autoterm_protocol import CMD_STATUS, DEVICE_HEATER, parse_frame

    parsed = parse_frame(raw)
    _require(parsed.get("device") == DEVICE_HEATER,
             "response is not from heater")
    _require(parsed.get("command") == CMD_STATUS, "response is not STATUS")
    _require(parsed.get("reserved") == 0, "response reserved byte differs")
    _require(parsed.get("crc_valid") is True, "response CRC is invalid")
    _require(parsed.get("payload_length") == len(parsed.get("payload", b"")),
             "response payload length differs")
    _require(isinstance(parsed.get("status"), dict),
             "response has no decoded status fields")
    return parsed


def arm(confirmation):
    """Open UART with heater power off; never write."""

    _require(confirmation == ARM_CONFIRMATION,
             "exact arm confirmation required")
    result = _init_probe.arm(_init_probe.ARM_CONFIRMATION)
    print(ARMED_TOKEN)
    return result


def _write_exact(uart, frame, ticks_ms, ticks_diff, sleep_ms):
    written = uart.write(frame)
    _require(type(written) is int and written == len(frame),
             "UART write was incomplete")
    started = ticks_ms()
    while uart.txdone() is not True:
        _require(ticks_diff(ticks_ms(), started) < TX_DRAIN_TIMEOUT_MS,
                 "UART TX drain timed out")
        sleep_ms(POLL_INTERVAL_MS)


def _read_expected(
    uart,
    validator,
    board_config,
    ticks_ms,
    ticks_diff,
    sleep_ms,
):
    from protocol.autoterm_frames import RawFrameStreamParser

    parser = RawFrameStreamParser()
    rejected = 0
    started = ticks_ms()
    while ticks_diff(ticks_ms(), started) < RESPONSE_TIMEOUT_MS:
        available = uart.any()
        _require(type(available) is int and available >= 0,
                 "UART.any() returned an invalid value")
        if available:
            chunk = uart.read(board_config.UART_MAX_READ_BYTES)
            _require(isinstance(chunk, (bytes, bytearray, memoryview)),
                     "UART.read() returned invalid data")
            for raw in parser.feed(bytes(chunk)):
                try:
                    return validator(raw), rejected
                except (ValueError, RuntimeError):
                    rejected += 1
        sleep_ms(POLL_INTERVAL_MS)
    raise RuntimeError(
        "no valid response; rejected={}".format(rejected)
    )


def trigger(confirmation):
    """Send one INIT, require its response, then send one STATUS request."""

    _require(confirmation == TRIGGER_CONFIRMATION,
             "exact trigger confirmation required")
    _require(_sys.platform == "esp32", "target is not ESP32 MicroPython")
    _require(_init_probe._armed and _init_probe._uart is not None,
             "probe is not armed")

    import board_config
    import network
    from machine import Pin
    from time import sleep_ms, ticks_diff, ticks_ms
    from protocol.autoterm_protocol import (
        build_init_request,
        build_status_request,
    )

    uart = _init_probe._uart
    init_response = None
    status_response = None
    init_rejected = 0
    status_rejected = 0
    primary_error = None
    try:
        _init_probe._require_closed_product_flags(board_config)
        _require(_init_probe._radios_inactive(network),
                 "a WLAN interface is active")
        _require(build_init_request() == EXPECTED_INIT_REQUEST,
                 "INIT request differs")
        _require(build_status_request() == EXPECTED_STATUS_REQUEST,
                 "STATUS request differs")

        _require(Pin(board_config.UART_RX_PIN).value() == 1,
                 "green RX line is not idle-high before INIT")
        _require(Pin(board_config.UART_TX_PIN).value() == 1,
                 "white TX line is not idle-high before INIT")
        _write_exact(uart, EXPECTED_INIT_REQUEST, ticks_ms, ticks_diff, sleep_ms)
        init_response, init_rejected = _read_expected(
            uart,
            _init_probe.validate_init_response,
            board_config,
            ticks_ms,
            ticks_diff,
            sleep_ms,
        )

        _require(Pin(board_config.UART_RX_PIN).value() == 1,
                 "green RX line is not idle-high before STATUS")
        _require(Pin(board_config.UART_TX_PIN).value() == 1,
                 "white TX line is not idle-high before STATUS")
        _write_exact(uart, EXPECTED_STATUS_REQUEST, ticks_ms, ticks_diff, sleep_ms)
        status_response, status_rejected = _read_expected(
            uart,
            validate_status_response,
            board_config,
            ticks_ms,
            ticks_diff,
            sleep_ms,
        )
    except BaseException as error:
        primary_error = error

    cleanup_error = None
    try:
        _init_probe._release()
    except BaseException as error:
        cleanup_error = error

    if cleanup_error is not None:
        raise cleanup_error
    if primary_error is not None:
        raise primary_error
    _init_probe._require_closed_product_flags(board_config)
    _require(_init_probe._radios_inactive(network),
             "a WLAN interface became active")

    status = status_response["status"]
    print("init_request_hex={}".format(EXPECTED_INIT_REQUEST.hex()))
    print("init_response_hex={}".format(init_response["raw"].hex()))
    print("status_request_hex={}".format(EXPECTED_STATUS_REQUEST.hex()))
    print("status_response_hex={}".format(status_response["raw"].hex()))
    print("heater_state={}".format(status.get("heater_state_name")))
    print("voltage={}".format(status.get("voltage")))
    print("init_rejected_frames={}".format(init_rejected))
    print("status_rejected_frames={}".format(status_rejected))
    print("tx_gpio14_released=True")
    print("tx_gate_gpio12_released=True")
    print("radios_inactive=True")
    print(PASS_TOKEN)
    return {
        "init_request": EXPECTED_INIT_REQUEST,
        "init_response": bytes(init_response["raw"]),
        "status_request": EXPECTED_STATUS_REQUEST,
        "status_response": bytes(status_response["raw"]),
        "status": dict(status),
        "init_rejected_frames": init_rejected,
        "status_rejected_frames": status_rejected,
        "complete": True,
    }


def cancel():
    """Release an armed probe without sending anything."""

    return _init_probe.cancel()
