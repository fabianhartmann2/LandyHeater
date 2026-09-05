"""Bounded Phase-11 diagnostics UI and RAM-capture phone gate.

Importing this module is inert. ``run`` reuses the established AP-only,
isolated-storage, full-REST and ordered-cleanup seams.  It permits only the
Phase-11 capture mutations and injects synthetic diagnostic records without
touching UART or any heater/peripheral capability.
"""

import gc as _gc
import sys as _sys

from tools import phase9_web_phone_smoke as _phase9


PHASE11_DIAGNOSTICS_CONFIRMATION = "PHASE11_DIAGNOSTICS_CONFIRM_V1"
PHASE11_DIAGNOSTICS_READY_TOKEN = "PHASE11_DIAGNOSTICS_READY_V1"
PHASE11_DIAGNOSTICS_PASS_TOKEN = "PHASE11_DIAGNOSTICS_PHONE_PASS_V1"
PHASE11_DIAGNOSTICS_FAIL_TOKEN = "PHASE11_DIAGNOSTICS_PHONE_FAIL_V1"

AP_IP = "192.168.4.1"
ROOT_URL = "http://192.168.4.1/"
POLL_INTERVAL_MS = 25
DEFAULT_WINDOW_SECONDS = 300
_SECRET_SENTINEL = "PHASE11-MUST-NOT-LEAK"

_STATIC_TARGETS = (
    "/assets/diagnostics.css",
    "/assets/diagnostics.js",
    "/assets/diagnostics.html",
)
_DIAGNOSTICS_PREFIX = "/api/v1/diagnostics?"
_CAPTURE_EXPORT_PREFIX = "/api/v1/capture/export?"
_CAPTURE_TARGET = "/api/v1/capture"


def _require(condition, message):
    if not condition:
        raise RuntimeError(
            "Phase-11 diagnostics phone smoke failed: {}".format(message)
        )


def _target_matches(values, prefix):
    for value in values:
        if type(value) is str and value.startswith(prefix):
            return True
    return False


class _DiagnosticsWebGateway:
    __slots__ = (
        "application",
        "runtime",
        "controller",
        "protocol_port",
        "validated",
        "rejected",
        "mutation_attempts",
        "successful_starts",
        "successful_stops",
        "exported",
        "last_peer",
        "_password",
    )

    def __init__(self, runtime, controller, protocol_port, password):
        from app.web_application import Phase9WebApplication

        self.application = Phase9WebApplication(runtime, AP_IP)
        self.runtime = runtime
        self.controller = controller
        self.protocol_port = protocol_port
        self.validated = {}
        self.rejected = 0
        self.mutation_attempts = 0
        self.successful_starts = 0
        self.successful_stops = 0
        self.exported = 0
        self.last_peer = None
        self._password = password

    def clear_secret(self):
        self._password = None
        return None

    def _safety(self, response):
        _require(self.controller.requested_on is False, "Requested State changed")
        _require(self.controller.request_revision == 0, "request revision changed")
        _require(self.protocol_port.calls == 0, "heater protocol was accessed")
        rendered = repr(getattr(response, "body", None))
        _require(_SECRET_SENTINEL not in rendered, "redaction sentinel leaked")
        if self._password is not None:
            _require(self._password not in rendered, "WPA2 key leaked")
        return response

    def _record(self, key):
        self.validated[key] = self.validated.get(key, 0) + 1

    def _valid_read(self, target, response):
        if target in _STATIC_TARGETS:
            from app.web_assets import asset_for_path

            expected = asset_for_path(target)
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
        if target.startswith(_DIAGNOSTICS_PREFIX):
            live = body.get("live")
            return (
                type(live) is dict
                and type(live.get("phase11")) is dict
                and type(body.get("events")) is dict
                and type(body.get("protocol_log")) is dict
            )
        if target.startswith(_CAPTURE_EXPORT_PREFIX):
            capture = body.get("capture_export")
            return (
                type(capture) is dict
                and capture.get("schema") == "landy-heater.protocol-capture"
                and capture.get("version") == 1
                and capture.get("active") is None
                and capture.get("total", 0) >= 4
                and _SECRET_SENTINEL not in repr(capture)
            )
        return False

    def _capture_mutation(self, method, response, now_ms):
        body = getattr(response, "body", None)
        capture = body.get("capture") if type(body) is dict else None
        if method == "POST" and getattr(response, "status", None) == 201:
            if type(capture) is dict and capture.get("active") is True:
                self.successful_starts += 1
                hub = self.runtime.diagnostics_hub
                _require(
                    hub.record_event(
                        "phase11",
                        "synthetic_phone_gate",
                        now_ms,
                        {"value": 11, "password": _SECRET_SENTINEL},
                    ) is True,
                    "synthetic event was not recorded",
                )
                _require(
                    hub.record_protocol_activity(
                        ("rx_frame", now_ms, b"\xaa\x55\x02\x01\x33\x00", {})
                    ) is True,
                    "synthetic protocol frame was not recorded",
                )
                return True
        if method == "DELETE" and getattr(response, "status", None) == 200:
            if type(capture) is dict and capture.get("available") is True:
                self.successful_stops += 1
                return True
        return False

    def handle(self, request, peer_ip=None):
        method = getattr(request, "method", None)
        target = getattr(request, "target", None)
        if not _phase9._ap_peer(peer_ip):
            self.rejected += 1
            return self._safety(self.application.handle(request, peer_ip))

        if method in ("POST", "DELETE"):
            self.mutation_attempts += 1
            if target != _CAPTURE_TARGET:
                self.rejected += 1
                from app.web_application import WebResponse

                return self._safety(
                    WebResponse(404, b"", "text/plain; charset=utf-8")
                )
            response = self.application.handle(request, peer_ip)
            now_ms = self.runtime.diagnostics_hub._ticks_ms()
            if not self._capture_mutation(method, response, now_ms):
                self.rejected += 1
            else:
                self.last_peer = peer_ip
            return self._safety(response)

        if method != "GET":
            self.mutation_attempts += 1
            self.rejected += 1
            from app.web_application import WebResponse

            return self._safety(
                WebResponse(404, b"", "text/plain; charset=utf-8")
            )

        response = self.application.handle(request, peer_ip)
        observed = False
        if target in _STATIC_TARGETS and self._valid_read(target, response):
            self._record(target)
            observed = True
        elif type(target) is str and target.startswith(_DIAGNOSTICS_PREFIX):
            if self._valid_read(target, response):
                self._record(_DIAGNOSTICS_PREFIX)
                observed = True
        elif type(target) is str and target.startswith(_CAPTURE_EXPORT_PREFIX):
            if self._valid_read(target, response):
                self._record(_CAPTURE_EXPORT_PREFIX)
                self.exported += 1
                observed = True
        if observed:
            self.last_peer = peer_ip
        return self._safety(response)


