import unittest
from unittest import mock

import board_config
import protocol
import protocol.uart_transport as uart_transport_module

from protocol.autoterm_frames import RawFrameStreamParser
from protocol.autoterm_protocol import (
    FrameStreamParser,
    build_frame,
    build_init_request,
    build_status_request,
)
from protocol.uart_transport import (
    UARTTransport,
    UARTTransportError,
    UARTTransportTxDisabledError,
    UARTTransportWriteError,
    open_from_board_config,
)


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

    def advance(self, milliseconds):
        self.value += milliseconds
        if self.modulus is not None:
            self.value %= self.modulus


class FakeUART:
    DEFAULT_WRITE = object()

    def __init__(self, *args, **kwargs):
        self.constructor_args = args
        self.constructor_kwargs = kwargs
        self.rx = bytearray()
        self.writes = []
        self.write_result = self.DEFAULT_WRITE
        self.any_result = None
        self.read_result = self.DEFAULT_WRITE
        self.any_error = None
        self.read_error = None
        self.write_error = None
        self.read_calls = 0
        self.deinitialized = False

    def inject(self, data):
        self.rx.extend(data)

    def any(self):
        if self.any_error is not None:
            raise self.any_error
        if self.any_result is not None:
            return self.any_result
        return len(self.rx)

    def read(self, count):
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        if self.read_result is not self.DEFAULT_WRITE:
            result = self.read_result
            self.read_result = self.DEFAULT_WRITE
            return result
        data = bytes(self.rx[:count])
        del self.rx[:count]
        return data

    def write(self, data):
        if self.write_error is not None:
            raise self.write_error
        raw = bytes(data)
        self.writes.append(raw)
        if self.write_result is self.DEFAULT_WRITE:
            return len(raw)
        return self.write_result

    def deinit(self):
        self.deinitialized = True


def make_transport(uart=None, clock=None, **kwargs):
    uart = uart or FakeUART()
    clock = clock or FakeClock()
    kwargs.setdefault(
        "_tx_authorization", uart_transport_module._TX_AUTHORIZATION
    )
    transport = UARTTransport(
        uart,
        ticks_ms=clock.ticks_ms,
        ticks_diff=clock.ticks_diff,
        **kwargs
    )
    return transport, uart, clock


