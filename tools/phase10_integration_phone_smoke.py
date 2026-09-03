"""Two-stage private Phase-7/8/9/10 phone integration gate.

The phone writes real Wi-Fi credentials directly to disposable target storage.
This runner never prints, returns, hashes, or otherwise exports those secrets.
``provision`` serves the Setup Assistant on the stable commissioning AP and
then powers both radios down while preserving only the isolated A/B records.
``exercise`` reloads those records, proves real STA DHCP plus AP re-authentication
with the newly stored AP key, and observes one exact create/edit/delete timer
sequence through the real web UI.  Product storage and every heater/peripheral
boundary remain untouched.
"""

import gc as _gc
import os as _os
import sys as _sys

from tools import phase8_full_rest_phone_smoke as _base
from tools import phase9_web_phone_smoke as _phase9
from tools import phase10_setup_phone_smoke as _phase10


INTEGRATION_PROVISION_CONFIRMATION = "PHASE10_INTEGRATION_PROVISION_CONFIRM_V1"
INTEGRATION_EXERCISE_CONFIRMATION = "PHASE10_INTEGRATION_EXERCISE_CONFIRM_V1"
INTEGRATION_AP_READY_TOKEN = "PHASE10_INTEGRATION_AP_READY_V1"
INTEGRATION_SETUP_READY_TOKEN = "PHASE10_INTEGRATION_SETUP_READY_V1"
INTEGRATION_PROVISIONED_TOKEN = "PHASE10_INTEGRATION_PROVISIONED_V1"
INTEGRATION_RECONFIGURED_AP_TOKEN = "PHASE10_INTEGRATION_RECONFIGURED_AP_READY_V1"
INTEGRATION_STA_CONNECTED_TOKEN = "PHASE10_INTEGRATION_STA_CONNECTED_V1"
INTEGRATION_PHONE_REAUTH_TOKEN = "PHASE10_INTEGRATION_PHONE_REAUTH_V1"
INTEGRATION_TIMER_READY_TOKEN = "PHASE10_INTEGRATION_TIMER_READY_V1"
INTEGRATION_TIMER_CREATED_TOKEN = "PHASE10_INTEGRATION_TIMER_CREATED_V1"
INTEGRATION_TIMER_UPDATED_TOKEN = "PHASE10_INTEGRATION_TIMER_UPDATED_V1"
INTEGRATION_TIMER_DELETED_TOKEN = "PHASE10_INTEGRATION_TIMER_DELETED_V1"
INTEGRATION_PASS_TOKEN = "PHASE10_INTEGRATION_PASS_V1"
INTEGRATION_FAIL_TOKEN = "PHASE10_INTEGRATION_FAIL_V1"

AP_IP = _phase9.AP_IP
ROOT_URL = _phase9.ROOT_URL
POLL_INTERVAL_MS = 25
STARTUP_TIMEOUT_MS = 60000
MINIMUM_FREE_HEAP_BYTES = 32 * 1024
MINIMUM_WINDOW_SECONDS = 180
MAXIMUM_WINDOW_SECONDS = 900
CONFIG_BASE_PATH = "/phase8_full_rest_phone_smoke_v1_config"
LEDGER_BASE_PATH = "/phase8_full_rest_phone_smoke_v1_ledger"
CONFIG_MAX_RECORD_BYTES = 12 * 1024
PRODUCTION_CONFIG_BASE_PATH = "/landy_heater_config"
PRODUCTION_LEDGER_BASE_PATH = "/landy_heater_scheduler"
STORAGE_SUFFIXES = (".a", ".b", ".tmp")

_INITIAL_AP_PASSWORD = "Phase7RadioOnly!92"
_EXPECTED_TIMER_CREATED = {
    "name": "Integrationstest",
    "enabled": False,
    "weekdays": [0],
    "start": "07:15",
    "mode": "power",
    "target_temperature": None,
    "power_level": 3,
    "runtime_minutes": 15,
}
_EXPECTED_TIMER_UPDATED = {
    "name": "Integrationstest bearbeitet",
    "enabled": False,
    "weekdays": [0],
    "start": "07:30",
    "mode": "power",
    "target_temperature": None,
    "power_level": 4,
    "runtime_minutes": 15,
}
_EXPECTED_TIMER_PARTIAL_EDIT = {
    "name": "Integrationstest bearbeitet",
    "enabled": False,
    "weekdays": [0],
    "start": "07:30",
    "mode": "power",
    "target_temperature": None,
    "power_level": 3,
    "runtime_minutes": 15,
}


