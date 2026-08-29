import datetime as _datetime
import inspect
import runpy
import unittest
from unittest import mock

from services.time_service import (
    CLOCK_HEALTH_HOLDOVER,
    CLOCK_HEALTH_INVALID,
    CLOCK_HEALTH_OK,
    CLOCK_SOURCE_BROWSER,
    CLOCK_SOURCE_NTP,
    CLOCK_SOURCE_RTC,
    EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
    EUROPE_ZURICH_TIMEZONE_NAME,
    RTC_HEALTH_ERROR,
    RTC_HEALTH_OK,
    TIMEZONE_RULE_EUROPE_ZURICH,
    TIMEZONE_RULE_FIXED,
    TimeService,
    civil_to_utc_seconds,
    epoch_seconds_to_civil,
    europe_zurich_transition_utc_seconds,
    is_timezone_transition_instant,
    local_civil_to_utc_occurrences,
    utc_seconds_to_local,
)


class ModularClock:
    def __init__(self, period):
        self.period = period
        self.half = period // 2

    def diff(self, newer, older):
        return ((newer - older + self.half) % self.period) - self.half


class TestTimeServiceCalendar(unittest.TestCase):
    def test_calendar_round_trip_and_weekday(self):
        cases = (
            ((2000, 1, 1, 0, 0, 0), 5),
            ((2024, 2, 29, 12, 34, 56), 3),
            ((2099, 12, 31, 23, 59, 59), 3),
        )
        for civil, weekday in cases:
            with self.subTest(civil=civil):
                seconds = civil_to_utc_seconds(*civil)
                result = epoch_seconds_to_civil(seconds)
                self.assertEqual(
                    tuple(result[name] for name in (
                        "year", "month", "day", "hour", "minute", "second"
                    )),
                    civil,
                )
                self.assertEqual(result["weekday"], weekday)

    def test_invalid_civil_values_are_rejected(self):
        invalid = (
            (1999, 1, 1, 0, 0, 0),
            (2100, 1, 1, 0, 0, 0),
            (2023, 2, 29, 0, 0, 0),
            (2024, 13, 1, 0, 0, 0),
            (2024, 1, 0, 0, 0, 0),
            (2024, 1, 1, 24, 0, 0),
            (2024, 1, 1, 0, 60, 0),
            (2024, 1, 1, 0, 0, True),
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    civil_to_utc_seconds(*value)


class TestEuropeZurichTimezone(unittest.TestCase):
    @staticmethod
    def _last_sunday(year, month):
        if month == 3:
            last_day = 31
        else:
            last_day = 31
        while _datetime.date(year, month, last_day).weekday() != 6:
            last_day -= 1
        return last_day

    def test_transition_instants_match_independent_calendar_for_all_years(self):
        for year in range(2000, 2100):
            with self.subTest(year=year):
                march_day = self._last_sunday(year, 3)
                october_day = self._last_sunday(year, 10)
                expected = (
                    civil_to_utc_seconds(year, 3, march_day, 1, 0, 0),
                    civil_to_utc_seconds(year, 10, october_day, 1, 0, 0),
                )
                actual = europe_zurich_transition_utc_seconds(year)
                self.assertEqual(actual, expected)
                self.assertTrue(
                    is_timezone_transition_instant(
                        actual[0],
                        TIMEZONE_RULE_EUROPE_ZURICH,
                        EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
                    )
                )
                self.assertTrue(
                    is_timezone_transition_instant(
                        actual[1],
                        TIMEZONE_RULE_EUROPE_ZURICH,
                        EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
                    )
                )

    def test_spring_gap_and_fall_fold_boundaries_are_exact(self):
        cases = (
            ((2026, 3, 29, 0, 59, 59), (1, 59, 59, 60, False, 0)),
            ((2026, 3, 29, 1, 0, 0), (3, 0, 0, 120, True, 0)),
            ((2026, 10, 25, 0, 59, 59), (2, 59, 59, 120, True, 0)),
            ((2026, 10, 25, 1, 0, 0), (2, 0, 0, 60, False, 1)),
            ((2026, 10, 25, 1, 59, 59), (2, 59, 59, 60, False, 1)),
            ((2026, 10, 25, 2, 0, 0), (3, 0, 0, 60, False, 0)),
        )
        for utc_civil, expected in cases:
            with self.subTest(utc_civil=utc_civil):
                projection = utc_seconds_to_local(
                    civil_to_utc_seconds(*utc_civil),
                    TIMEZONE_RULE_EUROPE_ZURICH,
                    EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
                )
                local = projection["local"]
                actual = (
                    local["hour"],
                    local["minute"],
                    local["second"],
                    projection["utc_offset_minutes"],
                    projection["is_dst"],
                    local["fold"],
                )
                self.assertEqual(actual, expected)

    def test_local_gap_has_no_occurrence_and_fold_has_two_ordered_occurrences(self):
        gap = local_civil_to_utc_occurrences(
            2026,
            3,
            29,
            2,
            30,
            0,
            TIMEZONE_RULE_EUROPE_ZURICH,
            EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
        )
        self.assertEqual(gap, ())

        fold = local_civil_to_utc_occurrences(
            2026,
            10,
            25,
            2,
            30,
            0,
            TIMEZONE_RULE_EUROPE_ZURICH,
            EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
        )
        self.assertEqual(len(fold), 2)
        self.assertEqual(
            tuple(item["utc_seconds"] for item in fold),
            (
                civil_to_utc_seconds(2026, 10, 25, 0, 30, 0),
                civil_to_utc_seconds(2026, 10, 25, 1, 30, 0),
            ),
        )
        self.assertEqual(tuple(item["fold"] for item in fold), (0, 1))


class TestTimeServiceState(unittest.TestCase):
    def test_initial_state_is_invalid_and_invents_no_datetime(self):
        service = TimeService()
        snapshot = service.snapshot(0)
        self.assertFalse(snapshot["valid"])
        self.assertEqual(snapshot["health"], CLOCK_HEALTH_INVALID)
        self.assertIsNone(snapshot["utc_seconds"])
        self.assertIsNone(snapshot["local"])

    def test_rtc_sample_advances_from_monotonic_time(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 23, 59, 59, CLOCK_SOURCE_RTC, 100
        )
        first = service.snapshot(100)
        later = service.snapshot(2100)
        self.assertEqual(first["local"]["day"], 9)
        self.assertEqual(later["local"]["day"], 10)
        self.assertEqual(later["local"]["second"], 1)
        self.assertEqual(later["sync_age_ms"], 2000)
        self.assertEqual(later["rtc_health"], RTC_HEALTH_OK)

    def test_matching_periodic_rtc_refresh_does_not_fence_scheduler(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_RTC, 0
        )
        revision = service.clock_revision
        self.assertFalse(
            service.refresh_rtc_datetime(2026, 8, 9, 10, 0, 1, 1000)
        )
        self.assertEqual(service.clock_revision, revision)
        self.assertTrue(
            service.refresh_rtc_datetime(2026, 8, 9, 10, 0, 5, 2000)
        )
        self.assertEqual(service.clock_revision, revision + 1)

    def test_one_second_rtc_phase_difference_is_not_a_correction(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 14, 29, 59, CLOCK_SOURCE_RTC, 0
        )
        revision = service.clock_revision
        self.assertFalse(
            service.refresh_rtc_datetime(
                2026, 8, 9, 14, 30, 0, 500
            )
        )
        self.assertEqual(service.clock_revision, revision)

    def test_old_rtc_refresh_cannot_undo_pending_correction(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        pending_revision = service.snapshot(0)["rtc_write_revision"]
        self.assertFalse(
            service.refresh_rtc_datetime(2026, 8, 9, 9, 59, 0, 1)
        )
        snapshot = service.snapshot(1)
        self.assertEqual(snapshot["source"], CLOCK_SOURCE_NTP)
        self.assertTrue(snapshot["rtc_write_pending"])
        self.assertEqual(snapshot["rtc_write_revision"], pending_revision)
        self.assertEqual(snapshot["local"]["hour"], 10)

    def test_fixed_offset_crosses_local_date_without_changing_utc(self):
        service = TimeService(
            timezone_name="Europe/Zurich-fixed",
            utc_offset_minutes=120,
        )
        service.set_utc_datetime(
            2026, 8, 9, 22, 30, 0, CLOCK_SOURCE_RTC, 0
        )
        snapshot = service.snapshot(0)
        self.assertEqual(snapshot["utc_offset_minutes"], 120)
        self.assertEqual(snapshot["local"]["day"], 10)
        self.assertEqual(snapshot["local"]["hour"], 0)
        self.assertEqual(snapshot["local"]["minute"], 30)

    def test_europe_zurich_projects_winter_and_summer_without_rtc_rewrite(self):
        service = TimeService(
            timezone_name=EUROPE_ZURICH_TIMEZONE_NAME,
            utc_offset_minutes=EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
            timezone_rule=TIMEZONE_RULE_EUROPE_ZURICH,
        )
        service.set_utc_datetime(
            2026, 3, 29, 0, 59, 59, CLOCK_SOURCE_RTC, 0
        )
        before = service.snapshot(0)
        after = service.snapshot(1000)
        self.assertEqual(before["local"]["hour"], 1)
        self.assertEqual(before["utc_offset_minutes"], 60)
        self.assertFalse(before["is_dst"])
        self.assertEqual(after["local"]["hour"], 3)
        self.assertEqual(after["utc_offset_minutes"], 120)
        self.assertTrue(after["is_dst"])
        for name in (
            "clock_revision",
            "utc_revision",
            "timezone_revision",
            "rtc_write_pending",
            "rtc_write_revision",
        ):
            self.assertEqual(after[name], before[name])
        self.assertEqual(after["timezone_rule"], TIMEZONE_RULE_EUROPE_ZURICH)
        self.assertEqual(after["standard_utc_offset_minutes"], 60)
        self.assertEqual(after["timezone_rule_version"], 1)

    def test_explicit_europe_zurich_configuration_is_revisioned_not_persisted(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 7, 1, 12, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        before = service.snapshot(0)
        self.assertTrue(
            service.configure_timezone(
                EUROPE_ZURICH_TIMEZONE_NAME,
                EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
                1,
                TIMEZONE_RULE_EUROPE_ZURICH,
            )
        )
        after = service.snapshot(1)
        self.assertEqual(after["local"]["hour"], 14)
        self.assertEqual(after["utc_offset_minutes"], 120)
        self.assertTrue(after["is_dst"])
        self.assertEqual(after["clock_revision"], before["clock_revision"] + 1)
        self.assertEqual(
            after["timezone_revision"], before["timezone_revision"] + 1
        )
        self.assertEqual(after["utc_revision"], before["utc_revision"])
        self.assertEqual(
            after["rtc_write_revision"], before["rtc_write_revision"]
        )
        self.assertFalse(
            service.configure_timezone(
                EUROPE_ZURICH_TIMEZONE_NAME,
                EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
                2,
                TIMEZONE_RULE_EUROPE_ZURICH,
            )
        )

    def test_europe_zurich_configuration_is_strict_and_atomic(self):
        invalid = (
            {
                "timezone_name": "Europe/Zurich-copy",
                "utc_offset_minutes": 60,
                "timezone_rule": TIMEZONE_RULE_EUROPE_ZURICH,
            },
            {
                "timezone_name": EUROPE_ZURICH_TIMEZONE_NAME,
                "utc_offset_minutes": 120,
                "timezone_rule": TIMEZONE_RULE_EUROPE_ZURICH,
            },
            {
                "timezone_name": EUROPE_ZURICH_TIMEZONE_NAME,
                "utc_offset_minutes": 60,
                "timezone_rule": "iana",
            },
            {
                "timezone_name": EUROPE_ZURICH_TIMEZONE_NAME,
                "utc_offset_minutes": 60,
                "timezone_rule": TIMEZONE_RULE_FIXED,
            },
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    TimeService(**kwargs)

        service = TimeService()
        before = service.snapshot(0)
        with self.assertRaises(ValueError):
            service.configure_timezone(
                EUROPE_ZURICH_TIMEZONE_NAME,
                120,
                1,
                TIMEZONE_RULE_EUROPE_ZURICH,
            )
        after = service.snapshot(1)
        self.assertEqual(after["timezone"], before["timezone"])
        self.assertEqual(after["timezone_rule"], TIMEZONE_RULE_FIXED)
        self.assertEqual(
            after["timezone_revision"], before["timezone_revision"]
        )

    def test_ntp_and_browser_corrections_require_rtc_persistence(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        pending = service.snapshot(0)
        self.assertTrue(pending["rtc_write_pending"])
        self.assertEqual(pending["source"], CLOCK_SOURCE_NTP)
        self.assertTrue(
            service.mark_rtc_write_result(
                True, pending["rtc_write_revision"], 1
            )
        )
        stored = service.snapshot(1)
        self.assertFalse(stored["rtc_write_pending"])
        self.assertEqual(stored["rtc_health"], RTC_HEALTH_OK)

        revision = stored["clock_revision"]
        service.set_utc_datetime(
            2026, 8, 9, 10, 1, 0, CLOCK_SOURCE_BROWSER, 2
        )
        corrected = service.snapshot(2)
        self.assertEqual(corrected["clock_revision"], revision + 1)
        self.assertEqual(corrected["source"], CLOCK_SOURCE_BROWSER)

    def test_stale_rtc_write_ack_cannot_confirm_a_newer_correction(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        first_revision = service.snapshot(0)["rtc_write_revision"]
        service.set_utc_datetime(
            2026, 8, 9, 10, 1, 0, CLOCK_SOURCE_BROWSER, 1
        )
        second = service.snapshot(1)
        self.assertNotEqual(first_revision, second["rtc_write_revision"])
        self.assertFalse(
            service.mark_rtc_write_result(True, first_revision, 2)
        )
        still_pending = service.snapshot(2)
        self.assertTrue(still_pending["rtc_write_pending"])
        self.assertEqual(
            still_pending["rtc_write_revision"],
            second["rtc_write_revision"],
        )

    def test_rtc_commit_lock_blocks_reentrant_clock_changes_and_fences_health(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        pending = service.snapshot(0)
        revision = pending["rtc_write_revision"]
        self.assertTrue(service.begin_rtc_commit(revision, 0))
        locked = service.snapshot(0)
        self.assertEqual(locked["rtc_commit_revision"], revision)
        self.assertEqual(locked["health"], CLOCK_HEALTH_HOLDOVER)

        operations = (
            lambda: service.set_utc_datetime(
                2026, 8, 9, 11, 0, 0, CLOCK_SOURCE_BROWSER, 0
            ),
            lambda: service.refresh_rtc_datetime(
                2026, 8, 9, 10, 0, 0, 0
            ),
            lambda: service.configure_timezone("UTC+1-fixed", 60, 0),
            lambda: service.invalidate(0, "test"),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(
                    RuntimeError, "commit is in progress"
                ):
                    operation()

        self.assertTrue(
            service.mark_rtc_write_result(True, revision, 0)
        )
        self.assertTrue(service.mark_rtc_commit_recovered(revision, 0))
        self.assertTrue(service.end_rtc_commit(revision))
        committed = service.snapshot(0)
        self.assertEqual(committed["health"], CLOCK_HEALTH_OK)
        self.assertIsNone(committed["rtc_commit_revision"])

    def test_timezone_revision_is_separate_from_utc_persistence_revision(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        before = service.snapshot(0)
        self.assertTrue(service.configure_timezone("UTC+1-fixed", 60, 1))
        after = service.snapshot(1)
        self.assertEqual(after["utc_revision"], before["utc_revision"])
        self.assertEqual(
            after["rtc_write_revision"], before["rtc_write_revision"]
        )
        self.assertNotEqual(
            after["clock_revision"], before["clock_revision"]
        )

    def test_rtc_error_retains_time_only_as_visible_holdover(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_RTC, 0
        )
        self.assertTrue(service.report_rtc_error(1000, "oscillator stop"))
        snapshot = service.snapshot(2000)
        self.assertTrue(snapshot["valid"])
        self.assertEqual(snapshot["health"], CLOCK_HEALTH_HOLDOVER)
        self.assertEqual(snapshot["rtc_health"], RTC_HEALTH_ERROR)
        self.assertEqual(snapshot["local"]["second"], 2)

    def test_explicit_invalidation_fails_closed_until_new_sample(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_RTC, 0
        )
        revision = service.clock_revision
        self.assertTrue(service.invalidate(1, "lost trust"))
        invalid = service.snapshot(1)
        self.assertFalse(invalid["valid"])
        self.assertEqual(invalid["clock_revision"], revision + 1)
        self.assertFalse(invalid["rtc_write_pending"])
        self.assertIsNone(invalid["rtc_write_revision"])
        service.set_utc_datetime(
            2026, 8, 9, 10, 1, 0, CLOCK_SOURCE_RTC, 2
        )
        self.assertTrue(service.snapshot(2)["valid"])

    def test_invalidation_cancels_pending_untrusted_rtc_write(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        pending = service.snapshot(0)
        self.assertTrue(pending["rtc_write_pending"])
        service.invalidate(1, "source withdrawn")
        invalid = service.snapshot(1)
        self.assertFalse(invalid["rtc_write_pending"])
        self.assertIsNone(invalid["rtc_write_revision"])

    def test_timezone_change_is_revisioned_and_atomic(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_RTC, 0
        )
        clock_revision = service.clock_revision
        self.assertTrue(service.configure_timezone("CET-fixed", 60, 1))
        snapshot = service.snapshot(1)
        self.assertEqual(snapshot["local"]["hour"], 11)
        self.assertEqual(snapshot["timezone_revision"], 1)
        self.assertEqual(snapshot["clock_revision"], clock_revision + 1)
        self.assertFalse(service.configure_timezone("CET-fixed", 60, 2))

        before = service.snapshot(2)
        with self.assertRaises(ValueError):
            service.configure_timezone("bad", 900, 3)
        self.assertEqual(service.snapshot(3)["timezone"], before["timezone"])

    def test_tick_wrap_is_handled_by_injected_diff(self):
        clock = ModularClock(64)
        service = TimeService(ticks_diff=clock.diff)
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_RTC, 60
        )
        snapshot = service.snapshot(6)
        self.assertEqual(snapshot["sync_age_ms"], 10)

    def test_utc_range_end_invalidates_even_with_negative_local_offset(self):
        service = TimeService(utc_offset_minutes=-840)
        service.set_utc_datetime(
            2099, 12, 31, 23, 59, 59, CLOCK_SOURCE_RTC, 0
        )
        snapshot = service.snapshot(1000)
        self.assertFalse(snapshot["valid"])
        self.assertIsNone(snapshot["utc_seconds"])
        self.assertEqual(snapshot["last_error"], "clock_range_exceeded")

    def test_europe_zurich_local_range_end_is_fail_closed(self):
        service = TimeService(
            timezone_name=EUROPE_ZURICH_TIMEZONE_NAME,
            utc_offset_minutes=EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
            timezone_rule=TIMEZONE_RULE_EUROPE_ZURICH,
        )
        service.set_utc_datetime(
            2099, 12, 31, 22, 59, 59, CLOCK_SOURCE_RTC, 0
        )
        valid = service.snapshot(0)
        self.assertEqual(valid["local"]["hour"], 23)
        self.assertEqual(valid["local"]["minute"], 59)
        self.assertEqual(valid["local"]["second"], 59)
        self.assertFalse(service.snapshot(1000)["valid"])

        fresh = TimeService(
            timezone_name=EUROPE_ZURICH_TIMEZONE_NAME,
            utc_offset_minutes=EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
            timezone_rule=TIMEZONE_RULE_EUROPE_ZURICH,
        )
        before = fresh.snapshot(0)
        with self.assertRaises(ValueError):
            fresh.set_utc_datetime(
                2099, 12, 31, 23, 0, 0, CLOCK_SOURCE_RTC, 0
            )
        self.assertEqual(fresh.snapshot(0), before)

    def test_backward_monotonic_input_is_rejected_without_clock_rewrite(self):
        service = TimeService()
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_RTC, 100
        )
        with self.assertRaises(ValueError):
            service.snapshot(99)
        self.assertEqual(service.snapshot(100)["local"]["hour"], 10)

    def test_validation_and_read_only_configuration(self):
        for kwargs in (
            {"ticks_diff": "bad"},
            {"event_capacity": 0},
            {"event_capacity": 65},
            {"event_capacity": True},
            {"timezone_name": ""},
            {"utc_offset_minutes": 841},
            {"utc_offset_minutes": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    TimeService(**kwargs)
        service = TimeService()
        with self.assertRaises(AttributeError):
            service.utc_offset_minutes = 60
        with self.assertRaises(ValueError):
            service.set_utc_datetime(
                2026, 8, 9, 0, 0, 0, "internet", 0
            )

    def test_events_are_bounded_and_detached(self):
        service = TimeService(event_capacity=2)
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_RTC, 0
        )
        service.report_rtc_error(1, "x")
        service.invalidate(2, "y")
        self.assertEqual(service.snapshot(2)["events_dropped"], 1)
        events = service.drain_events()
        self.assertEqual(len(events), 2)
        events[0]["code"] = "mutated"
        self.assertEqual(service.drain_events(), [])

    def test_matching_rtc_refresh_is_not_periodic_event_telemetry(self):
        service = TimeService(event_capacity=2)
        service.set_utc_datetime(
            2026, 8, 9, 10, 0, 0, CLOCK_SOURCE_RTC, 0
        )
        service.drain_events()
        for second in range(1, 6):
            service.refresh_rtc_datetime(
                2026, 8, 9, 10, 0, second, second * 1000
            )
        snapshot = service.snapshot(5000)
        self.assertEqual(snapshot["events_pending"], 0)
        self.assertEqual(snapshot["events_dropped"], 0)

    def test_module_import_has_no_hardware_or_network_dependency(self):
        source = inspect.getsource(__import__(
            "services.time_service", fromlist=["TimeService"]
        ))
        for forbidden in ("machine", "network", "ds3231", "board_config"):
            self.assertNotIn("import {}".format(forbidden), source)
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name in ("machine", "network", "ds3231", "board_config"):
                raise AssertionError("hardware import attempted")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            runpy.run_path(
                inspect.getsourcefile(TimeService),
                run_name="time_service_import_smoke",
            )


if __name__ == "__main__":
    unittest.main()
