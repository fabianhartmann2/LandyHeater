import unittest

from app.sensor_composition import (
    SensorRuntimeError,
    build_configured_sensor_runtime,
)
from app.temperature_manager import TemperatureManager


ASSIGNMENTS = {
    "roof_tent": "286ed3bd0b000013",
    "cabin": "28875f270d00006d",
    "outside": "28159f270d000090",
}


class FakeConfigManager:
    def __init__(self, generation=2):
        self.generation = generation


class FakeTemperatureManager:
    def __init__(self, assignments=None):
        self.assignments = dict(ASSIGNMENTS if assignments is None else assignments)


class FakeConfiguredRuntime:
    def __init__(self, manager=None, generation=2):
        self.temperature_manager = manager or FakeTemperatureManager()
        self.configuration_generation = generation

    def restart_required(self, config_manager):
        return config_manager.generation != self.configuration_generation


class FakeAdapter:
    def __init__(self, steps=None, cleanup_failures=0):
        self.step_plan = list(steps or (1, 0))
        self.cleanup_failures = cleanup_failures
        self.step_times = []
        self.deinit_calls = 0
        self.closed = False

    def step(self, now_ms):
        self.step_times.append(now_ms)
        value = self.step_plan.pop(0) if self.step_plan else 0
        if isinstance(value, BaseException):
            raise value
        return value

    def status(self):
        return {"closed": self.closed, "steps": len(self.step_times)}

    def deinit(self):
        self.deinit_calls += 1
        if self.cleanup_failures:
            self.cleanup_failures -= 1
            raise OSError("cleanup")
        self.closed = True
        return True


class RecordingAdapter(FakeAdapter):
    def __init__(self, manager):
        super().__init__((1,))
        self.manager = manager

    def step(self, now_ms):
        self.step_times.append(now_ms)
        self.manager.record_discovery(tuple(ASSIGNMENTS.values()), now_ms)
        for role, value_c in (
            ("roof_tent", 11.25),
            ("cabin", 19.5),
            ("outside", 7.0),
        ):
            self.manager.record_valid(ASSIGNMENTS[role], value_c, now_ms)
        return 1


