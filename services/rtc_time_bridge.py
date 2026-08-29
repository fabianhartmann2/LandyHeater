"""Cooperative, hardware-independent bridge between DS3231 and TimeService.

The injected RTC port owns register verification.  This bridge owns cadence
and the exact TimeService revision handshake.  Construction and import perform
no RTC, I2C or other hardware access.
"""

import time as _time

from adapters.ds3231_adapter import DS3231Error
from services.time_service import (
    CLOCK_SOURCE_RTC,
    civil_to_utc_seconds,
    epoch_seconds_to_civil,
)


DEFAULT_REFRESH_INTERVAL_MS = 60000
DEFAULT_RETRY_INTERVAL_MS = 5000


def _plain_ticks_diff(newer, older):
    return newer - older


def _plain_ticks_add(ticks, delta):
    return ticks + delta


_platform_ticks_diff = getattr(_time, "ticks_diff", _plain_ticks_diff)
_platform_ticks_add = getattr(_time, "ticks_add", _plain_ticks_add)


def _require_positive_integer(name, value):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("{} must be a positive integer".format(name))


def _require_ticks(now_ms):
    if not isinstance(now_ms, int) or isinstance(now_ms, bool):
        raise ValueError("now_ms must be an integer")


class RTCTimeBridge:
    """Run at most one verified RTC read or write transaction per step."""

    __slots__ = (
        "__rtc",
        "__time_service",
        "__ticks_diff",
        "__ticks_add",
        "__refresh_interval_ms",
        "__retry_interval_ms",
        "__next_due_ms",
        "__last_step_ms",
        "__faulted",
        "__closed",
        "__last_operation",
        "__last_error",
        "__reads",
        "__writes",
        "__errors",
        "__commit_pending",
        "__commit_revision",
    )

    def __init__(
        self,
        rtc_port,
        time_service,
        ticks_diff=None,
        ticks_add=None,
        refresh_interval_ms=DEFAULT_REFRESH_INTERVAL_MS,
        retry_interval_ms=DEFAULT_RETRY_INTERVAL_MS,
    ):
        for method_name in (
            "read_utc_datetime",
            "stage_utc_datetime",
            "commit_staged_write",
        ):
            if not callable(getattr(rtc_port, method_name, None)):
                raise ValueError(
                    "rtc_port must provide {}()".format(method_name)
                )
        for method_name in (
            "snapshot",
            "set_utc_datetime",
            "refresh_rtc_datetime",
            "report_rtc_error",
            "mark_rtc_write_result",
            "begin_rtc_commit",
            "mark_rtc_commit_recovered",
            "end_rtc_commit",
        ):
            if not callable(getattr(time_service, method_name, None)):
                raise ValueError(
                    "time_service must provide {}()".format(method_name)
                )
        if (ticks_diff is None) != (ticks_add is None):
            raise ValueError("ticks_diff and ticks_add must be paired")
        if ticks_diff is None:
            ticks_diff = _platform_ticks_diff
            ticks_add = _platform_ticks_add
        if not callable(ticks_diff) or not callable(ticks_add):
            raise ValueError("tick helpers must be callable")
        _require_positive_integer("refresh_interval_ms", refresh_interval_ms)
        _require_positive_integer("retry_interval_ms", retry_interval_ms)
        if retry_interval_ms >= refresh_interval_ms:
            raise ValueError("retry interval must be shorter than refresh")

        self.__rtc = rtc_port
        self.__time_service = time_service
        self.__ticks_diff = ticks_diff
        self.__ticks_add = ticks_add
        self.__refresh_interval_ms = refresh_interval_ms
        self.__retry_interval_ms = retry_interval_ms
        self.__next_due_ms = None
        self.__last_step_ms = None
        self.__faulted = False
        self.__closed = False
        self.__last_operation = None
        self.__last_error = None
        self.__reads = 0
        self.__writes = 0
        self.__errors = 0
        self.__commit_pending = False
        self.__commit_revision = None

    @property
    def faulted(self):
        return self.__faulted

    @property
    def closed(self):
        return self.__closed

    def _schedule(self, now_ms, delay_ms):
        try:
            due_ms = self.__ticks_add(now_ms, delay_ms)
        except MemoryError:
            self._latch_fault("rtc_bridge_memory_error")
            self._fence_time_service(now_ms, "rtc_bridge_timing_failed")
            raise
        except Exception:
            self._latch_fault("rtc_bridge_contract_error")
            self._fence_time_service(now_ms, "rtc_bridge_timing_failed")
            raise
        except BaseException:
            self._latch_fault("rtc_bridge_scheduling_interrupted")
            self._fence_time_service(now_ms, "rtc_bridge_timing_failed")
            raise
        self.__next_due_ms = due_ms

    def _ticks_difference(self, newer, older):
        try:
            return self.__ticks_diff(newer, older)
        except MemoryError:
            self._latch_fault("rtc_bridge_memory_error")
            self._fence_time_service(newer, "rtc_bridge_timing_failed")
            raise
        except Exception:
            self._latch_fault("rtc_bridge_contract_error")
            self._fence_time_service(newer, "rtc_bridge_timing_failed")
            raise
        except BaseException:
            self._latch_fault("rtc_bridge_timing_interrupted")
            self._fence_time_service(newer, "rtc_bridge_timing_failed")
            raise

    def _latch_fault(self, reason):
        self.__faulted = True
        self.__last_error = reason
        self.__errors += 1

    def _fence_time_service(self, now_ms, reason):
        try:
            self.__time_service.report_rtc_error(now_ms, reason)
        except BaseException:
            # Preserve the primary bridge failure.  The bridge fault remains a
            # mandatory composition-level gate even if diagnostics also fail.
            return False
        return True

    @staticmethod
    def _sample_fields(sample):
        if type(sample) is not dict or set(sample) != {
            "year",
            "month",
            "day",
            "weekday",
            "hour",
            "minute",
            "second",
        }:
            raise ValueError("RTC sample has an invalid shape")
        return (
            sample["year"],
            sample["month"],
            sample["day"],
            sample["hour"],
            sample["minute"],
            sample["second"],
        )

    def _read_rtc(self, now_ms, before):
        sample = self.__rtc.read_utc_datetime()
        fields = self._sample_fields(sample)
        confirmed = self.__time_service.snapshot(now_ms)
        for field in (
            "valid",
            "clock_revision",
            "utc_revision",
            "source",
            "utc_seconds",
            "rtc_write_pending",
            "rtc_write_revision",
            "rtc_commit_revision",
        ):
            if confirmed.get(field) != before.get(field):
                self.__reads += 1
                self.__last_operation = "rtc_read_stale"
                self.__last_error = None
                self._schedule(now_ms, 0)
                return "rtc_read_stale"
        if before["valid"]:
            self.__time_service.refresh_rtc_datetime(*fields, now_ms)
        else:
            self.__time_service.set_utc_datetime(
                *fields, CLOCK_SOURCE_RTC, now_ms
            )
        self.__reads += 1
        self.__last_operation = "rtc_read"
        self.__last_error = None
        self._schedule(now_ms, self.__refresh_interval_ms)
        return "rtc_read"

    def _write_pending(self, now_ms, before):
        revision = before["rtc_write_revision"]
        utc_seconds = before["utc_seconds"]
        if (
            type(revision) is not int
            or type(utc_seconds) is not int
            or not before["valid"]
        ):
            raise ValueError("pending RTC write snapshot is inconsistent")
        civil = epoch_seconds_to_civil(utc_seconds)

        # Re-read the revision immediately before the external side effect.
        # There is no yield or callback between this check and the write.
        confirmed = self.__time_service.snapshot(now_ms)
        if (
            confirmed.get("valid") is not True
            or confirmed.get("rtc_write_pending") is not True
            or confirmed.get("rtc_write_revision") != revision
            or confirmed.get("utc_revision") != revision
            or confirmed.get("utc_seconds") != utc_seconds
        ):
            self.__last_operation = "rtc_write_stale"
            return "rtc_write_stale"

        staged = self.__rtc.stage_utc_datetime(
            civil["year"],
            civil["month"],
            civil["day"],
            civil["hour"],
            civil["minute"],
            civil["second"],
        )
        staged_fields = self._sample_fields(staged)
        staged_seconds = civil_to_utc_seconds(*staged_fields)
        # The DS3231 may advance normally between the register write and its
        # canonical readback.  The register adapter therefore guarantees the
        # requested second or exactly the following second; preserve that
        # narrow contract at the bridge boundary.
        if staged_seconds not in (utc_seconds, utc_seconds + 1):
            raise ValueError("RTC staged write verification differs")
        self.__writes += 1
        self.__commit_pending = True
        self.__commit_revision = revision
        locked = self.__time_service.begin_rtc_commit(revision, now_ms)
        if type(locked) is not bool:
            raise ValueError("TimeService returned an invalid commit lock")
        if not locked:
            self.__commit_pending = False
            self.__commit_revision = None
            self.__last_operation = "rtc_write_stale"
            self._schedule(now_ms, 0)
            return self.__last_operation
        try:
            accepted = self.__time_service.mark_rtc_write_result(
                True, revision, now_ms
            )
            if accepted:
                # TimeService is locked before its exact generation is
                # acknowledged.  Corrections invoked by a Python callback are
                # rejected until the staged RTC trust marker is released.
                return self._commit_staged(now_ms, "rtc_write")

            # A newer correction won the generation race.  Do not release the
            # hardware marker for the obsolete staged value; the immediate
            # retry will replace it with the new generation first.
            self.__commit_pending = False
            self.__commit_revision = None
            self.__last_operation = "rtc_write_stale"
            self.__last_error = None
            self._schedule(now_ms, 0)
            return self.__last_operation
        finally:
            released = self.__time_service.end_rtc_commit(revision)
            if released is not True:
                raise ValueError("TimeService RTC commit lock was lost")

    def _commit_staged(self, now_ms, operation):
        revision = self.__commit_revision
        try:
            result = self.__rtc.commit_staged_write()
            if result is not None:
                raise ValueError("RTC commit returned an invalid result")
        except BaseException:
            # TimeService may already have accepted the exact write revision.
            # Fence timer use of that clock until the durable RTC trust release
            # succeeds; the staged marker and retry state remain intact.
            self.__time_service.report_rtc_error(
                now_ms, "rtc_commit_failed"
            )
            raise
        recovered = self.__time_service.mark_rtc_commit_recovered(
            revision, now_ms
        )
        if recovered is not True:
            raise ValueError("TimeService rejected committed RTC revision")
        self.__commit_pending = False
        self.__commit_revision = None
        self.__last_operation = operation
        self.__last_error = None
        self._schedule(now_ms, self.__refresh_interval_ms)
        return operation

    def _resume_staged_commit(self, now_ms, before):
        revision = self.__commit_revision
        utc_seconds = before.get("utc_seconds")
        if (
            type(revision) is not int
            or before.get("valid") is not True
            or type(utc_seconds) is not int
            or before.get("utc_revision") != revision
        ):
            self.__time_service.report_rtc_error(
                now_ms, "rtc_commit_revision_changed"
            )
            raise ValueError("staged RTC commit revision changed")

        # The oscillator continues while EOSC is used as a trust marker.  A
        # retry after backoff therefore restages the current monotonic UTC
        # value instead of requiring the original seconds value to stand
        # still.  The exact clock revision remains unchanged.
        locked = self.__time_service.begin_rtc_commit(revision, now_ms)
        if locked is not True:
            raise ValueError("staged RTC commit could not be relocked")
        try:
            civil = epoch_seconds_to_civil(utc_seconds)
            staged = self.__rtc.stage_utc_datetime(
                civil["year"],
                civil["month"],
                civil["day"],
                civil["hour"],
                civil["minute"],
                civil["second"],
            )
            staged_fields = self._sample_fields(staged)
            staged_seconds = civil_to_utc_seconds(*staged_fields)
            if staged_seconds not in (utc_seconds, utc_seconds + 1):
                raise ValueError("RTC restaged verification differs")
            self.__writes += 1
            confirmed = self.__time_service.snapshot(now_ms)
            if (
                confirmed.get("valid") is not True
                or confirmed.get("rtc_write_pending") is True
                or confirmed.get("utc_revision") != revision
                or confirmed.get("utc_seconds") != utc_seconds
            ):
                self.__commit_pending = False
                self.__commit_revision = None
                self.__last_operation = "rtc_write_stale"
                self.__last_error = None
                self._schedule(now_ms, 0)
                return self.__last_operation
            return self._commit_staged(now_ms, "rtc_write_commit")
        finally:
            released = self.__time_service.end_rtc_commit(revision)
            if released is not True:
                raise ValueError("TimeService RTC commit lock was lost")

    def step(self, now_ms):
        """Perform one due operation; never loop to catch up."""

        _require_ticks(now_ms)
        if self.__closed:
            return "closed"
        if self.__faulted:
            return "faulted"
        if (
            self.__last_step_ms is not None
            and self._ticks_difference(now_ms, self.__last_step_ms) < 0
        ):
            self._latch_fault("rtc_bridge_time_reversed")
            raise ValueError("now_ms moved backwards")
        self.__last_step_ms = now_ms
        if (
            self.__next_due_ms is not None
            and self._ticks_difference(now_ms, self.__next_due_ms) < 0
        ):
            return None

        try:
            before = self.__time_service.snapshot(now_ms)
            if self.__commit_pending:
                if before.get("rtc_write_pending") is True:
                    # A newer correction appeared while a previous commit was
                    # interrupted.  Restage that current generation instead of
                    # releasing the older value.
                    self.__commit_pending = False
                    self.__commit_revision = None
                else:
                    return self._resume_staged_commit(now_ms, before)
            if before.get("rtc_write_pending") is True:
                return self._write_pending(now_ms, before)
            return self._read_rtc(now_ms, before)
        except DS3231Error:
            self.__errors += 1
            self.__last_error = "rtc_io_failed"
            self.__last_operation = "rtc_error"
            try:
                try:
                    before = self.__time_service.snapshot(now_ms)
                    revision = before.get("rtc_write_revision")
                    if (
                        before.get("rtc_write_pending") is True
                        and type(revision) is int
                    ):
                        self.__time_service.mark_rtc_write_result(
                            False, revision, now_ms, "rtc_write_failed"
                        )
                    else:
                        self.__time_service.report_rtc_error(
                            now_ms, "rtc_read_failed"
                        )
                except MemoryError:
                    self._latch_fault("rtc_bridge_memory_error")
                    raise
                except Exception:
                    self._latch_fault("rtc_bridge_contract_error")
                    raise
                except BaseException:
                    self._latch_fault("rtc_bridge_reporting_interrupted")
                    raise
            finally:
                try:
                    self._schedule(now_ms, self.__retry_interval_ms)
                except MemoryError:
                    self._latch_fault("rtc_bridge_memory_error")
                    raise
                except Exception:
                    self._latch_fault("rtc_bridge_contract_error")
                    raise
                except BaseException:
                    self._latch_fault("rtc_bridge_scheduling_interrupted")
                    raise
            return "rtc_error"
        except MemoryError:
            self._latch_fault("rtc_bridge_memory_error")
            self._fence_time_service(now_ms, "rtc_bridge_memory_error")
            raise
        except Exception:
            self._latch_fault("rtc_bridge_contract_error")
            self._fence_time_service(now_ms, "rtc_bridge_contract_error")
            raise
        except BaseException:
            self._latch_fault("rtc_bridge_interrupted")
            self._fence_time_service(now_ms, "rtc_bridge_interrupted")
            raise

    def reset_fault(self, now_ms):
        _require_ticks(now_ms)
        if not self.__faulted:
            return False
        self.__faulted = False
        self.__last_error = None
        self.__last_step_ms = now_ms
        self.__next_due_ms = now_ms
        return True

    def deinit(self):
        self.__closed = True
        deinit = getattr(self.__rtc, "deinit", None)
        if callable(deinit):
            return deinit()
        return None

    def snapshot(self):
        return {
            "faulted": self.__faulted,
            "closed": self.__closed,
            "next_due_ms": self.__next_due_ms,
            "last_step_ms": self.__last_step_ms,
            "last_operation": self.__last_operation,
            "last_error": self.__last_error,
            "reads": self.__reads,
            "writes": self.__writes,
            "errors": self.__errors,
            "commit_pending": self.__commit_pending,
            "commit_revision": self.__commit_revision,
        }
