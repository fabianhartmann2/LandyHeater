"""Hardware-independent temperature assignment and health supervision.

This module intentionally performs no 1-Wire I/O.  The cooperative DS18B20
adapter feeds discovery results, valid samples and read failures
into this bounded core.  The manager never imports the heater protocol and
never requests a control command.
"""

import math
import time as _time


SENSOR_ROLE_ROOF_TENT = "roof_tent"
SENSOR_ROLE_CABIN = "cabin"
SENSOR_ROLE_OUTSIDE = "outside"

SENSOR_ROLES = (
    SENSOR_ROLE_ROOF_TENT,
    SENSOR_ROLE_CABIN,
    SENSOR_ROLE_OUTSIDE,
)

SENSOR_HEALTH_OK = "ok"
SENSOR_HEALTH_STALE = "stale"
SENSOR_HEALTH_FAILED = "failed"
SENSOR_HEALTH_MISSING = "missing"

SENSOR_HEALTH_STATES = (
    SENSOR_HEALTH_OK,
    SENSOR_HEALTH_STALE,
    SENSOR_HEALTH_FAILED,
    SENSOR_HEALTH_MISSING,
)

DEFAULT_STALE_AFTER_MS = 30000
DEFAULT_FAILED_AFTER_MS = 300000
DEFAULT_EVENT_CAPACITY = 16
DEFAULT_MAX_DISCOVERED_SENSORS = 16
DEFAULT_MINIMUM_TEMPERATURE_C = -55.0
DEFAULT_MAXIMUM_TEMPERATURE_C = 125.0
MAX_ROM_ID_LENGTH = 64
MAX_ERROR_LENGTH = 160


def _plain_ticks_diff(newer, older):
    return newer - older


_platform_ticks_diff = getattr(_time, "ticks_diff", _plain_ticks_diff)


def _require_positive_integer(name, value):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("{} must be a positive integer".format(name))


def _require_ticks(now_ms):
    if not isinstance(now_ms, int) or isinstance(now_ms, bool):
        raise ValueError("now_ms must be an integer")


def _normalize_rom_id(rom_id):
    if not isinstance(rom_id, str):
        raise ValueError("ROM ID must be a string")
    rom_id = rom_id.strip().lower()
    if not rom_id or len(rom_id) > MAX_ROM_ID_LENGTH:
        raise ValueError("ROM ID must be a non-empty bounded string")
    return rom_id


