"""Hardware-independent, fail-closed weekly timer scheduler.

This module creates short-lived application intents only.  It deliberately
does not import or call HeaterController, the Autoterm protocol, UART, RTC or
any hardware module.  A later composition gateway must validate an intent's
monotonic deadline before applying it to requested application state.

The scheduler has conservative clock semantics:

* the first valid clock observation only establishes a baseline;
* clock corrections, timezone changes and unexpected jumps create a fence;
* missed occurrences are never caught up;
* a local civil occurrence is consumed before an intent is returned;
* collisions and unavailable control consume the occurrence without retry.
* timezone transition minutes are fenced and the repeated fold is start-free.
"""

import time as _time

from app.application_state import validate_start_request
from services.time_service import (
    CLOCK_SOURCES,
    CLOCK_HEALTH_HOLDOVER,
    CLOCK_HEALTH_OK,
    EUROPE_ZURICH_TIMEZONE_NAME,
    MAXIMUM_UTC_OFFSET_MINUTES,
    MAX_TIMEZONE_NAME_LENGTH,
    MINIMUM_UTC_OFFSET_MINUTES,
    RTC_HEALTH_OK,
    TIMEZONE_RULE_EUROPE_ZURICH,
    TIMEZONE_RULES,
    epoch_seconds_to_civil,
    is_timezone_transition_instant,
    local_civil_to_utc_occurrences,
    utc_seconds_to_local,
)


TIMER_SOURCE = "timer"
DEFAULT_MAX_TIMERS = 32
DEFAULT_EVENT_CAPACITY = 16
MAX_EVENT_CAPACITY = 64
DEFAULT_INTENT_VALID_MS = 5000
MAX_INTENT_VALID_MS = 60000
DEFAULT_MAX_CLOCK_STEP_SECONDS = 90
MAX_TIMER_ID_LENGTH = 64
MAX_TIMER_NAME_LENGTH = 80
MAX_OCCURRENCE_KEY_LENGTH = 128
_ASCII_DIGITS = "0123456789"
_MINIMUM_PERSISTENT_LOCAL_MINUTE_ID = 0
_MAXIMUM_PERSISTENT_LOCAL_MINUTE_ID = 52595999

PERSISTENT_HISTORY_STATUS_CONSUMED = "consumed"
PERSISTENT_HISTORY_STATUS_OVERRIDDEN = "overridden"

_PERSISTENT_HISTORY_FIELDS = frozenset(
    ("consumed_local_high_water", "occurrences")
)
_PERSISTENT_OCCURRENCE_FIELDS = frozenset(
    (
        "timer_id",
        "occurrence_key",
        "local_minute_id",
        "status",
        "overridden",
    )
)
_PERSISTENT_HISTORY_STATUSES = frozenset(
    (
        PERSISTENT_HISTORY_STATUS_CONSUMED,
        PERSISTENT_HISTORY_STATUS_OVERRIDDEN,
    )
)
_INTERNAL_CONSUMED_STATUSES = frozenset(
    (
        PERSISTENT_HISTORY_STATUS_CONSUMED,
        "conflict",
        "suppressed_busy",
        "intent_created",
        "expired",
        "authorization_rejected",
        "authorized_pending",
        "accepted",
        "application_failed",
        "completed",
    )
)

_TIMER_FIELDS = frozenset(
    (
        "id",
        "name",
        "enabled",
        "weekdays",
        "start",
        "mode",
        "target_temperature",
        "power_level",
        "runtime_minutes",
    )
)


def _plain_ticks_diff(newer, older):
    return newer - older


def _plain_ticks_add(ticks, delta):
    return ticks + delta


_platform_ticks_diff = getattr(_time, "ticks_diff", _plain_ticks_diff)
_platform_ticks_add = getattr(_time, "ticks_add", _plain_ticks_add)


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


def _parse_start(value):
    if (
        not isinstance(value, str)
        or len(value) != 5
        or value[2] != ":"
        or value[0] not in _ASCII_DIGITS
        or value[1] not in _ASCII_DIGITS
        or value[3] not in _ASCII_DIGITS
        or value[4] not in _ASCII_DIGITS
    ):
        raise ValueError("start must use exact HH:MM format")
    hour = int(value[:2])
    minute = int(value[3:])
    if hour > 23 or minute > 59:
        raise ValueError("start time is outside 00:00-23:59")
    return hour, minute


def _date_key(local):
    return "{:04d}-{:02d}-{:02d}".format(
        local["year"], local["month"], local["day"]
    )


def _occurrence_key(timer, local):
    return "{}|{}|{}".format(
        timer["id"], _date_key(local), timer["start"]
    )


def _require_exact_fields(name, value, expected):
    if type(value) is not dict:
        raise ValueError("{} must be a dictionary".format(name))
    for key in value:
        if type(key) is not str:
            raise ValueError("{} fields must be strings".format(name))
        if key not in expected:
            raise ValueError("unknown {} field: {}".format(name, key))
    for key in expected:
        if key not in value:
            raise ValueError("missing {} field: {}".format(name, key))


def _canonical_persistent_text(name, value, maximum):
    if type(value) is not str:
        raise ValueError("{} must be a string".format(name))
    if not value or len(value) > maximum or value != value.strip():
        raise ValueError("{} must be a canonical bounded string".format(name))
    return value


def _persistent_local_civil(name, value):
    if type(value) is not int:
        raise ValueError("{} must be an integer".format(name))
    if not (
        _MINIMUM_PERSISTENT_LOCAL_MINUTE_ID
        <= value
        <= _MAXIMUM_PERSISTENT_LOCAL_MINUTE_ID
    ):
        raise ValueError("{} is outside the supported civil range".format(name))
    try:
        return epoch_seconds_to_civil(value * 60)
    except ValueError:
        raise ValueError("{} is outside the supported civil range".format(name))


def _normalize_persistent_occurrence(definition):
    _require_exact_fields(
        "persistent occurrence", definition, _PERSISTENT_OCCURRENCE_FIELDS
    )
    timer_id = _canonical_persistent_text(
        "persistent timer_id", definition["timer_id"], MAX_TIMER_ID_LENGTH
    )
    if "|" in timer_id:
        raise ValueError("persistent timer_id contains a reserved delimiter")
    occurrence_key = _canonical_persistent_text(
        "persistent occurrence_key",
        definition["occurrence_key"],
        MAX_OCCURRENCE_KEY_LENGTH,
    )
    local_minute_id = definition["local_minute_id"]
    local = _persistent_local_civil(
        "persistent local_minute_id", local_minute_id
    )
    expected_key = "{}|{}|{:02d}:{:02d}".format(
        timer_id,
        _date_key(local),
        local["hour"],
        local["minute"],
    )
    if occurrence_key != expected_key:
        raise ValueError(
            "persistent occurrence_key does not match local_minute_id"
        )

    status = definition["status"]
    if type(status) is not str or status not in _PERSISTENT_HISTORY_STATUSES:
        raise ValueError("persistent occurrence status is invalid")
    overridden = definition["overridden"]
    if type(overridden) is not bool:
        raise ValueError("persistent overridden must be a boolean")
    expected_overridden = (
        status == PERSISTENT_HISTORY_STATUS_OVERRIDDEN
    )
    if overridden is not expected_overridden:
        raise ValueError("persistent status and overridden disagree")
    return {
        "timer_id": timer_id,
        "occurrence_key": occurrence_key,
        "local_minute_id": local_minute_id,
        "status": status,
        "overridden": overridden,
    }