def _require(condition, message):
    if not condition:
        raise RuntimeError(
            "Phase-10 integration phone smoke failed: {}".format(message)
        )


def _validate_window_seconds(value):
    if (
        type(value) is not int
        or value < MINIMUM_WINDOW_SECONDS
        or value > MAXIMUM_WINDOW_SECONDS
    ):
        raise ValueError("window_seconds is outside its bounded range")
    return value


def _paths(bases):
    return tuple(base + suffix for base in bases for suffix in STORAGE_SUFFIXES)


def _storage_paths():
    return _paths((CONFIG_BASE_PATH, LEDGER_BASE_PATH))


def _production_paths():
    return _paths((PRODUCTION_CONFIG_BASE_PATH, PRODUCTION_LEDGER_BASE_PATH))


def _missing_file(error):
    code = getattr(error, "errno", None)
    if code is None and getattr(error, "args", None):
        code = error.args[0]
    return code == 2


def _path_exists(path):
    try:
        _os.stat(path)
    except OSError as error:
        if _missing_file(error):
            return False
        raise
    return True


def _stat_signature(paths):
    result = []
    for path in paths:
        try:
            value = _os.stat(path)
        except OSError as error:
            if _missing_file(error):
                result.append(None)
                continue
            raise
        result.append(tuple(value))
    return tuple(result)


def _store_write_truth(manager):
    status = manager.status()
    config_store = status.get("config_store")
    ledger_store = status.get("ledger_store")
    _require(
        type(config_store) is dict and type(ledger_store) is dict,
        "storage status is malformed",
    )
    truth = (
        status.get("generation"),
        status.get("ledger_generation"),
        config_store.get("writes"),
        ledger_store.get("writes"),
    )
    _require(
        all(type(value) is int and value >= 0 for value in truth),
        "storage generation/write truth is malformed",
    )
    return truth


def _http_transport_healthy(snapshot):
    """Accept only fully accounted browser-cancelled sends as non-fatal."""

    try:
        faulted = snapshot["faulted"]
        parse_errors = snapshot["parse_errors"]
        socket_errors = snapshot["socket_errors"]
        accepted = snapshot["accepted"]
        completed = snapshot["completed"]
        clients = snapshot["client_count"]
        last_error = snapshot["last_error"]
    except (KeyError, TypeError):
        return False
    if (
        faulted is not False
        or type(parse_errors) is not int
        or parse_errors != 0
        or type(socket_errors) is not int
        or socket_errors < 0
        or type(accepted) is not int
        or type(completed) is not int
        or type(clients) is not int
        or min(accepted, completed, clients) < 0
        or completed + clients > accepted
    ):
        return False
    if socket_errors == 0:
        return True
    return (
        last_error == "client_send_failed"
        and socket_errors == accepted - completed - clients
    )


def _assert_storage_sealed():
    for base in (CONFIG_BASE_PATH, LEDGER_BASE_PATH):
        _require(_path_exists(base + ".a"), "A slot is absent")
        _require(_path_exists(base + ".b"), "B slot is absent")
        _require(not _path_exists(base + ".tmp"), "temporary slot remains")
    return True


def _assert_storage_absent():
    for path in _storage_paths():
        _require(not _path_exists(path), "an isolated smoke file remains")
    return True


class _NullProtocolPort:
    __slots__ = ("calls",)

    def __init__(self):
        self.calls = 0

    def _forbidden(self):
        self.calls += 1
        raise RuntimeError("heater protocol access is forbidden")

    def validate_inbound_frame(self, frame):
        return self._forbidden()

    def request_initialization(self):
        return self._forbidden()

    def request_status(self):
        return self._forbidden()

    def request_start(self, *arguments, **keywords):
        return self._forbidden()

    def request_shutdown(self):
        return self._forbidden()


