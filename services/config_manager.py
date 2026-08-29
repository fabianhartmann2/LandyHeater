"""Versioned, hardware-independent application configuration manager.

Phase 6 deliberately persists only cold-boot configuration and a separate,
minimal Scheduler safety ledger.  It never persists Requested/Actual heater
state, an active session, monotonic deadlines, RTC trust state, sensor samples,
hardware pins or protocol/TX authorization.  Construction and import perform
no filesystem or hardware I/O; callers explicitly invoke :meth:`load`,
:meth:`commit` and :meth:`checkpoint_scheduler`.
"""

from app.application_state import (
    CONTROL_MODE_ROOF_TENT_TEMPERATURE,
    validate_start_request,
)
from app.network_configuration import (
    default_network_configuration,
    validate_network_configuration,
)
from app.scheduler import (
    DEFAULT_MAX_TIMERS,
    MAX_OCCURRENCE_KEY_LENGTH,
    MAX_TIMER_ID_LENGTH,
    MAX_TIMER_NAME_LENGTH,
)
from app.temperature_manager import (
    DEFAULT_FAILED_AFTER_MS,
    DEFAULT_STALE_AFTER_MS,
    MAX_ROM_ID_LENGTH,
    SENSOR_ROLE_CABIN,
    SENSOR_ROLE_OUTSIDE,
    SENSOR_ROLE_ROOF_TENT,
    SENSOR_ROLES,
)
from services.time_service import (
    EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
    EUROPE_ZURICH_TIMEZONE_NAME,
    MAXIMUM_UTC_OFFSET_MINUTES,
    MAX_TIMEZONE_NAME_LENGTH,
    MINIMUM_UTC_OFFSET_MINUTES,
    TIMEZONE_RULE_EUROPE_ZURICH,
    TIMEZONE_RULE_FIXED,
    TIMEZONE_RULES,
    civil_to_utc_seconds,
    timezone_rule_version,
)
from services.configuration_errors import (
    ConfigurationConflictError,
    ConfigurationStateError,
    ConfigurationValidationError,
)


CONFIG_SCHEMA_VERSION = 2
SCHEDULER_LEDGER_SCHEMA_VERSION = 1
DEFAULT_EVENT_CAPACITY = 16
MAX_EVENT_CAPACITY = 64
MAXIMUM_RUNTIME_MINUTES = 120
MAXIMUM_SENSOR_HEALTH_MS = 3600000
MAX_MIGRATION_STEPS = 4
MAX_LOCAL_MINUTE_ID = 52595999
# The ESP32 must hold validation state, canonical JSON and the A/B publication
# envelope at the same time.  The Phase-7 board probe therefore establishes an
# 8-KiB aggregate application limit.  Individual field bounds still apply,
# and 32 timers plus eight ordinary Wi-Fi profiles fit inside this ceiling.
# Oversized legacy documents remain fail-closed and require explicit recovery.
MAX_CONFIGURATION_CANONICAL_BYTES = 8 * 1024

LOAD_FIRST_BOOT = "first_boot"
LOAD_OK = "ok"
LOAD_RECOVERY_REQUIRED = "recovery_required"
LOAD_INVALID = "invalid"

_CONFIG_FIELDS = frozenset((
    "schema_version",
    "system",
    "heater",
    "sensors",
    "time",
    "timers",
    "network",
))
_CONFIG_V1_FIELDS = frozenset((
    "schema_version",
    "system",
    "heater",
    "sensors",
    "time",
    "timers",
))
_SYSTEM_FIELDS = frozenset(("setup_complete",))
_HEATER_FIELDS = frozenset(("maximum_runtime_minutes", "quick_start"))
_START_FIELDS = frozenset((
    "mode",
    "target_temperature",
    "power_level",
    "runtime_minutes",
))
_SENSOR_FIELDS = frozenset((
    "assignments",
    "stale_after_ms",
    "failed_after_ms",
))
_TIME_FIELDS = frozenset((
    "timezone_name",
    "timezone_rule",
    "timezone_rule_version",
    "standard_utc_offset_minutes",
))
_TIMER_FIELDS = frozenset((
    "id",
    "name",
    "enabled",
    "weekdays",
    "start",
    "mode",
    "target_temperature",
    "power_level",
    "runtime_minutes",
))
_LEDGER_FIELDS = frozenset((
    "schema_version",
    "consumed_local_high_water",
    "occurrences",
))
_LEDGER_OCCURRENCE_FIELDS = frozenset((
    "timer_id",
    "occurrence_key",
    "local_minute_id",
    "status",
    "overridden",
))
_LEDGER_STATUSES = ("consumed", "overridden")


def _validation_error(message):
    raise ConfigurationValidationError(message)


def _require_exact_dict(name, value, fields):
    if type(value) is not dict:
        _validation_error("{} must be a dictionary".format(name))
    keys = frozenset(value)
    missing = fields - keys
    unknown = keys - fields
    if missing:
        _validation_error(
            "{} is missing field {}".format(name, sorted(missing)[0])
        )
    if unknown:
        _validation_error(
            "{} contains unknown field {}".format(name, sorted(unknown)[0])
        )
    return value


def _require_integer(name, value, minimum=None, maximum=None):
    if type(value) is not int:
        _validation_error("{} must be an integer".format(name))
    if minimum is not None and value < minimum:
        _validation_error("{} is below its minimum".format(name))
    if maximum is not None and value > maximum:
        _validation_error("{} exceeds its maximum".format(name))
    return value


def _bounded_text(name, value, maximum, allow_empty=False):
    if type(value) is not str:
        _validation_error("{} must be a string".format(name))
    normalized = value.strip()
    if (not normalized and not allow_empty) or len(normalized) > maximum:
        _validation_error("{} must be a bounded string".format(name))
    try:
        encoded = normalized.encode("utf-8")
    except (UnicodeError, ValueError):
        _validation_error("{} must be UTF-8 encodable".format(name))
    if len(encoded) > maximum:
        _validation_error("{} exceeds its UTF-8 byte limit".format(name))
    return normalized


