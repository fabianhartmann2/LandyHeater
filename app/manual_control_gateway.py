"""Synchronous user-control boundary for the local REST application.

The gateway changes Requested State only.  It never imports the heater
protocol, calls ``controller.step()`` or performs hardware I/O.  A start is
accepted only against one exact persisted configuration generation, one exact
Requested-State revision and freshly synchronized controller truth.  Stop is
always routed through :class:`SchedulerControllerGateway` so an active timer
occurrence is durably marked as a manual override.
"""

import time as _time


SOURCE_MANUAL = "manual"
SOURCE_QUICK_START = "quick_start"
MANUAL_START_VALID_MS = 5000


class ManualControlError(RuntimeError):
    pass


class ManualControlConflictError(ManualControlError):
    pass


class ManualControlConfigurationConflictError(ManualControlConflictError):
    pass


class ManualControlStateConflictError(ManualControlConflictError):
    pass


class ManualControlUnavailableError(ManualControlError):
    pass


class ManualControlInvariantError(ManualControlError):
    pass


def _plain_ticks_ms():
    return 0


def _plain_ticks_add(now_ms, delta_ms):
    return now_ms + delta_ms


_platform_ticks_ms = getattr(_time, "ticks_ms", _plain_ticks_ms)
_platform_ticks_add = getattr(_time, "ticks_add", _plain_ticks_add)


