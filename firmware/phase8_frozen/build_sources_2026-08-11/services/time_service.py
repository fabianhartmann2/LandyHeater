"""Hardware-independent wall-clock state for the scheduler.

The service deliberately performs no RTC, I2C, network or browser I/O.  A
future adapter supplies already validated UTC samples and persists requested
corrections to the DS3231.  Internally the wall clock is advanced only from a
wrap-safe monotonic clock.

The service supports explicit fixed offsets and one small embedded production
rule for ``Europe/Zurich``.  The latter follows the CET/CEST EU transition
rule without importing an operating-system timezone database.  DS3231 and all
external corrections remain UTC-only.
"""

import time as _time


CLOCK_SOURCE_RTC = "rtc"
CLOCK_SOURCE_NTP = "ntp"
CLOCK_SOURCE_BROWSER = "browser"
CLOCK_SOURCES = (
    CLOCK_SOURCE_RTC,
    CLOCK_SOURCE_NTP,
    CLOCK_SOURCE_BROWSER,
)

CLOCK_HEALTH_INVALID = "invalid"
CLOCK_HEALTH_OK = "ok"
CLOCK_HEALTH_HOLDOVER = "holdover"

RTC_HEALTH_UNKNOWN = "unknown"
RTC_HEALTH_OK = "ok"
RTC_HEALTH_ERROR = "error"

MINIMUM_YEAR = 2000
MAXIMUM_YEAR = 2099
MINIMUM_UTC_OFFSET_MINUTES = -14 * 60
MAXIMUM_UTC_OFFSET_MINUTES = 14 * 60
DEFAULT_EVENT_CAPACITY = 16
MAX_EVENT_CAPACITY = 64
MAX_TIMEZONE_NAME_LENGTH = 64
MAX_ERROR_LENGTH = 160

TIMEZONE_RULE_FIXED = "fixed"
TIMEZONE_RULE_EUROPE_ZURICH = "europe_zurich"
TIMEZONE_RULES = (
    TIMEZONE_RULE_FIXED,
    TIMEZONE_RULE_EUROPE_ZURICH,
)
TIMEZONE_RULE_VERSION_FIXED = 1
TIMEZONE_RULE_VERSION_EUROPE_ZURICH = 1
EUROPE_ZURICH_TIMEZONE_NAME = "Europe/Zurich"
EUROPE_ZURICH_STANDARD_OFFSET_MINUTES = 60
EUROPE_ZURICH_DST_OFFSET_MINUTES = 120

_SECONDS_PER_DAY = 86400
_MILLISECONDS_PER_SECOND = 1000
_DAYS_BEFORE_MONTH = (
    0,
    31,
    59,
    90,
    120,
    151,
    181,
    212,
    243,
    273,
    304,
    334,
)


def _plain_ticks_diff(newer, older):
    return newer - older


_platform_ticks_diff = getattr(_time, "ticks_diff", _plain_ticks_diff)


def _require_integer(name, value):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("{} must be an integer".format(name))


def _require_positive_integer(name, value):
    _require_integer(name, value)
    if value <= 0:
        raise ValueError("{} must be positive".format(name))


def _require_ticks(now_ms):
    _require_integer("now_ms", now_ms)


def _bounded_text(name, value, maximum, allow_empty=False):
    if not isinstance(value, str):
        raise ValueError("{} must be a string".format(name))
    value = value.strip()
    if (not value and not allow_empty) or len(value) > maximum:
        raise ValueError("{} must be a bounded string".format(name))
    return value


def _is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year, month):
    if month == 2:
        return 29 if _is_leap_year(year) else 28
    if month in (4, 6, 9, 11):
        return 30
    return 31


