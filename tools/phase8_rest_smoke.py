"""USB-only Phase-8 REST integration smoke.

This module is deliberately inert at import time.  The explicit runner uses
only synthetic application models and in-memory fake sockets; it never imports
board configuration, ``machine``, ``network``, hardware factories or the
heater protocol, and it never opens a real socket.  A successful run proves
the bounded JSON/HTTP boundary, AP peer forwarding, CSRF-protected STOP,
fail-closed manual START, cooperative socket budgets and complete teardown.
"""

import gc as _gc


SOFTWARE_ONLY_CONFIRMATION = "PHASE8_USB_REST_SMOKE_CONFIRM_V1"
PHASE8_PASS_TOKEN = "PHASE8_USB_REST_SMOKE_PASS_V1"
DEFAULT_ITERATIONS = 4
MIN_ITERATIONS = 1
MAX_ITERATIONS = 8
MINIMUM_FREE_HEAP_BYTES = 32 * 1024
MAXIMUM_HEAP_DRIFT_BYTES = 4096
MAX_STEPS_PER_REQUEST = 96

AP_IP = "192.168.4.1"
PEER_IP = "192.168.4.2"
ORIGIN = "http://192.168.4.1"


def _require(condition, message):
    if not condition:
        raise RuntimeError("phase8 REST smoke failed: {}".format(message))


def _plain_ticks_diff(newer, older):
    return newer - older


def _plain_ticks_add(value, delta):
    return value + delta


def _memory_free():
    _gc.collect()
    reader = getattr(_gc, "mem_free", None)
    if not callable(reader):
        return None
    value = reader()
    if type(value) is not int or value < 0:
        raise RuntimeError("gc.mem_free() returned an invalid value")
    return value


def _check_platform_ticks():
    try:
        from time import ticks_add, ticks_diff, ticks_ms
    except ImportError:
        return False
    now_ms = ticks_ms()
    future_ms = ticks_add(now_ms, 37)
    _require(
        type(now_ms) is int
        and type(future_ms) is int
        and ticks_diff(future_ms, now_ms) == 37,
        "MicroPython tick primitives are inconsistent",
    )
    return True


