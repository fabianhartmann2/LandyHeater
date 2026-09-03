"""Bounded Phase-10 Setup Assistant phone gate.

Import is inert. ``run`` reuses the accepted single-listener Phase-8/9 target
seam, requires one complete browser boot and permits exactly one successful
``PUT /api/v1/setup`` against disposable A/B storage. Product storage and all
heater/peripheral gates remain untouched.
"""

import gc as _gc
import sys as _sys

from tools import phase9_web_phone_smoke as _phase9


PHASE10_SETUP_PHONE_CONFIRMATION = "PHASE10_SETUP_PHONE_CONFIRM_V1"
PHASE10_SETUP_PHONE_READY_TOKEN = "PHASE10_SETUP_PHONE_READY_V1"
PHASE10_SETUP_PHONE_PASS_TOKEN = "PHASE10_SETUP_PHONE_SMOKE_PASS_V1"
PHASE10_SETUP_PHONE_FAIL_TOKEN = "PHASE10_SETUP_PHONE_SMOKE_FAIL_V1"

AP_IP = _phase9.AP_IP
ROOT_URL = _phase9.ROOT_URL
MINIMUM_FREE_HEAP_BYTES = _phase9.MINIMUM_FREE_HEAP_BYTES
POLL_INTERVAL_MS = _phase9.POLL_INTERVAL_MS
DEFAULT_WINDOW_SECONDS = _phase9.DEFAULT_WINDOW_SECONDS

_STATIC_TARGETS = (
    "/",
    "/assets/base.css",
    "/assets/components.css",
    "/assets/session.css",
    "/assets/setup.css",
    "/assets/i18n.js",
    "/assets/app.js",
    "/assets/home.js",
    "/assets/timers.js",
    "/assets/settings.js",
    "/assets/setup.js",
)
_API_TARGETS = (
    "/api/v1/security-context",
    "/api/v1/status",
    "/api/v1/settings",
    "/api/v1/timers?offset=0&limit=8",
    "/api/v1/setup",
)
_READ_TARGETS = _STATIC_TARGETS + _API_TARGETS
_SETUP_MUTATION = "PUT /api/v1/setup"
_PRODUCTION_PATHS = _phase9._PRODUCTION_PATHS


def _require(condition, message):
    if not condition:
        raise RuntimeError("Phase-10 setup phone smoke failed: {}".format(message))


def _contains_password(value):
    if type(value) is dict:
        for key, item in value.items():
            if key == "password" or _contains_password(item):
                return True
    elif type(value) is list:
        for item in value:
            if _contains_password(item):
                return True
    return False


class _ObservedClient(_phase9._ObservedClient):
    __slots__ = ()

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
        if len(fields) != 3:
            return None
        try:
            method = fields[0].decode("ascii")
            target = fields[1].decode("ascii")
        except UnicodeError:
            return None
        self.target = target if method == "GET" else method + " " + target
        return None


class _TransportObserver(_phase9._TransportObserver):
    __slots__ = ()

    def __init__(self):
        self.clients = (_ObservedClient(self), _ObservedClient(self))
        self.accepted = 0
        self.closed_count = 0
        self.completed = {}
        self.faulted = False