def _clone_json(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is list:
        return [_clone_json(item) for item in value]
    if type(value) is dict:
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                _validation_error("configuration keys must be strings")
            result[key] = _clone_json(item)
        return result
    _validation_error("configuration contains a non-JSON value")


def _canonical_json_size(value, depth=0):
    """Return the exact byte size used by the canonical storage encoder."""

    if depth > 16:
        _validation_error("configuration JSON is too deeply nested")
    if value is None:
        return 4
    if type(value) is bool:
        return 4 if value else 5
    if type(value) is int:
        return len(str(value))
    if type(value) is str:
        size = 2
        for character in value:
            if character in ('"', "\\", "\b", "\f", "\n", "\r", "\t"):
                size += 2
            elif ord(character) < 0x20:
                size += 6
            else:
                try:
                    size += len(character.encode("utf-8"))
                except (UnicodeError, ValueError):
                    _validation_error("configuration is not valid UTF-8")
        return size
    if type(value) is list:
        size = 2
        for index, item in enumerate(value):
            if index:
                size += 1
            size += _canonical_json_size(item, depth + 1)
        return size
    if type(value) is dict:
        size = 2
        for index, (key, item) in enumerate(value.items()):
            if type(key) is not str:
                _validation_error("configuration keys must be strings")
            if index:
                size += 1
            size += _canonical_json_size(key, depth + 1)
            size += 1
            size += _canonical_json_size(item, depth + 1)
        return size
    _validation_error("configuration contains a non-JSON value")


def _is_ascii_digits(value):
    if type(value) is not str or not value:
        return False
    for character in value:
        if character < "0" or character > "9":
            return False
    return True


def _parse_start(value):
    if (
        type(value) is not str
        or len(value) != 5
        or value[2] != ":"
        or not _is_ascii_digits(value[:2])
        or not _is_ascii_digits(value[3:])
    ):
        _validation_error("timer start must use exact HH:MM format")
    try:
        hour = int(value[:2])
        minute = int(value[3:])
    except (TypeError, ValueError, OverflowError):
        _validation_error("timer start is malformed")
    if hour > 23 or minute > 59:
        _validation_error("timer start is outside 00:00-23:59")
    return hour, minute


def _normalize_start(value, maximum_runtime_minutes, name):
    value = _require_exact_dict(name, value, _START_FIELDS)
    mode = value["mode"]
    target = value["target_temperature"]
    power = value["power_level"]
    runtime = value["runtime_minutes"]
    try:
        validate_start_request(
            mode,
            target,
            power,
            runtime,
            "configuration",
            maximum_runtime_minutes,
        )
    except ValueError as exc:
        raise ConfigurationValidationError(str(exc)) from exc
    return {
        "mode": mode,
        "target_temperature": target,
        "power_level": power,
        "runtime_minutes": runtime,
    }


def _normalize_timer(value, maximum_runtime_minutes):
    value = _require_exact_dict("timer", value, _TIMER_FIELDS)
    timer_id = _bounded_text(
        "timer id", value["id"], MAX_TIMER_ID_LENGTH
    )
    if "|" in timer_id:
        _validation_error("timer id contains a reserved delimiter")
    name = _bounded_text(
        "timer name", value["name"], MAX_TIMER_NAME_LENGTH, True
    )
    enabled = value["enabled"]
    if type(enabled) is not bool:
        _validation_error("timer enabled must be boolean")
    weekdays = value["weekdays"]
    if type(weekdays) is not list or not weekdays or len(weekdays) > 7:
        _validation_error("timer weekdays must be a non-empty bounded list")
    normalized_weekdays = []
    for weekday in weekdays:
        _require_integer("timer weekday", weekday, 0, 6)
        if weekday in normalized_weekdays:
            _validation_error("timer weekdays must be unique")
        normalized_weekdays.append(weekday)
    normalized_weekdays.sort()
    _parse_start(value["start"])
    start_request = _normalize_start(
        {
            "mode": value["mode"],
            "target_temperature": value["target_temperature"],
            "power_level": value["power_level"],
            "runtime_minutes": value["runtime_minutes"],
        },
        maximum_runtime_minutes,
        "timer start request",
    )
    return {
        "id": timer_id,
        "name": name,
        "enabled": enabled,
        "weekdays": normalized_weekdays,
        "start": value["start"],
        "mode": start_request["mode"],
        "target_temperature": start_request["target_temperature"],
        "power_level": start_request["power_level"],
        "runtime_minutes": start_request["runtime_minutes"],
    }


def _normalize_time(value):
    value = _require_exact_dict("time", value, _TIME_FIELDS)
    timezone_name = _bounded_text(
        "timezone_name", value["timezone_name"], MAX_TIMEZONE_NAME_LENGTH
    )
    timezone_rule = value["timezone_rule"]
    if type(timezone_rule) is not str or timezone_rule not in TIMEZONE_RULES:
        _validation_error("unsupported timezone rule")
    configured_version = _require_integer(
        "timezone_rule_version", value["timezone_rule_version"], 1
    )
    expected_version = timezone_rule_version(timezone_rule)
    if configured_version != expected_version:
        _validation_error("timezone rule version is unsupported")
    standard_offset = _require_integer(
        "standard_utc_offset_minutes",
        value["standard_utc_offset_minutes"],
        MINIMUM_UTC_OFFSET_MINUTES,
        MAXIMUM_UTC_OFFSET_MINUTES,
    )
    if timezone_rule == TIMEZONE_RULE_EUROPE_ZURICH:
        if (
            timezone_name != EUROPE_ZURICH_TIMEZONE_NAME
            or standard_offset != EUROPE_ZURICH_STANDARD_OFFSET_MINUTES
        ):
            _validation_error("Europe/Zurich timezone tuple is inconsistent")
    elif timezone_name == EUROPE_ZURICH_TIMEZONE_NAME:
        _validation_error("Europe/Zurich name requires its canonical rule")
    return {
        "timezone_name": timezone_name,
        "timezone_rule": timezone_rule,
        "timezone_rule_version": configured_version,
        "standard_utc_offset_minutes": standard_offset,
    }


def default_configuration():
    """Return a fresh fail-safe configuration with no timer start authority."""

    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "system": {"setup_complete": False},
        "heater": {
            "maximum_runtime_minutes": MAXIMUM_RUNTIME_MINUTES,
            "quick_start": {
                "mode": CONTROL_MODE_ROOF_TENT_TEMPERATURE,
                "target_temperature": 20,
                "power_level": None,
                "runtime_minutes": 60,
            },
        },
        "sensors": {
            "assignments": {
                SENSOR_ROLE_ROOF_TENT: None,
                SENSOR_ROLE_CABIN: None,
                SENSOR_ROLE_OUTSIDE: None,
            },
            "stale_after_ms": DEFAULT_STALE_AFTER_MS,
            "failed_after_ms": DEFAULT_FAILED_AFTER_MS,
        },
        "time": {
            "timezone_name": EUROPE_ZURICH_TIMEZONE_NAME,
            "timezone_rule": TIMEZONE_RULE_EUROPE_ZURICH,
            "timezone_rule_version": timezone_rule_version(
                TIMEZONE_RULE_EUROPE_ZURICH
            ),
            "standard_utc_offset_minutes": (
                EUROPE_ZURICH_STANDARD_OFFSET_MINUTES
            ),
        },
        "timers": [],
        "network": default_network_configuration(),
    }


def default_scheduler_ledger():
    return {
        "schema_version": SCHEDULER_LEDGER_SCHEMA_VERSION,
        "consumed_local_high_water": None,
        "occurrences": [],
    }


