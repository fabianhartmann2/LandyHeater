import copy
import unittest
from unittest import mock

from app.application_state import CONTROL_MODE_POWER
from app.scheduler import (
    PERSISTENT_HISTORY_STATUS_CONSUMED,
    PERSISTENT_HISTORY_STATUS_OVERRIDDEN,
    Scheduler,
)
from services.time_service import (
    CLOCK_SOURCE_RTC,
    TimeService,
    civil_to_utc_seconds,
    epoch_seconds_to_civil,
)


def timer_definition(timer_id="weekday"):
    return {
        "id": timer_id,
        "name": "Test timer",
        "enabled": True,
        "weekdays": [6],
        "start": "14:30",
        "mode": CONTROL_MODE_POWER,
        "target_temperature": None,
        "power_level": 5,
        "runtime_minutes": 30,
    }


def clock_at(year=2026, month=8, day=9):
    clock = TimeService()
    clock.set_utc_datetime(
        year,
        month,
        day,
        14,
        29,
        59,
        CLOCK_SOURCE_RTC,
        0,
    )
    return clock


def configured_scheduler(clock=None, definitions=None, **kwargs):
    if clock is None:
        clock = clock_at()
    scheduler = Scheduler(clock, maximum_runtime_minutes=120, **kwargs)
    if definitions is None:
        definitions = [timer_definition()]
    scheduler.replace_timers(definitions)
    return scheduler


def create_intent(scheduler):
    scheduler.arm()
    scheduler.step(0, True)
    intent = scheduler.step(1000, True)
    if intent is None:
        raise AssertionError("test fixture did not create an intent")
    return intent


def requested_snapshot_for(authorized):
    return {
        "on": True,
        "mode": authorized.mode,
        "target_temperature": authorized.target_temperature,
        "power_level": authorized.power_level,
        "runtime_minutes": authorized.runtime_minutes,
        "source": authorized.source,
    }


def accept_intent(scheduler, intent, now_ms=1001):
    authorized = scheduler.authorize_intent(intent, now_ms, True)
    if authorized is None:
        raise AssertionError("test fixture did not authorize the intent")
    if not scheduler.complete_intent(
        authorized,
        True,
        requested_snapshot_for(authorized),
        now_ms,
    ):
        raise AssertionError("test fixture did not accept the intent")
    return authorized


def local_minute_id(year=2026, month=8, day=9, hour=14, minute=30):
    return civil_to_utc_seconds(
        year, month, day, hour, minute, 0
    ) // 60


def persistent_record(
    timer_id="weekday",
    minute_id=None,
    status=PERSISTENT_HISTORY_STATUS_CONSUMED,
):
    if minute_id is None:
        minute_id = local_minute_id()
    projected = epoch_seconds_to_civil(minute_id * 60)
    key = "{}|{:04d}-{:02d}-{:02d}|{:02d}:{:02d}".format(
        timer_id,
        projected["year"],
        projected["month"],
        projected["day"],
        projected["hour"],
        projected["minute"],
    )
    return {
        "timer_id": timer_id,
        "occurrence_key": key,
        "local_minute_id": minute_id,
        "status": status,
        "overridden": status == PERSISTENT_HISTORY_STATUS_OVERRIDDEN,
    }


def persistent_history(records=None, high_water=None):
    if records is None:
        records = [persistent_record()]
    if high_water is None and records:
        high_water = max(record["local_minute_id"] for record in records)
    return {
        "consumed_local_high_water": high_water,
        "occurrences": records,
    }


