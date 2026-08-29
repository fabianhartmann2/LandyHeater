import unittest

from app.application_state import (
    CONTROL_MODE_CABIN_TEMPERATURE,
    CONTROL_MODE_POWER,
    CONTROL_MODE_ROOF_TENT_TEMPERATURE,
)
from app.heater_controller import PHASE_ERROR, HeaterController
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
from tests.test_heater_controller import (
    REAL_OFF_STATUS,
    RecordingProtocolPort,
    status_frame,
    synchronize,
)
from protocol.autoterm_protocol import parse_frame


ROM_ROOF = "28-controller-roof"
ROM_CABIN = "28-controller-cabin"
ROM_OUTSIDE = "28-controller-outside"


def assigned_manager(stale_after_ms=30000, failed_after_ms=300000):
    return TemperatureManager(
        {
            SENSOR_ROLE_ROOF_TENT: ROM_ROOF,
            SENSOR_ROLE_CABIN: ROM_CABIN,
            SENSOR_ROLE_OUTSIDE: ROM_OUTSIDE,
        },
        stale_after_ms=stale_after_ms,
        failed_after_ms=failed_after_ms,
    )


def running_temperature_controller(state=4):
    temperatures = assigned_manager(
        stale_after_ms=1000, failed_after_ms=2000
    )
    temperatures.record_valid(ROM_ROOF, 20, 0)
    port = RecordingProtocolPort()
    controller = HeaterController(port, temperature_manager=temperatures)
    controller.request_start(
        CONTROL_MODE_ROOF_TENT_TEMPERATURE,
        target_temperature=20,
    )
    synchronize(controller, parse_frame(REAL_OFF_STATUS))
    temperatures.record_valid(ROM_ROOF, 20, 219)
    if controller.step(220) != ["start"]:
        raise AssertionError("temperature session did not start")
    temperatures.record_valid(ROM_ROOF, 20, 1200)
    if controller.step(1220) != ["status"]:
        raise AssertionError("controller did not request confirmation status")
    if not controller.handle_frame(status_frame(state), 1230):
        raise AssertionError("controller rejected confirmation status")
    return controller, port, temperatures


def refresh_running_status(controller, state=4):
    if controller.step(2230) != ["status"]:
        raise AssertionError("controller did not refresh heater status")
    if not controller.handle_frame(status_frame(state), 2240):
        raise AssertionError("controller rejected refreshed heater status")


