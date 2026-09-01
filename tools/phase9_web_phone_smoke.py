"""Bounded Phase-9 phone UI gate over the proven Phase-8 target seam.

Import is inert.  ``run`` reuses the accepted AP-first configuration, storage,
REST, socket and cleanup stages.  Its late proof layer permits GET only and
requires one completely delivered copy of every UI asset plus the four API
reads performed by the booting browser application.
"""

import gc as _gc
import os as _os
import sys as _sys


PHASE9_WEB_PHONE_CONFIRMATION = "PHASE9_WEB_PHONE_CONFIRM_V1"
PHASE9_WEB_PHONE_READY_TOKEN = "PHASE9_WEB_PHONE_READY_V1"
PHASE9_WEB_PHONE_PASS_TOKEN = "PHASE9_WEB_PHONE_SMOKE_PASS_V1"
PHASE9_WEB_PHONE_FAIL_TOKEN = "PHASE9_WEB_PHONE_SMOKE_FAIL_V1"

AP_IP = "192.168.4.1"
ROOT_URL = "http://192.168.4.1/"
MINIMUM_FREE_HEAP_BYTES = 32 * 1024
POLL_INTERVAL_MS = 25
DEFAULT_WINDOW_SECONDS = 300

_STATIC_TARGETS = (
    "/",
    "/assets/base.css",
    "/assets/components.css",
    "/assets/session.css",
    "/assets/i18n.js",
    "/assets/app.js",
    "/assets/home.js",
    "/assets/timers.js",
    "/assets/settings.js",
)
_API_TARGETS = (
    "/api/v1/security-context",
    "/api/v1/status",
    "/api/v1/settings",
    "/api/v1/timers?offset=0&limit=8",
)
_REQUIRED_TARGETS = _STATIC_TARGETS + _API_TARGETS
_PRODUCTION_PATHS = tuple(
    base + suffix
    for base in ("/landy_heater_config", "/landy_heater_scheduler")
    for suffix in (".a", ".b", ".tmp")
)


def _require(condition, message):
    if not condition:
        raise RuntimeError("Phase-9 web phone smoke failed: {}".format(message))


def _memory_free():
    _gc.collect()
    reader = getattr(_gc, "mem_free", None)
    _require(callable(reader), "GC heap API is unavailable")
    value = reader()
    _require(type(value) is int and value >= 0, "GC heap value is malformed")
    return value


def _require_heap(value, checkpoint):
    _require(
        type(value) is int and value >= MINIMUM_FREE_HEAP_BYTES,
        "free heap is below {}".format(checkpoint),
    )
    return value


def _validate_window_seconds(value):
    if type(value) is not int or not 60 <= value <= 300:
        raise ValueError("observation window must be 60 to 300 seconds")
    return value


def _missing_file(error):
    code = getattr(error, "errno", None)
    if code is None and getattr(error, "args", None):
        code = error.args[0]
    return code == 2


def _stat_signature(paths):
    result = []
    for path in paths:
        try:
            result.append(tuple(_os.stat(path)))
        except OSError as error:
            if not _missing_file(error):
                raise
            result.append(None)
    return tuple(result)


def _store_write_truth(manager):
    status = manager.status()
    config_store = status.get("config_store")
    ledger_store = status.get("ledger_store")
    _require(
        type(config_store) is dict and type(ledger_store) is dict,
        "storage status is malformed",
    )
    return (
        status.get("generation"),
        status.get("ledger_generation"),
        config_store.get("writes"),
        ledger_store.get("writes"),
    )


def _ap_peer(value):
    if type(value) is not str:
        return False
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        octets = tuple(int(part) for part in parts)
    except (TypeError, ValueError):
        return False
    return octets[:3] == (192, 168, 4) and 2 <= octets[3] <= 254


