"""USB-only MicroPython smoke test for the Phase-5 software core.

The test uses deterministic, synthetic clock values.  It imports no board
configuration, hardware driver, heater controller or protocol module and
performs no pin, UART, I2C or 1-Wire operation.  Importing this module does
not run the test; :func:`run` must be called explicitly.
"""

import gc as _gc

SOFTWARE_ONLY_CONFIRMATION = "USB_ONLY_NO_GPIO_CONNECTED"
PHASE5_PASS_TOKEN = "PHASE5_USB_SMOKE_PASS_V1"
DEFAULT_ITERATIONS = 8
MAX_ITERATIONS = 32
MINIMUM_FREE_HEAP_BYTES = 32 * 1024
MAXIMUM_HEAP_DRIFT_BYTES = 4096
_EXPECTED_OCCURRENCE_KEY = "phase5-smoke|2026-08-09|14:30"


def _require(condition, message):
    if not condition:
        raise RuntimeError("phase5 software smoke failed: {}".format(message))


def _memory_free():
    """Return free MicroPython heap bytes, or ``None`` on CPython."""

    _gc.collect()
    reader = getattr(_gc, "mem_free", None)
    if not callable(reader):
        return None
    value = reader()
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError("gc.mem_free() returned an invalid value")
    return value


def _plain_ticks_diff(newer, older):
    return newer - older


def _plain_ticks_add(ticks, delta):
    return ticks + delta


def _load_software_core():
    """Load only the three hardware-independent Phase-5 dependencies."""

    from app.application_state import CONTROL_MODE_POWER
    from app.scheduler import Scheduler
    from services.time_service import CLOCK_SOURCE_RTC, TimeService

    return (
        CONTROL_MODE_POWER,
        Scheduler,
        CLOCK_SOURCE_RTC,
        TimeService,
    )


def _check_platform_ticks():
    """Exercise MicroPython's real wrap-safe tick primitives when present."""

    try:
        from time import ticks_add, ticks_diff, ticks_ms
    except ImportError:
        return False
    now_ms = ticks_ms()
    future_ms = ticks_add(now_ms, 17)
    _require(
        ticks_diff(future_ms, now_ms) == 17,
        "MicroPython tick primitives are inconsistent",
    )
    return True


def _check_memory(
    memory_before,
    memory_after_import,
    memory_after_warmup,
    memory_after,
):
    values = (
        memory_before,
        memory_after_import,
        memory_after_warmup,
        memory_after,
    )
    available = tuple(value is not None for value in values)
    if not any(available):
        return False
    _require(all(available), "heap measurements are incomplete")
    _require(
        memory_after_import >= MINIMUM_FREE_HEAP_BYTES,
        "free heap after core import is below 32 KiB",
    )
    _require(
        memory_after_warmup >= MINIMUM_FREE_HEAP_BYTES,
        "free heap after warm-up is below 32 KiB",
    )
    _require(
        memory_after >= MINIMUM_FREE_HEAP_BYTES,
        "free heap after smoke test is below 32 KiB",
    )
    allowed_drift = max(
        MAXIMUM_HEAP_DRIFT_BYTES, memory_after_warmup // 50
    )
    _require(
        memory_after >= memory_after_warmup - allowed_drift,
        "free heap did not recover after bounded iterations",
    )
    return True


def _timer_definition(control_mode_power):
    return {
        "id": "phase5-smoke",
        "name": "Phase 5 USB smoke",
        "enabled": True,
        # 2026-08-09 is Sunday; Monday is weekday zero.
        "weekdays": [6],
        "start": "14:30",
        "mode": control_mode_power,
        "target_temperature": None,
        "power_level": 5,
        "runtime_minutes": 30,
    }


def _requested_snapshot(authorized):
    """Create the synthetic application result used by this isolated test."""

    return {
        "on": True,
        "mode": authorized.mode,
        "target_temperature": authorized.target_temperature,
        "power_level": authorized.power_level,
        "runtime_minutes": authorized.runtime_minutes,
        "source": authorized.source,
    }


