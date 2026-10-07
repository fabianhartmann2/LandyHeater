"""Two-stage INIT probes for the proven passive level shifter.

This is a bounded bench-only exception to the product TX-gate architecture.
Importing the module is inert. ``arm()`` opens UART2 with heater power off and
does not write. ``trigger()`` can then emit exactly one canonical INIT request.
The separately confirmed ``trigger_bounded()`` mirrors the old controller only
far enough to emit at most three INIT requests one second apart. Neither path
has a dangerous-command surface. Product approval flags remain closed.
"""

import sys as _sys


ARM_CONFIRMATION = (
    "DFR0975U_USB_ONLY_DIRECT_LEVEL_SHIFTER_GREEN_RX13_WHITE_TX14_"
    "D12_DISCONNECTED_HEATER_OFF_V1"
)
TRIGGER_CONFIRMATION = (
    "DFR0975U_DIRECT_LEVEL_SHIFTER_HEATER_12V_IDLE_INIT_ONCE_V1"
)
BOUNDED_TRIGGER_CONFIRMATION = (
    "DFR0975U_DIRECT_LEVEL_SHIFTER_HEATER_12V_IDLE_INIT_MAX3_V1"
)
ARMED_TOKEN = "DFR0975U_DIRECT_UART_ARMED_NO_WRITE_V1"
PASS_TOKEN = "DFR0975U_DIRECT_UART_INIT_ONE_SHOT_PASS_V1"
BOUNDED_PASS_TOKEN = "DFR0975U_DIRECT_UART_INIT_MAX3_PASS_V1"
EXPECTED_MACHINE = "DFRobot DFR0975-U N16R8 with ESP32S3"
EXPECTED_REQUEST = bytes((0xAA, 0x03, 0x00, 0x00, 0x04, 0x9F, 0x3D))
RESPONSE_TIMEOUT_MS = 10000
INIT_RETRY_INTERVAL_MS = 1000
MAX_BOUNDED_INIT_ATTEMPTS = 3
TX_DRAIN_TIMEOUT_MS = 500
POLL_INTERVAL_MS = 2

_uart = None
_armed = False


def _require(condition, message):
    if not condition:
        raise RuntimeError("DFR0975-U direct INIT gate failed: {}".format(message))


def _radios_inactive(network_module):
    return (
        network_module.WLAN(network_module.STA_IF).active() is False
        and network_module.WLAN(network_module.AP_IF).active() is False
    )


def _require_closed_product_flags(board_config):
    for name in (
        "UART_PINS_APPROVED",
        "UART_TX_GATE_APPROVED",
        "UART_PROTOCOL_TX_ENABLED",
    ):
        _require(getattr(board_config, name, None) is False,
                 "{} must remain closed".format(name))


def validate_init_response(raw):
    from protocol.autoterm_protocol import CMD_INIT, DEVICE_HEATER, parse_frame

    parsed = parse_frame(raw)
    _require(parsed.get("device") == DEVICE_HEATER, "response is not from heater")
    _require(parsed.get("command") == CMD_INIT, "response is not INIT")
    _require(parsed.get("reserved") == 0, "response reserved byte differs")
    _require(parsed.get("crc_valid") is True, "response CRC is invalid")
    _require(parsed.get("payload_length") == len(parsed.get("payload", b"")),
             "response payload length differs")
    return parsed


def _release():
    """Close UART and release TX/gate pins; safe to repeat."""

    global _uart, _armed
    errors = []
    uart = _uart
    _uart = None
    _armed = False
    if uart is not None:
        try:
            uart.deinit()
        except BaseException as error:
            errors.append(error)
    try:
        import board_config
        from machine import Pin

        Pin(board_config.UART_TX_PIN, Pin.IN, pull=None, hold=False)
        Pin(board_config.UART_TX_GATE_PIN, Pin.IN, pull=None, hold=False)
    except BaseException as error:
        errors.append(error)
    if errors:
        raise RuntimeError(
            "DFR0975-U direct INIT cleanup failed: {}".format(
                "; ".join(str(error) for error in errors)
            )
        )