class TestUARTTransportReceive(unittest.TestCase):
    def test_empty_poll_is_nonblocking(self):
        transport, uart, _ = make_transport()
        self.assertEqual(transport.poll(), [])
        self.assertEqual(uart.read_calls, 0)

    def test_complete_frame_emits_immediately_as_raw_bytes(self):
        transport, uart, _ = make_transport()
        raw = build_init_request()
        uart.inject(raw)
        self.assertEqual(transport.poll(), [raw])
        self.assertIsInstance(transport.poll(), list)

    def test_partial_frame_across_polls_under_timeout(self):
        transport, uart, clock = make_transport()
        raw = build_status_request()
        uart.inject(raw[:3])
        self.assertEqual(transport.poll(), [])
        clock.advance(199)
        uart.inject(raw[3:])
        self.assertEqual(transport.poll(), [raw])
        self.assertEqual(transport.timeout_recoveries, 0)

    def test_every_split_position(self):
        raw = build_frame(0x7E, bytes(range(20)))
        for split in range(1, len(raw)):
            with self.subTest(split=split):
                transport, uart, clock = make_transport()
                uart.inject(raw[:split])
                self.assertEqual(transport.poll(), [])
                clock.advance(10)
                uart.inject(raw[split:])
                self.assertEqual(transport.poll(), [raw])

    def test_multiple_frames_keep_order(self):
        transport, uart, _ = make_transport()
        expected = [build_init_request(), build_status_request()]
        uart.inject(expected[0] + expected[1])
        self.assertEqual(transport.poll(), expected)

    def test_any_is_used_only_as_readiness_indicator(self):
        transport, uart, _ = make_transport()
        raw = build_init_request()
        uart.inject(raw)
        uart.any_result = 1
        self.assertEqual(transport.poll(), [raw])

    def test_timeout_boundary_recovers_once(self):
        transport, uart, clock = make_transport()
        stalled = bytes((0xAA, 0x03, 0x20)) + build_init_request()
        uart.inject(stalled)
        self.assertEqual(transport.poll(), [])
        clock.advance(199)
        self.assertEqual(transport.poll(), [])
        clock.advance(1)
        self.assertEqual(transport.poll(), [build_init_request()])
        self.assertEqual(transport.timeout_recoveries, 1)
        clock.advance(1)
        self.assertEqual(transport.poll(), [])
        self.assertEqual(transport.timeout_recoveries, 1)

    def test_buffered_remainder_is_read_before_timeout_recovery(self):
        transport, uart, clock = make_transport()
        raw = build_init_request()
        uart.inject(raw[:3])
        self.assertEqual(transport.poll(), [])
        clock.advance(200)
        # These bytes arrived before the delayed poll and are already waiting
        # in the hardware buffer.  They must be consumed before timeout logic.
        uart.inject(raw[3:])
        self.assertEqual(transport.poll(), [raw])
        self.assertEqual(transport.timeout_recoveries, 0)

    def test_hardware_backlog_larger_than_read_limit_is_not_timed_out(self):
        transport, uart, clock = make_transport(max_read_bytes=3)
        raw = build_init_request()
        uart.inject(raw)
        self.assertEqual(transport.poll(), [])
        self.assertEqual(uart.any(), 4)

        clock.advance(200)
        self.assertEqual(transport.poll(), [])
        self.assertEqual(transport.timeout_recoveries, 0)
        self.assertEqual(transport.poll(), [raw])
        self.assertEqual(transport.timeout_recoveries, 0)

    def test_corrupt_prefix_recovers_after_new_data_then_empty_gap(self):
        transport, uart, clock = make_transport()
        uart.inject(bytes((0xAA, 0x03, 0x20)))
        self.assertEqual(transport.poll(), [])

        clock.advance(200)
        uart.inject(build_init_request())
        self.assertEqual(transport.poll(), [])
        self.assertEqual(transport.timeout_recoveries, 0)

        clock.advance(200)
        self.assertEqual(transport.poll(), [build_init_request()])
        self.assertEqual(transport.timeout_recoveries, 1)

    def test_interbyte_timer_resets_for_long_continuous_frame(self):
        transport, uart, clock = make_transport()
        raw = build_frame(0x7E, bytes(255))
        frames = []
        for value in raw:
            uart.inject(bytes((value,)))
            frames.extend(transport.poll())
            clock.advance(2)
        self.assertEqual(frames, [raw])
        self.assertEqual(transport.timeout_recoveries, 0)

    def test_ticks_wraparound_uses_injected_ticks_diff(self):
        clock = FakeClock(now=900, modulus=1024)
        transport, uart, _ = make_transport(clock=clock)
        uart.inject(bytes((0xAA, 0x03, 0x20)) + build_init_request())
        self.assertEqual(transport.poll(), [])
        clock.advance(200)
        self.assertEqual(transport.poll(), [build_init_request()])

    def test_activity_queue_preserves_junk_chunk_and_frame(self):
        transport, uart, _ = make_transport()
        chunk = b"junk" + build_init_request()
        uart.inject(chunk)
        self.assertEqual(transport.poll(), [build_init_request()])
        self.assertEqual(
            transport.drain_activity(),
            [
                ("rx_chunk", 0, chunk, None),
                ("rx_frame", 0, build_init_request(), None),
            ],
        )

    def test_full_activity_queue_cannot_stop_rx(self):
        transport, uart, _ = make_transport(activity_queue_capacity=1)
        uart.inject(build_init_request())
        self.assertEqual(transport.poll(), [build_init_request()])
        self.assertEqual(transport.status()["activity_queued"], 1)
        self.assertEqual(transport.status()["activity_dropped"], 1)
        self.assertFalse(transport.status()["activity_complete"])

    def test_activity_details_cannot_mutate_transport_status(self):
        transport, uart, _ = make_transport()
        uart.any_error = OSError("any failed")
        self.assertEqual(transport.poll(), [])
        event = transport.pop_activity()
        event[3]["operation"] = "changed by consumer"
        self.assertEqual(transport.status()["last_error"]["operation"], "any")

    def test_activity_queue_error_marks_capture_incomplete(self):
        transport, uart, _ = make_transport()

        def broken_record(*_args, **_kwargs):
            raise MemoryError("capture allocation failed")

        transport._activity_queue.record = broken_record
        uart.inject(build_init_request())
        self.assertEqual(transport.poll(), [build_init_request()])
        self.assertGreater(transport.status()["activity_errors"], 0)
        self.assertFalse(transport.status()["activity_complete"])

    def test_semantic_frame_parser_is_rejected(self):
        with self.assertRaises(ValueError):
            make_transport(framer=FrameStreamParser())

    def test_non_byte_raw_framer_output_is_contained_and_reset(self):
        class InvalidOutputFramer(RawFrameStreamParser):
            def __init__(self):
                super().__init__()
                self.was_reset = False

            def feed(self, data):
                self.buffer.extend(data)
                return [{"raw": bytes(data)}]

            def reset(self):
                self.was_reset = True
                super().reset()

        framer = InvalidOutputFramer()
        transport, uart, _ = make_transport(framer=framer)
        uart.inject(build_init_request())
        self.assertEqual(transport.poll(), [])
        self.assertTrue(framer.was_reset)
        self.assertEqual(transport.framer_errors, 1)
        self.assertEqual(transport.last_error["operation"], "framer_feed")

    def test_recovery_and_reset_failures_are_contained(self):
        class BrokenRecoveryFramer(RawFrameStreamParser):
            def recover_after_timeout(self):
                raise RuntimeError("recovery failed")

            def reset(self):
                raise RuntimeError("reset failed")

        framer = BrokenRecoveryFramer()
        transport, uart, clock = make_transport(framer=framer)
        uart.inject(bytes((0xAA, 0x03, 0x20)))
        self.assertEqual(transport.poll(), [])
        clock.advance(200)
        self.assertEqual(transport.poll(), [])
        self.assertEqual(transport.framer_errors, 1)
        self.assertEqual(
            transport.last_error["operation"], "framer_timeout_recovery"
        )
        self.assertEqual(transport.last_error["reset_type"], "RuntimeError")
        self.assertTrue(transport.status()["rx_faulted"])

        read_calls = uart.read_calls
        uart.inject(build_init_request())
        self.assertEqual(transport.poll(), [])
        self.assertEqual(uart.read_calls, read_calls)

    def test_noop_framer_reset_faults_rx_and_explicit_reset_fails(self):
        class NoOpResetFramer(RawFrameStreamParser):
            def feed(self, data):
                self.buffer.extend(data)
                raise RuntimeError("feed failed")

            def reset(self):
                pass

        transport, uart, _ = make_transport(framer=NoOpResetFramer())
        uart.inject(build_init_request())
        self.assertEqual(transport.poll(), [])
        self.assertTrue(transport.status()["rx_faulted"])
        self.assertGreater(transport.status()["pending_rx_bytes"], 0)
        with self.assertRaises(UARTTransportError):
            transport.reset_rx()
        self.assertTrue(transport.status()["rx_faulted"])

    def test_any_and_invalid_read_errors_are_contained(self):
        transport, uart, _ = make_transport()
        uart.any_error = OSError("any failed")
        self.assertEqual(transport.poll(), [])
        uart.any_error = None
        uart.any_result = -1
        self.assertEqual(transport.poll(), [])
        uart.any_result = 1
        uart.read_result = "not bytes"
        self.assertEqual(transport.poll(), [])
        uart.read_result = [0xAA]
        self.assertEqual(transport.poll(), [])
        self.assertEqual(transport.read_errors, 4)

    def test_repeated_ready_but_empty_reads_fault_rx_until_reset(self):
        transport, uart, _ = make_transport(max_empty_ready_reads=3)
        uart.any_result = 1
        for expected_count in (1, 2, 3):
            uart.read_result = None
            self.assertEqual(transport.poll(), [])
            self.assertEqual(
                transport.consecutive_empty_ready_reads, expected_count
            )

        self.assertTrue(transport.status()["rx_faulted"])
        read_calls = uart.read_calls
        self.assertEqual(transport.poll(), [])
        self.assertEqual(uart.read_calls, read_calls)

        transport.reset_rx()
        self.assertFalse(transport.status()["rx_faulted"])
        uart.any_result = None
        uart.inject(build_init_request())
        self.assertEqual(transport.poll(), [build_init_request()])

    def test_idle_poll_breaks_ready_but_empty_streak(self):
        transport, uart, _ = make_transport(max_empty_ready_reads=3)
        uart.any_result = 1
        uart.read_result = None
        self.assertEqual(transport.poll(), [])
        self.assertEqual(transport.consecutive_empty_ready_reads, 1)

        uart.any_result = 0
        self.assertEqual(transport.poll(), [])
        self.assertEqual(transport.consecutive_empty_ready_reads, 0)

        uart.any_result = 1
        for expected_count in (1, 2):
            uart.read_result = b""
            self.assertEqual(transport.poll(), [])
            self.assertEqual(
                transport.consecutive_empty_ready_reads, expected_count
            )
        self.assertFalse(transport.status()["rx_faulted"])

    def test_read_none_and_error_do_not_reset_partial_buffer(self):
        transport, uart, clock = make_transport()
        raw = build_init_request()
        uart.inject(raw[:3])
        self.assertEqual(transport.poll(), [])

        clock.advance(50)
        uart.any_result = 1
        uart.read_result = None
        self.assertEqual(transport.poll(), [])
        self.assertEqual(bytes(transport.framer.buffer), raw[:3])

        clock.advance(50)
        uart.read_error = OSError("temporary read error")
        self.assertEqual(transport.poll(), [])
        self.assertEqual(bytes(transport.framer.buffer), raw[:3])
        self.assertEqual(transport.read_errors, 2)

        uart.read_error = None
        uart.any_result = None
        uart.inject(raw[3:])
        self.assertEqual(transport.poll(), [raw])
        self.assertEqual(transport.read_errors, 2)

    def test_unknown_source_command_and_reserved_pass_unchanged(self):
        transport, uart, _ = make_transport()
        raw = build_frame(0x7E, b"\x12", device=0x99, reserved=0xA5)
        uart.inject(raw)
        self.assertEqual(transport.poll(), [raw])


