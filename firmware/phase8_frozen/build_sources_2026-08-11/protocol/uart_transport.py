"""Non-blocking UART transport for raw Autoterm frames.

The transport deliberately has no knowledge of commands, CRC meaning or
heater states.  It reads bounded chunks, preserves them for diagnostics and
returns complete raw frames produced by ``RawFrameStreamParser``.

The UART object is injected so all behaviour can be tested with CPython.  A
real ``machine.UART`` is only constructed by an explicit factory function and
never at module import time.
"""

try:
    from time import ticks_diff as _platform_ticks_diff
    from time import ticks_ms as _platform_ticks_ms
except ImportError:  # CPython
    import time as _time

    def _platform_ticks_ms():
        return int(_time.monotonic() * 1000)

    def _platform_ticks_diff(current, previous):
        return current - previous


from .autoterm_frames import MAX_PROTOCOL_FRAME_LENGTH, RawFrameStreamParser


# Enabling protocol TX is intentionally a module-internal capability.  The
# board factory grants it only when the board configuration explicitly opts
# in.  Normal callers cannot accidentally override a board-level lock with a
# constructor/factory flag.
_TX_AUTHORIZATION = object()


class UARTTransportError(Exception):
    """Base class for transport failures requiring higher-layer attention."""


class UARTTransportWriteError(UARTTransportError):
    """The UART did not accept one complete frame in a single write."""

    def __init__(self, message, expected, written):
        super().__init__(message)
        self.expected = expected
        self.written = written


class UARTTransportTxDisabledError(UARTTransportError):
    """Protocol TX is blocked by the current board safety policy."""


class _BoundedActivityQueue:
    """Fixed-size O(1) queue used only inside the transport poll path."""

    def __init__(self, capacity):
        self.capacity = capacity
        self._items = [None] * capacity
        self._head = 0
        self._size = 0
        self.dropped = 0

    def record(self, event_name, now_ms, raw, details=None):
        if self._size >= self.capacity:
            # Keep the newest evidence.  A later capture service must treat
            # any nonzero dropped count as an explicitly incomplete capture.
            self._items[self._head] = None
            self._head = (self._head + 1) % self.capacity
            self._size -= 1
            self.dropped += 1

        index = (self._head + self._size) % self.capacity
        isolated_details = (
            dict(details) if isinstance(details, dict) else details
        )
        self._items[index] = (
            event_name,
            now_ms,
            bytes(raw),
            isolated_details,
        )
        self._size += 1
        return True

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