def _run_iteration(core):
    control_mode_power, Scheduler, clock_source_rtc, TimeService = core
    clock = TimeService(ticks_diff=_plain_ticks_diff)
    _require(
        clock.set_utc_datetime(
            2026,
            8,
            9,
            14,
            29,
            59,
            clock_source_rtc,
            0,
        )
        is True,
        "synthetic RTC sample was not accepted",
    )
    clock_snapshot = clock.snapshot(0)
    _require(clock_snapshot["valid"] is True, "synthetic clock is invalid")
    _require(clock_snapshot["health"] == "ok", "clock health differs")
    _require(clock_snapshot["rtc_health"] == "ok", "RTC health differs")
    _require(
        clock_snapshot["rtc_write_pending"] is False,
        "synthetic RTC unexpectedly needs persistence",
    )
    _require(clock_snapshot["source"] == "rtc", "clock source differs")
    _require(clock_snapshot["clock_revision"] == 1, "clock revision differs")
    _require(
        clock_snapshot["utc_seconds"] == 839600999,
        "synthetic UTC seconds differ",
    )
    _require(
        clock_snapshot["local"]
        == {
            "year": 2026,
            "month": 8,
            "day": 9,
            "weekday": 6,
            "hour": 14,
            "minute": 29,
            "second": 59,
            "local_minute_id": 13993349,
            "fold": 0,
        },
        "synthetic local civil time differs",
    )

    scheduler = Scheduler(
        clock,
        maximum_runtime_minutes=120,
        ticks_diff=_plain_ticks_diff,
        ticks_add=_plain_ticks_add,
        intent_valid_ms=5000,
    )
    _require(
        scheduler.replace_timers((_timer_definition(control_mode_power),))
        is True,
        "timer configuration was not accepted",
    )
    _require(scheduler.arm() is True, "scheduler did not arm")
    next_occurrence = scheduler.next_occurrence(0)
    _require(next_occurrence is not None, "next occurrence is missing")
    _require(
        next_occurrence
        == {
            "occurrence_key": _EXPECTED_OCCURRENCE_KEY,
            "timer_id": "phase5-smoke",
            "local_date": "2026-08-09",
            "start": "14:30",
            "weekday": 6,
            "minutes_from_now": 1,
        },
        "next occurrence differs",
    )
    _require(
        scheduler.step(0, control_available=True) is None,
        "first clock observation was not fenced",
    )

    intent = scheduler.step(1000, control_available=True)
    _require(intent is not None, "natural timer edge created no intent")
    _require(
        intent.snapshot()
        == {
            "occurrence_key": _EXPECTED_OCCURRENCE_KEY,
            "timer_id": "phase5-smoke",
            "timer_revision": 1,
            "mode": control_mode_power,
            "target_temperature": None,
            "power_level": 5,
            "runtime_minutes": 30,
            "source": "timer",
            "created_at_ms": 1000,
            "not_after_ms": 6000,
            "local_date": "2026-08-09",
            "start": "14:30",
        },
        "timer intent differs",
    )
    created = scheduler.snapshot()["occurrences"].get("phase5-smoke")
    _require(created is not None, "created occurrence is missing")
    _require(created["status"] == "intent_created", "intent status differs")
    _require(created["created_utc_seconds"] == 839601000, "intent UTC differs")
    _require(created["authorization_epoch"] == 1, "intent epoch differs")
    _require(created["intent_token"] == 1, "intent token differs")

    authorized = scheduler.authorize_intent(
        intent, 1001, control_available=True
    )
    _require(authorized is not None, "intent was not authorized")
    _require(
        authorized.occurrence_key == _EXPECTED_OCCURRENCE_KEY,
        "authorized occurrence differs",
    )
    authorized_snapshot = authorized.snapshot()
    _require(
        authorized_snapshot["authorization_token"] == 1,
        "authorization token differs",
    )
    _require(
        authorized_snapshot["authorization_epoch"] == 1,
        "authorization epoch differs",
    )
    pending = scheduler.snapshot()["occurrences"].get("phase5-smoke")
    _require(pending["status"] == "authorized_pending", "pending status differs")
    _require(pending["authorized_at_ms"] == 1001, "authorization time differs")
    _require(
        scheduler.complete_intent(
            authorized,
            True,
            _requested_snapshot(authorized),
            1001,
        )
        is True,
        "synthetic requested state was not confirmed",
    )

    accepted = scheduler.snapshot()
    record = accepted["occurrences"].get("phase5-smoke")
    _require(record is not None, "accepted occurrence is missing")
    _require(record["status"] == "accepted", "occurrence is not accepted")
    _require(record["completed_at_ms"] == 1001, "completion time differs")
    _require(
        record["completion_reason"] == "application_confirmed",
        "completion reason differs",
    )
    _require(
        accepted["active_occurrence_key"] == _EXPECTED_OCCURRENCE_KEY,
        "active timer association is missing",
    )
    _require(
        scheduler.step(2000, control_available=True) is None,
        "same local minute created a duplicate intent",
    )
    _require(
        scheduler.mark_manual_override(_EXPECTED_OCCURRENCE_KEY, 2001)
        is True,
        "manual override was not recorded",
    )
    _require(
        scheduler.mark_manual_override(_EXPECTED_OCCURRENCE_KEY, 2001)
        is False,
        "manual override was not idempotent",
    )

    final = scheduler.snapshot()
    _require(final["active_occurrence_key"] is None, "override stayed active")
    _require(final["last_override"] is not None, "override record is missing")
    _require(
        final["last_override"]["status"] == "overridden",
        "override record has the wrong status",
    )
    final_record = final["occurrences"].get("phase5-smoke")
    _require(final_record["status"] == "overridden", "final status differs")
    _require(final_record["overridden"] is True, "override flag differs")
    _require(final_record["overridden_at_ms"] == 2001, "override time differs")
    _require(not final["faulted"], "scheduler faulted during smoke test")
    _require(
        [event["code"] for event in clock.drain_events()]
        == ["clock_synchronized"],
        "clock event sequence differs",
    )
    _require(
        [event["code"] for event in scheduler.drain_events()]
        == [
            "scheduler_baseline_established",
            "timer_intent_created",
            "timer_intent_authorized",
            "timer_intent_accepted",
            "manual_timer_override",
        ],
        "scheduler event sequence differs",
    )


