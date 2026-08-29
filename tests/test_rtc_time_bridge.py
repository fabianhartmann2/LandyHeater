import unittest

from adapters.ds3231_adapter import DS3231BusError
from services.rtc_time_bridge import RTCTimeBridge
from services.time_service import (
    CLOCK_SOURCE_BROWSER,
    CLOCK_SOURCE_NTP,
    TimeService,
)


SAMPLE = {
    "year": 2026,
    "month": 8,
    "day": 9,
    "weekday": 6,
    "hour": 14,
    "minute": 29,
    "second": 59,
}


class FakeRTC:
    def __init__(self):
        self.sample = dict(SAMPLE)
        self.calls = []
        self.read_plan = []
        self.write_plan = []
        self.commit_plan = []
        self.read_hook = None
        self.write_hook = None
        self.commit_hook = None
        self.staged = False
        self.closed = False

    def read_utc_datetime(self):
        self.calls.append(("read",))
        if self.read_hook is not None:
            self.read_hook()
        if self.read_plan:
            result = self.read_plan.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return dict(self.sample)

    def stage_utc_datetime(self, year, month, day, hour, minute, second):
        fields = (year, month, day, hour, minute, second)
        self.calls.append(("stage", fields))
        self.staged = True
        if self.write_hook is not None:
            self.write_hook()
        if self.write_plan:
            result = self.write_plan.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        self.sample = {
            "year": year,
            "month": month,
            "day": day,
            "weekday": 6,
            "hour": hour,
            "minute": minute,
            "second": second,
        }
        return dict(self.sample)

    def commit_staged_write(self):
        self.calls.append(("commit",))
        if self.commit_hook is not None:
            self.commit_hook()
        if self.commit_plan:
            result = self.commit_plan.pop(0)
            if isinstance(result, BaseException):
                raise result
            if result is not None:
                return result
        if not self.staged:
            raise DS3231BusError("not staged")
        self.staged = False
        return None

    def deinit(self):
        self.closed = True
        return None