class TestSchedulerPersistentExport(unittest.TestCase):
    def test_empty_export_has_the_exact_detached_shape(self):
        scheduler = configured_scheduler()
        first = scheduler.export_persistent_history()
        self.assertEqual(
            first,
            {
                "consumed_local_high_water": None,
                "occurrences": [],
            },
        )
        first["occurrences"].append({"tampered": True})
        self.assertEqual(
            scheduler.export_persistent_history()["occurrences"], []
        )

    def test_intent_authorization_and_acceptance_export_as_consumed(self):
        for stage in ("intent_created", "authorized_pending", "accepted"):
            with self.subTest(stage=stage):
                scheduler = configured_scheduler()
                intent = create_intent(scheduler)
                if stage == "authorized_pending":
                    self.assertIsNotNone(
                        scheduler.authorize_intent(intent, 1001, True)
                    )
                elif stage == "accepted":
                    accept_intent(scheduler, intent)

                exported = scheduler.export_persistent_history()
                self.assertEqual(len(exported["occurrences"]), 1)
                record = exported["occurrences"][0]
                self.assertEqual(
                    set(record),
                    {
                        "timer_id",
                        "occurrence_key",
                        "local_minute_id",
                        "status",
                        "overridden",
                    },
                )
                self.assertEqual(
                    record["status"], PERSISTENT_HISTORY_STATUS_CONSUMED
                )
                self.assertIs(record["overridden"], False)
                for forbidden in (
                    "consumed_at_ms",
                    "not_after_ms",
                    "intent_token",
                    "authorization_token",
                    "authorized_at_ms",
                    "completed_at_ms",
                    "authorization_epoch",
                ):
                    self.assertNotIn(forbidden, record)

    def test_completed_and_suppressed_occurrences_export_as_consumed(self):
        completed = configured_scheduler()
        intent = create_intent(completed)
        accept_intent(completed, intent)
        self.assertTrue(
            completed.mark_active_complete(intent.occurrence_key, 1002)
        )
        self.assertEqual(
            completed.export_persistent_history()["occurrences"][0][
                "status"
            ],
            PERSISTENT_HISTORY_STATUS_CONSUMED,
        )

        suppressed = configured_scheduler()
        suppressed.arm()
        suppressed.step(0, False)
        self.assertIsNone(suppressed.step(1000, False))
        self.assertEqual(
            suppressed.export_persistent_history()["occurrences"][0][
                "status"
            ],
            PERSISTENT_HISTORY_STATUS_CONSUMED,
        )

    def test_override_is_the_only_overridden_persistent_latch(self):
        scheduler = configured_scheduler()
        intent = create_intent(scheduler)
        accept_intent(scheduler, intent)
        self.assertTrue(
            scheduler.mark_manual_override(intent.occurrence_key, 1002)
        )
        exported = scheduler.export_persistent_history()
        self.assertEqual(
            exported["occurrences"][0]["status"],
            PERSISTENT_HISTORY_STATUS_OVERRIDDEN,
        )
        self.assertIs(exported["occurrences"][0]["overridden"], True)

    def test_export_is_deeply_detached_and_canonically_ordered(self):
        scheduler = configured_scheduler(
            definitions=[timer_definition("z"), timer_definition("a")]
        )
        scheduler.arm()
        scheduler.step(0, True)
        self.assertIsNone(scheduler.step(1000, True))
        exported = scheduler.export_persistent_history()
        self.assertEqual(
            [item["timer_id"] for item in exported["occurrences"]],
            ["a", "z"],
        )
        exported["occurrences"][0]["timer_id"] = "tampered"
        exported["occurrences"].clear()
        self.assertEqual(
            [
                item["timer_id"]
                for item in scheduler.export_persistent_history()[
                    "occurrences"
                ]
            ],
            ["a", "z"],
        )