def _run_expiry_check(core):
    control_mode_power, Scheduler, clock_source_rtc, TimeService = core
    clock = TimeService(ticks_diff=_plain_ticks_diff)
    _require(
        clock.set_utc_datetime(
            2026, 8, 9, 14, 29, 59, clock_source_rtc, 0
        )
        is True,
        "expiry clock setup failed",
    )
    scheduler = Scheduler(
        clock,
        maximum_runtime_minutes=120,
        ticks_diff=_plain_ticks_diff,
        ticks_add=_plain_ticks_add,
        intent_valid_ms=100,
    )
    _require(
        scheduler.replace_timers((_timer_definition(control_mode_power),))
        is True,
        "expiry timer setup failed",
    )
    _require(scheduler.arm() is True, "expiry scheduler did not arm")
    _require(scheduler.step(0, True) is None, "expiry baseline failed")
    intent = scheduler.step(1000, True)
    _require(intent is not None, "expiry check created no intent")
    _require(intent.not_after_ms == 1100, "expiry deadline differs")
    _require(
        scheduler.authorize_intent(intent, 1101, True) is None,
        "expired intent was authorized",
    )
    expired = scheduler.snapshot()
    record = expired["occurrences"].get("phase5-smoke")
    _require(record is not None, "expired occurrence is missing")
    _require(record["status"] == "expired", "expiry was not recorded")
    _require(record["completed_at_ms"] == 1101, "expiry time differs")
    _require(
        record["completion_reason"] == "deadline_expired",
        "expiry reason differs",
    )
    _require(
        expired["active_occurrence_key"] is None,
        "expired intent became active",
    )
    _require(not expired["faulted"], "expiry check faulted scheduler")
    _require(
        scheduler.authorize_intent(intent, 1101, True) is None,
        "expired intent was retried",
    )
    _require(
        scheduler.step(2000, True) is None,
        "expired intent created a later retry",
    )
    _require(
        [event["code"] for event in scheduler.drain_events()]
        == [
            "scheduler_baseline_established",
            "timer_intent_created",
            "timer_intent_expired",
        ],
        "expiry event sequence differs",
    )


def run(confirmation, iterations=DEFAULT_ITERATIONS):
    """Run a bounded deterministic smoke test and return its result."""

    if (
        type(confirmation) is not str
        or confirmation != SOFTWARE_ONLY_CONFIRMATION
    ):
        raise RuntimeError(
            "software smoke not armed; disconnect every GPIO and use the "
            "exact USB-only confirmation"
        )
    if (
        not isinstance(iterations, int)
        or isinstance(iterations, bool)
        or iterations <= 0
        or iterations > MAX_ITERATIONS
    ):
        raise ValueError("iterations must be between 1 and 32")

    memory_before = _memory_free()
    core = _load_software_core()
    memory_after_import = _memory_free()
    platform_ticks_checked = _check_platform_ticks()
    # Stabilize one-time allocations before measuring repeated lifecycles.
    _run_iteration(core)
    _run_expiry_check(core)
    memory_after_warmup = _memory_free()
    passed = 0
    for _ in range(iterations):
        _run_iteration(core)
        passed += 1
    _run_expiry_check(core)
    memory_after = _memory_free()
    memory_checked = _check_memory(
        memory_before,
        memory_after_import,
        memory_after_warmup,
        memory_after,
    )
    if memory_checked:
        _require(
            platform_ticks_checked is True,
            "MicroPython wrap-safe tick primitives are unavailable",
        )

    result = {
        "phase": 5,
        "scope": "software_only",
        "iterations": iterations,
        "passed": passed,
        "timer_intents": passed,
        "manual_overrides": passed,
        "expired_intents": 1,
        "platform_ticks_checked": platform_ticks_checked,
        "memory_checked": memory_checked,
        "memory_free_before": memory_before,
        "memory_free_after_import": memory_after_import,
        "memory_free_after_warmup": memory_after_warmup,
        "memory_free_after": memory_after,
    }
    print(
        "PHASE 5 SOFTWARE-ONLY SMOKE PASS: {}/{} iterations; "
        "no hardware opened".format(passed, iterations)
    )
    if memory_checked:
        print(
            "MicroPython heap free: before={} after_import={} "
            "after_warmup={} after={} bytes".format(
                memory_before,
                memory_after_import,
                memory_after_warmup,
                memory_after,
            )
        )
    print(PHASE5_PASS_TOKEN)
    return result