def _validate_configuration(candidate, require_ap_password=None):
    """Return canonical schema v2 with an internal migration-only override."""

    candidate = _clone_json(candidate)
    candidate = _require_exact_dict(
        "configuration", candidate, _CONFIG_FIELDS
    )
    schema_version = _require_integer(
        "schema_version", candidate["schema_version"], 1
    )
    if schema_version != CONFIG_SCHEMA_VERSION:
        _validation_error("unsupported configuration schema version")

    system = _require_exact_dict(
        "system", candidate["system"], _SYSTEM_FIELDS
    )
    if type(system["setup_complete"]) is not bool:
        _validation_error("setup_complete must be boolean")

    heater = _require_exact_dict(
        "heater", candidate["heater"], _HEATER_FIELDS
    )
    maximum_runtime = _require_integer(
        "maximum_runtime_minutes",
        heater["maximum_runtime_minutes"],
        1,
        MAXIMUM_RUNTIME_MINUTES,
    )
    quick_start = _normalize_start(
        heater["quick_start"], maximum_runtime, "quick_start"
    )

    sensors = _require_exact_dict(
        "sensors", candidate["sensors"], _SENSOR_FIELDS
    )
    assignments = _require_exact_dict(
        "sensor assignments",
        sensors["assignments"],
        frozenset(SENSOR_ROLES),
    )
    normalized_assignments = {}
    used_roms = set()
    for role in SENSOR_ROLES:
        rom_id = assignments[role]
        if rom_id is not None:
            rom_id = _bounded_text("sensor ROM ID", rom_id, MAX_ROM_ID_LENGTH)
            # Case folding can expand Unicode text (for example U+0130).
            # Revalidate the canonical value so a document accepted for the
            # first commit is guaranteed to validate identically on reboot.
            rom_id = _bounded_text(
                "sensor ROM ID", rom_id.lower(), MAX_ROM_ID_LENGTH
            )
            if rom_id in used_roms:
                _validation_error("sensor ROM IDs must be unique")
            used_roms.add(rom_id)
        normalized_assignments[role] = rom_id
    stale_after = _require_integer(
        "stale_after_ms",
        sensors["stale_after_ms"],
        1,
        MAXIMUM_SENSOR_HEALTH_MS,
    )
    failed_after = _require_integer(
        "failed_after_ms",
        sensors["failed_after_ms"],
        1,
        MAXIMUM_SENSOR_HEALTH_MS,
    )
    if failed_after <= stale_after:
        _validation_error("failed_after_ms must exceed stale_after_ms")

    timers = candidate["timers"]
    if type(timers) is not list or len(timers) > DEFAULT_MAX_TIMERS:
        _validation_error("timers must be a bounded list")
    normalized_timers = []
    used_timer_ids = set()
    for timer in timers:
        normalized = _normalize_timer(timer, maximum_runtime)
        if normalized["id"] in used_timer_ids:
            _validation_error("timer IDs must be unique")
        used_timer_ids.add(normalized["id"])
        normalized_timers.append(normalized)

    if require_ap_password is None:
        require_ap_password = system["setup_complete"]
    elif type(require_ap_password) is not bool:
        _validation_error("network password policy must be boolean")

    result = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "system": {"setup_complete": system["setup_complete"]},
        "heater": {
            "maximum_runtime_minutes": maximum_runtime,
            "quick_start": quick_start,
        },
        "sensors": {
            "assignments": normalized_assignments,
            "stale_after_ms": stale_after,
            "failed_after_ms": failed_after,
        },
        "time": _normalize_time(candidate["time"]),
        "timers": normalized_timers,
        "network": validate_network_configuration(
            candidate["network"],
            require_ap_password=require_ap_password,
        ),
    }
    if _canonical_json_size(result) > MAX_CONFIGURATION_CANONICAL_BYTES:
        _validation_error("configuration canonical payload exceeds size limit")
    return result


def validate_configuration(candidate):
    """Return a detached, canonical schema-v2 configuration."""

    return _validate_configuration(candidate)


def migrate_configuration_v1_to_v2(candidate):
    """Explicitly add the unprovisioned Phase-7 network section.

    Schema v1 had no AP secret.  Migration therefore never invents a shared
    password or opens an unauthenticated AP.  ``setup_complete`` is cleared so
    the later setup flow must collect an individual password before the whole
    installation can again be declared complete.
    """

    candidate = _clone_json(candidate)
    candidate = _require_exact_dict(
        "schema-v1 configuration", candidate, _CONFIG_V1_FIELDS
    )
    version = _require_integer(
        "schema-v1 version", candidate["schema_version"], 1, 1
    )
    if version != 1:
        _validation_error("configuration is not schema version 1")
    migrated = _clone_json(candidate)
    migrated["schema_version"] = 2
    migrated["network"] = default_network_configuration()
    # Validate every released-v1 field, including the original setup flag,
    # before changing any meaning.  The internal password override exists
    # only because v1 had no network section or AP credential at all.
    migrated = _validate_configuration(
        migrated, require_ap_password=False
    )
    migrated["system"]["setup_complete"] = False
    return validate_configuration(migrated)


def _parse_occurrence_key(timer_id, occurrence_key):
    if type(occurrence_key) is not str:
        _validation_error("occurrence_key must be a string")
    if not occurrence_key or len(occurrence_key) > MAX_OCCURRENCE_KEY_LENGTH:
        _validation_error("occurrence_key exceeds its bound")
    parts = occurrence_key.split("|")
    if len(parts) != 3 or parts[0] != timer_id:
        _validation_error("occurrence_key does not match timer_id")
    date_value = parts[1]
    if (
        len(date_value) != 10
        or date_value[4] != "-"
        or date_value[7] != "-"
        or not _is_ascii_digits(date_value[:4])
        or not _is_ascii_digits(date_value[5:7])
        or not _is_ascii_digits(date_value[8:])
    ):
        _validation_error("occurrence date is malformed")
    hour, minute = _parse_start(parts[2])
    try:
        local_minute = civil_to_utc_seconds(
            int(date_value[:4]),
            int(date_value[5:7]),
            int(date_value[8:]),
            hour,
            minute,
            0,
        ) // 60
    except ValueError as exc:
        raise ConfigurationValidationError(
            "occurrence date is invalid"
        ) from exc
    return occurrence_key, local_minute


