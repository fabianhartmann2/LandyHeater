import inspect
import math
import runpy
import unittest
from unittest import mock

import app.temperature_manager as temperature_manager_module
from app.temperature_manager import (
    SENSOR_HEALTH_FAILED,
    SENSOR_HEALTH_MISSING,
    SENSOR_HEALTH_OK,
    SENSOR_HEALTH_STALE,
    SENSOR_ROLE_CABIN,
    SENSOR_ROLE_OUTSIDE,
    SENSOR_ROLE_ROOF_TENT,
    TemperatureManager,
)


ROM_ROOF = "28-000000000001"
ROM_CABIN = "28-000000000002"
ROM_OUTSIDE = "28-000000000003"


def assigned_manager(**kwargs):
    return TemperatureManager(
        {
            SENSOR_ROLE_ROOF_TENT: ROM_ROOF,
            SENSOR_ROLE_CABIN: ROM_CABIN,
            SENSOR_ROLE_OUTSIDE: ROM_OUTSIDE,
        },
        **kwargs
    )


class TestTemperatureManagerConfiguration(unittest.TestCase):
    def test_discovery_capacity_is_read_only(self):
        manager = assigned_manager(max_discovered_sensors=3)
        self.assertEqual(manager.max_discovered_sensors, 3)
        with self.assertRaises(AttributeError):
            manager.max_discovered_sensors = 4

    def test_constructor_validates_timing_bounds_and_clock(self):
        invalid = (
            {"stale_after_ms": 0},
            {"stale_after_ms": True},
            {"failed_after_ms": 30000},
            {"ticks_diff": "not callable"},
            {"event_capacity": 0},
            {"max_discovered_sensors": 0},
            {"minimum_temperature_c": math.nan},
            {"maximum_temperature_c": math.inf},
            {"minimum_temperature_c": -1e300},
            {"maximum_temperature_c": 1e300},
            {"minimum_temperature_c": -55.01},
            {"maximum_temperature_c": 125.01},
            {
                "minimum_temperature_c": 10,
                "maximum_temperature_c": 10,
            },
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    TemperatureManager(**kwargs)

        for assignments in ([], (), ""):
            with self.subTest(assignments=assignments):
                with self.assertRaises(ValueError):
                    TemperatureManager(assignments)

        manager = TemperatureManager(
            minimum_temperature_c=-10,
            maximum_temperature_c=80,
        )
        self.assertEqual(manager.minimum_temperature_c, -10.0)
        self.assertEqual(manager.maximum_temperature_c, 80.0)

    def test_health_thresholds_are_read_only_after_construction(self):
        manager = TemperatureManager()
        with self.assertRaises(AttributeError):
            manager.stale_after_ms = 1
        with self.assertRaises(AttributeError):
            manager.failed_after_ms = 2
        with self.assertRaises(AttributeError):
            manager.minimum_temperature_c = -100
        with self.assertRaises(AttributeError):
            manager.maximum_temperature_c = 200

    def test_assignments_are_unique_normalized_and_defensively_copied(self):
        supplied = {
            SENSOR_ROLE_ROOF_TENT: " 28-ABCDEF ",
            SENSOR_ROLE_CABIN: ROM_CABIN,
        }
        manager = TemperatureManager(supplied)
        supplied[SENSOR_ROLE_ROOF_TENT] = "changed"
        self.assertEqual(
            manager.assignments[SENSOR_ROLE_ROOF_TENT], "28-abcdef"
        )
        self.assertIsNone(manager.assignments[SENSOR_ROLE_OUTSIDE])

        before = manager.assignments
        with self.assertRaises(ValueError):
            manager.configure_assignments(
                {
                    SENSOR_ROLE_ROOF_TENT: ROM_ROOF,
                    SENSOR_ROLE_CABIN: ROM_ROOF.upper(),
                }
            )
        self.assertEqual(manager.assignments, before)

        for bad in (
            {"unknown": ROM_ROOF},
            {SENSOR_ROLE_ROOF_TENT: ""},
            {SENSOR_ROLE_ROOF_TENT: b"12345678"},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    manager.configure_assignments(bad)

    def test_remap_preserves_values_by_rom_not_by_old_role(self):
        manager = assigned_manager()
        manager.record_valid(ROM_ROOF, 10, 0)
        manager.record_valid(ROM_CABIN, 20, 0)
        manager.configure_assignments(
            {
                SENSOR_ROLE_ROOF_TENT: ROM_CABIN,
                SENSOR_ROLE_CABIN: ROM_ROOF,
                SENSOR_ROLE_OUTSIDE: ROM_OUTSIDE,
            }
        )
        snapshot = manager.snapshot(1)["sensors"]
        self.assertEqual(snapshot[SENSOR_ROLE_ROOF_TENT]["value_c"], 20.0)
        self.assertEqual(snapshot[SENSOR_ROLE_CABIN]["value_c"], 10.0)

    def test_assignment_revisions_change_only_for_remapped_roles(self):
        manager = assigned_manager()
        initial = manager.assignment_revisions
        manager.configure_assignments(manager.assignments)
        self.assertEqual(manager.assignment_revisions, initial)

        manager.configure_assignments(
            {
                SENSOR_ROLE_ROOF_TENT: ROM_CABIN,
                SENSOR_ROLE_CABIN: ROM_ROOF,
                SENSOR_ROLE_OUTSIDE: ROM_OUTSIDE,
            }
        )
        changed = manager.assignment_revisions
        self.assertEqual(
            changed[SENSOR_ROLE_ROOF_TENT],
            initial[SENSOR_ROLE_ROOF_TENT] + 1,
        )
        self.assertEqual(
            changed[SENSOR_ROLE_CABIN], initial[SENSOR_ROLE_CABIN] + 1
        )
        self.assertEqual(
            changed[SENSOR_ROLE_OUTSIDE], initial[SENSOR_ROLE_OUTSIDE]
        )

    def test_unassigned_role_is_missing_without_fake_value(self):
        manager = TemperatureManager()
        sensor = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 0)
        self.assertEqual(sensor["health"], SENSOR_HEALTH_MISSING)
        self.assertIsNone(sensor["value_c"])
        self.assertIsNone(sensor["last_valid_ms"])
        self.assertIsNone(sensor["age_ms"])
        self.assertFalse(sensor["usable"])
        self.assertFalse(manager.record_valid(ROM_ROOF, 20, 0))


class TestTemperatureManagerHealth(unittest.TestCase):
    def test_valid_physical_endpoints_and_real_zero_are_preserved(self):
        manager = assigned_manager()
        for index, value in enumerate((-55, 0, 20.25, 85, 125)):
            with self.subTest(value=value):
                self.assertTrue(manager.record_valid(ROM_ROOF, value, index))
                sensor = manager.sensor_snapshot(
                    SENSOR_ROLE_ROOF_TENT, index
                )
                self.assertEqual(sensor["value_c"], float(value))
                self.assertEqual(sensor["health"], SENSOR_HEALTH_OK)

    def test_invalid_samples_never_replace_or_refresh_last_valid_value(self):
        manager = assigned_manager()
        self.assertTrue(manager.record_valid(ROM_ROOF, 0.0, 0))
        invalid = (
            None,
            True,
            "20",
            math.nan,
            math.inf,
            -math.inf,
            -55.01,
            125.01,
        )
        for index, value in enumerate(invalid, 1):
            with self.subTest(value=value):
                self.assertFalse(
                    manager.record_valid(ROM_ROOF, value, index * 1000)
                )
        stale = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 30000)
        self.assertEqual(stale["value_c"], 0.0)
        self.assertEqual(stale["last_valid_ms"], 0)
        self.assertEqual(stale["health"], SENSOR_HEALTH_STALE)
        self.assertEqual(stale["invalid_readings"], len(invalid))

    def test_health_boundaries_are_exact_and_keep_diagnostic_value(self):
        manager = assigned_manager()
        manager.record_valid(ROM_ROOF, 19.5, 0)
        cases = (
            (29999, SENSOR_HEALTH_OK, True),
            (30000, SENSOR_HEALTH_STALE, True),
            (299999, SENSOR_HEALTH_STALE, True),
            (300000, SENSOR_HEALTH_FAILED, False),
        )
        for now_ms, expected, usable in cases:
            with self.subTest(now_ms=now_ms):
                sensor = manager.sensor_snapshot(
                    SENSOR_ROLE_ROOF_TENT, now_ms
                )
                self.assertEqual(sensor["health"], expected)
                self.assertEqual(sensor["usable"], usable)
                self.assertEqual(sensor["value_c"], 19.5)

    def test_read_failure_keeps_value_and_does_not_reset_age(self):
        manager = assigned_manager()
        manager.record_valid(ROM_ROOF, 18, 0)
        for now_ms in (1000, 10000, 299999):
            self.assertTrue(
                manager.record_failure(ROM_ROOF, now_ms, "CRC failure")
            )
        sensor = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 300000)
        self.assertEqual(sensor["health"], SENSOR_HEALTH_FAILED)
        self.assertEqual(sensor["value_c"], 18.0)
        self.assertEqual(sensor["last_valid_ms"], 0)
        self.assertEqual(sensor["invalid_readings"], 3)

    def test_recovery_is_immediate_but_events_only_follow_transitions(self):
        manager = assigned_manager()
        manager.record_valid(ROM_ROOF, 18, 0)
        manager.drain_events()
        manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 30000)
        manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 30001)
        manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 300000)
        manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 300001)
        self.assertTrue(manager.record_valid(ROM_ROOF, 19, 300002))
        events = [
            event
            for event in manager.drain_events()
            if event["type"] == "sensor_health_changed"
        ]
        self.assertEqual(
            [event["details"]["current"] for event in events],
            [
                SENSOR_HEALTH_STALE,
                SENSOR_HEALTH_FAILED,
                SENSOR_HEALTH_OK,
            ],
        )

    def test_tick_wrap_preserves_stale_and_failed_boundaries(self):
        period = 1048576

        def ticks_diff(newer, older):
            half = period // 2
            return ((newer - older + half) % period) - half

        manager = assigned_manager(ticks_diff=ticks_diff)
        started = 1000000
        manager.record_valid(ROM_ROOF, 17, started)
        for age, expected in (
            (29999, SENSOR_HEALTH_OK),
            (30000, SENSOR_HEALTH_STALE),
            (299999, SENSOR_HEALTH_STALE),
            (300000, SENSOR_HEALTH_FAILED),
        ):
            with self.subTest(age=age):
                now_ms = (started + age) % period
                self.assertEqual(
                    manager.sensor_snapshot(
                        SENSOR_ROLE_ROOF_TENT, now_ms
                    )["health"],
                    expected,
                )

    def test_out_of_order_samples_and_failures_cannot_refresh_state(self):
        manager = assigned_manager()
        manager.record_valid(ROM_ROOF, 20, 100)
        self.assertFalse(manager.record_valid(ROM_ROOF, 25, 99))
        self.assertFalse(manager.record_failure(ROM_ROOF, 99, "late error"))
        sensor = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 101)
        self.assertEqual(sensor["value_c"], 20.0)
        self.assertEqual(sensor["last_valid_ms"], 100)

    def test_never_valid_assigned_sensor_becomes_failed_after_deadline(self):
        manager = assigned_manager()
        self.assertTrue(manager.record_failure(ROM_ROOF, 0, "no response"))
        missing = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 299999)
        self.assertEqual(missing["health"], SENSOR_HEALTH_MISSING)
        self.assertIsNone(missing["value_c"])
        self.assertEqual(missing["unavailable_age_ms"], 299999)

        failed = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 300000)
        self.assertEqual(failed["health"], SENSOR_HEALTH_FAILED)
        self.assertIsNone(failed["last_valid_ms"])
        self.assertEqual(failed["failure_generation"], 1)

    def test_late_recovery_preserves_a_failure_generation(self):
        manager = assigned_manager(stale_after_ms=100, failed_after_ms=200)
        manager.record_valid(ROM_ROOF, 20, 0)
        self.assertTrue(manager.record_valid(ROM_ROOF, 21, 201))
        recovered = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 201)
        self.assertEqual(recovered["health"], SENSOR_HEALTH_OK)
        self.assertEqual(recovered["failure_generation"], 1)
        transitions = [
            event["details"]["current"]
            for event in manager.drain_events()
            if event["type"] == "sensor_health_changed"
        ]
        self.assertIn(SENSOR_HEALTH_FAILED, transitions)

    def test_out_of_order_never_valid_updates_are_atomic(self):
        manager = assigned_manager()
        manager.record_failure(ROM_ROOF, 100, "first")
        before = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 100)

        self.assertFalse(manager.record_failure(ROM_ROOF, 99, "late"))
        self.assertFalse(manager.record_valid(ROM_ROOF, 20, 99))
        with self.assertRaises(ValueError):
            manager.record_discovery([ROM_ROOF], 99)
        after = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 100)
        self.assertEqual(after, before)

    def test_newer_failure_orders_every_later_sensor_mutation(self):
        manager = assigned_manager(
            stale_after_ms=100, failed_after_ms=200
        )
        manager.record_valid(ROM_ROOF, 20, 0)
        manager.record_failure(ROM_ROOF, 150, "newer failure")
        before = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 150)

        self.assertFalse(manager.record_valid(ROM_ROOF, 21, 100))
        self.assertFalse(manager.record_failure(ROM_ROOF, 100, "late"))
        with self.assertRaises(ValueError):
            manager.record_discovery([ROM_ROOF], 100)
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 150), before
        )

        manager.record_discovery([ROM_ROOF], 200)
        self.assertFalse(manager.record_valid(ROM_ROOF, 22, 199))
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 200)["value_c"],
            20.0,
        )

    def test_repeated_read_failures_do_not_flood_the_event_queue(self):
        manager = assigned_manager(event_capacity=4)
        manager.record_failure(ROM_ROOF, 0, "offline")
        manager.drain_events()
        for now_ms in range(1, 101):
            manager.record_failure(ROM_ROOF, now_ms, "offline")
        self.assertEqual(manager.drain_events(), [])
        self.assertEqual(manager.events_dropped, 0)


