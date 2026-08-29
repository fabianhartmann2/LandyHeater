"""Raw receive-only UART path for passive Autoterm captures.

This module deliberately provides no protocol framing and no transmission
method.  On ESP32 MicroPython, UART2 always maps its TX signal to GPIO17 while
the driver is constructed.  The guarded factory therefore returns GPIO17 to
``Pin.IN`` before opening the UART, immediately afterwards, on setup errors,
and again during deinitialization.

That software measure is defense in depth only.  GPIO17/D10 must remain
physically disconnected from the heater bus and level converter.
"""

try:
    from time import ticks_ms as _platform_ticks_ms
except ImportError:  # CPython
    import time as _time

    def _platform_ticks_ms():
        return int(_time.monotonic() * 1000)


class RXOnlyTransportError(Exception):
    """Base class for receive-only transport failures."""


class _BoundedChunkQueue:
    """Fixed-size queue that keeps the newest raw UART chunks."""

    def __init__(self, capacity):
        self.capacity = capacity
        self._items = [None] * capacity
        self._head = 0
        self._size = 0
        self.dropped_chunks = 0
        self.dropped_bytes = 0

    def append(self, item):
        if self._size >= self.capacity:
            dropped = self._items[self._head]
            self._items[self._head] = None
            self._head = (self._head + 1) % self.capacity
            self._size -= 1
            self.dropped_chunks += 1
            self.dropped_bytes += len(dropped[2])

        index = (self._head + self._size) % self.capacity
        self._items[index] = item
        self._size += 1

    def pop(self):
        if self._size == 0:
            return None
        item = self._items[self._head]
        self._items[self._head] = None
        self._head = (self._head + 1) % self.capacity
        self._size -= 1
        return item

    def __len__(self):
        return self._size


class _RXOnlyUARTReader:
    """Narrow internal facade: only receive and cleanup methods exist."""

    def __init__(self, uart, neutralize_tx):
        self._uart = uart
        self._neutralize_tx = neutralize_tx
        self._poll_closed = False
        self._driver_closed = False
        self._cleanup_complete = False

    def any(self):
        if self._poll_closed:
            raise RXOnlyTransportError("RX-only UART is closed")
        return self._uart.any()

    def read(self, count):
        if self._poll_closed:
            raise RXOnlyTransportError("RX-only UART is closed")
        return self._uart.read(count)

    def deinit(self):
        if self._cleanup_complete:
            return

        # Stop all future reads immediately, but keep cleanup retryable until
        # both the driver close and GPIO17 neutralization have succeeded.
        self._poll_closed = True
        driver_error = None
        if not self._driver_closed:
            try:
                self._uart.deinit()
                self._driver_closed = True
            except Exception as error:
                driver_error = error

        try:
            self._neutralize_tx()
        except Exception as neutralize_error:
            raise RXOnlyTransportError(
                "failed to neutralize GPIO17 after UART deinit: {}".format(
                    neutralize_error
                )
            )

        if driver_error is not None:
            raise driver_error
        self._cleanup_complete = True