def _validate_civil(year, month, day, hour, minute, second):
    for name, value in (
        ("year", year),
        ("month", month),
        ("day", day),
        ("hour", hour),
        ("minute", minute),
        ("second", second),
    ):
        _require_integer(name, value)
    if year < MINIMUM_YEAR or year > MAXIMUM_YEAR:
        raise ValueError("year must be between 2000 and 2099")
    if month < 1 or month > 12:
        raise ValueError("month must be between 1 and 12")
    if day < 1 or day > _days_in_month(year, month):
        raise ValueError("day is invalid for month")
    if hour < 0 or hour > 23:
        raise ValueError("hour must be between 0 and 23")
    if minute < 0 or minute > 59:
        raise ValueError("minute must be between 0 and 59")
    if second < 0 or second > 59:
        raise ValueError("second must be between 0 and 59")


def civil_to_utc_seconds(year, month, day, hour, minute, second):
    """Return seconds since 2000-01-01 for one UTC civil timestamp."""

    _validate_civil(year, month, day, hour, minute, second)
    days = 0
    current_year = MINIMUM_YEAR
    while current_year < year:
        days += 366 if _is_leap_year(current_year) else 365
        current_year += 1
    days += _DAYS_BEFORE_MONTH[month - 1]
    if month > 2 and _is_leap_year(year):
        days += 1
    days += day - 1
    return (
        days * _SECONDS_PER_DAY
        + hour * 3600
        + minute * 60
        + second
    )


def _seconds_to_civil(total_seconds):
    if not isinstance(total_seconds, int) or isinstance(total_seconds, bool):
        raise ValueError("seconds must be an integer")
    if total_seconds < 0:
        raise ValueError("time precedes supported range")

    days, seconds_in_day = divmod(total_seconds, _SECONDS_PER_DAY)
    year = MINIMUM_YEAR
    remaining_days = days
    while year <= MAXIMUM_YEAR:
        year_days = 366 if _is_leap_year(year) else 365
        if remaining_days < year_days:
            break
        remaining_days -= year_days
        year += 1
    if year > MAXIMUM_YEAR:
        raise ValueError("time exceeds supported range")

    month = 1
    while True:
        month_days = _days_in_month(year, month)
        if remaining_days < month_days:
            break
        remaining_days -= month_days
        month += 1

    hour, remainder = divmod(seconds_in_day, 3600)
    minute, second = divmod(remainder, 60)
    return {
        "year": year,
        "month": month,
        "day": remaining_days + 1,
        # 2000-01-01 was a Saturday; Monday is weekday zero.
        "weekday": (days + 5) % 7,
        "hour": hour,
        "minute": minute,
        "second": second,
        "local_minute_id": total_seconds // 60,
        # UTC and fixed-offset civil values are unambiguous.  A timezone
        # projection overwrites this field for the repeated Zurich hour.
        "fold": 0,
    }


def epoch_seconds_to_civil(total_seconds):
    """Return a detached civil dictionary for seconds since 2000-01-01."""

    return dict(_seconds_to_civil(total_seconds))


def timezone_rule_version(timezone_rule):
    """Return the embedded rule version for one supported timezone rule."""

    if timezone_rule == TIMEZONE_RULE_FIXED:
        return TIMEZONE_RULE_VERSION_FIXED
    if timezone_rule == TIMEZONE_RULE_EUROPE_ZURICH:
        return TIMEZONE_RULE_VERSION_EUROPE_ZURICH
    raise ValueError("unsupported timezone rule")


def _validate_timezone_rule_and_offset(
    timezone_rule, standard_utc_offset_minutes
):
    if type(timezone_rule) is not str or timezone_rule not in TIMEZONE_RULES:
        raise ValueError("unsupported timezone rule")
    _require_integer(
        "standard_utc_offset_minutes", standard_utc_offset_minutes
    )
    if not (
        MINIMUM_UTC_OFFSET_MINUTES
        <= standard_utc_offset_minutes
        <= MAXIMUM_UTC_OFFSET_MINUTES
    ):
        raise ValueError(
            "standard_utc_offset_minutes is outside supported range"
        )
    if (
        timezone_rule == TIMEZONE_RULE_EUROPE_ZURICH
        and standard_utc_offset_minutes
        != EUROPE_ZURICH_STANDARD_OFFSET_MINUTES
    ):
        raise ValueError("Europe/Zurich requires the CET standard offset")