class _SetupWebGateway:
    __slots__ = (
        "application", "controller", "protocol_port", "manager",
        "validated", "rejected", "mutation_attempts",
        "successful_mutations", "last_peer", "_live_password",
        "_expected_ap_password", "_expected_station_ssid",
        "_expected_station_password",
    )

    def __init__(
        self,
        runtime,
        controller,
        protocol_port,
        manager,
        live_password,
        expected_ap_password,
        expected_station_ssid,
        expected_station_password,
    ):
        from app.web_application import Phase9WebApplication

        self.application = Phase9WebApplication(runtime)
        self.controller = controller
        self.protocol_port = protocol_port
        self.manager = manager
        self.validated = {}
        self.rejected = 0
        self.mutation_attempts = 0
        self.successful_mutations = 0
        self.last_peer = None
        self._live_password = live_password
        self._expected_ap_password = expected_ap_password
        self._expected_station_ssid = expected_station_ssid
        self._expected_station_password = expected_station_password

    def clear_secret(self):
        self._live_password = None
        self._expected_ap_password = None
        self._expected_station_ssid = None
        self._expected_station_password = None
        return None

    def _assert_redacted(self, response):
        rendered = repr(response)
        for value in (
            self._live_password,
            self._expected_ap_password,
            self._expected_station_password,
        ):
            _require(value not in rendered, "a Wi-Fi key leaked")
        return None

    def _valid_read(self, target, response):
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
        if body.get("api_version") != 1 or _contains_password(body):
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
        if target == "/api/v1/setup":
            checks = body.get("checks")
            return (
                type(body.get("network")) is dict
                and type(checks) is dict
                and checks.get("sensors", {}).get("active_probe_performed") is False
                and checks.get("autoterm", {}).get("active_test_performed") is False
            )
        return False

    def _valid_setup_commit(self, response):
        body = getattr(response, "body", None)
        if (
            getattr(response, "status", None) != 200
            or type(body) is not dict
            or body.get("api_version") != 1
            or body.get("changed") is not True
            or body.get("system", {}).get("setup_complete") is not True
            or _contains_password(body)
        ):
            return False
        privileged = self.manager.snapshot()["configuration"]
        network = privileged["network"]
        profiles = network["known_networks"]
        return (
            privileged["system"]["setup_complete"] is True
            and network["access_point"]["password"]
            == self._expected_ap_password
            and len(profiles) == 1
            and profiles[0]["ssid"] == self._expected_station_ssid
            and profiles[0]["password"] == self._expected_station_password
        )

    def handle(self, request, peer_ip=None):
        method = getattr(request, "method", None)
        target = getattr(request, "target", None)
        if not _phase9._ap_peer(peer_ip):
            self.rejected += 1
            return self.application.handle(request, peer_ip)
        if method == "PUT" and target == "/api/v1/setup":
            self.mutation_attempts += 1
            if self.mutation_attempts != 1:
                self.rejected += 1
                from app.web_application import WebResponse

                return WebResponse(404, b"", "text/plain; charset=utf-8")
            response = self.application.handle(request, peer_ip)
            if self._valid_setup_commit(response):
                self.successful_mutations = 1
                self.last_peer = peer_ip
            else:
                self.rejected += 1
            _require(self.controller.requested_on is False, "Requested State changed")
            _require(self.protocol_port.calls == 0, "heater protocol was accessed")
            self._assert_redacted(response)
            return response
        if method != "GET":
            self.mutation_attempts += 1
            self.rejected += 1
            from app.web_application import WebResponse

            return WebResponse(404, b"", "text/plain; charset=utf-8")
        if target not in _READ_TARGETS and target != "/favicon.ico":
            self.rejected += 1
            return self.application.handle(request, peer_ip)
        response = self.application.handle(request, peer_ip)
        if target in _READ_TARGETS and self._valid_read(target, response):
            self.validated[target] = self.validated.get(target, 0) + 1
            self.last_peer = peer_ip
        elif target in _READ_TARGETS:
            self.rejected += 1
        _require(self.controller.requested_on is False, "Requested State changed")
        _require(self.protocol_port.calls == 0, "heater protocol was accessed")
        self._assert_redacted(response)
        return response


def prepare_proof(context):
    _require(context.gateway is None, "setup proof gateway already exists")
    _require(context.socket_observer is None, "socket observer already exists")
    secrets = context.password
    _require(
        type(secrets) is tuple and len(secrets) == 4,
        "setup proof secrets are malformed",
    )
    context.gateway = _SetupWebGateway(
        context.rest_runtime,
        context.controller,
        context.protocol_port,
        context.config_manager,
        secrets[0],
        secrets[1],
        secrets[2],
        secrets[3],
    )
    context.socket_observer = _TransportObserver()
    context.password = None
    return None


def _all_complete(gateway, observer):
    missing_application, missing_wire = _missing_targets(gateway, observer)
    if missing_application or missing_wire:
        return False
    return (
        gateway.successful_mutations == 1
        and observer.completed.get(_SETUP_MUTATION, 0) >= 1
    )


def _missing_targets(gateway, observer):
    return (
        tuple(
            target for target in _READ_TARGETS
            if gateway.validated.get(target, 0) < 1
        ),
        tuple(
            target for target in _READ_TARGETS
            if observer.completed.get(target, 0) < 1
        ),
    )


def _committed_once(before, after):
    return (
        type(before) is tuple
        and type(after) is tuple
        and len(before) == 4
        and len(after) == 4
        and after[0] == before[0] + 1
        and after[1] == before[1]
        and after[2] == before[2] + 1
        and after[3] == before[3]
    )


