import inspect
import runpy
import unittest
from unittest import mock

from app.application_state import (
    CONTROL_MODE_CABIN_TEMPERATURE,
    CONTROL_MODE_POWER,
    CONTROL_MODE_ROOF_TENT_TEMPERATURE,
)
from app.scheduler import AuthorizedStartIntent, Scheduler, StartIntent
from services.time_service import (
    CLOCK_SOURCE_RTC,
    EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
    EUROPE_ZURICH_TIMEZONE_NAME,
    TIMEZONE_RULE_EUROPE_ZURICH,
    TimeService,
)


def timer_definition(
    timer_id="weekday",
    start="14:30",
    weekdays=None,
    mode=CONTROL_MODE_POWER,
    target_temperature=None,
    power_level=5,
    runtime_minutes=30,
    enabled=True,
):
    if weekdays is None:
        weekdays = [6]
    return {
        "id": timer_id,
        "name": "Test timer",
        "enabled": enabled,
        "weekdays": weekdays,
        "start": start,
        "mode": mode,
        "target_temperature": target_temperature,
        "power_level": power_level,
        "runtime_minutes": runtime_minutes,
    }


def clock_at(hour=14, minute=29, second=59, now_ms=0):
    clock = TimeService()
    clock.set_utc_datetime(
        2026, 8, 9, hour, minute, second, CLOCK_SOURCE_RTC, now_ms
    )
    return clock


def zurich_clock_at(
    year,
    month,
    day,
    hour,
    minute,
    second=0,
    now_ms=0,
):
    clock = TimeService(
        timezone_name=EUROPE_ZURICH_TIMEZONE_NAME,
        utc_offset_minutes=EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
        timezone_rule=TIMEZONE_RULE_EUROPE_ZURICH,
    )
    clock.set_utc_datetime(
        year, month, day, hour, minute, second, CLOCK_SOURCE_RTC, now_ms
    )
    return clock


def armed_scheduler(clock=None, definitions=None, **kwargs):
    if clock is None:
        clock = clock_at()
    scheduler = Scheduler(clock, maximum_runtime_minutes=120, **kwargs)
    if definitions is None:
        definitions = [timer_definition()]
    scheduler.replace_timers(definitions)
    scheduler.arm()
    return scheduler, clock


def requested_snapshot_for(authorized):
    return {
        "on": True,
        "mode": authorized.mode,
        "target_temperature": authorized.target_temperature,
        "power_level": authorized.power_level,
        "runtime_minutes": authorized.runtime_minutes,
        "source": authorized.source,
    }


def stopped_requested_snapshot():
    return {"on": False}


def accept_intent(scheduler, intent, now_ms, control_available=True):
    authorized = scheduler.authorize_intent(
        intent, now_ms, control_available
    )
    if authorized is None:
        return False
    return scheduler.complete_intent(
        authorized,
        True,
        requested_snapshot_for(authorized),
        now_ms,
    )


class FakeClock:
    def __init__(self, snapshot):
        self.value = snapshot
        self.calls = 0

    def snapshot(self, now_ms):
        self.calls += 1
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


class ModularClock:
    def __init__(self, period):
        self.period = period
        self.half = period // 2

    def diff(self, newer, older):
        return ((newer - older + self.half) % self.period) - self.half

    def add(self, ticks, delta):
        return (ticks + delta) % self.period


class EqualToEverything:
    def __eq__(self, other):
        return True