def _normalize_temperature(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < minimum or value > maximum:
        return None
    return value


class TemperatureManager:
    """Track three assigned sensors and derive bounded health snapshots."""

    def __init__(
        self,
        assignments=None,
        ticks_diff=None,
        stale_after_ms=DEFAULT_STALE_AFTER_MS,
        failed_after_ms=DEFAULT_FAILED_AFTER_MS,
        event_capacity=DEFAULT_EVENT_CAPACITY,
        max_discovered_sensors=DEFAULT_MAX_DISCOVERED_SENSORS,
        minimum_temperature_c=DEFAULT_MINIMUM_TEMPERATURE_C,
        maximum_temperature_c=DEFAULT_MAXIMUM_TEMPERATURE_C,
    ):
        _require_positive_integer("stale_after_ms", stale_after_ms)
        _require_positive_integer("failed_after_ms", failed_after_ms)
        _require_positive_integer("event_capacity", event_capacity)
        _require_positive_integer(
            "max_discovered_sensors", max_discovered_sensors
        )
        if failed_after_ms <= stale_after_ms:
            raise ValueError("failed_after_ms must exceed stale_after_ms")
        if ticks_diff is None:
            ticks_diff = _platform_ticks_diff
        if not callable(ticks_diff):
            raise ValueError("ticks_diff must be callable")

        minimum = _normalize_temperature(
            minimum_temperature_c,
            DEFAULT_MINIMUM_TEMPERATURE_C,
            DEFAULT_MAXIMUM_TEMPERATURE_C,
        )
        maximum = _normalize_temperature(
            maximum_temperature_c,
            DEFAULT_MINIMUM_TEMPERATURE_C,
            DEFAULT_MAXIMUM_TEMPERATURE_C,
        )
        if (
            minimum is None
            or maximum is None
            or minimum >= maximum
            or minimum < DEFAULT_MINIMUM_TEMPERATURE_C
            or maximum > DEFAULT_MAXIMUM_TEMPERATURE_C
        ):
            raise ValueError(
                "temperature bounds must be finite, increasing, and inside "
                "DS18B20 limits"
            )

        self._ticks_diff = ticks_diff
        self._stale_after_ms = stale_after_ms
        self._failed_after_ms = failed_after_ms
        self._minimum_temperature_c = minimum
        self._maximum_temperature_c = maximum
        self._max_discovered_sensors = max_discovered_sensors

        self._assignments = {role: None for role in SENSOR_ROLES}
        self._assignment_revision_by_role = {
            role: 0 for role in SENSOR_ROLES
        }
        self._readings = {}
        self._discovered = ()
        self._health_by_role = {
            role: SENSOR_HEALTH_MISSING for role in SENSOR_ROLES
        }

        self._events = []
        self._event_capacity = event_capacity
        self.events_dropped = 0
        self.event_errors = 0

        if assignments is None:
            assignments = {}
        self.configure_assignments(assignments)

    @property
    def stale_after_ms(self):
        return self._stale_after_ms

    @property
    def failed_after_ms(self):
        return self._failed_after_ms

    @property
    def minimum_temperature_c(self):
        return self._minimum_temperature_c

    @property
    def maximum_temperature_c(self):
        return self._maximum_temperature_c

    @property
    def max_discovered_sensors(self):
        return self._max_discovered_sensors

    @property
    def assignments(self):
        return dict(self._assignments)

    @property
    def assignment_revisions(self):
        return dict(self._assignment_revision_by_role)

    @staticmethod
    def _new_reading(rom_id):
        return {
            "rom_id": rom_id,
            "value_c": None,
            "last_valid_ms": None,
            "unavailable_since_ms": None,
            "last_failure_ms": None,
            "last_update_ms": None,
            "last_error": None,
            "invalid_readings": 0,
            "failure_generation": 0,
            "present": None,
        }

    def _time_precedes_reading(self, reading, now_ms):
        anchor_ms = reading["last_update_ms"]
        return (
            anchor_ms is not None
            and self._ticks_diff(now_ms, anchor_ms) < 0
        )

    def configure_assignments(self, assignments):
        """Atomically replace role-to-ROM assignments.

        ROM IDs are intentionally opaque persistent strings.  A later
        hardware adapter owns conversion from MicroPython's eight-byte ROM
        representation.
        """

        if not isinstance(assignments, dict):
            raise ValueError("assignments must be a dictionary")
        if len(assignments) > len(SENSOR_ROLES):
            raise ValueError("too many sensor-role assignments")
        unknown = [role for role in assignments if role not in SENSOR_ROLES]
        if unknown:
            raise ValueError("unknown sensor role: {}".format(unknown[0]))

        normalized = {role: None for role in SENSOR_ROLES}
        used = set()
        for role in SENSOR_ROLES:
            rom_id = assignments.get(role)
            if rom_id is None:
                continue
            rom_id = _normalize_rom_id(rom_id)
            if rom_id in used:
                raise ValueError("one ROM ID cannot be assigned twice")
            used.add(rom_id)
            normalized[role] = rom_id

        old_readings = self._readings
        readings = {}
        for rom_id in used:
            reading = old_readings.get(rom_id)
            if reading is None:
                reading = self._new_reading(rom_id)
                if self._discovered:
                    reading["present"] = rom_id in self._discovered
            readings[rom_id] = reading

        previous_assignments = self._assignments
        for role in SENSOR_ROLES:
            if normalized[role] != previous_assignments[role]:
                self._assignment_revision_by_role[role] += 1
        self._assignments = normalized
        self._readings = readings
        return True

    def record_discovery(self, rom_ids, now_ms):
        """Record one bounded, already completed hardware scan result."""

        _require_ticks(now_ms)
        if type(rom_ids) not in (list, tuple):
            raise ValueError("discovered ROM IDs must be a bounded sequence")
        if len(rom_ids) > self.max_discovered_sensors:
            raise ValueError("too many discovered sensors")

        normalized = []
        seen = set()
        for rom_id in rom_ids:
            rom_id = _normalize_rom_id(rom_id)
            if rom_id in seen:
                raise ValueError("duplicate discovered ROM ID")
            seen.add(rom_id)
            normalized.append(rom_id)

        for reading in self._readings.values():
            if self._time_precedes_reading(reading, now_ms):
                raise ValueError("discovery time precedes sensor state")

        self._discovered = tuple(normalized)
        for rom_id, reading in self._readings.items():
            reading["present"] = rom_id in seen
            if (
                not reading["present"]
                and reading["last_valid_ms"] is None
                and reading["unavailable_since_ms"] is None
            ):
                reading["unavailable_since_ms"] = now_ms
            reading["last_update_ms"] = now_ms
        self._refresh_all(now_ms)
        return len(normalized)

    def _record_failure(self, reading, now_ms, reason):
        if (
            reading["last_valid_ms"] is None
            and reading["unavailable_since_ms"] is None
        ):
            reading["unavailable_since_ms"] = now_ms
        reading["invalid_readings"] += 1
        reading["last_failure_ms"] = now_ms
        reading["last_update_ms"] = now_ms
        reading["last_error"] = reason

    def record_valid(self, rom_id, value_c, now_ms):
        """Store a valid sample without ever inventing a fallback value."""

        _require_ticks(now_ms)
        rom_id = _normalize_rom_id(rom_id)
        reading = self._readings.get(rom_id)
        if reading is None:
            return False
        if self._time_precedes_reading(reading, now_ms):
            return False

        value = _normalize_temperature(
            value_c,
            self.minimum_temperature_c,
            self.maximum_temperature_c,
        )
        if value is None:
            self._record_failure(reading, now_ms, "invalid temperature value")
            self._refresh_roles_for_rom(rom_id, now_ms)
            return False

        anchor_ms = reading["last_valid_ms"]
        if anchor_ms is None:
            anchor_ms = reading["unavailable_since_ms"]
        if (
            anchor_ms is not None
            and self._ticks_diff(now_ms, anchor_ms) >= self.failed_after_ms
        ):
            # Preserve a FAILED interval even when recovery arrives before the
            # controller's next cycle.  The transition generation is carried
            # in subsequent healthy snapshots and cannot be healed away.
            self._refresh_roles_for_rom(rom_id, now_ms)

        reading["value_c"] = value
        reading["last_valid_ms"] = now_ms
        reading["unavailable_since_ms"] = None
        reading["last_update_ms"] = now_ms
        reading["last_error"] = None
        reading["present"] = True
        self._refresh_roles_for_rom(rom_id, now_ms)
        return True

    def record_failure(self, rom_id, now_ms, reason=None):
        """Record a failed read while retaining the last valid sample."""

        _require_ticks(now_ms)
        rom_id = _normalize_rom_id(rom_id)
        reading = self._readings.get(rom_id)
        if reading is None:
            return False
        if self._time_precedes_reading(reading, now_ms):
            return False
        if reason is None:
            reason = "sensor read failed"
        if (
            not isinstance(reason, str)
            or not reason
            or len(reason) > MAX_ERROR_LENGTH
        ):
            raise ValueError("failure reason must be a bounded string")
        self._record_failure(reading, now_ms, reason)
        self._refresh_roles_for_rom(rom_id, now_ms)
        return True

    def _health_snapshot(self, role, now_ms):
        rom_id = self._assignments[role]
        if rom_id is None:
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
                "assignment_revision": self._assignment_revision_by_role[
                    role
                ],
                "last_error": None,
            }

        reading = self._readings[rom_id]
        if self._time_precedes_reading(reading, now_ms):
            raise ValueError("now_ms precedes sensor state")
        last_valid_ms = reading["last_valid_ms"]
        age_ms = None
        unavailable_since_ms = reading["unavailable_since_ms"]
        unavailable_age_ms = None
        health = SENSOR_HEALTH_MISSING
        if last_valid_ms is not None:
            age_ms = self._ticks_diff(now_ms, last_valid_ms)
            if age_ms < 0:
                raise ValueError("now_ms precedes the last valid sample")
            if age_ms >= self.failed_after_ms:
                health = SENSOR_HEALTH_FAILED
            elif age_ms >= self.stale_after_ms:
                health = SENSOR_HEALTH_STALE
            else:
                health = SENSOR_HEALTH_OK
        elif unavailable_since_ms is not None:
            unavailable_age_ms = self._ticks_diff(
                now_ms, unavailable_since_ms
            )
            if unavailable_age_ms < 0:
                raise ValueError("now_ms precedes sensor unavailability")
            if unavailable_age_ms >= self.failed_after_ms:
                health = SENSOR_HEALTH_FAILED

        return {
            "role": role,
            "rom_id": rom_id,
            "value_c": reading["value_c"],
            "last_valid_ms": last_valid_ms,
            "age_ms": age_ms,
            "unavailable_since_ms": unavailable_since_ms,
            "unavailable_age_ms": unavailable_age_ms,
            "health": health,
            "usable": health in (SENSOR_HEALTH_OK, SENSOR_HEALTH_STALE),
            "present": reading["present"],
            "invalid_readings": reading["invalid_readings"],
            "failure_generation": reading["failure_generation"],
            "assignment_revision": self._assignment_revision_by_role[role],
            "last_error": reading["last_error"],
        }

    def _emit_event(self, event_type, now_ms, details=None):
        try:
            event = {
                "type": event_type,
                "at_ms": now_ms,
                "details": dict(details) if isinstance(details, dict) else {},
            }
            if len(self._events) >= self._event_capacity:
                self._events.pop(0)
                self.events_dropped += 1
            self._events.append(event)
        except Exception:
            self.event_errors += 1
            return False
        return True

    def _refresh_role(self, role, now_ms):
        snapshot = self._health_snapshot(role, now_ms)
        previous = self._health_by_role[role]
        current = snapshot["health"]
        if current != previous:
            self._health_by_role[role] = current
            if current == SENSOR_HEALTH_FAILED and snapshot["rom_id"] is not None:
                reading = self._readings[snapshot["rom_id"]]
                reading["failure_generation"] += 1
                snapshot["failure_generation"] = reading[
                    "failure_generation"
                ]
            self._emit_event(
                "sensor_health_changed",
                now_ms,
                {
                    "role": role,
                    "rom_id": snapshot["rom_id"],
                    "previous": previous,
                    "current": current,
                    "age_ms": snapshot["age_ms"],
                },
            )
        return snapshot

    def _refresh_roles_for_rom(self, rom_id, now_ms):
        for role in SENSOR_ROLES:
            if self._assignments[role] == rom_id:
                self._refresh_role(role, now_ms)

    def _refresh_all(self, now_ms):
        return {
            role: self._refresh_role(role, now_ms)
            for role in SENSOR_ROLES
        }

    def sensor_snapshot(self, role, now_ms):
        """Return a detached, freshly aged snapshot for one semantic role."""

        _require_ticks(now_ms)
        if role not in SENSOR_ROLES:
            raise ValueError("unknown sensor role: {}".format(role))
        return dict(self._refresh_role(role, now_ms))

    def snapshot(self, now_ms):
        """Return all three freshly aged role snapshots and diagnostics."""

        _require_ticks(now_ms)
        sensors = self._refresh_all(now_ms)
        return {
            "sensors": {role: dict(sensors[role]) for role in SENSOR_ROLES},
            "assignments": dict(self._assignments),
            "assignment_revisions": dict(
                self._assignment_revision_by_role
            ),
            "discovered_rom_ids": tuple(self._discovered),
            "stale_after_ms": self.stale_after_ms,
            "failed_after_ms": self.failed_after_ms,
            "events_pending": len(self._events),
            "events_dropped": self.events_dropped,
            "event_errors": self.event_errors,
        }

    def drain_events(self):
        events = self._events
        self._events = []
        return events