class TestTemperatureManagerDiscoveryAndBoundaries(unittest.TestCase):
    def test_discovery_is_bounded_atomic_and_marks_presence(self):
        manager = assigned_manager(max_discovered_sensors=3)
        self.assertEqual(
            manager.record_discovery([ROM_ROOF, ROM_OUTSIDE], 0), 2
        )
        snapshot = manager.snapshot(0)
        self.assertTrue(
            snapshot["sensors"][SENSOR_ROLE_ROOF_TENT]["present"]
        )
        self.assertFalse(snapshot["sensors"][SENSOR_ROLE_CABIN]["present"])

        before = snapshot["discovered_rom_ids"]
        for invalid in (
            (value for value in (ROM_ROOF,)),
            [ROM_ROOF, ROM_ROOF],
            [ROM_ROOF, ROM_CABIN, ROM_OUTSIDE, "28-extra"],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    manager.record_discovery(invalid, 1)
                self.assertEqual(
                    manager.snapshot(1)["discovered_rom_ids"], before
                )

    def test_snapshots_and_assignment_properties_are_detached(self):
        manager = assigned_manager()
        manager.record_valid(ROM_ROOF, 20, 0)
        snapshot = manager.snapshot(1)
        snapshot["assignments"][SENSOR_ROLE_ROOF_TENT] = "forged"
        snapshot["assignment_revisions"][SENSOR_ROLE_ROOF_TENT] = 999
        snapshot["sensors"][SENSOR_ROLE_ROOF_TENT]["value_c"] = 99
        assignments = manager.assignments
        assignments[SENSOR_ROLE_ROOF_TENT] = "forged-again"
        fresh = manager.snapshot(1)
        self.assertEqual(
            fresh["assignments"][SENSOR_ROLE_ROOF_TENT], ROM_ROOF
        )
        self.assertEqual(
            fresh["sensors"][SENSOR_ROLE_ROOF_TENT]["value_c"], 20.0
        )
        self.assertNotEqual(
            fresh["assignment_revisions"][SENSOR_ROLE_ROOF_TENT], 999
        )

    def test_event_queue_is_bounded_and_allocation_failure_is_contained(self):
        manager = assigned_manager(event_capacity=2)
        manager.record_valid(ROM_ROOF, 20, 0)
        manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 30000)
        manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 300000)
        self.assertEqual(len(manager.drain_events()), 2)
        self.assertEqual(manager.events_dropped, 1)

        class ExplodingList(list):
            def append(self, value):
                raise MemoryError("simulated event allocation failure")

        manager._events = ExplodingList()
        self.assertTrue(manager.record_valid(ROM_ROOF, 21, 300001))
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 300001)[
                "health"
            ],
            SENSOR_HEALTH_OK,
        )
        self.assertGreaterEqual(manager.event_errors, 1)

    def test_runtime_module_has_no_hardware_or_protocol_dependency(self):
        source = inspect.getsource(temperature_manager_module)
        for forbidden in (
            "import machine",
            "import onewire",
            "import ds18x20",
            "import board_config",
            "import protocol",
            "from protocol",
            "HeaterController",
        ):
            self.assertNotIn(forbidden, source)

        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name in ("machine", "onewire", "ds18x20", "board_config"):
                raise AssertionError("hardware imported by pure manager")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            namespace = runpy.run_path(
                "app/temperature_manager.py",
                run_name="temperature_manager_import_test",
            )
        self.assertIn("TemperatureManager", namespace)


if __name__ == "__main__":
    unittest.main()