def prepare_proof(context):
    _require(context.gateway is None, "diagnostics proof gateway already exists")
    _require(context.socket_observer is None, "socket observer already exists")
    # The shared AP gate deliberately provisions an empty isolated product
    # profile.  Phase 11 tests the diagnostics view, not first-run setup, so
    # mark only that disposable profile complete before the browser arrives.
    snapshot = context.config_manager.snapshot()
    configuration = snapshot["configuration"]
    configuration["system"]["setup_complete"] = True
    _require(
        context.config_manager.commit(
            configuration, context.config_manager.generation
        ) is True,
        "isolated setup-complete precondition was not committed",
    )
    context.storage_write_baseline = _phase9._store_write_truth(
        context.config_manager
    )
    context.gateway = _DiagnosticsWebGateway(
        context.rest_runtime,
        context.controller,
        context.protocol_port,
        context.password,
    )
    context.socket_observer = _phase9._TransportObserver()
    context.password = None
    return None


def _wire_complete(observer, exact=None, prefix=None):
    values = observer.completed.keys()
    if exact is not None:
        return observer.completed.get(exact, 0) >= 1
    return _target_matches(values, prefix)


def _all_complete(gateway, observer):
    return (
        all(gateway.validated.get(target, 0) >= 1 for target in _STATIC_TARGETS)
        and gateway.validated.get(_DIAGNOSTICS_PREFIX, 0) >= 1
        and gateway.validated.get(_CAPTURE_EXPORT_PREFIX, 0) >= 1
        and gateway.successful_starts == 1
        and gateway.successful_stops == 1
        and gateway.exported >= 1
        and all(_wire_complete(observer, exact=target) for target in _STATIC_TARGETS)
        and _wire_complete(observer, prefix=_DIAGNOSTICS_PREFIX)
        and _wire_complete(observer, prefix=_CAPTURE_EXPORT_PREFIX)
    )