def _validate_timezone_configuration(
    timezone_name, standard_utc_offset_minutes, timezone_rule
):
    timezone_name = _bounded_text(
        "timezone_name", timezone_name, MAX_TIMEZONE_NAME_LENGTH
    )
    _validate_timezone_rule_and_offset(
        timezone_rule, standard_utc_offset_minutes
    )
    if (
        timezone_rule == TIMEZONE_RULE_EUROPE_ZURICH
        and timezone_name != EUROPE_ZURICH_TIMEZONE_NAME
    ):
        raise ValueError("Europe/Zurich rule requires its canonical name")
    if (
        timezone_name == EUROPE_ZURICH_TIMEZONE_NAME
        and timezone_rule != TIMEZONE_RULE_EUROPE_ZURICH
    ):
        raise ValueError("Europe/Zurich name requires its canonical rule")
    return timezone_name


def _last_sunday_of_month(year, month):
    last_day = _days_in_month(year, month)
    last_seconds = civil_to_utc_seconds(year, month, last_day, 0, 0, 0)
    weekday = _seconds_to_civil(last_seconds)["weekday"]
    return last_day - ((weekday - 6) % 7)


def europe_zurich_transition_utc_seconds(year):
    """Return the CET→CEST and CEST→CET UTC transition instants."""

    _require_integer("year", year)
    if year < MINIMUM_YEAR or year > MAXIMUM_YEAR:
        raise ValueError("year must be between 2000 and 2099")
    start_day = _last_sunday_of_month(year, 3)
    end_day = _last_sunday_of_month(year, 10)
    return (
        civil_to_utc_seconds(year, 3, start_day, 1, 0, 0),
        civil_to_utc_seconds(year, 10, end_day, 1, 0, 0),
    )


def utc_seconds_to_local(
    utc_seconds,
    timezone_rule=TIMEZONE_RULE_FIXED,
    standard_utc_offset_minutes=0,
):
    """Project one UTC second into a supported local timezone.

    The result contains a detached local civil dictionary, the effective
    offset, the DST flag and the embedded rule version.  It performs no I/O
    and relies only on bounded integer calendar arithmetic.
    """

    _require_integer("utc_seconds", utc_seconds)
    _validate_timezone_rule_and_offset(
        timezone_rule, standard_utc_offset_minutes
    )
    utc_civil = _seconds_to_civil(utc_seconds)
    effective_offset = standard_utc_offset_minutes
    is_dst = False
    fold = 0
    if timezone_rule == TIMEZONE_RULE_EUROPE_ZURICH:
        start_seconds, end_seconds = europe_zurich_transition_utc_seconds(
            utc_civil["year"]
        )
        if start_seconds <= utc_seconds < end_seconds:
            effective_offset = EUROPE_ZURICH_DST_OFFSET_MINUTES
            is_dst = True
        if end_seconds <= utc_seconds < end_seconds + 3600:
            fold = 1

    local = _seconds_to_civil(utc_seconds + effective_offset * 60)
    local["fold"] = fold
    return {
        "local": local,
        "utc_offset_minutes": effective_offset,
        "is_dst": is_dst,
        "timezone_rule_version": timezone_rule_version(timezone_rule),
    }


