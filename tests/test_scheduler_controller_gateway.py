import ast
import inspect
import unittest
from unittest import mock

import app.scheduler_controller_gateway as gateway_module
from app.scheduler import Scheduler
from app.scheduler_controller_gateway import SchedulerControllerGateway
from app.heater_controller import HeaterController
from protocol.autoterm_protocol import parse_frame
from services.time_service import CLOCK_SOURCE_RTC, TimeService


TIMER = {
    "id": "weekday",
    "name": "Morning",
    "enabled": True,
    "weekdays": [6],
    "start": "14:30",
    "mode": "power",
    "target_temperature": None,
    "power_level": 5,
    "runtime_minutes": 30,
}

REAL_INIT = bytes((170, 4, 5, 0, 4, 18, 138, 0, 61, 214, 203, 166))
REAL_OFF = bytes(
    (170, 4, 19, 0, 15, 0, 1, 0, 30, 127, 0, 128, 1, 47, 0, 0,
     0, 0, 0, 0, 0, 0, 0, 96, 109, 160)
)


class TickPlan:
    def __init__(self, *values):
        self.values = list(values)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if not self.values:
            raise AssertionError("unexpected tick read")
        return self.values.pop(0)


class FakeController:
    def __init__(self):
        self._on = False
        self._mode = "power"
        self._target = None
        self._power = 5
        self._runtime = 30
        self._source = "manual"
        self._not_after_ms = None
        self.generic_available = True
        self.specific_available = True
        self.session_complete = False
        self.request_result = True
        self.request_error = None
        self.mutate_before_error = False
        self.stop_error = None
        self.calls = []

    @property
    def requested_on(self):
        return self._on

    @property
    def requested_source(self):
        return self._source

    def timer_start_available(self, now_ms, request=None):
        self.calls.append(("available", now_ms, request is not None))
        return (
            self.generic_available
            if request is None
            else self.specific_available
        )

    def timer_session_complete(self, now_ms):
        self.calls.append(("complete_available", now_ms))
        return self.session_complete

    def request_start(
        self,
        mode,
        target_temperature=None,
        power_level=None,
        runtime_minutes=60,
        source="manual",
        not_after_ms=None,
        now_ms=None,
    ):
        self.calls.append(
            (
                "request_start",
                mode,
                target_temperature,
                power_level,
                runtime_minutes,
                source,
                not_after_ms,
                now_ms,
            )
        )
        if self.request_error is not None and not self.mutate_before_error:
            raise self.request_error
        self._on = True
        self._mode = mode
        self._target = target_temperature
        self._power = power_level
        self._runtime = runtime_minutes
        self._source = source
        self._not_after_ms = not_after_ms
        if self.request_error is not None:
            raise self.request_error
        return self.request_result

    def request_stop(self):
        self.calls.append(("request_stop",))
        changed = self._on
        self._on = False
        if self.stop_error is not None:
            raise self.stop_error
        return changed

    def requested_matches(
        self, on, mode, target, power, runtime, source, not_after_ms=None
    ):
        self.calls.append(("requested_matches",))
        return (
            self._on is on
            and self._mode == mode
            and self._target == target
            and self._power == power
            and self._runtime == runtime
            and self._source == source
            and self._not_after_ms == not_after_ms
        )


class RecordingProtocolPort:
    def __init__(self):
        self.calls = []

    def validate_inbound_frame(self, frame):
        return parse_frame(frame["raw"])

    def request_initialization(self):
        self.calls.append("initialization")
        return True

    def request_status(self):
        self.calls.append("status")
        return True

    def request_start(self, mode, target_temperature, power_level):
        self.calls.append("start")
        return True

    def request_shutdown(self):
        self.calls.append("shutdown")
        return True


def scheduler_and_intent(intent_valid_ms=5000):
    diff = lambda newer, older: newer - older
    add = lambda value, delta: value + delta
    clock = TimeService(ticks_diff=diff)
    clock.set_utc_datetime(
        2026, 8, 9, 14, 29, 59, CLOCK_SOURCE_RTC, 0
    )
    scheduler = Scheduler(
        clock,
        120,
        ticks_diff=diff,
        ticks_add=add,
        intent_valid_ms=intent_valid_ms,
    )
    scheduler.replace_timers([TIMER])
    scheduler.arm()
    assert scheduler.step(0, True) is None
    intent = scheduler.step(1000, True)
    assert intent is not None
    return scheduler, intent