class UARTTransport:
    """Poll an already configured UART without sleeping or interpreting data.

    Diagnostic activity is placed into a bounded internal queue instead of
    calling user code from ``poll()``.  ``pop_activity()`` and
    ``drain_activity()`` return tuples shaped as:

    ``(event_name, monotonic_ms, immutable_bytes, optional_details)``

    This preserves raw I/O for a later capture service without allowing file
    I/O or a slow callback to block UART supervision.
    """

    def __init__(
        self,
        uart,
        framer=None,
        inter_byte_timeout_ms=200,
        max_read_bytes=512,
        activity_queue_capacity=32,
        max_empty_ready_reads=3,
        _tx_authorization=None,
        ticks_ms=None,
        ticks_diff=None,
    ):
        if uart is None:
            raise ValueError("uart is required")
        if not callable(getattr(uart, "any", None)):
            raise ValueError("uart must provide any()")
        if not callable(getattr(uart, "read", None)):
            raise ValueError("uart must provide read()")
        if not callable(getattr(uart, "write", None)):
            raise ValueError("uart must provide write()")
        if (
            not isinstance(inter_byte_timeout_ms, int)
            or isinstance(inter_byte_timeout_ms, bool)
            or inter_byte_timeout_ms <= 0
        ):
            raise ValueError("inter_byte_timeout_ms must be a positive integer")
        if (
            not isinstance(max_read_bytes, int)
            or isinstance(max_read_bytes, bool)
            or max_read_bytes <= 0
        ):
            raise ValueError("max_read_bytes must be a positive integer")
        if (
            not isinstance(activity_queue_capacity, int)
            or isinstance(activity_queue_capacity, bool)
            or activity_queue_capacity <= 0
        ):
            raise ValueError("activity_queue_capacity must be a positive integer")
        if (
            not isinstance(max_empty_ready_reads, int)
            or isinstance(max_empty_ready_reads, bool)
            or max_empty_ready_reads <= 0
        ):
            raise ValueError("max_empty_ready_reads must be a positive integer")
        if (
            _tx_authorization is not None
            and _tx_authorization is not _TX_AUTHORIZATION
        ):
            raise ValueError("invalid protocol TX authorization")

        self._uart = uart
        self.framer = framer if framer is not None else RawFrameStreamParser()
        if not isinstance(self.framer, RawFrameStreamParser):
            raise ValueError("framer must be a RawFrameStreamParser")

        self.inter_byte_timeout_ms = inter_byte_timeout_ms
        self.max_read_bytes = max_read_bytes
        self.max_empty_ready_reads = max_empty_ready_reads
        self._tx_authorization = _tx_authorization
        self._activity_queue = _BoundedActivityQueue(activity_queue_capacity)
        self._ticks_ms = ticks_ms or _platform_ticks_ms
        self._ticks_diff = ticks_diff or _platform_ticks_diff
        if not callable(self._ticks_ms) or not callable(self._ticks_diff):
            raise ValueError("ticks_ms and ticks_diff must be callable")

        self.rx_bytes = 0
        self.tx_bytes = 0
        self.rx_chunks = 0
        self.rx_frames = 0
        self.tx_frames = 0
        self.timeout_recoveries = 0
        self.read_errors = 0
        self.write_errors = 0
        self.tx_blocked = 0
        self.framer_errors = 0
        self.activity_errors = 0
        self.empty_ready_reads = 0
        self.consecutive_empty_ready_reads = 0
        self.rx_faulted = False
        self.last_rx_ms = None
        self.last_tx_ms = None
        self.last_frame_ms = None
        self.last_error = None
        self._framer_activity_ms = None

    @property
    def tx_enabled(self):
        """Whether the immutable construction-time TX capability is active."""

        return self._tx_authorization is _TX_AUTHORIZATION

    def _now(self, supplied):
        return self._ticks_ms() if supplied is None else supplied

    def _record_activity(self, event_name, now_ms, raw, details=None):
        try:
            self._activity_queue.record(event_name, now_ms, raw, details)
        except Exception:
            # The queue is internal and bounded, but diagnostics must still
            # never stop transport I/O if allocation fails unexpectedly.
            self.activity_errors += 1

    def _record_frames(self, frames, now_ms):
        if not frames:
            return []

        self.rx_frames += len(frames)
        self.last_frame_ms = now_ms
        for frame in frames:
            self._record_activity("rx_frame", now_ms, frame)
        return frames

    def _validate_raw_frames(self, frames):
        if not isinstance(frames, (list, tuple)):
            raise TypeError("raw framer must return a list or tuple")

        immutable = []
        for frame in frames:
            if not isinstance(frame, (bytes, bytearray, memoryview)):
                raise TypeError("raw framer returned a non-byte frame")
            raw = bytes(frame)
            if not raw:
                raise ValueError("raw framer returned an empty frame")
            immutable.append(raw)
        return immutable

    def _record_read_error(self, operation, error, now_ms):
        self.read_errors += 1
        self.last_error = {
            "operation": operation,
            "type": error.__class__.__name__,
            "message": str(error),
        }
        self._record_activity("rx_error", now_ms, b"", self.last_error)

    def _reset_framer_checked(self):
        self.framer.reset()
        if self.framer.buffer:
            raise UARTTransportError(
                "framer reset returned with pending RX bytes"
            )

    def _handle_framer_error(self, operation, error, now_ms):
        self.framer_errors += 1
        details = {
            "operation": operation,
            "type": error.__class__.__name__,
            "message": str(error),
        }
        try:
            self._reset_framer_checked()
        except Exception as reset_error:
            details["reset_type"] = reset_error.__class__.__name__
            details["reset_message"] = str(reset_error)
            self.rx_faulted = True
        self._framer_activity_ms = None
        self.last_error = details
        self._record_activity("framer_error", now_ms, b"", details)

    def _record_empty_ready_read(self, now_ms):
        self.read_errors += 1
        self.empty_ready_reads += 1
        self.consecutive_empty_ready_reads += 1
        faulted = (
            self.consecutive_empty_ready_reads >= self.max_empty_ready_reads
        )
        if faulted:
            self.rx_faulted = True
        self.last_error = {
            "operation": "read",
            "type": "empty_ready_read",
            "message": "UART.any() reported data but read() returned none",
            "consecutive": self.consecutive_empty_ready_reads,
            "rx_faulted": faulted,
        }
        self._record_activity("rx_error", now_ms, b"", self.last_error)

    def _recover_expired_candidate(self, now_ms):
        if not self.framer.buffer or self._framer_activity_ms is None:
            return []

        age = self._ticks_diff(now_ms, self._framer_activity_ms)
        if age < self.inter_byte_timeout_ms:
            return []

        self.timeout_recoveries += 1
        self._framer_activity_ms = now_ms
        try:
            frames = self._validate_raw_frames(
                self.framer.recover_after_timeout()
            )
        except Exception as error:
            self._handle_framer_error(
                "framer_timeout_recovery", error, now_ms
            )
            return []
        return self._record_frames(frames, now_ms)

    def poll(self, now_ms=None):
        """Perform one bounded, non-blocking RX poll and return raw frames."""

        now_ms = self._now(now_ms)
        if self.rx_faulted:
            return []

        try:
            available = self._uart.any()
        except Exception as error:
            self._record_read_error("any", error, now_ms)
            return []

        if (
            not isinstance(available, int)
            or isinstance(available, bool)
            or available < 0
        ):
            self._record_read_error(
                "any",
                TypeError("UART.any() must return a non-negative integer"),
                now_ms,
            )
            return []

        if available == 0:
            self.consecutive_empty_ready_reads = 0
            return self._recover_expired_candidate(now_ms)

        # any() is only a readiness indicator on MicroPython and may return 1
        # even when more data is buffered, so request the bounded maximum.
        try:
            data = self._uart.read(self.max_read_bytes)
        except Exception as error:
            self._record_read_error("read", error, now_ms)
            return []

        if data is None or data == b"":
            self._record_empty_ready_read(now_ms)
            return []
        if not isinstance(data, (bytes, bytearray, memoryview)):
            self._record_read_error(
                "read",
                TypeError("UART.read() must return bytes or None"),
                now_ms,
            )
            return []

        try:
            chunk = bytes(data)
        except (TypeError, ValueError) as error:
            self._record_read_error("read", error, now_ms)
            return []

        if not chunk:
            self._record_empty_ready_read(now_ms)
            return []

        self.consecutive_empty_ready_reads = 0
        self.rx_bytes += len(chunk)
        self.rx_chunks += 1
        self.last_rx_ms = now_ms
        self._framer_activity_ms = now_ms
        self._record_activity("rx_chunk", now_ms, chunk)

        try:
            new_frames = self._validate_raw_frames(self.framer.feed(chunk))
        except Exception as error:
            self._handle_framer_error("framer_feed", error, now_ms)
            return []

        return self._record_frames(new_frames, now_ms)

    def send_frame(self, raw_frame, now_ms=None):
        """Write one complete frame exactly once; never retry automatically."""

        if isinstance(raw_frame, (str, int, bool)):
            raise ValueError("raw_frame must be a non-empty byte sequence")
        try:
            raw = bytes(raw_frame)
        except (TypeError, ValueError):
            raise ValueError("raw_frame must be a non-empty byte sequence")
        if not raw:
            raise ValueError("raw_frame must not be empty")
        if len(raw) > MAX_PROTOCOL_FRAME_LENGTH:
            raise ValueError(
                "raw_frame exceeds the maximum protocol frame length"
            )

        now_ms = self._now(now_ms)
        if self._tx_authorization is not _TX_AUTHORIZATION:
            self.tx_blocked += 1
            self.last_error = {
                "operation": "write",
                "type": "tx_disabled",
                "message": "protocol TX is disabled by board configuration",
            }
            self._record_activity(
                "tx_blocked",
                now_ms,
                raw,
                {"reason": "board_safety_policy"},
            )
            raise UARTTransportTxDisabledError(
                "protocol TX is disabled by board configuration"
            )

        try:
            written = self._uart.write(raw)
        except Exception as error:
            self.write_errors += 1
            self.last_error = {
                "operation": "write",
                "type": error.__class__.__name__,
                "message": str(error),
            }
            self._record_activity(
                "tx_error",
                now_ms,
                raw,
                {
                    "expected": len(raw),
                    "written": None,
                    "state_unknown": True,
                    "type": error.__class__.__name__,
                    "message": str(error),
                },
            )
            raise UARTTransportWriteError(
                "UART write failed; transmission state is unknown",
                len(raw),
                None,
            )

        if (
            written is None
            or not isinstance(written, int)
            or isinstance(written, bool)
            or written != len(raw)
        ):
            self.write_errors += 1
            accepted = (
                written
                if isinstance(written, int) and not isinstance(written, bool)
                else None
            )
            if accepted is not None and 0 < accepted <= len(raw):
                self.tx_bytes += accepted
                self.last_tx_ms = now_ms
            self.last_error = {
                "operation": "write",
                "type": "short_write",
                "expected": len(raw),
                "written": accepted,
            }
            event_name = (
                "tx_partial"
                if accepted is not None and 0 < accepted < len(raw)
                else "tx_error"
            )
            self._record_activity(
                event_name,
                now_ms,
                raw,
                {
                    "expected": len(raw),
                    "written": accepted,
                    "accepted": (
                        raw[:accepted]
                        if accepted is not None and 0 < accepted <= len(raw)
                        else b""
                    ),
                    "state_unknown": accepted is None or accepted != 0,
                    "type": "short_write",
                },
            )
            raise UARTTransportWriteError(
                "UART did not accept the complete frame; no retry was attempted",
                len(raw),
                accepted,
            )

        self.tx_bytes += written
        self.tx_frames += 1
        self.last_tx_ms = now_ms
        self._record_activity("tx_frame", now_ms, raw)
        return written

    def pop_activity(self):
        """Return one queued diagnostic event, or ``None`` when empty."""

        return self._activity_queue.pop()

    def drain_activity(self, max_events=None):
        """Drain queued events outside the time-critical UART poll path."""

        if max_events is not None and (
            not isinstance(max_events, int)
            or isinstance(max_events, bool)
            or max_events < 0
        ):
            raise ValueError("max_events must be a non-negative integer or None")

        events = []
        while len(self._activity_queue) and (
            max_events is None or len(events) < max_events
        ):
            events.append(self._activity_queue.pop())
        return events

    def reset_rx(self):
        """Reset RX framing/anomaly state and clear the RX fault lock."""

        try:
            self._reset_framer_checked()
        except Exception as error:
            self.rx_faulted = True
            self.last_error = {
                "operation": "reset_rx",
                "type": error.__class__.__name__,
                "message": str(error),
                "rx_faulted": True,
            }
            raise
        self._framer_activity_ms = None
        self.consecutive_empty_ready_reads = 0
        self.rx_faulted = False

    def status(self):
        """Return transport diagnostics without protocol/heater semantics."""

        return {
            "rx_bytes": self.rx_bytes,
            "tx_bytes": self.tx_bytes,
            "rx_chunks": self.rx_chunks,
            "rx_frames": self.rx_frames,
            "tx_frames": self.tx_frames,
            "timeout_recoveries": self.timeout_recoveries,
            "read_errors": self.read_errors,
            "write_errors": self.write_errors,
            "tx_blocked": self.tx_blocked,
            "tx_enabled": self._tx_authorization is _TX_AUTHORIZATION,
            "framer_errors": self.framer_errors,
            "activity_errors": self.activity_errors,
            "empty_ready_reads": self.empty_ready_reads,
            "consecutive_empty_ready_reads": self.consecutive_empty_ready_reads,
            "rx_faulted": self.rx_faulted,
            "activity_queued": len(self._activity_queue),
            "activity_dropped": self._activity_queue.dropped,
            "activity_complete": (
                self._activity_queue.dropped == 0
                and self.activity_errors == 0
            ),
            "last_rx_ms": self.last_rx_ms,
            "last_tx_ms": self.last_tx_ms,
            "last_frame_ms": self.last_frame_ms,
            "last_error": (
                dict(self.last_error) if self.last_error is not None else None
            ),
            "pending_rx_bytes": len(self.framer.buffer),
        }

    def deinit(self):
        """Deinitialize the injected UART when its port provides deinit()."""

        method = getattr(self._uart, "deinit", None)
        if callable(method):
            method()


