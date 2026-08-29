"""Lazy Stage-1 AP/IPv4/TCP proof for the full REST phone smoke.

Importing this module is inert.  The explicitly armed ``run()`` first opens
only the production Wi-Fi path.  After the AP is ready it binds one tiny
production HTTP server and requires a complete fixed response to an exact
AP-subnet peer.  Only that IPv4/TCP proof may lazy-load the full product stage
in :mod:`tools.phase8_full_rest_phone_stage2`.
"""

import gc as _gc


FULL_REST_PHONE_AP_READY_TOKEN = "PHASE8_FULL_REST_PHONE_AP_READY_V1"
FULL_REST_PHONE_IP_READY_TOKEN = "PHASE8_FULL_REST_PHONE_IP_READY_V1"
FULL_REST_PHONE_CLIENT_TOKEN = "PHASE8_FULL_REST_PHONE_CLIENT_SEEN_V1"
FULL_REST_PHONE_IP_PASS_TOKEN = "PHASE8_FULL_REST_PHONE_IP_PASS_V1"

AP_SSID = "Landy Heater"
AP_IP = "192.168.4.1"
IP_CHECK_PORT = 8080
IP_CHECK_PATH = "/api/v1/phase8-link-check"
IP_CHECK_URL = "http://192.168.4.1:8080/api/v1/phase8-link-check"
MINIMUM_FREE_HEAP_BYTES = 32 * 1024
POLL_INTERVAL_MS = 25
AP_CHECK_INTERVAL_MS = 1000
STARTUP_TIMEOUT_MS = 15000

_NETWORK_FROZEN_ORIGINS = (
    ("app.network_manager", "app/network_manager.py"),
    ("hardware.micropython_wifi", "hardware/micropython_wifi.py"),
)
_HTTP_FROZEN_ORIGINS = (
    ("adapters.micropython_http_server", "adapters/micropython_http_server.py"),
    ("services.http_protocol", "services/http_protocol.py"),
    ("services.strict_json", "services/strict_json.py"),
)
_WOULD_BLOCK_ERRNOS = (11, 35, 10035)


def _bounded_accept_errno(error):
    try:
        arguments = getattr(error, "args", ())
        if not arguments:
            return -1
        value = arguments[0]
        if value in _WOULD_BLOCK_ERRNOS:
            return -1
        if type(value) is int and 0 <= value <= 65535:
            return value
    except BaseException:
        pass
    return -1


class _DiagnosticListener:
    """Delegate the real listener while retaining only safe accept errno."""

    __slots__ = ("_port", "_factory")

    def __init__(self, port, factory):
        self._port = port
        self._factory = factory

    def setblocking(self, value):
        return self._port.setblocking(value)

    def bind(self, address):
        return self._port.bind(address)

    def listen(self, backlog):
        return self._port.listen(backlog)

    def accept(self):
        try:
            return self._port.accept()
        except OSError as error:
            try:
                errno = _bounded_accept_errno(error)
                if errno >= 0 and self._factory.accept_errno < 0:
                    self._factory.accept_errno = errno
            except BaseException:
                pass
            raise

    def close(self):
        port = self._port
        if port is None:
            return None
        result = port.close()
        if result is not None:
            return result
        self._port = None
        self._factory._release(port)
        return None