class TestSchedulerControllerGateway(unittest.TestCase):
    def test_module_has_no_async_hardware_or_protocol_surface(self):
        tree = ast.parse(inspect.getsource(gateway_module))
        forbidden_nodes = (ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom)
        self.assertFalse(any(isinstance(node, forbidden_nodes) for node in ast.walk(tree)))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append((node.module or "").split(".")[0])
        for forbidden in ("machine", "board_config", "protocol", "hardware"):
            self.assertNotIn(forbidden, imports)

    def test_success_applies_only_authorized_fields_and_associates_occurrence(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        ticks = TickPlan(1001, 1001, 1001)
        gateway = SchedulerControllerGateway(scheduler, controller, ticks)
        self.assertTrue(gateway.apply_intent(intent))
        self.assertEqual(ticks.calls, 3)
        request = next(call for call in controller.calls if call[0] == "request_start")
        self.assertEqual(
            request,
            ("request_start", "power", None, 5, 30, "timer", 6000, 1001),
        )
        self.assertEqual(scheduler.active_occurrence_key, intent.occurrence_key)
        self.assertTrue(controller.requested_on)
        self.assertEqual(gateway.snapshot()["applied"], 1)

    def test_real_controller_handoff_changes_requested_without_protocol_tx(self):
        scheduler, intent = scheduler_and_intent()
        port = RecordingProtocolPort()
        controller = HeaterController(port)
        self.assertEqual(controller.step(0), ["initialization"])
        self.assertTrue(controller.handle_frame(parse_frame(REAL_INIT), 10))
        self.assertEqual(controller.step(10), ["status"])
        self.assertTrue(controller.handle_frame(parse_frame(REAL_OFF), 20))
        before = list(port.calls)

        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001, 1001)
        )
        self.assertTrue(gateway.apply_intent(intent))
        self.assertEqual(port.calls, before)
        self.assertTrue(controller.requested_on)
        self.assertEqual(controller.requested_source, "timer")
        self.assertEqual(controller.snapshot()["request_not_after_ms"], 6000)

    def test_expired_intent_never_reaches_controller(self):
        scheduler, intent = scheduler_and_intent(intent_valid_ms=100)
        controller = FakeController()
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1101, 1101)
        )
        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(
            any(call[0] == "request_start" for call in controller.calls)
        )
        self.assertFalse(controller.requested_on)

    def test_specific_readiness_failure_completes_without_start(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        controller.specific_available = False
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001, 1001)
        )
        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(controller.requested_on)
        record = scheduler.snapshot()["occurrences"]["weekday"]
        self.assertEqual(record["status"], "application_failed")

    def test_nonboolean_readiness_is_fail_closed(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        controller.specific_available = 1
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001)
        )

        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(controller.requested_on)
        self.assertIsNone(scheduler.active_occurrence_key)

    def test_exception_before_mutation_is_completed_as_rejected(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        controller.request_error = RuntimeError("reject")
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001, 1001)
        )
        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(controller.requested_on)
        self.assertIsNone(scheduler.active_occurrence_key)

    def test_exception_after_mutation_preserves_association_and_faults_scheduler(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        controller.request_error = RuntimeError("post mutation")
        controller.mutate_before_error = True
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001, 1001)
        )
        self.assertTrue(gateway.apply_intent(intent))
        self.assertTrue(controller.requested_on)
        self.assertEqual(scheduler.active_occurrence_key, intent.occurrence_key)
        self.assertTrue(scheduler.faulted)

    def test_unassociated_on_is_rolled_back_in_same_stack(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()

        def mismatching(*args, **kwargs):
            return False

        controller.requested_matches = mismatching
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001, 1001)
        )
        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(controller.requested_on)
        self.assertIn(("request_stop",), controller.calls)

    def test_nonboolean_completion_rolls_unassociated_request_off(self):
        scheduler, intent = scheduler_and_intent()

        class NonBooleanCompletionScheduler:
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            @property
            def active_occurrence_key(self):
                return self.inner.active_occurrence_key

            def complete_intent(self, *args):
                return 1

        controller = FakeController()
        gateway = SchedulerControllerGateway(
            NonBooleanCompletionScheduler(scheduler),
            controller,
            TickPlan(1001, 1001, 1001),
        )
        with self.assertRaisesRegex(RuntimeError, "non-boolean"):
            gateway.apply_intent(intent)
        self.assertFalse(controller.requested_on)
        self.assertTrue(gateway.faulted)

    def test_claimed_completion_without_active_association_rolls_back(self):
        scheduler, intent = scheduler_and_intent()

        class LyingCompletionScheduler:
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            @property
            def active_occurrence_key(self):
                return self.inner.active_occurrence_key

            def complete_intent(self, *args):
                return True

        controller = FakeController()
        gateway = SchedulerControllerGateway(
            LyingCompletionScheduler(scheduler),
            controller,
            TickPlan(1001, 1001, 1001),
        )
        with self.assertRaisesRegex(RuntimeError, "did not associate"):
            gateway.apply_intent(intent)
        self.assertFalse(controller.requested_on)
        self.assertTrue(gateway.faulted)

    def test_gateway_step_uses_fresh_tick_for_authorization(self):
        diff = lambda newer, older: newer - older
        clock = TimeService(ticks_diff=diff)
        clock.set_utc_datetime(
            2026, 8, 9, 14, 29, 59, CLOCK_SOURCE_RTC, 0
        )
        scheduler = Scheduler(
            clock,
            120,
            ticks_diff=diff,
            ticks_add=lambda value, delta: value + delta,
            intent_valid_ms=100,
        )
        scheduler.replace_timers([TIMER])
        scheduler.arm()
        scheduler.step(0, True)
        controller = FakeController()
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1000, 1001, 1101)
        )
        self.assertFalse(gateway.step())
        self.assertFalse(controller.requested_on)

    def test_generic_readiness_cannot_stale_legacy_authorization_tick(self):
        scheduler, intent = scheduler_and_intent(intent_valid_ms=100)
        controller = FakeController()
        clock = {"now": 1001}
        original_available = controller.timer_start_available

        def advancing_available(now_ms, request=None):
            result = original_available(now_ms, request)
            if request is None:
                clock["now"] = 5000
            return result

        controller.timer_start_available = advancing_available
        gateway = SchedulerControllerGateway(
            scheduler, controller, lambda: clock["now"]
        )

        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(controller.requested_on)
        self.assertFalse(
            any(call[0] == "request_start" for call in controller.calls)
        )

    def test_specific_readiness_cannot_stale_legacy_request_tick(self):
        scheduler, intent = scheduler_and_intent(intent_valid_ms=100)
        controller = FakeController()
        clock = {"now": 1001}
        original_available = controller.timer_start_available

        def advancing_available(now_ms, request=None):
            result = original_available(now_ms, request)
            if request is not None:
                clock["now"] = 5000
            return result

        def deadline_aware_start(
            mode,
            target_temperature=None,
            power_level=None,
            runtime_minutes=60,
            source="manual",
            not_after_ms=None,
            now_ms=None,
        ):
            if now_ms > not_after_ms:
                controller.calls.append(
                    ("deadline_rejected", now_ms, not_after_ms)
                )
                return False
            raise AssertionError("stale request tick reached the controller")

        controller.timer_start_available = advancing_available
        controller.request_start = deadline_aware_start
        gateway = SchedulerControllerGateway(
            scheduler, controller, lambda: clock["now"]
        )

        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(controller.requested_on)
        self.assertIn(("deadline_rejected", 5000, 1100), controller.calls)

    def test_manual_stop_commits_off_before_override(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001, 1001, 1002)
        )
        self.assertTrue(gateway.apply_intent(intent))
        self.assertTrue(gateway.request_manual_stop())
        self.assertFalse(controller.requested_on)
        self.assertIsNone(scheduler.active_occurrence_key)
        record = scheduler.snapshot()["occurrences"]["weekday"]
        self.assertEqual(record["status"], "overridden")
        self.assertLess(
            controller.calls.index(("request_stop",)),
            len(controller.calls),
        )

    def test_stop_exception_after_off_still_marks_override_then_propagates_baseexception(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001, 1001, 1002)
        )
        gateway.apply_intent(intent)
        controller.stop_error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            gateway.request_manual_stop()
        self.assertFalse(controller.requested_on)
        self.assertIsNone(scheduler.active_occurrence_key)

    def test_ordinary_stop_exception_is_visible_after_safe_override(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001, 1001, 1002)
        )
        gateway.apply_intent(intent)
        controller.stop_error = RuntimeError("stop failed after OFF")

        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            gateway.request_manual_stop()

        self.assertFalse(controller.requested_on)
        self.assertIsNone(scheduler.active_occurrence_key)
        self.assertTrue(gateway.faulted)
        self.assertEqual(
            gateway.snapshot()["last_error"], "timer_manual_stop_failed"
        )

    def test_lying_stop_result_never_reports_manual_stop_success(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001, 1001, 1002)
        )
        gateway.apply_intent(intent)

        def lying_stop():
            controller.calls.append(("request_stop",))
            return True

        controller.request_stop = lying_stop
        with self.assertRaisesRegex(RuntimeError, "Requested OFF"):
            gateway.request_manual_stop()
        self.assertTrue(controller.requested_on)
        self.assertEqual(
            scheduler.active_occurrence_key, intent.occurrence_key
        )
        self.assertTrue(gateway.faulted)
        self.assertEqual(gateway.snapshot()["manual_stops"], 0)

    def test_override_bookkeeping_recovery_keeps_fault_visible_until_reset(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001, 1001, 1001, 1002, 1003),
        )
        gateway.apply_intent(intent)

        with mock.patch(
            "app.scheduler._bounded_text",
            side_effect=MemoryError("override allocation failed"),
        ):
            with self.assertRaises(MemoryError):
                gateway.request_manual_stop()

        self.assertFalse(controller.requested_on)
        self.assertTrue(gateway.faulted)
        self.assertIsNotNone(gateway.snapshot()["pending_override_key"])
        self.assertIsNone(gateway.step())
        self.assertIsNone(gateway.snapshot()["pending_override_key"])
        self.assertTrue(gateway.faulted)
        self.assertEqual(
            gateway.snapshot()["last_error"],
            "timer_override_bookkeeping_recovered",
        )
        self.assertTrue(gateway.reset_fault())

    def test_normal_off_completion_releases_active_without_override(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001, 1001, 2000)
        )
        gateway.apply_intent(intent)
        controller._on = False
        controller.session_complete = True
        self.assertIsNone(gateway.step())
        self.assertIsNone(scheduler.active_occurrence_key)
        record = scheduler.snapshot()["occurrences"]["weekday"]
        self.assertEqual(record["status"], "completed")
        self.assertFalse(record["overridden"])


if __name__ == "__main__":
    unittest.main()
