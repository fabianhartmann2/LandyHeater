import copy
import unittest

from app.scheduler import Scheduler
from app.scheduler_controller_gateway import SchedulerControllerGateway
from services.time_service import CLOCK_SOURCE_RTC, TimeService
from tests.test_scheduler_controller_gateway import (
    FakeController,
    TIMER,
    TickPlan,
    scheduler_and_intent,
)


class FakePersistence:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.generation = 1
        self.calls = []
        self.failure = None
        self.result = True
        self.on_checkpoint = None
        self.history = {
            "consumed_local_high_water": None,
            "occurrences": [],
        }

    @property
    def ledger_generation(self):
        return self.generation

    @property
    def timer_start_allowed(self):
        return self.allowed

    def checkpoint_scheduler_history(self, history, expected_generation):
        self.calls.append((copy.deepcopy(history), expected_generation))
        if self.on_checkpoint is not None:
            self.on_checkpoint()
        if self.failure is not None:
            raise self.failure
        if type(self.result) is bool and self.result:
            self.history = copy.deepcopy(history)
            self.generation += 1
        return self.result

    def scheduler_history_for_restore(self):
        return copy.deepcopy(self.history)


def due_scheduler():
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
    )
    scheduler.replace_timers([TIMER])
    scheduler.arm()
    scheduler.step(0, True)
    return scheduler