def _valid_private_configuration(configuration, initial_password):
    try:
        network = configuration["network"]
        access_point_password = network["access_point"]["password"]
        profiles = network["known_networks"]
        profile = profiles[0]
        station_password = profile["password"]
        station_ssid = profile["ssid"]
    except (KeyError, IndexError, TypeError):
        return False
    return (
        configuration.get("system", {}).get("setup_complete") is True
        and type(access_point_password) is str
        and 8 <= len(access_point_password) <= 63
        and access_point_password != initial_password
        and type(profiles) is list
        and len(profiles) == 1
        and type(station_ssid) is str
        and bool(station_ssid)
        and type(station_password) is str
        and 8 <= len(station_password) <= 64
    )


class _PrivateSetupGateway(_phase10._SetupWebGateway):
    __slots__ = ()

    def __init__(self, runtime, controller, protocol_port, manager, live_password):
        super().__init__(
            runtime,
            controller,
            protocol_port,
            manager,
            live_password,
            "private-ap-value",
            "private-ssid-value",
            "private-station-value",
        )

    def _assert_redacted(self, response):
        _require(
            self._live_password not in repr(response),
            "the commissioning AP key leaked",
        )
        _require(
            not _phase10._contains_password(getattr(response, "body", None)),
            "a credential field leaked into a response",
        )
        return None

    def _valid_setup_commit(self, response):
        body = getattr(response, "body", None)
        if (
            getattr(response, "status", None) != 200
            or type(body) is not dict
            or body.get("api_version") != 1
            or body.get("changed") is not True
            or body.get("system", {}).get("setup_complete") is not True
            or _phase10._contains_password(body)
        ):
            return False
        return _valid_private_configuration(
            self.manager.snapshot()["configuration"], self._live_password
        )


def _prepare_private_setup_proof(context):
    _require(context.gateway is None, "setup proof gateway already exists")
    _require(context.socket_observer is None, "socket observer already exists")
    live_password = context.password
    _require(type(live_password) is str, "commissioning AP key is unavailable")
    context.gateway = _PrivateSetupGateway(
        context.rest_runtime,
        context.controller,
        context.protocol_port,
        context.config_manager,
        live_password,
    )
    context.socket_observer = _phase10._TransportObserver()
    context.password = None
    return None


def _continue_provision(capsule, state, seam, window_seconds):
    context = state.context
    gateway = context.gateway
    observer = context.socket_observer
    server = state.server
    core = context.core
    context.failure_stage = "integration_setup_ready"
    _require(state.proof_loaded is True, "late setup proof was not loaded")
    _require(server.started is True, "HTTP listener is not active")
    _require(
        capsule.port.access_point_status()
        == {"active": True, "ip": AP_IP, "clients": 1},
        "phone association changed before Setup Assistant",
    )
    print(INTEGRATION_SETUP_READY_TOKEN)
    print("url={}".format(ROOT_URL))
    print("window_seconds={}".format(window_seconds))
    print("Enter one real WPA2/WPA3-compatible WLAN and replace the AP key.")

    while gateway.successful_mutations != 1:
        now_ms = core.ticks_ms()
        if core.ticks_diff(now_ms, context.observation_deadline) >= 0:
            raise RuntimeError("private Setup Assistant submission timed out")
        context.failure_stage = "integration_setup_network"
        context.network_manager.step(now_ms)
        context.network_manager.drain_events()
        snapshot = context.network_manager.snapshot()
        _require(
            snapshot["access_point"]["active"] is True,
            "commissioning AP stopped during setup",
        )
        context.failure_stage = "integration_setup_http"
        server.step()
        server_snapshot = server.snapshot()
        if (
            server_snapshot["faulted"] is not False
            or server_snapshot["parse_errors"] != 0
            or server_snapshot["socket_errors"] != 0
        ):
            print(
                "PHASE10_INTEGRATION_HTTP_DIAGNOSTIC_V1 {} {} {} {} {} {}".format(
                    server_snapshot.get("last_error") or "none",
                    server_snapshot.get("accepted"),
                    server_snapshot.get("completed"),
                    server_snapshot.get("client_count"),
                    server_snapshot.get("parse_errors"),
                    server_snapshot.get("socket_errors"),
                )
            )
        _require(
            _http_transport_healthy(server_snapshot),
            "HTTP transport faulted during setup",
        )
        _require(gateway.rejected == 0, "an unexpected setup request was rejected")
        _require(context.controller.requested_on is False, "Requested State changed")
        _require(context.protocol_port.calls == 0, "heater protocol was accessed")
        core.sleep_ms(POLL_INTERVAL_MS)

    context.failure_stage = "integration_setup_commit"
    _require(gateway.mutation_attempts == 1, "setup was not submitted exactly once")
    _require(
        _phase10._committed_once(
            context.storage_write_baseline,
            _store_write_truth(context.config_manager),
        ),
        "setup was not committed exactly once",
    )
    _assert_storage_sealed()
    _require(
        _valid_private_configuration(
            context.config_manager.snapshot()["configuration"],
            _INITIAL_AP_PASSWORD,
        ),
        "private setup readback is invalid",
    )
    _require(
        _stat_signature(_production_paths())
        == context.production_stat_baseline,
        "production storage changed",
    )

    # Release every live owner, but deliberately retain the isolated A/B files
    # for the separately invoked restart/reload stage.
    context.failure_stage = "integration_setup_radio_off"
    context.storage_owned = False
    _require(seam.fallback_cleanup(capsule, state) is True, "cleanup failed")
    _require(
        capsule.support._interfaces_inactive(capsule.network_module),
        "a WLAN interface remained active",
    )
    _require(
        all(_path_exists(path)
            for path in _storage_paths() if not path.endswith(".tmp")),
        "isolated restart records are incomplete",
    )
    print(INTEGRATION_PROVISIONED_TOKEN)
    return {"phase": 10, "stage": "provisioned", "radios_active": False}