def validate_scheduler_ledger(candidate):
    """Return a detached, reboot-safe Scheduler ledger."""

    candidate = _clone_json(candidate)
    candidate = _require_exact_dict(
        "scheduler ledger", candidate, _LEDGER_FIELDS
    )
    schema_version = _require_integer(
        "scheduler ledger schema_version", candidate["schema_version"], 1
    )
    if schema_version != SCHEDULER_LEDGER_SCHEMA_VERSION:
        _validation_error("unsupported scheduler ledger schema version")
    high_water = candidate["consumed_local_high_water"]
    if high_water is not None:
        high_water = _require_integer(
            "consumed_local_high_water", high_water, 0, MAX_LOCAL_MINUTE_ID
        )
    occurrences = candidate["occurrences"]
    if type(occurrences) is not list or len(occurrences) > DEFAULT_MAX_TIMERS:
        _validation_error("ledger occurrences must be a bounded list")
    normalized = []
    timer_ids = set()
    occurrence_keys = set()
    for item in occurrences:
        item = _require_exact_dict(
            "ledger occurrence", item, _LEDGER_OCCURRENCE_FIELDS
        )
        timer_id = _bounded_text(
            "ledger timer_id", item["timer_id"], MAX_TIMER_ID_LENGTH
        )
        if "|" in timer_id:
            _validation_error("ledger timer_id contains a reserved delimiter")
        occurrence_key, calculated_minute = _parse_occurrence_key(
            timer_id, item["occurrence_key"]
        )
        local_minute = _require_integer(
            "ledger local_minute_id",
            item["local_minute_id"],
            0,
            MAX_LOCAL_MINUTE_ID,
        )
        if local_minute != calculated_minute:
            _validation_error("ledger local_minute_id differs from key")
        status = item["status"]
        overridden = item["overridden"]
        if status not in _LEDGER_STATUSES or type(status) is not str:
            _validation_error("ledger status is unsupported")
        if type(overridden) is not bool:
            _validation_error("ledger overridden must be boolean")
        if overridden is not (status == "overridden"):
            _validation_error("ledger status and override flag differ")
        if timer_id in timer_ids or occurrence_key in occurrence_keys:
            _validation_error("ledger entries must be unique")
        if high_water is None or local_minute > high_water:
            _validation_error("ledger entry exceeds its high-water mark")
        timer_ids.add(timer_id)
        occurrence_keys.add(occurrence_key)
        normalized.append({
            "timer_id": timer_id,
            "occurrence_key": occurrence_key,
            "local_minute_id": local_minute,
            "status": status,
            "overridden": overridden,
        })
    # Match Scheduler.export_persistent_history() exactly so a successful
    # durable readback is byte/order stable across the trust boundary.
    normalized.sort(key=lambda item: item["timer_id"])
    return {
        "schema_version": SCHEDULER_LEDGER_SCHEMA_VERSION,
        "consumed_local_high_water": high_water,
        "occurrences": normalized,
    }


def _safe_config_without_timers(configuration):
    result = _clone_json(configuration)
    result["timers"] = []
    result["system"]["setup_complete"] = False
    # A recovery survivor is not authority for radio credentials.  Requiring
    # a later normal whole-document commit with a freshly supplied AP secret
    # prevents an old/corrupt slot from silently re-enabling WLAN.
    result["network"] = default_network_configuration()
    return result


