import inspect
import unittest

from tools import phase13_heater_runtime_probe as probe


class FakeUART:
    def __init__(self):
        self.writes = []
        self.done = True

    def any(self):
        return 0

    def read(self, count):
        return None

    def write(self, data):
        raw = bytes(data)
        self.writes.append(raw)
        return len(raw)

    def txdone(self):
        return self.done


class HighPin:
    def __init__(self, number):
        self.number = number

    def value(self):
        return 1


class FakeService:
    def __init__(self):
        self.calls = []

    def poll_inbound(self, now_ms=None):
        self.calls.append(("poll", now_ms))
        return []

    def validate_inbound_frame(self, frame):
        return frame

    def transport_status(self):
        return {"rx_faulted": False}

    def drain_activity(self, maximum):
        return []

    def reset_inbound(self):
        return True

    def request_initialization(self):
        self.calls.append("init")
        return True

    def request_status(self):
        self.calls.append("status")
        return True

    def deinit(self):
        self.calls.append("deinit")


class TestPhase13HeaterRuntimeProbe(unittest.TestCase):
    def build_uart_boundary(self):
        raw = FakeUART()
        boundary = probe._SyncOnlyUART(
            raw,
            HighPin,
            14,
            13,
            ticks_ms=lambda: 0,
            ticks_diff=lambda newer, older: newer - older,
            sleep_ms=lambda delay: None,
        )
        return boundary, raw

    def test_import_is_inert_and_confirmations_are_distinct(self):
        self.assertFalse(probe._armed)
        self.assertIsNone(probe._runtime)
        self.assertIn("HEATER_OFF", probe.ARM_CONFIRMATION)
        self.assertIn("INIT_STATUS_ONLY", probe.TRIGGER_CONFIRMATION)
        self.assertNotEqual(probe.ARM_CONFIRMATION, probe.TRIGGER_CONFIRMATION)

    def test_wrong_confirmations_stop_before_target_hardware(self):
        with self.assertRaisesRegex(RuntimeError, "exact arm confirmation"):
            probe.arm("wrong")
        with self.assertRaisesRegex(RuntimeError, "exact trigger confirmation"):
            probe.trigger("wrong")

    def test_uart_boundary_allows_exactly_one_ordered_init_and_status(self):
        boundary, raw = self.build_uart_boundary()
        self.assertEqual(boundary.write(probe.EXPECTED_INIT_REQUEST), 7)
        self.assertEqual(boundary.write(probe.EXPECTED_STATUS_REQUEST), 7)
        self.assertEqual(
            raw.writes,
            [probe.EXPECTED_INIT_REQUEST, probe.EXPECTED_STATUS_REQUEST],
        )
        for rejected in (
            probe.EXPECTED_INIT_REQUEST,
            probe.EXPECTED_STATUS_REQUEST,
            b"dangerous",
        ):
            with self.assertRaisesRegex(RuntimeError, "write blocked"):
                boundary.write(rejected)
        self.assertEqual(len(raw.writes), 2)

    def test_status_before_init_is_blocked(self):
        boundary, raw = self.build_uart_boundary()
        with self.assertRaisesRegex(RuntimeError, "write blocked"):
            boundary.write(probe.EXPECTED_STATUS_REQUEST)
        self.assertEqual(raw.writes, [])

    def test_protocol_facade_blocks_control_commands(self):
        service = FakeService()
        port = probe._SyncOnlyProtocolPort(service)
        self.assertTrue(port.request_initialization())
        self.assertTrue(port.request_status())
        with self.assertRaisesRegex(RuntimeError, "START is unavailable"):
            port.request_start("power", power_level=1)
        with self.assertRaisesRegex(RuntimeError, "SHUTDOWN is unavailable"):
            port.request_shutdown()
        self.assertEqual(service.calls, ["init", "status"])

    def test_source_has_one_raw_write_site_and_no_flash_or_config_commit(self):
        source = inspect.getsource(probe)
        self.assertEqual(source.count("self._uart.write(raw)"), 1)
        self.assertIn("raw == EXPECTED_INIT_REQUEST", source)
        self.assertIn("raw == EXPECTED_STATUS_REQUEST", source)
        self.assertNotIn("build_start", source)
        self.assertNotIn("build_shutdown", source)
        self.assertNotIn("write_flash", source)
        self.assertNotIn("erase_flash", source)
        self.assertNotIn("manager.commit", source)
        self.assertNotIn("checkpoint_scheduler", source)


if __name__ == "__main__":
    unittest.main()