def _open_uart_from_board_config(config_module=None, uart_class=None):
    """Internal raw-UART factory used by the guarded transport factory."""

    if config_module is None:
        import board_config as config_module

    config_module.require_uart_configuration()

    if uart_class is None:
        try:
            from machine import UART as uart_class
        except ImportError:
            raise RuntimeError("machine.UART is only available on MicroPython")

    return uart_class(
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


def open_from_board_config(
    config_module=None,
    uart_class=None,
    framer=None,
    activity_queue_capacity=None,
    max_empty_ready_reads=None,
    ticks_ms=None,
    ticks_diff=None,
):
    """Create ``UARTTransport`` without coupling module import to hardware."""

    if config_module is None:
        import board_config as config_module

    tx_enabled = getattr(config_module, "UART_PROTOCOL_TX_ENABLED", False)
    if not isinstance(tx_enabled, bool):
        raise ValueError("UART_PROTOCOL_TX_ENABLED must be boolean")

    uart = _open_uart_from_board_config(config_module, uart_class)
    try:
        if activity_queue_capacity is None:
            activity_queue_capacity = getattr(
                config_module, "UART_ACTIVITY_QUEUE_CAPACITY", 32
            )
        if max_empty_ready_reads is None:
            max_empty_ready_reads = getattr(
                config_module, "UART_MAX_EMPTY_READY_READS", 3
            )
        return UARTTransport(
            uart=uart,
            framer=framer,
            inter_byte_timeout_ms=config_module.UART_INTER_BYTE_TIMEOUT_MS,
            max_read_bytes=config_module.UART_MAX_READ_BYTES,
            activity_queue_capacity=activity_queue_capacity,
            max_empty_ready_reads=max_empty_ready_reads,
            _tx_authorization=(
                _TX_AUTHORIZATION if tx_enabled else None
            ),
            ticks_ms=ticks_ms,
            ticks_diff=ticks_diff,
        )
    except BaseException as primary_error:
        cleanup_error = None
        for _ in range(2):
            try:
                deinit = getattr(uart, "deinit", None)
                if not callable(deinit):
                    raise RuntimeError("opened UART has no deinit()")
                deinit()
                cleanup_error = None
                break
            except BaseException as error:
                cleanup_error = error

        if cleanup_error is not None:
            raise UARTTransportError(
                "UART transport construction failed ({0}); UART cleanup "
                "also failed ({1})".format(primary_error, cleanup_error)
            )
        raise