class TestSchedulerConfiguration(unittest.TestCase):
    def test_constructor_performs_no_clock_io_and_validates_bounds(self):
        clock = FakeClock({})
        scheduler = Scheduler(clock, 120)
        self.assertEqual(clock.calls, 0)
        self.assertFalse(scheduler.armed)
        for kwargs in (
            {"maximum_runtime_minutes": 0},
            {"maximum_runtime_minutes": True},
            {"max_timers": 0},
            {"max_timers": 33},
            {"event_capacity": 0},
            {"event_capacity": 65},
            {"intent_valid_ms": 0},
            {"intent_valid_ms": 60001},
            {"max_clock_step_seconds": 0},
            {"ticks_diff": "bad", "ticks_add": "bad"},
            {"ticks_diff": lambda a, b: a - b},
        ):
            with self.subTest(kwargs=kwargs):
                maximum = kwargs.pop("maximum_runtime_minutes", 120)
                with self.assertRaises(ValueError):
                    Scheduler(clock, maximum, **kwargs)

    def test_timer_definitions_are_validated_atomically(self):
        scheduler = Scheduler(clock_at(), 120, max_timers=2)
        scheduler.replace_timers([timer_definition()])
        before = scheduler.snapshot()["timers"]
        invalid = (
            [timer_definition(start="6:30")],
            [timer_definition(start="24:00")],
            [timer_definition(weekdays=[])],
            [timer_definition(weekdays=[0, 0])],
            [timer_definition(weekdays=[7])],
            [timer_definition(runtime_minutes=121)],
            [timer_definition(power_level=True)],
            [timer_definition(timer_id="reserved|id")],
            [dict(timer_definition(), typo=True)],
        )
        for definitions in invalid:
            with self.subTest(definitions=definitions):
                with self.assertRaises(ValueError):
                    scheduler.replace_timers(definitions)
                self.assertEqual(scheduler.snapshot()["timers"], before)

        with self.assertRaises(ValueError):
            scheduler.replace_timers(
                [timer_definition("same"), timer_definition("same")]
            )
        with self.assertRaises(ValueError):
            scheduler.replace_timers(
                [timer_definition("a"), timer_definition("b"), timer_definition("c")]
            )

    def test_all_three_application_modes_are_preserved(self):
        definitions = (
            timer_definition("power"),
            timer_definition(
                "roof",
                mode=CONTROL_MODE_ROOF_TENT_TEMPERATURE,
                target_temperature=20,
                power_level=None,
            ),
            timer_definition(
                "cabin",
                mode=CONTROL_MODE_CABIN_TEMPERATURE,
                target_temperature=18,
                power_level=None,
            ),
        )
        scheduler = Scheduler(clock_at(), 120)
        scheduler.replace_timers(definitions)
        modes = [item["mode"] for item in scheduler.snapshot()["timers"]]
        self.assertEqual(
            modes,
            [
                CONTROL_MODE_POWER,
                CONTROL_MODE_ROOF_TENT_TEMPERATURE,
                CONTROL_MODE_CABIN_TEMPERATURE,
            ],
        )

    def test_snapshots_are_defensively_copied(self):
        scheduler = Scheduler(clock_at(), 120)
        definition = timer_definition()
        scheduler.replace_timers([definition])
        definition["weekdays"][0] = 0
        first = scheduler.snapshot()
        first["timers"][0]["weekdays"][0] = 1
        self.assertEqual(scheduler.snapshot()["timers"][0]["weekdays"], [6])


