import unittest

from app.heater_composition import (
    HeaterRuntimeError,
    build_configured_heater_runtime,
)
from protocol.autoterm_protocol import parse_frame


REAL_INIT = bytes.fromhex("AA 04 05 00 04 12 8A 00 3D D6 CB A6")
REAL_OFF_STATUS = bytes.fromhex(
    "AA 04 13 00 0F 00 01 00 10 7F 00 7A 01 21 "
    "00 00 00 00 00 00 00 00 00 60 1A 48"
)


class FakeConfigManager:
    def __init__(self, generation=2):
        self.generation = generation


class FakeScheduler:
    maximum_runtime_minutes = 120


class FakeTemperatureManager:
    stale_after_ms = 5000
    failed_after_ms = 30000
    minimum_temperature_c = -55.0
    maximum_temperature_c = 125.0

    def sensor_snapshot(self, role, now_ms):
        return {
            "role": role,
            "rom_id": None,
            "value_c": None,
            "last_valid_ms": None,
            "age_ms": None,
            "unavailable_since_ms": None,
            "unavailable_age_ms": None,
            "health": "missing",
            "usable": False,
            "present": False,
            "invalid_readings": 0,
            "failure_generation": 0,
            "assignment_revision": 0,
            "last_error": "not sampled",
        }


class FakeConfiguredRuntime:
    def __init__(self, generation=2):
        self.configuration_generation = generation
        self.scheduler = FakeScheduler()
        self.temperature_manager = FakeTemperatureManager()

    def restart_required(self, config_manager):
        return config_manager.generation != self.configuration_generation


class FakeProtocol:
    def __init__(self, cleanup_failures=0):
        self.poll_batches = []
        self.sent = []
        self.cleanup_failures = cleanup_failures
        self.deinit_calls = 0
        self.closed = False
        self.rx_faulted = False
        self.poll_error = None

    def poll_inbound(self, now_ms=None):
        if self.poll_error is not None:
            raise self.poll_error
        return self.poll_batches.pop(0) if self.poll_batches else []

    def validate_inbound_frame(self, frame):
        if not isinstance(frame, dict):
            return None
        return parse_frame(frame.get("raw"))

    def transport_status(self):
        return {"rx_faulted": self.rx_faulted, "closed": self.closed}

    def drain_activity(self, max_events):
        return []

    def reset_inbound(self):
        self.rx_faulted = False
        return True

    def request_initialization(self):
        self.sent.append("init")
        return True

    def request_status(self):
        self.sent.append("status")
        return True

    def request_start(self, **kwargs):
        self.sent.append(("start", kwargs))
        return True

    def request_shutdown(self):
        self.sent.append("shutdown")
        return True

    def deinit(self):
        self.deinit_calls += 1
        if self.cleanup_failures:
            self.cleanup_failures -= 1
            raise OSError("cleanup")
        self.closed = True


class Clock:
    def __init__(self, value=0):
        self.value = value

    def ticks_ms(self):
        return self.value

    @staticmethod
    def ticks_diff(newer, older):
        return newer - older

    @staticmethod
    def ticks_add(ticks, delta):
        return ticks + delta