class TestConfiguredSensorRuntime(unittest.TestCase):
    def build(self, adapter=None, manager=None, clock=None):
        config = FakeConfigManager()
        configured = FakeConfiguredRuntime(manager=manager)
        calls = []
        adapter = adapter or FakeAdapter()

        def factory(temperature_manager):
            calls.append(temperature_manager)
            return adapter

        times = iter((100, 101, 102))
        runtime = build_configured_sensor_runtime(
            config,
            configured,
            adapter_factory=factory,
            ticks_ms=clock or (lambda: next(times)),
        )
        return runtime, config, configured, adapter, calls

    def test_construction_is_cold_and_start_opens_exactly_once(self):
        runtime, _, configured, adapter, calls = self.build()
        self.assertEqual(calls, [])
        self.assertFalse(runtime.started)
        self.assertIs(runtime.temperature_manager, configured.temperature_manager)
        self.assertTrue(runtime.start())
        self.assertFalse(runtime.start())
        self.assertEqual(calls, [configured.temperature_manager])
        self.assertFalse(adapter.closed)

    def test_step_is_bounded_and_snapshot_counts_actions(self):
        runtime, _, _, adapter, _ = self.build(FakeAdapter((1, 0)))
        runtime.start()
        self.assertTrue(runtime.step())
        self.assertFalse(runtime.step())
        self.assertEqual(adapter.step_times, [100, 101])
        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["steps"], 2)
        self.assertEqual(snapshot["actions"], 1)
        self.assertFalse(snapshot["faulted"])

    def test_step_updates_the_shared_product_temperature_manager(self):
        manager = TemperatureManager(ASSIGNMENTS)
        config = FakeConfigManager()
        configured = FakeConfiguredRuntime(manager=manager)
        adapters = []

        def factory(shared_manager):
            adapter = RecordingAdapter(shared_manager)
            adapters.append(adapter)
            return adapter

        runtime = build_configured_sensor_runtime(
            config,
            configured,
            adapter_factory=factory,
            ticks_ms=lambda: 500,
        )
        runtime.start()
        self.assertTrue(runtime.step())

        snapshot = manager.snapshot(500)
        self.assertEqual(snapshot["discovered_rom_ids"], tuple(ASSIGNMENTS.values()))
        self.assertEqual(
            {
                role: snapshot["sensors"][role]["value_c"]
                for role in ASSIGNMENTS
            },
            {"roof_tent": 11.25, "cabin": 19.5, "outside": 7.0},
        )
        self.assertTrue(
            all(snapshot["sensors"][role]["usable"] for role in ASSIGNMENTS)
        )
        self.assertIs(adapters[0].manager, runtime.temperature_manager)

    def test_all_three_unique_assignments_are_required_before_hardware(self):
        variants = (
            {"roof_tent": None, "cabin": "b", "outside": "c"},
            {"roof_tent": "a", "cabin": "a", "outside": "c"},
            {"roof_tent": "a", "cabin": "b"},
        )
        for assignments in variants:
            with self.subTest(assignments=assignments):
                config = FakeConfigManager()
                configured = FakeConfiguredRuntime(
                    manager=FakeTemperatureManager(assignments)
                )
                calls = []
                with self.assertRaises(ValueError):
                    build_configured_sensor_runtime(
                        config,
                        configured,
                        adapter_factory=lambda manager: calls.append(manager),
                    )
                self.assertEqual(calls, [])

    def test_generation_change_during_start_cleans_adapter_and_faults(self):
        config = FakeConfigManager()
        configured = FakeConfiguredRuntime()
        adapter = FakeAdapter()

        def factory(manager):
            config.generation += 1
            return adapter

        runtime = build_configured_sensor_runtime(
            config, configured, adapter_factory=factory
        )
        with self.assertRaisesRegex(SensorRuntimeError, "start failed"):
            runtime.start()
        self.assertTrue(adapter.closed)
        self.assertTrue(runtime.faulted)
        self.assertEqual(runtime.snapshot()["last_error"], "sensor_start_failed")

    def test_generation_change_during_poll_closes_before_raising(self):
        runtime, config, _, adapter, _ = self.build()
        runtime.start()
        config.generation += 1
        with self.assertRaisesRegex(SensorRuntimeError, "configuration changed"):
            runtime.step()
        self.assertTrue(adapter.closed)
        self.assertTrue(runtime.faulted)
        self.assertFalse(runtime.started)

    def test_adapter_failure_is_redacted_and_cleanup_is_confirmed(self):
        adapter = FakeAdapter((OSError("driver secret"),))
        runtime, _, _, _, _ = self.build(adapter)
        runtime.start()
        with self.assertRaisesRegex(SensorRuntimeError, "sensor step failed") as caught:
            runtime.step()
        self.assertNotIn("driver secret", str(caught.exception))
        self.assertTrue(adapter.closed)
        self.assertEqual(runtime.snapshot()["last_error"], "sensor_step_failed")

    def test_cleanup_retries_once_and_deinit_is_idempotent(self):
        adapter = FakeAdapter(cleanup_failures=1)
        runtime, _, _, _, _ = self.build(adapter)
        runtime.start()
        self.assertIsNone(runtime.deinit())
        self.assertIsNone(runtime.deinit())
        self.assertEqual(adapter.deinit_calls, 2)
        self.assertTrue(runtime.closed)
        self.assertFalse(runtime.started)
        self.assertTrue(runtime.snapshot()["cleanup_complete"])

    def test_cleanup_can_be_retried_after_both_immediate_attempts_fail(self):
        adapter = FakeAdapter(cleanup_failures=2)
        runtime, _, _, _, _ = self.build(adapter)
        runtime.start()
        with self.assertRaisesRegex(SensorRuntimeError, "sensor cleanup failed"):
            runtime.deinit()
        self.assertTrue(runtime.closed)
        self.assertFalse(runtime.snapshot()["cleanup_complete"])
        self.assertIsNone(runtime.deinit())
        self.assertEqual(adapter.deinit_calls, 3)
        self.assertTrue(runtime.snapshot()["cleanup_complete"])

    def test_malformed_adapter_and_clock_fail_closed(self):
        config = FakeConfigManager()
        configured = FakeConfiguredRuntime()
        runtime = build_configured_sensor_runtime(
            config,
            configured,
            adapter_factory=lambda manager: object(),
        )
        with self.assertRaisesRegex(SensorRuntimeError, "start failed"):
            runtime.start()
        self.assertTrue(runtime.faulted)

        runtime, _, _, adapter, _ = self.build(clock=lambda: None)
        runtime.start()
        with self.assertRaisesRegex(SensorRuntimeError, "clock is malformed"):
            runtime.step()
        self.assertTrue(adapter.closed)

    def test_adapter_status_failure_after_creation_is_cleaned_up(self):
        adapter = FakeAdapter()

        def broken_status():
            raise OSError("private driver detail")

        adapter.status = broken_status
        runtime, _, _, _, _ = self.build(adapter)
        with self.assertRaisesRegex(SensorRuntimeError, "sensor start failed") as caught:
            runtime.start()
        self.assertNotIn("private driver detail", str(caught.exception))
        self.assertTrue(adapter.closed)
        self.assertEqual(adapter.deinit_calls, 1)


if __name__ == "__main__":
    unittest.main()
