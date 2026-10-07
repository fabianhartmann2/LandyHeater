import inspect
import unittest

from tools import phase13_heater_active_cycle_probe as probe


class FakeService:
    def __init__(self):
        self.calls = []

    def request_initialization(self):
        self.calls.append("init")
        return True

    def request_status(self):
        self.calls.append("status")
        return True

    def request_start(self, mode, target_temperature=None, power_level=None):
        self.calls.append(("start", mode, target_temperature, power_level))
        return True

    def request_shutdown(self):
        self.calls.append("shutdown")
        return True

    def poll_inbound(self, now_ms=None):
        return []

    def validate_inbound_frame(self, frame):
        return frame

    def transport_status(self):
        return {"tx_enabled": True, "rx_faulted": False}

    def drain_activity(self, maximum):
        return []

    def reset_inbound(self):
        return True

    def deinit(self):
        self.calls.append("deinit")


class TestPhase13HeaterActiveCycleProbe(unittest.TestCase):
    def test_fixed_envelope_allows_five_minute_start_plus_stable_time(self):
        self.assertEqual(probe.POWER_LEVEL, 1)
        self.assertEqual(probe.RUNTIME_MINUTES, 7)
        self.assertGreaterEqual(probe.RUNTIME_MINUTES, 5)
        self.assertEqual(probe.MAX_CONTROL_ATTEMPTS, 2)
        self.assertGreater(probe.TOTAL_CYCLE_TIMEOUT_MS, 7 * 60 * 1000)

    def test_wrong_confirmations_stop_before_target_hardware(self):
        with self.assertRaisesRegex(RuntimeError, "exact arm confirmation"):
            probe.arm("wrong")
        with self.assertRaisesRegex(RuntimeError, "exact cycle confirmation"):
            probe.run_cycle("wrong")
        with self.assertRaisesRegex(RuntimeError, "exact power-removed"):
            probe.emergency_power_removed("wrong")

    def test_protocol_envelope_accepts_only_fixed_power_start_and_two_attempts(self):
        service = FakeService()
        port = probe._ActiveCycleProtocolPort(service)
        self.assertTrue(port.request_start("power", power_level=1))
        self.assertTrue(port.request_start("power", power_level=1))
        with self.assertRaisesRegex(RuntimeError, "START attempt bound"):
            port.request_start("power", power_level=1)
        with self.assertRaisesRegex(RuntimeError, "START power differs"):
            probe._ActiveCycleProtocolPort(service).request_start(
                "power", power_level=2
            )
        with self.assertRaisesRegex(RuntimeError, "START mode differs"):
            probe._ActiveCycleProtocolPort(service).request_start(
                "roof_tent_temperature", target_temperature=20
            )

    def test_protocol_envelope_bounds_shutdown(self):
        service = FakeService()
        port = probe._ActiveCycleProtocolPort(service)
        self.assertTrue(port.request_shutdown())
        self.assertTrue(port.request_shutdown())
        with self.assertRaisesRegex(RuntimeError, "SHUTDOWN attempt bound"):
            port.request_shutdown()

    def test_source_has_no_flash_config_commit_external_temperature_or_raw_send(self):
        source = inspect.getsource(probe)
        self.assertNotIn("write_flash", source)
        self.assertNotIn("erase_flash", source)
        self.assertNotIn("manager.commit", source)
        self.assertNotIn("checkpoint_scheduler", source)
        self.assertNotIn("external_temperature", source)
        self.assertNotIn("send_frame", source)
        self.assertIn("request_start", source)
        self.assertIn("request_shutdown", source)
        self.assertIn("saw_running", source)
        self.assertIn("heater_state=off", source)


if __name__ == "__main__":
    unittest.main()
