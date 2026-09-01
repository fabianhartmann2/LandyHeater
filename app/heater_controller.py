"""Hardware-independent Autoterm control authority and state machine.

The controller consumes dictionaries produced by ``parse_frame`` and requests
named operations from an injected protocol port.  It never opens hardware,
builds raw frames, or calculates a CRC.
"""

import math
import time as _time

from protocol.autoterm_protocol import (
    CMD_INIT,
    CMD_STATUS,
    DEVICE_HEATER,
    FRAME_START,
)

from .application_state import (
    COMMUNICATION_ERROR,
    COMMUNICATION_OK,
    CONTROL_MODE_CABIN_TEMPERATURE,
    CONTROL_MODE_POWER,
    CONTROL_MODE_ROOF_TENT_TEMPERATURE,
    HEATER_STATE_OFF,
    HEATER_STATE_RUNNING,
    HEATER_STATE_SHUTTING_DOWN,
    HEATER_STATE_STARTING,
    HEATER_STATE_TEMP_MONITORING,
    HEATER_STATE_UNKNOWN,
    ActualHeaterState,
    HeaterSession,
    RequestedHeaterState,
    validate_start_request,
)
from .temperature_manager import (
    DEFAULT_FAILED_AFTER_MS,
    DEFAULT_MAXIMUM_TEMPERATURE_C,
    DEFAULT_MINIMUM_TEMPERATURE_C,
    DEFAULT_STALE_AFTER_MS,
    SENSOR_HEALTH_FAILED,
    SENSOR_HEALTH_MISSING,
    SENSOR_HEALTH_OK,
    SENSOR_HEALTH_STALE,
    SENSOR_ROLE_CABIN,
    SENSOR_ROLE_ROOF_TENT,
)


PHASE_UNSYNCHRONIZED = "unsynchronized"
PHASE_WAIT_INIT = "wait_init"
PHASE_WAIT_STATUS = "wait_status"
PHASE_READY = "ready"
PHASE_ERROR = "error"

DEFAULT_HEARTBEAT_MS = 1000
DEFAULT_RESPONSE_TIMEOUT_MS = 10000
DEFAULT_CONTROL_SETTLE_MS = 200
DEFAULT_CONTROL_CONFIRMATION_TIMEOUT_MS = 10000
DEFAULT_MAX_CONTROL_ATTEMPTS = 2
DEFAULT_MAXIMUM_RUNTIME_MINUTES = 120
DEFAULT_EVENT_CAPACITY = 16
DEFAULT_STARTING_STOP_POLICY_TIMEOUT_MS = 300000
DEFAULT_INVALID_FRAME_THRESHOLD = 3

_INIT_RX_PAYLOAD_LENGTH = 5
_STATUS_RX_PAYLOAD_LENGTH = 19
_CONTROL_START = "start"
_CONTROL_SHUTDOWN = "shutdown"

_ACTIVE_SENSOR_ROLE_BY_MODE = {
    CONTROL_MODE_ROOF_TENT_TEMPERATURE: SENSOR_ROLE_ROOF_TENT,
    CONTROL_MODE_CABIN_TEMPERATURE: SENSOR_ROLE_CABIN,
}

_PROTOCOL_METHODS = (
    "validate_inbound_frame",
    "request_initialization",
    "request_status",
    "request_start",
    "request_shutdown",
)


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


