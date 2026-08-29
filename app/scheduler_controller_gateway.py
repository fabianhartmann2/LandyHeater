"""Synchronous handoff between Scheduler and HeaterController requested state.

The gateway performs no hardware or protocol operation.  It never calls
``HeaterController.step()``.  Authorization, requested-state mutation and
Scheduler completion happen in one synchronous stack without yielding.
"""

import time as _time


TIMER_SOURCE = "timer"

_HISTORY_FIELDS = frozenset((
    "consumed_local_high_water",
    "occurrences",
))
_HISTORY_OCCURRENCE_FIELDS = frozenset((
    "timer_id",
    "occurrence_key",
    "local_minute_id",
    "status",
    "overridden",
))


def _plain_ticks_ms():
    return int(_time.monotonic() * 1000)


_platform_ticks_ms = getattr(_time, "ticks_ms", _plain_ticks_ms)


def _require_tick(value):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("ticks_ms() must return an integer")
    return value


def _exact_history_equal(first, second):
    """Compare only exact JSON-primitives; reject equality-spoof objects."""

    if (
        type(first) is not dict
        or type(second) is not dict
        or frozenset(first) != _HISTORY_FIELDS
        or frozenset(second) != _HISTORY_FIELDS
    ):
        return False
    for history in (first, second):
        high_water = history["consumed_local_high_water"]
        if high_water is not None and type(high_water) is not int:
            return False
        if type(history["occurrences"]) is not list:
            return False
    if first["consumed_local_high_water"] != second[
        "consumed_local_high_water"
    ]:
        return False
    first_items = first["occurrences"]
    second_items = second["occurrences"]
    if len(first_items) != len(second_items):
        return False
    for first_item, second_item in zip(first_items, second_items):
        for item in (first_item, second_item):
            if (
                type(item) is not dict
                or frozenset(item) != _HISTORY_OCCURRENCE_FIELDS
                or type(item["timer_id"]) is not str
                or type(item["occurrence_key"]) is not str
                or type(item["local_minute_id"]) is not int
                or type(item["status"]) is not str
                or type(item["overridden"]) is not bool
            ):
                return False
        for field in _HISTORY_OCCURRENCE_FIELDS:
            if first_item[field] != second_item[field]:
                return False
    return True


