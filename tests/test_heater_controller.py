import inspect
import unittest

import app.heater_controller as heater_controller_module
from app.application_state import (
    COMMUNICATION_ERROR,
    COMMUNICATION_OK,
    COMMUNICATION_UNKNOWN,
    HEATER_STATE_OFF,
    HEATER_STATE_RUNNING,
    HEATER_STATE_STARTING,
    HEATER_STATE_TEMP_MONITORING,
    HEATER_STATE_UNKNOWN,
)
from app.heater_controller import (
    PHASE_ERROR,
    PHASE_READY,
    PHASE_UNSYNCHRONIZED,
    PHASE_WAIT_INIT,
    PHASE_WAIT_STATUS,
    HeaterController,
)
from app.temperature_manager import (
    SENSOR_ROLE_ROOF_TENT,
    TemperatureManager,
)
from protocol.autoterm_protocol import (
    CMD_SETTINGS,
    CMD_STATUS,
    CONTROL_MODE_POWER,
    CONTROL_MODE_ROOF_TENT_TEMPERATURE,
    DEVICE_HEATER,
    build_frame,
    build_status_request,
    parse_frame,
)


REAL_INIT = bytes.fromhex("AA 04 05 00 04 12 8A 00 3D D6 CB A6")
REAL_OFF_STATUS = bytes.fromhex(
    "AA 04 13 00 0F 00 01 00 1E 7F 00 80 01 2F "
    "00 00 00 00 00 00 00 00 00 60 6D A0"
)


class RecordingProtocolPort:
    def __init__(self):
        self.calls = []
        self.raise_on = None
        self.return_false_on = None

    def _record(self, name, details=None):
        self.calls.append((name, details))
        if self.raise_on == name:
            raise OSError("simulated {} failure".format(name))
        if self.return_false_on == name:
            return False
        return None

    def validate_inbound_frame(self, frame):
        if not isinstance(frame, dict):
            return None
        raw = frame.get("raw")
        if not isinstance(raw, bytes):
            return None
        try:
            return parse_frame(raw)
        except ValueError:
            return None

    def request_initialization(self):
        return self._record("initialization")

    def request_status(self):
        return self._record("status")

    def request_start(self, mode, target_temperature, power_level):
        return self._record(
            "start",
            {
                "mode": mode,
                "target_temperature": target_temperature,
                "power_level": power_level,
            },
        )

    def request_shutdown(self):
        return self._record("shutdown")

    def request_external_temperature(self, temperature):
        return self._record("external_temperature", temperature)

    def request_settings(self):
        return self._record("settings_get")

    def request_set_power_settings(self, baseline, level):
        return self._record("settings_set", (baseline, level))


def status_frame(state, voltage=126, glow=7, fan=55):
    payload = bytearray(19)
    payload[6] = voltage
    payload[8] = glow
    payload[9] = state
    payload[14] = fan
    return parse_frame(build_frame(CMD_STATUS, payload, device=DEVICE_HEATER))


def synchronize(controller, frame, start_at=0):
    controller.step(start_at)
    controller.handle_frame(parse_frame(REAL_INIT), start_at + 10)
    controller.step(start_at + 10)
    controller.handle_frame(frame, start_at + 20)