class TestConfiguredHeaterRuntime(unittest.TestCase):
    def build(self, protocol=None, config=None, configured=None, clock=None):
        protocol = protocol or FakeProtocol()
        config = config or FakeConfigManager()
        configured = configured or FakeConfiguredRuntime()
        clock = clock or Clock()
        factory_calls = []

        def factory():
            factory_calls.append(True)
            return protocol

        runtime = build_configured_heater_runtime(
            config,
            configured,
            protocol_factory=factory,
            ticks_ms=clock.ticks_ms,
            ticks_diff=clock.ticks_diff,
            ticks_add=clock.ticks_add,
        )
        return runtime, protocol, config, configured, clock, factory_calls

    def test_construction_is_cold_and_start_requested_state_is_off(self):
        runtime, protocol, _, _, _, calls = self.build()
        self.assertEqual(calls, [])
        self.assertIsNone(runtime.controller)
        self.assertTrue(runtime.start())
        self.assertFalse(runtime.start())
        self.assertEqual(calls, [True])
        self.assertFalse(runtime.controller.snapshot()["requested"]["on"])
        self.assertEqual(protocol.sent, [])
        self.assertIsNone(runtime.deinit())
        self.assertTrue(protocol.closed)

    def test_step_orders_init_status_and_reaches_ready_off(self):
        runtime, protocol, _, _, clock, _ = self.build()
        runtime.start()

        self.assertTrue(runtime.step())
        self.assertEqual(protocol.sent, ["init"])

        clock.value = 10
        protocol.poll_batches.append([parse_frame(REAL_INIT)])
        self.assertTrue(runtime.step())
        self.assertEqual(protocol.sent, ["init", "status"])

        clock.value = 20
        protocol.poll_batches.append([parse_frame(REAL_OFF_STATUS)])
        self.assertTrue(runtime.step())
        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["controller"]["phase"], "ready")
        self.assertEqual(
            snapshot["controller"]["actual"]["heater_state"], "off"
        )
        self.assertEqual(snapshot["controller"]["actual"]["voltage"], 12.2)
        self.assertEqual(snapshot["rx_frames"], 2)
        self.assertEqual(snapshot["controller_operations"], 2)
        self.assertIsNone(runtime.deinit())
        self.assertTrue(protocol.closed)

    def test_close_refuses_after_io_until_heater_is_confirmed_off(self):
        runtime, protocol, _, _, clock, _ = self.build()
        runtime.start()
        runtime.step()
        with self.assertRaisesRegex(HeaterRuntimeError, "not confirmed off"):
            runtime.deinit()
        self.assertTrue(runtime.started)
        self.assertFalse(protocol.closed)

        clock.value = 10
        protocol.poll_batches.append([parse_frame(REAL_INIT)])
        runtime.step()
        clock.value = 20
        protocol.poll_batches.append([parse_frame(REAL_OFF_STATUS)])
        runtime.step()
        self.assertIsNone(runtime.deinit())
        self.assertTrue(protocol.closed)

    def test_generation_change_latches_requested_off_without_closing_uart(self):
        runtime, protocol, config, _, _, _ = self.build()
        runtime.start()
        config.generation += 1
        self.assertTrue(runtime.step())
        snapshot = runtime.snapshot()
        self.assertTrue(snapshot["restart_required"])
        self.assertTrue(snapshot["restart_pending"])
        self.assertFalse(snapshot["controller"]["requested"]["on"])
        self.assertFalse(protocol.closed)

    def test_poll_failure_is_redacted_and_controller_recovers(self):
        protocol = FakeProtocol()
        protocol.poll_error = OSError("private UART detail")
        runtime, _, _, _, _, _ = self.build(protocol=protocol)
        runtime.start()
        self.assertTrue(runtime.step())
        snapshot = runtime.snapshot()
        self.assertFalse(snapshot["faulted"])
        self.assertNotIn(
            "private UART detail",
            str(snapshot["controller"].get("last_error")),
        )
        self.assertEqual(protocol.sent, ["init"])

    def test_start_failure_cleans_protocol_and_faults(self):
        protocol = FakeProtocol()
        protocol.transport_status = lambda: None
        runtime, _, _, _, _, _ = self.build(protocol=protocol)
        with self.assertRaisesRegex(HeaterRuntimeError, "start failed"):
            runtime.start()
        self.assertTrue(protocol.closed)
        self.assertTrue(runtime.faulted)
        self.assertEqual(runtime.snapshot()["last_error"], "heater_start_failed")

    def test_cleanup_retries_once(self):
        protocol = FakeProtocol(cleanup_failures=1)
        runtime, _, _, _, _, _ = self.build(protocol=protocol)
        runtime.start()
        self.assertIsNone(runtime.deinit())
        self.assertEqual(protocol.deinit_calls, 2)

    def test_builder_rejects_generation_and_malformed_runtime_before_io(self):
        calls = []
        with self.assertRaises(ValueError):
            build_configured_heater_runtime(
                FakeConfigManager(3),
                FakeConfiguredRuntime(2),
                protocol_factory=lambda: calls.append(True),
            )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