def provision(confirmation, window_seconds=600):
    if confirmation != INTEGRATION_PROVISION_CONFIRMATION:
        raise RuntimeError("exact integration provisioning confirmation is required")
    window_seconds = _validate_window_seconds(window_seconds)
    capsule = _base._OwnershipCapsule()
    capsule.support = _base
    stage1 = None
    seam = None
    prepare_module = None
    state = None
    primary = None
    try:
        _base._require_stage2_unloaded()
        stage1 = _base._load_stage1()
        stage1.prepare(capsule, _INITIAL_AP_PASSWORD, window_seconds)
        print(INTEGRATION_AP_READY_TOKEN)
        _base._unload_stage1(stage1)
        stage1 = None
        _gc.collect()
        seam = _base._load_stage2_seam()
        state = seam.Stage2State()
        seam._load_proof = lambda: _sys.modules[__name__]
        prepare_module = _base._load_stage2_prepare()
        prepare_module.prepare(
            capsule, state, _INITIAL_AP_PASSWORD, window_seconds
        )
        _base._unload_module(
            prepare_module,
            _base._STAGE2_PREPARE_MODULE,
            "phase8_full_rest_phone_stage2_prepare",
        )
        prepare_module = None
        state.context.memory_before_http_start = seam.require_heap(
            seam.memory_free(), seam.MINIMUM_PRE_BIND_HEAP_BYTES, "pre-bind"
        )
        _require(state.server.start() is True, "HTTP server did not start")
        return _continue_provision(capsule, state, seam, window_seconds)
    except BaseException as error:
        primary = error
        stage = "before_state" if state is None else state.context.failure_stage
        print("PHASE10_INTEGRATION_FAILURE_STAGE_V1")
        print("stage={}".format(stage or "unknown"))
        print("error_type={}".format(type(error).__name__))
        print("error={}".format(str(error)))
    finally:
        if stage1 is not None:
            try:
                _base._unload_stage1(stage1)
            except BaseException:
                pass
        if prepare_module is not None:
            try:
                _base._unload_module(
                    prepare_module,
                    _base._STAGE2_PREPARE_MODULE,
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
                _base._outer_cleanup(capsule)
            except BaseException:
                pass
    print(INTEGRATION_FAIL_TOKEN)
    if isinstance(primary, (KeyboardInterrupt, SystemExit, MemoryError)):
        raise primary
    raise RuntimeError("Phase-10 integration provisioning failed") from None


def prepare_proof(context):
    """Late-listener hook used by the existing bounded socket seam."""

    return _prepare_private_setup_proof(context)


def _new_manager():
    from adapters.config_file_store import AtomicJSONConfigStore
    from services.config_manager import ConfigManager

    manager = ConfigManager(
        AtomicJSONConfigStore(
            CONFIG_BASE_PATH,
            max_record_bytes=CONFIG_MAX_RECORD_BYTES,
        ),
        AtomicJSONConfigStore(LEDGER_BASE_PATH),
    )
    _require(manager.load() is True, "isolated configuration did not reload")
    _require(
        manager.load_scheduler_checkpoint() is True,
        "isolated scheduler ledger did not reload",
    )
    return manager


def _durable_timer(manager, timer_id):
    reloaded = _new_manager()
    timers = reloaded.snapshot()["configuration"]["timers"]
    matches = [timer for timer in timers if timer["id"] == timer_id]
    _require(len(matches) == 1, "timer did not survive isolated reload")
    return matches[0]


def _timer_matches(timer, expected, timer_id=None):
    if type(timer) is not dict:
        return False
    if timer_id is not None and timer.get("id") != timer_id:
        return False
    return all(timer.get(key) == value for key, value in expected.items())


class _TimerGateway:
    __slots__ = (
        "application", "controller", "protocol_port", "manager", "stage",
        "timer_id", "rejected", "transient", "last_status",
        "last_error_code", "last_peer",
    )

    def __init__(
        self,
        application,
        controller,
        protocol_port,
        manager,
        initial_stage=0,
        timer_id=None,
    ):
        if initial_stage not in (0, 1, 2):
            raise ValueError("initial timer stage is invalid")
        if initial_stage in (1, 2) and (
            type(timer_id) is not str or not timer_id
        ):
            raise ValueError("resume timer id is invalid")
        self.application = application
        self.controller = controller
        self.protocol_port = protocol_port
        self.manager = manager
        self.stage = initial_stage
        self.timer_id = timer_id
        self.rejected = 0
        self.transient = 0
        self.last_status = None
        self.last_error_code = None
        self.last_peer = None

    def clear_secret(self):
        return None

    def handle(self, request, peer_ip):
        method = getattr(request, "method", None)
        path = getattr(request, "path", None)
        is_timer_collection = path == "/api/v1/timers"
        is_timer_item = (
            type(path) is str and path.startswith("/api/v1/timers/~id/")
        )
        if method in ("POST", "PUT", "PATCH", "DELETE") and not (
            is_timer_collection or is_timer_item
        ):
            self.rejected += 1
            from app.web_application import WebResponse

            return WebResponse(404, b"", "text/plain; charset=utf-8")

        response = self.application.handle(request, peer_ip)
        body = getattr(response, "body", None)
        status = getattr(response, "status", None)
        if method in ("POST", "PUT", "DELETE") and (
            is_timer_collection or is_timer_item
        ):
            self.last_status = status
            error = None if type(body) is not dict else body.get("error")
            self.last_error_code = (
                error.get("code") if type(error) is dict else None
            )
        if method == "POST" and is_timer_collection:
            timer = None if type(body) is not dict else body.get("timer")
            if (
                self.stage == 0
                and status == 201
                and _timer_matches(timer, _EXPECTED_TIMER_CREATED)
            ):
                self.timer_id = timer.get("id")
                durable = _durable_timer(self.manager, self.timer_id)
                _require(
                    _timer_matches(durable, _EXPECTED_TIMER_CREATED, self.timer_id),
                    "created timer reload differs",
                )
                self.stage = 1
                self.last_peer = peer_ip
                print(INTEGRATION_TIMER_CREATED_TOKEN)
            elif status != 201:
                self.transient += 1
                print("PHASE10_INTEGRATION_TIMER_RETRY_V1")
                print("status={}".format(status))
                print("error_code={}".format(self.last_error_code or "none"))
            else:
                self.rejected += 1
        elif method == "PUT" and is_timer_item:
            timer = None if type(body) is not dict else body.get("timer")
            if (
                self.stage == 1
                and status == 200
                and _timer_matches(timer, _EXPECTED_TIMER_UPDATED, self.timer_id)
            ):
                durable = _durable_timer(self.manager, self.timer_id)
                _require(
                    _timer_matches(durable, _EXPECTED_TIMER_UPDATED, self.timer_id),
                    "updated timer reload differs",
                )
                self.stage = 2
                self.last_peer = peer_ip
                print(INTEGRATION_TIMER_UPDATED_TOKEN)
            elif status != 200:
                self.transient += 1
                print("PHASE10_INTEGRATION_TIMER_RETRY_V1")
                print("status={}".format(status))
                print("error_code={}".format(self.last_error_code or "none"))
            else:
                self.rejected += 1
        elif method == "DELETE" and is_timer_item:
            if (
                self.stage == 2
                and status == 200
                and type(body) is dict
                and body.get("deleted") is True
            ):
                reloaded = _new_manager()
                timers = reloaded.snapshot()["configuration"]["timers"]
                _require(
                    not any(timer["id"] == self.timer_id for timer in timers),
                    "deleted timer survived isolated reload",
                )
                self.stage = 3
                self.last_peer = peer_ip
                print(INTEGRATION_TIMER_DELETED_TOKEN)
            elif status != 200:
                self.transient += 1
                print("PHASE10_INTEGRATION_TIMER_RETRY_V1")
                print("status={}".format(status))
                print("error_code={}".format(self.last_error_code or "none"))
            else:
                self.rejected += 1
        _require(self.controller.requested_on is False, "Requested State changed")
        _require(self.protocol_port.calls == 0, "heater protocol was accessed")
        _require(
            not _phase10._contains_password(body),
            "a credential field leaked into an HTTP response",
        )
        return response


class _ServerRuntime:
    __slots__ = ("application", "_gateway")

    def __init__(self, gateway):
        self.application = gateway
        self._gateway = gateway

    def handle(self, request, peer_ip):
        return self._gateway.handle(request, peer_ip)


def _remove_isolated_files():
    clean = True
    for path in _storage_paths():
        try:
            _os.remove(path)
        except OSError as error:
            if getattr(error, "errno", None) != 2:
                clean = False
    try:
        _assert_storage_absent()
    except BaseException:
        clean = False
    return clean


def exercise(confirmation, window_seconds=900):
    if confirmation != INTEGRATION_EXERCISE_CONFIRMATION:
        raise RuntimeError("exact integration exercise confirmation is required")
    window_seconds = _validate_window_seconds(window_seconds)
    board_config = None
    network_module = None
    port = None
    network_manager = None
    rest_runtime = None
    server = None
    manager = None
    controller = None
    protocol_port = None
    gateway = None
    production_baseline = None
    private_network = None
    stage = "exercise_preflight"
    primary = None
    try:
        import time
        import board_config
        import hardware.micropython_wifi as wifi_module
        from app.configuration_bootstrap import build_configured_runtime
        from app.heater_controller import HeaterController
        from app.network_composition import build_configured_network
        from app.rest_composition import build_rest_http_server, build_rest_runtime
        from app.scheduler_controller_gateway import SchedulerControllerGateway
        from app.web_application import Phase9WebApplication

        ticks_ms = time.ticks_ms
        ticks_add = time.ticks_add
        ticks_diff = time.ticks_diff
        sleep_ms = time.sleep_ms
        _base._verify_hardware_locks(board_config)
        _base._verify_platform(board_config)
        network_module = _base._load_network_module()
        _require(
            _base._interfaces_inactive(network_module),
            "a WLAN interface was active before restart",
        )
        production_baseline = _stat_signature(_production_paths())
        manager = _new_manager()
        original_generation = manager.generation
        original_configuration = manager.snapshot()["configuration"]
        _require(
            _valid_private_configuration(
                original_configuration, _INITIAL_AP_PASSWORD
            ),
            "stored private network configuration is invalid",
        )
        private_network = manager.network_configuration_for_runtime()["network"]
        initial_timers = original_configuration["timers"]
        initial_timer_stage = 0
        initial_timer_id = None
        expected_timer_mutations = 3
        if initial_timers:
            _require(
                len(initial_timers) == 1,
                "isolated resume contains an unexpected timer count",
            )
            initial_timer = initial_timers[0]
            initial_timer_id = initial_timer.get("id")
            if _timer_matches(
                initial_timer, _EXPECTED_TIMER_UPDATED, initial_timer_id
            ):
                initial_timer_stage = 2
                expected_timer_mutations = 1
            else:
                _require(
                    _timer_matches(
                        initial_timer,
                        _EXPECTED_TIMER_PARTIAL_EDIT,
                        initial_timer_id,
                    ),
                    "isolated resume timer differs from an accepted edit state",
                )
                initial_timer_stage = 1
                expected_timer_mutations = 2
        configured_runtime = build_configured_runtime(
            manager, ticks_diff=ticks_diff, ticks_add=ticks_add
        )
        _require(configured_runtime.scheduler.armed is False, "scheduler was armed")

        stage = "exercise_network_start"
        board_config.WIFI_RADIO_APPROVED = True
        port = wifi_module.open_wifi_from_board_config()
        network_runtime = build_configured_network(
            manager, port, ticks_diff=ticks_diff, ticks_add=ticks_add
        )
        network_manager = network_runtime.manager
        _require(network_manager.start(ticks_ms()) is True, "network did not start")
        deadline = ticks_add(ticks_ms(), window_seconds * 1000)
        startup_deadline = ticks_add(ticks_ms(), STARTUP_TIMEOUT_MS)
        ap_announced = False
        sta_announced = False
        phone_announced = False
        while not (sta_announced and phone_announced):
            now_ms = ticks_ms()
            if ticks_diff(now_ms, deadline) >= 0:
                raise RuntimeError("network restart/re-authentication timed out")
            action = network_manager.step(now_ms)
            snapshot = network_manager.snapshot()
            network_manager.drain_events()
            _require(snapshot["faulted"] is False, "network manager faulted")
            ap = snapshot["access_point"]
            sta = snapshot["station"]
            if ap["active"] and not ap_announced:
                ap_announced = True
                print(INTEGRATION_RECONFIGURED_AP_TOKEN)
                print("ssid=Landy Heater")
                print("Use the new AP password entered in Setup Assistant.")
            if sta["connected"] and not sta_announced:
                _require(
                    sta["ip"] not in (None, "0.0.0.0")
                    and sta["gateway"] not in (None, "0.0.0.0"),
                    "STA connected without DHCP truth",
                )
                sta_announced = True
                print(INTEGRATION_STA_CONNECTED_TOKEN)
                print("sta_dhcp_confirmed=True")
            if ap.get("clients") == 1 and not phone_announced:
                phone_announced = True
                print(INTEGRATION_PHONE_REAUTH_TOKEN)
                print("ap_clients=1")
            if ticks_diff(now_ms, startup_deadline) >= 0 and not ap_announced:
                raise RuntimeError("reconfigured AP did not start")
            sleep_ms(POLL_INTERVAL_MS)

        stage = "exercise_http_start"
        protocol_port = _NullProtocolPort()
        controller = HeaterController(
            protocol_port,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
            maximum_runtime_minutes=original_configuration["heater"][
                "maximum_runtime_minutes"
            ],
            temperature_manager=configured_runtime.temperature_manager,
        )
        scheduler_gateway = SchedulerControllerGateway(
            configured_runtime.scheduler,
            controller,
            ticks_ms=ticks_ms,
            persistence=manager,
        )
        rest_runtime = build_rest_runtime(
            manager,
            configured_runtime,
            controller,
            scheduler_gateway,
            _os.urandom,
            (AP_IP,),
            "ap",
            configured_network_runtime=network_runtime,
            ticks_ms=ticks_ms,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
        )
        _require(rest_runtime.start() is True, "REST security did not start")
        web = Phase9WebApplication(rest_runtime)
        gateway = _TimerGateway(
            web,
            controller,
            protocol_port,
            manager,
            initial_stage=initial_timer_stage,
            timer_id=initial_timer_id,
        )
        server = build_rest_http_server(
            _ServerRuntime(gateway),
            AP_IP,
            ticks_ms=ticks_ms,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
        )
        _require(server.start() is True, "HTTP server did not start")
        print(INTEGRATION_TIMER_READY_TOKEN)
        print("url={}".format(ROOT_URL))
        if initial_timer_stage == 0:
            print("Create, edit and delete the exact inactive integration timer.")
        elif initial_timer_stage == 1:
            print("Resume the retained inactive timer with edit and delete.")
        else:
            print("Resume the retained edited inactive timer with delete.")

        stage = "exercise_timer_flow"
        completed_at = None
        while True:
            now_ms = ticks_ms()
            if ticks_diff(now_ms, deadline) >= 0:
                raise RuntimeError("timer integration flow timed out")
            network_manager.step(now_ms)
            network_manager.drain_events()
            snapshot = network_manager.snapshot()
            _require(
                snapshot["access_point"]["active"] is True
                and snapshot["station"]["connected"] is True,
                "AP or STA link was lost during timer flow",
            )
            server.step()
            server_snapshot = server.snapshot()
            _require(
                server_snapshot["faulted"] is False
                and server_snapshot["reentries"] == 0,
                "HTTP transport faulted during timer flow",
            )
            _require(gateway.rejected == 0, "an unexpected mutation was rejected")
            if gateway.stage == 3:
                if completed_at is None:
                    completed_at = now_ms
                if (
                    ticks_diff(now_ms, completed_at) >= 1500
                    and server_snapshot["client_count"] == 0
                ):
                    break
            sleep_ms(POLL_INTERVAL_MS)

        stage = "exercise_final_readback"
        final_manager = _new_manager()
        final_configuration = final_manager.snapshot()["configuration"]
        _require(final_configuration["timers"] == [], "timer cleanup is not durable")
        _require(
            final_configuration["network"] == private_network,
            "network credentials changed during timer flow",
        )
        _require(
            final_manager.generation
            == original_generation + expected_timer_mutations,
            "timer mutations did not advance the exact remaining generations",
        )
        _require(controller.requested_on is False, "Requested State changed")
        _require(controller.request_revision == 0, "Requested revision changed")
        _require(protocol_port.calls == 0, "heater protocol was accessed")
        _require(
            _stat_signature(_production_paths())
            == production_baseline,
            "production storage changed",
        )
        stage = "exercise_pass"
    except BaseException as error:
        primary = error
        print("PHASE10_INTEGRATION_FAILURE_STAGE_V1")
        print("stage={}".format(stage))
        print("error_type={}".format(type(error).__name__))
        print("error={}".format(str(error)))
    finally:
        if server is not None:
            try:
                server.deinit()
            except BaseException:
                pass
        if rest_runtime is not None:
            try:
                rest_runtime.deinit()
            except BaseException:
                pass
        radio_ok = True
        if network_manager is not None or port is not None:
            try:
                radio_ok = _base._cleanup_radio(
                    network_manager, port, network_module
                )
            except BaseException:
                radio_ok = False
        if board_config is not None:
            try:
                board_config.WIFI_RADIO_APPROVED = False
            except BaseException:
                radio_ok = False
        # Preserve the isolated A/B records after a failed exercise so a
        # transport-only retry never forces the user to re-enter private Wi-Fi
        # credentials.  A complete PASS still removes every owned test file.
        files_ok = True if primary is not None else _remove_isolated_files()
        if primary is None:
            _require(radio_ok is True, "radio cleanup failed")
            _require(files_ok is True, "isolated storage cleanup failed")
            _require(
                _base._interfaces_inactive(network_module),
                "a WLAN interface remained active after PASS",
            )
    if primary is not None:
        print("isolated_retry_state_retained=True")
        print(INTEGRATION_FAIL_TOKEN)
        if isinstance(primary, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise primary
        raise RuntimeError("Phase-10 integration exercise failed") from None
    print("sta_dhcp_confirmed=True")
    print("ap_reauthentication_confirmed=True")
    print("timer_create_reload_confirmed=True")
    print("timer_update_reload_confirmed=True")
    print("timer_delete_reload_confirmed=True")
    print("production_storage_unchanged=True")
    print("radio_cleanup_confirmed=True")
    print(INTEGRATION_PASS_TOKEN)
    return {"phase": 10, "stage": "complete", "timer_mutations": 3}