def local_civil_to_utc_occurrences(
    year,
    month,
    day,
    hour,
    minute,
    second,
    timezone_rule=TIMEZONE_RULE_FIXED,
    standard_utc_offset_minutes=0,
):
    """Return zero, one or two exact UTC interpretations of local civil time."""

    local_seconds = civil_to_utc_seconds(
        year, month, day, hour, minute, second
    )
    _validate_timezone_rule_and_offset(
        timezone_rule, standard_utc_offset_minutes
    )
    if timezone_rule == TIMEZONE_RULE_EUROPE_ZURICH:
        offsets = (
            EUROPE_ZURICH_DST_OFFSET_MINUTES,
            EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
        )
    else:
        offsets = (standard_utc_offset_minutes,)

    occurrences = []
    for offset in offsets:
        candidate = local_seconds - offset * 60
        try:
            projection = utc_seconds_to_local(
                candidate, timezone_rule, standard_utc_offset_minutes
            )
        except ValueError:
            continue
        local = projection["local"]
        if (
            local["year"] == year
            and local["month"] == month
            and local["day"] == day
            and local["hour"] == hour
            and local["minute"] == minute
            and local["second"] == second
            and projection["utc_offset_minutes"] == offset
        ):
            occurrences.append(
                {
                    "utc_seconds": candidate,
                    "utc_offset_minutes": offset,
                    "is_dst": projection["is_dst"],
                    "fold": local["fold"],
                }
            )
    return tuple(occurrences)


def is_timezone_transition_instant(
    utc_seconds,
    timezone_rule=TIMEZONE_RULE_FIXED,
    standard_utc_offset_minutes=0,
):
    """Return whether this exact UTC second changes the effective offset."""

    _require_integer("utc_seconds", utc_seconds)
    _validate_timezone_rule_and_offset(
        timezone_rule, standard_utc_offset_minutes
    )
    if timezone_rule == TIMEZONE_RULE_FIXED:
        _seconds_to_civil(utc_seconds)
        return False
    year = _seconds_to_civil(utc_seconds)["year"]
    start_seconds, end_seconds = europe_zurich_transition_utc_seconds(year)
    return utc_seconds in (start_seconds, end_seconds)