class TestSchedulerPersistentGateway(unittest.TestCase):
    def test_direct_apply_checkpoints_consumption_before_requested_on(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001, 1001, 1001),
            persistence=persistence,
        )

        self.assertTrue(gateway.apply_intent(intent))
        self.assertEqual(len(persistence.calls), 1)
        history, expected_generation = persistence.calls[0]
        self.assertEqual(expected_generation, 1)
        self.assertEqual(history["occurrences"][0]["status"], "consumed")
        self.assertTrue(controller.requested_on)
        self.assertEqual(gateway.snapshot()["checkpoints"], 1)

    def test_direct_apply_samples_tick_after_durable_checkpoint(self):
        scheduler, intent = scheduler_and_intent(intent_valid_ms=100)
        controller = FakeController()
        persistence = FakePersistence()
        clock = {"now": 1001}

        def advance_past_deadline():
            clock["now"] = 1101

        persistence.on_checkpoint = advance_past_deadline
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            lambda: clock["now"],
            persistence=persistence,
        )

        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(controller.requested_on)
        self.assertFalse(
            any(call[0] == "request_start" for call in controller.calls)
        )

    def test_generic_readiness_cannot_stale_authorization_tick(self):
        scheduler, intent = scheduler_and_intent(intent_valid_ms=100)
        controller = FakeController()
        persistence = FakePersistence()
        clock = {"now": 1001}
        original_available = controller.timer_start_available

        def advancing_available(now_ms, request=None):
            result = original_available(now_ms, request)
            if request is None:
                clock["now"] = 5000
            return result

        controller.timer_start_available = advancing_available
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            lambda: clock["now"],
            persistence=persistence,
        )

        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(controller.requested_on)
        self.assertFalse(
            any(call[0] == "request_start" for call in controller.calls)
        )

    def test_specific_readiness_cannot_stale_request_tick(self):
        scheduler, intent = scheduler_and_intent(intent_valid_ms=100)
        controller = FakeController()
        persistence = FakePersistence()
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
            scheduler,
            controller,
            lambda: clock["now"],
            persistence=persistence,
        )

        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(controller.requested_on)
        self.assertIn(("deadline_rejected", 5000, 1100), controller.calls)

    def test_reentrant_apply_is_rejected_even_when_callback_swallows_error(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        nested_errors = []
        gateway = None

        def reenter():
            try:
                gateway.apply_intent(intent)
            except RuntimeError as error:
                nested_errors.append(str(error))

        persistence.on_checkpoint = reenter
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001, 1001),
            persistence=persistence,
        )

        with self.assertRaisesRegex(RuntimeError, "was re-entered"):
            gateway.apply_intent(intent)
        self.assertEqual(nested_errors, ["gateway operation is already active"])
        self.assertFalse(controller.requested_on)
        self.assertFalse(gateway.snapshot()["operation_active"])
        self.assertTrue(gateway.faulted)
        self.assertFalse(
            any(call[0] == "request_start" for call in controller.calls)
        )

    def test_checkpoint_failure_prevents_start_and_latches_fault(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        persistence.failure = OSError("storage unavailable")
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001, 1001),
            persistence=persistence,
        )

        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(controller.requested_on)
        self.assertFalse(
            any(call[0] == "request_start" for call in controller.calls)
        )
        status = gateway.snapshot()
        self.assertTrue(status["faulted"])
        self.assertEqual(status["checkpoint_failures"], 1)
        self.assertEqual(
            status["last_error"], "timer_persistence_checkpoint_failed"
        )

    def test_checkpoint_callback_cannot_leave_an_orphan_requested_on(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()

        def mutate_then_fail():
            controller._on = True
            controller._source = "timer"
            raise OSError("reentrant storage callback")

        persistence.on_checkpoint = mutate_then_fail
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001),
            persistence=persistence,
        )
        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(controller.requested_on)
        self.assertIsNone(scheduler.active_occurrence_key)
        self.assertTrue(gateway.faulted)

    def test_availability_callback_cannot_leave_an_orphan_requested_on(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()

        def mutate_and_reject(now_ms, request=None):
            controller._on = True
            controller._source = "timer"
            return False

        controller.timer_start_available = mutate_and_reject
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001)
        )
        with self.assertRaisesRegex(RuntimeError, "unassociated Requested ON"):
            gateway.apply_intent(intent)
        self.assertFalse(controller.requested_on)
        self.assertIsNone(scheduler.active_occurrence_key)
        self.assertTrue(gateway.faulted)

    def test_memory_error_faults_before_start_and_remains_visible(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        persistence.failure = MemoryError("oom")
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001, 1001, 1001),
            persistence=persistence,
        )

        with self.assertRaises(MemoryError):
            gateway.apply_intent(intent)
        self.assertTrue(gateway.faulted)
        self.assertFalse(controller.requested_on)

    def test_generic_readiness_memory_error_is_propagated_fail_closed(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()

        def unavailable(now_ms, request=None):
            raise MemoryError("readiness oom")

        controller.timer_start_available = unavailable
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1001, 1001)
        )

        with self.assertRaisesRegex(MemoryError, "readiness oom"):
            gateway.apply_intent(intent)
        self.assertFalse(controller.requested_on)
        self.assertIsNone(scheduler.active_occurrence_key)
        self.assertTrue(gateway.faulted)

    def test_post_readiness_tick_memory_error_is_propagated_fail_closed(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        calls = {"count": 0}

        def ticks():
            calls["count"] += 1
            if calls["count"] == 3:
                raise MemoryError("tick oom")
            return 1001

        gateway = SchedulerControllerGateway(scheduler, controller, ticks)

        with self.assertRaisesRegex(MemoryError, "tick oom"):
            gateway.apply_intent(intent)
        self.assertFalse(controller.requested_on)
        self.assertIsNone(scheduler.active_occurrence_key)
        self.assertTrue(gateway.faulted)

    def test_closed_persistent_gate_consumes_but_never_authorizes(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence(allowed=False)
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001, 1001),
            persistence=persistence,
        )

        self.assertFalse(gateway.apply_intent(intent))
        self.assertEqual(len(persistence.calls), 1)
        self.assertFalse(controller.requested_on)
        self.assertIsNone(scheduler.active_occurrence_key)

    def test_nonboolean_checkpoint_result_is_fail_closed(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        persistence.result = 1
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001),
            persistence=persistence,
        )

        self.assertFalse(gateway.apply_intent(intent))
        self.assertTrue(gateway.faulted)
        self.assertFalse(controller.requested_on)

    def test_false_checkpoint_requires_exact_durable_noop_readback(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        persistence.result = False
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001),
            persistence=persistence,
        )
        self.assertFalse(gateway.apply_intent(intent))
        self.assertFalse(controller.requested_on)
        self.assertTrue(gateway.faulted)

        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        persistence.result = False
        persistence.history = scheduler.export_persistent_history()
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001, 1001, 1001),
            persistence=persistence,
        )
        self.assertTrue(gateway.apply_intent(intent))
        self.assertTrue(controller.requested_on)

    def test_history_change_during_commit_is_rejected(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        original_export = scheduler.export_persistent_history
        calls = {"count": 0}

        def changing_export():
            calls["count"] += 1
            value = original_export()
            if calls["count"] > 1:
                value["consumed_local_high_water"] += 1
            return value

        scheduler.export_persistent_history = changing_export
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001),
            persistence=persistence,
        )
        self.assertFalse(gateway.apply_intent(intent))
        self.assertTrue(gateway.faulted)
        self.assertFalse(controller.requested_on)

    def test_busy_occurrence_is_checkpointed_even_without_intent(self):
        scheduler = due_scheduler()
        controller = FakeController()
        controller.generic_available = False
        persistence = FakePersistence()
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1000),
            persistence=persistence,
        )

        self.assertIsNone(gateway.step())
        self.assertEqual(len(persistence.calls), 1)
        history = persistence.calls[0][0]
        self.assertEqual(history["occurrences"][0]["status"], "consumed")
        self.assertFalse(controller.requested_on)

    def test_step_availability_callback_cannot_leave_orphan_on(self):
        scheduler = due_scheduler()
        controller = FakeController()

        def mutate_and_reject(now_ms, request=None):
            controller._on = True
            controller._source = "timer"
            return False

        controller.timer_start_available = mutate_and_reject
        gateway = SchedulerControllerGateway(
            scheduler, controller, TickPlan(1000)
        )
        with self.assertRaisesRegex(RuntimeError, "unassociated Requested ON"):
            gateway.step()
        self.assertFalse(controller.requested_on)
        self.assertIsNone(scheduler.active_occurrence_key)
        self.assertTrue(gateway.faulted)

    def test_step_checkpoints_before_it_applies_start(self):
        scheduler = due_scheduler()
        controller = FakeController()
        persistence = FakePersistence()
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1000, 1001, 1001, 1001),
            persistence=persistence,
        )

        self.assertTrue(gateway.step())
        # First publication follows Scheduler.step(); apply_intent performs a
        # verified no-op checkpoint again before authorization.
        self.assertEqual(len(persistence.calls), 2)
        self.assertTrue(controller.requested_on)

    def test_reentrant_step_is_rejected_even_when_callback_swallows_error(self):
        scheduler = due_scheduler()
        controller = FakeController()
        persistence = FakePersistence()
        nested_errors = []
        callback_pending = {"value": True}
        gateway = None

        def reenter_once():
            if not callback_pending["value"]:
                return
            callback_pending["value"] = False
            try:
                gateway.step()
            except RuntimeError as error:
                nested_errors.append(str(error))

        persistence.on_checkpoint = reenter_once
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1000, 1001, 1001),
            persistence=persistence,
        )

        with self.assertRaisesRegex(RuntimeError, "was re-entered"):
            gateway.step()
        self.assertEqual(nested_errors, ["gateway operation is already active"])
        self.assertFalse(controller.requested_on)
        self.assertFalse(gateway.snapshot()["operation_active"])
        self.assertTrue(gateway.faulted)
        self.assertFalse(
            any(call[0] == "request_start" for call in controller.calls)
        )

    def test_manual_override_is_checkpointed_after_requested_off(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001, 1001, 1001, 1002),
            persistence=persistence,
        )
        self.assertTrue(gateway.apply_intent(intent))
        self.assertTrue(gateway.request_manual_stop())
        self.assertFalse(controller.requested_on)
        self.assertEqual(
            persistence.calls[-1][0]["occurrences"][0]["status"],
            "overridden",
        )

    def test_reentrant_manual_stop_is_rejected_after_safe_off(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001, 1001, 1001, 1002),
            persistence=persistence,
        )
        self.assertTrue(gateway.apply_intent(intent))
        nested_errors = []

        def reenter():
            try:
                gateway.request_manual_stop()
            except RuntimeError as error:
                nested_errors.append(str(error))

        persistence.on_checkpoint = reenter
        with self.assertRaisesRegex(RuntimeError, "was re-entered"):
            gateway.request_manual_stop()
        self.assertEqual(nested_errors, ["gateway operation is already active"])
        self.assertFalse(controller.requested_on)
        self.assertFalse(gateway.snapshot()["operation_active"])
        self.assertTrue(gateway.faulted)

    def test_nonboolean_override_result_faults_and_remains_pending(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()

        class NonBooleanOverrideScheduler:
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            @property
            def active_occurrence_key(self):
                return self.inner.active_occurrence_key

            def mark_manual_override(self, key, now_ms):
                return 1

        wrapped = NonBooleanOverrideScheduler(scheduler)
        gateway = SchedulerControllerGateway(
            wrapped,
            controller,
            TickPlan(1001, 1001, 1001, 1002, 1003),
        )
        self.assertTrue(gateway.apply_intent(intent))

        with self.assertRaisesRegex(RuntimeError, "non-boolean"):
            gateway.request_manual_stop()
        self.assertFalse(controller.requested_on)
        self.assertEqual(
            scheduler.active_occurrence_key, intent.occurrence_key
        )
        self.assertEqual(
            gateway.snapshot()["pending_override_key"], intent.occurrence_key
        )
        self.assertTrue(gateway.faulted)

        self.assertIsNone(gateway.step())
        self.assertEqual(
            gateway.snapshot()["pending_override_key"], intent.occurrence_key
        )

    def test_lying_natural_completion_cannot_release_an_on_request(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001, 1001, 1001, 2000),
            persistence=persistence,
        )
        gateway.apply_intent(intent)
        controller.session_complete = True
        self.assertIsNone(gateway.step())
        self.assertFalse(controller.requested_on)
        self.assertEqual(
            scheduler.active_occurrence_key, intent.occurrence_key
        )
        self.assertTrue(gateway.faulted)

    def test_override_checkpoint_failure_keeps_heater_off_and_faults(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001, 1001, 1001, 1002),
            persistence=persistence,
        )
        gateway.apply_intent(intent)
        persistence.failure = OSError("override write failed")

        with self.assertRaisesRegex(RuntimeError, "checkpoint failed"):
            gateway.request_manual_stop()
        self.assertFalse(controller.requested_on)
        self.assertTrue(gateway.faulted)
        self.assertIsNone(scheduler.active_occurrence_key)

    def test_override_callback_cannot_restore_requested_on(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()

        class ReentrantOverrideScheduler:
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            @property
            def active_occurrence_key(self):
                return self.inner.active_occurrence_key

            def mark_manual_override(self, key, now_ms):
                result = self.inner.mark_manual_override(key, now_ms)
                controller._on = True
                controller._source = "timer"
                return result

        wrapped = ReentrantOverrideScheduler(scheduler)
        gateway = SchedulerControllerGateway(
            wrapped, controller, TickPlan(1001, 1001, 1001, 1002)
        )
        gateway.apply_intent(intent)
        with self.assertRaisesRegex(RuntimeError, "restored Requested ON"):
            gateway.request_manual_stop()
        self.assertFalse(controller.requested_on)
        self.assertTrue(gateway.faulted)

    def test_reset_requires_repaired_persistence_and_open_start_gate(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        persistence.failure = OSError("down")
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001),
            persistence=persistence,
        )
        self.assertFalse(gateway.apply_intent(intent))
        persistence.failure = None
        persistence.allowed = False
        with self.assertRaisesRegex(RuntimeError, "gate remains closed"):
            gateway.reset_fault()
        persistence.allowed = True
        self.assertTrue(gateway.reset_fault())

    def test_reset_gate_callback_cannot_restore_requested_on(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()

        class ReentrantPersistence(FakePersistence):
            def __init__(self):
                super().__init__()
                self.mutate_on_gate = False

            @property
            def timer_start_allowed(self):
                if self.mutate_on_gate:
                    controller._on = True
                    controller._source = "timer"
                return self.allowed

        persistence = ReentrantPersistence()
        persistence.failure = OSError("down")
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001),
            persistence=persistence,
        )
        self.assertFalse(gateway.apply_intent(intent))
        persistence.failure = None
        persistence.mutate_on_gate = True
        with self.assertRaisesRegex(RuntimeError, "restored Requested ON"):
            gateway.reset_fault()
        self.assertFalse(controller.requested_on)
        self.assertTrue(gateway.faulted)

    def test_reentrant_reset_is_rejected_and_fault_remains_latched(self):
        scheduler, intent = scheduler_and_intent()
        controller = FakeController()
        persistence = FakePersistence()
        persistence.failure = OSError("down")
        gateway = SchedulerControllerGateway(
            scheduler,
            controller,
            TickPlan(1001),
            persistence=persistence,
        )
        self.assertFalse(gateway.apply_intent(intent))
        persistence.failure = None
        nested_errors = []

        def reenter():
            try:
                gateway.reset_fault()
            except RuntimeError as error:
                nested_errors.append(str(error))

        persistence.on_checkpoint = reenter
        with self.assertRaisesRegex(RuntimeError, "was re-entered"):
            gateway.reset_fault()
        self.assertEqual(nested_errors, ["gateway operation is already active"])
        self.assertFalse(controller.requested_on)
        self.assertFalse(gateway.snapshot()["operation_active"])
        self.assertTrue(gateway.faulted)


if __name__ == "__main__":
    unittest.main()