def _normalize_persistent_history(history, maximum_occurrences):
    _require_exact_fields(
        "persistent history", history, _PERSISTENT_HISTORY_FIELDS
    )
    high_water = history["consumed_local_high_water"]
    if high_water is not None:
        _persistent_local_civil(
            "consumed_local_high_water", high_water
        )

    definitions = history["occurrences"]
    if type(definitions) is not list:
        raise ValueError("persistent occurrences must be a list")
    if len(definitions) > maximum_occurrences:
        raise ValueError("too many persistent occurrences")

    normalized = []
    used_timer_ids = set()
    used_occurrence_keys = set()
    for definition in definitions:
        record = _normalize_persistent_occurrence(definition)
        timer_id = record["timer_id"]
        occurrence_key = record["occurrence_key"]
        if timer_id in used_timer_ids:
            raise ValueError("persistent timer_ids must be unique")
        if occurrence_key in used_occurrence_keys:
            raise ValueError("persistent occurrence_keys must be unique")
        if high_water is None or record["local_minute_id"] > high_water:
            raise ValueError(
                "persistent occurrence exceeds consumed_local_high_water"
            )
        used_timer_ids.add(timer_id)
        used_occurrence_keys.add(occurrence_key)
        normalized.append(record)

    normalized.sort(key=lambda record: record["timer_id"])
    return {
        "consumed_local_high_water": high_water,
        "occurrences": normalized,
    }


class StartIntent:
    """Read-only start request with a short monotonic validity window."""

    __slots__ = (
        "_occurrence_key",
        "_timer_id",
        "_timer_revision",
        "_mode",
        "_target_temperature",
        "_power_level",
        "_runtime_minutes",
        "_created_at_ms",
        "_not_after_ms",
        "_local_date",
        "_start",
        "_owner",
        "_creation_token",
        "_sealed",
    )

    def __init__(
        self,
        timer,
        occurrence_key,
        created_at_ms,
        not_after_ms,
        owner,
        creation_token,
    ):
        self._occurrence_key = occurrence_key
        self._timer_id = timer["id"]
        self._timer_revision = timer["revision"]
        self._mode = timer["mode"]
        self._target_temperature = timer["target_temperature"]
        self._power_level = timer["power_level"]
        self._runtime_minutes = timer["runtime_minutes"]
        self._created_at_ms = created_at_ms
        self._not_after_ms = not_after_ms
        parts = occurrence_key.split("|")
        self._local_date = parts[1]
        self._start = timer["start"]
        self._owner = owner
        self._creation_token = creation_token
        self._sealed = True

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("StartIntent is read-only")
        object.__setattr__(self, name, value)

    @property
    def occurrence_key(self):
        return self._occurrence_key

    @property
    def timer_id(self):
        return self._timer_id

    @property
    def timer_revision(self):
        return self._timer_revision

    @property
    def mode(self):
        return self._mode

    @property
    def target_temperature(self):
        return self._target_temperature

    @property
    def power_level(self):
        return self._power_level

    @property
    def runtime_minutes(self):
        return self._runtime_minutes

    @property
    def source(self):
        return TIMER_SOURCE

    @property
    def created_at_ms(self):
        return self._created_at_ms

    @property
    def not_after_ms(self):
        return self._not_after_ms

    def snapshot(self):
        return {
            "occurrence_key": self._occurrence_key,
            "timer_id": self._timer_id,
            "timer_revision": self._timer_revision,
            "mode": self._mode,
            "target_temperature": self._target_temperature,
            "power_level": self._power_level,
            "runtime_minutes": self._runtime_minutes,
            "source": TIMER_SOURCE,
            "created_at_ms": self._created_at_ms,
            "not_after_ms": self._not_after_ms,
            "local_date": self._local_date,
            "start": self._start,
        }


class AuthorizedStartIntent:
    """Fresh canonical token returned immediately before application."""

    __slots__ = (
        "_intent",
        "_token",
        "_epoch",
        "_owner",
        "_record",
        "_sealed",
    )

    def __init__(self, intent, token, epoch, owner, record):
        self._intent = intent
        self._token = token
        self._epoch = epoch
        self._owner = owner
        self._record = record
        self._sealed = True

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise AttributeError("AuthorizedStartIntent is read-only")
        object.__setattr__(self, name, value)

    @property
    def authorization_token(self):
        return self._token

    @property
    def occurrence_key(self):
        return self._intent.occurrence_key

    @property
    def timer_id(self):
        return self._intent.timer_id

    @property
    def timer_revision(self):
        return self._intent.timer_revision

    @property
    def mode(self):
        return self._intent.mode

    @property
    def target_temperature(self):
        return self._intent.target_temperature

    @property
    def power_level(self):
        return self._intent.power_level

    @property
    def runtime_minutes(self):
        return self._intent.runtime_minutes

    @property
    def source(self):
        return self._intent.source

    @property
    def not_after_ms(self):
        return self._intent.not_after_ms

    @property
    def created_at_ms(self):
        return self._intent.created_at_ms

    def snapshot(self):
        result = self._intent.snapshot()
        result["authorization_token"] = self._token
        result["authorization_epoch"] = self._epoch
        return result