def _require_integer(name, value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("{} must be an integer".format(name))
    return value


def _require_exact_snapshot(name, value, fields):
    if type(value) is not dict or frozenset(value) != frozenset(fields):
        raise ManualControlInvariantError(
            "{} snapshot is malformed".format(name)
        )
    return value


class ManualControlGateway:
    """Apply idempotent manual start/stop requests without protocol access."""

    __slots__ = (
        "__controller",
        "__scheduler_gateway",
        "__config_manager",
        "__configured_runtime",
        "__ticks_ms",
        "__ticks_add",
        "__operation_active",
        "__operation_reentered",
        "__operation_started_off",
        "__operation_committed_on",
        "__faulted",
        "__last_error",
        "__starts",
        "__stops",
    )

    def __init__(
        self,
        controller,
        scheduler_gateway,
        config_manager,
        configured_runtime,
        ticks_ms=None,
        ticks_add=None,
    ):
        for name in (
            "manual_start_available",
            "request_start",
            "requested_matches",
        ):
            if not callable(getattr(controller, name, None)):
                raise ValueError("controller must provide {}()".format(name))
        for name in ("requested_on", "request_revision"):
            if not hasattr(controller, name):
                raise ValueError("controller must expose {}".format(name))
        if not callable(
            getattr(scheduler_gateway, "request_manual_stop", None)
        ):
            raise ValueError(
                "scheduler_gateway must provide request_manual_stop()"
            )
        for name in (
            "generation",
            "timer_start_allowed",
        ):
            if not hasattr(config_manager, name):
                raise ValueError("config_manager must expose {}".format(name))
        if not callable(getattr(configured_runtime, "snapshot", None)):
            raise ValueError("configured_runtime must provide snapshot()")
        if not callable(
            getattr(configured_runtime, "restart_required", None)
        ):
            raise ValueError(
                "configured_runtime must provide restart_required()"
            )
        if ticks_ms is None:
            ticks_ms = _platform_ticks_ms
        if ticks_add is None:
            ticks_add = _platform_ticks_add
        if not callable(ticks_ms) or not callable(ticks_add):
            raise ValueError("tick helpers must be callable")

        self.__controller = controller
        self.__scheduler_gateway = scheduler_gateway
        self.__config_manager = config_manager
        self.__configured_runtime = configured_runtime
        self.__ticks_ms = ticks_ms
        self.__ticks_add = ticks_add
        self.__operation_active = False
        self.__operation_reentered = False
        self.__operation_started_off = True
        self.__operation_committed_on = False
        self.__faulted = False
        self.__last_error = None
        self.__starts = 0
        self.__stops = 0

    @property
    def faulted(self):
        return self.__faulted

    def _now(self):
        value = self.__ticks_ms()
        return _require_integer("ticks_ms", value)

    def _begin_operation(self):
        if self.__operation_active:
            self.__operation_reentered = True
            raise ManualControlInvariantError(
                "manual control operation was re-entered"
            )
        self.__operation_active = True
        self.__operation_reentered = False
        self.__operation_started_off = False
        self.__operation_committed_on = False
        try:
            requested_on = self.__controller.requested_on
            if type(requested_on) is not bool:
                raise ManualControlInvariantError(
                    "controller Requested State is malformed"
                )
            self.__operation_started_off = requested_on is False
        except BaseException:
            self.__faulted = True
            self.__last_error = "manual_control_begin_failed"
            self.__operation_active = False
            self.__operation_reentered = False
            self.__operation_committed_on = False
            raise

    def _fail_safe_off(self):
        result = self.__scheduler_gateway.request_manual_stop()
        if type(result) is not bool:
            raise ManualControlInvariantError(
                "manual stop gateway returned a non-boolean"
            )
        if self.__controller.requested_on is not False:
            raise ManualControlInvariantError(
                "manual stop did not commit Requested OFF"
            )
        return result

    def _finish_operation(self, primary_error):
        rollback_error = None
        finish_error = None
        unassociated_on = False
        needs_off_verification = (
            self.__operation_started_off
            and (
                self.__operation_reentered
                or not self.__operation_committed_on
            )
        )
        try:
            if needs_off_verification:
                try:
                    requested_on = self.__controller.requested_on
                    if type(requested_on) is not bool:
                        raise ManualControlInvariantError(
                            "controller Requested State is malformed"
                        )
                except BaseException as error:
                    finish_error = error
                    self.__faulted = True
                    self.__last_error = "manual_control_finish_failed"
                    try:
                        self._fail_safe_off()
                    except BaseException as rollback:
                        rollback_error = rollback
                        self.__last_error = "manual_control_rollback_failed"
                else:
                    unassociated_on = requested_on is not False

            if self.__operation_reentered or unassociated_on:
                self.__faulted = True
                self.__last_error = (
                    "manual_control_reentrancy_detected"
                    if self.__operation_reentered
                    else "manual_control_unassociated_request_detected"
                )
                if unassociated_on:
                    try:
                        self._fail_safe_off()
                    except BaseException as error:
                        rollback_error = error
                        self.__last_error = "manual_control_rollback_failed"
        finally:
            self.__operation_active = False
            self.__operation_reentered = False
            self.__operation_committed_on = False
        if rollback_error is not None:
            raise rollback_error
        if finish_error is not None and primary_error is None:
            raise finish_error
        if primary_error is None and self.__faulted and self.__last_error in (
            "manual_control_reentrancy_detected",
            "manual_control_unassociated_request_detected",
        ):
            raise ManualControlInvariantError(
                "manual control operation lost its state association"
            )

    def _configuration_snapshot(self, expected_generation):
        _require_integer("expected_configuration_generation", expected_generation)
        generation = self.__config_manager.generation
        gate = self.__config_manager.timer_start_allowed
        if type(generation) is not int or type(gate) is not bool:
            raise ManualControlInvariantError(
                "configuration start gate is malformed"
            )
        if generation != expected_generation:
            raise ManualControlConfigurationConflictError(
                "configuration generation changed"
            )
        if not gate:
            raise ManualControlUnavailableError(
                "configuration start gate is closed"
            )
        restart_required = self.__configured_runtime.restart_required(
            self.__config_manager
        )
        if type(restart_required) is not bool:
            raise ManualControlInvariantError(
                "runtime restart gate is malformed"
            )
        if restart_required:
            raise ManualControlUnavailableError(
                "configured runtime restart is required"
            )
        snapshot = _require_exact_snapshot(
            "configured runtime",
            self.__configured_runtime.snapshot(),
            (
                "configuration_generation",
                "ledger_generation",
                "setup_complete",
                "persistent_start_gate_open",
                "quick_start",
                "clock_valid",
                "scheduler_armed",
            ),
        )
        if (
            snapshot["configuration_generation"] != expected_generation
            or snapshot["setup_complete"] is not True
            or snapshot["persistent_start_gate_open"] is not True
        ):
            raise ManualControlUnavailableError(
                "configured runtime is not start-authoritative"
            )
        if (
            self.__config_manager.generation != expected_generation
            or self.__config_manager.timer_start_allowed is not True
            or self.__configured_runtime.restart_required(
                self.__config_manager
            ) is not False
        ):
            raise ManualControlConfigurationConflictError(
                "configuration changed during start staging"
            )
        return snapshot

    def _request_matches(
        self,
        mode,
        target_temperature,
        power_level,
        runtime_minutes,
        source,
    ):
        result = self.__controller.requested_matches(
            True,
            mode,
            target_temperature,
            power_level,
            runtime_minutes,
            source,
            None,
            True,
        )
        if type(result) is not bool:
            raise ManualControlInvariantError(
                "controller requested comparison returned a non-boolean"
            )
        return result

    def _confirm_configuration_authority(self, expected_generation):
        # ``restart_required`` is the only callback in this final fence.
        # Run it first, then sample the ConfigManager's allocation-free
        # generation and gate truth so a callback mutation cannot be accepted
        # through stale local values.
        restart_required = self.__configured_runtime.restart_required(
            self.__config_manager
        )
        generation = self.__config_manager.generation
        gate = self.__config_manager.timer_start_allowed
        if (
            type(generation) is not int
            or type(gate) is not bool
            or type(restart_required) is not bool
        ):
            raise ManualControlInvariantError(
                "configuration start authority is malformed"
            )
        if (
            generation != expected_generation
            or gate is not True
            or restart_required is not False
        ):
            raise ManualControlConfigurationConflictError(
                "application state changed during start staging"
            )
        return None

    def _start_request(
        self,
        expected_configuration_generation,
        expected_requested_revision,
        mode,
        target_temperature,
        power_level,
        runtime_minutes,
        source,
        runtime_snapshot=None,
    ):
        if self.__faulted:
            raise ManualControlUnavailableError(
                "manual control gateway is faulted"
            )
        if runtime_snapshot is None:
            runtime_snapshot = self._configuration_snapshot(
                expected_configuration_generation
            )
        _require_integer("expected_requested_revision", expected_requested_revision)
        revision = self.__controller.request_revision
        if type(revision) is not int:
            raise ManualControlInvariantError(
                "controller request revision is malformed"
            )
        if revision != expected_requested_revision:
            if (
                revision == expected_requested_revision + 1
                and self._request_matches(
                    mode,
                    target_temperature,
                    power_level,
                    runtime_minutes,
                    source,
                )
            ):
                return False
            raise ManualControlConflictError("Requested State changed")

        # All configuration/runtime callbacks happen before the final
        # controller readiness decision.  Once that final decision returns,
        # only allocation-free controller truth is read before request_start;
        # a callback cannot therefore make an unsynchronised start latent.
        self._confirm_configuration_authority(
            expected_configuration_generation
        )

        now_ms = self._now()
        available = self.__controller.manual_start_available(
            now_ms,
            mode,
            target_temperature,
            power_level,
            runtime_minutes,
            source,
        )
        if type(available) is not bool:
            raise ManualControlInvariantError(
                "controller availability returned a non-boolean"
            )
        if not available:
            raise ManualControlStateConflictError(
                "heater is not available for a manual start"
            )
        deadline_ms = self.__ticks_add(now_ms, MANUAL_START_VALID_MS)
        _require_integer("manual start deadline", deadline_ms)

        # Availability and tick helpers are injected callbacks.  Reconfirm
        # the durable/static authority after all of them have returned and
        # immediately before touching Requested State.
        self._confirm_configuration_authority(
            expected_configuration_generation
        )
        if (
            self.__controller.request_revision != expected_requested_revision
            or self.__controller.requested_on is not False
        ):
            raise ManualControlConflictError(
                "application state changed during start staging"
            )

        changed = self.__controller.request_start(
            mode=mode,
            target_temperature=target_temperature,
            power_level=power_level,
            runtime_minutes=runtime_minutes,
            source=source,
            not_after_ms=deadline_ms,
            now_ms=now_ms,
        )
        if type(changed) is not bool or not changed:
            raise ManualControlInvariantError(
                "controller failed to commit a new Requested State"
            )
        if (
            self.__controller.request_revision
            != expected_requested_revision + 1
            or not self._request_matches(
                mode,
                target_temperature,
                power_level,
                runtime_minutes,
                source,
            )
        ):
            raise ManualControlInvariantError(
                "controller Requested-State readback differs"
            )
        # ``request_start`` is also a port callback.  A configuration change
        # inside it must leave this operation unassociated so the outer
        # finally path synchronously returns Requested State to OFF.
        self._confirm_configuration_authority(
            expected_configuration_generation
        )
        if (
            self.__controller.request_revision
            != expected_requested_revision + 1
            or self.__controller.requested_on is not True
        ):
            raise ManualControlInvariantError(
                "controller Requested-State association changed"
            )
        self.__operation_committed_on = True
        self.__starts += 1
        return True

    def request_start(
        self,
        expected_configuration_generation,
        expected_requested_revision,
        mode,
        target_temperature=None,
        power_level=None,
        runtime_minutes=60,
    ):
        self._begin_operation()
        primary_error = None
        try:
            return self._start_request(
                expected_configuration_generation,
                expected_requested_revision,
                mode,
                target_temperature,
                power_level,
                runtime_minutes,
                SOURCE_MANUAL,
            )
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def request_quick_start(
        self,
        expected_configuration_generation,
        expected_requested_revision,
    ):
        self._begin_operation()
        primary_error = None
        try:
            runtime = self._configuration_snapshot(
                expected_configuration_generation
            )
            quick = _require_exact_snapshot(
                "quick start",
                runtime["quick_start"],
                (
                    "mode",
                    "target_temperature",
                    "power_level",
                    "runtime_minutes",
                ),
            )
            return self._start_request(
                expected_configuration_generation,
                expected_requested_revision,
                quick["mode"],
                quick["target_temperature"],
                quick["power_level"],
                quick["runtime_minutes"],
                SOURCE_QUICK_START,
                runtime_snapshot=runtime,
            )
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def request_stop(self):
        """Commit Requested OFF even if configuration or start gates failed."""

        self._begin_operation()
        primary_error = None
        try:
            result = self.__scheduler_gateway.request_manual_stop()
            if type(result) is not bool:
                raise ManualControlInvariantError(
                    "manual stop gateway returned a non-boolean"
                )
            if self.__controller.requested_on is not False:
                raise ManualControlInvariantError(
                    "manual stop did not commit Requested OFF"
                )
            if result:
                self.__stops += 1
            return result
        except BaseException as error:
            primary_error = error
            if self.__controller.requested_on is not False:
                self.__faulted = True
                self.__last_error = "manual_stop_failed"
            raise
        finally:
            self._finish_operation(primary_error)

    def reset_fault(self):
        self._begin_operation()
        primary_error = None
        try:
            if not self.__faulted:
                return False
            if self.__controller.requested_on is not False:
                raise ManualControlUnavailableError(
                    "manual control fault cannot reset while Requested ON"
                )
            self.__faulted = False
            self.__last_error = None
            return True
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def snapshot(self):
        revision = self.__controller.request_revision
        if type(revision) is not int:
            raise ManualControlInvariantError(
                "controller request revision is malformed"
            )
        return {
            "faulted": self.__faulted,
            "last_error": self.__last_error,
            "request_revision": revision,
            "starts": self.__starts,
            "stops": self.__stops,
            "operation_active": self.__operation_active,
        }
