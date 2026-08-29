import json
import unittest

from services.protocol_capture import ProtocolCaptureSession


class FakeConfig:
    UART_ID = 2
    UART_RX_PIN = 16
    UART_BAUDRATE = 9600
    UART_BITS = 8
    UART_PARITY = None
    UART_STOP_BITS = 1


class FakeTransport:
    def __init__(self):
        self.values = {
            "rx_bytes": 0,
            "rx_chunks": 0,
            "read_errors": 0,
            "rx_faulted": False,
            "dropped_chunks": 0,
            "dropped_bytes": 0,
            "queued_chunks": 0,
        }

    def status(self):
        return dict(self.values)


def modular_diff(current, previous):
    modulus = 1024
    half = modulus // 2
    return ((current - previous + half) % modulus) - half


class TestProtocolCaptureSession(unittest.TestCase):
    def test_records_are_json_serializable_and_raw_bytes_are_exact(self):
        transport = FakeTransport()
        session = ProtocolCaptureSession(
            transport,
            FakeConfig,
            "heater_off",
            100,
            lambda current, previous: current - previous,
        )
        start = session.start_record()
        chunk = session.chunk_record((7, 112, b"\x00\xaa\xff"))
        transport.values["rx_bytes"] = 3
        transport.values["rx_chunks"] = 1
        end = session.end_record(130)

        self.assertEqual(start["schema"], "landy-heater.rx-capture")
        self.assertEqual(start["gpio17_required"], "physically_disconnected")
        self.assertFalse(start["tx_software_enabled"])
        self.assertEqual(
            chunk,
            {
                "type": "rx_chunk",
                "seq": 7,
                "offset_ms": 12,
                "length": 3,
                "raw_hex": "00aaff",
            },
        )
        self.assertTrue(end["complete"])
        for record in (start, chunk, end):
            self.assertIsInstance(json.dumps(record), str)

    def test_end_uses_per_capture_loss_baselines(self):
        transport = FakeTransport()
        transport.values["dropped_chunks"] = 5
        transport.values["dropped_bytes"] = 20
        session = ProtocolCaptureSession(
            transport,
            FakeConfig,
            "status",
            0,
            lambda current, previous: current - previous,
        )
        transport.values.update(
            {
                "rx_bytes": 30,
                "rx_chunks": 4,
                "dropped_chunks": 7,
                "dropped_bytes": 29,
            }
        )
        end = session.end_record(20)
        self.assertEqual(end["dropped_chunks"], 2)
        self.assertEqual(end["dropped_bytes"], 9)
        self.assertFalse(end["complete"])

    def test_unexported_queue_or_accounting_mismatch_is_incomplete(self):
        transport = FakeTransport()
        session = ProtocolCaptureSession(
            transport,
            FakeConfig,
            "pending",
            0,
            lambda current, previous: current - previous,
        )
        transport.values.update(
            {"rx_bytes": 1, "rx_chunks": 1, "queued_chunks": 1}
        )
        end = session.end_record(1)
        self.assertFalse(end["complete"])
        self.assertEqual(end["emitted_chunks"], 0)
        self.assertEqual(end["rx_chunks"], 1)

    def test_final_drain_limit_marks_capture_incomplete(self):
        transport = FakeTransport()
        session = ProtocolCaptureSession(
            transport,
            FakeConfig,
            "limit",
            0,
            lambda current, previous: current - previous,
        )
        self.assertFalse(
            session.end_record(1, final_drain_limit_reached=True)["complete"]
        )

    def test_read_error_fault_interrupt_and_run_error_mark_incomplete(self):
        for changes, kwargs in (
            ({"read_errors": 1}, {}),
            ({"rx_faulted": True}, {}),
            ({}, {"interrupted": True}),
            ({}, {"run_error": OSError("failed")}),
        ):
            with self.subTest(changes=changes, kwargs=kwargs):
                transport = FakeTransport()
                session = ProtocolCaptureSession(
                    transport,
                    FakeConfig,
                    "test",
                    0,
                    lambda current, previous: current - previous,
                )
                transport.values.update(changes)
                self.assertFalse(session.end_record(1, **kwargs)["complete"])

    def test_chunk_offset_is_wrap_safe(self):
        transport = FakeTransport()
        session = ProtocolCaptureSession(
            transport,
            FakeConfig,
            "wrap",
            1000,
            modular_diff,
        )
        record = session.chunk_record((0, 10, b"x"))
        self.assertEqual(record["offset_ms"], 34)


if __name__ == "__main__":
    unittest.main()