class SchedulerControllerGateway:
    """Apply short-lived timer intents to Requested State only."""

    __slots__ = (
        "__scheduler",
        "__controller",
        "__persistence",
        "__ticks_ms",
        "__pending_override_key",
        "__faulted",
        "__last_error",
        "__applied",
        "__rejected",
        "__manual_stops",
        "__checkpoints",
        "__checkpoint_failures",
        "__operation_active",
        "__operation_reentered",
    )

    def __init__(self, scheduler, controller, ticks_ms=None, persistence=None):
        for method_name in (
            "step",
            "authorize_intent",
            "complete_intent",
            "mark_manual_override",
            "mark_active_complete",
        ):
            if not callable(getattr(scheduler, method_name, None)):
                raise ValueError(
                    "scheduler must provide {}()".format(method_name)
                )
        for method_name in (
            "timer_start_available",
            "timer_session_complete",
            "request_start",
            "request_stop",
            "requested_matches",
        ):
            if not callable(getattr(controller, method_name, None)):
                raise ValueError(
                    "controller must provide {}()".format(method_name)
                )
        if not hasattr(scheduler, "active_occurrence_key"):
            raise ValueError("scheduler must expose active_occurrence_key")
        if not hasattr(controller, "requested_on"):
            raise ValueError("controller must expose requested_on")
        if not hasattr(controller, "requested_source"):
            raise ValueError("controller must expose requested_source")
        if persistence is not None:
            if not callable(
                getattr(scheduler, "export_persistent_history", None)
            ):
                raise ValueError(
                    "scheduler must provide export_persistent_history()"
                )
            if not callable(
                getattr(persistence, "checkpoint_scheduler_history", None)
            ):
                raise ValueError(
                    "persistence must provide checkpoint_scheduler_history()"
                )
            if not callable(
                getattr(persistence, "scheduler_history_for_restore", None)
            ):
                raise ValueError(
                    "persistence must provide scheduler_history_for_restore()"
                )
            if not hasattr(persistence, "ledger_generation"):
                raise ValueError("persistence must expose ledger_generation")
            if not hasattr(persistence, "timer_start_allowed"):
                raise ValueError("persistence must expose timer_start_allowed")
        if ticks_ms is None:
            ticks_ms = _platform_ticks_ms
        if not callable(ticks_ms):
            raise ValueError("ticks_ms must be callable")

        self.__scheduler = scheduler
        self.__controller = controller
        self.__persistence = persistence
        self.__ticks_ms = ticks_ms
        self.__pending_override_key = None
        self.__faulted = False
        self.__last_error = None
        self.__applied = 0
        self.__rejected = 0
        self.__manual_stops = 0
        self.__checkpoints = 0
        self.__checkpoint_failures = 0
        self.__operation_active = False
        self.__operation_reentered = False

    @property
    def faulted(self):
        return self.__faulted

    def _now(self):
        return _require_tick(self.__ticks_ms())

    def _begin_operation(self):
        if self.__operation_active:
            self.__operation_reentered = True
            self.__faulted = True
            self.__last_error = "timer_gateway_reentrancy_detected"
            raise RuntimeError("gateway operation is already active")
        self.__operation_active = True
        self.__operation_reentered = False

    def _finish_operation(self, primary_error):
        reentered = self.__operation_reentered
        if not reentered:
            self.__operation_active = False
            return

        self.__faulted = True
        self.__last_error = "timer_gateway_reentrancy_detected"
        rollback_error = None
        try:
            if self.__controller.requested_on is not False:
                self._fail_safe_requested_off()
        except BaseException as error:
            self.__last_error = "timer_gateway_reentrancy_rollback_failed"
            rollback_error = error
        self.__operation_active = False
        self.__operation_reentered = False
        if rollback_error is not None:
            raise rollback_error
        if primary_error is None:
            raise RuntimeError("gateway operation was re-entered")

    def _persistence_start_allowed(self):
        if self.__persistence is None:
            return True
        result = self.__persistence.timer_start_allowed
        if type(result) is not bool:
            raise RuntimeError(
                "persistence timer_start_allowed is not boolean"
            )
        return result

    def _checkpoint_scheduler_history(self):
        """Durably consume/suppress occurrences before authorization."""

        if self.__persistence is None:
            return True
        history = self.__scheduler.export_persistent_history()
        generation = self.__persistence.ledger_generation
        if type(generation) is not int:
            raise RuntimeError("persistence ledger_generation is invalid")
        result = self.__persistence.checkpoint_scheduler_history(
            history, generation
        )
        if type(result) is not bool:
            raise RuntimeError(
                "persistence checkpoint returned a non-boolean"
            )
        confirmed_generation = self.__persistence.ledger_generation
        if type(confirmed_generation) is not int:
            raise RuntimeError("confirmed ledger_generation is invalid")
        expected_confirmed_generation = generation + (1 if result else 0)
        if confirmed_generation != expected_confirmed_generation:
            raise RuntimeError(
                "persistence checkpoint generation was not confirmed"
            )
        confirmed_history = (
            self.__persistence.scheduler_history_for_restore()
        )
        if not _exact_history_equal(confirmed_history, history):
            raise RuntimeError(
                "persistence checkpoint readback differs"
            )
        # No caller or injected storage implementation may mutate Scheduler
        # truth while its durable checkpoint is being published.
        if not _exact_history_equal(
            self.__scheduler.export_persistent_history(), history
        ):
            raise RuntimeError(
                "scheduler history changed during persistent checkpoint"
            )
        self.__checkpoints += 1
        return True

    def _checkpoint_or_fault(self):
        requested_was_off = self.__controller.requested_on is False
        try:
            result = self._checkpoint_scheduler_history()
        except BaseException as error:
            self.__faulted = True
            self.__last_error = "timer_persistence_checkpoint_failed"
            self.__checkpoint_failures += 1
            if requested_was_off and self.__controller.requested_on is not False:
                try:
                    self._fail_safe_requested_off()
                except BaseException as rollback_error:
                    self.__last_error = "timer_persistence_rollback_failed"
                    raise rollback_error
            if isinstance(error, MemoryError) or not isinstance(error, Exception):
                raise
            return False
        if requested_was_off and self.__controller.requested_on is not False:
            self.__faulted = True
            self.__last_error = "timer_persistence_reentrancy_detected"
            self.__checkpoint_failures += 1
            self._fail_safe_requested_off()
            return False
        return result

    @staticmethod
    def _requested_dict(authorized, on):
        return {
            "on": on,
            "mode": authorized.mode,
            "target_temperature": authorized.target_temperature,
            "power_level": authorized.power_level,
            "runtime_minutes": authorized.runtime_minutes,
            "source": authorized.source,
        }

    def _authorized_request_matches(self, authorized):
        return self.__controller.requested_matches(
            True,
            authorized.mode,
            authorized.target_temperature,
            authorized.power_level,
            authorized.runtime_minutes,
            authorized.source,
            authorized.not_after_ms,
        )

    def _timer_start_available(self, now_ms, request=None):
        result = self.__controller.timer_start_available(now_ms, request)
        if type(result) is not bool:
            raise RuntimeError(
                "controller timer_start_available returned a non-boolean"
            )
        return result

    def _fail_safe_requested_off(self):
        if self.__controller.requested_on is False:
            return True
        result = self.__controller.request_stop()
        if type(result) is not bool:
            raise RuntimeError(
                "controller request_stop returned a non-boolean"
            )
        if self.__controller.requested_on is not False:
            raise RuntimeError("controller failed to commit Requested OFF")
        return True

    def _repair_override(self, now_ms):
        key = self.__pending_override_key
        if key is None:
            return True
        if self.__scheduler.active_occurrence_key != key:
            self.__pending_override_key = None
            return True
        try:
            repaired = self.__scheduler.mark_manual_override(key, now_ms)
        except Exception:
            self.__last_error = "timer_override_bookkeeping_failed"
            return False
        if type(repaired) is not bool:
            self.__faulted = True
            self.__last_error = "timer_override_bookkeeping_failed"
            return False
        if repaired:
            self.__pending_override_key = None
            self.__last_error = (
                "timer_override_bookkeeping_recovered"
                if self.__faulted
                else None
            )
            return True
        return False

    def _reconcile_active(self, now_ms):
        if self.__pending_override_key is not None:
            return False
        key = self.__scheduler.active_occurrence_key
        if key is None:
            return False
        complete = self.__controller.timer_session_complete(now_ms)
        if type(complete) is not bool:
            self.__faulted = True
            self.__last_error = "timer_completion_readiness_failed"
            return False
        if not complete:
            return False
        if self.__controller.requested_on is not False:
            self.__faulted = True
            self.__last_error = "timer_completion_requested_state_mismatch"
            try:
                self._fail_safe_requested_off()
            except BaseException:
                self.__last_error = "timer_completion_stop_failed"
                raise
            return False
        try:
            marked = self.__scheduler.mark_active_complete(
                key, now_ms, "controller_off"
            )
        except Exception:
            self.__faulted = True
            self.__last_error = "timer_completion_bookkeeping_failed"
            return False
        if type(marked) is not bool or not marked:
            self.__faulted = True
            self.__last_error = "timer_completion_bookkeeping_failed"
            return False
        return True

    def apply_intent(self, intent):
        self._begin_operation()
        primary_error = None
        try:
            return self._apply_intent_once(intent)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def _apply_intent_once(self, intent):
        """Apply one intent and never leave a callback-created orphan ON."""

        requested_was_off = self.__controller.requested_on is False
        try:
            result = self._apply_intent_core(intent)
        except BaseException:
            active_key = self.__scheduler.active_occurrence_key
            intent_key = getattr(intent, "occurrence_key", None)
            associated = (
                active_key is not None
                and active_key == intent_key
                and self.__controller.requested_on is True
                and self.__controller.requested_source == TIMER_SOURCE
            )
            if requested_was_off and self.__controller.requested_on and not associated:
                self.__faulted = True
                self.__last_error = "timer_preapply_reentrancy_detected"
                self._fail_safe_requested_off()
            raise
        if requested_was_off and self.__controller.requested_on and result is not True:
            self.__faulted = True
            self.__last_error = "timer_preapply_reentrancy_detected"
            self._fail_safe_requested_off()
            raise RuntimeError(
                "timer callback left an unassociated Requested ON state"
            )
        return result

    def _apply_intent_core(self, intent):
        """Authorize and apply one intent using a freshly sampled tick."""

        if not self._checkpoint_or_fault():
            self.__rejected += 1
            return False
        now_ms = self._now()
        availability_error = None
        try:
            generic_available = (
                not self.__faulted
                and self.__pending_override_key is None
                and self.__scheduler.active_occurrence_key is None
                and self._persistence_start_allowed()
                and self._timer_start_available(now_ms)
            )
        except BaseException as error:
            generic_available = False
            availability_error = error
        # A store or readiness adapter is an external synchronous boundary.
        # Re-sample after it so authorization never relies on the tick
        # observed before that callback returned.
        now_ms = self._now()
        authorized = self.__scheduler.authorize_intent(
            intent, now_ms, generic_available
        )
        if authorized is None:
            self.__rejected += 1
            if availability_error is not None:
                self.__faulted = True
                self.__last_error = "timer_availability_failed"
                if (
                    isinstance(availability_error, MemoryError)
                    or not isinstance(availability_error, Exception)
                ):
                    raise availability_error
            return False

        # Allocate both possible completion snapshots before Requested State
        # can change.  The post-mutation truth check itself is allocation-free.
        try:
            on_snapshot = self._requested_dict(authorized, True)
            off_snapshot = self._requested_dict(authorized, False)
        except BaseException:
            self.__scheduler.complete_intent(
                authorized, False, None, now_ms
            )
            self.__faulted = True
            self.__last_error = "timer_intent_snapshot_allocation_failed"
            raise
        applied = False
        primary_error = None
        try:
            specific_available = self._timer_start_available(
                now_ms, authorized
            )
            # HeaterController performs the final deadline check in
            # request_start().  Give it a tick sampled after its specific
            # readiness callback, and use the same synchronous tick to close
            # Scheduler phase two.
            now_ms = self._now()
            if specific_available:
                result = self.__controller.request_start(
                    authorized.mode,
                    target_temperature=authorized.target_temperature,
                    power_level=authorized.power_level,
                    runtime_minutes=authorized.runtime_minutes,
                    source=authorized.source,
                    not_after_ms=authorized.not_after_ms,
                    now_ms=now_ms,
                )
                if not isinstance(result, bool):
                    raise RuntimeError(
                        "controller request_start returned a non-boolean"
                    )
                applied = result
        except BaseException as error:
            primary_error = error

        try:
            exact_on = self._authorized_request_matches(authorized)
        except BaseException as error:
            exact_on = False
            if primary_error is None:
                primary_error = error
        if type(exact_on) is not bool:
            exact_on = False
            if primary_error is None:
                primary_error = RuntimeError(
                    "controller requested_matches returned a non-boolean"
                )
        if exact_on:
            requested_snapshot = on_snapshot
        elif self.__controller.requested_on is False:
            requested_snapshot = off_snapshot
        else:
            requested_snapshot = None

        completion_error = None
        completed = False
        try:
            completion_result = self.__scheduler.complete_intent(
                authorized, applied, requested_snapshot, now_ms
            )
            if type(completion_result) is not bool:
                raise RuntimeError(
                    "scheduler complete_intent returned a non-boolean"
                )
            completed = completion_result
        except BaseException as error:
            completion_error = error

        active_key = self.__scheduler.active_occurrence_key
        associated = (
            completed is True
            and exact_on
            and active_key == authorized.occurrence_key
        )
        if completed is True and exact_on and not associated:
            completion_error = RuntimeError(
                "scheduler did not associate the completed timer intent"
            )
        if not associated and self.__controller.requested_on:
            try:
                self._fail_safe_requested_off()
            except BaseException as stop_error:
                self.__faulted = True
                self.__last_error = "timer_start_rollback_failed"
                if completion_error is None:
                    completion_error = stop_error

        if completion_error is not None:
            self.__faulted = True
            self.__last_error = "timer_intent_completion_failed"
            raise completion_error
        if primary_error is not None:
            self.__last_error = "timer_start_application_failed"
            if (
                isinstance(primary_error, MemoryError)
                or not isinstance(primary_error, Exception)
            ):
                self.__faulted = True
                raise primary_error
        if associated:
            self.__applied += 1
            return True
        self.__rejected += 1
        return False

    def step(self):
        self._begin_operation()
        primary_error = None
        try:
            return self._step_once()
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def _step_once(self):
        """Run one step and reject every callback-created orphan ON."""

        requested_was_off = self.__controller.requested_on is False
        try:
            result = self._step_core()
        except BaseException:
            active_key = self.__scheduler.active_occurrence_key
            associated = (
                active_key is not None
                and self.__controller.requested_on is True
                and self.__controller.requested_source == TIMER_SOURCE
            )
            if requested_was_off and self.__controller.requested_on and not associated:
                self.__faulted = True
                self.__last_error = "timer_step_reentrancy_detected"
                self._fail_safe_requested_off()
            raise
        if requested_was_off and self.__controller.requested_on and result is not True:
            self.__faulted = True
            self.__last_error = "timer_step_reentrancy_detected"
            self._fail_safe_requested_off()
            raise RuntimeError(
                "timer step left an unassociated Requested ON state"
            )
        return result

    def _step_core(self):
        """Run one Scheduler step and synchronously apply at most one intent."""

        now_ms = self._now()
        self._repair_override(now_ms)
        self._reconcile_active(now_ms)
        try:
            available = (
                not self.__faulted
                and self.__pending_override_key is None
                and self.__scheduler.active_occurrence_key is None
                and self._persistence_start_allowed()
                and self._timer_start_available(now_ms)
            )
        except BaseException as error:
            self.__faulted = True
            self.__last_error = "timer_availability_failed"
            available = False
            if not isinstance(error, Exception):
                raise
        intent = self.__scheduler.step(now_ms, available)
        if not self._checkpoint_or_fault():
            return False
        if intent is None:
            return None
        return self._apply_intent_once(intent)

    def request_manual_stop(self):
        self._begin_operation()
        primary_error = None
        try:
            return self._request_manual_stop_once()
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def _request_manual_stop_once(self):
        """Commit Requested OFF before marking the active timer override."""

        now_ms = self._now()
        active_key = self.__scheduler.active_occurrence_key
        requested_on = self.__controller.requested_on
        requested_source = self.__controller.requested_source
        was_timer_request = (
            requested_on is True
            and type(requested_source) is str
            and requested_source == TIMER_SOURCE
        )
        result = False
        primary_error = None
        try:
            result = self.__controller.request_stop()
            if not isinstance(result, bool):
                raise RuntimeError(
                    "controller request_stop returned a non-boolean"
                )
        except BaseException as error:
            primary_error = error

        is_off = self.__controller.requested_on is False
        if primary_error is None and not is_off:
            primary_error = RuntimeError(
                "controller failed to commit Requested OFF"
            )
        should_override = (
            active_key is not None
            and was_timer_request
            and is_off
            and (result is True or primary_error is not None)
        )
        if should_override:
            try:
                marked = self.__scheduler.mark_manual_override(
                    active_key, now_ms
                )
            except BaseException as error:
                marked = False
                if primary_error is None:
                    primary_error = error
            if type(marked) is not bool:
                if primary_error is None:
                    primary_error = RuntimeError(
                        "scheduler mark_manual_override returned a non-boolean"
                    )
                marked = False
            if not marked:
                self.__pending_override_key = active_key
                self.__last_error = "timer_override_bookkeeping_failed"
            elif not self._checkpoint_or_fault():
                if primary_error is None:
                    primary_error = RuntimeError(
                        "timer override checkpoint failed"
                    )
        if is_off and (result or primary_error is not None):
            self.__manual_stops += 1
        if is_off and self.__controller.requested_on is not False:
            self.__faulted = True
            self.__last_error = "timer_manual_stop_reentrancy_detected"
            try:
                self._fail_safe_requested_off()
            except BaseException as rollback_error:
                if primary_error is None:
                    primary_error = rollback_error
            if primary_error is None:
                primary_error = RuntimeError(
                    "manual stop callback restored Requested ON"
                )
        if primary_error is not None:
            self.__faulted = True
            if self.__last_error != "timer_override_bookkeeping_failed":
                self.__last_error = "timer_manual_stop_failed"
            raise primary_error
        return bool(result)

    def reset_fault(self):
        self._begin_operation()
        primary_error = None
        try:
            return self._reset_fault_once()
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def _reset_fault_once(self):
        if not self.__faulted:
            return False
        if self.__controller.requested_on:
            raise RuntimeError("gateway fault cannot reset while Requested ON")
        if self.__pending_override_key is not None:
            raise RuntimeError("gateway override repair is still pending")
        if not self._checkpoint_or_fault():
            raise RuntimeError("gateway persistence remains unavailable")
        if not self._persistence_start_allowed():
            raise RuntimeError("persistent timer start gate remains closed")
        if self.__controller.requested_on is not False:
            self.__last_error = "timer_reset_reentrancy_detected"
            self._fail_safe_requested_off()
            raise RuntimeError(
                "gateway reset callback restored Requested ON"
            )
        self.__faulted = False
        self.__last_error = None
        return True

    def snapshot(self):
        return {
            "faulted": self.__faulted,
            "last_error": self.__last_error,
            "pending_override_key": self.__pending_override_key,
            "applied": self.__applied,
            "rejected": self.__rejected,
            "manual_stops": self.__manual_stops,
            "persistence_enabled": self.__persistence is not None,
            "checkpoints": self.__checkpoints,
            "checkpoint_failures": self.__checkpoint_failures,
            "operation_active": self.__operation_active,
        }