class ConfigManager:
    """Own static configuration and a separately committed Scheduler ledger."""

    def __init__(
        self,
        config_store=None,
        ledger_store=None,
        migrations=None,
        event_capacity=DEFAULT_EVENT_CAPACITY,
    ):
        _require_integer("event_capacity", event_capacity, 1, MAX_EVENT_CAPACITY)
        for name, store in (
            ("config_store", config_store),
            ("ledger_store", ledger_store),
        ):
            if store is None:
                continue
            for method in ("load_records", "commit", "status"):
                if not callable(getattr(store, method, None)):
                    raise ValueError("{} must provide {}()".format(name, method))
        if migrations is None:
            migrations = {}
        if type(migrations) is not dict or len(migrations) > MAX_MIGRATION_STEPS:
            raise ValueError("migrations must be a bounded dictionary")
        # Schema 1 is the released Phase-6 format.  Its migration is part of
        # the production contract and cannot be replaced by an injected test
        # or caller function.
        normalized_migrations = {1: migrate_configuration_v1_to_v2}
        for version, migration in migrations.items():
            if type(version) is not int or version < 0 or version >= CONFIG_SCHEMA_VERSION:
                raise ValueError("migration source version is invalid")
            if not callable(migration):
                raise ValueError("migration must be callable")
            if version == 1:
                raise ValueError("schema-v1 migration is built in")
            normalized_migrations[version] = migration

        self._config_store = config_store
        self._ledger_store = ledger_store
        self._migrations = normalized_migrations
        self._configuration = default_configuration()
        self._ledger = default_scheduler_ledger()
        self._generation = 0
        self._ledger_generation = 0
        self._load_status = LOAD_FIRST_BOOT
        self._ledger_load_status = LOAD_FIRST_BOOT
        self._source_slot = None
        self._ledger_source_slot = None
        self._migration_pending = False
        # Static configuration and the Scheduler safety ledger are separate
        # trust domains.  A successful repair of one must never clear a fault
        # still latched in the other.
        self._config_faulted = False
        self._ledger_faulted = False
        self._config_error = None
        self._ledger_error = None
        self._operation_active = False
        self._operation_reentered = False
        self._operational_faulted = False
        self._operational_error = None
        self._loaded = False
        self._ledger_loaded = False
        self._events = []
        self._event_capacity = event_capacity
        self.events_dropped = 0
        self.event_errors = 0

    @staticmethod
    def validate(candidate):
        return validate_configuration(candidate)

    @staticmethod
    def validate_scheduler_checkpoint(candidate):
        return validate_scheduler_ledger(candidate)

    def _emit(self, code, details=None):
        try:
            event = {"code": code}
            if details is not None:
                event["details"] = _clone_json(details)
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

    def _begin_operation(self):
        if self._operation_active:
            self._operation_reentered = True
            self._operational_faulted = True
            self._operational_error = "configuration_reentrant_operation"
            raise ConfigurationStateError(
                "configuration operation is already active"
            )
        self._operation_active = True
        self._operation_reentered = False

    def _finish_operation(self):
        reentered = self._operation_reentered
        self._operation_active = False
        self._operation_reentered = False
        if reentered:
            self._operational_faulted = True
            self._operational_error = "configuration_reentrant_operation"
            raise ConfigurationStateError(
                "configuration operation was re-entered"
            )

    def _migrate_configuration(self, payload):
        if type(payload) is not dict or type(payload.get("schema_version")) is not int:
            _validation_error("configuration schema version is missing")
        version = payload["schema_version"]
        migrated = _clone_json(payload)
        steps = 0
        while version < CONFIG_SCHEMA_VERSION:
            migration = self._migrations.get(version)
            if migration is None:
                _validation_error("configuration migration is unavailable")
            migrated = migration(_clone_json(migrated))
            if type(migrated) is not dict:
                _validation_error("configuration migration returned non-dict")
            next_version = migrated.get("schema_version")
            if type(next_version) is not int or next_version != version + 1:
                _validation_error("configuration migration did not advance once")
            version = next_version
            steps += 1
            if steps > MAX_MIGRATION_STEPS:
                _validation_error("too many configuration migrations")
        if version != CONFIG_SCHEMA_VERSION:
            _validation_error("unsupported future configuration schema")
        return validate_configuration(migrated), steps > 0

    @staticmethod
    def _record_contract(record):
        if type(record) is not dict or frozenset(record) != frozenset((
            "slot", "generation", "payload", "fingerprint",
            "canonical_payload"
        )):
            raise ConfigurationStateError("store returned malformed record")
        if record["slot"] not in ("a", "b") or type(record["slot"]) is not str:
            raise ConfigurationStateError("store returned invalid slot")
        _require_integer("store generation", record["generation"], 1)
        _require_integer("store fingerprint", record["fingerprint"], 0, 0xFFFFFFFF)
        if type(record["canonical_payload"]) is not str:
            raise ConfigurationStateError(
                "store returned invalid canonical payload"
            )
        return record

    @staticmethod
    def _choose_records(valid_records, invalid_count):
        if not valid_records:
            return None, LOAD_INVALID if invalid_count else LOAD_FIRST_BOOT
        if len(valid_records) == 1:
            # One slot is never start-authoritative.  A lone generation one
            # is indistinguishable from rollback after the newer slot was
            # lost.  Initial provisioning therefore writes generations one
            # and two before ConfigManager exposes LOAD_OK.
            return valid_records[0], LOAD_RECOVERY_REQUIRED
        first, second = valid_records
        if first["generation"] == second["generation"]:
            if (
                first["fingerprint"] != second["fingerprint"]
                or first["canonical_payload"] != second["canonical_payload"]
            ):
                return None, LOAD_INVALID
            return first, LOAD_OK if invalid_count == 0 else LOAD_RECOVERY_REQUIRED
        newest = first if first["generation"] > second["generation"] else second
        oldest = second if newest is first else first
        normal = (
            newest["generation"] == oldest["generation"] + 1
            and invalid_count == 0
        )
        return newest, LOAD_OK if normal else LOAD_RECOVERY_REQUIRED

    def _load_domain(self, store, validator, allow_migration=False):
        if store is None:
            return None, LOAD_FIRST_BOOT, None, 0, False
        records = store.load_records()
        if type(records) is not tuple or len(records) > 2:
            raise ConfigurationStateError("store returned an invalid record set")
        valid = []
        semantic_invalid = 0
        seen_slots = set()
        for raw_record in records:
            record = self._record_contract(raw_record)
            if record["slot"] in seen_slots:
                raise ConfigurationStateError(
                    "store returned one slot more than once"
                )
            seen_slots.add(record["slot"])
            try:
                if allow_migration:
                    canonical, migrated = self._migrate_configuration(
                        record["payload"]
                    )
                else:
                    canonical = validator(record["payload"])
                    migrated = False
            except (ConfigurationValidationError, ValueError):
                semantic_invalid += 1
                continue
            valid.append({
                "slot": record["slot"],
                "generation": record["generation"],
                "fingerprint": record["fingerprint"],
                "canonical_payload": record["canonical_payload"],
                "canonical": canonical,
                "migrated": migrated,
            })
        store_status = store.status()
        invalid_slots = store_status.get("invalid_slots", 0)
        if type(invalid_slots) is not int or invalid_slots < 0:
            raise ConfigurationStateError("store status is invalid")
        durability_unknown = store_status.get("durability_unknown", False)
        if type(durability_unknown) is not bool:
            raise ConfigurationStateError(
                "store durability status is invalid"
            )
        chosen, load_status = self._choose_records(
            valid, semantic_invalid + invalid_slots
        )
        if durability_unknown:
            # A post-publish sync/readback failure is sticky for this live
            # store instance.  Merely re-reading plausible bytes cannot prove
            # that power-loss durability was restored, so timer authority
            # remains closed until a fresh boot/reconciliation path.
            load_status = (
                LOAD_INVALID
                if chosen is None
                else LOAD_RECOVERY_REQUIRED
            )
        if chosen is None:
            return None, load_status, None, 0, False
        return (
            chosen["canonical"],
            load_status,
            chosen["slot"],
            chosen["generation"],
            chosen["migrated"],
        )

    def load(self):
        self._begin_operation()
        result = None
        primary = None
        try:
            result = self._load_configuration()
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                self._finish_operation()
            except BaseException:
                if primary is None:
                    raise
        return result

    def _load_configuration(self):
        """Load static configuration; unsafe recovery never restores timers."""

        # Close the start gate before any allocation or external port call.
        # A failure while staging defaults/recovery data must never leave a
        # previously trusted in-memory configuration start-authoritative.
        self._config_faulted = True
        self._config_error = "configuration_load_in_progress"
        try:
            configuration, status, slot, generation, migrated = self._load_domain(
                self._config_store, validate_configuration, True
            )
            config_faulted = False
            config_error = None
            if configuration is None:
                configuration = default_configuration()
                if status == LOAD_INVALID:
                    config_faulted = True
                    config_error = "configuration_invalid"
            elif status == LOAD_RECOVERY_REQUIRED:
                configuration = _safe_config_without_timers(configuration)
                config_faulted = True
                config_error = "configuration_recovery_required"
        except BaseException:
            self._config_faulted = True
            self._config_error = "configuration_load_failed"
            raise
        self._configuration = configuration
        self._generation = generation
        self._source_slot = slot
        self._load_status = status
        self._migration_pending = migrated
        self._loaded = True
        self._config_faulted = config_faulted
        self._config_error = config_error
        self._emit("configuration_loaded", {"status": status})
        return status == LOAD_OK

    def load_scheduler_checkpoint(self):
        self._begin_operation()
        result = None
        primary = None
        try:
            result = self._load_scheduler_checkpoint()
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                self._finish_operation()
            except BaseException:
                if primary is None:
                    raise
        return result

    def _load_scheduler_checkpoint(self):
        """Load only durable tombstones/high-water; never restore an active run."""

        self._ledger_faulted = True
        self._ledger_error = "scheduler_ledger_load_in_progress"
        try:
            ledger, status, slot, generation, _ = self._load_domain(
                self._ledger_store, validate_scheduler_ledger, False
            )
            ledger_faulted = False
            ledger_error = None
            if ledger is None or status != LOAD_OK:
                ledger = default_scheduler_ledger()
                if status in (LOAD_INVALID, LOAD_RECOVERY_REQUIRED):
                    ledger_faulted = True
                    ledger_error = "scheduler_ledger_untrusted"
                    # Never attach the untrusted persistent generation to an
                    # empty replacement ledger.  Ordinary checkpointing must
                    # not be able to erase tombstones/high-water after a
                    # rollback or corrupt-slot recovery.
                    generation = 0
                    slot = None
        except BaseException:
            self._ledger_faulted = True
            self._ledger_error = "scheduler_ledger_load_failed"
            raise
        self._ledger = ledger
        self._ledger_generation = generation
        self._ledger_source_slot = slot
        self._ledger_load_status = status
        self._ledger_loaded = True
        self._ledger_faulted = ledger_faulted
        self._ledger_error = ledger_error
        self._emit("scheduler_ledger_loaded", {"status": status})
        return status == LOAD_OK

    def snapshot(self):
        """Return the privileged full configuration, including credentials."""

        return {
            "generation": self._generation,
            "configuration": _clone_json(self._configuration),
        }

    def public_snapshot(self):
        """Return a detached settings view with every Wi-Fi secret removed."""

        configuration = _clone_json(self._configuration)
        network = configuration["network"]
        access_point = network["access_point"]
        public_profiles = []
        for profile in network["known_networks"]:
            public_profiles.append({
                "id": profile["id"],
                "ssid": profile["ssid"],
                "password_configured": profile["password"] is not None,
            })
        configuration["network"] = {
            "hostname": network["hostname"],
            "access_point": {
                "ssid": access_point["ssid"],
                "password_configured": access_point["password"] is not None,
            },
            "known_networks": public_profiles,
        }
        return {
            "generation": self._generation,
            "configuration": configuration,
        }

    def network_configuration_for_runtime(self):
        """Return credentials only across the explicit internal apply port."""

        if not self.network_start_allowed:
            raise ConfigurationStateError(
                "network configuration is not trusted or provisioned"
            )
        return {
            "generation": self._generation,
            "network": _clone_json(self._configuration["network"]),
        }

    def scheduler_checkpoint(self):
        return {
            "generation": self._ledger_generation,
            "ledger": _clone_json(self._ledger),
        }

    def scheduler_history_for_restore(self):
        """Return the schema-free shape consumed by Scheduler.restore()."""

        if self._ledger_load_status != LOAD_OK or self._ledger_faulted:
            raise ConfigurationStateError(
                "scheduler ledger is not trusted for restore"
            )
        configured_timer_ids = {
            timer["id"] for timer in self._configuration["timers"]
        }
        return {
            "consumed_local_high_water": self._ledger[
                "consumed_local_high_water"
            ],
            # Deleted timer latches remain durably covered by the global
            # high-water mark.  Scheduler.restore() intentionally accepts
            # records only for current definitions, so omit diagnostics for
            # removed IDs without weakening exactly-once protection.
            "occurrences": _clone_json([
                item for item in self._ledger["occurrences"]
                if item["timer_id"] in configured_timer_ids
            ]),
        }

    @property
    def generation(self):
        return self._generation

    @property
    def ledger_generation(self):
        return self._ledger_generation

    @property
    def faulted(self):
        return (
            self._config_faulted
            or self._ledger_faulted
            or self._operational_faulted
        )

    @property
    def last_error(self):
        if self._operational_error is not None:
            return self._operational_error
        if self._config_error is not None:
            return self._config_error
        return self._ledger_error

    @property
    def timer_start_allowed(self):
        return (
            self._loaded
            and self._ledger_loaded
            and self._load_status == LOAD_OK
            and self._ledger_load_status == LOAD_OK
            and not self.faulted
            and not self._operation_active
            and not self._migration_pending
            and self._configuration["system"]["setup_complete"] is True
        )

    @property
    def network_start_allowed(self):
        """Return whether the trusted config may be passed to WLAN code."""

        return (
            self._loaded
            and self._load_status == LOAD_OK
            and not self._config_faulted
            and not self._operational_faulted
            and not self._operation_active
            and not self._migration_pending
            and self._configuration["network"]["access_point"][
                "password"
            ] is not None
        )

    def commit(self, candidate, expected_generation):
        self._begin_operation()
        result = None
        primary = None
        try:
            result = self._commit_configuration(candidate, expected_generation)
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                self._finish_operation()
            except BaseException:
                if primary is None:
                    raise
        return result

    def _commit_configuration(self, candidate, expected_generation):
        """Atomically replace the complete static document."""

        _require_integer("expected_generation", expected_generation, 0)
        if not self._loaded:
            raise ConfigurationStateError(
                "configuration must be loaded before commit"
            )
        if expected_generation != self._generation:
            raise ConfigurationConflictError("configuration generation changed")
        if self._load_status in (LOAD_INVALID, LOAD_RECOVERY_REQUIRED):
            raise ConfigurationStateError(
                "untrusted configuration requires explicit recovery"
            )
        canonical = validate_configuration(candidate)
        if (
            (
                canonical["system"]["setup_complete"] is True
                or (
                    self._generation == 0
                    and self._load_status == LOAD_FIRST_BOOT
                )
            )
            and (
                not self._ledger_loaded
                or self._ledger_load_status != LOAD_OK
                or self._ledger_faulted
            )
        ):
            raise ConfigurationStateError(
                "trusted scheduler ledger must be provisioned first"
            )
        unchanged = canonical == self._configuration
        if unchanged and self._load_status == LOAD_OK and not self._migration_pending:
            return False
        if self._config_store is None:
            self._config_faulted = True
            self._config_error = "configuration_store_missing"
            raise ConfigurationStateError("no configuration store is attached")
        first_provision = (
            self._generation == 0 and self._load_status == LOAD_FIRST_BOOT
        )
        next_generation = 2 if first_provision else self._generation + 1
        try:
            if first_provision:
                result = self._config_store.commit(canonical, 1, 0)
                if result is not True:
                    raise ConfigurationStateError(
                        "store bootstrap commit did not return True"
                    )
                result = self._config_store.commit(canonical, 2, 1)
            else:
                result = self._config_store.commit(
                    canonical, next_generation, expected_generation
                )
            if result is not True:
                raise ConfigurationStateError(
                    "store commit did not return True"
                )
            (
                confirmed,
                confirmed_status,
                confirmed_slot,
                confirmed_generation,
                confirmed_migrated,
            ) = self._load_domain(
                self._config_store, validate_configuration, True
            )
            if (
                confirmed_status != LOAD_OK
                or confirmed_generation != next_generation
                or confirmed_migrated
                or confirmed != canonical
            ):
                raise ConfigurationStateError(
                    "configuration commit readback differs"
                )
        except BaseException:
            self._config_faulted = True
            self._config_error = "configuration_commit_failed"
            raise
        self._configuration = canonical
        self._generation = next_generation
        self._source_slot = confirmed_slot
        self._load_status = LOAD_OK
        self._migration_pending = False
        self._config_faulted = False
        self._config_error = None
        self._loaded = True
        self._emit("configuration_committed", {"generation": next_generation})
        return True

    @staticmethod
    def _ledger_is_monotone(previous, candidate):
        old_high = previous["consumed_local_high_water"]
        new_high = candidate["consumed_local_high_water"]
        if old_high is not None and (new_high is None or new_high < old_high):
            return False
        old_by_timer = {
            item["timer_id"]: item for item in previous["occurrences"]
        }
        new_by_timer = {
            item["timer_id"]: item for item in candidate["occurrences"]
        }
        for timer_id, old in old_by_timer.items():
            new = new_by_timer.get(timer_id)
            # The global high-water mark remains authoritative across timer
            # deletion and bounds history independently of timer IDs.  An old
            # per-timer diagnostic latch may therefore be compacted away once
            # its minute is covered by the non-regressing high-water mark.
            if new is None:
                if new_high is None or new_high < old["local_minute_id"]:
                    return False
                continue
            if new["local_minute_id"] < old["local_minute_id"]:
                return False
            if (
                new["local_minute_id"] == old["local_minute_id"]
                and new["occurrence_key"] != old["occurrence_key"]
            ):
                return False
            if (
                new["local_minute_id"] == old["local_minute_id"]
                and old["overridden"]
                and not new["overridden"]
            ):
                return False
        return True

    def checkpoint_scheduler_history(self, history, expected_generation):
        """Commit one schema-free Scheduler export behind the safety barrier."""

        self._begin_operation()
        primary = None
        try:
            return self._checkpoint_scheduler_history(
                history, expected_generation
            )
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                self._finish_operation()
            except BaseException:
                if primary is None:
                    raise

    def _checkpoint_scheduler_history(self, history, expected_generation):
        if type(history) is not dict or frozenset(history) != frozenset((
            "consumed_local_high_water", "occurrences"
        )):
            raise ConfigurationValidationError(
                "scheduler history has an invalid shape"
            )
        candidate = {
            "schema_version": SCHEDULER_LEDGER_SCHEMA_VERSION,
            "consumed_local_high_water": history[
                "consumed_local_high_water"
            ],
            "occurrences": history["occurrences"],
        }
        return self._checkpoint_scheduler(candidate, expected_generation)

    def checkpoint_scheduler(self, candidate, expected_generation):
        self._begin_operation()
        result = None
        primary = None
        try:
            result = self._checkpoint_scheduler(
                candidate, expected_generation
            )
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                self._finish_operation()
            except BaseException:
                if primary is None:
                    raise
        return result

    def _checkpoint_scheduler(self, candidate, expected_generation):
        """Durably advance Scheduler tombstones before start authorization."""

        _require_integer("expected_generation", expected_generation, 0)
        if not self._loaded or not self._ledger_loaded:
            raise ConfigurationStateError(
                "both persistence domains must be loaded before checkpoint"
            )
        if expected_generation != self._ledger_generation:
            raise ConfigurationConflictError("scheduler ledger generation changed")
        if self._ledger_load_status in (LOAD_INVALID, LOAD_RECOVERY_REQUIRED):
            raise ConfigurationStateError(
                "untrusted scheduler ledger requires explicit recovery"
            )
        if self._load_status in (LOAD_INVALID, LOAD_RECOVERY_REQUIRED):
            raise ConfigurationStateError(
                "untrusted configuration requires explicit recovery"
            )
        if (
            self._ledger_load_status == LOAD_FIRST_BOOT
            and self._load_status == LOAD_OK
            and self._generation > 0
        ):
            raise ConfigurationStateError(
                "missing scheduler ledger requires explicit recovery"
            )
        canonical = validate_scheduler_ledger(candidate)
        if not self._ledger_is_monotone(self._ledger, canonical):
            raise ConfigurationConflictError("scheduler ledger cannot regress")
        unchanged = canonical == self._ledger
        if unchanged and self._ledger_load_status == LOAD_OK:
            return False
        if self._ledger_store is None:
            self._ledger_faulted = True
            self._ledger_error = "scheduler_ledger_store_missing"
            raise ConfigurationStateError("no scheduler ledger store is attached")
        first_provision = (
            self._ledger_generation == 0
            and self._ledger_load_status == LOAD_FIRST_BOOT
        )
        next_generation = 2 if first_provision else self._ledger_generation + 1
        try:
            if first_provision:
                result = self._ledger_store.commit(canonical, 1, 0)
                if result is not True:
                    raise ConfigurationStateError(
                        "ledger bootstrap commit did not return True"
                    )
                result = self._ledger_store.commit(canonical, 2, 1)
            else:
                result = self._ledger_store.commit(
                    canonical, next_generation, expected_generation
                )
            if result is not True:
                raise ConfigurationStateError("ledger commit did not return True")
            (
                confirmed,
                confirmed_status,
                confirmed_slot,
                confirmed_generation,
                _,
            ) = self._load_domain(
                self._ledger_store, validate_scheduler_ledger, False
            )
            if (
                confirmed_status != LOAD_OK
                or confirmed_generation != next_generation
                or confirmed != canonical
            ):
                raise ConfigurationStateError(
                    "scheduler ledger commit readback differs"
                )
        except BaseException:
            self._ledger_faulted = True
            self._ledger_error = "scheduler_ledger_commit_failed"
            raise
        self._ledger = canonical
        self._ledger_generation = next_generation
        self._ledger_source_slot = confirmed_slot
        self._ledger_load_status = LOAD_OK
        self._ledger_loaded = True
        self._ledger_faulted = False
        self._ledger_error = None
        self._emit("scheduler_ledger_committed", {"generation": next_generation})
        return True

    def recover_configuration(self, candidate):
        """Explicitly reseal static storage as setup-incomplete and timer-free."""

        self._begin_operation()
        primary = None
        try:
            return self._recover_configuration(candidate)
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                self._finish_operation()
            except BaseException:
                if primary is None:
                    raise

    def _recover_configuration(self, candidate):
        if self._operational_faulted:
            raise ConfigurationStateError(
                "operational fault requires a fresh ConfigManager"
            )
        if not self._loaded:
            raise ConfigurationStateError(
                "configuration must be loaded before recovery"
            )
        if (
            self._load_status not in (LOAD_INVALID, LOAD_RECOVERY_REQUIRED)
            and not self._config_faulted
        ):
            raise ConfigurationStateError(
                "configuration recovery is not required"
            )
        reseal = getattr(self._config_store, "reseal", None)
        inspect_recovery = getattr(
            self._config_store, "inspect_recovery", None
        )
        if not callable(reseal) or not callable(inspect_recovery):
            raise ConfigurationStateError(
                "configuration store does not support explicit recovery"
            )
        safe_candidate = _safe_config_without_timers(
            validate_configuration(candidate)
        )
        recovery_view = inspect_recovery()
        if type(recovery_view) is not tuple or len(recovery_view) != 2:
            raise ConfigurationStateError(
                "configuration store returned an invalid recovery view"
            )
        recovery_records, recovery_signature = recovery_view
        if type(recovery_records) is not tuple:
            raise ConfigurationStateError(
                "configuration store returned invalid recovery records"
            )
        self._config_faulted = True
        self._config_error = "configuration_recovery_in_progress"
        try:
            generation = reseal(safe_candidate, recovery_signature)
            if type(generation) is not int or generation < 2:
                raise ConfigurationStateError(
                    "configuration reseal returned an invalid generation"
                )
            (
                confirmed,
                status,
                slot,
                confirmed_generation,
                migrated,
            ) = self._load_domain(
                self._config_store, validate_configuration, True
            )
            if (
                status != LOAD_OK
                or confirmed_generation != generation
                or migrated
                or confirmed != safe_candidate
            ):
                raise ConfigurationStateError(
                    "configuration recovery readback differs"
                )
        except BaseException:
            self._config_faulted = True
            self._config_error = "configuration_recovery_failed"
            raise
        self._configuration = safe_candidate
        self._generation = generation
        self._source_slot = slot
        self._load_status = LOAD_OK
        self._migration_pending = False
        self._config_faulted = False
        self._config_error = None
        self._emit("configuration_recovered", {"generation": generation})
        return True

    def recover_scheduler_ledger(self, recovery_local_minute_id):
        """Explicitly reseal an untrusted ledger at a trusted local minute."""

        self._begin_operation()
        primary = None
        try:
            return self._recover_scheduler_ledger(
                recovery_local_minute_id
            )
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                self._finish_operation()
            except BaseException:
                if primary is None:
                    raise

    def _recover_scheduler_ledger(self, recovery_local_minute_id):
        if self._operational_faulted:
            raise ConfigurationStateError(
                "operational fault requires a fresh ConfigManager"
            )
        if not self._loaded or not self._ledger_loaded:
            raise ConfigurationStateError(
                "both persistence domains must be loaded before recovery"
            )
        if (
            self._ledger_load_status
            not in (LOAD_FIRST_BOOT, LOAD_INVALID, LOAD_RECOVERY_REQUIRED)
            and not self._ledger_faulted
        ):
            raise ConfigurationStateError(
                "scheduler ledger recovery is not required"
            )
        recovery_local_minute_id = _require_integer(
            "recovery_local_minute_id",
            recovery_local_minute_id,
            0,
            MAX_LOCAL_MINUTE_ID,
        )
        reseal = getattr(self._ledger_store, "reseal", None)
        inspect_recovery = getattr(
            self._ledger_store, "inspect_recovery", None
        )
        if not callable(reseal) or not callable(inspect_recovery):
            raise ConfigurationStateError(
                "scheduler ledger store does not support explicit recovery"
            )
        # Recovery may discard untrusted occurrence details, but it must never
        # move the durable replay fence backwards.  A lone or topologically
        # inconsistent slot is not start-authoritative, yet any semantically
        # valid high-water mark it contains remains a conservative lower bound
        # for the newly resealed ledger.
        recovery_view = inspect_recovery()
        if type(recovery_view) is not tuple or len(recovery_view) != 2:
            raise ConfigurationStateError(
                "scheduler ledger store returned an invalid recovery view"
            )
        survivor_records, recovery_signature = recovery_view
        if type(survivor_records) is not tuple or len(survivor_records) > 2:
            raise ConfigurationStateError(
                "scheduler ledger store returned an invalid record set"
            )
        survivor_slots = set()
        survivor_high_water = None
        for raw_record in survivor_records:
            record = self._record_contract(raw_record)
            if record["slot"] in survivor_slots:
                raise ConfigurationStateError(
                    "scheduler ledger store returned one slot more than once"
                )
            survivor_slots.add(record["slot"])
            try:
                survivor = validate_scheduler_ledger(record["payload"])
            except (ConfigurationValidationError, ValueError):
                continue
            high_water = survivor["consumed_local_high_water"]
            if high_water is not None and (
                survivor_high_water is None or high_water > survivor_high_water
            ):
                survivor_high_water = high_water
        if (
            survivor_high_water is not None
            and survivor_high_water > recovery_local_minute_id
        ):
            recovery_local_minute_id = survivor_high_water
        candidate = {
            "schema_version": SCHEDULER_LEDGER_SCHEMA_VERSION,
            "consumed_local_high_water": recovery_local_minute_id,
            "occurrences": [],
        }
        self._ledger_faulted = True
        self._ledger_error = "scheduler_ledger_recovery_in_progress"
        try:
            generation = reseal(candidate, recovery_signature)
            if type(generation) is not int or generation < 2:
                raise ConfigurationStateError(
                    "scheduler ledger reseal returned an invalid generation"
                )
            (
                confirmed,
                status,
                slot,
                confirmed_generation,
                _,
            ) = self._load_domain(
                self._ledger_store, validate_scheduler_ledger, False
            )
            if (
                status != LOAD_OK
                or confirmed_generation != generation
                or confirmed != candidate
            ):
                raise ConfigurationStateError(
                    "scheduler ledger recovery readback differs"
                )
        except BaseException:
            self._ledger_faulted = True
            self._ledger_error = "scheduler_ledger_recovery_failed"
            raise
        self._ledger = candidate
        self._ledger_generation = generation
        self._ledger_source_slot = slot
        self._ledger_load_status = LOAD_OK
        self._ledger_faulted = False
        self._ledger_error = None
        self._emit("scheduler_ledger_recovered", {"generation": generation})
        return True

    def status(self):
        return {
            "loaded": self._loaded,
            "ledger_loaded": self._ledger_loaded,
            "generation": self._generation,
            "ledger_generation": self._ledger_generation,
            "load_status": self._load_status,
            "ledger_load_status": self._ledger_load_status,
            "source_slot": self._source_slot,
            "ledger_source_slot": self._ledger_source_slot,
            "migration_pending": self._migration_pending,
            "setup_complete": self._configuration["system"][
                "setup_complete"
            ],
            "faulted": self.faulted,
            "last_error": self.last_error,
            "config_faulted": self._config_faulted,
            "ledger_faulted": self._ledger_faulted,
            "operational_faulted": self._operational_faulted,
            "config_error": self._config_error,
            "ledger_error": self._ledger_error,
            "operational_error": self._operational_error,
            "operation_active": self._operation_active,
            "timer_start_allowed": self.timer_start_allowed,
            "network_start_allowed": self.network_start_allowed,
            "events_pending": len(self._events),
            "events_dropped": self.events_dropped,
            "event_errors": self.event_errors,
            "config_store": (
                None
                if self._config_store is None
                else _clone_json(self._config_store.status())
            ),
            "ledger_store": (
                None
                if self._ledger_store is None
                else _clone_json(self._ledger_store.status())
            ),
        }

    def public_status(self):
        """Return bounded persistence health without paths or error text."""

        def public_store(store):
            if store is None:
                return {"available": False}
            status = store.status()
            if type(status) is not dict:
                raise ConfigurationStateError(
                    "configuration store status is malformed"
                )
            result = {"available": True}
            for key in (
                "max_record_bytes",
                "durability_unknown",
                "reads",
                "writes",
                "invalid_slots",
                "slot_files_present",
                "temp_present",
            ):
                if key in status:
                    result[key] = status[key]
            return result

        return {
            "loaded": self._loaded,
            "ledger_loaded": self._ledger_loaded,
            "generation": self._generation,
            "ledger_generation": self._ledger_generation,
            "load_status": self._load_status,
            "ledger_load_status": self._ledger_load_status,
            "migration_pending": self._migration_pending,
            "setup_complete": self._configuration["system"][
                "setup_complete"
            ],
            "faulted": self.faulted,
            "config_faulted": self._config_faulted,
            "ledger_faulted": self._ledger_faulted,
            "operational_faulted": self._operational_faulted,
            "operation_active": self._operation_active,
            "timer_start_allowed": self.timer_start_allowed,
            "network_start_allowed": self.network_start_allowed,
            "events_pending": len(self._events),
            "events_dropped": self.events_dropped,
            "event_errors": self.event_errors,
            "config_store": public_store(self._config_store),
            "ledger_store": public_store(self._ledger_store),
        }
