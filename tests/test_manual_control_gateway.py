import ast
import inspect
import unittest

import app.manual_control_gateway as gateway_module
from app.heater_controller import HeaterController
from app.manual_control_gateway import (
    ManualControlConfigurationConflictError,
    ManualControlConflictError,
    ManualControlGateway,
    ManualControlInvariantError,
    ManualControlStateConflictError,
    ManualControlUnavailableError,
)
from protocol.autoterm_protocol import CONTROL_MODE_POWER, parse_frame
from tests.test_heater_controller import (
    REAL_OFF_STATUS,
    RecordingProtocolPort,
    synchronize,
)


QUICK_START = {
    "mode": "power",
    "target_temperature": None,
    "power_level": 4,
    "runtime_minutes": 45,
}


class FakeConfigManager:
    def __init__(self):
        self.generation = 7
        self.timer_start_allowed = True


class FakeConfiguredRuntime:
    def __init__(self):
        self.configuration_generation = 7
        self.ledger_generation = 5
        self.setup_complete = True
        self.persistent_start_gate_open = True
        self.quick_start = dict(QUICK_START)
        self.clock_valid = True
        self.scheduler_armed = True
        self.restart = False
        self.on_restart = None
        self.on_snapshot = None

    def restart_required(self, config_manager):
        if self.on_restart is not None:
            self.on_restart()
        return self.restart

    def snapshot(self):
        if self.on_snapshot is not None:
            self.on_snapshot()
        return {
            "configuration_generation": self.configuration_generation,
            "ledger_generation": self.ledger_generation,
            "setup_complete": self.setup_complete,
            "persistent_start_gate_open": self.persistent_start_gate_open,
            "quick_start": dict(self.quick_start),
            "clock_valid": self.clock_valid,
            "scheduler_armed": self.scheduler_armed,
        }


class FakeController:
    def __init__(self):
        self._on = False
        self._mode = "power"
        self._target = None
        self._power = 5
        self._runtime = 30
        self._source = "manual"
        self._deadline = None
        self._requested_at = None
        self._revision = 0
        self.available = True
        self.available_result = None
        self.on_available = None
        self.request_result = True
        self.on_request = None
        self.requested_on_calls = 0
        self.requested_on_failure_at = None
        self.requested_on_failure = None
        self.calls = []

    @property
    def requested_on(self):
        self.requested_on_calls += 1
        if self.requested_on_calls == self.requested_on_failure_at:
            raise self.requested_on_failure
        return self._on

    @property
    def request_revision(self):
        return self._revision

    def manual_start_available(
        self,
        now_ms,
        mode,
        target_temperature,
        power_level,
        runtime_minutes,
        source,
    ):
        self.calls.append(("available", now_ms, source))
        if self.on_available is not None:
            self.on_available()
        if self.available_result is not None:
            return self.available_result
        return self.available

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
        self.calls.append(("start", mode, runtime_minutes, source))
        if self.on_request is not None:
            self.on_request()
        if self.request_result is not True:
            return self.request_result
        self._on = True
        self._mode = mode
        self._target = target_temperature
        self._power = power_level
        self._runtime = runtime_minutes
        self._source = source
        self._deadline = not_after_ms
        self._requested_at = now_ms
        self._revision += 1
        return True

    def requested_matches(
        self,
        on,
        mode,
        target_temperature,
        power_level,
        runtime_minutes,
        source,
        not_after_ms=None,
        ignore_deadline=False,
    ):
        return (
            self._on is on
            and self._mode == mode
            and self._target == target_temperature
            and self._power == power_level
            and self._runtime == runtime_minutes
            and self._source == source
            and (ignore_deadline or not_after_ms is None)
        )

    def force_on(self, source="manual", increment=True):
        self._on = True
        self._source = source
        if increment:
            self._revision += 1

    def stop(self):
        changed = self._on
        self._on = False
        if changed:
            self._revision += 1
        return changed