def arm(confirmation):
    """Open UART with heater power off, holding TX at idle; never write."""

    global _uart, _armed
    _require(confirmation == ARM_CONFIRMATION, "exact arm confirmation required")
    _require(_sys.platform == "esp32", "target is not ESP32 MicroPython")
    _require(not _armed and _uart is None, "probe is already armed")

    import board_config
    import network
    from machine import Pin, UART
    from os import uname

    _require(getattr(uname(), "machine", None) == EXPECTED_MACHINE,
             "machine identity differs")
    _require(_radios_inactive(network), "a WLAN interface is active")
    _require_closed_product_flags(board_config)
    _require(
        (
            board_config.UART_ID,
            board_config.UART_TX_PIN,
            board_config.UART_RX_PIN,
            board_config.UART_TX_GATE_PIN,
        ) == (2, 14, 13, 12),
        "UART pin profile differs",
    )
    _require(
        (
            board_config.UART_BAUDRATE,
            board_config.UART_BITS,
            board_config.UART_PARITY,
            board_config.UART_STOP_BITS,
            board_config.UART_INVERT,
        ) == (9600, 8, None, 1, 0),
        "UART serial profile differs",
    )

    # Establish UART idle-high before assigning the peripheral. The level
    # shifter's own pull-ups hold the line high during reset and mode changes.
    Pin(board_config.UART_TX_GATE_PIN, Pin.IN, pull=None, hold=False)
    rx_sense = Pin(board_config.UART_RX_PIN, Pin.IN, pull=None, hold=False)
    tx_sense = Pin(board_config.UART_TX_PIN, Pin.IN, pull=None, hold=False)
    _require(rx_sense.value() == 1, "green RX line is not idle-high")
    _require(tx_sense.value() == 1, "white TX line is not idle-high")
    Pin(board_config.UART_TX_PIN, Pin.OUT, value=1, pull=None, hold=False)
    uart = None
    try:
        uart = UART(
            board_config.UART_ID,
            baudrate=board_config.UART_BAUDRATE,
            bits=board_config.UART_BITS,
            parity=board_config.UART_PARITY,
            stop=board_config.UART_STOP_BITS,
            tx=board_config.UART_TX_PIN,
            rx=board_config.UART_RX_PIN,
            timeout=board_config.UART_DRIVER_TIMEOUT_MS,
            timeout_char=board_config.UART_DRIVER_TIMEOUT_CHAR_MS,
            rxbuf=board_config.UART_RX_BUFFER_SIZE,
            invert=board_config.UART_INVERT,
            flow=0,
        )
        _require(callable(getattr(uart, "txdone", None)),
                 "UART.txdone() is unavailable")
        _require(uart.txdone() is True, "UART is not idle after arming")
        _uart = uart
        _armed = True
    except BaseException:
        if uart is not None:
            try:
                uart.deinit()
            except BaseException:
                pass
        Pin(board_config.UART_TX_PIN, Pin.IN, pull=None, hold=False)
        Pin(board_config.UART_TX_GATE_PIN, Pin.IN, pull=None, hold=False)
        raise

    print("writes=0")
    print("rx_idle_high=True")
    print("tx_idle_high=True")
    print("tx_gate_gpio12_disconnected=True")
    print("radios_inactive=True")
    print(ARMED_TOKEN)
    return True