class _DiagnosticSocketFactory:
    """Open the production listener without retaining request data."""

    __slots__ = (
        "accept_errno", "_raw_owner", "_orphan_port", "_listener"
    )

    def __init__(self):
        self.accept_errno = -1
        self._raw_owner = [None]
        self._orphan_port = None
        self._listener = None

    def _release(self, port):
        owner = self._raw_owner
        if type(owner) is list and len(owner) == 1 and owner[0] is port:
            owner[0] = None
        if self._orphan_port is port:
            self._orphan_port = None

    def close_retained(self):
        listener = self._listener
        if listener is not None:
            try:
                result = listener.close()
            except BaseException:
                return False
            if result is not None:
                return False
            self._listener = None
        owner = self._raw_owner
        if type(owner) is not list or len(owner) != 1:
            return False
        port = owner[0]
        if port is None:
            return True
        try:
            result = port.close()
        except BaseException:
            return False
        if result is not None:
            return False
        owner[0] = None
        if self._orphan_port is port:
            self._orphan_port = None
        return True

    def __call__(self):
        import socket as socket_module

        raw = None
        try:
            owner = self._raw_owner
            if (
                type(owner) is not list
                or len(owner) != 1
                or owner[0] is not None
            ):
                raise RuntimeError("Stage-1 raw listener owner is invalid")
            raw = socket_module.socket(
                socket_module.AF_INET,
                socket_module.SOCK_STREAM,
            )
            owner[0] = raw
            self._orphan_port = raw
            listener = _DiagnosticListener(raw, self)
            self._listener = listener
            return listener
        except BaseException:
            owned = False
            try:
                owned = raw is not None and self._raw_owner[0] is raw
            except BaseException:
                pass
            if owned:
                try:
                    self.close_retained()
                except BaseException:
                    pass
            elif raw is not None:
                closed = False
                try:
                    closed = raw.close() is None
                except BaseException:
                    pass
                if not closed:
                    try:
                        self._raw_owner[0] = raw
                    except BaseException:
                        pass
                    try:
                        self.close_retained()
                    except BaseException:
                        pass
            raise


class _Response:
    __slots__ = ("status", "body", "headers")

    def __init__(self, status, body, headers=None):
        self.status = status
        self.body = body
        self.headers = {} if headers is None else headers


class _IPCheckHandler:
    """Fixed read-only route proving one usable AP IPv4/TCP peer."""

    __slots__ = (
        "valid_requests",
        "rejected_requests",
        "responses_returned",
        "last_valid_peer_ip",
        "_ok",
        "_not_found",
        "_method_not_allowed",
    )

    def __init__(self):
        self.valid_requests = 0
        self.rejected_requests = 0
        self.responses_returned = 0
        self.last_valid_peer_ip = None
        self._ok = _Response(200, {
            "api_version": 1,
            "phase": 8,
            "ip_check": {
                "ap_peer_validated": True,
                "full_product_loaded": False,
                "result": "ok",
            },
        })
        self._not_found = _Response(404, {
            "api_version": 1,
            "error": {"code": "not_found", "message": "Not found"},
        })
        self._method_not_allowed = _Response(
            405,
            {
                "api_version": 1,
                "error": {
                    "code": "method_not_allowed",
                    "message": "Method not allowed",
                },
            },
            {"Allow": "GET"},
        )

    @staticmethod
    def _is_ap_peer(value):
        if type(value) is not str or not value or len(value) > 15:
            return False
        parts = value.split(".")
        if len(parts) != 4 or parts[:3] != ["192", "168", "4"]:
            return False
        final = parts[3]
        if not final or (len(final) > 1 and final[0] == "0"):
            return False
        number = 0
        for character in final:
            if character < "0" or character > "9":
                return False
            number = number * 10 + ord(character) - 48
        return 2 <= number <= 254

    def _return(self, response, rejected):
        self.responses_returned += 1
        if rejected:
            self.rejected_requests += 1
        return response

    def handle(self, request, peer_ip=None):
        exact = (
            getattr(request, "path", None) == IP_CHECK_PATH
            and getattr(request, "target", None) == IP_CHECK_PATH
            and getattr(request, "query", None) is None
        )
        if (
            not exact
            or getattr(request, "host", None) != AP_IP + ":8080"
            or not self._is_ap_peer(peer_ip)
        ):
            return self._return(self._not_found, True)
        if getattr(request, "method", None) != "GET":
            return self._return(self._method_not_allowed, True)
        if self.valid_requests != 0:
            return self._return(self._not_found, True)
        self.valid_requests = 1
        self.last_valid_peer_ip = peer_ip
        return self._return(self._ok, False)


def _require(condition, message):
    if not condition:
        raise RuntimeError("Phase-8 full REST phone smoke failed: {}".format(
            message
        ))


def _validate_password(value):
    if type(value) is not str:
        raise ValueError("temporary WPA2 password must be a string")
    try:
        encoded = value.encode("ascii")
    except (UnicodeError, ValueError):
        raise ValueError("temporary WPA2 password must be printable ASCII")
    if not 12 <= len(encoded) <= 63:
        raise ValueError("temporary WPA2 password must contain 12 to 63 bytes")
    for byte in encoded:
        if byte < 0x20 or byte > 0x7E:
            raise ValueError("temporary WPA2 password must be printable ASCII")
    return value