class RXOnlyTransport:
    """Poll raw UART bytes without exposing ``write`` or protocol semantics."""

    def __init__(
        self,
        reader,
        max_read_bytes=128,
        queue_capacity=64,
        max_empty_ready_reads=3,
        ticks_ms=None,
    ):
        if reader is None:
            raise ValueError("reader is required")
        for method_name in ("any", "read", "deinit"):
            if not callable(getattr(reader, method_name, None)):
                raise ValueError(
                    "reader must provide callable {}()".format(method_name)
                )
        for forbidden_name in ("write", "init", "sendbreak"):
            if callable(getattr(reader, forbidden_name, None)):
                raise ValueError(
                    "reader must not expose {}()".format(forbidden_name)
                )
        if (
            not isinstance(max_read_bytes, int)
            or isinstance(max_read_bytes, bool)
            or max_read_bytes <= 0
        ):
            raise ValueError("max_read_bytes must be a positive integer")
        if (
            not isinstance(queue_capacity, int)
            or isinstance(queue_capacity, bool)
            or queue_capacity <= 0
        ):
            raise ValueError("queue_capacity must be a positive integer")
        if (
            not isinstance(max_empty_ready_reads, int)
            or isinstance(max_empty_ready_reads, bool)
            or max_empty_ready_reads <= 0
        ):
            raise ValueError(
                "max_empty_ready_reads must be a positive integer"
            )

        self._reader = reader
        self._queue = _BoundedChunkQueue(queue_capacity)
        self._ticks_ms = ticks_ms or _platform_ticks_ms
        if not callable(self._ticks_ms):
            raise ValueError("ticks_ms must be callable")

        self.max_read_bytes = max_read_bytes
        self.max_empty_ready_reads = max_empty_ready_reads
        self.rx_bytes = 0
        self.rx_chunks = 0
        self.read_errors = 0
        self.empty_ready_reads = 0
        self.consecutive_empty_ready_reads = 0
        self.rx_faulted = False
        self.closed = False
        self.cleanup_complete = False
        self.last_rx_ms = None
        self.last_error = None
        self._next_sequence = 0

    def _record_error(self, operation, error):
        self.read_errors += 1
        self.last_error = {
            "operation": operation,
            "type": error.__class__.__name__,
            "message": str(error),
        }

    def _record_empty_ready_read(self):
        self.read_errors += 1
        self.empty_ready_reads += 1
        self.consecutive_empty_ready_reads += 1
        if self.consecutive_empty_ready_reads >= self.max_empty_ready_reads:
            self.rx_faulted = True
        self.last_error = {
            "operation": "read",
            "type": "empty_ready_read",
            "message": "UART.any() reported data but read() returned none",
            "consecutive": self.consecutive_empty_ready_reads,
            "rx_faulted": self.rx_faulted,
        }

    def poll(self, now_ms=None):
        """Perform one bounded read and return the new immutable chunk list."""

        if self.closed:
            raise RXOnlyTransportError("RX-only transport is closed")
        if self.rx_faulted:
            return []
        if now_ms is None:
            now_ms = self._ticks_ms()

        try:
            available = self._reader.any()
        except Exception as error:
            self._record_error("any", error)
            return []

        if (
            not isinstance(available, int)
            or isinstance(available, bool)
            or available < 0
        ):
            self._record_error(
                "any",
                TypeError("UART.any() must return a non-negative integer"),
            )
            return []

        if available == 0:
            self.consecutive_empty_ready_reads = 0
            return []

        try:
            data = self._reader.read(self.max_read_bytes)
        except Exception as error:
            self._record_error("read", error)
            return []

        if data is None or data == b"":
            self._record_empty_ready_read()
            return []
        if not isinstance(data, (bytes, bytearray, memoryview)):
            self._record_error(
                "read", TypeError("UART.read() must return bytes or None")
            )
            return []

        try:
            raw = bytes(data)
        except (TypeError, ValueError) as error:
            self._record_error("read", error)
            return []
        if not raw:
            self._record_empty_ready_read()
            return []

        self.consecutive_empty_ready_reads = 0
        sequence = self._next_sequence
        self._next_sequence += 1
        item = (sequence, now_ms, raw)
        self._queue.append(item)
        self.rx_bytes += len(raw)
        self.rx_chunks += 1
        self.last_rx_ms = now_ms
        return [raw]

    def drain_chunks(self, max_chunks=None):
        """Return queued ``(sequence, timestamp_ms, bytes)`` records."""

        if max_chunks is not None and (
            not isinstance(max_chunks, int)
            or isinstance(max_chunks, bool)
            or max_chunks < 0
        ):
            raise ValueError("max_chunks must be a non-negative integer or None")

        chunks = []
        while len(self._queue) and (
            max_chunks is None or len(chunks) < max_chunks
        ):
            chunks.append(self._queue.pop())
        return chunks

    def status(self):
        """Return capture diagnostics; no protocol fields are interpreted."""

        return {
            "rx_bytes": self.rx_bytes,
            "rx_chunks": self.rx_chunks,
            "read_errors": self.read_errors,
            "empty_ready_reads": self.empty_ready_reads,
            "consecutive_empty_ready_reads": self.consecutive_empty_ready_reads,
            "rx_faulted": self.rx_faulted,
            "queued_chunks": len(self._queue),
            "dropped_chunks": self._queue.dropped_chunks,
            "dropped_bytes": self._queue.dropped_bytes,
            "complete": (
                self._queue.dropped_chunks == 0
                and self.read_errors == 0
                and not self.rx_faulted
            ),
            "closed": self.closed,
            "cleanup_complete": self.cleanup_complete,
            "last_rx_ms": self.last_rx_ms,
            "last_error": (
                dict(self.last_error) if self.last_error is not None else None
            ),
        }

    def deinit(self):
        """Close the UART and neutralize GPIO17 again; safe to repeat."""

        if self.cleanup_complete:
            return
        self.closed = True
        try:
            self._reader.deinit()
        except Exception:
            self.cleanup_complete = False
            raise
        self.cleanup_complete = True


