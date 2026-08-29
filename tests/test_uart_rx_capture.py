import json
import unittest

from tools.uart_rx_capture import RX_ONLY_CONFIRMATION, run


class FakeConfig:
    UART_ID = 2
    UART_RX_PIN = 16
    UART_BAUDRATE = 9600
    UART_BITS = 8
    UART_PARITY = None
    UART_STOP_BITS = 1
    UART_RX_CAPTURE_MAX_DURATION_MS = 120000


class FakeClock:
    def __init__(self, now=0, modulus=None):
        self.value = now
        self.modulus = modulus

    def ticks_ms(self):
        return self.value

    def ticks_diff(self, current, previous):
        if self.modulus is None:
            return current - previous
        half = self.modulus // 2
        return ((current - previous + half) % self.modulus) - half

    def sleep_ms(self, milliseconds):
        self.value += milliseconds
        if self.modulus is not None:
            self.value %= self.modulus


class FakeTransport:
    def __init__(
        self,
        fail_on_poll=False,
        interrupt_on_poll=False,
        inject_on_poll=1,
        continuous=False,
    ):
        self.fail_on_poll = fail_on_poll
        self.interrupt_on_poll = interrupt_on_poll
        self.inject_on_poll = inject_on_poll
        self.continuous = continuous
        self.poll_calls = 0
        self.closed = False
        self.queue = []
        self.values = {
            "rx_bytes": 0,
            "rx_chunks": 0,
            "read_errors": 0,
            "rx_faulted": False,
            "dropped_chunks": 0,
            "dropped_bytes": 0,
            "queued_chunks": 0,
        }

    def poll(self, now_ms):
        self.poll_calls += 1
        if self.fail_on_poll:
            raise OSError("poll failed")
        if self.interrupt_on_poll:
            raise KeyboardInterrupt()
        if self.continuous or self.poll_calls == self.inject_on_poll:
            raw = b"\x00\xaa\xff"
            self.queue.append((self.values["rx_chunks"], now_ms, raw))
            self.values["rx_bytes"] += len(raw)
            self.values["rx_chunks"] += 1
            self.values["queued_chunks"] = len(self.queue)
            return [raw]
        return []

    def drain_chunks(self):
        chunks = self.queue
        self.queue = []
        self.values["queued_chunks"] = 0
        return chunks

    def status(self):
        return dict(self.values)

    def deinit(self):
        self.closed = True


