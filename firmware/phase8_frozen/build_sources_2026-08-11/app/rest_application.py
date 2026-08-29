"""Hardware-free Phase-8 REST application for Landy Heater.

The router consumes one already parsed HTTP request and returns a small JSON
response model.  HTTP sockets, listener binding and deadlines live in a later
adapter.  Mutations require an AP-bound :class:`RestSecurityPolicy` decision
and are delegated only to the configuration and manual-control gateways.
Neither this module nor any error path imports or calls the heater protocol.
"""

import time as _time

from app.configuration_api_gateway import (
    ConfigurationAPIConflictError,
    ConfigurationAPIInvariantError,
    ConfigurationAPINotFoundError,
    ConfigurationAPIResourceConflictError,
    ConfigurationAPIValidationError,
)
from app.application_state import validate_start_request
from app.manual_control_gateway import (
    ManualControlConfigurationConflictError,
    ManualControlConflictError,
    ManualControlInvariantError,
    ManualControlStateConflictError,
    ManualControlUnavailableError,
)
from services.configuration_errors import (
    ConfigurationConflictError,
    ConfigurationStateError,
    ConfigurationValidationError,
)
from services.rest_security import (
    RestSecurityDenied,
    RestSecurityUnavailable,
)
from services.rest_rate_limiter import (
    RestRateLimitExceeded,
    RestRateLimitUnavailable,
)
from services.strict_json import (
    StrictJSONDecodeError,
    StrictJSONLimitError,
    decode_json_bytes,
)


API_VERSION = 1
API_PREFIX = "/api/v1"
MAX_TIMER_PAGE_SIZE = 8
MAX_HTTP_TARGET_BYTES = 192
MAX_WARNINGS = 16
MAX_REQUEST_ID = 0x7FFFFFFF

_JSON_CONTENT_TYPES = (
    "application/json",
    "application/json;charset=utf-8",
    "application/json; charset=utf-8",
)
_START_FIELDS = frozenset((
    "expected_request_revision",
    "mode",
    "target_temperature",
    "power_level",
    "runtime_minutes",
))
_QUICK_START_FIELDS = frozenset(("expected_request_revision",))
_SETTINGS_FIELDS = frozenset(("heater", "sensors", "time"))
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
_HEX = "0123456789abcdefABCDEF"
_UNRESERVED = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


class RestResponse:
    __slots__ = ("status", "body", "headers")

    def __init__(self, status, body, headers=None):
        self.status = status
        self.body = body
        self.headers = {} if headers is None else headers


class _RestProblem(Exception):
    __slots__ = ("status", "code", "message", "headers", "details")

    def __init__(
        self,
        status,
        code,
        message,
        headers=None,
        details=None,
    ):
        self.status = status
        self.code = code
        self.message = message
        self.headers = {} if headers is None else headers
        self.details = {} if details is None else details

    def __str__(self):
        return self.code


def _plain_ticks_ms():
    return 0


def _plain_ticks_diff(newer, older):
    return newer - older


_platform_ticks_ms = getattr(_time, "ticks_ms", _plain_ticks_ms)
_platform_ticks_diff = getattr(_time, "ticks_diff", _plain_ticks_diff)


def _require_integer(name, value, minimum=0, maximum=None):
    if type(value) is not int or value < minimum:
        raise ValueError("{} must be an integer".format(name))
    if maximum is not None and value > maximum:
        raise ValueError("{} exceeds its bound".format(name))
    return value


def _exact_dict(name, value, fields):
    if type(value) is not dict or frozenset(value) != frozenset(fields):
        raise ValueError("{} has an invalid shape".format(name))
    return value


def _contains_password_field(value, depth=0):
    if depth > 16:
        raise ValueError("public value is too deeply nested")
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("public value contains a non-string key")
            if key.lower() == "password":
                return True
            if _contains_password_field(item, depth + 1):
                return True
    elif type(value) in (list, tuple):
        for item in value:
            if _contains_password_field(item, depth + 1):
                return True
    return False


def _require_boolean(name, value):
    if type(value) is not bool:
        raise ValueError("{} must be boolean".format(name))
    return value


def _require_canonical_timer_dto(timer):
    _exact_dict("timer", timer, _TIMER_FIELDS)
    timer_id = timer["id"]
    name = timer["name"]
    if (
        type(timer_id) is not str
        or not timer_id
        or timer_id != timer_id.strip()
        or len(timer_id) > 64
        or "|" in timer_id
    ):
        raise ValueError("timer id is not canonical")
    if type(name) is not str:
        raise ValueError("timer name is not canonical")
    try:
        timer_id_bytes = timer_id.encode("utf-8")
        name_bytes = name.encode("utf-8")
    except (UnicodeError, ValueError):
        raise ValueError("timer text is not valid UTF-8") from None
    if len(timer_id_bytes) > 64 or len(name_bytes) > 80:
        raise ValueError("timer text exceeds its byte bound")
    if (
        type(name) is not str
        or name != name.strip()
        or len(name) > 80
    ):
        raise ValueError("timer name is not canonical")
    _require_boolean("timer enabled", timer["enabled"])
    weekdays = timer["weekdays"]
    if type(weekdays) is not list or not weekdays or len(weekdays) > 7:
        raise ValueError("timer weekdays are not canonical")
    previous = -1
    for weekday in weekdays:
        if type(weekday) is not int or weekday < 0 or weekday > 6:
            raise ValueError("timer weekday is invalid")
        if weekday <= previous:
            raise ValueError("timer weekdays are not canonical")
        previous = weekday
    start = timer["start"]
    if type(start) is not str or len(start) != 5 or start[2] != ":":
        raise ValueError("timer start is invalid")
    for index in (0, 1, 3, 4):
        if start[index] < "0" or start[index] > "9":
            raise ValueError("timer start is invalid")
    if int(start[:2]) > 23 or int(start[3:]) > 59:
        raise ValueError("timer start is invalid")
    return timer


def _config_etag(generation):
    _require_integer("configuration generation", generation)
    return '"config-{}"'.format(generation)


def _parse_config_etag(value):
    if type(value) is not str or len(value) < 10 or len(value) > 24:
        raise _RestProblem(
            400, "invalid_configuration_etag", "Invalid If-Match header"
        )
    if not value.startswith('"config-') or not value.endswith('"'):
        raise _RestProblem(
            400, "invalid_configuration_etag", "Invalid If-Match header"
        )
    digits = value[8:-1]
    if (
        not digits
        or (len(digits) > 1 and digits[0] == "0")
        or len(digits) > 10
    ):
        raise _RestProblem(
            400, "invalid_configuration_etag", "Invalid If-Match header"
        )
    for character in digits:
        if character < "0" or character > "9":
            raise _RestProblem(
                400, "invalid_configuration_etag", "Invalid If-Match header"
            )
    generation = int(digits)
    if generation > MAX_REQUEST_ID:
        raise _RestProblem(
            400, "invalid_configuration_etag", "Invalid If-Match header"
        )
    return generation