class TestSensorStartPolicy(unittest.TestCase):
    def test_temperature_modes_select_only_their_matching_sensor_role(self):
        cases = (
            (
                CONTROL_MODE_ROOF_TENT_TEMPERATURE,
                SENSOR_ROLE_ROOF_TENT,
                ROM_ROOF,
            ),
            (
                CONTROL_MODE_CABIN_TEMPERATURE,
                SENSOR_ROLE_CABIN,
                ROM_CABIN,
            ),
        )
        for mode, role, rom_id in cases:
            with self.subTest(mode=mode):
                temperatures = assigned_manager()
                temperatures.record_valid(rom_id, 18, 0)
                port = RecordingProtocolPort()
                controller = HeaterController(
                    port, temperature_manager=temperatures
                )
                controller.request_start(mode, target_temperature=20)
                synchronize(controller, parse_frame(REAL_OFF_STATUS))

                self.assertEqual(controller.step(220), ["start"])
                snapshot = controller.snapshot()
                self.assertEqual(snapshot["active_sensor"]["role"], role)
                self.assertEqual(
                    snapshot["active_sensor"]["health"], SENSOR_HEALTH_OK
                )
                start = [
                    details
                    for name, details in port.calls
                    if name == "start"
                ]
                self.assertEqual(len(start), 1)
                self.assertEqual(start[0]["mode"], mode)

    def test_power_mode_has_no_active_regulation_sensor(self):
        temperatures = assigned_manager()
        port = RecordingProtocolPort()
        controller = HeaterController(port, temperature_manager=temperatures)
        controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(controller, parse_frame(REAL_OFF_STATUS))

        self.assertEqual(controller.step(220), ["start"])
        self.assertIsNone(controller.snapshot()["active_sensor"])

    def test_power_request_clears_an_old_temperature_start_alert(self):
        temperatures = assigned_manager()
        port = RecordingProtocolPort()
        controller = HeaterController(port, temperature_manager=temperatures)
        controller.request_start(
            CONTROL_MODE_ROOF_TENT_TEMPERATURE,
            target_temperature=20,
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        self.assertIsNotNone(controller.snapshot()["sensor_alert"])

        controller.request_start(CONTROL_MODE_POWER, power_level=5)
        cleared = controller.snapshot()
        self.assertIsNone(cleared["sensor_alert"])
        self.assertIsNone(cleared["last_error"])
        self.assertEqual(controller.step(220), ["start"])

    def test_forged_out_of_range_temperature_cannot_authorize_start(self):
        class ForgedHealthPort:
            stale_after_ms = 30000
            failed_after_ms = 300000
            minimum_temperature_c = -55.0
            maximum_temperature_c = 125.0

            def sensor_snapshot(self, role, now_ms):
                return {
                    "role": role,
                    "rom_id": "28-forged",
                    "value_c": 126.0,
                    "last_valid_ms": now_ms,
                    "age_ms": 0,
                    "unavailable_since_ms": None,
                    "unavailable_age_ms": None,
                    "health": SENSOR_HEALTH_OK,
                    "usable": True,
                    "present": True,
                    "invalid_readings": 0,
                    "failure_generation": 0,
                    "last_error": None,
                }

        port = RecordingProtocolPort()
        controller = HeaterController(
            port, temperature_manager=ForgedHealthPort()
        )
        controller.request_start(
            CONTROL_MODE_ROOF_TENT_TEMPERATURE,
            target_temperature=20,
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(controller.step(220), [])
        self.assertFalse(controller.requested["on"])
        self.assertNotIn("start", [name for name, _ in port.calls])

    def test_missing_sensor_blocks_start_and_recovery_never_auto_starts(self):
        temperatures = assigned_manager()
        port = RecordingProtocolPort()
        controller = HeaterController(port, temperature_manager=temperatures)
        controller.request_start(
            CONTROL_MODE_ROOF_TENT_TEMPERATURE,
            target_temperature=20,
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))

        self.assertFalse(controller.requested["on"])
        self.assertEqual(controller.step(220), [])
        self.assertNotIn("start", [name for name, _ in port.calls])
        snapshot = controller.snapshot()
        self.assertIsNone(snapshot["active_sensor"])
        self.assertEqual(
            snapshot["sensor_alert"]["health"], SENSOR_HEALTH_MISSING
        )
        self.assertEqual(snapshot["sensor_alert"]["action"], "start_blocked")

        temperatures.record_valid(ROM_ROOF, 20, 30)
        self.assertEqual(controller.step(221), [])
        self.assertNotIn("start", [name for name, _ in port.calls])

        controller.request_start(
            CONTROL_MODE_ROOF_TENT_TEMPERATURE,
            target_temperature=20,
        )
        self.assertEqual(controller.step(1020), ["status"])
        controller.handle_frame(parse_frame(REAL_OFF_STATUS), 1030)
        self.assertEqual(controller.step(1230), ["start"])

    def test_never_valid_assigned_sensor_reaches_failed_not_synthetic_missing(self):
        temperatures = assigned_manager(
            stale_after_ms=100, failed_after_ms=200
        )
        temperatures.record_failure(ROM_ROOF, 0, "sensor offline")
        port = RecordingProtocolPort()
        controller = HeaterController(port, temperature_manager=temperatures)
        synchronize(controller, parse_frame(REAL_OFF_STATUS), start_at=180)
        controller.request_start(
            CONTROL_MODE_ROOF_TENT_TEMPERATURE,
            target_temperature=20,
        )

        self.assertEqual(controller.step(200), [])
        snapshot = controller.snapshot()
        self.assertFalse(snapshot["requested"]["on"])
        self.assertEqual(
            snapshot["sensor_alert"]["health"], SENSOR_HEALTH_FAILED
        )
        self.assertEqual(
            snapshot["active_sensor"]["unavailable_age_ms"], 200
        )

    def test_stale_and_failed_sensor_boundaries_block_new_start(self):
        cases = (
            (120, 220, SENSOR_HEALTH_STALE),
            (20, 220, SENSOR_HEALTH_FAILED),
        )
        for sample_at, step_at, expected_health in cases:
            with self.subTest(health=expected_health):
                temperatures = assigned_manager(
                    stale_after_ms=100, failed_after_ms=200
                )
                temperatures.record_valid(ROM_ROOF, 20, 0)
                port = RecordingProtocolPort()
                controller = HeaterController(
                    port, temperature_manager=temperatures
                )
                controller.request_start(
                    CONTROL_MODE_ROOF_TENT_TEMPERATURE,
                    target_temperature=20,
                )
                synchronize(controller, parse_frame(REAL_OFF_STATUS))
                temperatures.record_valid(ROM_ROOF, 20, sample_at)

                controller.step(step_at)
                self.assertFalse(controller.requested["on"])
                self.assertNotIn("start", [name for name, _ in port.calls])
                self.assertEqual(
                    controller.snapshot()["active_sensor"]["health"],
                    expected_health,
                )

    def test_malformed_or_failed_health_port_blocks_temperature_start(self):
        class BrokenHealthPort:
            stale_after_ms = 30000
            failed_after_ms = 300000

            def __init__(self, raises):
                self.raises = raises

            def sensor_snapshot(self, role, now_ms):
                if self.raises:
                    raise OSError("sensor service unavailable")
                return {"role": role, "health": "forged"}

        for raises in (False, True):
            with self.subTest(raises=raises):
                port = RecordingProtocolPort()
                controller = HeaterController(
                    port, temperature_manager=BrokenHealthPort(raises)
                )
                controller.request_start(
                    CONTROL_MODE_ROOF_TENT_TEMPERATURE,
                    target_temperature=20,
                )
                synchronize(controller, parse_frame(REAL_OFF_STATUS))
                controller.step(220)
                self.assertFalse(controller.requested["on"])
                self.assertNotIn("start", [name for name, _ in port.calls])

    def test_forged_sensor_age_or_presence_cannot_authorize_start(self):
        class ForgedHealthPort:
            stale_after_ms = 30000
            failed_after_ms = 300000

            def sensor_snapshot(self, role, now_ms):
                return {
                    "role": role,
                    "rom_id": None,
                    "value_c": 20,
                    "last_valid_ms": -999999999,
                    "age_ms": 0,
                    "unavailable_since_ms": None,
                    "unavailable_age_ms": None,
                    "health": SENSOR_HEALTH_OK,
                    "usable": True,
                    "present": False,
                    "invalid_readings": 0,
                    "failure_generation": 0,
                    "last_error": None,
                }

        port = RecordingProtocolPort()
        controller = HeaterController(
            port, temperature_manager=ForgedHealthPort()
        )
        controller.request_start(
            CONTROL_MODE_ROOF_TENT_TEMPERATURE,
            target_temperature=20,
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        controller.step(220)
        self.assertFalse(controller.requested["on"])
        self.assertNotIn("start", [name for name, _ in port.calls])

    def test_stale_sensor_cancels_an_unconfirmed_start_retry(self):
        temperatures = assigned_manager(
            stale_after_ms=100, failed_after_ms=5000
        )
        temperatures.record_valid(ROM_ROOF, 20, 0)
        port = RecordingProtocolPort()
        controller = HeaterController(port, temperature_manager=temperatures)
        controller.request_start(
            CONTROL_MODE_ROOF_TENT_TEMPERATURE,
            target_temperature=20,
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        temperatures.record_valid(ROM_ROOF, 20, 210)
        port.return_false_on = "start"
        self.assertEqual(controller.step(220), ["start_error"])

        self.assertEqual(controller.step(400), [])
        self.assertFalse(controller.requested["on"])
        self.assertEqual([name for name, _ in port.calls].count("start"), 1)


class TestActiveSensorSafety(unittest.TestCase):
    def test_stale_running_sensor_warns_then_failed_sensor_shutdowns_once(self):
        controller, port, temperatures = running_temperature_controller()

        self.assertEqual(controller.step(2200), [])
        stale = controller.snapshot()
        self.assertTrue(stale["requested"]["on"])
        self.assertEqual(stale["active_sensor"]["health"], SENSOR_HEALTH_STALE)
        self.assertEqual(stale["sensor_alert"]["severity"], "warning")
        self.assertEqual(
            [name for name, _ in port.calls].count("shutdown"), 0
        )

        refresh_running_status(controller)
        self.assertEqual(controller.step(3200), ["shutdown"])
        failed = controller.snapshot()
        self.assertFalse(failed["requested"]["on"])
        self.assertEqual(
            failed["active_sensor"]["health"], SENSOR_HEALTH_FAILED
        )
        self.assertEqual(
            failed["sensor_alert"]["action"], "shutdown_latched"
        )
        self.assertIsNotNone(failed["sensor_stop_latch"])
        self.assertEqual(controller.step(3300), [])
        self.assertEqual(
            [name for name, _ in port.calls].count("shutdown"), 1
        )

        event_types = [event["type"] for event in controller.drain_events()]
        self.assertEqual(event_types.count("sensor_shutdown_requested"), 1)

    def test_inactive_and_outside_sensor_failures_do_not_stop_roof_session(self):
        controller, port, temperatures = running_temperature_controller()
        temperatures.record_failure(ROM_CABIN, 1231, "cabin disconnected")
        temperatures.record_failure(
            ROM_OUTSIDE, 1231, "outside disconnected"
        )
        controller.step(2200)
        refresh_running_status(controller)
        temperatures.record_valid(ROM_ROOF, 20, 3199)
        temperatures.drain_events()
        cabin = temperatures.sensor_snapshot(SENSOR_ROLE_CABIN, 3231)
        outside = temperatures.sensor_snapshot(SENSOR_ROLE_OUTSIDE, 3231)

        self.assertEqual(cabin["health"], SENSOR_HEALTH_FAILED)
        self.assertEqual(outside["health"], SENSOR_HEALTH_FAILED)
        failed_roles = {
            event["details"]["role"]
            for event in temperatures.drain_events()
            if event["type"] == "sensor_health_changed"
            and event["details"]["current"] == SENSOR_HEALTH_FAILED
        }
        self.assertEqual(
            failed_roles, {SENSOR_ROLE_CABIN, SENSOR_ROLE_OUTSIDE}
        )

        self.assertEqual(controller.step(3231), [])
        self.assertTrue(controller.requested["on"])
        self.assertEqual(
            controller.snapshot()["active_sensor"]["role"],
            SENSOR_ROLE_ROOF_TENT,
        )
        self.assertEqual(
            [name for name, _ in port.calls].count("shutdown"), 0
        )

    def test_sensor_recovery_does_not_cancel_latched_stop_or_restart(self):
        controller, port, temperatures = running_temperature_controller()
        controller.step(2200)
        refresh_running_status(controller)
        self.assertEqual(controller.step(3200), ["shutdown"])
        temperatures.record_valid(ROM_ROOF, 21, 3201)

        self.assertEqual(controller.step(3202), [])
        snapshot = controller.snapshot()
        self.assertFalse(snapshot["requested"]["on"])
        self.assertIsNotNone(snapshot["sensor_stop_latch"])
        self.assertEqual([name for name, _ in port.calls].count("start"), 1)

    def test_sensor_stop_waits_in_unknown_state_then_shutdowns_from_running(self):
        controller, port, temperatures = running_temperature_controller()
        controller.step(2200)
        refresh_running_status(controller, state=2)

        self.assertEqual(controller.step(3200), [])
        snapshot = controller.snapshot()
        self.assertFalse(snapshot["requested"]["on"])
        self.assertIsNotNone(snapshot["sensor_stop_latch"])
        self.assertEqual([name for name, _ in port.calls].count("shutdown"), 0)

        self.assertEqual(controller.step(3240), ["status"])
        self.assertTrue(controller.handle_frame(status_frame(4), 3250))
        self.assertEqual(controller.step(3450), ["shutdown"])
        self.assertEqual([name for name, _ in port.calls].count("shutdown"), 1)

    def test_confirmed_off_completes_sensor_stop_and_never_restarts(self):
        controller, port, temperatures = running_temperature_controller()
        controller.step(2200)
        refresh_running_status(controller)
        self.assertEqual(controller.step(3200), ["shutdown"])

        self.assertEqual(controller.step(4200), ["status"])
        self.assertTrue(controller.handle_frame(status_frame(0), 4210))
        snapshot = controller.snapshot()
        self.assertIsNone(snapshot["session"])
        self.assertIsNone(snapshot["sensor_stop_latch"])
        self.assertIsNone(snapshot["sensor_alert"])
        self.assertIsNone(snapshot["last_error"])
        self.assertFalse(snapshot["requested"]["on"])
        self.assertEqual(controller.step(4211), [])
        self.assertEqual([name for name, _ in port.calls].count("start"), 1)
        events = [event["type"] for event in controller.drain_events()]
        self.assertEqual(events.count("sensor_shutdown_completed"), 1)

    def test_valid_heater_status_does_not_erase_sensor_failure_reason(self):
        controller, port, temperatures = running_temperature_controller()
        controller.step(2200)
        refresh_running_status(controller)
        self.assertEqual(controller.step(3200), ["shutdown"])
        reason = controller.snapshot()["sensor_alert"]["reason"]

        self.assertEqual(controller.step(4200), ["status"])
        self.assertTrue(controller.handle_frame(status_frame(4), 4210))
        snapshot = controller.snapshot()
        self.assertEqual(snapshot["sensor_alert"]["reason"], reason)
        self.assertEqual(snapshot["last_error"], reason)

    def test_latch_allocation_failure_keeps_off_intent_and_shutdown_path(self):
        class AllocationFailingController(HeaterController):
            @staticmethod
            def _new_sensor_stop_latch(*args):
                raise MemoryError("simulated latch allocation failure")

        temperatures = assigned_manager(
            stale_after_ms=1000, failed_after_ms=2000
        )
        temperatures.record_valid(ROM_ROOF, 20, 0)
        port = RecordingProtocolPort()
        controller = AllocationFailingController(
            port, temperature_manager=temperatures
        )
        controller.request_start(
            CONTROL_MODE_ROOF_TENT_TEMPERATURE,
            target_temperature=20,
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        temperatures.record_valid(ROM_ROOF, 20, 219)
        self.assertEqual(controller.step(220), ["start"])
        temperatures.record_valid(ROM_ROOF, 20, 1200)
        self.assertEqual(controller.step(1220), ["status"])
        controller.handle_frame(status_frame(4), 1230)

        controller.step(2200)
        refresh_running_status(controller)
        self.assertEqual(controller.step(3200), ["shutdown"])
        self.assertFalse(controller.requested["on"])
        self.assertGreaterEqual(controller.snapshot()["event_errors"], 1)

    def test_alert_allocation_failure_cannot_block_shutdown(self):
        class ExplodingAlert(dict):
            def get(self, key, default=None):
                raise MemoryError("simulated alert allocation failure")

        controller, port, temperatures = running_temperature_controller()
        controller.step(2200)
        refresh_running_status(controller)
        controller._sensor_alert = ExplodingAlert()

        self.assertEqual(controller.step(3200), ["shutdown"])
        self.assertFalse(controller.requested["on"])
        self.assertEqual([name for name, _ in port.calls].count("shutdown"), 1)
        self.assertGreaterEqual(controller.snapshot()["event_errors"], 1)

    def test_active_sensor_assignment_is_frozen_for_the_session(self):
        controller, port, temperatures = running_temperature_controller()
        temperatures.record_valid(ROM_CABIN, 30, 1231)
        temperatures.configure_assignments(
            {
                SENSOR_ROLE_ROOF_TENT: ROM_CABIN,
                SENSOR_ROLE_CABIN: ROM_ROOF,
                SENSOR_ROLE_OUTSIDE: ROM_OUTSIDE,
            }
        )

        self.assertEqual(controller.step(1232), ["shutdown"])
        snapshot = controller.snapshot()
        self.assertFalse(snapshot["requested"]["on"])
        self.assertEqual(snapshot["session_sensor_rom_id"], ROM_ROOF)
        self.assertIn("assignment changed", snapshot["sensor_alert"]["reason"])

    def test_unassign_reassign_cannot_erase_a_failed_session_sensor(self):
        controller, port, temperatures = running_temperature_controller()
        controller.step(2200)
        refresh_running_status(controller)
        temperatures.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 3200)
        temperatures.configure_assignments(
            {
                SENSOR_ROLE_CABIN: ROM_CABIN,
                SENSOR_ROLE_OUTSIDE: ROM_OUTSIDE,
            }
        )
        temperatures.configure_assignments(
            {
                SENSOR_ROLE_ROOF_TENT: ROM_ROOF,
                SENSOR_ROLE_CABIN: ROM_CABIN,
                SENSOR_ROLE_OUTSIDE: ROM_OUTSIDE,
            }
        )
        temperatures.record_valid(ROM_ROOF, 21, 3201)

        self.assertEqual(controller.step(3201), ["shutdown"])
        snapshot = controller.snapshot()
        self.assertFalse(snapshot["requested"]["on"])
        self.assertIn(
            "assignment configuration changed",
            snapshot["sensor_alert"]["reason"],
        )
        self.assertEqual([name for name, _ in port.calls].count("start"), 1)

    def test_transient_failed_interval_cannot_be_healed_between_steps(self):
        controller, port, temperatures = running_temperature_controller()
        controller.step(2200)
        refresh_running_status(controller)
        temperatures.record_failure(ROM_ROOF, 3200, "temporary outage")
        temperatures.record_valid(ROM_ROOF, 21, 3201)

        self.assertEqual(controller.step(3201), ["shutdown"])
        snapshot = controller.snapshot()
        self.assertFalse(snapshot["requested"]["on"])
        self.assertEqual(snapshot["active_sensor"]["health"], SENSOR_HEALTH_OK)
        self.assertIn("failure deadline", snapshot["sensor_alert"]["reason"])


class TestSensorStateMachineInteractions(unittest.TestCase):
    def test_failure_while_starting_waits_for_running_before_shutdown(self):
        controller, port, temperatures = running_temperature_controller(state=1)

        controller.step(2200)
        refresh_running_status(controller, state=1)
        self.assertEqual(controller.step(3200), [])
        self.assertFalse(controller.requested["on"])
        self.assertEqual(
            [name for name, _ in port.calls].count("shutdown"), 0
        )

        self.assertEqual(controller.step(3240), ["status"])
        controller.handle_frame(status_frame(4), 3250)
        self.assertEqual(controller.step(3450), ["shutdown"])
        self.assertEqual(
            [name for name, _ in port.calls].count("shutdown"), 1
        )

    def test_temp_monitoring_failure_is_visible_then_recovers_to_safe_stop(self):
        controller, port, temperatures = running_temperature_controller(state=6)

        controller.step(2200)
        refresh_running_status(controller, state=6)
        self.assertEqual(controller.step(3200), [])
        snapshot = controller.snapshot()
        self.assertEqual(snapshot["phase"], PHASE_ERROR)
        self.assertFalse(snapshot["requested"]["on"])
        self.assertIn("TEMP_MONITORING", snapshot["last_error"])
        self.assertEqual(
            [name for name, _ in port.calls].count("shutdown"), 0
        )

        self.assertEqual(controller.step(3240), ["status"])
        controller.handle_frame(status_frame(4), 3250)
        self.assertEqual(controller.step(3450), ["shutdown"])

    def test_communication_loss_delays_but_does_not_cancel_sensor_stop(self):
        controller, port, temperatures = running_temperature_controller()
        controller.step(2200)
        refresh_running_status(controller)
        controller.report_communication_error("link lost", now_ms=3190)

        self.assertEqual(controller.step(3200), [])
        self.assertFalse(controller.requested["on"])
        self.assertIsNotNone(controller.snapshot()["sensor_stop_latch"])
        self.assertEqual(
            [name for name, _ in port.calls].count("shutdown"), 0
        )

        from tests.test_heater_controller import REAL_INIT

        self.assertEqual(controller.step(3240), ["initialization"])
        controller.handle_frame(parse_frame(REAL_INIT), 3250)
        self.assertEqual(controller.step(3250), ["status"])
        controller.handle_frame(status_frame(4), 3260)
        self.assertEqual(controller.step(3460), ["shutdown"])

    def test_runtime_expiry_and_sensor_failure_coalesce_to_one_shutdown(self):
        temperatures = assigned_manager(
            stale_after_ms=30000, failed_after_ms=60000
        )
        temperatures.record_valid(ROM_ROOF, 20, 0)
        port = RecordingProtocolPort()
        controller = HeaterController(
            port,
            maximum_runtime_minutes=5,
            temperature_manager=temperatures,
        )
        controller.request_start(
            CONTROL_MODE_ROOF_TENT_TEMPERATURE,
            target_temperature=20,
            runtime_minutes=1,
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        temperatures.record_valid(ROM_ROOF, 20, 220)
        self.assertEqual(controller.step(220), ["start"])

        self.assertEqual(controller.step(60220), ["status"])
        controller.handle_frame(status_frame(4), 60221)
        self.assertEqual(controller.step(60421), ["shutdown"])
        self.assertEqual(
            [name for name, _ in port.calls].count("shutdown"), 1
        )
        events = [event["type"] for event in controller.drain_events()]
        self.assertEqual(events.count("session_expired"), 1)
        self.assertEqual(events.count("sensor_shutdown_requested"), 1)

    def test_sensor_snapshots_are_detached_from_controller_state(self):
        controller, port, temperatures = running_temperature_controller()
        controller.step(2200)
        snapshot = controller.snapshot()
        snapshot["active_sensor"]["health"] = SENSOR_HEALTH_OK
        snapshot["sensor_alert"]["reason"] = "forged"

        fresh = controller.snapshot()
        self.assertEqual(
            fresh["active_sensor"]["health"], SENSOR_HEALTH_STALE
        )
        self.assertNotEqual(fresh["sensor_alert"]["reason"], "forged")

    def test_start_request_between_status_poll_and_reply_is_bounded(self):
        temperatures = assigned_manager()
        temperatures.record_valid(ROM_ROOF, 20, 1000)
        controller = HeaterController(
            RecordingProtocolPort(), temperature_manager=temperatures
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(controller.step(1020), ["status"])
        controller.request_start(
            CONTROL_MODE_ROOF_TENT_TEMPERATURE,
            target_temperature=20,
        )

        self.assertTrue(controller.handle_frame(status_frame(1), 1030))
        self.assertEqual(controller.step(1030), [])
        snapshot = controller.snapshot()
        self.assertIsNotNone(snapshot["session"])
        self.assertTrue(snapshot["session"]["confirmed_active"])
        self.assertEqual(snapshot["session_sensor_rom_id"], ROM_ROOF)


class TestSensorControllerBoundaries(unittest.TestCase):
    def test_constructor_rejects_invalid_temperature_manager(self):
        with self.assertRaises(ValueError):
            HeaterController(
                RecordingProtocolPort(), temperature_manager=object()
            )


if __name__ == "__main__":
    unittest.main()
