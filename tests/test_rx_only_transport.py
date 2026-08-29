import unittest

from protocol.rx_only_transport import (
    RXOnlyTransport,
    RXOnlyTransportError,
    open_rx_only_from_board_config,
)


class FakeConfig:
    BOARD_SKU = "DFR0654"
    UART_ID = 2
    UART_TX_PIN = 17
    UART_RX_PIN = 16
    UART_BAUDRATE = 9600
    UART_BITS = 8
    UART_PARITY = None
    UART_STOP_BITS = 1
    UART_PROTOCOL_TX_ENABLED = False
    UART_DRIVER_TIMEOUT_MS = 0
    UART_DRIVER_TIMEOUT_CHAR_MS = 0
    UART_INVERT = 0
    UART_RX_ONLY_BUFFER_SIZE = 2048
    UART_RX_ONLY_MAX_READ_BYTES = 128
    UART_RX_ONLY_QUEUE_CAPACITY = 64
    UART_RX_ONLY_MAX_EMPTY_READY_READS = 3

    @staticmethod
    def require_uart_configuration():
        return None


class FakePin:
    IN = 1
    events = []
    failures_remaining = 0

    def __init__(self, number, mode, pull=None, hold=None):
        self.events.append(("pin", number, mode, pull, hold))
        if self.failures_remaining:
            type(self).failures_remaining -= 1
            raise OSError("pin failed")


class FakeRawUART:
    instances = []
    events = []
    fail_on_open = False

    def __init__(self, *args, **kwargs):
        self.events.append(("uart_open", args, kwargs))
        if self.fail_on_open:
            raise OSError("open failed")
        self.constructor_args = args
        self.constructor_kwargs = kwargs
        self.rx = bytearray()
        self.writes = []
        self.read_calls = 0
        self.deinitialized = False
        self.deinit_failures_remaining = 0
        self.any_result = None
        self.read_result = None
        self.read_error = None
        self.instances.append(self)

    def inject(self, raw):
        self.rx.extend(raw)

    def any(self):
        if self.any_result is not None:
            return self.any_result
        return len(self.rx)

    def read(self, count):
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        if self.read_result is not None:
            result = self.read_result
            self.read_result = None
            return result
        raw = bytes(self.rx[:count])
        del self.rx[:count]
        return raw

    def write(self, raw):
        self.writes.append(bytes(raw))
        return len(raw)

    def deinit(self):
        self.events.append(("uart_deinit",))
        if self.deinit_failures_remaining:
            self.deinit_failures_remaining -= 1
            raise OSError("deinit failed")
        self.deinitialized = True


class FakeReader:
    def __init__(self):
        self.rx = bytearray()
        self.any_result = None
        self.read_result = None
        self.deinitialized = False

    def inject(self, raw):
        self.rx.extend(raw)

    def any(self):
        if self.any_result is not None:
            return self.any_result
        return len(self.rx)

    def read(self, count):
        if self.read_result is not None:
            result = self.read_result
            self.read_result = None
            return result
        raw = bytes(self.rx[:count])
        del self.rx[:count]
        return raw

    def deinit(self):
        self.deinitialized = True


def reset_fakes():
    FakePin.events = []
    FakePin.failures_remaining = 0
    FakeRawUART.instances = []
    FakeRawUART.events = FakePin.events
    FakeRawUART.fail_on_open = False