def _percent_decode_segment(segment):
    if type(segment) is not str or not segment or len(segment) > 192:
        raise _RestProblem(400, "invalid_timer_id", "Invalid timer id")
    result = bytearray()
    index = 0
    while index < len(segment):
        character = segment[index]
        if character == "%":
            if (
                index + 2 >= len(segment)
                or segment[index + 1] not in _HEX
                or segment[index + 2] not in _HEX
            ):
                raise _RestProblem(
                    400, "invalid_timer_id", "Invalid timer id"
                )
            result.append(int(segment[index + 1:index + 3], 16))
            index += 3
            continue
        try:
            encoded = character.encode("ascii")
        except (UnicodeError, ValueError):
            raise _RestProblem(
                400, "invalid_timer_id", "Invalid timer id"
            ) from None
        result.extend(encoded)
        index += 1
    for byte in result:
        if byte < 0x20 or byte in (0x2F, 0x5C, 0x7F):
            raise _RestProblem(400, "invalid_timer_id", "Invalid timer id")
    try:
        value = bytes(result).decode("utf-8")
    except (UnicodeError, ValueError):
        raise _RestProblem(
            400, "invalid_timer_id", "Invalid timer id"
        ) from None
    if not value or "|" in value:
        raise _RestProblem(400, "invalid_timer_id", "Invalid timer id")
    return value


def _percent_encode_segment(value):
    if type(value) is not str:
        raise ValueError("timer id must be a string")
    data = value.encode("utf-8")
    pieces = []
    for byte in data:
        character = chr(byte)
        if character in _UNRESERVED:
            pieces.append(character)
        else:
            pieces.append("%{:02X}".format(byte))
    return "".join(pieces)


def _hex_timer_id(value):
    if type(value) is not str:
        raise ValueError("timer id must be a string")
    try:
        data = value.encode("utf-8")
    except (UnicodeError, ValueError):
        raise ValueError("timer id must be UTF-8") from None
    if not data or len(data) > 64 or "|" in value or value != value.strip():
        raise ValueError("timer id is malformed")
    pieces = []
    for byte in data:
        pieces.append("{:02x}".format(byte))
    return "".join(pieces)


def _timer_resource_path(value):
    path = API_PREFIX + "/timers/~id/" + _hex_timer_id(value)
    if len(path) > MAX_HTTP_TARGET_BYTES:
        raise ValueError("timer id is too large for an item URL")
    return path


def _decode_hex_timer_id(value):
    if (
        type(value) is not str
        or not value
        or len(value) > 128
        or len(value) % 2
    ):
        raise _RestProblem(400, "invalid_timer_id", "Invalid timer id")
    data = bytearray()
    index = 0
    while index < len(value):
        if value[index] not in _HEX or value[index + 1] not in _HEX:
            raise _RestProblem(400, "invalid_timer_id", "Invalid timer id")
        data.append(int(value[index:index + 2], 16))
        index += 2
    try:
        decoded = bytes(data).decode("utf-8")
    except (UnicodeError, ValueError):
        raise _RestProblem(
            400, "invalid_timer_id", "Invalid timer id"
        ) from None
    try:
        if _hex_timer_id(decoded) != value.lower():
            raise ValueError("timer id is not canonical")
    except ValueError:
        raise _RestProblem(
            400, "invalid_timer_id", "Invalid timer id"
        ) from None
    return decoded


def _decode_timer_resource(value):
    if value.startswith("~id/"):
        return _decode_hex_timer_id(value[4:])
    return _percent_decode_segment(value)


def _query_integer(value, name, minimum, maximum):
    if not value or (len(value) > 1 and value[0] == "0") or len(value) > 6:
        raise _RestProblem(400, "invalid_query", "Invalid query string")
    for character in value:
        if character < "0" or character > "9":
            raise _RestProblem(400, "invalid_query", "Invalid query string")
    number = int(value)
    if number < minimum or number > maximum:
        raise _RestProblem(400, "invalid_query", "Invalid query string")
    return number


def _timer_page(query):
    if query is None or query == "":
        return 0, MAX_TIMER_PAGE_SIZE
    values = {}
    for item in query.split("&"):
        parts = item.split("=")
        if len(parts) != 2 or parts[0] not in ("offset", "limit"):
            raise _RestProblem(400, "invalid_query", "Invalid query string")
        if parts[0] in values:
            raise _RestProblem(400, "invalid_query", "Invalid query string")
        values[parts[0]] = parts[1]
    offset = _query_integer(values.get("offset", "0"), "offset", 0, 32)
    limit = _query_integer(
        values.get("limit", str(MAX_TIMER_PAGE_SIZE)),
        "limit",
        1,
        MAX_TIMER_PAGE_SIZE,
    )
    return offset, limit