class TestUARTTransportSend(unittest.TestCase):
    def test_tx_is_disabled_by_default_and_never_reaches_uart(self):
        uart = FakeUART()
        transport = UARTTransport(
            uart,
            ticks_ms=lambda: 0,
            ticks_diff=lambda current, previous: current - previous,
        )
        raw = build_init_request()
        with self.assertRaises(UARTTransportTxDisabledError):
            transport.send_frame(raw)
        self.assertEqual(uart.writes, [])
        self.assertEqual(transport.status()["tx_blocked"], 1)
        self.assertFalse(transport.status()["tx_enabled"])
        self.assertEqual(transport.pop_activity()[:3], ("tx_blocked", 0, raw))

    def test_full_frame_is_written_once_and_observed(self):
        transport, uart, _ = make_transport()
        raw = build_init_request()
        self.assertEqual(transport.send_frame(raw), len(raw))
        self.assertEqual(uart.writes, [raw])
        self.assertEqual(
            transport.drain_activity(), [("tx_frame", 0, raw, None)]
        )
        self.assertEqual(transport.tx_frames, 1)

    def test_none_and_short_write_raise_without_retry(self):
        raw = build_init_request()
        for result in (None, 0, 2, len(raw) + 1, True):
            with self.subTest(result=result):
                transport, uart, _ = make_transport()
                uart.write_result = result
                with self.assertRaises(UARTTransportWriteError):
                    transport.send_frame(raw)
                self.assertEqual(len(uart.writes), 1)
                self.assertEqual(transport.tx_frames, 0)

    def test_write_exception_is_reported_without_retry(self):
        transport, uart, _ = make_transport()
        raw = build_init_request()
        uart.write_error = OSError("TX failed")
        with self.assertRaises(UARTTransportWriteError) as caught:
            transport.send_frame(raw)
        self.assertIsNone(caught.exception.written)
        self.assertEqual(len(uart.writes), 0)
        self.assertEqual(transport.write_errors, 1)
        event = transport.pop_activity()
        self.assertEqual(event[:3], ("tx_error", 0, raw))
        self.assertTrue(event[3]["state_unknown"])

    def test_partial_write_capture_keeps_attempt_and_accepted_prefix(self):
        transport, uart, _ = make_transport()
        raw = build_init_request()
        uart.write_result = 2
        with self.assertRaises(UARTTransportWriteError):
            transport.send_frame(raw)
        event = transport.pop_activity()
        self.assertEqual(event[:3], ("tx_partial", 0, raw))
        self.assertEqual(event[3]["written"], 2)
        self.assertEqual(event[3]["accepted"], raw[:2])

    def test_new_tx_evidence_replaces_old_activity_when_queue_is_full(self):
        transport, uart, _ = make_transport(activity_queue_capacity=1)
        uart.inject(b"junk")
        self.assertEqual(transport.poll(), [])

        raw = build_init_request()
        self.assertEqual(transport.send_frame(raw), len(raw))
        self.assertEqual(
            transport.pop_activity(), ("tx_frame", 0, raw, None)
        )
        self.assertEqual(transport.status()["activity_dropped"], 1)
        self.assertFalse(transport.status()["activity_complete"])

    def test_oversized_write_is_rejected_before_uart(self):
        transport, uart, _ = make_transport()
        with self.assertRaises(ValueError):
            transport.send_frame(bytes(263))
        self.assertEqual(uart.writes, [])

    def test_transport_never_generates_commands(self):
        transport, uart, clock = make_transport()
        for delta in (200, 1000, 10000):
            clock.advance(delta)
            self.assertEqual(transport.poll(), [])
        self.assertEqual(uart.writes, [])

    def test_deinit_is_forwarded(self):
        transport, uart, _ = make_transport()
        transport.deinit()
        self.assertTrue(uart.deinitialized)