class TestRXOnlyFactory(unittest.TestCase):
    def setUp(self):
        reset_fakes()

    def test_guard_fails_before_pin_or_uart_side_effects(self):
        class UnsafeConfig(FakeConfig):
            UART_PROTOCOL_TX_ENABLED = True

        with self.assertRaises(RuntimeError):
            open_rx_only_from_board_config(
                UnsafeConfig,
                uart_class=FakeRawUART,
                pin_class=FakePin,
            )
        self.assertEqual(FakePin.events, [])
        self.assertEqual(FakeRawUART.instances, [])

    def test_factory_neutralizes_tx_before_and_after_uart_open(self):
        transport = open_rx_only_from_board_config(
            FakeConfig,
            uart_class=FakeRawUART,
            pin_class=FakePin,
            ticks_ms=lambda: 0,
        )
        events = FakePin.events
        self.assertEqual(events[0], ("pin", 17, FakePin.IN, None, False))
        self.assertEqual(events[1][0], "uart_open")
        self.assertEqual(events[2], ("pin", 17, FakePin.IN, None, False))
        self.assertEqual(events[3], ("pin", 17, FakePin.IN, None, False))

        uart = FakeRawUART.instances[0]
        self.assertEqual(uart.constructor_args, (2,))
        self.assertEqual(
            uart.constructor_kwargs,
            {
                "baudrate": 9600,
                "bits": 8,
                "parity": None,
                "stop": 1,
                "tx": 17,
                "rx": 16,
                "timeout": 0,
                "timeout_char": 0,
                "rxbuf": 2048,
                "invert": 0,
                "flow": 0,
            },
        )
        self.assertFalse(hasattr(transport, "write"))
        self.assertFalse(hasattr(transport, "send_frame"))
        self.assertFalse(hasattr(transport, "init"))
        self.assertFalse(hasattr(transport, "sendbreak"))
        self.assertFalse(hasattr(transport, "uart"))
        self.assertEqual(uart.writes, [])

    def test_deinit_closes_uart_and_neutralizes_tx_again(self):
        transport = open_rx_only_from_board_config(
            FakeConfig,
            uart_class=FakeRawUART,
            pin_class=FakePin,
        )
        uart = FakeRawUART.instances[0]
        pin_calls_before = len(
            [event for event in FakePin.events if event[0] == "pin"]
        )
        transport.deinit()
        self.assertTrue(uart.deinitialized)
        self.assertEqual(uart.writes, [])
        pin_calls_after = len(
            [event for event in FakePin.events if event[0] == "pin"]
        )
        self.assertEqual(pin_calls_after, pin_calls_before + 1)
        transport.deinit()
        self.assertEqual(
            len([event for event in FakePin.events if event[0] == "pin"]),
            pin_calls_after,
        )

    def test_uart_open_failure_leaves_tx_as_input(self):
        FakeRawUART.fail_on_open = True
        with self.assertRaises(OSError):
            open_rx_only_from_board_config(
                FakeConfig,
                uart_class=FakeRawUART,
                pin_class=FakePin,
            )
        pin_events = [item for item in FakePin.events if item[0] == "pin"]
        self.assertGreaterEqual(len(pin_events), 2)
        self.assertTrue(
            all(
                item == ("pin", 17, FakePin.IN, None, False)
                for item in pin_events
            )
        )

    def test_driver_deinit_failure_can_be_retried(self):
        transport = open_rx_only_from_board_config(
            FakeConfig,
            uart_class=FakeRawUART,
            pin_class=FakePin,
        )
        uart = FakeRawUART.instances[0]
        uart.deinit_failures_remaining = 1
        with self.assertRaisesRegex(OSError, "deinit failed"):
            transport.deinit()
        self.assertTrue(transport.closed)
        self.assertFalse(transport.cleanup_complete)
        transport.deinit()
        self.assertTrue(uart.deinitialized)
        self.assertTrue(transport.cleanup_complete)

    def test_pin_neutralization_failure_can_be_retried(self):
        transport = open_rx_only_from_board_config(
            FakeConfig,
            uart_class=FakeRawUART,
            pin_class=FakePin,
        )
        FakePin.failures_remaining = 1
        with self.assertRaisesRegex(
            RXOnlyTransportError, "failed to neutralize GPIO17"
        ):
            transport.deinit()
        self.assertFalse(transport.cleanup_complete)
        transport.deinit()
        self.assertTrue(transport.cleanup_complete)

    def test_factory_never_calls_raw_uart_write(self):
        transport = open_rx_only_from_board_config(
            FakeConfig,
            uart_class=FakeRawUART,
            pin_class=FakePin,
        )
        uart = FakeRawUART.instances[0]
        uart.inject(b"\x00\xaa\xff")
        self.assertEqual(transport.poll(10), [b"\x00\xaa\xff"])
        transport.deinit()
        self.assertEqual(uart.writes, [])