class TestUARTRXCaptureTool(unittest.TestCase):
    def test_confirmation_is_required_before_factory_call(self):
        calls = []
        with self.assertRaises(RuntimeError):
            run(
                "wrong",
                transport_factory=lambda: calls.append(True),
                config_module=FakeConfig,
            )
        self.assertEqual(calls, [])

    def test_duration_limit_is_checked_before_factory_call(self):
        calls = []
        with self.assertRaises(ValueError):
            run(
                RX_ONLY_CONFIRMATION,
                duration_ms=120001,
                transport_factory=lambda: calls.append(True),
                config_module=FakeConfig,
            )
        self.assertEqual(calls, [])

    def test_success_emits_only_valid_ndjson_and_deinitializes(self):
        transport = FakeTransport()
        clock = FakeClock()
        lines = []
        result = run(
            RX_ONLY_CONFIRMATION,
            duration_ms=5,
            label="heater_off",
            transport_factory=lambda: transport,
            config_module=FakeConfig,
            ticks_ms=clock.ticks_ms,
            ticks_diff=clock.ticks_diff,
            sleep_ms=clock.sleep_ms,
            emit_line=lines.append,
        )
        records = [json.loads(line) for line in lines]
        self.assertEqual([item["type"] for item in records], [
            "start", "rx_chunk", "end"
        ])
        self.assertEqual(records[1]["raw_hex"], "00aaff")
        self.assertTrue(records[-1]["complete"])
        self.assertTrue(result["complete"])
        self.assertTrue(transport.closed)

    def test_poll_error_emits_incomplete_end_and_deinitializes(self):
        transport = FakeTransport(fail_on_poll=True)
        clock = FakeClock()
        lines = []
        result = run(
            RX_ONLY_CONFIRMATION,
            duration_ms=5,
            transport_factory=lambda: transport,
            config_module=FakeConfig,
            ticks_ms=clock.ticks_ms,
            ticks_diff=clock.ticks_diff,
            sleep_ms=clock.sleep_ms,
            emit_line=lines.append,
        )
        records = [json.loads(line) for line in lines]
        self.assertEqual(records[-1]["type"], "end")
        self.assertFalse(records[-1]["complete"])
        self.assertIn("poll failed", records[-1]["run_error"])
        self.assertTrue(transport.closed)
        self.assertFalse(result["complete"])

    def test_keyboard_interrupt_returns_marked_capture(self):
        transport = FakeTransport(interrupt_on_poll=True)
        clock = FakeClock()
        lines = []
        result = run(
            RX_ONLY_CONFIRMATION,
            duration_ms=5,
            transport_factory=lambda: transport,
            config_module=FakeConfig,
            ticks_ms=clock.ticks_ms,
            ticks_diff=clock.ticks_diff,
            sleep_ms=clock.sleep_ms,
            emit_line=lines.append,
        )
        self.assertTrue(result["interrupted"])
        self.assertFalse(result["complete"])
        self.assertTrue(transport.closed)

    def test_wraparound_duration_and_offsets_are_supported(self):
        transport = FakeTransport()
        clock = FakeClock(now=60, modulus=64)
        lines = []
        result = run(
            RX_ONLY_CONFIRMATION,
            duration_ms=6,
            transport_factory=lambda: transport,
            config_module=FakeConfig,
            ticks_ms=clock.ticks_ms,
            ticks_diff=clock.ticks_diff,
            sleep_ms=clock.sleep_ms,
            emit_line=lines.append,
        )
        records = [json.loads(line) for line in lines]
        self.assertEqual(records[1]["offset_ms"], 0)
        self.assertEqual(result["elapsed_ms"], 6)

    def test_final_hardware_drain_captures_byte_arriving_at_deadline(self):
        # With duration 1 and a 2-ms sleep, poll 2 is the first bounded final
        # drain call and represents data already queued at the deadline.
        transport = FakeTransport(inject_on_poll=2)
        clock = FakeClock()
        lines = []
        result = run(
            RX_ONLY_CONFIRMATION,
            duration_ms=1,
            transport_factory=lambda: transport,
            config_module=FakeConfig,
            ticks_ms=clock.ticks_ms,
            ticks_diff=clock.ticks_diff,
            sleep_ms=clock.sleep_ms,
            emit_line=lines.append,
        )
        records = [json.loads(line) for line in lines]
        self.assertEqual(records[1]["type"], "rx_chunk")
        self.assertEqual(result["rx_bytes"], 3)
        self.assertTrue(result["complete"])

    def test_continuous_final_drain_is_bounded_and_marked_incomplete(self):
        transport = FakeTransport(continuous=True)
        clock = FakeClock()
        lines = []
        result = run(
            RX_ONLY_CONFIRMATION,
            duration_ms=1,
            transport_factory=lambda: transport,
            config_module=FakeConfig,
            ticks_ms=clock.ticks_ms,
            ticks_diff=clock.ticks_diff,
            sleep_ms=clock.sleep_ms,
            emit_line=lines.append,
        )
        self.assertTrue(result["final_drain_limit_reached"])
        self.assertFalse(result["complete"])
        self.assertEqual(transport.poll_calls, 33)

    def test_invalid_label_and_start_emit_failure_deinitialize(self):
        invalid_transport = FakeTransport()
        with self.assertRaises(ValueError):
            run(
                RX_ONLY_CONFIRMATION,
                duration_ms=1,
                label="",
                transport_factory=lambda: invalid_transport,
                config_module=FakeConfig,
            )
        self.assertFalse(invalid_transport.closed)

        transport = FakeTransport()
        clock = FakeClock()

        def broken_emit(_line):
            raise OSError("output failed")

        with self.assertRaisesRegex(OSError, "output failed"):
            run(
                RX_ONLY_CONFIRMATION,
                duration_ms=1,
                transport_factory=lambda: transport,
                config_module=FakeConfig,
                ticks_ms=clock.ticks_ms,
                ticks_diff=clock.ticks_diff,
                sleep_ms=clock.sleep_ms,
                emit_line=broken_emit,
            )
        self.assertTrue(transport.closed)


if __name__ == "__main__":
    unittest.main()