class _ObservedClient:
    __slots__ = (
        "_owner", "_port", "_leased", "_request", "target", "bytes_sent"
    )

    def __init__(self, owner):
        self._owner = owner
        self._port = None
        self._leased = False
        self._request = bytearray()
        self.target = None
        self.bytes_sent = 0

    @property
    def leased(self):
        return self._leased

    def claim(self, port):
        _require(not self._leased and self._port is None, "client double lease")
        self._port = port
        self._leased = True
        self._request = bytearray()
        self.target = None
        self.bytes_sent = 0
        return self

    def setblocking(self, value):
        setter = getattr(self._port, "setblocking", None)
        if callable(setter):
            return setter(value)
        setter = getattr(self._port, "settimeout", None)
        if not callable(setter):
            raise AttributeError("accepted socket has no nonblocking API")
        return setter(0 if value is False else None)

    def settimeout(self, value):
        setter = getattr(self._port, "settimeout", None)
        if not callable(setter):
            raise AttributeError("accepted socket has no timeout API")
        return setter(value)

    def _observe_request(self, payload):
        if self.target is not None or not payload:
            return None
        remaining = 512 - len(self._request)
        if remaining <= 0:
            return None
        self._request.extend(payload[:remaining])
        end = self._request.find(b"\r\n")
        if end < 0:
            return None
        fields = bytes(self._request[:end]).split(b" ")
        if len(fields) == 3 and fields[0] == b"GET":
            try:
                self.target = fields[1].decode("ascii")
            except UnicodeError:
                self.target = None
        return None

    def recv(self, maximum):
        payload = self._port.recv(maximum)
        if type(payload) in (bytes, bytearray, memoryview):
            self._observe_request(payload)
        return payload

    def send(self, payload):
        sent = self._port.send(payload)
        if type(sent) is int and 0 < sent <= len(payload):
            self.bytes_sent += sent
        return sent

    def _close_unguarded(self):
        if not self._leased:
            return None
        result = self._port.close()
        if result is None:
            self._owner.closed(self.target, self.bytes_sent)
            self._port = None
            self._leased = False
            self._request = bytearray()
            self.target = None
            self.bytes_sent = 0
        return result

    def close(self):
        return self._close_unguarded()


class _TransportObserver:
    __slots__ = ("clients", "accepted", "closed_count", "completed", "faulted")

    def __init__(self):
        self.clients = (_ObservedClient(self), _ObservedClient(self))
        self.accepted = 0
        self.closed_count = 0
        self.completed = {}
        self.faulted = False

    def _mark_fault(self):
        self.faulted = True
        return None

    def claim_client(self, port):
        for client in self.clients:
            if not client.leased:
                self.accepted += 1
                return client.claim(port)
        raise RuntimeError("observed client capacity exceeded")

    def open_clients(self):
        return sum(1 for client in self.clients if client.leased)

    def closed(self, target, bytes_sent):
        self.closed_count += 1
        if type(target) is str and bytes_sent > 0:
            self.completed[target] = self.completed.get(target, 0) + 1
        return None


