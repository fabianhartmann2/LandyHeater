"""Turn raw RX-only chunks into a stable, line-oriented capture schema."""

try:
    from ubinascii import hexlify
except ImportError:  # CPython
    from binascii import hexlify


CAPTURE_SCHEMA = "landy-heater.rx-capture"
CAPTURE_VERSION = 1


def _raw_hex(raw):
    value = hexlify(bytes(raw))
    if isinstance(value, bytes):
        return value.decode("ascii")
    return value


class ProtocolCaptureSession:
    """Build raw-chunk capture records without interpreting frame contents."""

    def __init__(
        self,
        transport,
        config_module,
        label,
        started_ms,
        ticks_diff,
    ):
        if transport is None:
            raise ValueError("transport is required")
        if not isinstance(label, str) or not label or len(label) > 64:
            raise ValueError("label must be a non-empty string up to 64 chars")
        if not callable(ticks_diff):
            raise ValueError("ticks_diff must be callable")

        self.transport = transport
        self.config = config_module
        self.label = label
        self.started_ms = started_ms
        self.ticks_diff = ticks_diff
        baseline = transport.status()
        self._start_rx_bytes = baseline["rx_bytes"]
        self._start_rx_chunks = baseline["rx_chunks"]
        self._start_dropped_chunks = baseline["dropped_chunks"]
        self._start_dropped_bytes = baseline["dropped_bytes"]
        self._start_read_errors = baseline["read_errors"]
        self._emitted_chunks = 0
        self._emitted_bytes = 0

    def start_record(self):
        return {
            "schema": CAPTURE_SCHEMA,
            "version": CAPTURE_VERSION,
            "type": "start",
            "label": self.label,
            "uart": {
                "id": self.config.UART_ID,
                "rx_gpio": self.config.UART_RX_PIN,
                "baudrate": self.config.UART_BAUDRATE,
                "bits": self.config.UART_BITS,
                "parity": self.config.UART_PARITY,
                "stop": self.config.UART_STOP_BITS,
            },
            "tx_software_enabled": False,
            "gpio17_required": "physically_disconnected",
        }

    def chunk_record(self, chunk):
        if not isinstance(chunk, (tuple, list)) or len(chunk) != 3:
            raise ValueError("chunk must contain sequence, timestamp and bytes")
        sequence, timestamp_ms, raw = chunk
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise ValueError("chunk payload must be bytes-like")
        raw = bytes(raw)
        self._emitted_chunks += 1
        self._emitted_bytes += len(raw)
        return {
            "type": "rx_chunk",
            "seq": sequence,
            "offset_ms": self.ticks_diff(timestamp_ms, self.started_ms),
            "length": len(raw),
            "raw_hex": _raw_hex(raw),
        }

    def end_record(
        self,
        ended_ms,
        interrupted=False,
        run_error=None,
        final_drain_limit_reached=False,
    ):
        status = self.transport.status()
        rx_bytes = status["rx_bytes"] - self._start_rx_bytes
        rx_chunks = status["rx_chunks"] - self._start_rx_chunks
        dropped_chunks = (
            status["dropped_chunks"] - self._start_dropped_chunks
        )
        dropped_bytes = status["dropped_bytes"] - self._start_dropped_bytes
        read_errors = status["read_errors"] - self._start_read_errors
        expected_emitted_chunks = rx_chunks - dropped_chunks
        expected_emitted_bytes = rx_bytes - dropped_bytes
        export_matches_rx = (
            self._emitted_chunks == expected_emitted_chunks
            and self._emitted_bytes == expected_emitted_bytes
        )
        complete = (
            dropped_chunks == 0
            and read_errors == 0
            and not status["rx_faulted"]
            and status.get("queued_chunks", 0) == 0
            and export_matches_rx
            and not interrupted
            and run_error is None
            and not final_drain_limit_reached
        )
        record = {
            "type": "end",
            "elapsed_ms": self.ticks_diff(ended_ms, self.started_ms),
            "rx_bytes": rx_bytes,
            "rx_chunks": rx_chunks,
            "emitted_bytes": self._emitted_bytes,
            "emitted_chunks": self._emitted_chunks,
            "dropped_chunks": dropped_chunks,
            "dropped_bytes": dropped_bytes,
            "read_errors": read_errors,
            "rx_faulted": status["rx_faulted"],
            "queued_chunks": status.get("queued_chunks", 0),
            "interrupted": bool(interrupted),
            "final_drain_limit_reached": bool(final_drain_limit_reached),
            "complete": complete,
        }
        if run_error is not None:
            record["run_error"] = str(run_error)
        return record