def _require_rx_only_configuration(config_module):
    """Validate every safety-critical value before touching GPIO or UART."""

    config_module.require_uart_configuration()
    if config_module.BOARD_SKU != "DFR0654":
        raise RuntimeError("RX-only capture supports only DFR0654")
    if (
        config_module.UART_ID,
        config_module.UART_TX_PIN,
        config_module.UART_RX_PIN,
    ) != (2, 17, 16):
        raise RuntimeError("RX-only capture requires UART2 TX=17 RX=16")
    if (
        config_module.UART_BAUDRATE,
        config_module.UART_BITS,
        config_module.UART_PARITY,
        config_module.UART_STOP_BITS,
    ) != (9600, 8, None, 1):
        raise RuntimeError("RX-only capture requires 9600/8N1")
    if config_module.UART_PROTOCOL_TX_ENABLED is not False:
        raise RuntimeError("protocol TX must be exactly False")
    if (
        config_module.UART_DRIVER_TIMEOUT_MS,
        config_module.UART_DRIVER_TIMEOUT_CHAR_MS,
    ) != (0, 0):
        raise RuntimeError("RX-only capture requires non-blocking UART")
    if config_module.UART_INVERT != 0:
        raise RuntimeError("RX-only capture requires non-inverted UART")

    capture_profile = (
        config_module.UART_RX_ONLY_BUFFER_SIZE,
        config_module.UART_RX_ONLY_MAX_READ_BYTES,
        config_module.UART_RX_ONLY_QUEUE_CAPACITY,
        config_module.UART_RX_ONLY_MAX_EMPTY_READY_READS,
    )
    if capture_profile != (2048, 128, 64, 3):
        raise RuntimeError(
            "DFR0654 RX-only profile must be buffer=2048, chunk=128, "
            "queue=64, empty-ready-limit=3"
        )


def _neutralize_tx(pin_class, pin_number):
    input_mode = getattr(pin_class, "IN", None)
    if input_mode is None:
        raise RuntimeError("machine.Pin.IN is unavailable")
    # ESP32 pad hold can preserve an earlier output configuration while later
    # Pin calls appear to succeed.  Explicit hold=False releases that state and
    # applies the input/no-pull configuration on MicroPython 1.28.
    pin_class(pin_number, input_mode, pull=None, hold=False)


def open_rx_only_from_board_config(
    config_module=None,
    uart_class=None,
    pin_class=None,
    ticks_ms=None,
):
    """Open the guarded DFR0654 passive-capture path.

    The returned object has no ``write()``, ``send_frame()``, ``init()`` or
    ``sendbreak()`` method.  Physical disconnection of GPIO17 remains required.
    """

    if config_module is None:
        import board_config as config_module

    _require_rx_only_configuration(config_module)

    if uart_class is None or pin_class is None:
        try:
            from machine import Pin, UART
        except ImportError:
            raise RuntimeError(
                "machine.UART and machine.Pin are only available on MicroPython"
            )
        if uart_class is None:
            uart_class = UART
        if pin_class is None:
            pin_class = Pin

    def neutralize():
        _neutralize_tx(pin_class, config_module.UART_TX_PIN)

    # First establish the harmless GPIO state.  The UART constructor then
    # briefly maps TX17, so it is neutralized again before anything is exposed.
    neutralize()
    uart = None
    reader = None
    try:
        uart = uart_class(
            config_module.UART_ID,
            baudrate=config_module.UART_BAUDRATE,
            bits=config_module.UART_BITS,
            parity=config_module.UART_PARITY,
            stop=config_module.UART_STOP_BITS,
            tx=config_module.UART_TX_PIN,
            rx=config_module.UART_RX_PIN,
            timeout=config_module.UART_DRIVER_TIMEOUT_MS,
            timeout_char=config_module.UART_DRIVER_TIMEOUT_CHAR_MS,
            rxbuf=config_module.UART_RX_ONLY_BUFFER_SIZE,
            invert=config_module.UART_INVERT,
            flow=0,
        )
        neutralize()
        reader = _RXOnlyUARTReader(uart, neutralize)
        transport = RXOnlyTransport(
            reader,
            max_read_bytes=config_module.UART_RX_ONLY_MAX_READ_BYTES,
            queue_capacity=config_module.UART_RX_ONLY_QUEUE_CAPACITY,
            max_empty_ready_reads=(
                config_module.UART_RX_ONLY_MAX_EMPTY_READY_READS
            ),
            ticks_ms=ticks_ms,
        )
        # A final post-construction assertion of the physical pin mode covers
        # any setup code added between the UART and transport constructors.
        neutralize()
        return transport
    except Exception as setup_error:
        cleanup_errors = []
        if reader is not None:
            try:
                reader.deinit()
            except Exception as error:
                cleanup_errors.append(error)
        elif uart is not None:
            deinit = getattr(uart, "deinit", None)
            if callable(deinit):
                try:
                    deinit()
                except Exception as error:
                    cleanup_errors.append(error)
        try:
            neutralize()
        except Exception as error:
            cleanup_errors.append(error)
        if cleanup_errors:
            raise RXOnlyTransportError(
                "RX-only setup failed ({}) and safety cleanup failed ({})".format(
                    setup_error,
                    "; ".join(str(error) for error in cleanup_errors),
                )
            )
        raise
