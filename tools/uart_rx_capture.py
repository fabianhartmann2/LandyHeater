"""Manual bounded RX-only capture streamed as NDJSON over USB.

Importing this module does not initialize hardware.  The capture never writes
to the UART or the ESP32 filesystem.  GPIO17/D10 must be physically unconnected
before ``run()`` is invoked.
"""

try:
    import ujson as _json
except ImportError:  # CPython
    import json as _json

try:
    from time import sleep_ms as _platform_sleep_ms
    from time import ticks_diff as _platform_ticks_diff
    from time import ticks_ms as _platform_ticks_ms
except ImportError:  # CPython
    import time as _time

    def _platform_sleep_ms(milliseconds):
        _time.sleep(milliseconds / 1000)

    def _platform_ticks_ms():
        return int(_time.monotonic() * 1000)

    def _platform_ticks_diff(current, previous):
        return current - previous


RX_ONLY_CONFIRMATION = "GPIO17_DISCONNECTED_RX16_ONLY"
DEFAULT_CAPTURE_DURATION_MS = 30000
POLL_INTERVAL_MS = 2
FINAL_DRAIN_MAX_POLLS = 32


def _emit(record, emit_line):
    line = _json.dumps(record)
    if emit_line is None:
        print(line)
    else:
        emit_line(line)


def run(
    confirmation,
    duration_ms=DEFAULT_CAPTURE_DURATION_MS,
    label="heater_off",
    transport_factory=None,
    config_module=None,
    ticks_ms=None,
    ticks_diff=None,
    sleep_ms=None,
    emit_line=None,
):
    """Capture raw UART RX chunks for a bounded period and emit NDJSON."""

    if confirmation != RX_ONLY_CONFIRMATION:
        raise RuntimeError(
            "RX-only capture not armed; GPIO17 must be physically disconnected"
        )
    if (
        not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or duration_ms <= 0
    ):
        raise ValueError("duration_ms must be a positive integer")
    if config_module is None:
        import board_config as config_module
    maximum = getattr(config_module, "UART_RX_CAPTURE_MAX_DURATION_MS", None)
    if (
        not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or maximum <= 0
    ):
        raise RuntimeError(
            "UART_RX_CAPTURE_MAX_DURATION_MS must be a positive integer"
        )
    if duration_ms > maximum:
        raise ValueError("duration_ms exceeds the configured safety limit")
    if not isinstance(label, str) or not label or len(label) > 64:
        raise ValueError("label must be a non-empty string up to 64 chars")

    ticks_ms = ticks_ms or _platform_ticks_ms
    ticks_diff = ticks_diff or _platform_ticks_diff
    sleep_ms = sleep_ms or _platform_sleep_ms
    if not all(callable(item) for item in (ticks_ms, ticks_diff, sleep_ms)):
        raise ValueError("clock and sleep functions must be callable")

    if transport_factory is None:
        from protocol.rx_only_transport import open_rx_only_from_board_config

        transport_factory = lambda: open_rx_only_from_board_config(
            config_module=config_module,
            ticks_ms=ticks_ms,
        )
    if not callable(transport_factory):
        raise ValueError("transport_factory must be callable")

    from services.protocol_capture import ProtocolCaptureSession

    transport = transport_factory()
    started_ms = None
    session = None
    interrupted = False
    run_error = None
    final_drain_limit_reached = False

    try:
        try:
            started_ms = ticks_ms()
            session = ProtocolCaptureSession(
                transport,
                config_module,
                label,
                started_ms,
                ticks_diff,
            )
            _emit(session.start_record(), emit_line)

            while True:
                now_ms = ticks_ms()
                if ticks_diff(now_ms, started_ms) >= duration_ms:
                    break
                transport.poll(now_ms)
                for chunk in transport.drain_chunks():
                    _emit(session.chunk_record(chunk), emit_line)
                sleep_ms(POLL_INTERVAL_MS)
        except KeyboardInterrupt:
            interrupted = True
        except Exception as error:
            run_error = error

        if session is not None and run_error is None and not interrupted:
            try:
                # Read only bytes already waiting at the deadline.  No sleep is
                # used here, and the loop is bounded even under continuous RX.
                for _ in range(FINAL_DRAIN_MAX_POLLS):
                    new_chunks = transport.poll(ticks_ms())
                    for chunk in transport.drain_chunks():
                        _emit(session.chunk_record(chunk), emit_line)
                    if not new_chunks:
                        break
                else:
                    final_drain_limit_reached = True
            except Exception as error:
                if run_error is None:
                    run_error = error
    finally:
        try:
            transport.deinit()
        except Exception as error:
            if run_error is None:
                run_error = error

    if session is None:
        if run_error is not None:
            raise run_error
        raise RuntimeError("RX-only capture session could not be created")

    ended_ms = ticks_ms()
    end_record = session.end_record(
        ended_ms,
        interrupted=interrupted,
        run_error=run_error,
        final_drain_limit_reached=final_drain_limit_reached,
    )
    _emit(end_record, emit_line)
    return end_record