class _ReadOnlyWebGateway:
    __slots__ = (
        "application", "controller", "protocol_port", "validated",
        "rejected", "mutation_attempts", "last_peer", "_password"
    )

    def __init__(self, runtime, controller, protocol_port, password):
        from app.web_application import Phase9WebApplication

        self.application = Phase9WebApplication(runtime)
        self.controller = controller
        self.protocol_port = protocol_port
        self.validated = {}
        self.rejected = 0
        self.mutation_attempts = 0
        self.last_peer = None
        self._password = password

    def clear_secret(self):
        self._password = None
        return None

    def _valid_response(self, target, response):
        if target in _STATIC_TARGETS:
            from app.web_assets import asset_for_path

            route = "/index.html" if target == "/" else target
            expected = asset_for_path(route)
            return (
                expected is not None
                and getattr(response, "status", None) == 200
                and getattr(response, "content_type", None) == expected[0]
                and getattr(response, "body", None) == expected[1]
            )
        body = getattr(response, "body", None)
        if getattr(response, "status", None) != 200 or type(body) is not dict:
            return False
        if body.get("api_version") != 1:
            return False
        if target == "/api/v1/security-context":
            return (
                type(body.get("csrf_token")) is str
                and len(body["csrf_token"]) == 64
                and body.get("mutation_api_available") is True
            )
        if target == "/api/v1/status":
            return type(body.get("heater")) is dict and type(body.get("network")) is dict
        if target == "/api/v1/settings":
            return type(body.get("heater")) is dict and type(body.get("network")) is dict
        if target == "/api/v1/timers?offset=0&limit=8":
            return (
                type(body.get("items")) is list
                and body.get("offset") == 0
                and body.get("limit") == 8
            )
        return False

    def handle(self, request, peer_ip=None):
        method = getattr(request, "method", None)
        target = getattr(request, "target", None)
        if method != "GET":
            self.mutation_attempts += 1
            self.rejected += 1
            from app.web_application import WebResponse

            return WebResponse(404, b"", "text/plain; charset=utf-8")
        if target not in _REQUIRED_TARGETS and target != "/favicon.ico":
            self.rejected += 1
            return self.application.handle(request, peer_ip)
        if not _ap_peer(peer_ip):
            self.rejected += 1
            return self.application.handle(request, peer_ip)
        response = self.application.handle(request, peer_ip)
        if target in _REQUIRED_TARGETS and self._valid_response(target, response):
            self.validated[target] = self.validated.get(target, 0) + 1
            self.last_peer = peer_ip
        elif target in _REQUIRED_TARGETS:
            self.rejected += 1
        _require(self.controller.requested_on is False, "Requested State changed")
        _require(self.protocol_port.calls == 0, "heater protocol was accessed")
        _require(self._password not in repr(response), "WPA2 key leaked")
        return response


def prepare_proof(context):
    """Late seam hook called after bind and immediately before listen."""

    _require(context.gateway is None, "web proof gateway already exists")
    _require(context.socket_observer is None, "socket observer already exists")
    gateway = _ReadOnlyWebGateway(
        context.rest_runtime,
        context.controller,
        context.protocol_port,
        context.password,
    )
    context.gateway = gateway
    context.socket_observer = _TransportObserver()
    context.password = None
    return None


def _all_targets_complete(gateway, observer):
    for target in _REQUIRED_TARGETS:
        if gateway.validated.get(target, 0) < 1:
            return False
        if observer.completed.get(target, 0) < 1:
            return False
    return True