def _validate_window_seconds(value):
    if (
        type(value) is not int
        or not 60 <= value <= 300
    ):
        raise ValueError("observation window must be 60 to 300 seconds")
    return value


def _plain_ticks_ms():
    return 0


def _plain_ticks_add(value, delta):
    return value + delta


def _plain_ticks_diff(newer, older):
    return newer - older


def _memory_free():
    _gc.collect()
    reader = getattr(_gc, "mem_free", None)
    if not callable(reader):
        return None
    value = reader()
    _require(type(value) is int and value >= 0, "invalid heap reading")
    return value


def _require_heap(value, checkpoint):
    _require(
        type(value) is int and value >= MINIMUM_FREE_HEAP_BYTES,
        "free heap at {} is unavailable or below 32 KiB".format(checkpoint),
    )
    return value


def _load_wifi_runtime():
    """Load only the production Wi-Fi boundary before DHCP/IP proof."""

    import sys
    import time
    import board_config
    from app.network_manager import NetworkManager
    import hardware.micropython_wifi as wifi_module

    sleep_ms = getattr(time, "sleep_ms", None)
    if not callable(sleep_ms):
        sleep = getattr(time, "sleep", None)
        if callable(sleep):
            sleep_ms = lambda milliseconds: sleep(milliseconds / 1000.0)
    return (
        sys,
        board_config,
        NetworkManager,
        wifi_module,
        wifi_module.open_wifi_from_board_config,
        getattr(time, "ticks_ms", _plain_ticks_ms),
        getattr(time, "ticks_add", _plain_ticks_add),
        getattr(time, "ticks_diff", _plain_ticks_diff),
        sleep_ms,
    )


def _load_http_runtime():
    from adapters.micropython_http_server import MicroPythonHTTPServer

    return MicroPythonHTTPServer


def _verify_frozen_origins(sys_module, expected):
    if (
        type(sys_module.path) is not list
        or not sys_module.path
        or sys_module.path[0] != ".frozen"
    ):
        raise RuntimeError("frozen module path is not first")
    for module_name, origin in expected:
        module = sys_module.modules.get(module_name)
        actual = None if module is None else getattr(module, "__file__", None)
        if actual != origin or actual.startswith("/"):
            raise RuntimeError("required frozen module origin is invalid")
    return True


def _assert_redacted(password, *values):
    if password in repr(values):
        raise RuntimeError("Phase-8 full REST phone smoke leaked its key")


def _record_probe_snapshot(capsule, snapshot):
    """Persist only normalized scalars before any listener cleanup."""

    try:
        if type(snapshot) is not dict:
            return None
        for target, source in (
            ("stage1_http_started", "started"),
            ("stage1_http_closed", "closed"),
            ("stage1_http_faulted", "faulted"),
        ):
            value = snapshot.get(source)
            setattr(
                capsule,
                target,
                value if value is True or value is False else None,
            )
        for target, source in (
            ("stage1_http_clients", "client_count"),
            ("stage1_http_accepted", "accepted"),
            ("stage1_http_completed", "completed"),
            ("stage1_http_parse_errors", "parse_errors"),
            ("stage1_http_timeouts", "timeouts"),
            ("stage1_http_socket_errors", "socket_errors"),
            ("stage1_http_reentries", "reentries"),
        ):
            value = snapshot.get(source)
            setattr(
                capsule,
                target,
                value
                if type(value) is int and 0 <= value <= 1000000
                else -1,
            )
        last_error = snapshot.get("last_error")
        if last_error is None:
            last_error = "none"
        elif last_error not in ("accept_failed", "accept_contract_failed"):
            last_error = "other"
        capsule.stage1_http_last_error = last_error
    except BaseException:
        pass
    return None


def _sleep_checked(sleep_ms, milliseconds):
    result = sleep_ms(milliseconds)
    if result is not None:
        raise RuntimeError("sleep_ms returned a value")


def _cleanup_http_server(server):
    if server is None:
        return True, None
    first_error = None
    clean = False
    for _ in range(2):
        try:
            result = server.deinit()
            if result is not None and first_error is None:
                first_error = RuntimeError("IP probe deinit returned a value")
        except BaseException as error:
            if first_error is None:
                first_error = error
        try:
            snapshot = server.snapshot()
            clean = (
                type(snapshot) is dict
                and snapshot.get("closed") is True
                and snapshot.get("started") is False
                and snapshot.get("client_count") == 0
            )
        except BaseException as error:
            clean = False
            if first_error is None:
                first_error = error
        if clean:
            break
    return bool(clean and first_error is None), first_error