class RestApplication:
    """Versioned request router over existing application models."""

    __slots__ = (
        "__configuration_gateway",
        "__manual_gateway",
        "__config_manager",
        "__configured_runtime",
        "__controller",
        "__temperature_manager",
        "__time_service",
        "__scheduler",
        "__scheduler_gateway",
        "__network_manager",
        "__security",
        "__rate_limiter",
        "__ticks_ms",
        "__ticks_diff",
        "__mem_free",
        "__operation_active",
        "__operation_reentered",
        "__faulted",
        "__last_error",
        "__next_request_id",
        "__requests",
        "__mutations",
        "__errors",
    )

    def __init__(
        self,
        configuration_gateway,
        manual_gateway,
        config_manager,
        configured_runtime,
        controller,
        temperature_manager,
        time_service,
        scheduler,
        scheduler_gateway,
        security_policy,
        network_manager=None,
        ticks_ms=None,
        ticks_diff=None,
        mem_free=None,
        rate_limiter=None,
    ):
        requirements = (
            (configuration_gateway, "settings_snapshot"),
            (configuration_gateway, "timers_snapshot"),
            (configuration_gateway, "snapshot"),
            (configuration_gateway, "patch_settings"),
            (configuration_gateway, "create_timer"),
            (configuration_gateway, "replace_timer"),
            (configuration_gateway, "delete_timer"),
            (manual_gateway, "request_start"),
            (manual_gateway, "request_quick_start"),
            (manual_gateway, "request_stop"),
            (manual_gateway, "snapshot"),
            (config_manager, "public_status"),
            (configured_runtime, "snapshot"),
            (configured_runtime, "restart_required"),
            (controller, "public_snapshot"),
            (temperature_manager, "snapshot"),
            (time_service, "snapshot"),
            (scheduler, "public_snapshot"),
            (scheduler, "next_occurrence"),
            (scheduler_gateway, "snapshot"),
            (security_policy, "validate_read"),
            (security_policy, "authorize_mutation"),
            (security_policy, "security_context"),
            (security_policy, "snapshot"),
        )
        for owner, method in requirements:
            if not callable(getattr(owner, method, None)):
                raise ValueError("REST dependency must provide {}()".format(method))
        if not hasattr(config_manager, "generation"):
            raise ValueError("config_manager must expose generation")
        if not hasattr(controller, "requested_on"):
            raise ValueError("controller must expose requested_on")
        if not hasattr(controller, "maximum_runtime_minutes"):
            raise ValueError(
                "controller must expose maximum_runtime_minutes"
            )
        if network_manager is not None and not callable(
            getattr(network_manager, "snapshot", None)
        ):
            raise ValueError("network_manager must provide snapshot()")
        if ticks_ms is None:
            ticks_ms = _platform_ticks_ms
        if ticks_diff is None:
            ticks_diff = _platform_ticks_diff
        if not callable(ticks_ms) or not callable(ticks_diff):
            raise ValueError("REST tick helpers must be callable")
        if mem_free is not None and not callable(mem_free):
            raise ValueError("mem_free must be callable")
        if rate_limiter is not None:
            for method in ("authorize", "complete", "snapshot"):
                if not callable(getattr(rate_limiter, method, None)):
                    raise ValueError(
                        "rate_limiter must provide {}()".format(method)
                    )

        self.__configuration_gateway = configuration_gateway
        self.__manual_gateway = manual_gateway
        self.__config_manager = config_manager
        self.__configured_runtime = configured_runtime
        self.__controller = controller
        self.__temperature_manager = temperature_manager
        self.__time_service = time_service
        self.__scheduler = scheduler
        self.__scheduler_gateway = scheduler_gateway
        self.__network_manager = network_manager
        self.__security = security_policy
        self.__rate_limiter = rate_limiter
        self.__ticks_ms = ticks_ms
        self.__ticks_diff = ticks_diff
        self.__mem_free = mem_free
        self.__operation_active = False
        self.__operation_reentered = False
        self.__faulted = False
        self.__last_error = None
        self.__next_request_id = 1
        self.__requests = 0
        self.__mutations = 0
        self.__errors = 0

    def _request_id(self):
        value = self.__next_request_id
        self.__next_request_id += 1
        if self.__next_request_id > MAX_REQUEST_ID:
            self.__next_request_id = 1
        return value

    @staticmethod
    def _success(status, request_id, data, headers=None):
        body = {"api_version": API_VERSION, "request_id": request_id}
        for key, value in data.items():
            body[key] = value
        return RestResponse(status, body, {} if headers is None else headers)

    @staticmethod
    def _problem_response(problem, request_id):
        details = {}
        for key, value in problem.details.items():
            details[key] = value
        error = {
            "code": problem.code,
            "message": problem.message,
            "request_id": request_id,
        }
        for key, value in details.items():
            error[key] = value
        return RestResponse(
            problem.status,
            {"api_version": API_VERSION, "error": error},
            dict(problem.headers),
        )

    def _current_generation(self):
        generation = self.__config_manager.generation
        if type(generation) is not int or generation < 0:
            raise ConfigurationAPIInvariantError(
                "configuration generation is malformed"
            )
        return generation

    def _required_generation(self, request):
        value = request.header("if-match")
        if value is None:
            raise _RestProblem(
                428,
                "configuration_precondition_required",
                "If-Match is required",
            )
        expected = _parse_config_etag(value)
        current = self._current_generation()
        if expected != current:
            raise _RestProblem(
                412,
                "configuration_precondition_failed",
                "Configuration changed",
                headers={"ETag": _config_etag(current)},
                details={"current_generation": current},
            )
        return expected

    def _assert_not_reentered(self):
        if self.__operation_reentered:
            raise _RestProblem(
                503,
                "rest_reentrancy_detected",
                "REST service is unavailable",
            )

    @staticmethod
    def _accepts_json(request):
        value = request.header("accept")
        if value is None:
            return True
        for item in value.lower().split(","):
            parts = item.strip().split(";")
            media = parts[0].strip()
            quality = 1
            quality_seen = False
            valid = True
            for parameter in parts[1:]:
                parameter = parameter.strip()
                if not parameter.startswith("q="):
                    continue
                if quality_seen:
                    valid = False
                    break
                quality_seen = True
                quality_value = parameter[2:]
                if quality_value in ("0", "0.0", "0.00", "0.000"):
                    quality = 0
                elif quality_value in ("1", "1.0", "1.00", "1.000"):
                    quality = 1
                elif (
                    quality_value.startswith("0.")
                    and 1 <= len(quality_value[2:]) <= 3
                    and all(
                        "0" <= character <= "9"
                        for character in quality_value[2:]
                    )
                ):
                    quality = 1
                else:
                    valid = False
                    break
            if (
                valid
                and quality > 0
                and media in ("*/*", "application/json")
            ):
                return True
        return False

    def _authorize_read(self, request):
        if not self._accepts_json(request):
            raise _RestProblem(406, "json_response_required", "JSON is required")
        self.__security.validate_read(request.headers)

    def _authorize_mutation(self, request, allow_faulted=False):
        if not self._accepts_json(request):
            raise _RestProblem(406, "json_response_required", "JSON is required")
        if self.__faulted and not allow_faulted:
            raise _RestProblem(
                503, "rest_service_faulted", "REST service is unavailable"
            )
        self.__security.authorize_mutation(request.headers)
        self._assert_not_reentered()

    @staticmethod
    def _json_object(request, fields):
        content_type = request.header("content-type")
        if type(content_type) is not str or content_type.lower() not in (
            _JSON_CONTENT_TYPES
        ):
            raise _RestProblem(
                415,
                "json_content_type_required",
                "Content-Type must be application/json",
            )
        try:
            value = decode_json_bytes(request.body)
        except (StrictJSONDecodeError, StrictJSONLimitError):
            raise _RestProblem(400, "invalid_json", "Invalid JSON body") from None
        if type(value) is not dict or frozenset(value) != frozenset(fields):
            raise _RestProblem(
                422, "invalid_request_shape", "Request fields are invalid"
            )
        return value

    @staticmethod
    def _empty_body(request):
        if request.body != b"" or request.header("content-type") is not None:
            raise _RestProblem(
                422, "request_body_not_allowed", "Request body is not allowed"
            )

    def _controller_public(self):
        value = self.__controller.public_snapshot()
        _exact_dict(
            "controller public snapshot",
            value,
            (
                "phase",
                "request_revision",
                "requested",
                "actual",
                "session",
                "control_transition_pending",
                "control_faulted",
                "restart_blocked",
                "sensor_stop_latched",
                "active_sensor",
                "counters",
            ),
        )
        requested = _exact_dict(
            "controller requested state",
            value["requested"],
            (
                "on",
                "mode",
                "target_temperature",
                "power_level",
                "runtime_minutes",
                "source",
            ),
        )
        actual = _exact_dict(
            "controller actual state",
            value["actual"],
            (
                "communication",
                "initialized",
                "synchronized",
                "heater_state",
                "heater_state_raw",
                "voltage",
                "glow_plug_raw",
                "fan_raw",
                "last_status_ms",
            ),
        )
        session = value["session"]
        if session is not None:
            session = _exact_dict(
                "controller session",
                session,
                (
                    "id",
                    "source",
                    "mode",
                    "target",
                    "started_at_ms",
                    "expires_at_ms",
                    "runtime_minutes",
                    "confirmed_active",
                    "expired",
                ),
            )
        active_sensor = value["active_sensor"]
        if active_sensor is not None:
            active_sensor = _exact_dict(
                "controller active sensor",
                active_sensor,
                ("role", "value_c", "age_ms", "health", "usable", "present"),
            )
        counters = _exact_dict(
            "controller counters",
            value["counters"],
            (
                "invalid_frames",
                "ignored_frames",
                "communication_failures",
                "control_failures",
                "events_dropped",
                "event_errors",
            ),
        )
        return {
            "phase": value["phase"],
            "request_revision": value["request_revision"],
            "requested": dict(requested),
            "actual": dict(actual),
            "session": None if session is None else dict(session),
            "control_transition_pending": value[
                "control_transition_pending"
            ],
            "control_faulted": value["control_faulted"],
            "restart_blocked": value["restart_blocked"],
            "sensor_stop_latched": value["sensor_stop_latched"],
            "active_sensor": (
                None if active_sensor is None else dict(active_sensor)
            ),
            "counters": dict(counters),
        }

    @staticmethod
    def _temperature_public(snapshot):
        result = {}
        sensors = snapshot.get("sensors")
        if type(sensors) is not dict:
            raise ValueError("temperature snapshot is malformed")
        for role in ("roof_tent", "cabin", "outside"):
            sensor = sensors.get(role)
            if type(sensor) is not dict:
                raise ValueError("temperature sensor snapshot is malformed")
            result[role] = {
                "value_c": sensor.get("value_c"),
                "age_ms": sensor.get("age_ms"),
                "health": sensor.get("health"),
                "usable": sensor.get("usable"),
                "present": sensor.get("present"),
            }
        return result

    @staticmethod
    def _clock_public(snapshot):
        if type(snapshot) is not dict:
            raise ValueError("clock snapshot is malformed")
        return {
            "valid": snapshot.get("valid"),
            "health": snapshot.get("health"),
            "rtc_health": snapshot.get("rtc_health"),
            "rtc_write_pending": snapshot.get("rtc_write_pending"),
            "rtc_commit_pending": snapshot.get("rtc_commit_revision") is not None,
            "source": snapshot.get("source"),
            "timezone": snapshot.get("timezone"),
            "timezone_rule": snapshot.get("timezone_rule"),
            "timezone_rule_version": snapshot.get("timezone_rule_version"),
            "utc_offset_minutes": snapshot.get("utc_offset_minutes"),
            "is_dst": snapshot.get("is_dst"),
            "sync_age_ms": snapshot.get("sync_age_ms"),
            "utc_seconds": snapshot.get("utc_seconds"),
            "local": snapshot.get("local"),
        }

    def _remaining_seconds(self, session, now_ms):
        if session is None:
            return None
        expires = session.get("expires_at_ms")
        if type(expires) is not int:
            return None
        remaining = self.__ticks_diff(expires, now_ms)
        if type(remaining) is not int:
            raise ValueError("ticks_diff returned a non-integer")
        if remaining <= 0:
            return 0
        return (remaining + 999) // 1000

    def _network_public(self):
        if self.__network_manager is None:
            return {"available": False}
        value = self.__network_manager.snapshot()
        if type(value) is not dict:
            raise ValueError("network snapshot is malformed")
        access_point = value.get("access_point")
        station = value.get("station")
        mdns = value.get("mdns")
        counters = value.get("counters")
        if not all(type(item) is dict for item in (
            access_point, station, mdns, counters
        )):
            raise ValueError("network snapshot is malformed")
        profiles = station.get("known_networks")
        if type(profiles) is not list or len(profiles) > 8:
            raise ValueError("network profiles are malformed")
        public_profiles = []
        for profile in profiles:
            _exact_dict(
                "network profile",
                profile,
                ("id", "ssid", "password_configured"),
            )
            public_profiles.append({
                "id": profile["id"],
                "ssid": profile["ssid"],
                "password_configured": profile["password_configured"],
            })
        return {
            "available": True,
            "state": {
                "running": value.get("running"),
                "closed": value.get("closed"),
                "faulted": value.get("faulted"),
                "state": value.get("state"),
                "access_point": {
                    "ssid": access_point.get("ssid"),
                    "active": access_point.get("active"),
                    "ip": access_point.get("ip"),
                    "clients": access_point.get("clients"),
                    "password_configured": access_point.get(
                        "password_configured"
                    ),
                },
                "station": {
                    "state": station.get("state"),
                    "connected": station.get("connected"),
                    "profile_id": station.get("profile_id"),
                    "ssid": station.get("ssid"),
                    "ip": station.get("ip"),
                    "gateway": station.get("gateway"),
                    "dns": station.get("dns"),
                    "rssi": station.get("rssi"),
                    "known_networks": public_profiles,
                },
                "mdns": {
                    "hostname": mdns.get("hostname"),
                    "ready": mdns.get("ready"),
                    "ap_only_guaranteed": mdns.get(
                        "ap_only_guaranteed"
                    ),
                },
                "internet_likely_available": value.get(
                    "internet_likely_available"
                ),
                "counters": {
                    "attempts": counters.get("attempts"),
                    "connections": counters.get("connections"),
                    "disconnects": counters.get("disconnects"),
                    "ap_repairs": counters.get("ap_repairs"),
                    "port_errors": counters.get("port_errors"),
                    "events_dropped": counters.get("events_dropped"),
                    "event_errors": counters.get("event_errors"),
                },
            },
        }

    def _status_data(self, now_ms):
        config = self.__config_manager.public_status()
        runtime = self.__configured_runtime.snapshot()
        restart = self.__configured_runtime.restart_required(
            self.__config_manager
        )
        if type(config) is not dict or type(runtime) is not dict or type(restart) is not bool:
            raise ValueError("runtime status is malformed")
        controller = self._controller_public()
        temperatures = self._temperature_public(
            self.__temperature_manager.snapshot(now_ms)
        )
        clock = self._clock_public(self.__time_service.snapshot(now_ms))
        scheduler = self.__scheduler.public_snapshot()
        _exact_dict(
            "scheduler public snapshot",
            scheduler,
            (
                "armed",
                "faulted",
                "configuration_revision",
                "timer_count",
                "active_occurrence_key",
                "active_occurrence",
                "consumed_local_high_water",
                "events_pending",
                "events_dropped",
                "event_errors",
            ),
        )
        next_occurrence = self.__scheduler.next_occurrence(now_ms)
        if next_occurrence is not None:
            next_occurrence = _exact_dict(
                "next occurrence",
                next_occurrence,
                (
                    "occurrence_key",
                    "timer_id",
                    "local_date",
                    "start",
                    "weekday",
                    "minutes_from_now",
                ),
            )
            next_occurrence = dict(next_occurrence)
        network = self._network_public()

        warnings = []
        if config.get("faulted") is True:
            warnings.append("configuration_fault")
        if restart:
            warnings.append("runtime_restart_required")
        if controller["control_faulted"]:
            warnings.append("heater_control_fault")
        if controller["sensor_stop_latched"]:
            warnings.append("sensor_stop_latched")
        if not clock["valid"]:
            warnings.append("clock_invalid")
        if scheduler.get("faulted") is True:
            warnings.append("scheduler_fault")
        for role in ("roof_tent", "cabin", "outside"):
            if temperatures[role]["health"] != "ok":
                warnings.append("sensor_{}_{}".format(
                    role, temperatures[role]["health"]
                ))
        if len(warnings) > MAX_WARNINGS:
            warnings = warnings[:MAX_WARNINGS]

        runtime_generation = runtime.get("configuration_generation")
        stored_setup_complete = config.get("setup_complete")
        runtime_setup_complete = runtime.get("setup_complete")
        return {
            "configuration": {
                "stored_generation": config.get("generation"),
                "runtime_generation": runtime_generation,
                "ledger_generation": config.get("ledger_generation"),
                "setup_complete": stored_setup_complete,
                "runtime_setup_complete": runtime_setup_complete,
                "restart_required": restart,
                "timer_start_allowed": config.get("timer_start_allowed"),
                "network_start_allowed": config.get("network_start_allowed"),
            },
            "heater": {
                "phase": controller["phase"],
                "request_revision": controller["request_revision"],
                "requested": controller["requested"],
                "actual": controller["actual"],
                "session": controller["session"],
                "remaining_seconds": self._remaining_seconds(
                    controller["session"], now_ms
                ),
                "control_transition_pending": controller[
                    "control_transition_pending"
                ],
                "control_faulted": controller["control_faulted"],
                "restart_blocked": controller["restart_blocked"],
                "sensor_stop_latched": controller["sensor_stop_latched"],
                "active_sensor": controller["active_sensor"],
            },
            "temperatures": temperatures,
            "time": clock,
            "scheduler": {
                "armed": scheduler.get("armed"),
                "faulted": scheduler.get("faulted"),
                "timer_count": scheduler.get("timer_count"),
                "active_occurrence_key": scheduler.get(
                    "active_occurrence_key"
                ),
                "next_occurrence": next_occurrence,
            },
            "network": network,
            "warnings": warnings,
        }

    def _diagnostics_data(self, now_ms):
        status = self._status_data(now_ms)
        heap = None
        if self.__mem_free is not None:
            heap = self.__mem_free()
            if type(heap) is not int or heap < 0:
                raise ValueError("mem_free returned an invalid value")
        configuration_api = _exact_dict(
            "configuration API diagnostics",
            self.__configuration_gateway.snapshot(),
            ("faulted", "last_error", "commits", "noops", "operation_active"),
        )
        manual_control = _exact_dict(
            "manual control diagnostics",
            self.__manual_gateway.snapshot(),
            (
                "faulted",
                "last_error",
                "request_revision",
                "starts",
                "stops",
                "operation_active",
            ),
        )
        timer_gateway = _exact_dict(
            "timer gateway diagnostics",
            self.__scheduler_gateway.snapshot(),
            (
                "faulted",
                "last_error",
                "pending_override_key",
                "applied",
                "rejected",
                "manual_stops",
                "persistence_enabled",
                "checkpoints",
                "checkpoint_failures",
                "operation_active",
            ),
        )
        security = _exact_dict(
            "security diagnostics",
            self.__security.snapshot(),
            (
                "started",
                "faulted",
                "last_error",
                "ingress",
                "allowed_host_count",
                "mutation_api_available",
                "operation_active",
            ),
        )
        return {
            "status": status,
            "configuration": self.__config_manager.public_status(),
            "configuration_api": dict(configuration_api),
            "manual_control": dict(manual_control),
            "timer_gateway": dict(timer_gateway),
            "security": dict(security),
            "heap_free_bytes": heap,
            "rest": self.snapshot(),
        }

    @staticmethod
    def _configuration_result(result):
        _exact_dict(
            "configuration API result",
            result,
            ("changed", "generation", "restart_required", "configuration"),
        )
        _require_boolean("configuration changed", result["changed"])
        _require_integer("configuration generation", result["generation"])
        _require_boolean(
            "configuration restart_required", result["restart_required"]
        )
        configuration = result["configuration"]
        if type(configuration) is not dict:
            raise ValueError("public configuration is malformed")
        if _contains_password_field(configuration):
            raise ConfigurationAPIInvariantError(
                "public configuration contains a password"
            )
        return result

    @staticmethod
    def _settings_snapshot(value):
        _exact_dict(
            "settings snapshot",
            value,
            (
                "generation",
                "schema_version",
                "system",
                "heater",
                "sensors",
                "time",
                "network",
                "restart_required",
            ),
        )
        if _contains_password_field(value):
            raise ConfigurationAPIInvariantError(
                "public settings contain a password"
            )
        return value

    @staticmethod
    def _settings_from_result(result):
        RestApplication._configuration_result(result)
        configuration = result["configuration"]
        return {
            "generation": result["generation"],
            "changed": result["changed"],
            "restart_required": result["restart_required"],
            "schema_version": configuration["schema_version"],
            "system": configuration["system"],
            "heater": configuration["heater"],
            "sensors": configuration["sensors"],
            "time": configuration["time"],
            "network": configuration["network"],
        }

    @staticmethod
    def _confirmed_timer(result, timer_id):
        RestApplication._configuration_result(result)
        timers = result["configuration"].get("timers")
        if type(timers) is not list or len(timers) > 32:
            raise ConfigurationAPIInvariantError(
                "public timer readback is malformed"
            )
        found = None
        for timer in timers:
            _exact_dict("public timer", timer, _TIMER_FIELDS)
            if timer["id"] == timer_id:
                if found is not None:
                    raise ConfigurationAPIInvariantError(
                        "public timer readback contains duplicate ids"
                    )
                found = timer
        if found is None:
            raise ConfigurationAPIInvariantError(
                "public timer readback omitted the committed timer"
            )
        _require_canonical_timer_dto(found)
        return found

    def _route(self, request, request_id, now_ms):
        method = request.method
        path = request.path
        if request.query is not None and path != API_PREFIX + "/timers":
            raise _RestProblem(400, "query_not_supported", "Query is not supported")

        if path == API_PREFIX + "/security-context":
            if method != "GET":
                raise _RestProblem(405, "method_not_allowed", "Method not allowed", {"Allow": "GET"})
            self._authorize_read(request)
            context = self.__security.security_context(request.headers)
            _exact_dict(
                "security context",
                context,
                ("csrf_token", "mutation_api_available"),
            )
            token = context["csrf_token"]
            if type(token) is not str or len(token) != 64:
                raise ValueError("security token is malformed")
            for character in token:
                if character not in "0123456789abcdef":
                    raise ValueError("security token is malformed")
            if context["mutation_api_available"] is not True:
                raise ValueError("security context is unavailable")
            return self._success(
                200,
                request_id,
                {
                    "csrf_token": token,
                    "mutation_api_available": True,
                },
            )

        if path == API_PREFIX + "/status":
            if method != "GET":
                raise _RestProblem(405, "method_not_allowed", "Method not allowed", {"Allow": "GET"})
            self._authorize_read(request)
            return self._success(200, request_id, self._status_data(now_ms))

        if path == API_PREFIX + "/diagnostics":
            if method != "GET":
                raise _RestProblem(405, "method_not_allowed", "Method not allowed", {"Allow": "GET"})
            self._authorize_read(request)
            return self._success(200, request_id, self._diagnostics_data(now_ms))

        if path == API_PREFIX + "/heater/start":
            if method != "POST":
                raise _RestProblem(405, "method_not_allowed", "Method not allowed", {"Allow": "POST"})
            self._authorize_mutation(request)
            generation = self._required_generation(request)
            body = self._json_object(request, _START_FIELDS)
            try:
                _require_integer(
                    "expected_request_revision",
                    body["expected_request_revision"],
                )
                maximum_runtime = _require_integer(
                    "maximum_runtime_minutes",
                    self.__controller.maximum_runtime_minutes,
                    1,
                )
                validate_start_request(
                    body["mode"],
                    body["target_temperature"],
                    body["power_level"],
                    body["runtime_minutes"],
                    "manual",
                    maximum_runtime,
                )
            except ValueError:
                raise _RestProblem(
                    422, "validation_failed", "Request validation failed"
                ) from None
            self._assert_not_reentered()
            changed = self.__manual_gateway.request_start(
                generation,
                body["expected_request_revision"],
                body["mode"],
                body["target_temperature"],
                body["power_level"],
                body["runtime_minutes"],
            )
            if type(changed) is not bool:
                raise ManualControlInvariantError(
                    "manual start result is malformed"
                )
            self.__mutations += 1
            controller = self._controller_public()
            return self._success(
                202 if changed else 200,
                request_id,
                {"changed": changed, "heater": controller},
                {"ETag": _config_etag(self._current_generation())},
            )

        if path == API_PREFIX + "/heater/quick-start":
            if method != "POST":
                raise _RestProblem(405, "method_not_allowed", "Method not allowed", {"Allow": "POST"})
            self._authorize_mutation(request)
            generation = self._required_generation(request)
            body = self._json_object(request, _QUICK_START_FIELDS)
            try:
                _require_integer(
                    "expected_request_revision",
                    body["expected_request_revision"],
                )
            except ValueError:
                raise _RestProblem(
                    422, "validation_failed", "Request validation failed"
                ) from None
            self._assert_not_reentered()
            changed = self.__manual_gateway.request_quick_start(
                generation, body["expected_request_revision"]
            )
            if type(changed) is not bool:
                raise ManualControlInvariantError(
                    "quick-start result is malformed"
                )
            self.__mutations += 1
            return self._success(
                202 if changed else 200,
                request_id,
                {"changed": changed, "heater": self._controller_public()},
                {"ETag": _config_etag(self._current_generation())},
            )

        if path == API_PREFIX + "/heater/stop":
            if method != "POST":
                raise _RestProblem(405, "method_not_allowed", "Method not allowed", {"Allow": "POST"})
            # STOP is the recovery path and therefore remains authorized even
            # when the REST mutation latch is faulted.  Host, Origin and CSRF
            # checks remain mandatory.
            self._authorize_mutation(request, allow_faulted=True)
            self._empty_body(request)
            self._assert_not_reentered()
            try:
                changed = self.__manual_gateway.request_stop()
            except MemoryError:
                raise
            except Exception:
                if self.__controller.requested_on is False:
                    raise _RestProblem(
                        503,
                        "stop_bookkeeping_failed",
                        "Stop was committed but bookkeeping failed",
                        details={"requested_off_committed": True},
                    ) from None
                raise
            if type(changed) is not bool:
                raise ManualControlInvariantError(
                    "manual stop result is malformed"
                )
            self.__mutations += 1
            return self._success(
                202 if changed else 200,
                request_id,
                {"changed": changed, "heater": self._controller_public()},
            )

        if path == API_PREFIX + "/settings":
            if method == "GET":
                self._authorize_read(request)
                value = self._settings_snapshot(
                    self.__configuration_gateway.settings_snapshot()
                )
                return self._success(
                    200,
                    request_id,
                    value,
                    {"ETag": _config_etag(value["generation"])},
                )
            if method == "PATCH":
                self._authorize_mutation(request)
                generation = self._required_generation(request)
                content_type = request.header("content-type")
                if type(content_type) is not str or content_type.lower() not in _JSON_CONTENT_TYPES:
                    raise _RestProblem(415, "json_content_type_required", "Content-Type must be application/json")
                try:
                    patch = decode_json_bytes(request.body)
                except (StrictJSONDecodeError, StrictJSONLimitError):
                    raise _RestProblem(400, "invalid_json", "Invalid JSON body") from None
                if (
                    type(patch) is not dict
                    or not patch
                    or not frozenset(patch).issubset(_SETTINGS_FIELDS)
                ):
                    raise _RestProblem(422, "invalid_request_shape", "Request fields are invalid")
                self._assert_not_reentered()
                result = self.__configuration_gateway.patch_settings(
                    patch, generation
                )
                self.__mutations += 1
                data = self._settings_from_result(result)
                return self._success(
                    200,
                    request_id,
                    data,
                    {"ETag": _config_etag(result["generation"])},
                )
            raise _RestProblem(405, "method_not_allowed", "Method not allowed", {"Allow": "GET, PATCH"})

        if path == API_PREFIX + "/timers":
            if method == "GET":
                self._authorize_read(request)
                offset, limit = _timer_page(request.query)
                value = self.__configuration_gateway.timers_snapshot()
                _exact_dict(
                    "timer snapshot",
                    value,
                    ("generation", "timers", "restart_required"),
                )
                _require_integer("timer generation", value["generation"])
                _require_boolean(
                    "timer restart_required", value["restart_required"]
                )
                timers = value["timers"]
                if type(timers) is not list or len(timers) > 32:
                    raise ConfigurationAPIInvariantError(
                        "public timers are malformed"
                    )
                for timer in timers:
                    _exact_dict("public timer", timer, _TIMER_FIELDS)
                data = {
                    "generation": value["generation"],
                    "restart_required": value["restart_required"],
                    "offset": offset,
                    "limit": limit,
                    "total": len(timers),
                    "items": timers[offset:offset + limit],
                }
                return self._success(
                    200,
                    request_id,
                    data,
                    {"ETag": _config_etag(value["generation"])},
                )
            if method == "POST":
                self._authorize_mutation(request)
                generation = self._required_generation(request)
                timer = self._json_object(request, _TIMER_FIELDS)
                try:
                    _require_canonical_timer_dto(timer)
                except ValueError:
                    raise _RestProblem(
                        422, "validation_failed", "Request validation failed"
                    ) from None
                self._assert_not_reentered()
                result = self.__configuration_gateway.create_timer(
                    timer, generation
                )
                self.__mutations += 1
                confirmed = self._confirmed_timer(result, timer["id"])
                location = _timer_resource_path(confirmed["id"])
                return self._success(
                    201,
                    request_id,
                    {
                        "generation": result["generation"],
                        "changed": result["changed"],
                        "restart_required": result["restart_required"],
                        "timer": confirmed,
                    },
                    {
                        "ETag": _config_etag(result["generation"]),
                        "Location": location,
                    },
                )
            raise _RestProblem(405, "method_not_allowed", "Method not allowed", {"Allow": "GET, POST"})

        timer_prefix = API_PREFIX + "/timers/"
        if path.startswith(timer_prefix) and len(path) > len(timer_prefix):
            if request.query is not None:
                raise _RestProblem(400, "query_not_supported", "Query is not supported")
            timer_id = _decode_timer_resource(path[len(timer_prefix):])
            if method == "GET":
                self._authorize_read(request)
                value = self.__configuration_gateway.timers_snapshot()
                _exact_dict(
                    "timer snapshot",
                    value,
                    ("generation", "timers", "restart_required"),
                )
                found = None
                for timer in value["timers"]:
                    if timer["id"] == timer_id:
                        found = timer
                        break
                if found is None:
                    raise ConfigurationAPINotFoundError("timer does not exist")
                return self._success(
                    200,
                    request_id,
                    {"generation": value["generation"], "timer": found},
                    {"ETag": _config_etag(value["generation"])},
                )
            if method == "PUT":
                self._authorize_mutation(request)
                generation = self._required_generation(request)
                timer = self._json_object(request, _TIMER_FIELDS)
                try:
                    _require_canonical_timer_dto(timer)
                except ValueError:
                    raise _RestProblem(
                        422, "validation_failed", "Request validation failed"
                    ) from None
                if timer["id"] != timer_id:
                    raise _RestProblem(
                        422, "validation_failed", "Request validation failed"
                    )
                self._assert_not_reentered()
                result = self.__configuration_gateway.replace_timer(
                    timer_id, timer, generation
                )
                self.__mutations += 1
                confirmed = self._confirmed_timer(result, timer_id)
                return self._success(
                    200,
                    request_id,
                    {
                        "generation": result["generation"],
                        "changed": result["changed"],
                        "restart_required": result["restart_required"],
                        "timer": confirmed,
                    },
                    {"ETag": _config_etag(result["generation"])},
                )
            if method == "DELETE":
                self._authorize_mutation(request)
                self._empty_body(request)
                generation = self._required_generation(request)
                self._assert_not_reentered()
                result = self.__configuration_gateway.delete_timer(
                    timer_id, generation
                )
                self._configuration_result(result)
                if result["changed"] is not True:
                    raise ConfigurationAPIInvariantError(
                        "deleted timer was not durably changed"
                    )
                self.__mutations += 1
                return self._success(
                    200,
                    request_id,
                    {
                        "generation": result["generation"],
                        "changed": True,
                        "deleted": True,
                        "restart_required": result["restart_required"],
                    },
                    {"ETag": _config_etag(result["generation"])},
                )
            raise _RestProblem(405, "method_not_allowed", "Method not allowed", {"Allow": "GET, PUT, DELETE"})

        if not request.method_supported:
            raise _RestProblem(
                405,
                "method_not_allowed",
                "Method not allowed",
                {"Allow": "GET, POST, PUT, PATCH, DELETE"},
            )
        raise _RestProblem(404, "not_found", "Resource not found")

    def _dispatch(self, request, request_id, now_ms):
        try:
            return self._route(request, request_id, now_ms)
        except _RestProblem:
            raise
        except RestSecurityDenied:
            raise _RestProblem(403, "request_forbidden", "Request is forbidden") from None
        except RestSecurityUnavailable:
            raise _RestProblem(503, "mutation_security_unavailable", "Mutation API is unavailable") from None
        except ConfigurationAPIResourceConflictError:
            raise _RestProblem(409, "resource_conflict", "Resource already exists") from None
        except ConfigurationAPINotFoundError:
            raise _RestProblem(404, "resource_not_found", "Resource not found") from None
        except (ConfigurationAPIConflictError, ConfigurationConflictError):
            current = self._current_generation()
            raise _RestProblem(
                412,
                "configuration_precondition_failed",
                "Configuration changed",
                {"ETag": _config_etag(current)},
                {"current_generation": current},
            ) from None
        except ManualControlConfigurationConflictError:
            current = self._current_generation()
            raise _RestProblem(
                412,
                "configuration_precondition_failed",
                "Configuration changed",
                {"ETag": _config_etag(current)},
                {"current_generation": current},
            ) from None
        except ManualControlStateConflictError:
            raise _RestProblem(409, "heater_start_conflict", "Heater cannot start in its current state") from None
        except ManualControlConflictError:
            raise _RestProblem(409, "control_precondition_failed", "Requested State changed") from None
        except (ManualControlUnavailableError, ConfigurationStateError):
            raise _RestProblem(503, "application_unavailable", "Application service is unavailable") from None
        except ConfigurationAPIValidationError:
            raise _RestProblem(422, "validation_failed", "Request validation failed") from None
        except (ConfigurationAPIInvariantError, ManualControlInvariantError):
            raise _RestProblem(503, "application_invariant_failed", "Application service is unavailable") from None
        except MemoryError:
            raise
        except OSError:
            raise _RestProblem(503, "application_io_failed", "Application service is unavailable") from None
        except RuntimeError:
            raise _RestProblem(503, "application_operation_failed", "Application service is unavailable") from None
        except (ConfigurationValidationError, ValueError):
            raise _RestProblem(
                503,
                "application_contract_failed",
                "Application service is unavailable",
            ) from None
        except Exception:
            raise _RestProblem(500, "internal_error", "Internal server error") from None

    def handle(self, request, peer_ip=None):
        request_id = self._request_id()
        if self.__operation_active:
            self.__operation_reentered = True
            problem = _RestProblem(
                503, "rest_reentrancy_rejected", "REST service is busy"
            )
            return self._problem_response(problem, request_id)
        self.__operation_active = True
        self.__operation_reentered = False
        self.__requests += 1
        response = None
        response_ready = False
        out_of_memory = False
        started_requested_off = False
        rate_ticket = None
        try:
            try:
                requested_on = self.__controller.requested_on
            except MemoryError:
                raise
            except Exception:
                raise ValueError(
                    "controller requested truth is unavailable"
                ) from None
            if type(requested_on) is not bool:
                raise ValueError("controller requested truth is malformed")
            started_requested_off = requested_on is False
            now_ms = self.__ticks_ms()
            _require_integer("ticks_ms", now_ms)
            if self.__rate_limiter is not None:
                rate_ticket = self.__rate_limiter.authorize(
                    peer_ip,
                    request.method,
                    request.path,
                    now_ms,
                )
            self._assert_not_reentered()
            response = self._dispatch(request, request_id, now_ms)
            if self.__rate_limiter is not None:
                config_committed = (
                    type(response) is RestResponse
                    and 200 <= response.status < 300
                    and type(response.body) is dict
                    and response.body.get("changed") is True
                )
                completed_at_ms = None
                if config_committed:
                    completed_at_ms = self.__ticks_ms()
                    _require_integer("ticks_ms", completed_at_ms)
                self.__rate_limiter.complete(
                    rate_ticket,
                    config_committed,
                    completed_at_ms,
                )
            response_ready = True
        except MemoryError:
            out_of_memory = True
        except RestRateLimitExceeded as error:
            self.__errors += 1
            response = self._problem_response(
                _RestProblem(
                    429,
                    "rate_limit_exceeded",
                    "Too many requests",
                    {"Retry-After": str(error.retry_after_seconds)},
                ),
                request_id,
            )
        except RestRateLimitUnavailable:
            self.__errors += 1
            response = self._problem_response(
                _RestProblem(
                    503,
                    "rate_limit_unavailable",
                    "REST service is unavailable",
                ),
                request_id,
            )
        except _RestProblem as problem:
            self.__errors += 1
            response = self._problem_response(problem, request_id)
        except (OSError, RuntimeError, ValueError):
            self.__errors += 1
            response = self._problem_response(
                _RestProblem(
                    503,
                    "application_contract_failed",
                    "Application service is unavailable",
                ),
                request_id,
            )
        except Exception:
            self.__errors += 1
            response = self._problem_response(
                _RestProblem(500, "internal_error", "Internal server error"),
                request_id,
            )
        except BaseException:
            raise
        finally:
            reentered = self.__operation_reentered
            rollback_failed = False
            rollback_needed = False
            successful_response = (
                response_ready
                and type(response) is RestResponse
                and 200 <= response.status < 300
            )
            if started_requested_off and (reentered or not successful_response):
                try:
                    rollback_needed = self.__controller.requested_on is not False
                except BaseException:
                    rollback_needed = True
                    rollback_failed = True
            if rollback_needed:
                try:
                    stopped = self.__manual_gateway.request_stop()
                    if type(stopped) is not bool:
                        rollback_failed = True
                except MemoryError:
                    out_of_memory = True
                    rollback_failed = True
                except BaseException:
                    rollback_failed = True
                try:
                    if self.__controller.requested_on is not False:
                        rollback_failed = True
                except BaseException:
                    rollback_failed = True
            self.__operation_active = False
            self.__operation_reentered = False
            if reentered or rollback_needed:
                self.__faulted = True
                if rollback_failed:
                    self.__last_error = "rest_requested_off_rollback_failed"
                elif reentered:
                    self.__last_error = "rest_application_reentrancy_detected"
                else:
                    self.__last_error = "rest_failed_response_rolled_off"
        if out_of_memory:
            raise MemoryError() from None
        if reentered:
            self.__errors += 1
            return self._problem_response(
                _RestProblem(
                    503,
                    "rest_reentrancy_detected",
                    "REST service is unavailable",
                ),
                request_id,
            )
        return response

    def snapshot(self):
        value = {
            "faulted": self.__faulted,
            "last_error": self.__last_error,
            "operation_active": self.__operation_active,
            "requests": self.__requests,
            "mutations": self.__mutations,
            "errors": self.__errors,
        }
        if self.__rate_limiter is not None:
            value["rate_limit"] = self.__rate_limiter.snapshot()
        return value