def continue_run(capsule, state, window_seconds):
    from tools import phase8_full_rest_phone_stage2_seam as seam

    context = state.context
    gateway = context.gateway
    observer = context.socket_observer
    server = state.server
    core = context.core
    context.failure_stage = "phase11_ready_preflight"
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
    context.failure_stage = "phase11_observe"
    print(PHASE11_DIAGNOSTICS_READY_TOKEN)
    print("url={}".format(ROOT_URL))
    print("window_seconds={}".format(window_seconds))
    print("Open Diagnose, start a named capture, stop it, then export JSON once.")

    memory_after_response = None
    last_progress_ms = core.ticks_ms()
    while True:
        now_ms = core.ticks_ms()
        if core.ticks_diff(now_ms, context.observation_deadline) >= 0:
            raise RuntimeError("complete diagnostics flow was not observed")
        context.failure_stage = "phase11_network_step"
        action = context.network_manager.step(now_ms)
        snapshot = context.network_manager.snapshot()
        context.network_manager.drain_events()
        access_point = snapshot["access_point"]
        _require(
            access_point["active"] is True
            and access_point["ip"] == AP_IP
            and access_point["clients"] == 1,
            "AP truth changed during diagnostics observation",
        )
        _require(action is None or action == "ap_checked", "network changed state")
        context.failure_stage = "phase11_http_step"
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
        if core.ticks_diff(now_ms, last_progress_ms) >= 5000:
            print(
                "PHASE11_PROGRESS {} {} {} {} {} {} {}".format(
                    sum(
                        1
                        for target in _STATIC_TARGETS
                        if gateway.validated.get(target, 0) >= 1
                    ),
                    gateway.validated.get(_DIAGNOSTICS_PREFIX, 0),
                    gateway.successful_starts,
                    gateway.successful_stops,
                    gateway.exported,
                    server_snapshot["accepted"],
                    server_snapshot["completed"],
                )
            )
            last_progress_ms = now_ms
        context.failure_stage = "phase11_completion_gate"
        if _all_complete(gateway, observer):
            memory_after_response = _phase9._require_heap(
                _phase9._memory_free(), "complete diagnostics responses"
            )
            break
        result = core.sleep_ms(POLL_INTERVAL_MS)
        _require(result is None, "sleep_ms returned a value")

    context.failure_stage = "phase11_post_response_safety"
    _require(gateway.mutation_attempts == 2, "capture mutation count is not two")
    _require(context.controller.requested_on is False, "Requested State changed")
    _require(context.controller.request_revision == 0, "request revision changed")
    _require(context.protocol_port.calls == 0, "heater protocol was accessed")
    _require(
        _phase9._store_write_truth(context.config_manager)
        == context.storage_write_baseline,
        "isolated product storage changed",
    )
    _require(
        _phase9._stat_signature(_phase9._PRODUCTION_PATHS)
        == context.production_stat_baseline,
        "production storage changed",
    )
    capture_items = context.rest_runtime.diagnostics_hub.capture_page(0, 4)["items"]
    _require(len(capture_items) == 4, "bounded synthetic capture differs")
    _require(_SECRET_SENTINEL not in repr(capture_items), "capture redaction failed")
    context.failure_stage = "phase11_cleanup"
    _require(seam.fallback_cleanup(capsule, state) is True, "cleanup failed")
    memory_after_cleanup = _phase9._require_heap(
        _phase9._memory_free(), "ordered cleanup"
    )
    _require(capsule.owner_state == "released", "ownership was not released")
    _require(
        capsule.support._interfaces_inactive(capsule.network_module),
        "a WLAN interface remained active",
    )
    _require(context.rest_runtime.diagnostics_hub.closed is True, "hub remained open")
    _require(
        context.rest_runtime.diagnostics_hub.snapshot()["event_count"] == 0,
        "diagnostic events remained after cleanup",
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
        all(
            type(value) is int and value >= _phase9.MINIMUM_FREE_HEAP_BYTES
            for value in heaps
        ),
        "one or more heap gates failed",
    )
    print("diagnostics_assets_completed={}".format(len(_STATIC_TARGETS)))
    print("diagnostics_live_reads={}".format(gateway.validated[_DIAGNOSTICS_PREFIX]))
    print("capture_mutations=2")
    print("capture_exports={}".format(gateway.exported))
    print("http_rest_radio_cleanup_confirmed=True")
    print(PHASE11_DIAGNOSTICS_PASS_TOKEN)
    return {"phase": 11, "port": 80, "capture_items": 4}


def _emit_failure(state, error):
    print("PHASE11_DIAGNOSTICS_FAILURE_STAGE_V1")
    if state is None:
        print("stage=before_state")
        print("error_type={}".format(type(error).__name__))
        return None
    context = state.context
    print("stage={}".format(context.failure_stage or "unknown"))
    print("error_type={}".format(type(error).__name__))
    gateway = context.gateway
    if gateway is not None:
        print(
            "gateway={} {} {} {}".format(
                sum(gateway.validated.values()),
                gateway.rejected,
                gateway.successful_starts,
                gateway.successful_stops,
            )
        )
    try:
        snapshot = state.server.snapshot()
        print(
            "http={} {} {} {} {}".format(
                snapshot.get("accepted"),
                snapshot.get("completed"),
                snapshot.get("client_count"),
                snapshot.get("parse_errors"),
                snapshot.get("faulted"),
            )
        )
    except BaseException:
        print("http=unavailable")
    return None


def run(
    confirmation,
    temporary_password,
    window_seconds=DEFAULT_WINDOW_SECONDS,
):
    """Run the one-phone diagnostics gate under exclusive WLAN ownership."""

    if confirmation != PHASE11_DIAGNOSTICS_CONFIRMATION:
        raise RuntimeError("exact Phase-11 diagnostics confirmation is required")
    window_seconds = _phase9._validate_window_seconds(window_seconds)
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
            print("PHASE11_DIAGNOSTICS_FAILURE_STAGE_V1")
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
    print(PHASE11_DIAGNOSTICS_FAIL_TOKEN)
    if isinstance(primary, (KeyboardInterrupt, SystemExit, MemoryError)):
        raise primary
    raise RuntimeError("Phase-11 diagnostics phone smoke failed") from None


__all__ = (
    "PHASE11_DIAGNOSTICS_CONFIRMATION",
    "PHASE11_DIAGNOSTICS_READY_TOKEN",
    "PHASE11_DIAGNOSTICS_PASS_TOKEN",
    "PHASE11_DIAGNOSTICS_FAIL_TOKEN",
    "run",
)