class FakeBoardConfig:
    UART_ID = 1
    UART_TX_PIN = 33
    UART_RX_PIN = 32
    UART_BAUDRATE = 9600
    UART_BITS = 8
    UART_PARITY = None
    UART_STOP_BITS = 1
    UART_PROTOCOL_TX_ENABLED = True
    UART_INTER_BYTE_TIMEOUT_MS = 200
    UART_RX_BUFFER_SIZE = 512
    UART_MAX_READ_BYTES = 512
    UART_ACTIVITY_QUEUE_CAPACITY = 32
    UART_MAX_EMPTY_READY_READS = 3
    UART_DRIVER_TIMEOUT_MS = 0
    UART_DRIVER_TIMEOUT_CHAR_MS = 0
    UART_INVERT = 0

    @staticmethod
    def require_uart_configuration():
        return None


class TestUARTFactory(unittest.TestCase):
    def test_real_board_config_builds_official_dfr0654_uart2_profile(self):
        created = []

        def uart_factory(*args, **kwargs):
            uart = FakeUART(*args, **kwargs)
            created.append(uart)
            return uart

        transport = open_from_board_config(
            board_config,
            uart_factory,
            ticks_ms=lambda: 0,
            ticks_diff=lambda current, previous: current - previous,
        )
        uart = created[0]
        self.assertEqual(uart.constructor_args, (2,))
        self.assertEqual(uart.constructor_kwargs["tx"], 17)
        self.assertEqual(uart.constructor_kwargs["rx"], 16)
        self.assertEqual(uart.constructor_kwargs["baudrate"], 9600)

    def test_real_board_transport_blocks_protocol_tx(self):
        uart = FakeUART()
        transport = open_from_board_config(
            board_config,
            lambda *args, **kwargs: uart,
            ticks_ms=lambda: 0,
            ticks_diff=lambda current, previous: current - previous,
        )
        with self.assertRaises(UARTTransportTxDisabledError):
            transport.send_frame(build_init_request())
        self.assertEqual(uart.writes, [])
        self.assertFalse(transport.tx_enabled)
        self.assertFalse(hasattr(transport, "uart"))

    def test_factory_uses_board_config_and_nonblocking_timeouts(self):
        created = []

        def uart_factory(*args, **kwargs):
            uart = FakeUART(*args, **kwargs)
            created.append(uart)
            return uart

        transport = open_from_board_config(
            FakeBoardConfig,
            uart_factory,
            ticks_ms=lambda: 0,
            ticks_diff=lambda current, previous: current - previous,
        )
        uart = created[0]
        self.assertEqual(uart.constructor_args, (1,))
        self.assertEqual(
            uart.constructor_kwargs,
            {
                "baudrate": 9600,
                "bits": 8,
                "parity": None,
                "stop": 1,
                "tx": 33,
                "rx": 32,
                "timeout": 0,
                "timeout_char": 0,
                "rxbuf": 512,
                "invert": 0,
                "flow": 0,
            },
        )

    def test_transport_factory_remains_injectable(self):
        transport = open_from_board_config(
            FakeBoardConfig,
            FakeUART,
            ticks_ms=lambda: 0,
            ticks_diff=lambda current, previous: current - previous,
        )
        self.assertIsInstance(transport, UARTTransport)
        self.assertEqual(transport.inter_byte_timeout_ms, 200)
        self.assertEqual(transport.max_read_bytes, 512)
        self.assertEqual(transport.max_empty_ready_reads, 3)
        self.assertTrue(transport.tx_enabled)

    def test_board_tx_lock_cannot_be_overridden_or_mutated(self):
        with self.assertRaises(TypeError):
            open_from_board_config(
                board_config,
                FakeUART,
                tx_enabled=True,
                ticks_ms=lambda: 0,
                ticks_diff=lambda current, previous: current - previous,
            )

        transport = open_from_board_config(
            board_config,
            FakeUART,
            ticks_ms=lambda: 0,
            ticks_diff=lambda current, previous: current - previous,
        )
        with self.assertRaises(AttributeError):
            transport.tx_enabled = True
        with self.assertRaises(UARTTransportTxDisabledError):
            transport.send_frame(build_init_request())

    def test_public_property_override_cannot_bypass_capability(self):
        class MisleadingTransport(UARTTransport):
            @property
            def tx_enabled(self):
                return True

        uart = FakeUART()
        transport = MisleadingTransport(
            uart,
            ticks_ms=lambda: 0,
            ticks_diff=lambda current, previous: current - previous,
        )
        self.assertTrue(transport.tx_enabled)
        self.assertFalse(transport.status()["tx_enabled"])
        with self.assertRaises(UARTTransportTxDisabledError):
            transport.send_frame(build_init_request())
        self.assertEqual(uart.writes, [])

    def test_raw_uart_factory_is_not_part_of_public_protocol_api(self):
        self.assertFalse(hasattr(protocol, "open_uart_from_board_config"))
        self.assertNotIn("open_uart_from_board_config", protocol.__all__)

    def test_factory_deinitializes_uart_when_transport_construction_fails(self):
        created = []

        class TrackingUART(FakeUART):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created.append(self)

        with self.assertRaises(ValueError):
            open_from_board_config(
                FakeBoardConfig,
                TrackingUART,
                framer=FrameStreamParser(),
                ticks_ms=lambda: 0,
                ticks_diff=lambda current, previous: current - previous,
            )
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].deinitialized)

    def test_factory_cleans_up_uart_on_base_exception(self):
        uart = FakeUART()
        uart.deinit_calls = 0
        original_deinit = uart.deinit

        def tracked_deinit():
            uart.deinit_calls += 1
            original_deinit()

        uart.deinit = tracked_deinit
        with mock.patch.object(
            uart_transport_module,
            "UARTTransport",
            side_effect=KeyboardInterrupt(),
        ):
            with self.assertRaises(KeyboardInterrupt):
                open_from_board_config(
                    FakeBoardConfig,
                    lambda *args, **kwargs: uart,
                )
        self.assertEqual(uart.deinit_calls, 1)
        self.assertTrue(uart.deinitialized)

    def test_factory_guard_starts_immediately_after_uart_open(self):
        class FailingPostOpenConfig(FakeBoardConfig):
            def __getattribute__(self, name):
                if name == "UART_ACTIVITY_QUEUE_CAPACITY":
                    raise KeyboardInterrupt()
                return super().__getattribute__(name)

        class TrackingUART(FakeUART):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.deinit_calls = 0

            def deinit(self):
                self.deinit_calls += 1
                super().deinit()

        uart = TrackingUART()
        with self.assertRaises(KeyboardInterrupt):
            open_from_board_config(
                FailingPostOpenConfig(),
                lambda *args, **kwargs: uart,
            )
        self.assertEqual(uart.deinit_calls, 1)
        self.assertTrue(uart.deinitialized)

    def test_factory_retries_transient_uart_cleanup_once(self):
        class FailOnceCleanupUART(FakeUART):
            def __init__(self):
                super().__init__()
                self.deinit_calls = 0

            def deinit(self):
                self.deinit_calls += 1
                if self.deinit_calls == 1:
                    raise OSError("temporary cleanup failure")
                super().deinit()

        uart = FailOnceCleanupUART()
        with mock.patch.object(
            uart_transport_module,
            "UARTTransport",
            side_effect=ValueError("invalid transport construction"),
        ):
            with self.assertRaisesRegex(
                ValueError, "invalid transport construction"
            ):
                open_from_board_config(
                    FakeBoardConfig,
                    lambda *args, **kwargs: uart,
                )
        self.assertEqual(uart.deinit_calls, 2)
        self.assertTrue(uart.deinitialized)

    def test_factory_reports_persistent_uart_cleanup_failure(self):
        class BrokenCleanupUART(FakeUART):
            def __init__(self):
                super().__init__()
                self.deinit_calls = 0

            def deinit(self):
                self.deinit_calls += 1
                raise OSError("persistent cleanup failure")

        uart = BrokenCleanupUART()
        with mock.patch.object(
            uart_transport_module,
            "UARTTransport",
            side_effect=ValueError("invalid transport construction"),
        ):
            with self.assertRaisesRegex(
                UARTTransportError, "UART cleanup also failed"
            ):
                open_from_board_config(
                    FakeBoardConfig,
                    lambda *args, **kwargs: uart,
                )
        self.assertEqual(uart.deinit_calls, 2)


if __name__ == "__main__":
    unittest.main()