class TestSchedulerPersistentRestore(unittest.TestCase):
    def test_restore_never_recreates_active_or_ephemeral_state(self):
        original = configured_scheduler()
        intent = create_intent(original)
        accept_intent(original, intent)
        history = original.export_persistent_history()

        restored = configured_scheduler()
        self.assertTrue(restored.restore_persistent_history(history))
        snapshot = restored.snapshot()
        self.assertFalse(snapshot["armed"])
        self.assertIsNone(snapshot["active_occurrence_key"])
        self.assertIsNone(snapshot["active_occurrence"])
        self.assertIsNone(snapshot["last_override"])
        self.assertEqual(
            snapshot["occurrences"],
            {"weekday": history["occurrences"][0]},
        )

    def test_restored_occurrence_does_not_refire_but_next_week_can(self):
        original = configured_scheduler()
        history = original.export_persistent_history()
        original_intent = create_intent(original)
        self.assertIsNotNone(original_intent)
        history = original.export_persistent_history()

        same_minute = configured_scheduler(clock=clock_at())
        same_minute.restore_persistent_history(history)
        same_minute.arm()
        self.assertIsNone(same_minute.step(0, True))
        self.assertIsNone(same_minute.step(1000, True))

        next_week = configured_scheduler(clock=clock_at(day=16))
        next_week.restore_persistent_history(history)
        next_week.arm()
        self.assertIsNone(next_week.step(0, True))
        self.assertIsNotNone(next_week.step(1000, True))

    def test_global_high_water_alone_prevents_rollback_refire(self):
        minute_id = local_minute_id()
        scheduler = configured_scheduler(clock=clock_at())
        scheduler.restore_persistent_history(
            {
                "consumed_local_high_water": minute_id,
                "occurrences": [],
            }
        )
        scheduler.arm()
        scheduler.step(0, True)
        self.assertIsNone(scheduler.step(1000, True))

    def test_restore_is_detached_from_input_and_export(self):
        candidate = persistent_history()
        expected = copy.deepcopy(candidate)
        scheduler = configured_scheduler()
        scheduler.restore_persistent_history(candidate)

        candidate["consumed_local_high_water"] = None
        candidate["occurrences"][0]["timer_id"] = "tampered"
        exported = scheduler.export_persistent_history()
        self.assertEqual(exported, expected)
        exported["occurrences"][0]["timer_id"] = "also-tampered"
        self.assertEqual(scheduler.export_persistent_history(), expected)

    def test_restore_requires_a_fresh_disarmed_scheduler_and_is_once_only(self):
        history = persistent_history()

        armed = configured_scheduler()
        armed.arm()
        with self.assertRaises(RuntimeError):
            armed.restore_persistent_history(history)

        previously_armed = configured_scheduler()
        previously_armed.arm()
        previously_armed.disarm()
        with self.assertRaises(RuntimeError):
            previously_armed.restore_persistent_history(history)

        once = configured_scheduler()
        self.assertTrue(once.restore_persistent_history(history))
        with self.assertRaises(RuntimeError):
            once.restore_persistent_history(history)

    def test_failed_validation_is_atomic_and_does_not_consume_restore(self):
        scheduler = configured_scheduler()
        invalid = persistent_history()
        invalid["occurrences"][0]["occurrence_key"] = (
            "weekday|2026-08-09|14:31"
        )
        with self.assertRaises(ValueError):
            scheduler.restore_persistent_history(invalid)
        self.assertEqual(
            scheduler.export_persistent_history(),
            {
                "consumed_local_high_water": None,
                "occurrences": [],
            },
        )
        self.assertTrue(
            scheduler.restore_persistent_history(persistent_history())
        )

    def test_memory_failure_is_atomic_and_retryable(self):
        scheduler = configured_scheduler()
        with mock.patch(
            "app.scheduler._normalize_persistent_occurrence",
            side_effect=MemoryError,
        ):
            with self.assertRaises(MemoryError):
                scheduler.restore_persistent_history(persistent_history())
        self.assertEqual(
            scheduler.export_persistent_history()["occurrences"], []
        )
        self.assertTrue(
            scheduler.restore_persistent_history(persistent_history())
        )

    def test_restore_accepts_at_most_the_instance_bound(self):
        minute_id = local_minute_id()
        records = [
            persistent_record("timer-{:02d}".format(index), minute_id)
            for index in range(32)
        ]
        maximum = Scheduler(clock_at(), 120)
        maximum.replace_timers(
            [
                timer_definition("timer-{:02d}".format(index))
                for index in range(32)
            ]
        )
        self.assertTrue(
            maximum.restore_persistent_history(
                persistent_history(records, minute_id)
            )
        )
        self.assertEqual(
            len(maximum.export_persistent_history()["occurrences"]), 32
        )

        too_many_records = records + [persistent_record("timer-32", minute_id)]
        too_many = Scheduler(clock_at(), 120)
        with self.assertRaises(ValueError):
            too_many.restore_persistent_history(
                persistent_history(too_many_records, minute_id)
            )

        instance_bounded = Scheduler(clock_at(), 120, max_timers=1)
        instance_bounded.replace_timers([timer_definition("timer-00")])
        with self.assertRaises(ValueError):
            instance_bounded.restore_persistent_history(
                persistent_history(records[:2], minute_id)
            )

    def test_unknown_timer_latches_are_rejected_before_the_bound_can_grow(self):
        scheduler = configured_scheduler(
            definitions=[timer_definition("configured")], max_timers=1
        )
        with self.assertRaises(ValueError):
            scheduler.restore_persistent_history(
                persistent_history([persistent_record("deleted")])
            )
        self.assertEqual(
            scheduler.export_persistent_history()["occurrences"], []
        )