def continue_run(capsule, state, window_seconds):
    """Observe one full browser boot, then perform the proven ordered cleanup."""

    from tools import phase8_full_rest_phone_stage2_seam as seam

    context = state.context
    gateway = context.gateway
    observer = context.socket_observer
    server = state.server
    core = context.core
    context.failure_stage = "phase9_ready_preflight"
    _require(state.proof_loaded is True, "late proof was not loaded")
    _require(server.started is True, "HTTP listener is not active")
    _require(
        capsule.port.access_point_status()
        == {"active": True, "ip": AP_IP, "clients": 1},
        "phone association changed before READY",
    )
    memory_after_bind = _require_heap(
        _memory_free(), "HTTP bind with associated phone"
    )
    context.failure_stage = "phase9_observe"
    print(PHASE9_WEB_PHONE_READY_TOKEN)
    print("url={}".format(ROOT_URL))
    print("window_seconds={}".format(window_seconds))
    print("Open the exact root URL once and leave it visible.")

    memory_after_response = None
    while True:
        now_ms = core.ticks_ms()
        if core.ticks_diff(now_ms, context.observation_deadline) >= 0:
            raise RuntimeError("complete UI and API browser boot was not observed")
        context.failure_stage = "phase9_network_step"
        action = context.network_manager.step(now_ms)
        snapshot = context.network_manager.snapshot()
        context.network_manager.drain_events()
        access_point = snapshot["access_point"]
        _require(
            access_point["active"] is True
            and access_point["ip"] == AP_IP
            and access_point["clients"] == 1,
            "AP truth changed during UI observation",
        )
        _require(action is None or action == "ap_checked", "network changed state")
        context.failure_stage = "phase9_http_step"
        before = server.snapshot()["completed"]
        server.step()
        context.failure_stage = "phase9_http_snapshot"
        server_snapshot = server.snapshot()
        _require(
            server_snapshot["started"] is True
            and server_snapshot["closed"] is False
            and server_snapshot["faulted"] is False
            and server_snapshot["parse_errors"] == 0
            and server_snapshot["socket_errors"] == 0
            and server_snapshot["reentries"] == 0,
            "HTTP transport reported a fault",
        )
        _require(
            observer.accepted == server_snapshot["accepted"]
            and observer.closed_count + observer.open_clients()
            == observer.accepted
            and observer.open_clients() == server_snapshot["client_count"],
            "socket observer diverged",
        )
        _require(observer.faulted is False, "socket observer faulted")
        context.failure_stage = "phase9_completion_gate"
        if _all_targets_complete(gateway, observer):
            _require(server_snapshot["completed"] >= before, "completion regressed")
            memory_after_response = _require_heap(
                _memory_free(), "complete UI and API responses"
            )
            break
        result = core.sleep_ms(POLL_INTERVAL_MS)
        _require(result is None, "sleep_ms returned a value")

    context.failure_stage = "phase9_post_response_safety"
    _require(gateway.mutation_attempts == 0, "a mutation request was observed")
    _require(context.controller.requested_on is False, "Requested State changed")
    _require(context.controller.request_revision == 0, "Requested revision changed")
    _require(context.protocol_port.calls == 0, "heater protocol was accessed")
    _require(
        _store_write_truth(context.config_manager)
        == context.storage_write_baseline,
        "isolated product storage changed during reads",
    )
    _require(
        _stat_signature(_PRODUCTION_PATHS) == context.production_stat_baseline,
        "production storage changed during the gate",
    )
    context.failure_stage = "phase9_cleanup"
    _require(seam.fallback_cleanup(capsule, state) is True, "cleanup failed")
    memory_after_cleanup = _require_heap(_memory_free(), "ordered cleanup")
    _require(capsule.owner_state == "released", "ownership was not released")
    _require(
        capsule.support._interfaces_inactive(capsule.network_module),
        "a WLAN interface remained active",
    )
    heaps = (
        capsule.memory_before,
        context.memory_after_product_imports,
        context.memory_after_configuration_adoption,
        capsule.memory_after_wifi_factory,
        capsule.memory_after_ap_ready,
        capsule.memory_after_client_association,
        context.memory_before_http_start,
        context.memory_after_proof_before_listen,
        memory_after_bind,
        memory_after_response,
        memory_after_cleanup,
    )
    _require(
        all(type(value) is int and value >= MINIMUM_FREE_HEAP_BYTES for value in heaps),
        "one or more heap gates failed",
    )
    print("ui_assets_completed={}".format(len(_STATIC_TARGETS)))
    print("api_reads_completed={}".format(len(_API_TARGETS)))
    print("mutation_requests=0")
    print("http_rest_radio_cleanup_confirmed=True")
    print(PHASE9_WEB_PHONE_PASS_TOKEN)
    return {
        "phase": 9,
        "port": 80,
        "ui_assets": len(_STATIC_TARGETS),
        "api_reads": len(_API_TARGETS),
    }