def _sanitized_raise(error):
    if isinstance(error, KeyboardInterrupt):
        raise KeyboardInterrupt() from None
    if isinstance(error, SystemExit):
        raise SystemExit() from None
    if isinstance(error, MemoryError):
        raise MemoryError() from None
    raise RuntimeError("Phase-8 full REST phone smoke failed") from None


def prepare(capsule, password, window_seconds):
    """Populate a preallocated owner capsule after a complete link proof."""

    sys_module = None
    support = None
    board_config = None
    wifi_module = None
    port = None
    network_module = None
    manager = None
    probe_server = None
    handler = None
    socket_factory = None
    cleanup_error = None
    cleanup_started = False
    failure_stage = "stage1_preflight"
    client_seen = False
    last_clients = -1
    last_action = "none"
    memory_before = _memory_free()
    memory_after_wifi_factory = None
    memory_after_ap_ready = None
    memory_after_ip_bind = None
    memory_after_ip_response = None
    ip_peer = None
    probe_completed = 0
    probe_rejected = 0

    try:
        support = capsule.support
        _require(support is not None, "radio safety support is unavailable")
        for name in (
            "_verify_hardware_locks",
            "_verify_platform",
            "_check_platform_ticks",
            "_load_network_module",
            "_interfaces_inactive",
            "_cleanup_radio",
        ):
            _require(
                callable(getattr(support, name, None)),
                "radio safety support contract is invalid",
            )
        (
            sys_module,
            board_config,
            NetworkManager,
            wifi_module,
            factory,
            ticks_ms,
            ticks_add,
            ticks_diff,
            sleep_ms,
        ) = _load_wifi_runtime()
        capsule.board_config = board_config
        capsule.wifi_module = wifi_module
        support._verify_hardware_locks(board_config)
        support._verify_platform(board_config)
        support._check_platform_ticks(ticks_ms, ticks_add, ticks_diff)
        _verify_frozen_origins(sys_module, _NETWORK_FROZEN_ORIGINS)
        _require_heap(_memory_free(), "Wi-Fi-only imports")

        board_config.WIFI_RADIO_APPROVED = True
        port = factory()
        capsule.port = port
        network_module = support._load_network_module()
        capsule.network_module = network_module
        _require(
            support._interfaces_inactive(network_module),
            "a WLAN interface was active before the phone test",
        )
        memory_after_wifi_factory = _require_heap(
            _memory_free(), "Wi-Fi factory construction"
        )
        live_configuration = {
            "hostname": "heater",
            "access_point": {"ssid": AP_SSID, "password": password},
            "known_networks": [],
        }
        manager = NetworkManager(
            port,
            live_configuration,
            ticks_add=ticks_add,
            ticks_diff=ticks_diff,
            ap_check_interval_ms=AP_CHECK_INTERVAL_MS,
        )
        capsule.network_manager = manager
        capsule.live_network_configuration = live_configuration
        capsule.ticks_ms = ticks_ms
        capsule.ticks_add = ticks_add
        capsule.ticks_diff = ticks_diff
        capsule.sleep_ms = sleep_ms
        failure_stage = "stage1_ap_startup"
        started_ms = ticks_ms()
        _require(manager.start(started_ms) is True, "network did not start")
        startup_deadline = ticks_add(started_ms, STARTUP_TIMEOUT_MS)
        while True:
            now_ms = ticks_ms()
            action = manager.step(now_ms)
            snapshot = manager.snapshot()
            events = manager.drain_events()
            _assert_redacted(password, action, snapshot, events)
            access_point = snapshot["access_point"]
            if access_point["active"] is True:
                _require(
                    access_point["ip"] == AP_IP
                    and access_point["clients"] == 0,
                    "access point readiness truth is invalid",
                )
                break
            if ticks_diff(now_ms, startup_deadline) >= 0:
                raise RuntimeError("access point startup timed out")
            _sleep_checked(sleep_ms, POLL_INTERVAL_MS)

        action = None
        snapshot = None
        events = None
        access_point = None
        memory_after_ap_ready = _require_heap(_memory_free(), "AP readiness")
        observation_deadline = ticks_add(ticks_ms(), window_seconds * 1000)
        capsule.observation_deadline = observation_deadline
        print(FULL_REST_PHONE_AP_READY_TOKEN)
        print("ssid={}".format(AP_SSID))

        MicroPythonHTTPServer = _load_http_runtime()
        _verify_frozen_origins(sys_module, _HTTP_FROZEN_ORIGINS)
        handler = _IPCheckHandler()
        socket_factory = _DiagnosticSocketFactory()
        capsule.stage1_socket_factory = socket_factory
        failure_stage = "stage1_http_bind"
        probe_server = MicroPythonHTTPServer(
            handler,
            AP_IP,
            port=IP_CHECK_PORT,
            socket_factory=socket_factory,
            request_handler=handler.handle,
            ticks_ms=ticks_ms,
            ticks_add=ticks_add,
            ticks_diff=ticks_diff,
        )
        capsule.stage1_server = probe_server
        _require(probe_server.start() is True, "IP probe server did not start")
        probe_snapshot = probe_server.snapshot()
        _assert_redacted(password, probe_snapshot)
        _record_probe_snapshot(capsule, probe_snapshot)
        _require(
            probe_snapshot["started"] is True
            and probe_snapshot["closed"] is False
            and probe_snapshot["client_count"] == 0,
            "IP probe listener truth is invalid",
        )
        memory_after_ip_bind = _require_heap(
            _memory_free(), "IP probe import and bind"
        )
        print(FULL_REST_PHONE_IP_READY_TOKEN)
        print("url={}".format(IP_CHECK_URL))
        print("window_seconds={}".format(window_seconds))
        print("Connect the phone with Automatic IP and open this exact URL.")

        while True:
            failure_stage = "stage1_observe_deadline"
            now_ms = ticks_ms()
            if ticks_diff(now_ms, observation_deadline) >= 0:
                raise RuntimeError("complete IP probe response was not observed")
            failure_stage = "stage1_observe_network_step"
            action = manager.step(now_ms)
            network_snapshot = manager.snapshot()
            events = manager.drain_events()
            _assert_redacted(password, action, network_snapshot, events)
            failure_stage = "stage1_observe_network_truth"
            access_point = network_snapshot["access_point"]
            _require(
                access_point["active"] is True
                and access_point["ip"] == AP_IP,
                "access point truth changed during IP proof",
            )
            if action is not None and action != "ap_checked":
                last_action = "other"
                raise RuntimeError("network changed state during IP proof")
            if action == "ap_checked":
                last_action = "ap_checked"
                clients = access_point["clients"]
                _require(
                    type(clients) is int and 0 <= clients <= 1,
                    "phone client count is invalid",
                )
                last_clients = clients
                if clients == 1 and not client_seen:
                    client_seen = True
                    print(FULL_REST_PHONE_CLIENT_TOKEN)
                    print("clients=1")
                elif clients == 0 and client_seen:
                    raise RuntimeError("phone disconnected during IP proof")
            else:
                last_action = "none"

            failure_stage = "stage1_observe_http_step"
            probe_server.step()
            probe_snapshot = probe_server.snapshot()
            _assert_redacted(password, probe_snapshot)
            _record_probe_snapshot(capsule, probe_snapshot)
            failure_stage = "stage1_observe_http_transport"
            _require(
                probe_snapshot["started"] is True
                and probe_snapshot["closed"] is False
                and probe_snapshot["faulted"] is False
                and probe_snapshot["parse_errors"] == 0
                and probe_snapshot["timeouts"] == 0
                and probe_snapshot["socket_errors"] == 0
                and probe_snapshot["reentries"] == 0,
                "IP probe transport result is unsafe",
            )
            completed = (
                handler.valid_requests == 1
                and handler.rejected_requests
                == handler.responses_returned - 1
                and probe_snapshot["client_count"] == 0
                and probe_snapshot["accepted"]
                == probe_snapshot["completed"]
                == handler.responses_returned
                and handler.responses_returned >= 1
            )
            if client_seen and completed:
                ip_peer = handler.last_valid_peer_ip
                probe_completed = probe_snapshot["completed"]
                probe_rejected = handler.rejected_requests
                break
            _sleep_checked(sleep_ms, POLL_INTERVAL_MS)

        network_snapshot = None
        events = None
        access_point = None
        probe_snapshot = None
        failure_stage = "stage1_post_response_heap"
        memory_after_ip_response = _require_heap(
            _memory_free(), "complete IP probe response"
        )
        failure_stage = "stage1_cleanup_http"
        cleanup_started = True
        probe_ok, cleanup_error = _cleanup_http_server(probe_server)
        factory_ok = socket_factory.close_retained()
        _require(
            probe_ok and cleanup_error is None and factory_ok,
            "IP probe cleanup failed",
        )
        probe_server = None
        capsule.stage1_server = None
        capsule.stage1_socket_factory = None
        capsule.stage1_cleanup_confirmed = True
        _gc.collect()
        failure_stage = "stage1_post_cleanup_heap"
        memory_after_ip_cleanup = _require_heap(
            _memory_free(), "IP probe cleanup"
        )
        failure_stage = "stage1_confirm_association"
        _require(
            port.access_point_status()
            == {"active": True, "ip": AP_IP, "clients": 1},
            "phone association changed after IP proof",
        )
        print(FULL_REST_PHONE_IP_PASS_TOKEN)
        print("peer={}".format(ip_peer))
        capsule.memory_before = memory_before
        capsule.memory_after_wifi_factory = memory_after_wifi_factory
        capsule.memory_after_ap_ready = memory_after_ap_ready
        capsule.memory_after_ip_bind = memory_after_ip_bind
        capsule.memory_after_ip_response = memory_after_ip_response
        capsule.memory_after_ip_cleanup = memory_after_ip_cleanup
        capsule.ip_peer = ip_peer
        capsule.probe_completed = probe_completed
        capsule.probe_rejected = probe_rejected
        capsule.stage1_failure_stage = None
        capsule.stage1_client_seen = False
        capsule.stage1_ap_clients = -1
        capsule.stage1_action = "none"
        capsule.stage1_http_started = None
        capsule.stage1_http_closed = None
        capsule.stage1_http_faulted = None
        capsule.stage1_http_clients = -1
        capsule.stage1_http_accepted = -1
        capsule.stage1_http_completed = -1
        capsule.stage1_http_parse_errors = -1
        capsule.stage1_http_timeouts = -1
        capsule.stage1_http_socket_errors = -1
        capsule.stage1_http_last_error = "none"
        capsule.stage1_http_reentries = -1
        capsule.stage1_accept_errno = -1
        capsule.stage1_handler_valid = -1
        capsule.stage1_handler_rejected = -1
        capsule.stage1_handler_responses = -1
        return None
    except BaseException:
        if capsule.port is None and port is not None:
            capsule.port = port
        if capsule.network_manager is None and manager is not None:
            capsule.network_manager = manager
        if capsule.network_module is None and network_module is not None:
            capsule.network_module = network_module
        if capsule.support is None and support is not None:
            capsule.support = support
        if capsule.board_config is None and board_config is not None:
            capsule.board_config = board_config
        if capsule.wifi_module is None and wifi_module is not None:
            capsule.wifi_module = wifi_module
        if (
            capsule.stage1_socket_factory is None
            and socket_factory is not None
        ):
            capsule.stage1_socket_factory = socket_factory
        if probe_server is not None:
            capsule.stage1_server = probe_server
        try:
            capsule.stage1_failure_stage = failure_stage
            capsule.stage1_client_seen = client_seen
            capsule.stage1_ap_clients = last_clients
            capsule.stage1_action = last_action
            capsule.stage1_accept_errno = (
                -1 if socket_factory is None else socket_factory.accept_errno
            )
            if handler is not None:
                capsule.stage1_handler_valid = handler.valid_requests
                capsule.stage1_handler_rejected = handler.rejected_requests
                capsule.stage1_handler_responses = handler.responses_returned
            if probe_server is not None and not cleanup_started:
                snapshot = probe_server.snapshot()
                _assert_redacted(password, snapshot)
                _record_probe_snapshot(capsule, snapshot)
        except BaseException:
            pass
        if probe_server is not None:
            try:
                _cleanup_http_server(probe_server)
            except BaseException:
                pass
        raise