class TestRTCTimeBridge(unittest.TestCase):
    def setUp(self):
        self.rtc = FakeRTC()
        self.clock = TimeService(ticks_diff=lambda newer, older: newer - older)
        self.bridge = RTCTimeBridge(
            self.rtc,
            self.clock,
            ticks_diff=lambda newer, older: newer - older,
            ticks_add=lambda value, delta: value + delta,
            refresh_interval_ms=60000,
            retry_interval_ms=5000,
        )

    def test_constructor_is_inert_and_first_step_loads_utc(self):
        self.assertEqual(self.rtc.calls, [])
        self.assertEqual(self.bridge.step(0), "rtc_read")
        snapshot = self.clock.snapshot(0)
        self.assertTrue(snapshot["valid"])
        self.assertEqual(snapshot["source"], "rtc")
        self.assertEqual(snapshot["local"]["hour"], 14)
        self.assertFalse(snapshot["rtc_write_pending"])

    def test_refresh_is_paced_and_matching_sample_does_not_fence(self):
        self.bridge.step(0)
        revision = self.clock.clock_revision
        self.assertIsNone(self.bridge.step(59999))
        self.rtc.sample["minute"] = 30
        self.rtc.sample["second"] = 59
        self.assertEqual(self.bridge.step(60000), "rtc_read")
        self.assertEqual(self.clock.clock_revision, revision)
        self.assertEqual(len(self.rtc.calls), 2)

    def test_expected_read_error_keeps_clock_invalid_and_is_backed_off(self):
        self.rtc.read_plan = [DS3231BusError("nack")]
        self.assertEqual(self.bridge.step(0), "rtc_error")
        snapshot = self.clock.snapshot(0)
        self.assertFalse(snapshot["valid"])
        self.assertEqual(snapshot["rtc_health"], "error")
        self.assertIsNone(self.bridge.step(4999))
        self.assertEqual(len(self.rtc.calls), 1)

    def test_error_reporting_oom_latches_fault_before_retry(self):
        class SnapshotFailure:
            def __init__(self, inner):
                self.inner = inner
                self.calls = 0

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def snapshot(self, now_ms):
                self.calls += 1
                if self.calls == 2:
                    raise MemoryError("reporting snapshot failed")
                return self.inner.snapshot(now_ms)

        clock = SnapshotFailure(self.clock)
        bridge = RTCTimeBridge(
            self.rtc,
            clock,
            ticks_diff=lambda newer, older: newer - older,
            ticks_add=lambda value, delta: value + delta,
            refresh_interval_ms=60000,
            retry_interval_ms=5000,
        )
        self.rtc.read_plan = [DS3231BusError("nack")]
        with self.assertRaisesRegex(MemoryError, "reporting snapshot"):
            bridge.step(0)
        self.assertTrue(bridge.faulted)
        self.assertEqual(bridge.snapshot()["next_due_ms"], 5000)
        self.assertEqual(bridge.step(5000), "faulted")

    def test_ntp_correction_is_written_and_exact_revision_acknowledged(self):
        self.clock.set_utc_datetime(
            2026, 8, 9, 15, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        revision = self.clock.snapshot(0)["rtc_write_revision"]
        self.assertEqual(self.bridge.step(0), "rtc_write")
        self.assertEqual(
            self.rtc.calls,
            [
                ("stage", (2026, 8, 9, 15, 0, 0)),
                ("commit",),
            ],
        )
        snapshot = self.clock.snapshot(0)
        self.assertFalse(snapshot["rtc_write_pending"])
        self.assertEqual(snapshot["clock_revision"], revision)
        self.assertEqual(snapshot["rtc_health"], "ok")

    def test_staged_readback_may_advance_by_exactly_one_second(self):
        self.clock.set_utc_datetime(
            2026, 8, 9, 15, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        advanced = {
            "year": 2026,
            "month": 8,
            "day": 9,
            "weekday": 6,
            "hour": 15,
            "minute": 0,
            "second": 1,
        }
        self.rtc.write_plan = [advanced]

        self.assertEqual(self.bridge.step(0), "rtc_write")
        snapshot = self.clock.snapshot(0)
        self.assertFalse(self.bridge.faulted)
        self.assertFalse(snapshot["rtc_write_pending"])
        self.assertEqual(snapshot["rtc_health"], "ok")
        self.assertEqual(self.rtc.calls[-1], ("commit",))

    def test_write_error_never_acknowledges_pending_revision(self):
        self.clock.set_utc_datetime(
            2026, 8, 9, 15, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        revision = self.clock.snapshot(0)["rtc_write_revision"]
        self.rtc.write_plan = [DS3231BusError("write")]
        self.assertEqual(self.bridge.step(0), "rtc_error")
        snapshot = self.clock.snapshot(0)
        self.assertTrue(snapshot["rtc_write_pending"])
        self.assertEqual(snapshot["rtc_write_revision"], revision)
        self.assertEqual(snapshot["rtc_health"], "error")
        self.assertIsNone(self.bridge.step(4999))

    def test_stale_write_cannot_acknowledge_new_browser_revision(self):
        self.clock.set_utc_datetime(
            2026, 8, 9, 15, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        old_revision = self.clock.snapshot(0)["rtc_write_revision"]

        def newer_correction():
            self.rtc.write_hook = None
            self.clock.set_utc_datetime(
                2026, 8, 9, 16, 0, 0, CLOCK_SOURCE_BROWSER, 0
            )

        self.rtc.write_hook = newer_correction
        self.assertEqual(self.bridge.step(0), "rtc_write_stale")
        snapshot = self.clock.snapshot(0)
        self.assertTrue(snapshot["rtc_write_pending"])
        self.assertNotEqual(snapshot["rtc_write_revision"], old_revision)
        self.assertEqual(snapshot["source"], "browser")
        self.assertEqual(self.bridge.snapshot()["next_due_ms"], 0)
        self.assertTrue(self.rtc.staged)
        self.assertNotIn(("commit",), self.rtc.calls)
        self.assertEqual(self.bridge.step(0), "rtc_write")
        self.assertFalse(self.clock.snapshot(0)["rtc_write_pending"])
        self.assertFalse(self.rtc.staged)

    def test_stale_write_marker_survives_a_simulated_reboot(self):
        self.clock.set_utc_datetime(
            2026, 8, 9, 15, 0, 0, CLOCK_SOURCE_NTP, 0
        )

        def newer_correction():
            self.rtc.write_hook = None
            self.clock.set_utc_datetime(
                2026, 8, 9, 16, 0, 0, CLOCK_SOURCE_BROWSER, 0
            )

        self.rtc.write_hook = newer_correction
        self.assertEqual(self.bridge.step(0), "rtc_write_stale")
        self.assertTrue(self.rtc.staged)
        self.assertNotIn(("commit",), self.rtc.calls)

        # A fresh process has no RAM-side correction state.  The persistent
        # RTC marker must still reject the old staged value.
        fresh_clock = TimeService(
            ticks_diff=lambda newer, older: newer - older
        )

        class RebootRTC:
            def __init__(self, inner):
                self.inner = inner

            def read_utc_datetime(self):
                if self.inner.staged:
                    raise DS3231BusError("durable marker locked")
                return dict(self.inner.sample)

            def stage_utc_datetime(self, *fields):
                return self.inner.stage_utc_datetime(*fields)

            def commit_staged_write(self):
                return self.inner.commit_staged_write()

        reboot_bridge = RTCTimeBridge(
            RebootRTC(self.rtc),
            fresh_clock,
            ticks_diff=lambda newer, older: newer - older,
            ticks_add=lambda value, delta: value + delta,
        )
        self.assertEqual(reboot_bridge.step(0), "rtc_error")
        self.assertFalse(fresh_clock.snapshot(0)["valid"])

    def test_commit_callback_cannot_replace_the_locked_revision(self):
        self.clock.set_utc_datetime(
            2026, 8, 9, 15, 0, 0, CLOCK_SOURCE_NTP, 0
        )

        def correction_during_commit():
            self.clock.set_utc_datetime(
                2026, 8, 9, 16, 0, 0, CLOCK_SOURCE_BROWSER, 0
            )

        self.rtc.commit_hook = correction_during_commit
        with self.assertRaisesRegex(RuntimeError, "commit is in progress"):
            self.bridge.step(0)
        snapshot = self.clock.snapshot(0)
        self.assertEqual(snapshot["source"], "ntp")
        self.assertEqual(snapshot["local"]["hour"], 15)
        self.assertFalse(snapshot["rtc_write_pending"])
        self.assertEqual(snapshot["rtc_health"], "error")
        self.assertTrue(self.bridge.faulted)
        self.assertIsNone(snapshot["rtc_commit_revision"])

    def test_commit_failure_fences_clock_and_retries_the_same_stage(self):
        self.clock.set_utc_datetime(
            2026, 8, 9, 15, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        self.rtc.commit_plan = [DS3231BusError("commit")]
        self.assertEqual(self.bridge.step(0), "rtc_error")
        snapshot = self.clock.snapshot(0)
        self.assertFalse(snapshot["rtc_write_pending"])
        self.assertEqual(snapshot["rtc_health"], "error")
        self.assertTrue(self.bridge.snapshot()["commit_pending"])
        self.assertTrue(
            self.clock.configure_timezone("UTC+1-fixed", 60, 1000)
        )
        self.rtc.write_plan = [
            {
                "year": 2026,
                "month": 8,
                "day": 9,
                "weekday": 6,
                "hour": 15,
                "minute": 0,
                "second": 6,
            }
        ]
        self.assertEqual(self.bridge.step(5000), "rtc_write_commit")
        self.assertFalse(self.bridge.snapshot()["commit_pending"])
        self.assertEqual(self.clock.snapshot(5000)["rtc_health"], "ok")
        self.assertIn(
            ("stage", (2026, 8, 9, 15, 0, 5)), self.rtc.calls
        )

    def test_tick_helper_failures_latch_fault(self):
        rtc = FakeRTC()
        clock = TimeService(ticks_diff=lambda newer, older: newer - older)

        def failing_add(value, delta):
            raise MemoryError("ticks_add oom")

        bridge = RTCTimeBridge(
            rtc,
            clock,
            ticks_diff=lambda newer, older: newer - older,
            ticks_add=failing_add,
        )
        with self.assertRaisesRegex(MemoryError, "ticks_add oom"):
            bridge.step(0)
        self.assertTrue(bridge.faulted)
        clock_state = clock.snapshot(0)
        self.assertTrue(clock_state["valid"])
        self.assertEqual(clock_state["health"], "holdover")
        self.assertEqual(clock_state["rtc_health"], "error")

        diff_calls = [0]

        def failing_diff(newer, older):
            diff_calls[0] += 1
            if diff_calls[0] == 1:
                raise MemoryError("ticks_diff oom")
            return newer - older

        diff_clock = TimeService(
            ticks_diff=lambda newer, older: newer - older
        )
        bridge = RTCTimeBridge(
            FakeRTC(),
            diff_clock,
            ticks_diff=failing_diff,
            ticks_add=lambda value, delta: value + delta,
        )
        self.assertEqual(bridge.step(0), "rtc_read")
        with self.assertRaisesRegex(MemoryError, "ticks_diff oom"):
            bridge.step(1)
        self.assertTrue(bridge.faulted)
        self.assertEqual(diff_clock.snapshot(1)["health"], "holdover")

    def test_invalid_stage_and_commit_return_contracts_never_create_trust(self):
        self.clock.set_utc_datetime(
            2026, 8, 9, 15, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        self.rtc.write_plan = [False]
        with self.assertRaises(ValueError):
            self.bridge.step(0)
        stage_state = self.clock.snapshot(0)
        self.assertTrue(stage_state["rtc_write_pending"])
        self.assertEqual(stage_state["rtc_health"], "error")
        self.assertTrue(self.bridge.faulted)

        rtc = FakeRTC()
        clock = TimeService(ticks_diff=lambda newer, older: newer - older)
        bridge = RTCTimeBridge(
            rtc,
            clock,
            ticks_diff=lambda newer, older: newer - older,
            ticks_add=lambda value, delta: value + delta,
        )
        clock.set_utc_datetime(
            2026, 8, 9, 15, 0, 0, CLOCK_SOURCE_NTP, 0
        )
        rtc.commit_plan = [False]
        with self.assertRaises(ValueError):
            bridge.step(0)
        commit_state = clock.snapshot(0)
        self.assertFalse(commit_state["rtc_write_pending"])
        self.assertEqual(commit_state["rtc_health"], "error")
        self.assertTrue(bridge.faulted)

    def test_rtc_read_cannot_overwrite_a_concurrent_correction(self):
        def correction_during_read():
            self.rtc.read_hook = None
            self.clock.set_utc_datetime(
                2026, 8, 9, 16, 0, 0, CLOCK_SOURCE_NTP, 0
            )

        self.rtc.read_hook = correction_during_read
        self.assertEqual(self.bridge.step(0), "rtc_read_stale")
        snapshot = self.clock.snapshot(0)
        self.assertEqual(snapshot["source"], "ntp")
        self.assertEqual(snapshot["local"]["hour"], 16)
        self.assertTrue(snapshot["rtc_write_pending"])
        self.assertEqual(self.bridge.snapshot()["next_due_ms"], 0)

    def test_malformed_sample_and_memory_error_latch_fault(self):
        self.rtc.read_plan = [{"year": 2026}]
        with self.assertRaises(ValueError):
            self.bridge.step(0)
        self.assertTrue(self.bridge.faulted)
        self.assertEqual(self.bridge.step(1), "faulted")
        self.assertTrue(self.bridge.reset_fault(2))

        self.rtc.read_plan = [MemoryError("oom")]
        with self.assertRaises(MemoryError):
            self.bridge.step(2)
        self.assertTrue(self.bridge.faulted)

    def test_backward_time_faults_before_new_io(self):
        self.bridge.step(10)
        before = len(self.rtc.calls)
        with self.assertRaises(ValueError):
            self.bridge.step(9)
        self.assertEqual(len(self.rtc.calls), before)
        self.assertTrue(self.bridge.faulted)

    def test_deinit_closes_bridge_and_delegates(self):
        self.assertIsNone(self.bridge.deinit())
        self.assertTrue(self.rtc.closed)
        self.assertEqual(self.bridge.step(0), "closed")


if __name__ == "__main__":
    unittest.main()