class TimeService:
    """Keep validated UTC wall time anchored to monotonic milliseconds."""

    def __init__(
        self,
        ticks_diff=None,
        timezone_name="UTC",
        utc_offset_minutes=0,
        event_capacity=DEFAULT_EVENT_CAPACITY,
        timezone_rule=TIMEZONE_RULE_FIXED,
    ):
        if ticks_diff is None:
            ticks_diff = _platform_ticks_diff
        if not callable(ticks_diff):
            raise ValueError("ticks_diff must be callable")
        _require_positive_integer("event_capacity", event_capacity)
        if event_capacity > MAX_EVENT_CAPACITY:
            raise ValueError("event_capacity exceeds the hard clock bound")
        timezone_name = _validate_timezone_configuration(
            timezone_name, utc_offset_minutes, timezone_rule
        )

        self._ticks_diff = ticks_diff
        self._timezone_name = timezone_name
        self._utc_offset_minutes = utc_offset_minutes
        self._timezone_rule = timezone_rule
        self._timezone_revision = 0

        self._valid = False
        self._utc_epoch_ms = None
        self._anchor_ticks_ms = None
        self._sync_age_ms = None
        self._source = None
        self._last_sync_utc_seconds = None
        self._clock_revision = 0
        self._utc_revision = 0
        self._rtc_health = RTC_HEALTH_UNKNOWN
        self._rtc_write_pending = False
        self._rtc_write_revision = None
        self._rtc_commit_revision = None
        self._last_error = None

        self._events = []
        self._event_capacity = event_capacity
        self.events_dropped = 0
        self.event_errors = 0

    @property
    def timezone_name(self):
        return self._timezone_name

    @property
    def utc_offset_minutes(self):
        """Return the configured standard offset, not a seasonal projection."""

        return self._utc_offset_minutes

    @property
    def standard_utc_offset_minutes(self):
        return self._utc_offset_minutes

    @property
    def timezone_rule(self):
        return self._timezone_rule

    @property
    def timezone_rule_version(self):
        return timezone_rule_version(self._timezone_rule)

    @property
    def clock_revision(self):
        return self._clock_revision

    @property
    def timezone_revision(self):
        return self._timezone_revision

    @property
    def valid(self):
        return self._valid

    def _emit(self, code, now_ms, details=None):
        try:
            event = {"code": code, "at_ms": now_ms}
            if details is not None:
                event["details"] = dict(details)
            if len(self._events) >= self._event_capacity:
                self._events.pop(0)
                self.events_dropped += 1
            self._events.append(event)
        except Exception:
            self.event_errors += 1
            return False
        return True

    def drain_events(self):
        events = self._events
        self._events = []
        return events

    def _advance(self, now_ms):
        _require_ticks(now_ms)
        if not self._valid:
            return
        elapsed = self._ticks_diff(now_ms, self._anchor_ticks_ms)
        if elapsed < 0:
            raise ValueError("now_ms precedes the current clock anchor")
        self._utc_epoch_ms += elapsed
        self._sync_age_ms += elapsed
        self._anchor_ticks_ms = now_ms

    def _health(self):
        if not self._valid:
            return CLOCK_HEALTH_INVALID
        if (
            self._rtc_health == RTC_HEALTH_ERROR
            or self._rtc_commit_revision is not None
        ):
            return CLOCK_HEALTH_HOLDOVER
        return CLOCK_HEALTH_OK

    def set_utc_datetime(
        self,
        year,
        month,
        day,
        hour,
        minute,
        second,
        source,
        now_ms,
    ):
        """Establish or correct UTC time and increment the clock revision.

        This is intentionally not the periodic RTC-health refresh API.  Every
        call is a scheduling fence, including an RTC-sourced correction.
        """

        _require_ticks(now_ms)
        if self._rtc_commit_revision is not None:
            raise RuntimeError("RTC commit is in progress")
        if source not in CLOCK_SOURCES:
            raise ValueError("unsupported clock source")
        utc_seconds = civil_to_utc_seconds(
            year, month, day, hour, minute, second
        )
        # Ensure the configured local projection stays inside the supported
        # civil range before committing any clock state.
        utc_seconds_to_local(
            utc_seconds, self._timezone_rule, self._utc_offset_minutes
        )
        if self._valid:
            self._advance(now_ms)

        self._utc_epoch_ms = utc_seconds * _MILLISECONDS_PER_SECOND
        self._anchor_ticks_ms = now_ms
        self._sync_age_ms = 0
        self._source = source
        self._last_sync_utc_seconds = utc_seconds
        self._valid = True
        self._clock_revision += 1
        self._utc_revision += 1
        self._last_error = None
        if source == CLOCK_SOURCE_RTC:
            self._rtc_health = RTC_HEALTH_OK
            self._rtc_write_pending = False
            self._rtc_write_revision = None
        else:
            self._rtc_write_pending = True
            self._rtc_write_revision = self._utc_revision
        self._emit(
            "clock_synchronized",
            now_ms,
            {"source": source, "clock_revision": self._clock_revision},
        )
        return True

    def refresh_rtc_datetime(
        self, year, month, day, hour, minute, second, now_ms
    ):
        """Confirm a matching periodic RTC reading without a clock fence.

        A mismatching reading is treated as a real RTC correction and uses
        :meth:`set_utc_datetime`, which increments ``clock_revision``.
        """

        _require_ticks(now_ms)
        if self._rtc_commit_revision is not None:
            raise RuntimeError("RTC commit is in progress")
        observed_seconds = civil_to_utc_seconds(
            year, month, day, hour, minute, second
        )
        utc_seconds_to_local(
            observed_seconds, self._timezone_rule, self._utc_offset_minutes
        )
        if not self._valid:
            return self.set_utc_datetime(
                year,
                month,
                day,
                hour,
                minute,
                second,
                CLOCK_SOURCE_RTC,
                now_ms,
            )
        self._advance(now_ms)
        expected_seconds = self._utc_epoch_ms // _MILLISECONDS_PER_SECOND
        difference = observed_seconds - expected_seconds
        if difference < -1 or difference > 1:
            if self._rtc_write_pending:
                self.report_rtc_error(
                    now_ms, "rtc_refresh_conflicts_pending_write"
                )
                return False
            return self.set_utc_datetime(
                year,
                month,
                day,
                hour,
                minute,
                second,
                CLOCK_SOURCE_RTC,
                now_ms,
            )
        if not self._rtc_write_pending:
            recovered = self._rtc_health != RTC_HEALTH_OK
            self._rtc_health = RTC_HEALTH_OK
            self._last_error = None
            if recovered:
                self._emit("rtc_refresh_recovered", now_ms)
        return False

    def configure_timezone(
        self,
        timezone_name,
        utc_offset_minutes,
        now_ms,
        timezone_rule=TIMEZONE_RULE_FIXED,
    ):
        """Atomically replace the global timezone configuration."""

        if self._rtc_commit_revision is not None:
            raise RuntimeError("RTC commit is in progress")
        timezone_name = _validate_timezone_configuration(
            timezone_name, utc_offset_minutes, timezone_rule
        )
        _require_ticks(now_ms)
        if self._valid:
            self._advance(now_ms)
            utc_seconds = self._utc_epoch_ms // _MILLISECONDS_PER_SECOND
            utc_seconds_to_local(
                utc_seconds, timezone_rule, utc_offset_minutes
            )
        if (
            timezone_name == self._timezone_name
            and utc_offset_minutes == self._utc_offset_minutes
            and timezone_rule == self._timezone_rule
        ):
            return False

        self._timezone_name = timezone_name
        self._utc_offset_minutes = utc_offset_minutes
        self._timezone_rule = timezone_rule
        self._timezone_revision += 1
        self._clock_revision += 1
        self._emit(
            "timezone_changed",
            now_ms,
            {
                "timezone": timezone_name,
                "timezone_rule": timezone_rule,
                "timezone_rule_version": timezone_rule_version(
                    timezone_rule
                ),
                "standard_utc_offset_minutes": utc_offset_minutes,
                "timezone_revision": self._timezone_revision,
            },
        )
        return True

    def report_rtc_error(self, now_ms, reason="rtc_error"):
        """Record an RTC fault while retaining a valid monotonic holdover."""

        _require_ticks(now_ms)
        reason = _bounded_text(
            "reason", reason, MAX_ERROR_LENGTH, allow_empty=False
        )
        if self._valid:
            self._advance(now_ms)
        changed = self._rtc_health != RTC_HEALTH_ERROR
        self._rtc_health = RTC_HEALTH_ERROR
        self._last_error = reason
        if changed:
            self._emit("rtc_error", now_ms, {"reason": reason})
        return changed

    def mark_rtc_write_result(
        self, success, write_revision, now_ms, reason="rtc_write"
    ):
        """Acknowledge persistence of an NTP/browser correction."""

        if not isinstance(success, bool):
            raise ValueError("success must be a boolean")
        _require_integer("write_revision", write_revision)
        _require_ticks(now_ms)
        if self._valid:
            self._advance(now_ms)
        if (
            not self._rtc_write_pending
            or write_revision != self._rtc_write_revision
        ):
            return False
        if success:
            self._rtc_write_pending = False
            self._rtc_write_revision = None
            self._rtc_health = RTC_HEALTH_OK
            self._last_error = None
            self._emit("rtc_write_confirmed", now_ms)
            return True
        return self.report_rtc_error(now_ms, reason)

    def begin_rtc_commit(self, write_revision, now_ms):
        """Lock one exact pending revision across its hardware trust release.

        The lock is deliberately allocation-free after argument validation.
        Clock corrections, invalidation and timezone changes are rejected until
        :meth:`end_rtc_commit` releases it, so no Python callback can replace
        the acknowledged generation between RTC staging and commit.
        """

        _require_integer("write_revision", write_revision)
        _require_ticks(now_ms)
        if self._rtc_commit_revision is not None:
            return False
        self._rtc_commit_revision = write_revision
        try:
            if self._valid:
                self._advance(now_ms)
            if (
                not self._valid
                or self._utc_revision != write_revision
                or (
                    self._rtc_write_pending
                    and self._rtc_write_revision != write_revision
                )
            ):
                self._rtc_commit_revision = None
                return False
        except BaseException:
            self._rtc_commit_revision = None
            raise
        return True

    def mark_rtc_commit_recovered(self, write_revision, now_ms):
        """Confirm that one locked UTC revision is trusted in hardware."""

        _require_integer("write_revision", write_revision)
        _require_ticks(now_ms)
        if (
            self._rtc_commit_revision != write_revision
            or not self._valid
            or self._utc_revision != write_revision
            or self._rtc_write_pending
        ):
            return False
        self._advance(now_ms)
        recovered = self._rtc_health != RTC_HEALTH_OK
        self._rtc_health = RTC_HEALTH_OK
        self._last_error = None
        if recovered:
            self._emit("rtc_commit_recovered", now_ms)
        return True

    def end_rtc_commit(self, write_revision):
        """Release one exact RTC commit lock."""

        _require_integer("write_revision", write_revision)
        if self._rtc_commit_revision != write_revision:
            return False
        self._rtc_commit_revision = None
        return True

    def invalidate(self, now_ms, reason="clock_invalid"):
        """Fail closed until another explicit clock sample is accepted."""

        _require_ticks(now_ms)
        if self._rtc_commit_revision is not None:
            raise RuntimeError("RTC commit is in progress")
        reason = _bounded_text(
            "reason", reason, MAX_ERROR_LENGTH, allow_empty=False
        )
        if self._valid:
            self._advance(now_ms)
        changed = self._valid
        self._valid = False
        self._utc_epoch_ms = None
        self._anchor_ticks_ms = None
        self._sync_age_ms = None
        self._source = None
        self._clock_revision += 1
        self._utc_revision += 1
        self._rtc_health = RTC_HEALTH_ERROR
        self._rtc_write_pending = False
        self._rtc_write_revision = None
        self._last_error = reason
        self._emit(
            "clock_invalid",
            now_ms,
            {"reason": reason, "clock_revision": self._clock_revision},
        )
        return changed

    def snapshot(self, now_ms):
        """Return a detached coherent UTC/local clock snapshot."""

        _require_ticks(now_ms)
        if self._valid:
            self._advance(now_ms)
        base = {
            "valid": self._valid,
            "health": self._health(),
            "rtc_health": self._rtc_health,
            "rtc_write_pending": self._rtc_write_pending,
            "rtc_write_revision": self._rtc_write_revision,
            "rtc_commit_revision": self._rtc_commit_revision,
            "source": self._source,
            "clock_revision": self._clock_revision,
            "utc_revision": self._utc_revision,
            "timezone_revision": self._timezone_revision,
            "timezone": self._timezone_name,
            "timezone_rule": self._timezone_rule,
            "timezone_rule_version": timezone_rule_version(
                self._timezone_rule
            ),
            "standard_utc_offset_minutes": self._utc_offset_minutes,
            "utc_offset_minutes": self._utc_offset_minutes,
            "is_dst": None,
            "sync_age_ms": self._sync_age_ms,
            "last_sync_utc_seconds": self._last_sync_utc_seconds,
            "last_error": self._last_error,
            "events_pending": len(self._events),
            "events_dropped": self.events_dropped,
            "event_errors": self.event_errors,
            "utc_seconds": None,
            "local": None,
        }
        if not self._valid:
            return base
        utc_seconds = self._utc_epoch_ms // _MILLISECONDS_PER_SECOND
        base["utc_seconds"] = utc_seconds
        try:
            projection = utc_seconds_to_local(
                utc_seconds, self._timezone_rule, self._utc_offset_minutes
            )
            base["local"] = projection["local"]
            base["utc_offset_minutes"] = projection[
                "utc_offset_minutes"
            ]
            base["is_dst"] = projection["is_dst"]
        except ValueError:
            self.invalidate(now_ms, "clock_range_exceeded")
            return self.snapshot(now_ms)
        return base
