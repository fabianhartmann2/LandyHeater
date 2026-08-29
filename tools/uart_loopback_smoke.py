"""Manual UART2 loopback smoke test for a bare DFRobot DFR0654 board.

Safety conditions:

- the heater and every external circuit are disconnected;
- only GPIO17/D10 is jumpered to GPIO16/D11;
- the exact confirmation token is passed explicitly to ``run()``;
- importing this module never opens or writes to a UART.

The ASCII test payload is deliberately not an Autoterm frame.
"""

try:
    from time import sleep_ms as _platform_sleep_ms
    from time import ticks_diff as _platform_ticks_diff
    from time import ticks_ms as _platform_ticks_ms
except ImportError:  # CPython test support
    import time as _time

    def _platform_sleep_ms(milliseconds):
        _time.sleep(milliseconds / 1000)

    def _platform_ticks_ms():
        return int(_time.monotonic() * 1000)

    def _platform_ticks_diff(current, previous):
        return current - previous


LOOPBACK_CONFIRMATION = "GPIO17_TO_GPIO16_ONLY"
LOOPBACK_PAYLOAD = b"LANDY_UART2_LOOP"
DEFAULT_TIMEOUT_MS = 500
DEFAULT_QUIET_MS = 10


def _require_ready_config(config_module):
    config_module.require_uart_configuration()
    if config_module.BOARD_SKU != "DFR0654":
        raise RuntimeError("loopback tool supports only DFR0654")
    if (
        config_module.UART_ID,
        config_module.UART_TX_PIN,
        config_module.UART_RX_PIN,
    ) != (2, 17, 16):
        raise RuntimeError("loopback requires UART2 TX=17 RX=16")
    if (
        config_module.UART_BAUDRATE,
        config_module.UART_BITS,
        config_module.UART_PARITY,
        config_module.UART_STOP_BITS,
    ) != (9600, 8, None, 1):
        raise RuntimeError("loopback requires the 9600/8N1 UART profile")
    if (
        config_module.UART_DRIVER_TIMEOUT_MS,
        config_module.UART_DRIVER_TIMEOUT_CHAR_MS,
    ) != (0, 0):
        raise RuntimeError("loopback requires non-blocking UART timeouts")
    if config_module.UART_INVERT != 0:
        raise RuntimeError("loopback requires non-inverted UART signals")
    if (
        not isinstance(config_module.UART_RX_BUFFER_SIZE, int)
        or isinstance(config_module.UART_RX_BUFFER_SIZE, bool)
        or config_module.UART_RX_BUFFER_SIZE < len(LOOPBACK_PAYLOAD) + 1
    ):
        raise RuntimeError("loopback requires a valid UART RX buffer")
    if config_module.UART_PROTOCOL_TX_ENABLED is not False:
        raise RuntimeError(
            "protocol TX must remain disabled during board loopback"
        )


def _open_loopback_uart(config_module):
    """Open the raw UART only inside this explicitly armed diagnostic tool."""

    try:
        from machine import UART
    except ImportError:
        raise RuntimeError("machine.UART is only available on MicroPython")

    return UART(
        config_module.UART_ID,
        baudrate=config_module.UART_BAUDRATE,
        bits=config_module.UART_BITS,
        parity=config_module.UART_PARITY,
        stop=config_module.UART_STOP_BITS,
        tx=config_module.UART_TX_PIN,
        rx=config_module.UART_RX_PIN,
        timeout=config_module.UART_DRIVER_TIMEOUT_MS,
        timeout_char=config_module.UART_DRIVER_TIMEOUT_CHAR_MS,
        rxbuf=config_module.UART_RX_BUFFER_SIZE,
        invert=config_module.UART_INVERT,
        flow=0,
    )