def _trigger(confirmation, expected_confirmation, max_attempts, pass_token):
    """Emit a fixed maximum of INIT requests and require a valid response."""

    global _uart, _armed
    _require(confirmation == expected_confirmation,
             "exact trigger confirmation required")
    _require(_sys.platform == "esp32", "target is not ESP32 MicroPython")
    _require(_armed and _uart is not None, "probe is not armed")

    import board_config
    import network
    from machine import Pin
    from time import sleep_ms, ticks_diff, ticks_ms
    from protocol.autoterm_frames import RawFrameStreamParser
    from protocol.autoterm_protocol import build_init_request

    _require_closed_product_flags(board_config)
    _require(_radios_inactive(network), "a WLAN interface is active")
    _require(build_init_request() == EXPECTED_REQUEST, "INIT request differs")
    _require(type(max_attempts) is int and 1 <= max_attempts <= 3,
             "INIT attempt bound differs")

    uart = _uart
    parsed = None
    rejected = 0
    attempts_sent = 0
    primary_error = None
    try:
        parser = RawFrameStreamParser()
        for attempt_index in range(max_attempts):
            # Read the actual pads without changing their UART configuration.
            # A forced-low line aborts before every individual write.
            _require(Pin(board_config.UART_RX_PIN).value() == 1,
                     "green RX line is not idle-high immediately before write")
            _require(Pin(board_config.UART_TX_PIN).value() == 1,
                     "white TX line is not idle-high immediately before write")

            written = uart.write(EXPECTED_REQUEST)
            _require(type(written) is int and written == len(EXPECTED_REQUEST),
                     "INIT write was incomplete")
            attempts_sent += 1

            drain_started = ticks_ms()
            while uart.txdone() is not True:
                _require(
                    ticks_diff(ticks_ms(), drain_started) < TX_DRAIN_TIMEOUT_MS,
                    "UART TX drain timed out",
                )
                sleep_ms(POLL_INTERVAL_MS)

            wait_ms = (
                RESPONSE_TIMEOUT_MS
                if attempt_index + 1 == max_attempts
                else INIT_RETRY_INTERVAL_MS
            )
            response_started = ticks_ms()
            while ticks_diff(ticks_ms(), response_started) < wait_ms:
                available = uart.any()
                _require(type(available) is int and available >= 0,
                         "UART.any() returned an invalid value")
                if available:
                    chunk = uart.read(board_config.UART_MAX_READ_BYTES)
                    _require(isinstance(chunk, (bytes, bytearray, memoryview)),
                             "UART.read() returned invalid data")
                    for raw in parser.feed(bytes(chunk)):
                        try:
                            parsed = validate_init_response(raw)
                        except (ValueError, RuntimeError):
                            rejected += 1
                            continue
                        break
                    if parsed is not None:
                        break
                sleep_ms(POLL_INTERVAL_MS)
                if parsed is not None:
                    break
            if parsed is not None:
                break
        _require(parsed is not None,
                 "no valid INIT response; attempts={}; rejected={}".format(
                     attempts_sent, rejected
                 ))
    except BaseException as error:
        primary_error = error

    cleanup_error = None
    try:
        _release()
    except BaseException as error:
        cleanup_error = error

    if cleanup_error is not None:
        raise cleanup_error
    if primary_error is not None:
        raise primary_error
    _require_closed_product_flags(board_config)
    _require(_radios_inactive(network), "a WLAN interface became active")

    print("request_hex={}".format(EXPECTED_REQUEST.hex()))
    print("response_hex={}".format(parsed["raw"].hex()))
    print("response_payload_hex={}".format(parsed["payload"].hex()))
    print("attempts_sent={}".format(attempts_sent))
    print("rejected_frames={}".format(rejected))
    print("tx_gpio14_released=True")
    print("tx_gate_gpio12_released=True")
    print("radios_inactive=True")
    print(pass_token)
    return {
        "request": EXPECTED_REQUEST,
        "response": bytes(parsed["raw"]),
        "payload": bytes(parsed["payload"]),
        "attempts_sent": attempts_sent,
        "rejected_frames": rejected,
        "complete": True,
    }


def trigger(confirmation):
    """Emit exactly one INIT request and require one valid response."""

    return _trigger(confirmation, TRIGGER_CONFIRMATION, 1, PASS_TOKEN)


def trigger_bounded(confirmation):
    """Emit at most three INIT requests, one second apart."""

    return _trigger(
        confirmation,
        BOUNDED_TRIGGER_CONFIRMATION,
        MAX_BOUNDED_INIT_ATTEMPTS,
        BOUNDED_PASS_TOKEN,
    )


def cancel():
    """Release an armed probe without sending anything."""

    _release()
    print("DFR0975U_DIRECT_UART_CANCELLED_NO_WRITE_V1")
    return True