def continue_run(capsule, state, window_seconds):
    from tools import phase8_full_rest_phone_stage2_seam as seam

    context = state.context
    gateway = context.gateway
    observer = context.socket_observer
    server = state.server
    core = context.core
    context.failure_stage = "phase10_ready_preflight"
    _require(state.proof_loaded is True, "late proof was not loaded")
    _require(server.started is True, "HTTP listener is not active")
    _require(
        capsule.port.access_point_status()
        == {"active": True, "ip": AP_IP, "clients": 1},
        "phone association changed before READY",
    )
    memory_after_bind = _phase9._require_heap(
        _phase9._memory_free(), "HTTP bind with associated phone"
    )
    context.failure_stage = "phase10_observe"
    print(PHASE10_SETUP_PHONE_READY_TOKEN)
    print("url={}".format(ROOT_URL))
    print("window_seconds={}".format(window_seconds))
    print("Open the root once, enter the agreed test WLAN values, and submit once.")

    memory_after_response = None
    while True:
        now_ms = core.ticks_ms()
        if core.ticks_diff(now_ms, context.observation_deadline) >= 0:
            raise RuntimeError("complete Setup Assistant flow was not observed")
        context.failure_stage = "phase10_network_step"
        action = context.network_manager.step(now_ms)
        snapshot = context.network_manager.snapshot()
        context.network_manager.drain_events()
        access_point = snapshot["access_point"]
        _require(
            access_point["active"] is True
            and access_point["ip"] == AP_IP
            and access_point["clients"] == 1,
            "AP truth changed during setup observation",
        )
        _require(action is None or action == "ap_checked", "network changed state")
        context.failure_stage = "phase10_http_step"
        server.step()
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
        context.failure_stage = "phase10_completion_gate"
        if _all_complete(gateway, observer):
            memory_after_response = _phase9._require_heap(
                _phase9._memory_free(), "complete setup responses"
            )
            break
        result = core.sleep_ms(POLL_INTERVAL_MS)
        _require(result is None, "sleep_ms returned a value")

    context.failure_stage = "phase10_post_response_safety"
    after_write = _phase9._store_write_truth(context.config_manager)
    _require(gateway.mutation_attempts == 1, "mutation count is not exactly one")
    _require(gateway.successful_mutations == 1, "setup mutation did not succeed")
    _require(
        _committed_once(context.storage_write_baseline, after_write),
        "isolated setup was not committed exactly once",
    )
    _require(context.controller.requested_on is False, "Requested State changed")
    _require(context.controller.request_revision == 0, "Requested revision changed")
    _require(context.protocol_port.calls == 0, "heater protocol was accessed")
    _require(
        _phase9._stat_signature(_PRODUCTION_PATHS)
        == context.production_stat_baseline,
        "production storage changed during the gate",
    )
    context.failure_stage = "phase10_cleanup"
    _require(seam.fallback_cleanup(capsule, state) is True, "cleanup failed")
    memory_after_cleanup = _phase9._require_heap(
        _phase9._memory_free(), "ordered cleanup"
    )
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
    gateway.clear_secret()
    print("ui_assets_completed={}".format(len(_STATIC_TARGETS)))
    print("api_reads_completed={}".format(len(_API_TARGETS)))
    print("setup_mutations_completed=1")
    print("isolated_commits=1")
    print("http_rest_radio_cleanup_confirmed=True")
    print(PHASE10_SETUP_PHONE_PASS_TOKEN)
    return {
        "phase": 10,
        "port": 80,
        "ui_assets": len(_STATIC_TARGETS),
        "api_reads": len(_API_TARGETS),
        "setup_mutations": 1,
    }


def _emit_failure(state, error):
    print("PHASE10_SETUP_PHONE_FAILURE_STAGE_V1")
    if state is None:
        print("stage=before_state")
        print("error_type={}".format(type(error).__name__))
        return None
    context = state.context
    print("stage={}".format(context.failure_stage or "unknown"))
    print("error_type={}".format(type(error).__name__))
    print("error={}".format(str(error)))
    gateway = context.gateway
    if gateway is not None:
        print(
            "gateway={} {} {} {}".format(
                sum(gateway.validated.values()),
                gateway.rejected,
                gateway.mutation_attempts,
                gateway.successful_mutations,
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
    if gateway is not None and observer is not None:
        missing_application, missing_wire = _missing_targets(gateway, observer)
        print("missing_application={}".format(",".join(missing_application)))
        print("missing_wire={}".format(",".join(missing_wire)))
    server = state.server
    if server is not None:
        snapshot = server.snapshot()
        print(
            "server={} {} {} {} {} {} {}".format(
                snapshot.get("accepted"),
                snapshot.get("completed"),
                snapshot.get("timeouts"),
                snapshot.get("parse_errors"),
                snapshot.get("socket_errors"),
                snapshot.get("faulted"),
                snapshot.get("last_error"),
            )
        )
    return None


def run(
    confirmation,
    temporary_password,
    expected_ap_password,
    expected_station_ssid,
    expected_station_password,
    window_seconds=DEFAULT_WINDOW_SECONDS,
):
    if confirmation != PHASE10_SETUP_PHONE_CONFIRMATION:
        raise RuntimeError("exact Phase-10 setup confirmation is required")
    window_seconds = _phase9._validate_window_seconds(window_seconds)
    from tools import phase8_full_rest_phone_smoke as base

    password = base._validate_password(temporary_password)
    replacement = base._validate_password(expected_ap_password)
    station_password = base._validate_password(expected_station_password)
    if (
        type(expected_station_ssid) is not str
        or not expected_station_ssid
        or len(expected_station_ssid.encode("utf-8")) > 32
    ):
        raise ValueError("expected station SSID is invalid")
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
        state.context.password = (
            password,
            replacement,
            expected_station_ssid,
            station_password,
        )
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
            print("PHASE10_SETUP_PHONE_FAILURE_STAGE_V1")
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
    print(PHASE10_SETUP_PHONE_FAIL_TOKEN)
    if isinstance(primary, (KeyboardInterrupt, SystemExit, MemoryError)):
        raise primary
    raise RuntimeError("Phase-10 setup phone smoke failed") from None