def _require_available_count(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError("UART.any() returned an invalid value")
    return value


def run(
    confirmation,
    uart_factory=None,
    config_module=None,
    timeout_ms=DEFAULT_TIMEOUT_MS,
    quiet_ms=DEFAULT_QUIET_MS,
    ticks_ms=None,
    ticks_diff=None,
    sleep_ms=None,
):
    """Run one guarded loopback and return a small result dictionary."""

    if confirmation != LOOPBACK_CONFIRMATION:
        raise RuntimeError(
            "loopback not armed; confirm GPIO17-to-GPIO16 jumper only"
        )
    if (
        not isinstance(timeout_ms, int)
        or isinstance(timeout_ms, bool)
        or timeout_ms <= 0
    ):
        raise ValueError("timeout_ms must be a positive integer")
    if (
        not isinstance(quiet_ms, int)
        or isinstance(quiet_ms, bool)
        or quiet_ms <= 0
    ):
        raise ValueError("quiet_ms must be a positive integer")

    if config_module is None:
        import board_config as config_module

    _require_ready_config(config_module)
    ticks_ms = ticks_ms or _platform_ticks_ms
    ticks_diff = ticks_diff or _platform_ticks_diff
    sleep_ms = sleep_ms or _platform_sleep_ms
    if not all(callable(item) for item in (ticks_ms, ticks_diff, sleep_ms)):
        raise ValueError("clock and sleep functions must be callable")

    if uart_factory is None:
        uart_factory = lambda: _open_loopback_uart(config_module)
    if not callable(uart_factory):
        raise ValueError("uart_factory must be callable")

    uart = uart_factory()
    received = bytearray()
    start_ms = ticks_ms()
    deinit = getattr(uart, "deinit", None)
    if not callable(deinit):
        raise RuntimeError("UART must provide callable deinit()")
    try:
        for method_name in ("any", "read", "write"):
            if not callable(getattr(uart, method_name, None)):
                raise RuntimeError(
                    "UART must provide callable {}()".format(method_name)
                )

        available = _require_available_count(uart.any())
        if available:
            raise RuntimeError(
                "UART was not quiet before loopback; nothing was transmitted"
            )

        written = uart.write(LOOPBACK_PAYLOAD)
        if (
            not isinstance(written, int)
            or isinstance(written, bool)
            or written != len(LOOPBACK_PAYLOAD)
        ):
            raise RuntimeError("UART loopback write was incomplete")

        while len(received) < len(LOOPBACK_PAYLOAD):
            if ticks_diff(ticks_ms(), start_ms) >= timeout_ms:
                break

            available = _require_available_count(uart.any())
            if ticks_diff(ticks_ms(), start_ms) >= timeout_ms:
                break
            if available:
                chunk = uart.read(config_module.UART_RX_BUFFER_SIZE)
                if ticks_diff(ticks_ms(), start_ms) >= timeout_ms:
                    break
                if chunk is not None and chunk != b"":
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise RuntimeError("UART.read() returned non-byte data")
                    received.extend(chunk)
                    if not LOOPBACK_PAYLOAD.startswith(bytes(received)):
                        raise RuntimeError("UART loopback returned unexpected bytes")

            if len(received) >= len(LOOPBACK_PAYLOAD):
                break
            sleep_ms(2)

        if bytes(received) == LOOPBACK_PAYLOAD:
            quiet_start_ms = ticks_ms()
            while ticks_diff(ticks_ms(), quiet_start_ms) < quiet_ms:
                available = _require_available_count(uart.any())
                if available:
                    chunk = uart.read(config_module.UART_RX_BUFFER_SIZE)
                    if chunk not in (None, b"") and not isinstance(
                        chunk, (bytes, bytearray, memoryview)
                    ):
                        raise RuntimeError(
                            "UART.read() returned non-byte data"
                        )
                    if chunk not in (None, b""):
                        received.extend(chunk)
                    raise RuntimeError(
                        "UART was not quiet after the loopback echo"
                    )
                sleep_ms(2)
    finally:
        deinit()

    elapsed_ms = ticks_diff(ticks_ms(), start_ms)
    if bytes(received) != LOOPBACK_PAYLOAD:
        raise RuntimeError(
            "UART loopback failed: expected {!r}, received {!r}".format(
                LOOPBACK_PAYLOAD, bytes(received)
            )
        )

    result = {
        "board_sku": config_module.BOARD_SKU,
        "uart_id": config_module.UART_ID,
        "tx_pin": config_module.UART_TX_PIN,
        "rx_pin": config_module.UART_RX_PIN,
        "bytes": len(LOOPBACK_PAYLOAD),
        "elapsed_ms": elapsed_ms,
    }
    print("DFR0654 UART2 loopback PASS: {} bytes".format(result["bytes"]))
    return result