def _check_memory(before, after_import, after_warmup, after):
    values = (before, after_import, after_warmup, after)
    available = tuple(value is not None for value in values)
    if not any(available):
        return False
    _require(all(available), "heap measurements are incomplete")
    for value in values[1:]:
        _require(
            value >= MINIMUM_FREE_HEAP_BYTES,
            "free heap after Phase-8 work is below 32 KiB",
        )
    allowed_drift = max(MAXIMUM_HEAP_DRIFT_BYTES, after_warmup // 50)
    _require(
        after >= after_warmup - allowed_drift,
        "free heap did not recover after bounded REST iterations",
    )
    return True


class _Core:
    __slots__ = (
        "build_rest_runtime",
        "build_rest_http_server",
        "RestApplication",
        "ManualControlGateway",
        "RestSecurityPolicy",
        "RestRateLimiter",
        "MicroPythonHTTPServer",
        "decode_json_bytes",
        "encode_json_bytes",
        "StrictJSONLimitError",
        "parse_request",
        "HttpParseError",
        "MAX_BODY_BYTES",
        "RECV_BUDGET_BYTES",
        "SEND_BUDGET_BYTES",
        "INGRESS_ACCESS_POINT",
    )


def _load_core():
    # Every import stays behind the explicit confirmation boundary.  These
    # modules are application-only and do not import or construct hardware.
    from adapters.micropython_http_server import (
        RECV_BUDGET_BYTES,
        SEND_BUDGET_BYTES,
        MicroPythonHTTPServer,
    )
    from app.manual_control_gateway import ManualControlGateway
    from app.rest_application import RestApplication
    from app.rest_composition import (
        build_rest_http_server,
        build_rest_runtime,
    )
    from services.http_protocol import (
        MAX_BODY_BYTES,
        HttpParseError,
        parse_request,
    )
    from services.rest_rate_limiter import RestRateLimiter
    from services.rest_security import (
        INGRESS_ACCESS_POINT,
        RestSecurityPolicy,
    )
    from services.strict_json import (
        StrictJSONLimitError,
        decode_json_bytes,
        encode_json_bytes,
    )

    core = _Core()
    core.build_rest_runtime = build_rest_runtime
    core.build_rest_http_server = build_rest_http_server
    core.RestApplication = RestApplication
    core.ManualControlGateway = ManualControlGateway
    core.RestSecurityPolicy = RestSecurityPolicy
    core.RestRateLimiter = RestRateLimiter
    core.MicroPythonHTTPServer = MicroPythonHTTPServer
    core.decode_json_bytes = decode_json_bytes
    core.encode_json_bytes = encode_json_bytes
    core.StrictJSONLimitError = StrictJSONLimitError
    core.parse_request = parse_request
    core.HttpParseError = HttpParseError
    core.MAX_BODY_BYTES = MAX_BODY_BYTES
    core.RECV_BUDGET_BYTES = RECV_BUDGET_BYTES
    core.SEND_BUDGET_BYTES = SEND_BUDGET_BYTES
    core.INGRESS_ACCESS_POINT = INGRESS_ACCESS_POINT
    return core


class _Clock:
    __slots__ = ("now",)

    def __init__(self):
        self.now = 0

    def ticks_ms(self):
        return self.now

    def ticks_diff(self, newer, older):
        return newer - older

    def ticks_add(self, value, delta):
        return value + delta

    def advance(self, delta):
        _require(type(delta) is int and delta >= 0, "clock advance is invalid")
        self.now += delta


class _ConfigManager:
    __slots__ = ("generation", "ledger_generation", "timer_start_allowed")

    def __init__(self):
        self.generation = 2
        self.ledger_generation = 2
        self.timer_start_allowed = True

    def snapshot(self):
        raise RuntimeError("configuration writes are outside this smoke")

    def public_snapshot(self):
        raise RuntimeError("configuration reads are outside this smoke")

    def public_status(self):
        raise RuntimeError("configuration status is outside this smoke")

    def commit(self, candidate, expected_generation):
        raise RuntimeError("configuration writes are outside this smoke")


class _Scheduler:
    __slots__ = ("armed", "active_occurrence_key")

    def __init__(self):
        self.armed = False
        self.active_occurrence_key = None

    def disarm(self):
        changed = self.armed
        self.armed = False
        return changed

    def public_snapshot(self):
        return {
            "armed": False,
            "faulted": False,
            "configuration_revision": 2,
            "timer_count": 0,
            "active_occurrence_key": None,
            "active_occurrence": None,
            "consumed_local_high_water": None,
            "events_pending": 0,
            "events_dropped": 0,
            "event_errors": 0,
        }

    def next_occurrence(self, now_ms):
        return None


class _TemperatureManager:
    __slots__ = ()

    def snapshot(self, now_ms=None):
        return {"sensors": {}}


class _TimeService:
    __slots__ = ()

    def snapshot(self, now_ms=None):
        return {"valid": False}


class _ConfiguredRuntime:
    __slots__ = ("scheduler", "temperature_manager", "time_service")

    def __init__(self):
        self.scheduler = _Scheduler()
        self.temperature_manager = _TemperatureManager()
        self.time_service = _TimeService()

    def snapshot(self):
        return {
            "configuration_generation": 2,
            "ledger_generation": 2,
            "setup_complete": True,
            "persistent_start_gate_open": True,
            "quick_start": {
                "mode": "power",
                "target_temperature": None,
                "power_level": 2,
                "runtime_minutes": 15,
            },
            "clock_valid": False,
            "scheduler_armed": False,
        }

    def restart_required(self, config_manager):
        return config_manager.generation != 2


class _Controller:
    __slots__ = (
        "requested_on",
        "request_revision",
        "maximum_runtime_minutes",
    )

    def __init__(self):
        self.requested_on = False
        self.request_revision = 0
        self.maximum_runtime_minutes = 120

    def arm_synthetic_request(self):
        _require(self.requested_on is False, "synthetic request was already ON")
        self.requested_on = True
        self.request_revision += 1

    def request_synthetic_stop(self):
        changed = self.requested_on
        if changed:
            self.requested_on = False
            self.request_revision += 1
        return changed

    def manual_start_available(self, *arguments):
        # The smoke intentionally models stale/unavailable Actual State.  The
        # real ManualControlGateway must reject START without making it latent.
        return False

    def request_start(self, *arguments, **keywords):
        raise RuntimeError("denied synthetic start reached Requested State")

    def requested_matches(self, *arguments):
        return False

    def update_active_session(self, *arguments, **keywords):
        raise RuntimeError("synthetic smoke has no active session")

    def public_snapshot(self):
        return {
            "phase": "synchronized",
            "request_revision": self.request_revision,
            "requested": {
                "on": self.requested_on,
                "mode": "power",
                "target_temperature": None,
                "power_level": 2,
                "runtime_minutes": 15,
                "source": "manual",
            },
            "actual": {
                "communication": "ok",
                "initialized": True,
                "synchronized": True,
                "heater_state": "off",
                "heater_state_raw": 0,
                "voltage": 12,
                "glow_plug_raw": 0,
                "fan_raw": 0,
                "last_status_ms": 0,
            },
            "session": None,
            "control_transition_pending": False,
            "control_faulted": False,
            "restart_blocked": False,
            "sensor_stop_latched": False,
            "active_sensor": None,
            "counters": {
                "invalid_frames": 0,
                "ignored_frames": 0,
                "communication_failures": 0,
                "control_failures": 0,
                "events_dropped": 0,
                "event_errors": 0,
            },
        }


class _SchedulerGateway:
    __slots__ = ("controller", "manual_stops")

    def __init__(self, controller):
        self.controller = controller
        self.manual_stops = 0

    def request_manual_stop(self):
        changed = self.controller.request_synthetic_stop()
        if changed:
            self.manual_stops += 1
        return changed

    def snapshot(self):
        return {
            "faulted": False,
            "last_error": None,
            "pending_override_key": None,
            "applied": 0,
            "rejected": 0,
            "manual_stops": self.manual_stops,
            "checkpoints": 0,
            "checkpoint_failures": 0,
        }


class _FakeClientSocket:
    __slots__ = (
        "incoming",
        "read_offset",
        "written",
        "closed",
        "recv_sizes",
        "send_sizes",
    )

    def __init__(self, incoming):
        self.incoming = bytes(incoming)
        self.read_offset = 0
        self.written = bytearray()
        self.closed = False
        self.recv_sizes = []
        self.send_sizes = []

    def setblocking(self, value):
        _require(value is False, "client socket was not made nonblocking")
        return None

    def recv(self, size):
        self.recv_sizes.append(size)
        if self.read_offset >= len(self.incoming):
            return b""
        end = min(len(self.incoming), self.read_offset + size)
        chunk = self.incoming[self.read_offset:end]
        self.read_offset = end
        return chunk

    def send(self, payload):
        offered = len(payload)
        self.send_sizes.append(offered)
        _require(offered > 0, "server offered an empty response chunk")
        count = min(offered, 97)
        self.written.extend(bytes(payload[:count]))
        return count

    def close(self):
        self.closed = True
        return None


class _FakeListener:
    __slots__ = (
        "pending",
        "bound",
        "backlog",
        "closed",
        "accept_count",
    )

    def __init__(self):
        self.pending = []
        self.bound = None
        self.backlog = None
        self.closed = False
        self.accept_count = 0

    def enqueue(self, client):
        _require(not self.closed, "client queued after listener cleanup")
        self.pending.append(client)

    def setblocking(self, value):
        _require(value is False, "listener was not made nonblocking")
        return None

    def bind(self, address):
        _require(address == (AP_IP, 80), "listener did not bind exact AP IPv4")
        self.bound = address
        return None

    def listen(self, backlog):
        self.backlog = backlog
        return None

    def accept(self):
        self.accept_count += 1
        if not self.pending:
            raise OSError(11)
        client = self.pending.pop(0)
        return client, (PEER_IP, 49152 + self.accept_count)

    def close(self):
        self.closed = True
        return None


class _SocketFactory:
    __slots__ = ("listener", "calls")

    def __init__(self, listener):
        self.listener = listener
        self.calls = 0

    def __call__(self):
        self.calls += 1
        _require(self.calls == 1, "listener factory was called more than once")
        return self.listener


class _Fixture:
    __slots__ = (
        "clock",
        "config_manager",
        "controller",
        "scheduler_gateway",
        "configured_runtime",
        "runtime",
        "listener",
        "factory",
        "server",
    )


def _random_bytes(count):
    _require(count == 32, "synthetic CSRF provider received an invalid length")
    value = bytearray(count)
    for index in range(count):
        value[index] = index
    return bytes(value)


def _build_fixture(core):
    fixture = _Fixture()
    fixture.clock = _Clock()
    fixture.config_manager = _ConfigManager()
    fixture.controller = _Controller()
    fixture.scheduler_gateway = _SchedulerGateway(fixture.controller)
    fixture.configured_runtime = _ConfiguredRuntime()
    fixture.runtime = core.build_rest_runtime(
        fixture.config_manager,
        fixture.configured_runtime,
        fixture.controller,
        fixture.scheduler_gateway,
        _random_bytes,
        (AP_IP,),
        core.INGRESS_ACCESS_POINT,
        ticks_ms=fixture.clock.ticks_ms,
        ticks_diff=fixture.clock.ticks_diff,
        ticks_add=fixture.clock.ticks_add,
    )
    _require(
        type(fixture.runtime.application) is core.RestApplication,
        "REST composition did not build RestApplication",
    )
    _require(
        type(fixture.runtime.manual_gateway) is core.ManualControlGateway,
        "REST composition did not build ManualControlGateway",
    )
    _require(
        type(fixture.runtime.security_policy) is core.RestSecurityPolicy,
        "REST composition did not build RestSecurityPolicy",
    )
    _require(
        type(fixture.runtime.rate_limiter) is core.RestRateLimiter,
        "REST composition did not build RestRateLimiter",
    )
    fixture.listener = _FakeListener()
    fixture.factory = _SocketFactory(fixture.listener)
    fixture.server = core.build_rest_http_server(
        fixture.runtime,
        AP_IP,
        socket_factory=fixture.factory,
        ticks_ms=fixture.clock.ticks_ms,
        ticks_diff=fixture.clock.ticks_diff,
        ticks_add=fixture.clock.ticks_add,
    )
    _require(
        type(fixture.server) is core.MicroPythonHTTPServer,
        "REST composition did not build MicroPythonHTTPServer",
    )
    _require(fixture.factory.calls == 0, "server construction performed I/O")
    return fixture


def _request(method, path, headers=None, body=b""):
    values = {} if headers is None else dict(headers)
    values["Host"] = AP_IP
    if method in ("POST", "PUT", "PATCH"):
        values["Content-Length"] = str(len(body))
    lines = ["{} {} HTTP/1.1".format(method, path)]
    for name in sorted(values):
        lines.append("{}: {}".format(name, values[name]))
    return "\r\n".join(lines).encode("ascii") + b"\r\n\r\n" + body


def _decode_response(core, wire):
    payload = bytes(wire)
    separator = payload.find(b"\r\n\r\n")
    _require(separator > 0, "HTTP response framing is incomplete")
    head = payload[:separator].decode("ascii")
    body = payload[separator + 4:]
    lines = head.split("\r\n")
    parts = lines[0].split(" ")
    _require(
        len(parts) >= 3 and parts[0] == "HTTP/1.1",
        "HTTP status line is malformed",
    )
    status = int(parts[1])
    headers = {}
    for line in lines[1:]:
        split = line.find(":")
        _require(split > 0, "HTTP response header is malformed")
        headers[line[:split].lower()] = line[split + 1:].strip()
    _require(
        headers.get("connection") == "close",
        "HTTP response did not close the connection",
    )
    _require(
        headers.get("content-length") == str(len(body)),
        "HTTP response length differs",
    )
    return status, core.decode_json_bytes(body)


def _pump_request(core, fixture, request):
    client = _FakeClientSocket(request)
    fixture.listener.enqueue(client)
    maximum_actions = 0
    for unused in range(MAX_STEPS_PER_REQUEST):
        before = fixture.server.snapshot()
        fixture.server.step()
        after = fixture.server.snapshot()
        actions = (
            after["accept_actions"] - before["accept_actions"]
            + after["recv_actions"] - before["recv_actions"]
            + after["send_actions"] - before["send_actions"]
        )
        _require(
            0 <= actions <= 1,
            "one server step exceeded its socket-action budget",
        )
        maximum_actions = max(maximum_actions, actions)
        if client.closed:
            break
    _require(client.closed, "bounded server steps did not finish a request")
    _require(client.recv_sizes, "request was not received")
    _require(client.send_sizes, "response was not sent")
    _require(
        max(client.recv_sizes) <= core.RECV_BUDGET_BYTES,
        "receive budget was exceeded",
    )
    _require(
        max(client.send_sizes) <= core.SEND_BUDGET_BYTES,
        "send budget was exceeded",
    )
    status, body = _decode_response(core, client.written)
    return status, body, maximum_actions


def _exercise_protocol_boundaries(core):
    edge = b'{"edge":"' + (b"x" * 256) + b'"}'
    decoded = core.decode_json_bytes(edge)
    _require(decoded == {"edge": "x" * 256}, "256-character JSON edge failed")
    encoded = core.encode_json_bytes(decoded)
    _require(
        core.decode_json_bytes(encoded) == decoded,
        "strict JSON roundtrip differs",
    )
    rejected = False
    try:
        core.decode_json_bytes(b'{"edge":"' + (b"x" * 257) + b'"}')
    except core.StrictJSONLimitError:
        rejected = True
    _require(rejected, "257-character JSON string was accepted")

    body = b"x" * core.MAX_BODY_BYTES
    raw = _request(
        "POST",
        "/api/v1/heater/stop",
        {"Content-Type": "application/octet-stream"},
        body,
    )
    parsed = core.parse_request(raw)
    _require(len(parsed.body) == core.MAX_BODY_BYTES, "HTTP body edge failed")
    rejected = False
    try:
        core.parse_request(
            _request(
                "POST",
                "/api/v1/heater/stop",
                {"Content-Type": "application/octet-stream"},
                body + b"x",
            )
        )
    except core.HttpParseError as error:
        rejected = error.status == 413
    _require(rejected, "HTTP body above its hard bound was accepted")


def _exercise_iteration(core, fixture):
    # Moving by more than both limiter windows keeps every iteration
    # independent while still using one stable boot-ephemeral CSRF token.
    fixture.clock.advance(11000)
    fixture.controller.arm_synthetic_request()

    status, context, actions = _pump_request(
        core,
        fixture,
        _request("GET", "/api/v1/security-context"),
    )
    _require(status == 200, "security-context did not return 200")
    _require(
        context.get("mutation_api_available") is True,
        "AP mutation context is unavailable",
    )
    csrf_token = context.get("csrf_token")
    _require(
        type(csrf_token) is str and len(csrf_token) == 64,
        "security-context token is malformed",
    )
    _require(
        fixture.runtime.rate_limiter.snapshot()["peer_count"] == 1,
        "accepted AP peer was not forwarded to the REST limiter",
    )

    mutation_headers = {
        "Origin": ORIGIN,
        "X-Landy-CSRF": csrf_token,
    }
    status, stopped, stop_actions = _pump_request(
        core,
        fixture,
        _request(
            "POST",
            "/api/v1/heater/stop",
            mutation_headers,
        ),
    )
    _require(status == 202, "valid CSRF STOP was not accepted")
    _require(stopped.get("changed") is True, "STOP was not committed")
    _require(
        fixture.controller.requested_on is False,
        "Requested State remained ON after STOP",
    )

    start_body = core.encode_json_bytes({
        "expected_request_revision": fixture.controller.request_revision,
        "mode": "power",
        "target_temperature": None,
        "power_level": 2,
        "runtime_minutes": 15,
    })
    start_headers = dict(mutation_headers)
    start_headers["Content-Type"] = "application/json"
    start_headers["If-Match"] = '"config-2"'
    status, denied, start_actions = _pump_request(
        core,
        fixture,
        _request(
            "POST",
            "/api/v1/heater/start",
            start_headers,
            start_body,
        ),
    )
    _require(status == 409, "unavailable START did not fail closed")
    _require(
        denied.get("error", {}).get("code") == "heater_start_conflict",
        "unavailable START returned the wrong fixed problem",
    )
    _require(
        fixture.controller.requested_on is False,
        "denied START changed Requested State",
    )
    return max(actions, stop_actions, start_actions)


def _cleanup_fixture(fixture):
    first_error = None
    try:
        fixture.server.deinit()
    except BaseException as error:
        first_error = error
    try:
        fixture.runtime.deinit()
    except BaseException as error:
        if first_error is None:
            first_error = error
    if first_error is not None:
        raise first_error

    server = fixture.server.snapshot()
    security = fixture.runtime.security_policy.snapshot()
    _require(server["closed"] is True, "server did not enter terminal cleanup")
    _require(server["started"] is False, "listener remained started")
    _require(server["client_count"] == 0, "client remained after cleanup")
    _require(fixture.listener.closed is True, "fake listener remained open")
    _require(
        security["started"] is False
        and security["mutation_api_available"] is False,
        "CSRF authority remained available after cleanup",
    )
    return True


def run(confirmation, iterations=DEFAULT_ITERATIONS):
    """Run the confirmed, bounded and hardware-free Phase-8 smoke."""

    if type(confirmation) is not str or confirmation != SOFTWARE_ONLY_CONFIRMATION:
        raise RuntimeError("exact Phase-8 USB REST confirmation is required")
    if (
        type(iterations) is not int
        or iterations < MIN_ITERATIONS
        or iterations > MAX_ITERATIONS
    ):
        raise ValueError("iterations must be an integer from 1 to 8")

    before_import = _memory_free()
    core = _load_core()
    after_import = _memory_free()
    platform_ticks_checked = _check_platform_ticks()

    fixture = None
    result = None
    primary_error = None
    after_warmup = None
    try:
        fixture = _build_fixture(core)
        _require(fixture.runtime.start() is True, "REST security did not start")
        _require(fixture.server.start() is True, "fake HTTP listener did not start")
        _require(
            fixture.listener.bound == (AP_IP, 80),
            "server was not bound only to the AP address",
        )

        maximum_step_actions = 0
        for index in range(iterations):
            _exercise_protocol_boundaries(core)
            actions = _exercise_iteration(core, fixture)
            maximum_step_actions = max(maximum_step_actions, actions)
            if index == 0:
                after_warmup = _memory_free()
        after = _memory_free()
        memory_checked = _check_memory(
            before_import,
            after_import,
            after_warmup,
            after,
        )

        application = fixture.runtime.application.snapshot()
        rate_limit = fixture.runtime.rate_limiter.snapshot()
        server = fixture.server.snapshot()
        _require(application["requests"] == iterations * 3, "request count differs")
        _require(application["mutations"] == iterations, "mutation count differs")
        _require(application["errors"] == iterations, "error count differs")
        _require(rate_limit["peer_count"] == 1, "AP peer table differs")
        _require(rate_limit["stop_bypasses"] == iterations, "STOP bypass count differs")
        _require(server["accepted"] == iterations * 3, "accepted count differs")
        _require(server["completed"] == iterations * 3, "completed count differs")
        _require(server["faulted"] is False, "HTTP server faulted")
        _require(fixture.controller.requested_on is False, "final Requested State is ON")
        result = {
            "phase": 8,
            "scope": "usb_only_rest_fake_socket",
            "passed": iterations,
            "requests": application["requests"],
            "mutations": application["mutations"],
            "errors": application["errors"],
            "server_completed": server["completed"],
            "peer_count": rate_limit["peer_count"],
            "stop_bypasses": rate_limit["stop_bypasses"],
            "maximum_step_actions": maximum_step_actions,
            "platform_ticks_checked": platform_ticks_checked,
            "memory_checked": memory_checked,
            "before_import": before_import,
            "after_import": after_import,
            "after_warmup": after_warmup,
            "after": after,
        }
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if fixture is not None:
            try:
                _cleanup_fixture(fixture)
            except BaseException:
                if primary_error is None:
                    raise

    _require(result is not None, "smoke result was not produced")
    result["cleanup_confirmed"] = True
    print(
        "PHASE 8 USB REST PASS: iterations={} requests={} completed={} "
        "peer_count={} step_actions={} heap={}/{}/{}/{}".format(
            result["passed"],
            result["requests"],
            result["server_completed"],
            result["peer_count"],
            result["maximum_step_actions"],
            result["before_import"],
            result["after_import"],
            result["after_warmup"],
            result["after"],
        )
    )
    print(PHASE8_PASS_TOKEN)
    return result


if __name__ == "__main__":
    raise SystemExit(
        "Import and call run(PHASE8_USB_REST_SMOKE_CONFIRM_V1); "
        "direct execution is intentionally disabled."
    )