class Scheduler:
    """Evaluate bounded weekly timers without performing control I/O."""

    def __init__(
        self,
        time_service,
        maximum_runtime_minutes,
        ticks_diff=None,
        ticks_add=None,
        max_timers=DEFAULT_MAX_TIMERS,
        event_capacity=DEFAULT_EVENT_CAPACITY,
        intent_valid_ms=DEFAULT_INTENT_VALID_MS,
        max_clock_step_seconds=DEFAULT_MAX_CLOCK_STEP_SECONDS,
    ):
        if not callable(getattr(time_service, "snapshot", None)):
            raise ValueError("time_service must provide snapshot()")
        if (ticks_diff is None) != (ticks_add is None):
            raise ValueError("ticks_diff and ticks_add must be provided together")
        if ticks_diff is None:
            ticks_diff = _platform_ticks_diff
            ticks_add = _platform_ticks_add
        if not callable(ticks_diff) or not callable(ticks_add):
            raise ValueError("tick helpers must be callable")
        for name, value in (
            ("maximum_runtime_minutes", maximum_runtime_minutes),
            ("max_timers", max_timers),
            ("event_capacity", event_capacity),
            ("intent_valid_ms", intent_valid_ms),
            ("max_clock_step_seconds", max_clock_step_seconds),
        ):
            _require_positive_integer(name, value)
        if intent_valid_ms > MAX_INTENT_VALID_MS:
            raise ValueError("intent_valid_ms exceeds the safe tick window")
        if max_timers > DEFAULT_MAX_TIMERS:
            raise ValueError("max_timers exceeds the hard scheduler bound")
        if event_capacity > MAX_EVENT_CAPACITY:
            raise ValueError("event_capacity exceeds the hard scheduler bound")

        self._time_service = time_service
        self._ticks_diff = ticks_diff
        self._ticks_add = ticks_add
        self._maximum_runtime_minutes = maximum_runtime_minutes
        self._max_timers = max_timers
        self._event_capacity = event_capacity
        self._intent_valid_ms = intent_valid_ms
        self._max_clock_step_seconds = max_clock_step_seconds

        self._timers = ()
        self._configuration_revision = 0
        self._authorization_epoch = 0
        self._authorization_sequence = 0
        self._intent_sequence = 0
        self._intent_capability = object()
        self._authorization_capability = object()
        self._occurrences = {}
        self._consumed_local_high_water = None
        self._persistent_history_restored = False
        self._active_occurrence_key = None
        self._active_occurrence = None
        self._last_override = None
        self._armed = False
        self._faulted = False
        self._baseline = None
        self._configuration_fence_pending = False
        self._last_step_ms = None
        self._last_error = None

        self._events = []
        self.events_dropped = 0
        self.event_errors = 0

    @property
    def maximum_runtime_minutes(self):
        return self._maximum_runtime_minutes

    @property
    def max_timers(self):
        return self._max_timers

    @property
    def armed(self):
        return self._armed

    @property
    def faulted(self):
        return self._faulted

    @property
    def active_occurrence_key(self):
        """Return the immutable active timer key without snapshot allocation."""

        return self._active_occurrence_key

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

    def _advance_authorization_epoch(self):
        try:
            next_epoch = self._authorization_epoch + 1
        except MemoryError:
            self._faulted = True
            self._last_error = "scheduler_authorization_epoch_memory_error"
            raise
        self._authorization_epoch = next_epoch

    def _latch_fault(self, code):
        newly_faulted = not self._faulted
        self._faulted = True
        self._last_error = code
        if newly_faulted:
            try:
                self._advance_authorization_epoch()
            except MemoryError:
                # The fail-closed latch is already committed.  Preserve the
                # original diagnostic and never let epoch allocation failure
                # re-arm the scheduler or mask the primary exception.
                self._last_error = code

    def drain_events(self):
        events = self._events
        self._events = []
        return events

    def _normalize_timer(self, definition, revision):
        if not isinstance(definition, dict):
            raise ValueError("each timer must be a dictionary")
        unknown = set(definition) - _TIMER_FIELDS
        required = _TIMER_FIELDS - frozenset(("name",))
        missing = required - set(definition)
        if unknown:
            raise ValueError("unknown timer field: {}".format(sorted(unknown)[0]))
        if missing:
            raise ValueError("missing timer field: {}".format(sorted(missing)[0]))

        timer_id = _bounded_text("timer id", definition["id"], MAX_TIMER_ID_LENGTH)
        if "|" in timer_id:
            raise ValueError("timer id contains a reserved delimiter")
        name = definition.get("name", "")
        if name is None:
            name = ""
        name = _bounded_text("timer name", name, MAX_TIMER_NAME_LENGTH, True)
        enabled = definition["enabled"]
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")

        weekdays = definition["weekdays"]
        if not isinstance(weekdays, (list, tuple)) or not weekdays:
            raise ValueError("weekdays must be a non-empty list or tuple")
        if len(weekdays) > 7:
            raise ValueError("weekdays contains too many values")
        normalized_weekdays = []
        for weekday in weekdays:
            _require_integer("weekday", weekday)
            if weekday < 0 or weekday > 6:
                raise ValueError("weekday must be between 0 and 6")
            if weekday in normalized_weekdays:
                raise ValueError("weekdays must be unique")
            normalized_weekdays.append(weekday)
        normalized_weekdays.sort()

        hour, minute = _parse_start(definition["start"])
        mode = definition["mode"]
        target = definition["target_temperature"]
        power = definition["power_level"]
        runtime = definition["runtime_minutes"]
        validate_start_request(
            mode,
            target,
            power,
            runtime,
            TIMER_SOURCE,
            self._maximum_runtime_minutes,
        )
        return {
            "id": timer_id,
            "name": name,
            "enabled": enabled,
            "weekdays": tuple(normalized_weekdays),
            "start": definition["start"],
            "start_hour": hour,
            "start_minute": minute,
            "mode": mode,
            "target_temperature": target,
            "power_level": power,
            "runtime_minutes": runtime,
            "revision": revision,
        }

    def replace_timers(self, definitions):
        """Atomically replace all timer definitions."""

        if not isinstance(definitions, (list, tuple)):
            raise ValueError("timer definitions must be a list or tuple")
        if len(definitions) > self._max_timers:
            raise ValueError("too many timer definitions")
        revision = self._configuration_revision + 1
        normalized = []
        used_ids = set()
        for definition in definitions:
            timer = self._normalize_timer(definition, revision)
            if timer["id"] in used_ids:
                raise ValueError("timer IDs must be unique")
            used_ids.add(timer["id"])
            normalized.append(timer)

        # Allocate every replacement structure before committing any part of
        # the new configuration.  OOM therefore leaves the old config and its
        # fence state intact.
        retained_occurrences = {
            timer_id: record
            for timer_id, record in self._occurrences.items()
            if timer_id in used_ids
        }
        committed_timers = tuple(normalized)
        self._timers = committed_timers
        self._occurrences = retained_occurrences
        self._configuration_revision = revision
        if self._armed:
            self._configuration_fence_pending = True
            self._advance_authorization_epoch()
        return True

    def export_persistent_history(self):
        """Return detached, bounded at-most-once latches for persistence.

        Every in-memory occurrence has already been consumed before control
        can be attempted.  Pending and accepted hand-offs are consequently
        exported as consumed, never as a resumable operation.  Monotonic
        timestamps, capabilities, tokens and active-session state are not
        part of this format.
        """

        if len(self._occurrences) > self._max_timers:
            raise RuntimeError("scheduler occurrence history exceeds its bound")
        high_water = self._consumed_local_high_water
        if high_water is not None:
            _persistent_local_civil(
                "consumed_local_high_water", high_water
            )

        occurrences = []
        for mapped_timer_id, record in self._occurrences.items():
            if type(record) is not dict:
                raise RuntimeError("scheduler occurrence record is invalid")
            timer_id = record.get("timer_id")
            if type(mapped_timer_id) is not str or mapped_timer_id != timer_id:
                raise RuntimeError("scheduler occurrence timer_id is invalid")
            status = record.get("status")
            overridden = record.get("overridden")
            if status == PERSISTENT_HISTORY_STATUS_OVERRIDDEN:
                if overridden is not True:
                    raise RuntimeError(
                        "scheduler overridden occurrence is inconsistent"
                    )
                persistent_status = PERSISTENT_HISTORY_STATUS_OVERRIDDEN
                persistent_overridden = True
            elif status in _INTERNAL_CONSUMED_STATUSES:
                if overridden is not False:
                    raise RuntimeError(
                        "scheduler consumed occurrence is inconsistent"
                    )
                persistent_status = PERSISTENT_HISTORY_STATUS_CONSUMED
                persistent_overridden = False
            else:
                raise RuntimeError("scheduler occurrence status is invalid")

            try:
                persistent = _normalize_persistent_occurrence(
                    {
                        "timer_id": timer_id,
                        "occurrence_key": record.get("occurrence_key"),
                        "local_minute_id": record.get("local_minute_id"),
                        "status": persistent_status,
                        "overridden": persistent_overridden,
                    }
                )
            except ValueError as exc:
                raise RuntimeError(
                    "scheduler occurrence cannot be persisted: {}".format(exc)
                )
            if (
                high_water is None
                or persistent["local_minute_id"] > high_water
            ):
                raise RuntimeError(
                    "scheduler occurrence exceeds its global high-water mark"
                )
            occurrences.append(persistent)

        occurrences.sort(key=lambda record: record["timer_id"])
        return {
            "consumed_local_high_water": high_water,
            "occurrences": occurrences,
        }

    def restore_persistent_history(self, history):
        """Restore bounded latches once, before this scheduler is armed.

        Timer definitions may already have been replaced during cold-boot
        composition.  No scheduling lifecycle, occurrence history or active
        hand-off may have started.  Validation and allocation finish before
        either persistent field is committed.
        """

        if (
            self._armed
            or self._faulted
            or self._persistent_history_restored
            or self._authorization_epoch != 0
            or self._authorization_sequence != 0
            or self._intent_sequence != 0
            or self._occurrences
            or self._consumed_local_high_water is not None
            or self._active_occurrence_key is not None
            or self._active_occurrence is not None
            or self._last_override is not None
            or self._baseline is not None
            or self._configuration_fence_pending
            or self._last_step_ms is not None
        ):
            raise RuntimeError(
                "persistent history may only be restored into a fresh "
                "disarmed scheduler"
            )

        normalized = _normalize_persistent_history(
            history, self._max_timers
        )
        configured_timer_ids = set()
        for timer in self._timers:
            configured_timer_ids.add(timer["id"])
        restored_occurrences = {}
        for record in normalized["occurrences"]:
            if record["timer_id"] not in configured_timer_ids:
                raise ValueError(
                    "persistent occurrence references an unknown timer_id"
                )
            restored_occurrences[record["timer_id"]] = record

        self._occurrences = restored_occurrences
        self._consumed_local_high_water = normalized[
            "consumed_local_high_water"
        ]
        self._persistent_history_restored = True
        return True

    def arm(self):
        if self._armed or self._faulted:
            return False
        self._advance_authorization_epoch()
        self._armed = True
        self._baseline = None
        self._configuration_fence_pending = False
        self._last_step_ms = None
        self._last_error = None
        return True

    def disarm(self):
        changed = self._armed
        self._armed = False
        self._baseline = None
        if changed:
            self._advance_authorization_epoch()
        return changed

    def reset_fault(self):
        if not self._faulted:
            return False
        # Allocate the new trust generation before clearing the fault.  If
        # that allocation fails the scheduler remains safely latched.
        self._advance_authorization_epoch()
        self._faulted = False
        self._baseline = None
        self._last_step_ms = None
        self._last_error = None
        return True

    def _canonical_clock(self, snapshot):
        if not isinstance(snapshot, dict):
            return None
        if snapshot.get("valid") is not True:
            return None
        if snapshot.get("health") not in (
            CLOCK_HEALTH_OK,
            CLOCK_HEALTH_HOLDOVER,
        ):
            return None
        # A correction that has not reached the authoritative RTC cannot
        # authorize a heater timer yet.  Holdover after a later RTC fault is
        # also fenced because rtc_health is no longer OK.
        if snapshot.get("rtc_health") != RTC_HEALTH_OK:
            return None
        if snapshot.get("rtc_write_pending") is not False:
            return None
        # A staged RTC correction remains untrusted until its persistent
        # hardware marker has been released.  TimeService exposes that narrow
        # critical section explicitly so even a re-entrant scheduler callback
        # cannot authorize a timer from an uncommitted wall clock.
        if snapshot.get("rtc_commit_revision") is not None:
            return None
        if snapshot.get("source") not in CLOCK_SOURCES:
            return None

        local = snapshot.get("local")
        if not isinstance(local, dict):
            return None
        timezone_name = snapshot.get("timezone")
        timezone_rule = snapshot.get("timezone_rule")
        if (
            type(timezone_name) is not str
            or not timezone_name
            or len(timezone_name) > MAX_TIMEZONE_NAME_LENGTH
            or type(timezone_rule) is not str
            or timezone_rule not in TIMEZONE_RULES
        ):
            return None
        if (
            timezone_rule == TIMEZONE_RULE_EUROPE_ZURICH
            and timezone_name != EUROPE_ZURICH_TIMEZONE_NAME
        ):
            return None
        if (
            timezone_name == EUROPE_ZURICH_TIMEZONE_NAME
            and timezone_rule != TIMEZONE_RULE_EUROPE_ZURICH
        ):
            return None
        for name in (
            "clock_revision",
            "timezone_revision",
            "utc_seconds",
            "utc_offset_minutes",
            "standard_utc_offset_minutes",
            "timezone_rule_version",
        ):
            value = snapshot.get(name)
            if type(value) is not int:
                return None
        if type(snapshot.get("is_dst")) is not bool:
            return None
        if snapshot["clock_revision"] < 0 or snapshot["timezone_revision"] < 0:
            return None
        if not (
            MINIMUM_UTC_OFFSET_MINUTES
            <= snapshot["utc_offset_minutes"]
            <= MAXIMUM_UTC_OFFSET_MINUTES
        ):
            return None
        try:
            projection = utc_seconds_to_local(
                snapshot["utc_seconds"],
                timezone_rule,
                snapshot["standard_utc_offset_minutes"],
            )
        except ValueError:
            return None
        if (
            snapshot["utc_offset_minutes"]
            != projection["utc_offset_minutes"]
            or snapshot["is_dst"] is not projection["is_dst"]
            or snapshot["timezone_rule_version"]
            != projection["timezone_rule_version"]
        ):
            return None
        expected_local = projection["local"]
        for name in (
            "year",
            "month",
            "day",
            "weekday",
            "hour",
            "minute",
            "second",
            "local_minute_id",
            "fold",
        ):
            value = local.get(name)
            if type(value) is not int or value != expected_local[name]:
                return None
        return {
            "utc_seconds": snapshot["utc_seconds"],
            "source": snapshot["source"],
            "timezone": timezone_name,
            "timezone_rule": timezone_rule,
            "timezone_rule_version": snapshot[
                "timezone_rule_version"
            ],
            "standard_utc_offset_minutes": snapshot[
                "standard_utc_offset_minutes"
            ],
            "utc_offset_minutes": snapshot["utc_offset_minutes"],
            "is_dst": snapshot["is_dst"],
            "clock_revision": snapshot["clock_revision"],
            "timezone_revision": snapshot["timezone_revision"],
            "local": dict(expected_local),
        }

    def _set_baseline(self, clock):
        try:
            baseline = {
                "utc_seconds": clock["utc_seconds"],
                "source": clock["source"],
                "timezone": clock["timezone"],
                "timezone_rule": clock["timezone_rule"],
                "timezone_rule_version": clock["timezone_rule_version"],
                "standard_utc_offset_minutes": clock[
                    "standard_utc_offset_minutes"
                ],
                "utc_offset_minutes": clock["utc_offset_minutes"],
                "is_dst": clock["is_dst"],
                "clock_revision": clock["clock_revision"],
                "timezone_revision": clock["timezone_revision"],
                "local_minute_id": clock["local"]["local_minute_id"],
            }
        except MemoryError:
            self._latch_fault("scheduler_baseline_memory_error")
            raise
        self._baseline = baseline

    def _matching_timers(self, local):
        matches = []
        for timer in self._timers:
            if (
                timer["enabled"]
                and local["weekday"] in timer["weekdays"]
                and local["hour"] == timer["start_hour"]
                and local["minute"] == timer["start_minute"]
            ):
                matches.append(timer)
        return matches

    def _consume(
        self,
        timers,
        local,
        status,
        now_ms,
        not_after_ms=None,
        clock=None,
        intent_token=None,
    ):
        updated = dict(self._occurrences)
        keys = []
        for timer in timers:
            key = _occurrence_key(timer, local)
            if len(key) > MAX_OCCURRENCE_KEY_LENGTH:
                raise ValueError("occurrence key exceeds bounded length")
            keys.append(key)
            record = {
                "occurrence_key": key,
                "timer_id": timer["id"],
                "timer_revision": timer["revision"],
                "status": status,
                "consumed_at_ms": now_ms,
                "local_minute_id": local["local_minute_id"],
                "overridden": False,
            }
            if not_after_ms is not None:
                record["not_after_ms"] = not_after_ms
            if clock is not None:
                record["clock_revision"] = clock["clock_revision"]
                record["timezone_revision"] = clock["timezone_revision"]
                record["timezone"] = clock["timezone"]
                record["timezone_rule"] = clock["timezone_rule"]
                record["timezone_rule_version"] = clock[
                    "timezone_rule_version"
                ]
                record["standard_utc_offset_minutes"] = clock[
                    "standard_utc_offset_minutes"
                ]
                record["utc_offset_minutes"] = clock[
                    "utc_offset_minutes"
                ]
                record["is_dst"] = clock["is_dst"]
                record["local_fold"] = local["fold"]
                record["clock_source"] = clock["source"]
                record["created_utc_seconds"] = clock["utc_seconds"]
                record["authorization_epoch"] = self._authorization_epoch
                record["intent_token"] = intent_token
                record["authorization_token"] = None
                record["authorized_at_ms"] = None
                record["completed_at_ms"] = None
                record["completion_reason"] = None
                record["mode"] = timer["mode"]
                record["target_temperature"] = timer[
                    "target_temperature"
                ]
                record["power_level"] = timer["power_level"]
                record["runtime_minutes"] = timer["runtime_minutes"]
            updated[timer["id"]] = record
        self._occurrences = updated
        minute_id = local["local_minute_id"]
        if (
            self._consumed_local_high_water is None
            or minute_id > self._consumed_local_high_water
        ):
            self._consumed_local_high_water = minute_id
        return keys

    def step(self, now_ms, control_available):
        """Return at most one :class:`StartIntent`, otherwise ``None``."""

        _require_ticks(now_ms)
        if not isinstance(control_available, bool):
            raise ValueError("control_available must be a boolean")
        if not self._armed or self._faulted:
            return None
        if self._last_step_ms is not None:
            if self._ticks_diff(now_ms, self._last_step_ms) < 0:
                self._latch_fault("scheduler_monotonic_time_reversed")
                raise ValueError("now_ms precedes the previous scheduler step")
        self._last_step_ms = now_ms

        try:
            snapshot = self._time_service.snapshot(now_ms)
            clock = self._canonical_clock(snapshot)
        except MemoryError:
            self._latch_fault("scheduler_clock_memory_error")
            raise
        except Exception:
            clock = None
        if clock is None:
            if self._baseline is not None:
                self._advance_authorization_epoch()
                self._emit("clock_unavailable", now_ms)
            self._baseline = None
            return None

        if self._baseline is None:
            self._set_baseline(clock)
            self._emit("scheduler_baseline_established", now_ms)
            return None
        if self._configuration_fence_pending:
            self._set_baseline(clock)
            self._configuration_fence_pending = False
            self._emit("timer_configuration_fence", now_ms)
            return None

        baseline = self._baseline
        if (
            clock["clock_revision"] != baseline["clock_revision"]
            or clock["timezone_revision"] != baseline["timezone_revision"]
            or clock["timezone"] != baseline["timezone"]
            or clock["timezone_rule"] != baseline["timezone_rule"]
            or clock["timezone_rule_version"]
            != baseline["timezone_rule_version"]
            or clock["standard_utc_offset_minutes"]
            != baseline["standard_utc_offset_minutes"]
            or clock["utc_offset_minutes"]
            != baseline["utc_offset_minutes"]
            or clock["is_dst"] is not baseline["is_dst"]
            or clock["source"] != baseline["source"]
        ):
            self._advance_authorization_epoch()
            self._set_baseline(clock)
            self._emit("clock_revision_fence", now_ms)
            return None

        utc_delta = clock["utc_seconds"] - baseline["utc_seconds"]
        local_delta = (
            clock["local"]["local_minute_id"]
            - baseline["local_minute_id"]
        )
        # Commit the newest baseline before allocations or external use.  A
        # later failure may miss an occurrence but can never duplicate it.
        self._set_baseline(clock)
        if utc_delta < 0 or utc_delta > self._max_clock_step_seconds:
            self._advance_authorization_epoch()
            self._emit("clock_jump_fence", now_ms, {"delta_seconds": utc_delta})
            return None
        if local_delta < 0 or local_delta > 1:
            self._advance_authorization_epoch()
            self._emit("local_time_jump_fence", now_ms, {"delta_minutes": local_delta})
            return None
        if local_delta == 0:
            return None

        local = clock["local"]
        # The repeated CET hour is a valid diagnostic clock state, but it is
        # never start-eligible.  This preserves at-most-once behavior even
        # after a reboot with an empty in-memory occurrence ledger.
        if local["fold"] == 1:
            return None
        try:
            matches = self._matching_timers(local)
            if not matches:
                return None

            # A timer edit may retain the same civil key.  Already consumed
            # keys and all civil history at/below the global high-water mark
            # are excluded before collision handling.
            due = []
            for timer in matches:
                key = _occurrence_key(timer, local)
                prior = self._occurrences.get(timer["id"])
                above_global_high_water = (
                    self._consumed_local_high_water is None
                    or local["local_minute_id"]
                    > self._consumed_local_high_water
                )
                if above_global_high_water and (
                    prior is None
                    or (
                        prior["occurrence_key"] != key
                        and local["local_minute_id"]
                        > prior["local_minute_id"]
                    )
                ):
                    due.append(timer)
        except MemoryError:
            self._latch_fault("scheduler_memory_error")
            raise
        if not due:
            return None

        try:
            if len(due) > 1:
                keys = self._consume(due, local, "conflict", now_ms)
                self._emit("timer_conflict", now_ms, {"count": len(keys)})
                return None
            timer = due[0]
            if (
                not control_available
                or self._active_occurrence_key is not None
            ):
                key = self._consume(
                    (timer,), local, "suppressed_busy", now_ms
                )[0]
                self._emit("timer_suppressed", now_ms, {"occurrence_key": key})
                return None

            not_after_ms = self._ticks_add(now_ms, self._intent_valid_ms)
            intent_token = self._intent_sequence + 1
            prospective_key = _occurrence_key(timer, local)
            intent = StartIntent(
                timer,
                prospective_key,
                now_ms,
                not_after_ms,
                self._intent_capability,
                intent_token,
            )
            key = self._consume(
                (timer,),
                local,
                "intent_created",
                now_ms,
                not_after_ms=not_after_ms,
                clock=clock,
                intent_token=intent_token,
            )[0]
            if key != prospective_key:
                self._latch_fault("scheduler_occurrence_key_mismatch")
                raise RuntimeError("scheduler occurrence key mismatch")
            self._intent_sequence = intent_token
            self._emit("timer_intent_created", now_ms, {"occurrence_key": key})
            return intent
        except MemoryError:
            self._latch_fault("scheduler_memory_error")
            raise

    def _find_occurrence(self, occurrence_key):
        for timer_id, record in self._occurrences.items():
            if record["occurrence_key"] == occurrence_key:
                return timer_id, record
        return None, None

    def _finish_pending_record(
        self, timer_id, record, status, now_ms, reason, event_code
    ):
        """Commit a terminal pre-application result without partial state."""

        try:
            finished = dict(record)
            finished["status"] = status
            finished["completed_at_ms"] = now_ms
            finished["completion_reason"] = reason
            updated = dict(self._occurrences)
            updated[timer_id] = finished
        except MemoryError:
            self._latch_fault("scheduler_memory_error")
            raise
        self._occurrences = updated
        self._emit(
            event_code,
            now_ms,
            {"occurrence_key": record["occurrence_key"]},
        )
        return False

    @staticmethod
    def _timer_matches_record(timer, record):
        return (
            timer is not None
            and timer["enabled"]
            and timer["revision"] == record["timer_revision"]
            and timer["mode"] == record["mode"]
            and timer["target_temperature"]
            == record["target_temperature"]
            and timer["power_level"] == record["power_level"]
            and timer["runtime_minutes"] == record["runtime_minutes"]
        )

    def _intent_matches_record(self, intent, record):
        return (
            type(intent) is StartIntent
            and intent._owner is self._intent_capability
            and intent._creation_token == record["intent_token"]
            and intent.occurrence_key == record["occurrence_key"]
            and intent.timer_id == record["timer_id"]
            and intent.timer_revision == record["timer_revision"]
            and intent.mode == record["mode"]
            and intent.target_temperature == record["target_temperature"]
            and intent.power_level == record["power_level"]
            and intent.runtime_minutes == record["runtime_minutes"]
            and intent.created_at_ms == record["consumed_at_ms"]
            and intent.not_after_ms == record["not_after_ms"]
        )

    def authorize_intent(self, intent, now_ms, control_available):
        """Return a fresh canonical request immediately before application.

        This is phase one of the application hand-off.  The caller must use
        only the returned :class:`AuthorizedStartIntent`, call the requested
        state port synchronously without yielding, and then call
        :meth:`complete_intent` for phase two.
        """

        if type(intent) is not StartIntent:
            raise ValueError("intent must be a StartIntent")
        if not isinstance(control_available, bool):
            raise ValueError("control_available must be a boolean")
        _require_ticks(now_ms)
        occurrence_key = _bounded_text(
            "occurrence_key",
            intent.occurrence_key,
            MAX_OCCURRENCE_KEY_LENGTH,
        )
        match_id, stored = self._find_occurrence(occurrence_key)
        if stored is None or stored["status"] != "intent_created":
            return None
        if self._ticks_diff(now_ms, stored["consumed_at_ms"]) < 0:
            return None
        if (
            self._last_step_ms is not None
            and self._ticks_diff(now_ms, self._last_step_ms) < 0
        ):
            return None
        if self._ticks_diff(now_ms, stored["not_after_ms"]) > 0:
            self._finish_pending_record(
                match_id,
                stored,
                "expired",
                now_ms,
                "deadline_expired",
                "timer_intent_expired",
            )
            return None
        if (
            not self._armed
            or self._faulted
            or not control_available
            or self._active_occurrence_key is not None
            or stored["authorization_epoch"]
            != self._authorization_epoch
            or not self._intent_matches_record(intent, stored)
        ):
            self._finish_pending_record(
                match_id,
                stored,
                "authorization_rejected",
                now_ms,
                "scheduler_or_intent_changed",
                "timer_authorization_rejected",
            )
            return None

        timer = None
        for candidate in self._timers:
            if candidate["id"] == stored["timer_id"]:
                timer = candidate
                break
        if not self._timer_matches_record(timer, stored):
            self._finish_pending_record(
                match_id,
                stored,
                "authorization_rejected",
                now_ms,
                "timer_changed",
                "timer_authorization_rejected",
            )
            return None

        try:
            clock = self._canonical_clock(self._time_service.snapshot(now_ms))
        except MemoryError:
            self._latch_fault("scheduler_clock_memory_error")
            raise
        except Exception:
            clock = None
        elapsed_ms = self._ticks_diff(now_ms, stored["consumed_at_ms"])
        utc_delta = None
        if clock is not None:
            utc_delta = clock["utc_seconds"] - stored["created_utc_seconds"]
        expected_whole_seconds = elapsed_ms // 1000
        clock_matches = (
            clock is not None
            and clock["clock_revision"] == stored["clock_revision"]
            and clock["timezone_revision"] == stored["timezone_revision"]
            and clock["timezone"] == stored["timezone"]
            and clock["timezone_rule"] == stored["timezone_rule"]
            and clock["timezone_rule_version"]
            == stored["timezone_rule_version"]
            and clock["standard_utc_offset_minutes"]
            == stored["standard_utc_offset_minutes"]
            and clock["utc_offset_minutes"]
            == stored["utc_offset_minutes"]
            and clock["is_dst"] is stored["is_dst"]
            and clock["source"] == stored["clock_source"]
            and utc_delta in (
                expected_whole_seconds,
                expected_whole_seconds + 1,
            )
        )
        if not clock_matches:
            self._advance_authorization_epoch()
            self._baseline = None
            self._finish_pending_record(
                match_id,
                stored,
                "authorization_rejected",
                now_ms,
                "clock_trust_changed",
                "timer_authorization_rejected",
            )
            return None

        try:
            token = self._authorization_sequence + 1
            canonical = StartIntent(
                timer,
                stored["occurrence_key"],
                stored["consumed_at_ms"],
                stored["not_after_ms"],
                self._intent_capability,
                stored["intent_token"],
            )
            authorized_record = dict(stored)
            authorized_record["status"] = "authorized_pending"
            authorized_record["authorization_token"] = token
            authorized_record["authorized_at_ms"] = now_ms
            authorized_record["completion_reason"] = None
            authorized = AuthorizedStartIntent(
                canonical,
                token,
                stored["authorization_epoch"],
                self._authorization_capability,
                authorized_record,
            )
            updated = dict(self._occurrences)
            updated[match_id] = authorized_record
        except MemoryError:
            self._latch_fault("scheduler_memory_error")
            raise
        self._authorization_sequence = token
        self._occurrences = updated
        self._emit(
            "timer_intent_authorized",
            now_ms,
            {"occurrence_key": occurrence_key},
        )
        return authorized

    @staticmethod
    def _requested_state_matches(record, requested_snapshot):
        if (
            type(requested_snapshot) is not dict
            or len(requested_snapshot) != 6
            or "on" not in requested_snapshot
            or "mode" not in requested_snapshot
            or "target_temperature" not in requested_snapshot
            or "power_level" not in requested_snapshot
            or "runtime_minutes" not in requested_snapshot
            or "source" not in requested_snapshot
        ):
            return False
        mode = requested_snapshot.get("mode")
        source = requested_snapshot.get("source")
        runtime = requested_snapshot.get("runtime_minutes")
        target = requested_snapshot.get("target_temperature")
        power = requested_snapshot.get("power_level")
        if (
            requested_snapshot.get("on") is not True
            or type(mode) is not str
            or mode != record["mode"]
            or type(source) is not str
            or source != TIMER_SOURCE
            or type(runtime) is not int
            or runtime != record["runtime_minutes"]
        ):
            return False
        if record["power_level"] is None:
            return (
                power is None
                and type(target) is int
                and target == record["target_temperature"]
            )
        return (
            target is None
            and type(power) is int
            and power == record["power_level"]
        )

    def complete_intent(
        self, authorized, applied, requested_snapshot, now_ms
    ):
        """Finish phase two after the requested-state call has returned.

        Completion never rechecks the wall clock or the global authorization
        epoch: the application side effect has already happened.  Instead it
        binds the exact token and confirms the resulting requested snapshot.
        """

        if type(authorized) is not AuthorizedStartIntent:
            raise ValueError("authorized must be an AuthorizedStartIntent")
        if not isinstance(applied, bool):
            raise ValueError("applied must be a boolean")
        _require_ticks(now_ms)
        if authorized._owner is not self._authorization_capability:
            return False
        occurrence_key = authorized.occurrence_key
        record = authorized._record
        if (
            type(record) is not dict
            or record["occurrence_key"] != occurrence_key
            or record["status"] != "authorized_pending"
            or record["authorization_token"]
            != authorized.authorization_token
            or record["authorization_epoch"] != authorized._epoch
            or self._ticks_diff(now_ms, record["authorized_at_ms"]) < 0
            or not self._intent_matches_record(authorized._intent, record)
        ):
            return False

        snapshot_matches = self._requested_state_matches(
            record, requested_snapshot
        )
        if (
            snapshot_matches
            and self._active_occurrence_key is not None
            and self._active_occurrence_key != occurrence_key
        ):
            record["status"] = "application_failed"
            record["completed_at_ms"] = now_ms
            record["completion_reason"] = "active_occurrence_conflict"
            self._latch_fault("scheduler_active_occurrence_conflict")
            self._emit(
                "timer_intent_application_failed",
                now_ms,
                {"occurrence_key": occurrence_key},
            )
            return False
        if snapshot_matches:
            # Every field updated here already exists in the preallocated
            # authorization record.  This makes the post-side-effect truth
            # commit allocation-free; diagnostics are emitted afterwards.
            record["status"] = "accepted"
            record["completed_at_ms"] = now_ms
            record["completion_reason"] = (
                "application_confirmed"
                if applied
                else "application_confirmed_despite_failed_result"
            )
            self._active_occurrence_key = occurrence_key
            self._active_occurrence = record
            if not applied:
                self._latch_fault(
                    "scheduler_application_result_mismatch"
                )
            self._emit(
                "timer_intent_accepted",
                now_ms,
                {"occurrence_key": occurrence_key},
            )
            return True

        record["status"] = "application_failed"
        record["completed_at_ms"] = now_ms
        record["completion_reason"] = (
            "application_state_mismatch"
            if applied
            else (
                "application_rejected"
                if type(requested_snapshot) is dict
                and requested_snapshot.get("on") is False
                else "application_state_unknown"
            )
        )
        if applied or record["completion_reason"] == "application_state_unknown":
            self._latch_fault("scheduler_application_state_mismatch")
        self._emit(
            "timer_intent_application_failed",
            now_ms,
            {"occurrence_key": occurrence_key},
        )
        return False

    def mark_manual_override(self, occurrence_key, now_ms):
        """Latch one concrete accepted timer occurrence as overridden."""

        occurrence_key = _bounded_text(
            "occurrence_key", occurrence_key, MAX_OCCURRENCE_KEY_LENGTH
        )
        _require_ticks(now_ms)
        active = self._active_occurrence
        if (
            active is None
            or active["occurrence_key"] != occurrence_key
            or active["status"] != "accepted"
        ):
            return False
        if self._ticks_diff(now_ms, active["completed_at_ms"]) < 0:
            return False
        if (
            self._last_step_ms is not None
            and self._ticks_diff(now_ms, self._last_step_ms) < 0
        ):
            return False
        if active["overridden"]:
            return False
        record = dict(active)
        record["overridden"] = True
        record["status"] = "overridden"
        record["overridden_at_ms"] = now_ms
        updated = dict(self._occurrences)
        current = updated.get(record["timer_id"])
        if current is not None and current["occurrence_key"] == occurrence_key:
            updated[record["timer_id"]] = dict(record)
        last_override = dict(record)
        self._occurrences = updated
        self._last_override = last_override
        self._active_occurrence_key = None
        self._active_occurrence = None
        self._emit(
            "manual_timer_override",
            now_ms,
            {"occurrence_key": occurrence_key},
        )
        return True

    def mark_active_complete(
        self, occurrence_key, now_ms, reason="controller_off"
    ):
        """Release one normally completed accepted timer occurrence."""

        occurrence_key = _bounded_text(
            "occurrence_key", occurrence_key, MAX_OCCURRENCE_KEY_LENGTH
        )
        reason = _bounded_text("reason", reason, 80)
        _require_ticks(now_ms)
        active = self._active_occurrence
        if (
            active is None
            or active["occurrence_key"] != occurrence_key
            or active["status"] != "accepted"
        ):
            return False
        if self._ticks_diff(now_ms, active["completed_at_ms"]) < 0:
            return False
        if (
            self._last_step_ms is not None
            and self._ticks_diff(now_ms, self._last_step_ms) < 0
        ):
            return False
        try:
            record = dict(active)
            record["status"] = "completed"
            record["ended_at_ms"] = now_ms
            record["active_completion_reason"] = reason
            updated = dict(self._occurrences)
            current = updated.get(record["timer_id"])
            if (
                current is not None
                and current["occurrence_key"] == occurrence_key
            ):
                updated[record["timer_id"]] = dict(record)
        except MemoryError:
            self._latch_fault("scheduler_memory_error")
            raise
        self._occurrences = updated
        self._active_occurrence_key = None
        self._active_occurrence = None
        self._emit(
            "timer_session_completed",
            now_ms,
            {"occurrence_key": occurrence_key, "reason": reason},
        )
        return True

    def next_occurrence(self, now_ms):
        """Return the next start-eligible local weekly occurrence."""

        _require_ticks(now_ms)
        try:
            clock = self._canonical_clock(self._time_service.snapshot(now_ms))
        except MemoryError:
            self._latch_fault("scheduler_clock_memory_error")
            raise
        except Exception:
            self._last_error = "scheduler_clock_unavailable"
            return None
        if clock is None:
            return None
        local = clock["local"]
        current_minute = local["local_minute_id"]
        day_start = current_minute - local["hour"] * 60 - local["minute"]
        best = None
        for timer in self._timers:
            if not timer["enabled"]:
                continue
            # Fourteen bounded calendar days are sufficient to step over one
            # nonexistent or transition-fenced weekly occurrence.
            for day_offset in range(15):
                weekday = (local["weekday"] + day_offset) % 7
                if weekday not in timer["weekdays"]:
                    continue
                candidate = (
                    day_start
                    + day_offset * 1440
                    + timer["start_hour"] * 60
                    + timer["start_minute"]
                )
                # The scheduler never catches up or fires the already-open
                # minute.  Diagnostics therefore report strictly future
                # occurrences only.
                if candidate <= current_minute:
                    continue
                try:
                    candidate_local = epoch_seconds_to_civil(candidate * 60)
                    candidates = local_civil_to_utc_occurrences(
                        candidate_local["year"],
                        candidate_local["month"],
                        candidate_local["day"],
                        candidate_local["hour"],
                        candidate_local["minute"],
                        0,
                        clock["timezone_rule"],
                        clock["standard_utc_offset_minutes"],
                    )
                except ValueError:
                    continue
                if not candidates:
                    # The local timer minute lies in the spring gap.
                    continue
                selected = candidates[0]
                if selected["fold"] != 0:
                    continue
                candidate_utc = selected["utc_seconds"]
                if candidate_utc <= clock["utc_seconds"]:
                    # Never select the second interpretation of a repeated
                    # local minute after its first occurrence has passed.
                    continue
                if is_timezone_transition_instant(
                    candidate_utc,
                    clock["timezone_rule"],
                    clock["standard_utc_offset_minutes"],
                ):
                    # The exact offset-change minute is a conservative fence.
                    continue
                key = _occurrence_key(timer, candidate_local)
                prior = self._occurrences.get(timer["id"])
                if (
                    (
                        self._consumed_local_high_water is not None
                        and candidate <= self._consumed_local_high_water
                    )
                    or (
                        prior is not None
                        and candidate <= prior["local_minute_id"]
                    )
                ):
                    continue
                item = {
                    "occurrence_key": key,
                    "timer_id": timer["id"],
                    "local_date": _date_key(candidate_local),
                    "start": timer["start"],
                    "weekday": candidate_local["weekday"],
                    "minutes_from_now": (
                        candidate_utc - clock["utc_seconds"] + 59
                    )
                    // 60,
                }
                if best is None or item["minutes_from_now"] < best["minutes_from_now"]:
                    best = item
                break
        return best

    def snapshot(self):
        timers = []
        for timer in self._timers:
            timers.append(
                {
                    "id": timer["id"],
                    "name": timer["name"],
                    "enabled": timer["enabled"],
                    "weekdays": list(timer["weekdays"]),
                    "start": timer["start"],
                    "mode": timer["mode"],
                    "target_temperature": timer["target_temperature"],
                    "power_level": timer["power_level"],
                    "runtime_minutes": timer["runtime_minutes"],
                    "revision": timer["revision"],
                }
            )
        occurrences = {}
        for timer_id, record in self._occurrences.items():
            occurrences[timer_id] = dict(record)
        return {
            "armed": self._armed,
            "faulted": self._faulted,
            "configuration_revision": self._configuration_revision,
            "timers": timers,
            "occurrences": occurrences,
            "consumed_local_high_water": self._consumed_local_high_water,
            "active_occurrence_key": self._active_occurrence_key,
            "active_occurrence": (
                None
                if self._active_occurrence is None
                else dict(self._active_occurrence)
            ),
            "last_override": (
                None
                if self._last_override is None
                else dict(self._last_override)
            ),
            "last_error": self._last_error,
            "events_pending": len(self._events),
            "events_dropped": self.events_dropped,
            "event_errors": self.event_errors,
        }

    def public_snapshot(self):
        """Return a compact API view without intents or authorization data."""

        active = None
        if self._active_occurrence is not None:
            active = {
                "occurrence_key": self._active_occurrence.get(
                    "occurrence_key"
                ),
                "timer_id": self._active_occurrence.get("timer_id"),
                "local_date": self._active_occurrence.get("local_date"),
                "start": self._active_occurrence.get("start"),
                "status": self._active_occurrence.get("status"),
            }
        return {
            "armed": self._armed,
            "faulted": self._faulted,
            "configuration_revision": self._configuration_revision,
            "timer_count": len(self._timers),
            "active_occurrence_key": self._active_occurrence_key,
            "active_occurrence": active,
            "consumed_local_high_water": self._consumed_local_high_water,
            "events_pending": len(self._events),
            "events_dropped": self.events_dropped,
            "event_errors": self.event_errors,
        }