class TestSchedulerOccurrenceLogic(unittest.TestCase):
    def test_disarmed_and_first_valid_observation_never_trigger(self):
        clock = clock_at(hour=14, minute=30, second=0)
        scheduler = Scheduler(clock, 120)
        scheduler.replace_timers([timer_definition()])
        self.assertIsNone(scheduler.step(0, True))
        scheduler.arm()
        self.assertIsNone(scheduler.step(0, True))

    def test_natural_minute_edge_returns_one_short_lived_intent(self):
        scheduler, _ = armed_scheduler(intent_valid_ms=4000)
        self.assertIsNone(scheduler.step(0, True))
        intent = scheduler.step(1000, True)
        self.assertIsInstance(intent, StartIntent)
        self.assertEqual(intent.timer_id, "weekday")
        self.assertEqual(intent.source, "timer")
        self.assertEqual(intent.power_level, 5)
        self.assertEqual(intent.runtime_minutes, 30)
        self.assertEqual(intent.created_at_ms, 1000)
        self.assertEqual(intent.not_after_ms, 5000)
        self.assertIsNone(scheduler.step(2000, True))
        self.assertIsNone(scheduler.step(3000, True))

    def test_unavailable_control_consumes_occurrence_without_late_start(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        self.assertIsNone(scheduler.step(1000, False))
        self.assertIsNone(scheduler.step(2000, True))
        record = scheduler.snapshot()["occurrences"]["weekday"]
        self.assertEqual(record["status"], "suppressed_busy")

    def test_active_occurrence_suppresses_the_next_timer_internally(self):
        definitions = (
            timer_definition(timer_id="first", start="14:30"),
            timer_definition(timer_id="second", start="14:31"),
        )
        scheduler, _ = armed_scheduler(definitions=definitions)
        scheduler.step(0, True)
        first = scheduler.step(1000, True)
        self.assertTrue(accept_intent(scheduler, first, 1001))

        self.assertIsNone(scheduler.step(61000, True))
        snapshot = scheduler.snapshot()
        self.assertEqual(
            snapshot["active_occurrence_key"], first.occurrence_key
        )
        self.assertEqual(
            snapshot["occurrences"]["second"]["status"],
            "suppressed_busy",
        )

    def test_reentrant_double_completion_preserves_first_association(self):
        definitions = (
            timer_definition(timer_id="first", start="14:30"),
            timer_definition(timer_id="second", start="14:31"),
        )
        scheduler, _ = armed_scheduler(
            definitions=definitions, intent_valid_ms=60000
        )
        scheduler.step(0, True)
        first = scheduler.step(1000, True)
        second = scheduler.step(61000, True)
        first_authorized = scheduler.authorize_intent(
            first, 61000, True
        )
        second_authorized = scheduler.authorize_intent(
            second, 61000, True
        )
        self.assertIsNotNone(first_authorized)
        self.assertIsNotNone(second_authorized)
        self.assertTrue(
            scheduler.complete_intent(
                first_authorized,
                True,
                requested_snapshot_for(first_authorized),
                61000,
            )
        )
        self.assertFalse(
            scheduler.complete_intent(
                second_authorized,
                True,
                requested_snapshot_for(second_authorized),
                61000,
            )
        )
        snapshot = scheduler.snapshot()
        self.assertEqual(
            snapshot["active_occurrence_key"], first.occurrence_key
        )
        self.assertEqual(
            snapshot["occurrences"]["second"]["status"],
            "application_failed",
        )
        self.assertTrue(snapshot["faulted"])

    def test_two_simultaneous_timers_fail_closed_as_conflict(self):
        scheduler, _ = armed_scheduler(
            definitions=[timer_definition("a"), timer_definition("b")]
        )
        scheduler.step(0, True)
        self.assertIsNone(scheduler.step(1000, True))
        records = scheduler.snapshot()["occurrences"]
        self.assertEqual(records["a"]["status"], "conflict")
        self.assertEqual(records["b"]["status"], "conflict")

    def test_clock_correction_fences_current_occurrence(self):
        scheduler, clock = armed_scheduler()
        scheduler.step(0, True)
        clock.set_utc_datetime(
            2026, 8, 9, 14, 30, 0, CLOCK_SOURCE_RTC, 500
        )
        self.assertIsNone(scheduler.step(500, True))
        self.assertIsNone(scheduler.step(1000, True))
        self.assertEqual(scheduler.snapshot()["occurrences"], {})

    def test_large_forward_step_and_backward_wall_time_do_not_catch_up(self):
        scheduler, clock = armed_scheduler(max_clock_step_seconds=90)
        scheduler.step(0, True)
        self.assertIsNone(scheduler.step(120000, True))
        self.assertEqual(scheduler.snapshot()["occurrences"], {})

        clock.set_utc_datetime(
            2026, 8, 9, 14, 20, 0, CLOCK_SOURCE_RTC, 120001
        )
        self.assertIsNone(scheduler.step(120001, True))

    def test_rollback_before_latest_consumed_date_never_refires_history(self):
        scheduler, clock = armed_scheduler(max_clock_step_seconds=700000)
        scheduler.step(0, True)
        first = scheduler.step(1000, True)
        self.assertIsNotNone(first)

        clock.set_utc_datetime(
            2026, 8, 16, 14, 29, 59, CLOCK_SOURCE_RTC, 2000
        )
        scheduler.step(2000, True)
        second = scheduler.step(3000, True)
        self.assertIsNotNone(second)

        clock.set_utc_datetime(
            2026, 8, 9, 14, 29, 59, CLOCK_SOURCE_RTC, 4000
        )
        scheduler.step(4000, True)
        self.assertIsNone(scheduler.step(5000, True))

    def test_invalid_clock_and_rtc_holdover_do_not_authorize_timer(self):
        clock = TimeService()
        scheduler, _ = armed_scheduler(clock=clock)
        self.assertIsNone(scheduler.step(0, True))
        clock.set_utc_datetime(
            2026, 8, 9, 14, 29, 59, CLOCK_SOURCE_RTC, 1
        )
        self.assertIsNone(scheduler.step(1, True))
        clock.report_rtc_error(500, "fault")
        self.assertIsNone(scheduler.step(1001, True))
        self.assertEqual(scheduler.snapshot()["occurrences"], {})

    def test_in_progress_rtc_commit_is_not_timer_trusted(self):
        clock = TimeService()
        clock.set_utc_datetime(
            2026, 8, 9, 14, 29, 59, "ntp", 0
        )
        revision = clock.snapshot(0)["rtc_write_revision"]
        self.assertTrue(clock.begin_rtc_commit(revision, 0))
        self.assertTrue(clock.mark_rtc_write_result(True, revision, 0))
        scheduler, _ = armed_scheduler(clock=clock)
        self.assertIsNone(scheduler.step(0, True))
        self.assertIsNone(scheduler.step(1000, True))
        self.assertEqual(scheduler.snapshot()["occurrences"], {})
        self.assertTrue(clock.mark_rtc_commit_recovered(revision, 1000))
        self.assertTrue(clock.end_rtc_commit(revision))

    def test_timer_change_erects_configuration_fence(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        changed = timer_definition()
        changed["name"] = "Changed"
        scheduler.replace_timers([changed])
        self.assertIsNone(scheduler.step(1000, True))
        self.assertEqual(scheduler.snapshot()["occurrences"], {})

    def test_disabled_and_wrong_weekday_timers_do_not_trigger(self):
        for definition in (
            timer_definition(enabled=False),
            timer_definition(weekdays=[0]),
        ):
            with self.subTest(definition=definition):
                scheduler, _ = armed_scheduler(definitions=[definition])
                scheduler.step(0, True)
                self.assertIsNone(scheduler.step(1000, True))

    def test_future_weekly_occurrence_remains_valid_after_override(self):
        scheduler, clock = armed_scheduler(max_clock_step_seconds=700000)
        scheduler.step(0, True)
        first = scheduler.step(1000, True)
        self.assertTrue(accept_intent(scheduler, first, 1001))
        self.assertTrue(
            scheduler.mark_manual_override(first.occurrence_key, 1002)
        )
        self.assertFalse(
            scheduler.mark_manual_override(first.occurrence_key, 1003)
        )
        # Move by one week through an explicit correction: the correction is
        # fenced, then the next natural Sunday edge is independently valid.
        clock.set_utc_datetime(
            2026, 8, 16, 14, 29, 59, CLOCK_SOURCE_RTC, 2000
        )
        self.assertIsNone(scheduler.step(2000, True))
        second = scheduler.step(3000, True)
        self.assertIsNotNone(second)
        self.assertNotEqual(second.occurrence_key, first.occurrence_key)

    def test_intent_result_is_explicit_and_never_retries(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        authorized = scheduler.authorize_intent(intent, 1001, True)
        self.assertIsInstance(authorized, AuthorizedStartIntent)
        self.assertFalse(
            scheduler.complete_intent(
                authorized, False, stopped_requested_snapshot(), 1001
            )
        )
        self.assertIsNone(scheduler.step(2000, True))
        self.assertEqual(
            scheduler.snapshot()["occurrences"]["weekday"]["status"],
            "application_failed",
        )

    def test_next_occurrence_is_calculated_without_consuming_it(self):
        scheduler, _ = armed_scheduler()
        upcoming = scheduler.next_occurrence(0)
        self.assertEqual(upcoming["timer_id"], "weekday")
        self.assertEqual(upcoming["minutes_from_now"], 1)
        self.assertEqual(upcoming["local_date"], "2026-08-09")
        self.assertEqual(scheduler.snapshot()["occurrences"], {})

    def test_zurich_gap_and_transition_fence_are_skipped_by_next_occurrence(self):
        clock = zurich_clock_at(2026, 3, 29, 0, 59)
        gap_scheduler, _ = armed_scheduler(
            clock=clock,
            definitions=[timer_definition(start="02:30")],
        )
        gap = gap_scheduler.next_occurrence(0)
        self.assertEqual(gap["local_date"], "2026-04-05")
        self.assertEqual(gap["minutes_from_now"], 10051)

        transition_scheduler, _ = armed_scheduler(
            clock=clock,
            definitions=[timer_definition(start="03:00")],
        )
        transition = transition_scheduler.next_occurrence(0)
        self.assertEqual(transition["local_date"], "2026-04-05")
        self.assertEqual(transition["minutes_from_now"], 10081)

    def test_zurich_fold_next_occurrence_selects_only_the_first_hour(self):
        first_clock = zurich_clock_at(2026, 10, 25, 0, 0)
        first_scheduler, _ = armed_scheduler(
            clock=first_clock,
            definitions=[timer_definition(start="02:30")],
        )
        first = first_scheduler.next_occurrence(0)
        self.assertEqual(first["local_date"], "2026-10-25")
        self.assertEqual(first["minutes_from_now"], 30)

        repeated_clock = zurich_clock_at(2026, 10, 25, 1, 0)
        repeated_scheduler, _ = armed_scheduler(
            clock=repeated_clock,
            definitions=[timer_definition(start="02:30")],
        )
        repeated = repeated_scheduler.next_occurrence(0)
        self.assertEqual(repeated["local_date"], "2026-11-01")
        self.assertEqual(repeated["minutes_from_now"], 10110)

    def test_zurich_fold_second_hour_never_starts_even_after_reboot(self):
        clock = zurich_clock_at(2026, 10, 25, 1, 29)
        scheduler, _ = armed_scheduler(
            clock=clock,
            definitions=[timer_definition(start="02:30")],
        )
        self.assertIsNone(scheduler.step(0, True))
        self.assertIsNone(scheduler.step(60000, True))
        self.assertEqual(scheduler.snapshot()["occurrences"], {})

    def test_zurich_fold_timer_runs_once_first_fold_and_not_again(self):
        clock = zurich_clock_at(2026, 10, 25, 0, 29)
        scheduler, _ = armed_scheduler(
            clock=clock,
            definitions=[timer_definition(start="02:30")],
        )
        self.assertIsNone(scheduler.step(0, True))
        intents = [scheduler.step(60000, True)]
        for minute_index in range(2, 62):
            candidate = scheduler.step(minute_index * 60000, True)
            if candidate is not None:
                intents.append(candidate)
        self.assertEqual(len(intents), 1)
        self.assertIsInstance(intents[0], StartIntent)
        record = scheduler.snapshot()["occurrences"]["weekday"]
        self.assertEqual(record["local_fold"], 0)

    def test_zurich_fold_returns_to_normal_start_policy_at_three(self):
        clock = zurich_clock_at(2026, 10, 25, 1, 59)
        scheduler, _ = armed_scheduler(
            clock=clock,
            definitions=[timer_definition(start="03:00")],
        )
        self.assertIsNone(scheduler.step(0, True))
        intent = scheduler.step(60000, True)
        self.assertIsInstance(intent, StartIntent)

    def test_zurich_transition_minute_is_fenced_then_next_minute_can_start(self):
        clock = zurich_clock_at(2026, 3, 29, 0, 59)
        scheduler, _ = armed_scheduler(
            clock=clock,
            definitions=[timer_definition(start="03:01")],
        )
        self.assertIsNone(scheduler.step(0, True))
        self.assertIsNone(scheduler.step(60000, True))
        intent = scheduler.step(120000, True)
        self.assertIsInstance(intent, StartIntent)

    def test_zurich_timer_at_spring_transition_minute_is_not_started(self):
        clock = zurich_clock_at(2026, 3, 29, 0, 59)
        scheduler, _ = armed_scheduler(
            clock=clock,
            definitions=[timer_definition(start="03:00")],
        )
        self.assertIsNone(scheduler.step(0, True))
        self.assertIsNone(scheduler.step(60000, True))
        self.assertIsNone(scheduler.step(120000, True))
        self.assertEqual(scheduler.snapshot()["occurrences"], {})

    def test_zurich_pre_transition_intent_cannot_authorize_after_offset_change(self):
        clock = zurich_clock_at(2026, 3, 29, 0, 58)
        scheduler, _ = armed_scheduler(
            clock=clock,
            definitions=[timer_definition(start="01:59")],
            intent_valid_ms=60000,
        )
        self.assertIsNone(scheduler.step(0, True))
        intent = scheduler.step(60000, True)
        self.assertIsInstance(intent, StartIntent)
        self.assertIsNone(scheduler.authorize_intent(intent, 120000, True))
        self.assertEqual(
            scheduler.snapshot()["occurrences"]["weekday"]["status"],
            "authorization_rejected",
        )

    def test_forged_zurich_offset_fold_and_rule_are_rejected(self):
        real = zurich_clock_at(2026, 7, 1, 12, 0).snapshot(0)
        mutations = (
            ("top", "utc_offset_minutes", 60),
            ("top", "is_dst", False),
            ("top", "timezone_rule_version", 2),
            ("top", "timezone", "Europe/Zurich-copy"),
            ("local", "fold", 1),
            ("local", "local_minute_id", real["local"]["local_minute_id"] + 1),
        )
        for scope, name, value in mutations:
            with self.subTest(scope=scope, name=name, value=value):
                forged = dict(real)
                forged["local"] = dict(real["local"])
                if scope == "top":
                    forged[name] = value
                else:
                    forged["local"][name] = value
                scheduler = Scheduler(FakeClock(forged), 120)
                scheduler.replace_timers([timer_definition()])
                scheduler.arm()
                self.assertIsNone(scheduler.next_occurrence(0))

    def test_canonical_zurich_name_with_fixed_rule_is_rejected(self):
        fixed = TimeService(
            timezone_name="Europe/Zurich-fixed",
            utc_offset_minutes=60,
        )
        fixed.set_utc_datetime(
            2026, 7, 1, 12, 0, 0, CLOCK_SOURCE_RTC, 0
        )
        forged = fixed.snapshot(0)
        forged["timezone"] = EUROPE_ZURICH_TIMEZONE_NAME
        scheduler = Scheduler(FakeClock(forged), 120)
        scheduler.replace_timers([timer_definition()])
        scheduler.arm()
        self.assertIsNone(scheduler.next_occurrence(0))

    def test_backward_monotonic_step_faults_until_explicit_reset(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(10, True)
        with self.assertRaises(ValueError):
            scheduler.step(9, True)
        self.assertTrue(scheduler.faulted)
        self.assertFalse(scheduler.arm())
        self.assertTrue(scheduler.reset_fault())

    def test_clock_port_errors_fail_closed(self):
        clock = FakeClock(RuntimeError("broken"))
        scheduler = Scheduler(clock, 120)
        scheduler.replace_timers([timer_definition()])
        scheduler.arm()
        self.assertIsNone(scheduler.step(0, True))
        self.assertEqual(scheduler.snapshot()["occurrences"], {})

    def test_forged_source_and_offset_are_rejected(self):
        real = clock_at().snapshot(0)
        for mutation in (
            {"source": "forged"},
            {"utc_offset_minutes": 100000},
        ):
            forged = dict(real)
            forged.update(mutation)
            scheduler = Scheduler(FakeClock(forged), 120)
            scheduler.replace_timers([timer_definition()])
            scheduler.arm()
            self.assertIsNone(scheduler.step(0, True))
            self.assertEqual(scheduler.snapshot()["occurrences"], {})

    def test_unrevisioned_but_coherent_offset_change_is_fenced(self):
        base = clock_at().snapshot(0)
        port = FakeClock(base)
        scheduler = Scheduler(port, 120)
        scheduler.replace_timers([timer_definition()])
        scheduler.arm()
        scheduler.step(0, True)

        changed_clock = TimeService(utc_offset_minutes=-1)
        changed_clock.set_utc_datetime(
            2026, 8, 9, 14, 31, 0, CLOCK_SOURCE_RTC, 0
        )
        changed = changed_clock.snapshot(0)
        self.assertEqual(changed["clock_revision"], base["clock_revision"])
        self.assertEqual(
            changed["timezone_revision"], base["timezone_revision"]
        )
        self.assertEqual(changed["local"]["hour"], 14)
        self.assertEqual(changed["local"]["minute"], 30)
        port.value = changed
        self.assertIsNone(scheduler.step(1000, True))
        self.assertEqual(scheduler.snapshot()["occurrences"], {})

    def test_intent_ack_is_one_way_and_expires(self):
        scheduler, _ = armed_scheduler(intent_valid_ms=100)
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        authorized = scheduler.authorize_intent(intent, 1050, True)
        self.assertIsInstance(authorized, AuthorizedStartIntent)
        self.assertTrue(
            scheduler.complete_intent(
                authorized,
                True,
                requested_snapshot_for(authorized),
                1050,
            )
        )
        self.assertFalse(
            scheduler.complete_intent(
                authorized, False, stopped_requested_snapshot(), 1060
            )
        )
        self.assertEqual(
            scheduler.snapshot()["occurrences"]["weekday"]["status"],
            "accepted",
        )

        late_scheduler, _ = armed_scheduler(intent_valid_ms=100)
        late_scheduler.step(0, True)
        late = late_scheduler.step(1000, True)
        self.assertFalse(
            late_scheduler.authorize_intent(late, 1101, True)
        )
        self.assertEqual(
            late_scheduler.snapshot()["occurrences"]["weekday"]["status"],
            "expired",
        )

    def test_backdated_ack_after_scheduler_progress_is_rejected(self):
        scheduler, _ = armed_scheduler(intent_valid_ms=5000)
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        scheduler.step(2000, True)
        self.assertFalse(
            scheduler.authorize_intent(intent, 1050, True)
        )
        self.assertEqual(
            scheduler.snapshot()["occurrences"]["weekday"]["status"],
            "intent_created",
        )

    def test_authorization_rechecks_timer_scheduler_clock_and_control(self):
        def new_pending():
            item, item_clock = armed_scheduler(intent_valid_ms=5000)
            item.step(0, True)
            return item, item_clock, item.step(1000, True)

        scheduler, _, intent = new_pending()
        edited = timer_definition()
        edited["power_level"] = 9
        scheduler.replace_timers([edited])
        self.assertFalse(
            scheduler.authorize_intent(intent, 1001, True)
        )

        scheduler, _, intent = new_pending()
        disabled = timer_definition(enabled=False)
        scheduler.replace_timers([disabled])
        self.assertFalse(
            scheduler.authorize_intent(intent, 1001, True)
        )

        scheduler, _, intent = new_pending()
        scheduler.replace_timers([])
        self.assertFalse(
            scheduler.authorize_intent(intent, 1001, True)
        )

        scheduler, _, intent = new_pending()
        scheduler.disarm()
        self.assertFalse(
            scheduler.authorize_intent(intent, 1001, True)
        )

        scheduler, item_clock, intent = new_pending()
        item_clock.invalidate(1001, "lost clock")
        self.assertFalse(
            scheduler.authorize_intent(intent, 1001, True)
        )

        scheduler, _, intent = new_pending()
        self.assertFalse(
            scheduler.authorize_intent(intent, 1001, False)
        )

    def test_two_phase_controller_failure_never_creates_phantom_active(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        authorized = scheduler.authorize_intent(intent, 1001, True)
        self.assertIsInstance(authorized, AuthorizedStartIntent)

        # A RuntimeError or a False return from the future gateway is
        # represented by applied=False.  It must never create active truth.
        self.assertFalse(
            scheduler.complete_intent(
                authorized, False, stopped_requested_snapshot(), 1001
            )
        )
        status = scheduler.snapshot()
        self.assertEqual(
            status["occurrences"]["weekday"]["status"],
            "application_failed",
        )
        self.assertIsNone(status["active_occurrence_key"])
        self.assertFalse(
            scheduler.complete_intent(
                authorized,
                True,
                requested_snapshot_for(authorized),
                1002,
            )
        )

    def test_failed_result_after_requested_mutation_keeps_active_truth(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        authorized = scheduler.authorize_intent(intent, 1001, True)

        # The controller may have committed Requested=ON before a later
        # diagnostic/allocation error made its call report failure.
        self.assertTrue(
            scheduler.complete_intent(
                authorized,
                False,
                requested_snapshot_for(authorized),
                1001,
            )
        )
        status = scheduler.snapshot()
        self.assertTrue(status["faulted"])
        self.assertEqual(
            status["active_occurrence_key"], intent.occurrence_key
        )
        self.assertEqual(
            status["occurrences"]["weekday"]["completion_reason"],
            "application_confirmed_despite_failed_result",
        )
        self.assertTrue(
            scheduler.mark_manual_override(intent.occurrence_key, 1002)
        )

    def test_unknown_application_state_after_failure_latches_fault(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        authorized = scheduler.authorize_intent(intent, 1001, True)
        self.assertFalse(
            scheduler.complete_intent(authorized, False, None, 1001)
        )
        status = scheduler.snapshot()
        self.assertTrue(status["faulted"])
        self.assertEqual(
            status["occurrences"]["weekday"]["completion_reason"],
            "application_state_unknown",
        )

    def test_claimed_application_success_requires_exact_requested_state(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        authorized = scheduler.authorize_intent(intent, 1001, True)
        forged = requested_snapshot_for(authorized)
        forged["power_level"] = 9

        self.assertFalse(
            scheduler.complete_intent(authorized, True, forged, 1001)
        )
        status = scheduler.snapshot()
        self.assertTrue(status["faulted"])
        self.assertIsNone(status["active_occurrence_key"])
        self.assertEqual(
            status["occurrences"]["weekday"]["completion_reason"],
            "application_state_mismatch",
        )

    def test_requested_snapshot_rejects_bool_spoofs_and_missing_fields(self):
        for mutate in (
            lambda item: item.update(
                {"power_level": True, "runtime_minutes": True}
            ),
            lambda item: item.update(
                {"mode": EqualToEverything(), "source": EqualToEverything()}
            ),
            lambda item: (
                item.pop("target_temperature"),
                item.update({"extra": None}),
            ),
        ):
            with self.subTest(mutate=mutate):
                scheduler, _ = armed_scheduler(
                    definitions=[
                        timer_definition(power_level=1, runtime_minutes=1)
                    ]
                )
                scheduler.step(0, True)
                intent = scheduler.step(1000, True)
                authorized = scheduler.authorize_intent(
                    intent, 1001, True
                )
                forged = requested_snapshot_for(authorized)
                mutate(forged)
                self.assertFalse(
                    scheduler.complete_intent(
                        authorized, True, forged, 1001
                    )
                )
                self.assertTrue(scheduler.faulted)
                self.assertIsNone(
                    scheduler.snapshot()["active_occurrence_key"]
                )

    def test_intents_are_read_only_and_foreign_intents_are_rejected(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        with self.assertRaises(AttributeError):
            intent._power_level = 9

        other, _ = armed_scheduler()
        other.step(0, True)
        foreign = other.step(1000, True)
        self.assertIsNone(
            scheduler.authorize_intent(foreign, 1001, True)
        )

        # Use a fresh scheduler because a rejected authorization is terminal.
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        authorized = scheduler.authorize_intent(intent, 1001, True)
        with self.assertRaises(AttributeError):
            authorized._intent = foreign
        self.assertEqual(authorized.power_level, 5)

    def test_disarm_rearm_invalidates_previously_created_intent(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        scheduler.disarm()
        scheduler.arm()
        self.assertIsNone(scheduler.step(1001, True))
        self.assertIsNone(
            scheduler.authorize_intent(intent, 1002, True)
        )

    def test_clock_loss_and_recovery_never_revive_old_intent(self):
        scheduler, clock = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        with mock.patch.object(clock, "snapshot", side_effect=OSError("rtc")):
            self.assertIsNone(scheduler.step(1001, True))
        self.assertIsNone(scheduler.step(1002, True))
        self.assertIsNone(
            scheduler.authorize_intent(intent, 1003, True)
        )

    def test_fault_reset_never_revives_old_intent(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        with self.assertRaises(ValueError):
            scheduler.step(999, True)
        self.assertTrue(scheduler.reset_fault())
        self.assertIsNone(scheduler.step(1001, True))
        self.assertIsNone(
            scheduler.authorize_intent(intent, 1002, True)
        )

    def test_epoch_allocation_failure_cannot_prevent_or_reset_fault_latch(self):
        scheduler, clock = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        with mock.patch.object(
            clock, "snapshot", side_effect=MemoryError("clock OOM")
        ), mock.patch.object(
            scheduler,
            "_advance_authorization_epoch",
            side_effect=MemoryError("epoch OOM"),
        ):
            with self.assertRaises(MemoryError):
                scheduler.authorize_intent(intent, 1001, True)
        self.assertTrue(scheduler.faulted)
        self.assertIsNone(
            scheduler.authorize_intent(intent, 1002, True)
        )

        with mock.patch.object(
            scheduler,
            "_advance_authorization_epoch",
            side_effect=MemoryError("reset OOM"),
        ):
            with self.assertRaises(MemoryError):
                scheduler.reset_fault()
        self.assertTrue(scheduler.faulted)

    def test_authorize_and_complete_deadlines_are_wrap_safe(self):
        modular = ModularClock(8192)
        clock = TimeService(ticks_diff=modular.diff)
        clock.set_utc_datetime(
            2026, 8, 9, 14, 29, 59, CLOCK_SOURCE_RTC, 7000
        )
        scheduler, _ = armed_scheduler(
            clock=clock,
            ticks_diff=modular.diff,
            ticks_add=modular.add,
            intent_valid_ms=200,
        )
        scheduler.step(7000, True)
        intent = scheduler.step(8000, True)
        self.assertEqual(intent.not_after_ms, 8)
        authorized = scheduler.authorize_intent(intent, 8191, True)
        self.assertIsInstance(authorized, AuthorizedStartIntent)
        self.assertTrue(
            scheduler.complete_intent(
                authorized,
                True,
                requested_snapshot_for(authorized),
                0,
            )
        )

        late_clock = TimeService(ticks_diff=modular.diff)
        late_clock.set_utc_datetime(
            2026, 8, 9, 14, 29, 59, CLOCK_SOURCE_RTC, 7000
        )
        late_scheduler, _ = armed_scheduler(
            clock=late_clock,
            ticks_diff=modular.diff,
            ticks_add=modular.add,
            intent_valid_ms=200,
        )
        late_scheduler.step(7000, True)
        late = late_scheduler.step(8000, True)
        self.assertIsNone(
            late_scheduler.authorize_intent(late, 9, True)
        )

    def test_completion_preserves_association_after_applied_side_effect(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        authorized = scheduler.authorize_intent(intent, 1001, True)

        # A gateway must not yield between authorize and application.  If a
        # synchronous application callback nevertheless changes scheduler
        # configuration before returning, completion still has to retain the
        # association for a later manual stop because the requested-state
        # side effect has already happened.
        scheduler.replace_timers([])
        self.assertTrue(
            scheduler.complete_intent(
                authorized,
                True,
                requested_snapshot_for(authorized),
                1001,
            )
        )
        self.assertEqual(
            scheduler.snapshot()["active_occurrence_key"],
            intent.occurrence_key,
        )
        self.assertTrue(
            scheduler.mark_manual_override(intent.occurrence_key, 1002)
        )

    def test_same_id_edit_cannot_orphan_active_manual_override(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        first = scheduler.step(1000, True)
        self.assertTrue(accept_intent(scheduler, first, 1001))
        edited = timer_definition(start="14:31")
        scheduler.replace_timers([edited])
        scheduler.step(2000, False)
        self.assertIsNone(scheduler.step(62000, False))
        self.assertTrue(
            scheduler.mark_manual_override(first.occurrence_key, 62001)
        )
        self.assertEqual(
            scheduler.snapshot()["last_override"]["occurrence_key"],
            first.occurrence_key,
        )

    def test_manual_override_rejects_backdated_event_time(self):
        scheduler, _ = armed_scheduler()
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        self.assertTrue(accept_intent(scheduler, intent, 1002))
        self.assertFalse(
            scheduler.mark_manual_override(intent.occurrence_key, 900)
        )
        self.assertEqual(
            scheduler.snapshot()["active_occurrence_key"],
            intent.occurrence_key,
        )
        self.assertTrue(
            scheduler.mark_manual_override(intent.occurrence_key, 1003)
        )

    def test_delete_readd_and_rollback_cannot_refire_consumed_history(self):
        scheduler, clock = armed_scheduler(max_clock_step_seconds=700000)
        scheduler.step(0, True)
        first = scheduler.step(1000, True)
        self.assertIsNotNone(first)
        scheduler.replace_timers([])
        scheduler.replace_timers([timer_definition()])
        clock.set_utc_datetime(
            2026, 8, 9, 14, 29, 59, CLOCK_SOURCE_RTC, 2000
        )
        scheduler.step(2000, True)
        self.assertIsNone(scheduler.step(3000, True))

    def test_next_occurrence_at_open_minute_reports_next_week(self):
        clock = clock_at(hour=14, minute=30, second=0)
        scheduler, _ = armed_scheduler(clock=clock)
        scheduler.step(0, True)
        upcoming = scheduler.next_occurrence(0)
        self.assertEqual(upcoming["minutes_from_now"], 7 * 1440)

    def test_next_occurrence_contains_clock_port_failure(self):
        scheduler = Scheduler(FakeClock(OSError("rtc")), 120)
        scheduler.replace_timers([timer_definition()])
        self.assertIsNone(scheduler.next_occurrence(0))

    def test_events_are_bounded_and_do_not_affect_occurrence_truth(self):
        scheduler, _ = armed_scheduler(event_capacity=1)
        scheduler.step(0, True)
        intent = scheduler.step(1000, True)
        self.assertTrue(accept_intent(scheduler, intent, 1001))
        status = scheduler.snapshot()
        self.assertGreater(status["events_dropped"], 0)
        self.assertEqual(
            status["occurrences"]["weekday"]["status"], "accepted"
        )

    def test_module_imports_no_hardware_protocol_or_controller(self):
        import app.scheduler as scheduler_module

        source = inspect.getsource(scheduler_module)
        for forbidden in (
            "machine",
            "board_config",
            "protocol.",
            "uart_transport",
            "heater_controller",
        ):
            self.assertNotIn("import {}".format(forbidden), source)
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name in (
                "machine",
                "board_config",
                "protocol",
                "protocol.uart_transport",
                "app.heater_controller",
            ):
                raise AssertionError("forbidden import attempted")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            runpy.run_path(
                inspect.getsourcefile(Scheduler),
                run_name="scheduler_import_smoke",
            )


class TestSchedulerPublicSnapshot(unittest.TestCase):
    def test_public_snapshot_excludes_occurrence_and_authorization_maps(self):
        scheduler, _ = armed_scheduler()
        public = scheduler.public_snapshot()
        self.assertEqual(public["timer_count"], 1)
        self.assertTrue(public["armed"])
        for forbidden in (
            "timers",
            "occurrences",
            "last_error",
            "authorization_token",
        ):
            self.assertNotIn(forbidden, public)


if __name__ == "__main__":
    unittest.main()