class HeaterController:
    """Own all decisions that may request an Autoterm control operation."""

    def __init__(
        self,
        protocol_port,
        ticks_diff=None,
        ticks_add=None,
        heartbeat_ms=DEFAULT_HEARTBEAT_MS,
        response_timeout_ms=DEFAULT_RESPONSE_TIMEOUT_MS,
        control_settle_ms=DEFAULT_CONTROL_SETTLE_MS,
        control_confirmation_timeout_ms=(
            DEFAULT_CONTROL_CONFIRMATION_TIMEOUT_MS
        ),
        max_control_attempts=DEFAULT_MAX_CONTROL_ATTEMPTS,
        maximum_runtime_minutes=DEFAULT_MAXIMUM_RUNTIME_MINUTES,
        event_capacity=DEFAULT_EVENT_CAPACITY,
        starting_stop_policy_timeout_ms=(
            DEFAULT_STARTING_STOP_POLICY_TIMEOUT_MS
        ),
        invalid_frame_threshold=DEFAULT_INVALID_FRAME_THRESHOLD,
        temperature_manager=None,
    ):
        for method_name in _PROTOCOL_METHODS:
            if not callable(getattr(protocol_port, method_name, None)):
                raise ValueError(
                    "protocol_port must provide {}()".format(method_name)
                )

        _require_positive_integer("heartbeat_ms", heartbeat_ms)
        _require_positive_integer("response_timeout_ms", response_timeout_ms)
        _require_positive_integer("control_settle_ms", control_settle_ms)
        _require_positive_integer(
            "control_confirmation_timeout_ms",
            control_confirmation_timeout_ms,
        )
        _require_positive_integer(
            "max_control_attempts", max_control_attempts
        )
        _require_positive_integer(
            "maximum_runtime_minutes", maximum_runtime_minutes
        )
        _require_positive_integer("event_capacity", event_capacity)
        _require_positive_integer(
            "starting_stop_policy_timeout_ms",
            starting_stop_policy_timeout_ms,
        )
        _require_positive_integer(
            "invalid_frame_threshold", invalid_frame_threshold
        )
        if response_timeout_ms <= heartbeat_ms:
            raise ValueError("response_timeout_ms must exceed heartbeat_ms")

        if (ticks_diff is None) != (ticks_add is None):
            raise ValueError(
                "ticks_diff and ticks_add must be provided together"
            )
        if ticks_diff is None:
            ticks_diff = _platform_ticks_diff
            ticks_add = _platform_ticks_add
        if not callable(ticks_diff) or not callable(ticks_add):
            raise ValueError("ticks_diff and ticks_add must be callable")
        if temperature_manager is not None and not callable(
            getattr(temperature_manager, "sensor_snapshot", None)
        ):
            raise ValueError(
                "temperature_manager must provide sensor_snapshot()"
            )
        sensor_stale_after_ms = DEFAULT_STALE_AFTER_MS
        sensor_failed_after_ms = DEFAULT_FAILED_AFTER_MS
        sensor_minimum_temperature_c = DEFAULT_MINIMUM_TEMPERATURE_C
        sensor_maximum_temperature_c = DEFAULT_MAXIMUM_TEMPERATURE_C
        if temperature_manager is not None:
            sensor_stale_after_ms = getattr(
                temperature_manager, "stale_after_ms", None
            )
            sensor_failed_after_ms = getattr(
                temperature_manager, "failed_after_ms", None
            )
            _require_positive_integer(
                "temperature_manager.stale_after_ms",
                sensor_stale_after_ms,
            )
            _require_positive_integer(
                "temperature_manager.failed_after_ms",
                sensor_failed_after_ms,
            )
            if sensor_failed_after_ms <= sensor_stale_after_ms:
                raise ValueError(
                    "temperature_manager failed threshold must exceed stale"
                )
            sensor_minimum_temperature_c = getattr(
                temperature_manager,
                "minimum_temperature_c",
                DEFAULT_MINIMUM_TEMPERATURE_C,
            )
            sensor_maximum_temperature_c = getattr(
                temperature_manager,
                "maximum_temperature_c",
                DEFAULT_MAXIMUM_TEMPERATURE_C,
            )
            for name, value in (
                (
                    "temperature_manager.minimum_temperature_c",
                    sensor_minimum_temperature_c,
                ),
                (
                    "temperature_manager.maximum_temperature_c",
                    sensor_maximum_temperature_c,
                ),
            ):
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    raise ValueError("{} must be finite".format(name))
            if (
                sensor_minimum_temperature_c
                < DEFAULT_MINIMUM_TEMPERATURE_C
                or sensor_maximum_temperature_c
                > DEFAULT_MAXIMUM_TEMPERATURE_C
                or sensor_minimum_temperature_c
                >= sensor_maximum_temperature_c
            ):
                raise ValueError(
                    "temperature_manager bounds exceed DS18B20 limits"
                )

        self._protocol = protocol_port
        self._ticks_diff = ticks_diff
        self._ticks_add = ticks_add
        self.heartbeat_ms = heartbeat_ms
        self.response_timeout_ms = response_timeout_ms
        self.control_settle_ms = control_settle_ms
        self.control_confirmation_timeout_ms = (
            control_confirmation_timeout_ms
        )
        self.max_control_attempts = max_control_attempts
        self.maximum_runtime_minutes = maximum_runtime_minutes
        self.starting_stop_policy_timeout_ms = (
            starting_stop_policy_timeout_ms
        )
        self.invalid_frame_threshold = invalid_frame_threshold
        self._temperature_manager = temperature_manager
        self._sensor_stale_after_ms = sensor_stale_after_ms
        self._sensor_failed_after_ms = sensor_failed_after_ms
        self._sensor_minimum_temperature_c = float(
            sensor_minimum_temperature_c
        )
        self._sensor_maximum_temperature_c = float(
            sensor_maximum_temperature_c
        )

        self._requested = RequestedHeaterState()
        self._actual = ActualHeaterState()
        self._phase = PHASE_UNSYNCHRONIZED

        self._next_request_due_ms = None
        self._pending_control_due_ms = None
        self._expected_command = None
        self._response_wait_started_ms = None
        self._control_attempt = None
        self._control_fault = None
        self._restart_blocked = False
        self._communication_fault_active = False
        self._request_revision = 0
        self._request_not_after_ms = None
        self._request_requested_at_ms = None
        self._start_sent_revision = None
        self._shutdown_sent_revision = None
        self._starting_stop_since_ms = None
        self._active_sensor = None
        self._sensor_alert = None
        self._sensor_stop_latch = None

        self._session = None
        self._session_sensor_rom_id = None
        self._session_sensor_failure_generation = None
        self._session_sensor_assignment_revision = None
        self._next_session_id = 1

        self._events = []
        self._event_capacity = event_capacity
        self.events_dropped = 0
        self.event_errors = 0

        self.invalid_frames = 0
        self._invalid_frame_strikes = 0
        self._consecutive_valid_statuses = 0
        self.ignored_frames = 0
        self.communication_failures = 0
        self.control_failures = 0
        self.last_error = None

    @property
    def phase(self):
        return self._phase

    @property
    def requested(self):
        """Return a detached requested-state snapshot."""

        return self._requested.snapshot()

    @property
    def requested_on(self):
        """Allocation-free requested power truth for composition gateways."""

        return self._requested.on

    @property
    def requested_source(self):
        """Allocation-free requested source for manual-override correlation."""

        return self._requested.source

    @property
    def request_revision(self):
        """Return the monotone Requested-State revision.

        Application gateways use this value for optimistic concurrency.  It
        is deliberately distinct from Actual State and never authorizes a
        protocol operation by itself.
        """

        return self._request_revision

    def requested_matches(
        self,
        on,
        mode,
        target_temperature,
        power_level,
        runtime_minutes,
        source,
        not_after_ms=None,
        ignore_deadline=False,
    ):
        """Compare requested truth without allocating a snapshot."""

        if type(ignore_deadline) is not bool:
            raise ValueError("ignore_deadline must be boolean")
        return (
            self._requested.on is on
            and self._requested.mode == mode
            and self._requested.target_temperature == target_temperature
            and self._requested.power_level == power_level
            and self._requested.runtime_minutes == runtime_minutes
            and self._requested.source == source
            and (
                ignore_deadline
                or self._request_not_after_ms == not_after_ms
            )
        )

    def _start_base_available(self):
        return (
            not self._requested.on
            and self._actual.communication == COMMUNICATION_OK
            and self._actual.initialized
            and self._actual.synchronized
            and self._actual.heater_state == HEATER_STATE_OFF
            and self._session is None
            and self._control_attempt is None
            and self._control_fault is None
            and not self._restart_blocked
            and self._sensor_stop_latch is None
        )

    def _concrete_start_available(
        self,
        now_ms,
        mode,
        target_temperature,
        power_level,
        runtime_minutes,
        source,
        allowed_sources,
    ):
        validate_start_request(
            mode,
            target_temperature,
            power_level,
            runtime_minutes,
            source,
            self.maximum_runtime_minutes,
        )
        if source not in allowed_sources:
            raise ValueError("start request source is not allowed")
        if not self._start_base_available():
            return False
        role = _ACTIVE_SENSOR_ROLE_BY_MODE.get(mode)
        if role is None:
            return True
        if self._temperature_manager is None:
            return False
        sensor = self._canonical_sensor_snapshot(
            self._temperature_manager.sensor_snapshot(role, now_ms),
            role,
            now_ms,
        )
        return (
            self._start_base_available()
            and sensor["health"] == SENSOR_HEALTH_OK
            and sensor["present"] is True
            and sensor["usable"] is True
        )

    def timer_start_available(self, now_ms, request=None):
        """Return whether a timer may synchronously set Requested ON.

        This method never sends or requests protocol traffic.  With a concrete
        authorized request it additionally validates its parameters and the
        active regulation sensor.
        """

        _require_ticks(now_ms)
        available = self._start_base_available()
        if not available or request is None:
            return available
        return self._concrete_start_available(
            now_ms,
            getattr(request, "mode", None),
            getattr(request, "target_temperature", None),
            getattr(request, "power_level", None),
            getattr(request, "runtime_minutes", None),
            getattr(request, "source", None),
            ("timer",),
        )

    def manual_start_available(
        self,
        now_ms,
        mode,
        target_temperature=None,
        power_level=None,
        runtime_minutes=60,
        source="manual",
    ):
        """Validate one user start against current synchronized truth.

        The check is application-only and performs no protocol I/O.  A sensor
        callback may execute arbitrary Python, so base availability is checked
        again after the sample before returning True.
        """

        _require_ticks(now_ms)
        if not self._control_status_is_fresh(now_ms):
            return False
        available = self._concrete_start_available(
            now_ms,
            mode,
            target_temperature,
            power_level,
            runtime_minutes,
            source,
            ("manual", "quick_start"),
        )
        return available and self._control_status_is_fresh(now_ms)

    def timer_session_complete(self, now_ms):
        """Return true only after synchronized hardware and app truth are OFF."""

        _require_ticks(now_ms)
        return (
            not self._requested.on
            and self._actual.communication == COMMUNICATION_OK
            and self._actual.initialized
            and self._actual.synchronized
            and self._actual.heater_state == HEATER_STATE_OFF
            and self._session is None
            and self._control_attempt is None
        )

    @property
    def actual(self):
        """Return a detached actual-state snapshot."""

        return self._actual.snapshot()

    def _active_sensor_role(self):
        if self._session is not None:
            mode = self._session.mode
        elif self._requested.on:
            mode = self._requested.mode
        else:
            return None
        return _ACTIVE_SENSOR_ROLE_BY_MODE.get(mode)

    @staticmethod
    def _missing_sensor_snapshot(role, reason):
        return {
            "role": role,
            "rom_id": None,
            "value_c": None,
            "last_valid_ms": None,
            "age_ms": None,
            "unavailable_since_ms": None,
            "unavailable_age_ms": None,
            "health": SENSOR_HEALTH_MISSING,
            "usable": False,
            "present": None,
            "invalid_readings": 0,
            "failure_generation": 0,
            "assignment_revision": 0,
            "last_error": reason,
        }

    def _canonical_sensor_snapshot(self, snapshot, role, now_ms):
        if not isinstance(snapshot, dict):
            raise ValueError("sensor snapshot must be a dictionary")
        if snapshot.get("role") != role:
            raise ValueError("sensor snapshot role mismatch")

        health = snapshot.get("health")
        if health not in (
            SENSOR_HEALTH_OK,
            SENSOR_HEALTH_STALE,
            SENSOR_HEALTH_FAILED,
            SENSOR_HEALTH_MISSING,
        ):
            raise ValueError("invalid sensor health")

        rom_id = snapshot.get("rom_id")
        if rom_id is not None and (
            not isinstance(rom_id, str) or not rom_id
        ):
            raise ValueError("invalid sensor ROM ID")

        value_c = snapshot.get("value_c")
        if value_c is not None:
            if (
                isinstance(value_c, bool)
                or not isinstance(value_c, (int, float))
            ):
                raise ValueError("invalid sensor value")
            try:
                value_c = float(value_c)
            except (OverflowError, TypeError, ValueError):
                raise ValueError("invalid sensor value")
            if not math.isfinite(value_c):
                raise ValueError("invalid sensor value")
            if (
                value_c < self._sensor_minimum_temperature_c
                or value_c > self._sensor_maximum_temperature_c
            ):
                raise ValueError("sensor value exceeds configured bounds")

        last_valid_ms = snapshot.get("last_valid_ms")
        if last_valid_ms is not None and (
            not isinstance(last_valid_ms, int)
            or isinstance(last_valid_ms, bool)
        ):
            raise ValueError("invalid sensor timestamp")
        age_ms = snapshot.get("age_ms")
        if age_ms is not None and (
            not isinstance(age_ms, int)
            or isinstance(age_ms, bool)
            or age_ms < 0
        ):
            raise ValueError("invalid sensor age")

        unavailable_since_ms = snapshot.get("unavailable_since_ms")
        if unavailable_since_ms is not None and (
            not isinstance(unavailable_since_ms, int)
            or isinstance(unavailable_since_ms, bool)
        ):
            raise ValueError("invalid sensor unavailability timestamp")
        unavailable_age_ms = snapshot.get("unavailable_age_ms")
        if unavailable_age_ms is not None and (
            not isinstance(unavailable_age_ms, int)
            or isinstance(unavailable_age_ms, bool)
            or unavailable_age_ms < 0
        ):
            raise ValueError("invalid sensor unavailability age")

        if last_valid_ms is not None:
            if rom_id is None or value_c is None:
                raise ValueError("sensor reading lacks identity or value")
            calculated_age = self._ticks_diff(now_ms, last_valid_ms)
            if calculated_age < 0 or age_ms != calculated_age:
                raise ValueError("sensor age does not match its timestamp")
            if (
                unavailable_since_ms is not None
                or unavailable_age_ms is not None
            ):
                raise ValueError("valid sensor has stale unavailability data")
            if calculated_age >= self._sensor_failed_after_ms:
                expected_health = SENSOR_HEALTH_FAILED
            elif calculated_age >= self._sensor_stale_after_ms:
                expected_health = SENSOR_HEALTH_STALE
            else:
                expected_health = SENSOR_HEALTH_OK
        else:
            if value_c is not None or age_ms is not None:
                raise ValueError("sensor without valid timestamp has a value")
            expected_health = SENSOR_HEALTH_MISSING
            if unavailable_since_ms is None:
                if unavailable_age_ms is not None:
                    raise ValueError("unavailability age lacks timestamp")
            else:
                calculated_unavailable_age = self._ticks_diff(
                    now_ms, unavailable_since_ms
                )
                if (
                    calculated_unavailable_age < 0
                    or unavailable_age_ms != calculated_unavailable_age
                ):
                    raise ValueError(
                        "sensor unavailability age does not match timestamp"
                    )
                if (
                    calculated_unavailable_age
                    >= self._sensor_failed_after_ms
                ):
                    expected_health = SENSOR_HEALTH_FAILED
            if expected_health == SENSOR_HEALTH_FAILED and rom_id is None:
                raise ValueError("failed assigned sensor lacks identity")

        if health != expected_health:
            raise ValueError("sensor health contradicts measured age")

        usable = snapshot.get("usable")
        expected_usable = health in (SENSOR_HEALTH_OK, SENSOR_HEALTH_STALE)
        if usable is not expected_usable:
            raise ValueError("sensor usable flag contradicts health")
        if expected_usable and last_valid_ms is None:
            raise ValueError("usable sensor lacks a valid reading")

        present = snapshot.get("present")
        if present not in (None, True, False):
            raise ValueError("invalid sensor presence")
        invalid_readings = snapshot.get("invalid_readings", 0)
        if (
            not isinstance(invalid_readings, int)
            or isinstance(invalid_readings, bool)
            or invalid_readings < 0
        ):
            raise ValueError("invalid sensor failure count")
        failure_generation = snapshot.get("failure_generation", 0)
        if (
            not isinstance(failure_generation, int)
            or isinstance(failure_generation, bool)
            or failure_generation < 0
        ):
            raise ValueError("invalid sensor failure generation")
        assignment_revision = snapshot.get("assignment_revision", 0)
        if (
            not isinstance(assignment_revision, int)
            or isinstance(assignment_revision, bool)
            or assignment_revision < 0
        ):
            raise ValueError("invalid sensor assignment revision")
        last_error = snapshot.get("last_error")
        if last_error is not None and not isinstance(last_error, str):
            raise ValueError("invalid sensor error")

        return {
            "role": role,
            "rom_id": rom_id,
            "value_c": value_c,
            "last_valid_ms": last_valid_ms,
            "age_ms": age_ms,
            "unavailable_since_ms": unavailable_since_ms,
            "unavailable_age_ms": unavailable_age_ms,
            "health": health,
            "usable": expected_usable,
            "present": present,
            "invalid_readings": invalid_readings,
            "failure_generation": failure_generation,
            "assignment_revision": assignment_revision,
            "last_error": last_error,
        }

    def _read_active_sensor(self, role, now_ms):
        if self._temperature_manager is None:
            return self._missing_sensor_snapshot(
                role, "temperature manager is not configured"
            )
        try:
            snapshot = self._temperature_manager.sensor_snapshot(role, now_ms)
            return self._canonical_sensor_snapshot(snapshot, role, now_ms)
        except Exception as exc:
            return self._missing_sensor_snapshot(
                role, "temperature manager failed: {}".format(exc)
            )

    def _sensor_error_reason(self):
        if self._sensor_stop_latch is not None:
            return self._sensor_stop_latch["reason"]
        if (
            self._sensor_alert is not None
            and self._sensor_alert.get("severity") == "error"
        ):
            return self._sensor_alert["reason"]
        return None

    def _restore_noncommunication_error(self):
        if self._actual.communication == COMMUNICATION_ERROR:
            return
        if self._control_fault is not None:
            self.last_error = self._control_fault["reason"]
        else:
            self.last_error = self._sensor_error_reason()

    def _set_sensor_alert(
        self, role, health, severity, action, reason, now_ms
    ):
        try:
            current = self._sensor_alert
            if current is not None and (
                current.get("role") == role
                and current.get("health") == health
                and current.get("severity") == severity
                and current.get("action") == action
                and current.get("reason") == reason
            ):
                return False
            self._sensor_alert = {
                "role": role,
                "health": health,
                "severity": severity,
                "action": action,
                "reason": reason,
                "at_ms": now_ms,
            }
        except Exception:
            self.event_errors += 1
            if severity == "error":
                self.last_error = reason
            return False
        self._restore_noncommunication_error()
        return True

    @staticmethod
    def _new_sensor_stop_latch(role, health, reason, now_ms, session_id):
        return {
            "role": role,
            "health": health,
            "reason": reason,
            "at_ms": now_ms,
            "session_id": session_id,
        }

    def _apply_sensor_policy(self, now_ms):
        """Apply sensor policy before any control operation is evaluated."""

        role = self._active_sensor_role()
        if role is None:
            self._active_sensor = None
            return

        sensor = self._read_active_sensor(role, now_ms)
        session_exists = self._session is not None
        confirmed_active = (
            session_exists and self._session.confirmed_active
        )
        policy_reason = None
        if (
            session_exists
            and self._session_sensor_rom_id is not None
            and sensor.get("rom_id") != self._session_sensor_rom_id
        ):
            health = SENSOR_HEALTH_MISSING
            policy_reason = "active sensor assignment changed"
        elif (
            session_exists
            and self._session_sensor_assignment_revision is not None
            and sensor.get("assignment_revision", 0)
            != self._session_sensor_assignment_revision
        ):
            health = SENSOR_HEALTH_MISSING
            policy_reason = "active sensor assignment configuration changed"
        elif (
            session_exists
            and self._session_sensor_failure_generation is not None
            and sensor.get("failure_generation", 0)
            != self._session_sensor_failure_generation
        ):
            health = SENSOR_HEALTH_FAILED
            policy_reason = "active sensor crossed the failure deadline"
        else:
            health = sensor["health"]
        self._active_sensor = sensor

        if health == SENSOR_HEALTH_OK:
            if not confirmed_active and sensor.get("present") is not True:
                health = SENSOR_HEALTH_MISSING
                policy_reason = "fresh sensor is not confirmed present"
            else:
                if self._sensor_stop_latch is None:
                    self._sensor_alert = None
                    self._restore_noncommunication_error()
                return

        if health == SENSOR_HEALTH_STALE and confirmed_active:
            if self._sensor_stop_latch is None:
                self._set_sensor_alert(
                    role,
                    health,
                    "warning",
                    "continue_last_valid",
                    "active regulation sensor is stale",
                    now_ms,
                )
            return

        if health == SENSOR_HEALTH_STALE:
            reason = "temperature start requires a fresh active sensor"
        elif health == SENSOR_HEALTH_FAILED:
            reason = policy_reason or "active regulation sensor failed"
        else:
            reason = policy_reason or sensor.get("last_error") or (
                "active regulation sensor is missing"
            )

        if session_exists:
            session_id = self._session.session_id
            latch_matches = (
                self._sensor_stop_latch is not None
                and self._sensor_stop_latch.get("session_id") == session_id
            )
            changed = self.request_stop()
            if changed or not latch_matches:
                self._pending_control_due_ms = None
                self._schedule_control(now_ms, immediate=True)
            if not latch_matches:
                try:
                    self._sensor_stop_latch = self._new_sensor_stop_latch(
                        role, health, reason, now_ms, session_id
                    )
                except Exception:
                    # The safe OFF intent and control evaluation are already
                    # committed.  A diagnostic-allocation failure must never
                    # leave a temperature-controlled session requested ON.
                    self.event_errors += 1
                    self.last_error = reason
                    self._set_sensor_alert(
                        role,
                        health,
                        "error",
                        "shutdown_latched",
                        reason,
                        now_ms,
                    )
                    return
                self._emit_event(
                    "sensor_shutdown_requested",
                    now_ms,
                    {
                        "role": role,
                        "health": health,
                        "session_id": session_id,
                        "requested_changed": changed,
                    },
                )
            self._set_sensor_alert(
                role, health, "error", "shutdown_latched", reason, now_ms
            )
            return

        if self._requested.on:
            changed = self.request_stop()
            self._pending_control_due_ms = None
            self._set_sensor_alert(
                role, health, "error", "start_blocked", reason, now_ms
            )
            self._emit_event(
                "sensor_start_blocked",
                now_ms,
                {
                    "role": role,
                    "health": health,
                    "requested_changed": changed,
                },
            )

    def _emit_event(self, event_type, now_ms, details=None):
        try:
            event = {
                "type": event_type,
                "at_ms": now_ms,
                "details": (
                    dict(details) if isinstance(details, dict) else {}
                ),
            }
            if len(self._events) >= self._event_capacity:
                self._events.pop(0)
                self.events_dropped += 1
            self._events.append(event)
        except Exception:
            # Diagnostics must never interrupt a control decision that may
            # already have reached the protocol port.
            self.event_errors += 1
            return False
        return True

    def drain_events(self):
        events = self._events
        self._events = []
        return events

    def request_start(
        self,
        mode,
        target_temperature=None,
        power_level=None,
        runtime_minutes=60,
        source="manual",
        not_after_ms=None,
        now_ms=None,
    ):
        validate_start_request(
            mode,
            target_temperature,
            power_level,
            runtime_minutes,
            source,
            self.maximum_runtime_minutes,
        )
        if (not_after_ms is None) != (now_ms is None):
            raise ValueError("not_after_ms and now_ms must be provided together")
        if not_after_ms is not None:
            _require_ticks(not_after_ms)
            _require_ticks(now_ms)
            if source not in ("timer", "manual", "quick_start"):
                raise ValueError("request deadline source is not allowed")
            if self._ticks_diff(now_ms, not_after_ms) > 0:
                raise RuntimeError("timer start request has expired")
        if self._sensor_stop_latch is not None:
            raise RuntimeError(
                "cannot start while a sensor shutdown remains latched"
            )
        if self._control_attempt is not None or self._restart_blocked:
            raise RuntimeError(
                "cannot start while a control transition is unresolved"
            )
        if self._session is not None or self._requested.on:
            requested_target = (
                power_level
                if mode == CONTROL_MODE_POWER
                else target_temperature
            )
            current_target = (
                self._requested.power_level
                if self._requested.mode == CONTROL_MODE_POWER
                else self._requested.target_temperature
            )
            if (
                self._requested.on
                and self._requested.mode == mode
                and current_target == requested_target
                and self._requested.runtime_minutes == runtime_minutes
                and self._requested.source == source
            ):
                return False
            if self._session is not None:
                raise RuntimeError(
                    "active-session changes require the future session-update API"
                )
            raise RuntimeError(
                "pending start request parameters cannot be changed"
            )

        was_on = self._requested.on
        next_revision = self._request_revision
        if not was_on:
            # Precompute the potentially allocating integer before Requested
            # State can become ON.  A failed allocation must never leave a
            # timer start without its revision/deadline metadata.
            next_revision = self._request_revision + 1
        self._requested.request_start(
            mode=mode,
            target_temperature=target_temperature,
            power_level=power_level,
            runtime_minutes=runtime_minutes,
            source=source,
            maximum_runtime_minutes=self.maximum_runtime_minutes,
        )
        self._request_revision = next_revision
        self._request_not_after_ms = not_after_ms
        self._request_requested_at_ms = now_ms
        if mode == CONTROL_MODE_POWER and self._sensor_stop_latch is None:
            self._sensor_alert = None
            self._restore_noncommunication_error()
        return not was_on

    def request_stop(self):
        changed = self._requested.on
        self._requested.request_stop()
        self._request_not_after_ms = None
        self._request_requested_at_ms = None
        if changed:
            self._request_revision += 1
        return changed

    def update_active_session(
        self,
        expected_request_revision,
        target_temperature=None,
        extend_minutes=0,
        now_ms=None,
    ):
        """Adjust one confirmed same-mode session without direct protocol I/O."""

        _require_ticks(expected_request_revision)
        _require_ticks(now_ms)
        if (
            isinstance(extend_minutes, bool)
            or not isinstance(extend_minutes, int)
            or extend_minutes not in (0, 15)
        ):
            raise ValueError("extend_minutes must be 0 or 15")
        if target_temperature is not None and (
            isinstance(target_temperature, bool)
            or not isinstance(target_temperature, int)
            or target_temperature < 5
            or target_temperature > 30
        ):
            raise ValueError("target_temperature must be 5 to 30")
        if target_temperature is None and extend_minutes == 0:
            raise ValueError("session update is empty")
        if self._request_revision != expected_request_revision:
            raise RuntimeError("Requested State revision changed")
        session = self._session
        if (
            not self._requested.on
            or session is None
            or session.expired
            or not session.confirmed_active
            or self._control_attempt is not None
            or self._control_fault is not None
            or self._restart_blocked
        ):
            raise RuntimeError("active session is not safely adjustable")
        if (
            session.mode != self._requested.mode
            or session.runtime_minutes != self._requested.runtime_minutes
        ):
            raise RuntimeError("active session truth differs")
        if self._ticks_diff(session.expires_at_ms, now_ms) <= 0:
            raise RuntimeError("active session already expired")

        current_target = self._requested.target_temperature
        if self._requested.mode == CONTROL_MODE_POWER:
            if target_temperature is not None:
                raise ValueError("power sessions have no temperature target")
            if session.target != self._requested.power_level:
                raise RuntimeError("active power session truth differs")
        elif session.target != current_target:
            raise RuntimeError("active temperature session truth differs")

        next_target = (
            current_target
            if target_temperature is None
            else target_temperature
        )
        next_runtime = session.runtime_minutes + extend_minutes
        if next_runtime > self.maximum_runtime_minutes:
            raise ValueError("extended runtime exceeds configured maximum")
        next_expiry = session.expires_at_ms
        if extend_minutes:
            next_expiry = self._ticks_add(
                session.expires_at_ms, extend_minutes * 60 * 1000
            )
            _require_ticks(next_expiry)
        changed = (
            next_target != current_target
            or next_runtime != session.runtime_minutes
        )
        if not changed:
            return False

        next_revision = self._request_revision + 1
        self._requested.update_active_session(
            next_target,
            next_runtime,
            self.maximum_runtime_minutes,
        )
        session.target = (
            self._requested.power_level
            if self._requested.mode == CONTROL_MODE_POWER
            else next_target
        )
        session.runtime_minutes = next_runtime
        session.expires_at_ms = next_expiry
        self._request_revision = next_revision
        self._emit_event(
            "session_updated",
            now_ms,
            {
                "session_id": session.session_id,
                "target_temperature": next_target,
                "runtime_minutes": next_runtime,
                "request_revision": next_revision,
            },
        )
        return True

    def retry_control_fault(self, now_ms):
        """Explicitly authorize one new bounded control-attempt generation.

        A fault is never silently rearmed.  The caller must make this explicit,
        and the controller still requires a newly requested STATUS response
        before it can act.
        """

        _require_ticks(now_ms)
        if self._control_fault is None:
            return False
        if (
            self._actual.communication != COMMUNICATION_OK
            or not self._actual.initialized
            or not self._actual.synchronized
        ):
            raise RuntimeError(
                "control fault cannot be retried without synchronized status"
            )
        command = self._control_fault["command"]
        self._request_revision += 1
        self._control_fault = None
        self._control_attempt = None
        self._restore_noncommunication_error()
        self._pending_control_due_ms = None
        self._phase = PHASE_WAIT_STATUS
        if self._expected_command is None:
            self._next_request_due_ms = now_ms
        self._emit_event(
            "control_fault_retry_authorized",
            now_ms,
            {"command": command, "request_revision": self._request_revision},
        )
        return True

    def _due(self, now_ms, deadline_ms):
        return (
            deadline_ms is not None
            and self._ticks_diff(now_ms, deadline_ms) >= 0
        )

    def _schedule_control(self, now_ms, immediate=False):
        if self._pending_control_due_ms is not None:
            return
        delay = 0 if immediate else self.control_settle_ms
        self._pending_control_due_ms = self._ticks_add(now_ms, delay)

    def _mark_communication_error(self, now_ms, reason=None):
        was_error = self._actual.communication == COMMUNICATION_ERROR
        if not was_error:
            self.communication_failures += 1
        self._actual.mark_communication_error()
        self._communication_fault_active = True
        self._phase = PHASE_ERROR
        self._pending_control_due_ms = None
        self._expected_command = None
        self._response_wait_started_ms = None
        if self._next_request_due_ms is None:
            self._next_request_due_ms = now_ms
        if reason is not None:
            self.last_error = str(reason)
        if not was_error:
            self._emit_event(
                "communication_error",
                now_ms,
                {"reason": self.last_error},
            )

    def report_communication_error(self, reason=None, now_ms=None):
        """Fail closed when the protocol/transport reports an error."""

        if now_ms is not None:
            _require_ticks(now_ms)
        self._mark_communication_error(now_ms, reason)

    def _call_protocol(self, method_name, now_ms, *args, **kwargs):
        try:
            result = getattr(self._protocol, method_name)(*args, **kwargs)
            if result is False:
                raise RuntimeError("protocol port returned False")
        except Exception as exc:
            self._mark_communication_error(
                now_ms, "{} failed: {}".format(method_name, exc)
            )
            return False
        return True

    def _begin_response_wait(self, command, now_ms):
        if self._expected_command != command:
            self._expected_command = command
            self._response_wait_started_ms = now_ms

    def _request_synchronization(self, now_ms):
        self._next_request_due_ms = self._ticks_add(
            now_ms, self.heartbeat_ms
        )

        if self._actual.initialized:
            if self._phase != PHASE_ERROR:
                self._phase = PHASE_WAIT_STATUS
            if self._call_protocol("request_status", now_ms):
                self._begin_response_wait(CMD_STATUS, now_ms)
                return "status"
            return "status_error"

        if self._phase != PHASE_ERROR:
            self._phase = PHASE_WAIT_INIT
        if self._call_protocol("request_initialization", now_ms):
            self._begin_response_wait(CMD_INIT, now_ms)
            return "initialization"
        return "initialization_error"

    def _check_response_timeout(self, now_ms):
        if (
            self._expected_command is None
            or self._response_wait_started_ms is None
        ):
            return
        if (
            self._ticks_diff(now_ms, self._response_wait_started_ms)
            >= self.response_timeout_ms
        ):
            self._mark_communication_error(now_ms, "heater response timeout")

    def _expected_response_is_timely(self, command, now_ms):
        if (
            self._expected_command != command
            or self._response_wait_started_ms is None
        ):
            return False
        if (
            self._ticks_diff(now_ms, self._response_wait_started_ms)
            >= self.response_timeout_ms
        ):
            self._mark_communication_error(
                now_ms, "late heater response rejected"
            )
            return False
        return True

    @staticmethod
    def _known_frame_is_consistent(frame, command, payload_length):
        raw = frame.get("raw")
        if not isinstance(raw, bytes):
            return False
        if len(raw) != payload_length + 7:
            return False
        if (
            raw[0] != FRAME_START
            or raw[1] != DEVICE_HEATER
            or raw[2] != payload_length
            or raw[3] != 0
            or raw[4] != command
        ):
            return False
        if (
            frame.get("device") != raw[1]
            or frame.get("payload_length") != raw[2]
            or frame.get("reserved") != raw[3]
            or frame.get("command") != raw[4]
            or frame.get("payload") != raw[5:-2]
        ):
            return False
        received = (raw[-2] << 8) | raw[-1]
        return (
            frame.get("crc_valid") is True
            and frame.get("crc_received") == received
            and frame.get("crc_calculated") == received
        )

    def _status_is_consistent(self, frame):
        if not self._known_frame_is_consistent(
            frame, CMD_STATUS, _STATUS_RX_PAYLOAD_LENGTH
        ):
            return False
        status = frame.get("status")
        raw = frame["raw"]
        if not isinstance(status, dict):
            return False
        return (
            status.get("voltage") == raw[11] / 10.0
            and status.get("glow_plug_raw") == raw[13]
            and status.get("heater_state") == raw[14]
            and status.get("fan_raw") == raw[19]
        )

    def _record_invalid_frame(self, now_ms, reason="invalid heater frame"):
        self.invalid_frames += 1
        self._invalid_frame_strikes += 1
        self._consecutive_valid_statuses = 0
        self._pending_control_due_ms = None
        if self._invalid_frame_strikes >= self.invalid_frame_threshold:
            self._mark_communication_error(now_ms, reason)

    def _clear_control_attempt(self, now_ms, result):
        attempt = self._control_attempt
        if attempt is not None:
            self._emit_event(
                "control_{}".format(result),
                now_ms,
                {
                    "command": attempt["command"],
                    "attempts": attempt["attempts"],
                },
            )
        self._control_attempt = None

    def _clear_control_fault(self, now_ms, reason):
        if self._control_fault is None:
            return
        command = self._control_fault["command"]
        self._control_fault = None
        self._control_attempt = None
        self._restore_noncommunication_error()
        self._emit_event(
            "control_fault_cleared",
            now_ms,
            {"command": command, "reason": reason},
        )

    def _discard_session(self):
        self._session = None
        self._session_sensor_rom_id = None
        self._session_sensor_failure_generation = None
        self._session_sensor_assignment_revision = None

    def _complete_sensor_stop_if_off(self, now_ms):
        if (
            self._actual.heater_state != HEATER_STATE_OFF
            or self._requested.on
            or self._session is not None
            or self._sensor_stop_latch is None
        ):
            return False
        completed = self._sensor_stop_latch
        self._sensor_stop_latch = None
        self._sensor_alert = None
        self._emit_event(
            "sensor_shutdown_completed",
            now_ms,
            {
                "role": completed["role"],
                "session_id": completed["session_id"],
            },
        )
        self._restore_noncommunication_error()
        return True

    def _process_control_confirmation(
        self, now_ms, previous_state, recovering=False
    ):
        state = self._actual.heater_state
        attempt = self._control_attempt

        unexpected_stop = (
            state == HEATER_STATE_OFF
            and (
                previous_state
                in (
                    HEATER_STATE_STARTING,
                    HEATER_STATE_RUNNING,
                    HEATER_STATE_TEMP_MONITORING,
                )
                or (
                    recovering
                    and self._session is not None
                    and self._session.confirmed_active
                )
            )
            and self._requested.on
            and not self._restart_blocked
            and not (
                attempt is not None
                and attempt["command"] == _CONTROL_SHUTDOWN
            )
        )
        if unexpected_stop:
            self._requested.request_stop()
            self._request_not_after_ms = None
            self._request_requested_at_ms = None
            self._request_revision += 1
            self._discard_session()
            self._emit_event(
                "unexpected_stop",
                now_ms,
                {"previous_state": previous_state},
            )

        if state == HEATER_STATE_OFF:
            if self._restart_blocked:
                if self._requested.on:
                    self._requested.request_stop()
                    self._request_not_after_ms = None
                    self._request_requested_at_ms = None
                    self._request_revision += 1
                    self._emit_event(
                        "restart_request_cancelled",
                        now_ms,
                        {"reason": "heater reached OFF after shutdown"},
                    )
                self._restart_blocked = False
            if (
                not self._requested.on
                and not (
                    attempt is not None
                    and attempt["command"] == _CONTROL_START
                )
            ):
                self._discard_session()
            self._complete_sensor_stop_if_off(now_ms)

        if self._control_fault is not None:
            command = self._control_fault["command"]
            if command == _CONTROL_START:
                if state in (
                    HEATER_STATE_STARTING,
                    HEATER_STATE_RUNNING,
                    HEATER_STATE_TEMP_MONITORING,
                ):
                    self._clear_control_fault(now_ms, "late confirmation")
                elif not self._requested.on and state == HEATER_STATE_OFF:
                    self._clear_control_fault(now_ms, "request cancelled")
                    self._discard_session()
            elif command == _CONTROL_SHUTDOWN:
                if state in (HEATER_STATE_SHUTTING_DOWN, HEATER_STATE_OFF):
                    self._clear_control_fault(now_ms, "late confirmation")
                elif (
                    self._control_fault.get("recover_on_running") is True
                    and not self._requested.on
                    and state == HEATER_STATE_RUNNING
                ):
                    self._clear_control_fault(
                        now_ms, "state became shutdown-capable"
                    )
                elif self._requested.on and state == HEATER_STATE_RUNNING:
                    self._clear_control_fault(now_ms, "request cancelled")

        attempt = self._control_attempt
        if attempt is not None:
            if self._ticks_diff(now_ms, attempt["last_sent_ms"]) >= 0:
                attempt["status_after_send"] = True
            command = attempt["command"]
            if command == _CONTROL_START and state in (
                HEATER_STATE_STARTING,
                HEATER_STATE_RUNNING,
                HEATER_STATE_TEMP_MONITORING,
            ):
                self._clear_control_attempt(now_ms, "confirmed")
            elif command == _CONTROL_SHUTDOWN and state in (
                HEATER_STATE_SHUTTING_DOWN,
                HEATER_STATE_OFF,
            ):
                self._clear_control_attempt(now_ms, "confirmed")

        if (
            self._session is not None
            and state
            in (
                HEATER_STATE_STARTING,
                HEATER_STATE_RUNNING,
                HEATER_STATE_TEMP_MONITORING,
            )
        ):
            self._session.mark_confirmed_active()

    def _ensure_active_session(self, now_ms):
        if (
            self._session is not None
            or not self._requested.on
            or self._actual.heater_state
            not in (
                HEATER_STATE_STARTING,
                HEATER_STATE_RUNNING,
                HEATER_STATE_TEMP_MONITORING,
            )
        ):
            return
        try:
            self._start_session(now_ms)
        except Exception as exc:
            changed = self.request_stop()
            self._pending_control_due_ms = None
            self._schedule_control(now_ms, immediate=True)
            reason = "active session could not be supervised: {}".format(exc)
            role = _ACTIVE_SENSOR_ROLE_BY_MODE.get(self._requested.mode)
            if role is not None:
                self._set_sensor_alert(
                    role,
                    SENSOR_HEALTH_MISSING,
                    "error",
                    "session_blocked",
                    reason,
                    now_ms,
                )
            else:
                self.last_error = reason
            self._emit_event(
                "session_supervision_failed",
                now_ms,
                {"reason": reason, "requested_changed": changed},
            )
            return
        self._session.mark_confirmed_active()

    def handle_frame(self, frame, now_ms):
        """Consume one dictionary produced by ``parse_frame``.

        Only structurally complete, CRC-valid, real-shape heater INIT and
        STATUS responses may affect state.  Other raw data remains available
        to the protocol diagnostics layer.
        """

        _require_ticks(now_ms)
        try:
            frame = self._protocol.validate_inbound_frame(frame)
        except Exception as exc:
            self._record_invalid_frame(
                now_ms, "inbound validation failed: {}".format(exc)
            )
            self._mark_communication_error(
                now_ms, "inbound validation failed: {}".format(exc)
            )
            return False
        if not isinstance(frame, dict):
            self._record_invalid_frame(now_ms)
            return False
        if frame.get("device") != DEVICE_HEATER:
            self.ignored_frames += 1
            return False

        command = frame.get("command")
        if command not in (CMD_INIT, CMD_STATUS):
            self.ignored_frames += 1
            return False

        if command == CMD_INIT:
            valid = self._known_frame_is_consistent(
                frame, CMD_INIT, _INIT_RX_PAYLOAD_LENGTH
            )
        else:
            valid = self._status_is_consistent(frame)
        if not valid:
            self._record_invalid_frame(now_ms)
            return False

        if command == CMD_INIT:
            if not self._expected_response_is_timely(CMD_INIT, now_ms):
                self.ignored_frames += 1
                return False
            self._actual.mark_initialized()
            self._phase = PHASE_WAIT_STATUS
            self._expected_command = None
            self._response_wait_started_ms = None
            self._next_request_due_ms = now_ms
            self._pending_control_due_ms = None
            return True

        if (
            not self._actual.initialized
            or not self._expected_response_is_timely(CMD_STATUS, now_ms)
        ):
            self.ignored_frames += 1
            return False

        was_communication_error = self._communication_fault_active
        previous_state = self._actual.heater_state
        self._actual.update_from_status(frame["status"], now_ms)
        self._expected_command = None
        self._response_wait_started_ms = None
        self._next_request_due_ms = self._ticks_add(
            now_ms, self.heartbeat_ms
        )
        if was_communication_error:
            self._invalid_frame_strikes = 0
            self._consecutive_valid_statuses = 1
        else:
            self._consecutive_valid_statuses += 1
            if self._consecutive_valid_statuses >= self.invalid_frame_threshold:
                self._invalid_frame_strikes = 0

        self._process_control_confirmation(
            now_ms, previous_state, recovering=was_communication_error
        )

        if self._actual.synchronized:
            if self._control_fault is None:
                self._phase = PHASE_READY
                self._restore_noncommunication_error()
            else:
                self._phase = PHASE_ERROR
            self._schedule_control(now_ms)
            if was_communication_error:
                self._emit_event("communication_recovered", now_ms)
                self._communication_fault_active = False
        else:
            self._phase = PHASE_WAIT_STATUS
            self._pending_control_due_ms = None
        return True

    def _start_session(self, now_ms, session_spec=None):
        if self._session is not None:
            return
        if session_spec is None:
            session_spec = {
                "source": self._requested.source,
                "mode": self._requested.mode,
                "target": (
                    self._requested.power_level
                    if self._requested.mode == CONTROL_MODE_POWER
                    else self._requested.target_temperature
                ),
                "runtime_minutes": self._requested.runtime_minutes,
            }
        mode = session_spec["mode"]
        sensor_rom_id = None
        sensor_failure_generation = None
        sensor_assignment_revision = None
        role = _ACTIVE_SENSOR_ROLE_BY_MODE.get(mode)
        if role is not None:
            sensor = self._active_sensor
            if (
                not isinstance(sensor, dict)
                or sensor.get("role") != role
                or not isinstance(sensor.get("rom_id"), str)
                or not sensor.get("rom_id")
                or sensor.get("health")
                not in (SENSOR_HEALTH_OK, SENSOR_HEALTH_STALE)
            ):
                raise RuntimeError(
                    "temperature session lacks a usable bound sensor"
                )
            if (
                self._actual.heater_state == HEATER_STATE_OFF
                and (
                    sensor.get("health") != SENSOR_HEALTH_OK
                    or sensor.get("present") is not True
                )
            ):
                raise RuntimeError(
                    "new temperature session requires a fresh present sensor"
                )
            sensor_rom_id = sensor["rom_id"]
            sensor_failure_generation = sensor["failure_generation"]
            sensor_assignment_revision = sensor["assignment_revision"]

        runtime_minutes = session_spec["runtime_minutes"]
        runtime_ms = runtime_minutes * 60 * 1000
        self._session = HeaterSession(
            session_id=self._next_session_id,
            source=session_spec["source"],
            mode=session_spec["mode"],
            target=session_spec["target"],
            started_at_ms=now_ms,
            expires_at_ms=self._ticks_add(now_ms, runtime_ms),
            runtime_minutes=runtime_minutes,
        )
        self._session_sensor_rom_id = sensor_rom_id
        self._session_sensor_failure_generation = (
            sensor_failure_generation
        )
        self._session_sensor_assignment_revision = (
            sensor_assignment_revision
        )
        self._request_not_after_ms = None
        self._request_requested_at_ms = None
        self._next_session_id += 1
        self._emit_event(
            "session_started",
            now_ms,
            {"session_id": self._session.session_id},
        )

    def _check_session_expiry(self, now_ms):
        if self._session is None or self._session.expired:
            return
        if not self._due(now_ms, self._session.expires_at_ms):
            return

        self._session.expired = True
        if self._requested.on:
            self._requested.request_stop()
            self._request_not_after_ms = None
            self._request_requested_at_ms = None
            self._request_revision += 1
        self._emit_event(
            "session_expired",
            now_ms,
            {"session_id": self._session.session_id},
        )
        self._pending_control_due_ms = None
        self._schedule_control(now_ms, immediate=True)

    def _check_requested_start_deadline(self, now_ms):
        if (
            not self._requested.on
            or self._session is not None
            or self._request_not_after_ms is None
        ):
            return False
        if self._ticks_diff(now_ms, self._request_not_after_ms) <= 0:
            return False
        self._requested.request_stop()
        deadline = self._request_not_after_ms
        self._request_not_after_ms = None
        self._request_requested_at_ms = None
        self._request_revision += 1
        self._pending_control_due_ms = None
        self._emit_event(
            "requested_start_expired",
            now_ms,
            {"not_after_ms": deadline},
        )
        return True

    def _attempt_matches_current_request(self, attempt):
        if attempt["request_revision"] != self._request_revision:
            return False
        state = self._actual.heater_state
        if attempt["command"] == _CONTROL_START:
            return self._requested.on and state == HEATER_STATE_OFF
        return not self._requested.on and state == HEATER_STATE_RUNNING

    def _attempt_command_is_confirmed(self, attempt):
        state = self._actual.heater_state
        if attempt["command"] == _CONTROL_START:
            return state in (
                HEATER_STATE_STARTING,
                HEATER_STATE_RUNNING,
                HEATER_STATE_TEMP_MONITORING,
            )
        return state in (
            HEATER_STATE_SHUTTING_DOWN,
            HEATER_STATE_OFF,
        )

    def _attempt_was_cancelled(self, attempt):
        state = self._actual.heater_state
        if attempt["command"] == _CONTROL_START:
            return not self._requested.on and state == HEATER_STATE_OFF
        return self._requested.on and state == HEATER_STATE_RUNNING

    def _set_control_fault(self, now_ms, reason):
        attempt = self._control_attempt
        if attempt is None:
            return
        self.control_failures += 1
        self._control_fault = {
            "command": attempt["command"],
            "attempts": attempt["attempts"],
            "reason": reason,
            "recover_on_running": False,
        }
        self.last_error = reason
        self._phase = PHASE_ERROR
        self._pending_control_due_ms = None
        self._emit_event("control_error", now_ms, self._control_fault)

    def _set_policy_fault(
        self, now_ms, command, reason, recover_on_running=False
    ):
        if self._control_fault is not None:
            return
        self.control_failures += 1
        self._control_fault = {
            "command": command,
            "attempts": 0,
            "reason": reason,
            "recover_on_running": recover_on_running,
        }
        self.last_error = reason
        self._phase = PHASE_ERROR
        self._pending_control_due_ms = None
        self._emit_event("control_error", now_ms, self._control_fault)

    def _check_control_confirmation(self, now_ms):
        attempt = self._control_attempt
        if attempt is None or self._control_fault is not None:
            return
        if not attempt["status_after_send"]:
            return

        if self._attempt_command_is_confirmed(attempt):
            self._clear_control_attempt(now_ms, "resolved")
            return

        confirmation_expired = (
            self._ticks_diff(now_ms, attempt["last_sent_ms"])
            >= self.control_confirmation_timeout_ms
        )
        if not attempt["send_failed"] and not confirmation_expired:
            return

        if self._attempt_was_cancelled(attempt):
            command = attempt["command"]
            self._clear_control_attempt(now_ms, "cancelled")
            if command == _CONTROL_START and self._actual.heater_state == HEATER_STATE_OFF:
                self._discard_session()
            return

        if not self._attempt_matches_current_request(attempt):
            self._clear_control_attempt(now_ms, "cancelled")
            return
        if attempt["attempts"] >= self.max_control_attempts:
            self._set_control_fault(
                now_ms,
                "{} was not confirmed after {} attempts".format(
                    attempt["command"], attempt["attempts"]
                ),
            )
            return

        attempt["retry_ready"] = True
        self._schedule_control(now_ms, immediate=attempt["send_failed"])

    def _send_control(self, command, now_ms):
        if self._control_attempt is None:
            parameters = None
            session_spec = None
            if command == _CONTROL_START:
                validate_start_request(
                    self._requested.mode,
                    self._requested.target_temperature,
                    self._requested.power_level,
                    self._requested.runtime_minutes,
                    self._requested.source,
                    self.maximum_runtime_minutes,
                )
                parameters = {
                    "mode": self._requested.mode,
                    "target_temperature": (
                        self._requested.target_temperature
                    ),
                    "power_level": self._requested.power_level,
                }
                session_spec = {
                    "source": self._requested.source,
                    "mode": self._requested.mode,
                    "target": (
                        self._requested.power_level
                        if self._requested.mode == CONTROL_MODE_POWER
                        else self._requested.target_temperature
                    ),
                    "runtime_minutes": self._requested.runtime_minutes,
                }
            self._control_attempt = {
                "command": command,
                "attempts": 0,
                "last_sent_ms": None,
                "status_after_send": False,
                "send_failed": False,
                "retry_ready": False,
                "request_revision": self._request_revision,
                "parameters": parameters,
                "session_spec": session_spec,
            }

        attempt = self._control_attempt
        attempt["attempts"] += 1
        attempt["last_sent_ms"] = now_ms
        attempt["status_after_send"] = False
        attempt["send_failed"] = False
        attempt["retry_ready"] = False

        if command == _CONTROL_SHUTDOWN:
            self._restart_blocked = True
            self._shutdown_sent_revision = attempt["request_revision"]
            success = self._call_protocol("request_shutdown", now_ms)
        else:
            # Allocate and install the runtime deadline before the external
            # START side effect.  If allocation fails, no START may reach the
            # protocol port without supervision.
            try:
                self._start_session(now_ms, attempt["session_spec"])
            except Exception:
                self._control_attempt = None
                raise
            self._start_sent_revision = attempt["request_revision"]
            parameters = attempt["parameters"]
            success = self._call_protocol(
                "request_start", now_ms, **parameters
            )

        if success:
            self._next_request_due_ms = self._ticks_add(
                now_ms, self.heartbeat_ms
            )
            self._emit_event(
                "control_requested",
                now_ms,
                {
                    "command": command,
                    "attempt": attempt["attempts"],
                },
            )
            return command

        attempt["send_failed"] = True
        return "{}_error".format(command)

    def _evaluate_control(self, now_ms):
        self._pending_control_due_ms = None
        if (
            self._phase != PHASE_READY
            or self._actual.communication != COMMUNICATION_OK
            or not self._actual.initialized
            or not self._actual.synchronized
            or self._control_fault is not None
        ):
            return None

        attempt = self._control_attempt
        if attempt is not None:
            if attempt["retry_ready"] and self._attempt_matches_current_request(
                attempt
            ):
                return self._send_control(attempt["command"], now_ms)
            return None

        state = self._actual.heater_state
        if state != HEATER_STATE_STARTING or self._requested.on:
            self._starting_stop_since_ms = None
        if state == HEATER_STATE_OFF:
            if self._requested.on and not self._restart_blocked:
                if self._start_sent_revision == self._request_revision:
                    self._set_policy_fault(
                        now_ms,
                        _CONTROL_START,
                        "OFF returned after a completed START generation",
                    )
                    return None
                return self._send_control(_CONTROL_START, now_ms)
            return None

        # Confirmed behaviour of the working Node-RED controller: it waits for
        # RUNNING before requesting shutdown.  TEMP_MONITORING stop semantics
        # remain unknown and therefore fail closed without a command.
        if state == HEATER_STATE_STARTING:
            if not self._requested.on:
                if self._starting_stop_since_ms is None:
                    self._starting_stop_since_ms = now_ms
                    self._emit_event(
                        "shutdown_deferred",
                        now_ms,
                        {"state": HEATER_STATE_STARTING},
                    )
                elif (
                    self._ticks_diff(now_ms, self._starting_stop_since_ms)
                    >= self.starting_stop_policy_timeout_ms
                ):
                    self._set_policy_fault(
                        now_ms,
                        _CONTROL_SHUTDOWN,
                        "STARTING did not become RUNNING before stop-policy timeout",
                        recover_on_running=True,
                    )
            return None

        if state == HEATER_STATE_RUNNING:
            if not self._requested.on:
                if self._shutdown_sent_revision == self._request_revision:
                    self._set_policy_fault(
                        now_ms,
                        _CONTROL_SHUTDOWN,
                        "RUNNING returned after a completed SHUTDOWN generation",
                    )
                    return None
                return self._send_control(_CONTROL_SHUTDOWN, now_ms)
            return None

        if state == HEATER_STATE_TEMP_MONITORING:
            if not self._requested.on:
                self._set_policy_fault(
                    now_ms,
                    _CONTROL_SHUTDOWN,
                    "shutdown behaviour for TEMP_MONITORING is unconfirmed",
                    recover_on_running=True,
                )
            return None

        if state in (HEATER_STATE_SHUTTING_DOWN, HEATER_STATE_UNKNOWN):
            return None
        return None

    def _control_status_is_fresh(self, now_ms):
        last_status_ms = self._actual.last_status_ms
        if last_status_ms is None:
            return False
        age_ms = self._ticks_diff(now_ms, last_status_ms)
        return 0 <= age_ms < self.heartbeat_ms

    def step(self, now_ms):
        """Run one bounded controller cycle and return requested operations."""

        _require_ticks(now_ms)
        operations = []
        self._check_requested_start_deadline(now_ms)
        self._check_response_timeout(now_ms)
        self._check_session_expiry(now_ms)
        self._apply_sensor_policy(now_ms)
        self._ensure_active_session(now_ms)
        self._check_control_confirmation(now_ms)
        self._complete_sensor_stop_if_off(now_ms)

        if self._due(now_ms, self._pending_control_due_ms):
            if not self._control_status_is_fresh(now_ms):
                self._pending_control_due_ms = None
                if self._expected_command is None:
                    self._next_request_due_ms = now_ms
            else:
                operation = self._evaluate_control(now_ms)
                if operation is not None:
                    operations.append(operation)
                    return operations

        if (
            self._next_request_due_ms is None
            or self._due(now_ms, self._next_request_due_ms)
        ):
            operations.append(self._request_synchronization(now_ms))
        return operations

    def snapshot(self):
        session = None
        if self._session is not None:
            session = self._session.snapshot()
        control_attempt = None
        if self._control_attempt is not None:
            control_attempt = dict(self._control_attempt)
            parameters = control_attempt.get("parameters")
            if isinstance(parameters, dict):
                control_attempt["parameters"] = dict(parameters)
            session_spec = control_attempt.get("session_spec")
            if isinstance(session_spec, dict):
                control_attempt["session_spec"] = dict(session_spec)
        control_fault = None
        if self._control_fault is not None:
            control_fault = dict(self._control_fault)
        active_sensor = None
        if self._active_sensor is not None:
            active_sensor = dict(self._active_sensor)
        sensor_alert = None
        if self._sensor_alert is not None:
            sensor_alert = dict(self._sensor_alert)
        sensor_stop_latch = None
        if self._sensor_stop_latch is not None:
            sensor_stop_latch = dict(self._sensor_stop_latch)
        return {
            "phase": self._phase,
            "requested": self._requested.snapshot(),
            "actual": self._actual.snapshot(),
            "session": session,
            "session_sensor_rom_id": self._session_sensor_rom_id,
            "session_sensor_failure_generation": (
                self._session_sensor_failure_generation
            ),
            "session_sensor_assignment_revision": (
                self._session_sensor_assignment_revision
            ),
            "control_attempt": control_attempt,
            "control_fault": control_fault,
            "active_sensor": active_sensor,
            "sensor_alert": sensor_alert,
            "sensor_stop_latch": sensor_stop_latch,
            "temperature_manager_attached": (
                self._temperature_manager is not None
            ),
            "restart_blocked": self._restart_blocked,
            "request_not_after_ms": self._request_not_after_ms,
            "request_requested_at_ms": self._request_requested_at_ms,
            "starting_stop_since_ms": self._starting_stop_since_ms,
            "start_sent_revision": self._start_sent_revision,
            "shutdown_sent_revision": self._shutdown_sent_revision,
            "invalid_frames": self.invalid_frames,
            "ignored_frames": self.ignored_frames,
            "communication_failures": self.communication_failures,
            "control_failures": self.control_failures,
            "last_error": self.last_error,
            "events_pending": len(self._events),
            "events_dropped": self.events_dropped,
            "event_errors": self.event_errors,
            "invalid_frame_strikes": self._invalid_frame_strikes,
        }

    def public_snapshot(self):
        """Return the bounded, allowlisted controller view for local APIs.

        Diagnostic exception strings, control-attempt parameters and internal
        sensor latches are deliberately excluded.  Requested and Actual State
        remain separate so an accepted request is never presented as physical
        heater truth.
        """

        session = None
        if self._session is not None:
            session = self._session.snapshot()
        active_sensor = None
        if self._active_sensor is not None:
            active_sensor = {
                "role": self._active_sensor.get("role"),
                "value_c": self._active_sensor.get("value_c"),
                "age_ms": self._active_sensor.get("age_ms"),
                "health": self._active_sensor.get("health"),
                "usable": self._active_sensor.get("usable"),
                "present": self._active_sensor.get("present"),
            }
        return {
            "phase": self._phase,
            "request_revision": self._request_revision,
            "requested": self._requested.snapshot(),
            "actual": self._actual.snapshot(),
            "session": session,
            "control_transition_pending": self._control_attempt is not None,
            "control_faulted": self._control_fault is not None,
            "restart_blocked": self._restart_blocked,
            "sensor_stop_latched": self._sensor_stop_latch is not None,
            "active_sensor": active_sensor,
            "counters": {
                "invalid_frames": self.invalid_frames,
                "ignored_frames": self.ignored_frames,
                "communication_failures": self.communication_failures,
                "control_failures": self.control_failures,
                "events_dropped": self.events_dropped,
                "event_errors": self.event_errors,
            },
        }