class TestSchedulerPersistentValidation(unittest.TestCase):
    def assert_rejected(self, candidate):
        scheduler = configured_scheduler()
        with self.assertRaises(ValueError):
            scheduler.restore_persistent_history(candidate)
        self.assertEqual(
            scheduler.export_persistent_history(),
            {
                "consumed_local_high_water": None,
                "occurrences": [],
            },
        )

    def test_root_shape_and_types_are_exact(self):
        base = persistent_history()
        invalid = []

        candidate = copy.deepcopy(base)
        candidate["unknown"] = None
        invalid.append(candidate)
        candidate = copy.deepcopy(base)
        del candidate["occurrences"]
        invalid.append(candidate)
        candidate = copy.deepcopy(base)
        candidate["occurrences"] = tuple(candidate["occurrences"])
        invalid.append(candidate)
        candidate = copy.deepcopy(base)
        candidate["consumed_local_high_water"] = True
        invalid.append(candidate)
        candidate = copy.deepcopy(base)
        candidate["consumed_local_high_water"] = -1
        invalid.append(candidate)
        candidate = copy.deepcopy(base)
        candidate["consumed_local_high_water"] = (
            civil_to_utc_seconds(2099, 12, 31, 23, 59, 59) // 60 + 1
        )
        invalid.append(candidate)
        candidate = copy.deepcopy(base)
        candidate["consumed_local_high_water"] = 10 ** 10000
        invalid.append(candidate)

        for value in invalid:
            with self.subTest(value=value):
                self.assert_rejected(value)

        maximum_minute = civil_to_utc_seconds(
            2099, 12, 31, 23, 59, 59
        ) // 60
        maximum_record = persistent_record(
            minute_id=maximum_minute
        )
        scheduler = configured_scheduler()
        self.assertTrue(
            scheduler.restore_persistent_history(
                persistent_history([maximum_record], maximum_minute)
            )
        )

    def test_occurrence_fields_types_and_status_are_exact(self):
        base = persistent_history()
        invalid = []

        candidate = copy.deepcopy(base)
        candidate["occurrences"][0]["unknown"] = None
        invalid.append(candidate)
        candidate = copy.deepcopy(base)
        del candidate["occurrences"][0]["status"]
        invalid.append(candidate)
        for field, value in (
            ("timer_id", " weekday"),
            ("timer_id", "bad|id"),
            ("timer_id", "x" * 65),
            ("occurrence_key", 1),
            ("local_minute_id", True),
            ("status", "accepted"),
            ("overridden", 1),
        ):
            candidate = copy.deepcopy(base)
            candidate["occurrences"][0][field] = value
            invalid.append(candidate)

        candidate = copy.deepcopy(base)
        candidate["occurrences"][0]["status"] = (
            PERSISTENT_HISTORY_STATUS_OVERRIDDEN
        )
        invalid.append(candidate)
        candidate = copy.deepcopy(base)
        candidate["occurrences"][0]["overridden"] = True
        invalid.append(candidate)

        for value in invalid:
            with self.subTest(value=value):
                self.assert_rejected(value)

    def test_key_date_time_and_local_minute_must_be_coherent(self):
        base = persistent_history()
        for key in (
            "weekday|2026-02-30|14:30",
            "weekday|2026-08-09|4:30",
            "weekday|2026-08-09|14:31",
            "other|2026-08-09|14:30",
            "weekday|2026-08-09|14:30 ",
        ):
            candidate = copy.deepcopy(base)
            candidate["occurrences"][0]["occurrence_key"] = key
            with self.subTest(key=key):
                self.assert_rejected(candidate)

        candidate = copy.deepcopy(base)
        candidate["occurrences"][0]["local_minute_id"] += 1
        self.assert_rejected(candidate)

    def test_timer_start_rejects_non_ascii_digit_spoofs(self):
        scheduler = Scheduler(clock_at(), 120)
        definition = timer_definition()
        definition["start"] = "١٤:٣٠"
        with self.assertRaises(ValueError):
            scheduler.replace_timers([definition])

    def test_high_water_and_unique_timer_latches_are_enforced(self):
        record = persistent_record()
        self.assert_rejected(
            {
                "consumed_local_high_water": None,
                "occurrences": [record],
            }
        )
        self.assert_rejected(
            {
                "consumed_local_high_water": record["local_minute_id"] - 1,
                "occurrences": [record],
            }
        )
        self.assert_rejected(
            {
                "consumed_local_high_water": record["local_minute_id"],
                "occurrences": [record, copy.deepcopy(record)],
            }
        )


if __name__ == "__main__":
    unittest.main()