class TestRXOnlyTransport(unittest.TestCase):
    def test_preserves_arbitrary_bytes_and_chunk_boundaries(self):
        reader = FakeReader()
        transport = RXOnlyTransport(reader, ticks_ms=lambda: 0)
        reader.inject(b"\x00\xaa")
        self.assertEqual(transport.poll(10), [b"\x00\xaa"])
        reader.inject(b"\xffnot-a-frame")
        self.assertEqual(transport.poll(11), [b"\xffnot-a-frame"])
        self.assertEqual(
            transport.drain_chunks(),
            [
                (0, 10, b"\x00\xaa"),
                (1, 11, b"\xffnot-a-frame"),
            ],
        )

    def test_queue_overflow_keeps_newest_and_counts_exact_loss(self):
        reader = FakeReader()
        transport = RXOnlyTransport(reader, queue_capacity=2)
        for timestamp, raw in enumerate((b"a", b"bb", b"ccc")):
            reader.inject(raw)
            transport.poll(timestamp)
        self.assertEqual(
            transport.drain_chunks(),
            [(1, 1, b"bb"), (2, 2, b"ccc")],
        )
        status = transport.status()
        self.assertEqual(status["dropped_chunks"], 1)
        self.assertEqual(status["dropped_bytes"], 1)
        self.assertFalse(status["complete"])

    def test_any_is_readiness_not_an_exact_length(self):
        reader = FakeReader()
        transport = RXOnlyTransport(reader, max_read_bytes=128)
        reader.inject(bytes(range(100)))
        reader.any_result = 1
        self.assertEqual(transport.poll(1), [bytes(range(100))])

    def test_ready_but_empty_faults_after_bounded_streak(self):
        reader = FakeReader()
        reader.any_result = 1
        transport = RXOnlyTransport(reader, max_empty_ready_reads=3)
        for timestamp in range(3):
            reader.read_result = None
            self.assertEqual(transport.poll(timestamp), [])
        self.assertTrue(transport.status()["rx_faulted"])
        self.assertEqual(transport.status()["read_errors"], 3)

    def test_idle_poll_breaks_empty_ready_streak(self):
        reader = FakeReader()
        transport = RXOnlyTransport(reader, max_empty_ready_reads=2)
        reader.any_result = 1
        reader.read_result = None
        transport.poll(0)
        reader.any_result = 0
        transport.poll(1)
        reader.any_result = 1
        reader.read_result = None
        transport.poll(2)
        self.assertFalse(transport.status()["rx_faulted"])

    def test_invalid_read_is_contained_and_marks_capture_incomplete(self):
        reader = FakeReader()
        reader.any_result = 1
        reader.read_result = "not bytes"
        transport = RXOnlyTransport(reader)
        self.assertEqual(transport.poll(0), [])
        self.assertEqual(transport.status()["read_errors"], 1)
        self.assertFalse(transport.status()["complete"])

    def test_closed_transport_cannot_poll(self):
        reader = FakeReader()
        transport = RXOnlyTransport(reader)
        transport.deinit()
        self.assertTrue(reader.deinitialized)
        with self.assertRaises(RXOnlyTransportError):
            transport.poll()

    def test_reader_with_write_surface_is_rejected(self):
        class WritableReader(FakeReader):
            def write(self, raw):
                raise AssertionError("must never be called")

        with self.assertRaises(ValueError):
            RXOnlyTransport(WritableReader())


if __name__ == "__main__":
    unittest.main()