def _emit_failure(state, error):
    """Emit bounded counters only; never response bodies, tokens or keys."""

    print("PHASE9_WEB_PHONE_FAILURE_STAGE_V1")
    if state is None:
        print("stage=before_state")
        print("error_type={}".format(type(error).__name__))
        return None
    context = state.context
    print("stage={}".format(context.failure_stage or "unknown"))
    print("error_type={}".format(type(error).__name__))
    try:
        snapshot = state.server.snapshot()
        print(
            "http={} {} {} {} {} {} {}".format(
                snapshot.get("accepted"),
                snapshot.get("completed"),
                snapshot.get("client_count"),
                snapshot.get("parse_errors"),
                snapshot.get("timeouts"),
                snapshot.get("socket_errors"),
                snapshot.get("faulted"),
            )
        )
    except BaseException:
        print("http=unavailable")
    gateway = context.gateway
    if gateway is not None:
        print(
            "gateway={} {} {}".format(
                sum(gateway.validated.values()),
                gateway.rejected,
                gateway.mutation_attempts,
            )
        )
        print(
            "validated={}".format(
                ",".join(
                    target for target in _REQUIRED_TARGETS
                    if gateway.validated.get(target, 0) > 0
                )
            )
        )
    observer = context.socket_observer
    if observer is not None:
        print(
            "observer={} {} {} {}".format(
                observer.accepted,
                observer.closed_count,
                observer.open_clients(),
                observer.faulted,
            )
        )
        print(
            "completed={}".format(
                ",".join(
                    target for target in _REQUIRED_TARGETS
                    if observer.completed.get(target, 0) > 0
                )
            )
        )
    return None


def run(confirmation, temporary_password, window_seconds=DEFAULT_WINDOW_SECONDS):
    """Run the bounded UI gate under the existing exclusive hardware owner."""

    if confirmation != PHASE9_WEB_PHONE_CONFIRMATION:
        raise RuntimeError("exact Phase-9 web confirmation is required")
    window_seconds = _validate_window_seconds(window_seconds)
    from tools import phase8_full_rest_phone_smoke as base

    password = base._validate_password(temporary_password)
    capsule = base._OwnershipCapsule()
    capsule.support = base
    stage1 = None
    seam = None
    prepare = None
    state = None
    primary = None
    try:
        base._require_stage2_unloaded()
        stage1 = base._load_stage1()
        stage1.prepare(capsule, password, window_seconds)
        base._unload_stage1(stage1)
        stage1 = None
        _gc.collect()
        base._require_stage2_unloaded()
        seam = base._load_stage2_seam()
        state = seam.Stage2State()
        seam._load_proof = lambda: _sys.modules[__name__]
        prepare = base._load_stage2_prepare()
        prepare.prepare(capsule, state, password, window_seconds)
        base._unload_module(
            prepare,
            base._STAGE2_PREPARE_MODULE,
            "phase8_full_rest_phone_stage2_prepare",
        )
        prepare = None
        state.context.memory_before_http_start = seam.require_heap(
            seam.memory_free(), seam.MINIMUM_PRE_BIND_HEAP_BYTES, "pre-bind"
        )
        _require(state.server.start() is True, "HTTP server did not start")
        return continue_run(capsule, state, window_seconds)
    except BaseException as error:
        primary = error
        try:
            _emit_failure(state, error)
        except BaseException:
            print("PHASE9_WEB_PHONE_FAILURE_STAGE_V1")
            print("stage=diagnostics_failed")
    finally:
        if stage1 is not None:
            try:
                base._unload_stage1(stage1)
            except BaseException:
                pass
        if prepare is not None:
            try:
                base._unload_module(
                    prepare,
                    base._STAGE2_PREPARE_MODULE,
                    "phase8_full_rest_phone_stage2_prepare",
                )
            except BaseException:
                pass
        if capsule.owner_state != "released" and seam is not None and state is not None:
            try:
                seam.fallback_cleanup(capsule, state)
            except BaseException:
                pass
        if capsule.owner_state != "released":
            try:
                base._outer_cleanup(capsule)
            except BaseException:
                pass
    print(PHASE9_WEB_PHONE_FAIL_TOKEN)
    if isinstance(primary, (KeyboardInterrupt, SystemExit, MemoryError)):
        raise primary
    raise RuntimeError("Phase-9 web phone smoke failed") from None