class TestLivePowerUpdate(unittest.TestCase):
    BASELINE = b"\x01\x00\x04\x10\x02\x09"
    LOWER = BASELINE[:5] + b"\x04"

    @staticmethod
    def settings(payload, sender=DEVICE_HEATER):
        return parse_frame(build_frame(CMD_SETTINGS, payload, device=sender))

    def setUp(self):
        self.port = RecordingProtocolPort()
        self.controller = HeaterController(self.port)
        self.controller.request_start(
            CONTROL_MODE_POWER, power_level=9, runtime_minutes=1
        )
        synchronize(self.controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(self.controller.step(220), ["start"])
        self.assertEqual(self.controller.step(1220), ["status"])
        self.assertTrue(self.controller.handle_frame(status_frame(4), 1230))
        self.revision = self.controller.request_revision
        self.before = self.controller.snapshot()["session"]
        self.port.calls.clear()

    def stage(self):
        self.assertTrue(self.controller.request_power_update(self.revision, 4, 1231))
        self.assertEqual(self.port.calls, [])
        self.assertEqual(self.controller.request_revision, self.revision)
        self.assertEqual(self.controller.step(1232), ["power_settings_get"])

    def reach_verify(self):
        self.stage()
        self.assertTrue(self.controller.handle_frame(self.settings(self.BASELINE), 1240))
        self.assertEqual(self.controller.step(1240), ["power_settings_set"])
        self.assertTrue(self.controller.handle_frame(self.settings(self.LOWER), 1250))
        self.assertFalse(self.controller.handle_frame(self.settings(self.LOWER), 1250))
        self.assertEqual(self.controller.step(1250), ["status"])
        self.assertTrue(self.controller.handle_frame(status_frame(4), 1260))
        self.assertEqual(self.controller.step(1260), ["power_settings_verify"])

    def test_set_preserves_baseline_and_commits_only_after_fresh_readback(self):
        self.reach_verify()
        status = self.controller.public_snapshot()
        self.assertEqual(status["power_update"], {
            "state": "pending", "power_level": 4, "reason": None,
        })
        self.assertEqual(status["requested"]["power_level"], 9)
        self.assertEqual(status["session"], self.before)

        self.assertTrue(self.controller.handle_frame(self.settings(self.LOWER), 1270))
        status = self.controller.public_snapshot()
        self.assertEqual(status["power_update"], {
            "state": "applied", "power_level": 4, "reason": None,
        })
        self.assertEqual(status["request_revision"], self.revision + 1)
        self.assertEqual(status["requested"]["power_level"], 4)
        expected_session = dict(self.before, target=4)
        self.assertEqual(status["session"], expected_session)
        self.assertEqual(self.port.calls, [
            ("settings_get", None), ("settings_set", (self.BASELINE, 4)),
            ("status", None), ("settings_get", None),
        ])

    def test_lost_readback_times_out_without_retry_or_optimistic_change(self):
        self.reach_verify()
        self.assertEqual(self.controller.step(2260), ["status"])
        self.assertFalse(self.controller.handle_frame(self.settings(self.LOWER), 2260))
        status = self.controller.public_snapshot()
        self.assertEqual(status["power_update"]["reason"], "timeout")
        self.assertEqual(status["requested"]["power_level"], 9)
        self.assertEqual(status["request_revision"], self.revision)
        self.assertEqual(self.port.calls.count(("settings_set", (self.BASELINE, 4))), 1)
        with self.assertRaisesRegex(RuntimeError, "still be in flight"):
            self.controller.request_power_update(self.revision, 4, 2261)

    def test_mismatched_readback_never_promotes_power_level(self):
        self.reach_verify()
        changed_ventilation = self.LOWER[:4] + b"\x01\x04"
        self.assertFalse(self.controller.handle_frame(self.settings(changed_ventilation), 1270))
        status = self.controller.public_snapshot()
        self.assertEqual(status["power_update"]["reason"], "settings_mismatch")
        self.assertEqual(status["requested"]["power_level"], 9)
        self.assertEqual(status["session"], self.before)

    def test_only_valid_heater_power_settings_allow_write(self):
        self.stage()
        self.assertFalse(self.controller.handle_frame(self.settings(self.BASELINE, 3), 1240))
        unrelated_mode = self.BASELINE[:2] + b"\x02" + self.BASELINE[3:]
        self.assertFalse(self.controller.handle_frame(self.settings(unrelated_mode), 1240))
        self.assertEqual(self.controller.step(1240), [])
        self.assertEqual(self.port.calls, [("settings_get", None)])
        self.assertEqual(self.controller.public_snapshot()["power_update"]["reason"],
                         "incompatible_settings")

    def test_stop_cancels_a_staged_write_and_does_not_wait_for_settings(self):
        self.stage()
        self.assertTrue(self.controller.handle_frame(self.settings(self.BASELINE), 1240))
        self.assertTrue(self.controller.request_stop())
        self.assertEqual(self.controller.step(1430), ["shutdown"])
        self.assertEqual(self.port.calls, [("settings_get", None), ("shutdown", None)])
        self.assertFalse(self.controller.handle_frame(self.settings(self.LOWER), 1440))
        self.assertEqual(self.controller.public_snapshot()["power_update"]["reason"], "cancelled")

    def test_already_matching_actual_settings_skip_the_write(self):
        self.stage()
        self.assertTrue(self.controller.handle_frame(self.settings(self.LOWER), 1240))
        self.assertEqual(self.controller.step(1240), [])
        self.assertEqual(self.port.calls, [("settings_get", None)])
        self.assertEqual(self.controller.public_snapshot()["session"]["target"], 4)

    def test_rejects_invalid_stale_or_overlapping_changes(self):
        with self.assertRaises(ValueError):
            self.controller.request_power_update(self.revision, True, 1231)
        with self.assertRaisesRegex(RuntimeError, "revision changed"):
            self.controller.request_power_update(self.revision - 1, 4, 1231)
        self.assertFalse(self.controller.request_power_update(self.revision, 9, 1231))
        self.stage()
        with self.assertRaisesRegex(RuntimeError, "not safely adjustable"):
            self.controller.request_power_update(self.revision, 3, 1233)
        with self.assertRaisesRegex(RuntimeError, "not safely adjustable"):
            self.controller.update_active_session(self.revision, extend_minutes=15, now_ms=1233)

    def test_waits_for_fresh_status_when_next_heartbeat_is_near(self):
        self.assertTrue(self.controller.request_power_update(self.revision, 4, 2130))
        self.assertEqual(self.controller.step(2130), [])
        self.assertEqual(self.controller.step(2230), ["status"])
        self.assertEqual(self.controller.step(2231), [])
        self.assertTrue(self.controller.handle_frame(status_frame(4), 2240))
        self.assertEqual(self.controller.step(2240), ["power_settings_get"])

    def test_expiry_preempts_pending_write(self):
        self.assertEqual(self.controller.step(60180), ["status"])
        self.assertTrue(self.controller.handle_frame(status_frame(4), 60190))
        self.assertTrue(self.controller.request_power_update(self.revision, 4, 60191))
        self.assertEqual(self.controller.step(60191), ["power_settings_get"])
        self.assertTrue(self.controller.handle_frame(self.settings(self.BASELINE), 60219))
        self.assertEqual(self.controller.step(60220), ["shutdown"])
        self.assertEqual([operation for operation, _ in self.port.calls],
                         ["status", "settings_get", "shutdown"])

    def test_bad_crc_and_a_failed_send_are_not_retried(self):
        self.stage()
        raw = bytearray(self.settings(self.BASELINE)["raw"])
        raw[-1] ^= 1
        self.assertFalse(self.controller.handle_frame(parse_frame(bytes(raw)), 1240))
        self.assertEqual(self.controller.public_snapshot()["power_update"]["reason"],
                         "invalid_reply")
        self.assertEqual(self.controller.step(1241), [])
        self.assertEqual(self.port.calls, [("settings_get", None)])

        self.assertEqual(self.controller.step(6240), ["status"])
        self.assertTrue(self.controller.handle_frame(status_frame(4), 6250))
        self.assertTrue(self.controller.request_power_update(self.revision, 4, 6251))
        self.assertEqual(self.controller.step(6251), ["power_settings_get"])
        self.assertTrue(self.controller.handle_frame(self.settings(self.BASELINE), 6260))
        self.port.return_false_on = "settings_set"
        self.assertEqual(self.controller.step(6260), ["power_settings_set_error"])
        self.assertEqual(self.controller.public_snapshot()["power_update"]["reason"],
                         "communication_error")
        self.assertEqual(self.controller.step(7250), ["initialization"])
        self.assertEqual(self.port.calls.count(("settings_set", (self.BASELINE, 4))), 1)


class TestHeaterControllerSynchronization(unittest.TestCase):
    def setUp(self):
        self.port = RecordingProtocolPort()
        self.controller = HeaterController(self.port)

    def test_cold_boot_is_off_unknown_and_has_no_side_effect(self):
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot["phase"], PHASE_UNSYNCHRONIZED)
        self.assertFalse(snapshot["requested"]["on"])
        self.assertEqual(
            snapshot["actual"]["communication"], COMMUNICATION_UNKNOWN
        )
        self.assertEqual(
            snapshot["actual"]["heater_state"], HEATER_STATE_UNKNOWN
        )
        self.assertFalse(snapshot["actual"]["initialized"])
        self.assertFalse(snapshot["actual"]["synchronized"])
        self.assertEqual(self.port.calls, [])

    def test_first_step_requests_only_init_and_heartbeat_does_not_storm(self):
        self.assertEqual(self.controller.step(0), ["initialization"])
        self.assertEqual(self.controller.phase, PHASE_WAIT_INIT)
        self.assertEqual(self.controller.step(999), [])
        self.assertEqual(self.controller.step(1000), ["initialization"])
        self.assertEqual(
            self.port.calls,
            [("initialization", None), ("initialization", None)],
        )

    def test_real_init_then_real_status_reaches_ready(self):
        self.controller.step(0)
        self.assertTrue(self.controller.handle_frame(parse_frame(REAL_INIT), 10))
        self.assertEqual(self.controller.phase, PHASE_WAIT_STATUS)
        self.assertEqual(self.controller.step(10), ["status"])

        self.assertTrue(
            self.controller.handle_frame(parse_frame(REAL_OFF_STATUS), 20)
        )
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot["phase"], PHASE_READY)
        self.assertEqual(snapshot["actual"]["communication"], COMMUNICATION_OK)
        self.assertTrue(snapshot["actual"]["initialized"])
        self.assertTrue(snapshot["actual"]["synchronized"])
        self.assertEqual(snapshot["actual"]["heater_state"], HEATER_STATE_OFF)
        self.assertEqual(snapshot["actual"]["voltage"], 12.8)
        self.assertEqual(snapshot["actual"]["glow_plug_raw"], 47)
        self.assertEqual(snapshot["actual"]["fan_raw"], 0)
        self.assertEqual(snapshot["actual"]["last_status_ms"], 20)

    def test_status_before_init_and_wrong_source_are_ignored(self):
        self.assertFalse(
            self.controller.handle_frame(parse_frame(REAL_OFF_STATUS), 0)
        )
        self.assertFalse(
            self.controller.handle_frame(parse_frame(build_status_request()), 1)
        )
        self.assertEqual(self.controller.phase, PHASE_UNSYNCHRONIZED)
        self.assertEqual(self.controller.ignored_frames, 2)

    def test_invalid_crc_cancels_pending_control_and_preserves_actual(self):
        self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(self.controller, parse_frame(REAL_OFF_STATUS))
        before = self.controller.actual

        bad_status = bytearray(REAL_OFF_STATUS)
        bad_status[14] = 4
        self.assertFalse(
            self.controller.handle_frame(parse_frame(bad_status), 100)
        )
        self.assertEqual(self.controller.actual, before)
        self.assertEqual(self.controller.invalid_frames, 1)
        self.assertEqual(self.controller.step(220), [])
        self.assertNotIn("start", [name for name, _ in self.port.calls])

    def test_repeated_invalid_frames_escalate_instead_of_starving_shutdown(self):
        synchronize(self.controller, status_frame(4))
        bad = bytearray(status_frame(4)["raw"])
        bad[-1] ^= 0x01
        invalid = parse_frame(bad)

        self.assertFalse(self.controller.handle_frame(invalid, 100))
        self.assertEqual(self.controller.step(1020), ["status"])
        self.controller.handle_frame(status_frame(4), 1030)
        self.assertFalse(self.controller.handle_frame(invalid, 1100))
        self.assertEqual(self.controller.step(2030), ["status"])
        self.controller.handle_frame(status_frame(4), 2040)
        self.assertFalse(self.controller.handle_frame(invalid, 2100))

        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot["phase"], PHASE_ERROR)
        self.assertEqual(
            snapshot["actual"]["communication"], COMMUNICATION_ERROR
        )
        self.assertEqual(snapshot["invalid_frame_strikes"], 3)
        self.assertEqual(
            [name for name, _ in self.port.calls].count("shutdown"), 0
        )

    def test_short_crc_valid_status_cannot_authorize_control(self):
        payload = bytearray(10)
        payload[9] = 0
        short = parse_frame(
            build_frame(CMD_STATUS, payload, device=DEVICE_HEATER)
        )
        self.controller.step(0)
        self.controller.handle_frame(parse_frame(REAL_INIT), 10)
        self.controller.step(10)
        self.assertFalse(self.controller.handle_frame(short, 20))
        self.assertEqual(self.controller.phase, PHASE_WAIT_STATUS)
        self.assertEqual(self.controller.invalid_frames, 1)

    def test_forged_or_inconsistent_dictionary_cannot_authorize_control(self):
        self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        self.controller.step(0)
        self.controller.handle_frame(parse_frame(REAL_INIT), 10)
        self.controller.step(10)

        forged = {
            "device": DEVICE_HEATER,
            "command": CMD_STATUS,
            "crc_valid": True,
            "status": {"heater_state": 0, "heater_state_name": "off"},
        }
        self.assertFalse(self.controller.handle_frame(forged, 20))

        inconsistent = status_frame(255)
        inconsistent["status"]["heater_state_name"] = "off"
        self.assertTrue(self.controller.handle_frame(inconsistent, 30))
        self.assertEqual(
            self.controller.actual["heater_state"], HEATER_STATE_UNKNOWN
        )
        self.assertFalse(self.controller.actual["synchronized"])
        self.assertNotIn("start", [name for name, _ in self.port.calls])

    def test_forged_crc_metadata_is_reparsed_by_protocol_port(self):
        self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        self.controller.step(0)
        self.controller.handle_frame(parse_frame(REAL_INIT), 10)
        self.controller.step(10)

        bad_raw = bytearray(REAL_OFF_STATUS)
        bad_raw[14] = 4
        forged = parse_frame(bad_raw)
        forged["crc_valid"] = True
        forged["crc_calculated"] = forged["crc_received"]
        self.assertFalse(self.controller.handle_frame(forged, 20))
        self.assertEqual(self.controller.phase, PHASE_WAIT_STATUS)
        self.assertEqual(self.controller.invalid_frames, 1)

    def test_handle_frame_validates_time_before_mutating_state(self):
        self.controller.step(0)
        before = self.controller.snapshot()
        with self.assertRaises(ValueError):
            self.controller.handle_frame(parse_frame(REAL_INIT), "10")
        self.assertEqual(self.controller.snapshot(), before)

    def test_replayed_init_is_ignored_and_cannot_mask_status_timeout(self):
        self.controller.step(0)
        self.controller.handle_frame(parse_frame(REAL_INIT), 10)
        self.controller.step(10)

        for now_ms in (20, 30, 40):
            self.assertFalse(
                self.controller.handle_frame(parse_frame(REAL_INIT), now_ms)
            )
        operations = self.controller.step(10010)
        self.assertEqual(operations, ["initialization"])
        self.assertEqual(self.controller.communication_failures, 1)
        self.assertEqual(
            [name for name, _ in self.port.calls],
            ["initialization", "status", "initialization"],
        )

    def test_unsolicited_status_replay_cannot_mask_timeout(self):
        self.controller.step(0)
        self.controller.handle_frame(parse_frame(REAL_INIT), 10)
        self.controller.step(10)
        self.controller.handle_frame(parse_frame(REAL_OFF_STATUS), 20)

        for now_ms in range(500, 10000, 500):
            self.assertFalse(
                self.controller.handle_frame(
                    parse_frame(REAL_OFF_STATUS), now_ms
                )
            )
        # The scheduled real request still occurs; only its matching response
        # may refresh communication truth.
        self.assertEqual(self.controller.step(1020), ["status"])
        self.assertEqual(self.controller.step(11020), ["initialization"])
        self.assertEqual(self.controller.communication_failures, 1)

    def test_late_status_is_rejected_even_if_handle_precedes_step(self):
        self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        self.controller.step(0)
        self.controller.handle_frame(parse_frame(REAL_INIT), 10)
        self.controller.step(10)

        self.assertFalse(
            self.controller.handle_frame(parse_frame(REAL_OFF_STATUS), 10010)
        )
        self.assertEqual(self.controller.phase, PHASE_ERROR)
        self.assertEqual(self.controller.communication_failures, 1)
        self.assertFalse(self.controller.actual["synchronized"])
        self.assertEqual(self.controller.step(10010), ["initialization"])
        self.assertNotIn("start", [name for name, _ in self.port.calls])

    def test_response_timeout_fails_closed_and_recovers_via_fresh_pair(self):
        self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        self.controller.step(0)
        self.assertEqual(self.controller.step(10000), ["initialization"])
        self.assertEqual(self.controller.phase, PHASE_ERROR)
        self.assertEqual(
            self.controller.actual["communication"], COMMUNICATION_ERROR
        )
        self.assertEqual(self.controller.communication_failures, 1)

        self.controller.handle_frame(parse_frame(REAL_INIT), 10001)
        self.controller.step(10001)
        self.controller.handle_frame(parse_frame(REAL_OFF_STATUS), 10002)
        self.assertEqual(self.controller.phase, PHASE_READY)
        self.assertTrue(self.controller.actual["synchronized"])
        event_types = [event["type"] for event in self.controller.drain_events()]
        self.assertIn("communication_error", event_types)
        self.assertIn("communication_recovered", event_types)

    def test_default_tick_hooks_are_wrap_safe_when_platform_provides_them(self):
        period = 64

        def ticks_diff(newer, older):
            return ((newer - older + period // 2) % period) - period // 2

        def ticks_add(value, delta):
            return (value + delta) % period

        old_diff = heater_controller_module._platform_ticks_diff
        old_add = heater_controller_module._platform_ticks_add
        try:
            heater_controller_module._platform_ticks_diff = ticks_diff
            heater_controller_module._platform_ticks_add = ticks_add
            controller = HeaterController(
                self.port,
                heartbeat_ms=10,
                response_timeout_ms=20,
                control_settle_ms=3,
            )
            self.assertEqual(controller.step(60), ["initialization"])
            self.assertEqual(controller.step(5), [])
            self.assertEqual(controller.step(6), ["initialization"])
        finally:
            heater_controller_module._platform_ticks_diff = old_diff
            heater_controller_module._platform_ticks_add = old_add


class TestHeaterControllerDecisions(unittest.TestCase):
    def setUp(self):
        self.port = RecordingProtocolPort()
        self.controller = HeaterController(self.port)

    def test_off_plus_requested_on_starts_once_after_settle(self):
        self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(self.controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(self.controller.step(219), [])
        self.assertEqual(self.controller.step(220), ["start"])

        self.controller.handle_frame(parse_frame(REAL_OFF_STATUS), 300)
        self.assertEqual(self.controller.step(500), [])
        starts = [details for name, details in self.port.calls if name == "start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["mode"], CONTROL_MODE_POWER)
        self.assertEqual(starts[0]["power_level"], 5)

    def test_new_start_is_rejected_while_start_transition_is_unresolved(self):
        self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(self.controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(self.controller.step(220), ["start"])

        self.controller.request_stop()
        with self.assertRaises(RuntimeError):
            self.controller.request_start(CONTROL_MODE_POWER, power_level=6)
        self.assertEqual(self.controller.step(1220), ["status"])
        self.controller.handle_frame(parse_frame(REAL_OFF_STATUS), 1230)
        self.assertEqual(self.controller.step(1430), [])
        self.assertEqual(
            [name for name, _ in self.port.calls].count("start"), 1
        )

    def test_running_plus_requested_off_shutdowns_once(self):
        synchronize(self.controller, status_frame(4))
        self.assertEqual(
            self.controller.actual["heater_state"], HEATER_STATE_RUNNING
        )
        self.assertEqual(self.controller.step(220), ["shutdown"])
        self.controller.handle_frame(status_frame(4), 300)
        self.assertEqual(self.controller.step(500), [])
        self.assertEqual(
            [name for name, _ in self.port.calls].count("shutdown"), 1
        )

    def test_restart_is_rejected_while_shutdown_transition_is_unresolved(self):
        synchronize(self.controller, status_frame(4))
        self.assertEqual(self.controller.step(220), ["shutdown"])
        with self.assertRaises(RuntimeError):
            self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        self.assertEqual(self.controller.step(1220), ["status"])
        self.controller.handle_frame(status_frame(4), 1230)
        self.assertEqual(self.controller.step(1430), [])
        self.assertEqual(
            [name for name, _ in self.port.calls].count("shutdown"), 1
        )

    def test_unsolicited_status_replays_cannot_postpone_or_authorize_control(self):
        synchronize(self.controller, status_frame(4))
        for now_ms in (50, 100, 150, 200):
            self.assertFalse(
                self.controller.handle_frame(status_frame(4), now_ms)
            )
        self.assertEqual(self.controller.step(219), [])
        self.assertEqual(self.controller.step(220), ["shutdown"])

    def test_starting_plus_requested_off_waits_for_running(self):
        synchronize(self.controller, status_frame(1))
        self.assertEqual(
            self.controller.actual["heater_state"], HEATER_STATE_STARTING
        )
        self.assertEqual(self.controller.step(220), [])
        self.assertEqual(self.controller.step(1020), ["status"])
        self.controller.handle_frame(status_frame(4), 1030)
        self.assertEqual(self.controller.step(1230), ["shutdown"])

    def test_shutdown_cycle_requires_off_before_a_new_start_is_accepted(self):
        synchronize(self.controller, status_frame(4))
        self.assertEqual(self.controller.step(220), ["shutdown"])

        with self.assertRaises(RuntimeError):
            self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        self.assertEqual(self.controller.step(1220), ["status"])
        self.controller.handle_frame(status_frame(5), 1230)
        with self.assertRaises(RuntimeError):
            self.controller.request_start(CONTROL_MODE_POWER, power_level=5)

        self.assertEqual(self.controller.step(2230), ["status"])
        self.controller.handle_frame(parse_frame(REAL_OFF_STATUS), 2240)
        self.assertFalse(self.controller.requested["on"])
        self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        self.assertEqual(self.controller.step(2440), ["start"])
        self.assertEqual(
            [name for name, _ in self.port.calls].count("start"), 1
        )

    def test_unexpected_off_forces_requested_off_and_never_auto_restarts(self):
        self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(self.controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(self.controller.step(220), ["start"])
        self.assertEqual(self.controller.step(1220), ["status"])
        self.controller.handle_frame(status_frame(4), 1230)

        self.assertEqual(self.controller.step(2230), ["status"])
        self.controller.handle_frame(parse_frame(REAL_OFF_STATUS), 2240)
        self.assertFalse(self.controller.requested["on"])
        self.assertIsNone(self.controller.snapshot()["session"])
        self.assertEqual(self.controller.step(2440), [])
        self.assertEqual(
            [name for name, _ in self.port.calls].count("start"), 1
        )
        self.assertIn(
            "unexpected_stop",
            [event["type"] for event in self.controller.drain_events()],
        )

    def test_recovery_off_after_confirmed_running_never_auto_restarts(self):
        self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(self.controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(self.controller.step(220), ["start"])
        self.assertEqual(self.controller.step(1220), ["status"])
        self.controller.handle_frame(status_frame(4), 1230)
        self.assertTrue(
            self.controller.snapshot()["session"]["confirmed_active"]
        )

        self.controller.report_communication_error("link lost", now_ms=1300)
        self.assertEqual(self.controller.step(2230), ["initialization"])
        self.controller.handle_frame(parse_frame(REAL_INIT), 2240)
        self.assertEqual(self.controller.step(2240), ["status"])
        self.controller.handle_frame(parse_frame(REAL_OFF_STATUS), 2250)
        self.assertFalse(self.controller.requested["on"])
        self.assertIsNone(self.controller.snapshot()["session"])
        self.assertEqual(self.controller.step(2450), [])
        self.assertEqual(
            [name for name, _ in self.port.calls].count("start"), 1
        )

    def test_status_at_heartbeat_boundary_forces_fresh_poll_before_control(self):
        self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(self.controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(self.controller.step(1020), ["status"])
        self.assertEqual(
            [name for name, _ in self.port.calls].count("start"), 0
        )
        self.controller.handle_frame(parse_frame(REAL_OFF_STATUS), 1030)
        self.assertEqual(self.controller.step(1230), ["start"])

    def test_completed_shutdown_generation_cannot_be_replayed_into_a_storm(self):
        synchronize(self.controller, status_frame(4))
        self.assertEqual(self.controller.step(220), ["shutdown"])
        self.assertEqual(self.controller.step(1220), ["status"])
        self.controller.handle_frame(parse_frame(REAL_OFF_STATUS), 1230)

        self.assertEqual(self.controller.step(2230), ["status"])
        self.controller.handle_frame(status_frame(4), 2240)
        self.assertEqual(self.controller.step(2440), [])
        self.assertEqual(self.controller.phase, PHASE_ERROR)
        self.assertEqual(
            [name for name, _ in self.port.calls].count("shutdown"), 1
        )

        # Only an explicit operator/service authorization creates a new
        # bounded attempt generation.
        self.assertTrue(self.controller.retry_control_fault(2450))
        self.assertEqual(self.controller.step(2450), ["status"])
        self.controller.handle_frame(status_frame(4), 2460)
        self.assertEqual(self.controller.step(2660), ["shutdown"])
        self.assertEqual(
            [name for name, _ in self.port.calls].count("shutdown"), 2
        )

    def test_stuck_starting_stop_becomes_visible_fault_without_guessing(self):
        port = RecordingProtocolPort()
        controller = HeaterController(
            port, starting_stop_policy_timeout_ms=1000
        )
        synchronize(controller, status_frame(1))
        self.assertEqual(controller.step(220), [])
        self.assertEqual(controller.step(1020), ["status"])
        controller.handle_frame(status_frame(1), 1030)
        self.assertEqual(controller.step(1230), [])
        self.assertEqual(controller.phase, PHASE_ERROR)
        self.assertIn("STARTING", controller.snapshot()["last_error"])
        self.assertEqual([name for name, _ in port.calls].count("shutdown"), 0)

        self.assertEqual(controller.step(2030), ["status"])
        controller.handle_frame(status_frame(4), 2040)
        self.assertEqual(controller.phase, PHASE_READY)
        self.assertEqual(controller.step(2240), ["shutdown"])

    def test_unknown_is_inert_and_temp_monitoring_uses_bounded_shutdown(self):
        self.controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(self.controller, status_frame(2))
        self.assertEqual(
            self.controller.actual["heater_state"], HEATER_STATE_UNKNOWN
        )
        self.assertEqual(self.controller.step(220), [])

        other = HeaterController(RecordingProtocolPort())
        synchronize(other, status_frame(6))
        self.assertEqual(
            other.actual["heater_state"], HEATER_STATE_TEMP_MONITORING
        )
        self.assertEqual(other.step(220), ["shutdown"])
        self.assertEqual(other.phase, PHASE_READY)
        self.assertEqual(
            other.snapshot()["control_attempt"]["command"], "shutdown"
        )

        self.assertEqual(other.step(1220), ["status"])
        other.handle_frame(status_frame(5), 1230)
        self.assertEqual(other.phase, PHASE_READY)
        self.assertIsNone(other.snapshot()["control_attempt"])


class TestHeaterControllerRetriesAndRuntime(unittest.TestCase):
    def test_persistent_init_failure_is_heartbeat_paced(self):
        port = RecordingProtocolPort()
        port.return_false_on = "initialization"
        controller = HeaterController(port)
        self.assertEqual(controller.step(0), ["initialization_error"])
        for now_ms in (1, 2, 100, 999):
            self.assertEqual(controller.step(now_ms), [])
        self.assertEqual(controller.step(1000), ["initialization_error"])
        self.assertEqual(
            [name for name, _ in port.calls].count("initialization"), 2
        )

    def test_failed_start_retries_only_after_fresh_resynchronization(self):
        port = RecordingProtocolPort()
        controller = HeaterController(port)
        port.raise_on = "start"
        controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(controller.step(220), ["start_error"])
        self.assertEqual(controller.phase, PHASE_ERROR)

        port.raise_on = None
        self.assertEqual(controller.step(221), [])
        self.assertEqual(controller.step(1020), ["initialization"])
        controller.handle_frame(parse_frame(REAL_INIT), 1030)
        controller.step(1030)
        controller.handle_frame(parse_frame(REAL_OFF_STATUS), 1040)
        self.assertEqual(controller.step(1240), ["start"])
        self.assertEqual([name for name, _ in port.calls].count("start"), 2)

    def test_failed_shutdown_retries_after_fresh_running_status(self):
        port = RecordingProtocolPort()
        controller = HeaterController(port)
        port.raise_on = "shutdown"
        synchronize(controller, status_frame(4))
        self.assertEqual(controller.step(220), ["shutdown_error"])

        port.raise_on = None
        self.assertEqual(controller.step(221), [])
        self.assertEqual(controller.step(1020), ["initialization"])
        controller.handle_frame(parse_frame(REAL_INIT), 1030)
        controller.step(1030)
        controller.handle_frame(status_frame(4), 1040)
        self.assertEqual(controller.step(1240), ["shutdown"])
        self.assertEqual(
            [name for name, _ in port.calls].count("shutdown"), 2
        )

    def test_unconfirmed_temp_monitoring_shutdown_is_bounded_to_two_attempts(self):
        port = RecordingProtocolPort()
        controller = HeaterController(
            port,
            control_confirmation_timeout_ms=500,
            max_control_attempts=2,
        )
        synchronize(controller, status_frame(6))
        self.assertEqual(controller.step(220), ["shutdown"])

        self.assertEqual(controller.step(1220), ["status"])
        controller.handle_frame(status_frame(6), 1230)
        self.assertEqual(controller.step(1430), ["shutdown"])

        self.assertEqual(controller.step(2430), ["status"])
        controller.handle_frame(status_frame(6), 2440)
        self.assertEqual(controller.step(2640), [])
        self.assertEqual(controller.phase, PHASE_ERROR)
        self.assertEqual(
            [name for name, _ in port.calls].count("shutdown"), 2
        )

    def test_unconfirmed_start_has_one_bounded_retry_then_faults(self):
        port = RecordingProtocolPort()
        controller = HeaterController(
            port,
            control_confirmation_timeout_ms=500,
            max_control_attempts=2,
        )
        controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(controller.step(220), ["start"])

        self.assertEqual(controller.step(1220), ["status"])
        controller.handle_frame(parse_frame(REAL_OFF_STATUS), 1230)
        self.assertEqual(controller.step(1430), ["start"])
        self.assertEqual(controller.step(2430), ["status"])
        controller.handle_frame(parse_frame(REAL_OFF_STATUS), 2440)
        self.assertEqual(controller.step(2640), [])
        self.assertEqual(controller.phase, PHASE_ERROR)
        self.assertEqual(controller.snapshot()["control_failures"], 1)
        self.assertEqual([name for name, _ in port.calls].count("start"), 2)
        self.assertEqual(controller.step(3440), ["status"])
        controller.handle_frame(parse_frame(REAL_OFF_STATUS), 3450)
        self.assertEqual(controller.step(3650), [])

        controller.request_stop()
        self.assertEqual(controller.step(4450), ["status"])
        controller.handle_frame(parse_frame(REAL_OFF_STATUS), 4460)
        self.assertIsNone(controller.snapshot()["control_fault"])
        self.assertIsNone(controller.snapshot()["session"])
        controller.request_start(CONTROL_MODE_POWER, power_level=6)
        self.assertEqual(controller.step(4660), ["start"])

    def test_runtime_expiry_turns_request_off_and_shutdowns_running(self):
        port = RecordingProtocolPort()
        controller = HeaterController(port, maximum_runtime_minutes=5)
        controller.request_start(
            CONTROL_MODE_POWER, power_level=5, runtime_minutes=1
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(controller.step(220), ["start"])
        controller.handle_frame(status_frame(4), 300)

        self.assertEqual(controller.step(60219), ["status"])
        # Expiry changes requested state immediately, but a control command is
        # deliberately held until the outstanding STATUS has supplied fresh
        # actual truth.
        self.assertEqual(controller.step(60220), [])
        controller.handle_frame(status_frame(4), 60221)
        self.assertEqual(controller.step(60421), ["shutdown"])
        snapshot = controller.snapshot()
        self.assertFalse(snapshot["requested"]["on"])
        self.assertTrue(snapshot["session"]["expired"])
        self.assertEqual([name for name, _ in port.calls].count("shutdown"), 1)

    def test_temp_monitoring_expiry_requests_bounded_shutdown(self):
        port = RecordingProtocolPort()
        temperatures = TemperatureManager(
            {SENSOR_ROLE_ROOF_TENT: "28-runtime-test"}
        )
        temperatures.record_valid("28-runtime-test", 20, 0)
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
        synchronize(controller, status_frame(6))
        controller.step(20)
        self.assertIsNotNone(controller.snapshot()["session"])

        temperatures.record_valid("28-runtime-test", 20, 59000)
        self.assertEqual(controller.step(60019), ["status"])
        self.assertEqual(controller.step(60020), [])
        controller.handle_frame(status_frame(6), 60021)
        self.assertEqual(controller.step(60221), ["shutdown"])
        snapshot = controller.snapshot()
        self.assertEqual(snapshot["phase"], PHASE_READY)
        self.assertFalse(snapshot["requested"]["on"])
        self.assertTrue(snapshot["session"]["expired"])
        self.assertEqual(
            [name for name, _ in port.calls].count("shutdown"), 1
        )

    def test_runtime_cannot_exceed_configured_global_maximum(self):
        controller = HeaterController(
            RecordingProtocolPort(), maximum_runtime_minutes=90
        )
        with self.assertRaises(ValueError):
            controller.request_start(
                CONTROL_MODE_POWER, power_level=5, runtime_minutes=91
            )
        self.assertFalse(controller.requested["on"])

    def test_confirmed_temperature_session_update_is_fenced_and_protocol_free(self):
        port = RecordingProtocolPort()
        temperatures = TemperatureManager(
            {SENSOR_ROLE_ROOF_TENT: "28-session-update"}
        )
        temperatures.record_valid("28-session-update", 18, 0)
        controller = HeaterController(
            port,
            maximum_runtime_minutes=45,
            temperature_manager=temperatures,
        )
        controller.request_start(
            CONTROL_MODE_ROOF_TENT_TEMPERATURE,
            target_temperature=20,
            runtime_minutes=30,
        )
        synchronize(controller, status_frame(6))
        controller.step(20)
        before_calls = list(port.calls)
        revision = controller.request_revision

        self.assertTrue(controller.update_active_session(
            revision,
            target_temperature=22,
            extend_minutes=15,
            now_ms=25,
        ))
        snapshot = controller.snapshot()
        self.assertEqual(controller.request_revision, revision + 1)
        self.assertEqual(snapshot["requested"]["target_temperature"], 22)
        self.assertEqual(snapshot["requested"]["runtime_minutes"], 45)
        self.assertEqual(snapshot["session"]["target"], 22)
        self.assertEqual(snapshot["session"]["runtime_minutes"], 45)
        self.assertEqual(port.calls, before_calls)

        with self.assertRaises(RuntimeError):
            controller.update_active_session(
                revision,
                target_temperature=23,
                now_ms=26,
            )
        with self.assertRaises(ValueError):
            controller.update_active_session(
                revision + 1,
                extend_minutes=15,
                now_ms=26,
            )
        self.assertEqual(port.calls, before_calls)

    def test_off_status_during_start_attempt_keeps_original_deadline(self):
        port = RecordingProtocolPort()
        controller = HeaterController(port, maximum_runtime_minutes=5)
        controller.request_start(
            CONTROL_MODE_POWER, power_level=5, runtime_minutes=1
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(controller.step(220), ["start"])
        before = controller.snapshot()["session"]

        self.assertEqual(controller.step(1220), ["status"])
        controller.handle_frame(parse_frame(REAL_OFF_STATUS), 1230)
        after = controller.snapshot()["session"]
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["expires_at_ms"], before["expires_at_ms"])

    def test_active_or_pending_start_parameters_cannot_diverge(self):
        port = RecordingProtocolPort()
        controller = HeaterController(port)
        controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(controller.step(220), ["start"])
        with self.assertRaises(RuntimeError):
            controller.request_start(CONTROL_MODE_POWER, power_level=6)

        self.assertEqual(controller.step(1220), ["status"])
        controller.handle_frame(status_frame(4), 1230)
        with self.assertRaises(RuntimeError):
            controller.request_start(
                CONTROL_MODE_POWER, power_level=6, runtime_minutes=30
            )
        snapshot = controller.snapshot()
        self.assertEqual(snapshot["requested"]["power_level"], 5)
        self.assertEqual(snapshot["session"]["target"], 5)

    def test_session_deadline_survives_tick_wrap(self):
        period = 131072

        def ticks_diff(newer, older):
            return ((newer - older + period // 2) % period) - period // 2

        def ticks_add(value, delta):
            return (value + delta) % period

        port = RecordingProtocolPort()
        controller = HeaterController(
            port,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
            maximum_runtime_minutes=5,
        )
        controller.request_start(
            CONTROL_MODE_POWER, power_level=5, runtime_minutes=1
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS), start_at=130000)
        self.assertEqual(controller.step(130220), ["start"])
        deadline = controller.snapshot()["session"]["expires_at_ms"]
        self.assertEqual(deadline, (130220 + 60000) % period)
        self.assertTrue(controller.requested["on"])
        controller.step((deadline - 1) % period)
        self.assertTrue(controller.requested["on"])
        controller.step(deadline)
        self.assertFalse(controller.requested["on"])


class TestHeaterControllerBoundaries(unittest.TestCase):
    def test_controller_source_has_no_board_uart_or_crc_dependency(self):
        source = inspect.getsource(heater_controller_module)
        self.assertNotIn("import machine", source)
        self.assertNotIn("board_config", source)
        self.assertNotIn("uart_transport", source)
        self.assertNotIn("crc16", source)

    def test_protocol_port_and_timing_configuration_are_validated(self):
        with self.assertRaises(ValueError):
            HeaterController(object())
        port = RecordingProtocolPort()
        with self.assertRaises(ValueError):
            HeaterController(port, heartbeat_ms=0)
        with self.assertRaises(ValueError):
            HeaterController(port, response_timeout_ms=1000)
        with self.assertRaises(ValueError):
            HeaterController(port, ticks_diff=lambda a, b: a - b)
        with self.assertRaises(ValueError):
            HeaterController(port, ticks_diff="not callable", ticks_add=lambda x, y: x + y)

    def test_requested_state_validates_all_mode_specific_fields(self):
        controller = HeaterController(RecordingProtocolPort())
        with self.assertRaises(ValueError):
            controller.request_start("unknown", target_temperature=20)
        with self.assertRaises(ValueError):
            controller.request_start(CONTROL_MODE_POWER, power_level=0)
        with self.assertRaises(ValueError):
            controller.request_start(
                CONTROL_MODE_POWER,
                power_level=5,
                target_temperature=20,
            )
        with self.assertRaises(ValueError):
            controller.request_start(
                CONTROL_MODE_ROOF_TENT_TEMPERATURE,
                target_temperature=20,
                power_level=5,
            )
        with self.assertRaises(ValueError):
            controller.request_start(
                CONTROL_MODE_POWER, power_level=5, source=""
            )

    def test_public_snapshots_cannot_mutate_controller_truth(self):
        port = RecordingProtocolPort()
        controller = HeaterController(port)
        synchronize(controller, status_frame(4))
        requested = controller.requested
        actual = controller.actual
        requested["on"] = True
        requested["mode"] = "bogus"
        actual["heater_state"] = HEATER_STATE_OFF
        controller.step(220)
        self.assertNotIn("start", [name for name, _ in port.calls])
        self.assertEqual(
            controller.actual["heater_state"], HEATER_STATE_RUNNING
        )

    def test_false_protocol_return_is_a_communication_error(self):
        port = RecordingProtocolPort()
        port.return_false_on = "initialization"
        controller = HeaterController(port)
        self.assertEqual(controller.step(0), ["initialization_error"])
        self.assertEqual(controller.phase, PHASE_ERROR)
        self.assertEqual(
            controller.actual["communication"], COMMUNICATION_ERROR
        )

    def test_event_queue_is_bounded_and_reports_drops(self):
        controller = HeaterController(
            RecordingProtocolPort(), event_capacity=2
        )
        controller.report_communication_error("one", now_ms=1)
        controller._emit_event("two", 2)
        controller._emit_event("three", 3)
        events = controller.drain_events()
        self.assertEqual([event["type"] for event in events], ["two", "three"])
        self.assertEqual(controller.events_dropped, 1)

    def test_event_allocation_failure_never_breaks_completed_control(self):
        class ExplodingList(list):
            def append(self, value):
                raise MemoryError("simulated allocation failure")

        port = RecordingProtocolPort()
        controller = HeaterController(port)
        controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        controller._events = ExplodingList()
        self.assertEqual(controller.step(220), ["start"])
        self.assertEqual([name for name, _ in port.calls].count("start"), 1)
        self.assertGreaterEqual(controller.snapshot()["event_errors"], 1)

    def test_session_allocation_failure_blocks_start_before_protocol_port(self):
        port = RecordingProtocolPort()
        controller = HeaterController(port)
        controller.request_start(CONTROL_MODE_POWER, power_level=5)
        synchronize(controller, parse_frame(REAL_OFF_STATUS))

        original_session_class = heater_controller_module.HeaterSession

        def fail_session_allocation(*args, **kwargs):
            raise MemoryError("simulated session allocation failure")

        try:
            heater_controller_module.HeaterSession = fail_session_allocation
            with self.assertRaises(MemoryError):
                controller.step(220)
        finally:
            heater_controller_module.HeaterSession = original_session_class

        self.assertEqual([name for name, _ in port.calls].count("start"), 0)
        self.assertIsNone(controller.snapshot()["session"])
        self.assertIsNone(controller.snapshot()["control_attempt"])

    def test_pending_requested_start_cannot_be_reparameterized(self):
        controller = HeaterController(RecordingProtocolPort())
        controller.request_start(CONTROL_MODE_POWER, power_level=5)
        with self.assertRaises(RuntimeError):
            controller.request_start(
                CONTROL_MODE_POWER,
                power_level=6,
                runtime_minutes=30,
                source="timer",
            )
        self.assertEqual(controller.requested["power_level"], 5)
        self.assertEqual(controller.requested["source"], "manual")

    def test_timer_request_deadline_cancels_before_protocol_start(self):
        port = RecordingProtocolPort()
        controller = HeaterController(port)
        controller.request_start(
            CONTROL_MODE_POWER,
            power_level=5,
            runtime_minutes=30,
            source="timer",
            not_after_ms=100,
            now_ms=0,
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(controller.step(101), [])
        self.assertFalse(controller.requested_on)
        self.assertEqual([name for name, _ in port.calls].count("start"), 0)
        self.assertIn(
            "requested_start_expired",
            [event["type"] for event in controller.drain_events()],
        )

    def test_revision_allocation_failure_cannot_create_undated_timer_start(self):
        class ExplodingRevision(int):
            def __add__(self, other):
                raise MemoryError("revision allocation failed")

        controller = HeaterController(RecordingProtocolPort())
        controller._request_revision = ExplodingRevision(0)
        with self.assertRaisesRegex(MemoryError, "revision allocation"):
            controller.request_start(
                CONTROL_MODE_POWER,
                power_level=5,
                runtime_minutes=30,
                source="timer",
                not_after_ms=100,
                now_ms=0,
            )
        self.assertFalse(controller.requested_on)
        self.assertIsNone(controller.snapshot()["request_not_after_ms"])

    def test_timer_request_is_still_valid_at_exact_deadline(self):
        port = RecordingProtocolPort()
        controller = HeaterController(port, control_settle_ms=100)
        controller.request_start(
            CONTROL_MODE_POWER,
            power_level=5,
            runtime_minutes=30,
            source="timer",
            not_after_ms=120,
            now_ms=0,
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(controller.step(120), ["start"])

    def test_timer_start_availability_is_read_only_and_fail_closed(self):
        controller = HeaterController(RecordingProtocolPort())
        self.assertFalse(controller.timer_start_available(0))
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        before = controller.snapshot()
        self.assertTrue(controller.timer_start_available(20))
        after = controller.snapshot()
        self.assertEqual(before["requested"], after["requested"])
        self.assertEqual(before["events_pending"], after["events_pending"])
        controller.request_start(CONTROL_MODE_POWER, power_level=5)
        self.assertFalse(controller.timer_start_available(21))

    def test_manual_start_availability_requires_synchronized_off_truth(self):
        controller = HeaterController(RecordingProtocolPort())
        self.assertFalse(
            controller.manual_start_available(
                0, CONTROL_MODE_POWER, None, 5, 30, "manual"
            )
        )
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        self.assertTrue(
            controller.manual_start_available(
                20, CONTROL_MODE_POWER, None, 5, 30, "manual"
            )
        )
        with self.assertRaises(ValueError):
            controller.manual_start_available(
                20, CONTROL_MODE_POWER, None, 5, 30, "timer"
            )

    def test_public_snapshot_is_allowlisted_and_exposes_request_revision(self):
        controller = HeaterController(RecordingProtocolPort())
        controller.request_start(CONTROL_MODE_POWER, power_level=5)
        public = controller.public_snapshot()
        self.assertEqual(public["request_revision"], 1)
        self.assertTrue(public["requested"]["on"])
        for forbidden in (
            "last_error",
            "control_attempt",
            "control_fault",
            "sensor_stop_latch",
            "request_not_after_ms",
        ):
            self.assertNotIn(forbidden, public)
        controller.request_stop()
        self.assertEqual(controller.request_revision, 2)


if __name__ == "__main__":
    unittest.main()