class FakeSchedulerGateway:
    def __init__(self, controller):
        self.controller = controller
        self.result = None
        self.failure = None
        self.calls = 0

    def request_manual_stop(self):
        self.calls += 1
        changed = self.controller.stop()
        if self.failure is not None:
            raise self.failure
        if self.result is not None:
            return self.result
        return changed


def build_gateway(controller=None):
    if controller is None:
        controller = FakeController()
    scheduler = FakeSchedulerGateway(controller)
    config = FakeConfigManager()
    runtime = FakeConfiguredRuntime()
    ticks = []

    def ticks_ms():
        ticks.append(1000)
        return 1000

    gateway = ManualControlGateway(
        controller,
        scheduler,
        config,
        runtime,
        ticks_ms=ticks_ms,
    )
    return gateway, controller, scheduler, config, runtime, ticks


class TestManualControlGateway(unittest.TestCase):
    def test_manual_start_commits_requested_truth_only(self):
        gateway, controller, _, _, _, ticks = build_gateway()

        self.assertTrue(
            gateway.request_start(7, 0, "power", None, 5, 30)
        )
        self.assertTrue(controller.requested_on)
        self.assertEqual(controller.request_revision, 1)
        self.assertEqual(controller._source, "manual")
        self.assertEqual(controller._requested_at, 1000)
        self.assertEqual(controller._deadline, 6000)
        self.assertEqual(ticks, [1000])
        self.assertEqual(gateway.snapshot()["starts"], 1)

    def test_quick_start_uses_applied_runtime_defaults(self):
        gateway, controller, _, _, runtime, _ = build_gateway()

        self.assertTrue(gateway.request_quick_start(7, 0))
        self.assertEqual(controller._power, 4)
        self.assertEqual(controller._runtime, 45)
        self.assertEqual(controller._source, "quick_start")
        self.assertEqual(runtime.quick_start, QUICK_START)

    def test_exact_lost_response_retry_is_idempotent(self):
        gateway, controller, _, _, _, ticks = build_gateway()
        self.assertTrue(
            gateway.request_start(7, 0, "power", None, 5, 30)
        )

        self.assertFalse(
            gateway.request_start(7, 0, "power", None, 5, 30)
        )
        self.assertEqual(controller.request_revision, 1)
        self.assertEqual(ticks, [1000])
        self.assertEqual(
            [item[0] for item in controller.calls].count("start"), 1
        )

    def test_stale_requested_or_configuration_revision_is_conflict(self):
        gateway, controller, _, config, _, _ = build_gateway()
        controller._revision = 3
        with self.assertRaises(ManualControlConflictError):
            gateway.request_start(7, 1, "power", None, 5, 30)
        self.assertFalse(controller.requested_on)

        config.generation = 8
        with self.assertRaises(ManualControlConflictError):
            gateway.request_start(7, 3, "power", None, 5, 30)
        self.assertFalse(controller.requested_on)

    def test_closed_gate_and_restart_required_never_set_requested_on(self):
        gateway, controller, _, config, runtime, _ = build_gateway()
        config.timer_start_allowed = False
        with self.assertRaises(ManualControlUnavailableError):
            gateway.request_start(7, 0, "power", None, 5, 30)
        self.assertFalse(controller.requested_on)

        config.timer_start_allowed = True
        runtime.restart = True
        with self.assertRaises(ManualControlUnavailableError):
            gateway.request_start(7, 0, "power", None, 5, 30)
        self.assertFalse(controller.requested_on)

    def test_controller_unavailable_and_nonboolean_fail_closed(self):
        gateway, controller, _, _, _, _ = build_gateway()
        controller.available = False
        with self.assertRaises(ManualControlStateConflictError):
            gateway.request_start(7, 0, "power", None, 5, 30)
        self.assertFalse(controller.requested_on)

        controller.available = True
        controller.available_result = 1
        with self.assertRaises(ManualControlInvariantError):
            gateway.request_start(7, 0, "power", None, 5, 30)
        self.assertFalse(controller.requested_on)

    def test_availability_callback_cannot_start_stale_configuration(self):
        gateway, controller, scheduler, config, _, _ = build_gateway()
        controller.on_available = lambda: setattr(config, "generation", 8)

        with self.assertRaises(ManualControlConfigurationConflictError):
            gateway.request_start(7, 0, "power", None, 5, 30)
        self.assertFalse(controller.requested_on)
        self.assertEqual(scheduler.calls, 0)
        self.assertFalse(any(call[0] == "start" for call in controller.calls))

    def test_start_callback_configuration_race_rolls_requested_off(self):
        gateway, controller, scheduler, config, _, _ = build_gateway()
        controller.on_request = lambda: setattr(config, "generation", 8)

        with self.assertRaises(ManualControlConfigurationConflictError):
            gateway.request_start(7, 0, "power", None, 5, 30)
        self.assertFalse(controller.requested_on)
        self.assertEqual(scheduler.calls, 1)
        self.assertTrue(gateway.faulted)
        self.assertFalse(gateway.snapshot()["operation_active"])

    def test_post_commit_restart_callback_race_rolls_requested_off(self):
        gateway, controller, scheduler, config, runtime, _ = build_gateway()
        calls = [0]

        def mutate_on_final_confirmation():
            calls[0] += 1
            if calls[0] == 5:
                config.generation = 8

        runtime.on_restart = mutate_on_final_confirmation
        with self.assertRaises(ManualControlConfigurationConflictError):
            gateway.request_start(7, 0, "power", None, 5, 30)
        self.assertEqual(calls, [5])
        self.assertFalse(controller.requested_on)
        self.assertEqual(scheduler.calls, 1)
        self.assertTrue(gateway.faulted)

    def test_direct_callback_mutation_is_rolled_back_and_faulted(self):
        gateway, controller, scheduler, _, runtime, _ = build_gateway()

        def mutate_requested():
            controller.force_on()

        runtime.on_snapshot = mutate_requested
        with self.assertRaises(ManualControlInvariantError):
            gateway.request_start(7, 0, "power", None, 5, 30)
        self.assertFalse(controller.requested_on)
        self.assertEqual(scheduler.calls, 1)
        self.assertTrue(gateway.faulted)

    def test_nested_gateway_call_rolls_back_even_if_callback_swallows_error(self):
        gateway, controller, scheduler, _, _, _ = build_gateway()

        def reenter():
            try:
                gateway.request_start(7, 0, "power", None, 6, 30)
            except ManualControlInvariantError:
                pass

        controller.on_available = reenter
        with self.assertRaises(ManualControlInvariantError):
            gateway.request_start(7, 0, "power", None, 5, 30)
        self.assertFalse(controller.requested_on)
        self.assertEqual(scheduler.calls, 1)
        self.assertTrue(gateway.faulted)

    def test_stop_bypasses_start_gates_and_uses_scheduler_gateway(self):
        gateway, controller, scheduler, config, runtime, _ = build_gateway()
        controller.force_on(source="timer")
        config.timer_start_allowed = False
        runtime.restart = True

        self.assertTrue(gateway.request_stop())
        self.assertFalse(controller.requested_on)
        self.assertEqual(scheduler.calls, 1)
        self.assertEqual(gateway.snapshot()["stops"], 1)

    def test_stop_error_preserves_authoritative_off_truth(self):
        gateway, controller, scheduler, _, _, _ = build_gateway()
        controller.force_on(source="timer")
        scheduler.failure = OSError("persistence failed")

        with self.assertRaises(OSError):
            gateway.request_stop()
        self.assertFalse(controller.requested_on)
        self.assertFalse(gateway.faulted)

    def test_nonboolean_stop_result_is_invariant_error_but_off(self):
        gateway, controller, scheduler, _, _, _ = build_gateway()
        controller.force_on()
        scheduler.result = 1

        with self.assertRaises(ManualControlInvariantError):
            gateway.request_stop()
        self.assertFalse(controller.requested_on)

    def test_begin_truth_failure_releases_guard_and_later_stop_still_works(self):
        gateway, controller, scheduler, _, _, _ = build_gateway()
        controller.force_on(source="timer")
        controller.requested_on_calls = 0
        controller.requested_on_failure_at = 1
        controller.requested_on_failure = OSError("truth unavailable")

        with self.assertRaises(OSError):
            gateway.request_stop()
        snapshot = gateway.snapshot()
        self.assertTrue(snapshot["faulted"])
        self.assertFalse(snapshot["operation_active"])
        self.assertEqual(scheduler.calls, 0)

        controller.requested_on_failure_at = None
        self.assertTrue(gateway.request_stop())
        self.assertFalse(controller.requested_on)
        self.assertEqual(scheduler.calls, 1)

    def test_finish_truth_failure_releases_guard_and_fails_safe_off(self):
        gateway, controller, scheduler, _, _, _ = build_gateway()
        controller.requested_on_calls = 0
        controller.requested_on_failure_at = 3
        controller.requested_on_failure = OSError("truth unavailable")

        with self.assertRaises(OSError):
            gateway.request_stop()
        snapshot = gateway.snapshot()
        self.assertTrue(snapshot["faulted"])
        self.assertFalse(snapshot["operation_active"])
        self.assertFalse(controller.requested_on)
        self.assertEqual(scheduler.calls, 2)

        controller.requested_on_failure_at = None
        self.assertFalse(gateway.request_stop())
        self.assertFalse(gateway.snapshot()["operation_active"])

    def test_real_controller_requires_synchronized_off_and_sends_no_protocol(self):
        port = RecordingProtocolPort()
        controller = HeaterController(port)
        gateway, _, scheduler, _, _, _ = build_gateway(controller)

        with self.assertRaises(ManualControlStateConflictError):
            gateway.request_start(7, 0, CONTROL_MODE_POWER, None, 5, 30)
        self.assertFalse(controller.requested_on)
        self.assertEqual(port.calls, [])

        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        before = list(port.calls)
        self.assertTrue(
            gateway.request_start(7, 0, CONTROL_MODE_POWER, None, 5, 30)
        )
        self.assertTrue(controller.requested_on)
        self.assertEqual(controller.request_revision, 1)
        self.assertEqual(port.calls, before)
        self.assertEqual(scheduler.calls, 0)

        # A response loss may not leave a user start latent indefinitely.
        controller.step(6001)
        self.assertFalse(controller.requested_on)
        self.assertEqual(controller.request_revision, 2)
        self.assertFalse(any(call[0] == "start" for call in port.calls))

    def test_real_controller_rejects_stale_off_truth(self):
        port = RecordingProtocolPort()
        controller = HeaterController(port)
        synchronize(controller, parse_frame(REAL_OFF_STATUS))
        before = list(port.calls)
        config = FakeConfigManager()
        runtime = FakeConfiguredRuntime()
        scheduler = FakeSchedulerGateway(controller)
        gateway = ManualControlGateway(
            controller,
            scheduler,
            config,
            runtime,
            ticks_ms=lambda: 1020,
            ticks_add=lambda now, delta: now + delta,
        )

        with self.assertRaises(ManualControlStateConflictError):
            gateway.request_start(7, 0, CONTROL_MODE_POWER, None, 5, 30)
        self.assertFalse(controller.requested_on)
        self.assertEqual(port.calls, before)

    def test_module_is_hardware_and_protocol_free(self):
        source = inspect.getsource(gateway_module)
        tree = ast.parse(source)
        imports = set()
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif node.module:
                    imports.add(node.module.split(".")[0])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
        self.assertNotIn("machine", imports)
        self.assertNotIn("hardware", imports)
        self.assertNotIn("protocol", imports)
        self.assertNotIn("step", calls)


if __name__ == "__main__":
    unittest.main()
