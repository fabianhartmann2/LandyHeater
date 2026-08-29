"""Explicit, bounded phone HTTP smoke for the Phase-8 recovery AP.

Importing this module is inert.  :func:`run` is the only entry point which
opens anything.  After exact confirmation it temporarily opens the production
Wi-Fi factory/NetworkManager path and one production MicroPython HTTP server
bound to the direct AP address.  The local handler exposes one fixed read-only
truth document; it has no reference to heater, UART, protocol or sensor code.

The server is terminally closed before the production Wi-Fi owners are closed.
A pass token is printed only after the response was completely written, one AP
client was freshly observed, all sockets and both WLAN interfaces are closed,
the singleton Wi-Fi lease and approval are released, and heap truth is read.
"""

import gc as _gc


PHONE_HTTP_CONFIRMATION = "PHASE8_PHONE_HTTP_CONFIRM_V1"
PHONE_HTTP_READY_TOKEN = "PHASE8_PHONE_HTTP_READY_V1"
PHONE_HTTP_CLIENT_TOKEN = "PHASE8_PHONE_HTTP_CLIENT_SEEN_V1"
PHONE_HTTP_PASS_TOKEN = "PHASE8_PHONE_HTTP_SMOKE_PASS_V1"
PHONE_HTTP_FAIL_TOKEN = "PHASE8_PHONE_HTTP_SMOKE_FAIL_V1"

AP_SSID = "Landy Heater"
AP_IP = "192.168.4.1"
CHECK_PATH = "/api/v1/phase8-radio-check"
CHECK_URL = "http://192.168.4.1/api/v1/phase8-radio-check"

MINIMUM_PASSWORD_BYTES = 12
MAXIMUM_PASSWORD_BYTES = 63
MINIMUM_WINDOW_SECONDS = 60
MAXIMUM_WINDOW_SECONDS = 300
DEFAULT_WINDOW_SECONDS = 180
MINIMUM_FREE_HEAP_BYTES = 32 * 1024
POLL_INTERVAL_MS = 25
AP_CHECK_INTERVAL_MS = 1000
STARTUP_TIMEOUT_MS = 15000


class _Response:
    __slots__ = ("status", "body", "headers")

    def __init__(self, status, body, headers=None):
        self.status = status
        self.body = body
        self.headers = {} if headers is None else headers


class _Phase8RadioCheckHandler:
    """Tiny allowlisted handler used only by this explicit smoke."""

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
            "radio_check": {
                "ap_peer_validated": True,
                "heater_control_enabled": False,
                "uart_enabled": False,
                "sensor_buses_enabled": False,
                "result": "ok",
            },
        })
        self._not_found = _Response(404, {
            "api_version": 1,
            "error": {
                "code": "not_found",
                "message": "Not found",
            },
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
        """Return fixed JSON only; never reflect a header, peer or secret."""

        path_allowed = (
            getattr(request, "path", None) == CHECK_PATH
            and getattr(request, "target", None) == CHECK_PATH
            and getattr(request, "query", None) is None
        )
        host_allowed = getattr(request, "host", None) == AP_IP
        peer_allowed = self._is_ap_peer(peer_ip)
        if not path_allowed or not host_allowed or not peer_allowed:
            return self._return(self._not_found, True)
        if getattr(request, "method", None) != "GET":
            return self._return(self._method_not_allowed, True)

        self.valid_requests += 1
        self.last_valid_peer_ip = peer_ip
        return self._return(self._ok, False)


def _require(condition, message):
    if not condition:
        raise RuntimeError("Phase-8 phone HTTP smoke failed: {}".format(message))


def _validate_password(value):
    if type(value) is not str:
        raise ValueError("temporary WPA2 password must be a string")
    try:
        encoded = value.encode("ascii")
    except (UnicodeError, ValueError):
        raise ValueError("temporary WPA2 password must be printable ASCII")
    if not MINIMUM_PASSWORD_BYTES <= len(encoded) <= MAXIMUM_PASSWORD_BYTES:
        raise ValueError("temporary WPA2 password must contain 12 to 63 bytes")
    for byte in encoded:
        if byte < 0x20 or byte > 0x7E:
            raise ValueError("temporary WPA2 password must be printable ASCII")
    return value


def _validate_window_seconds(value):
    if (
        type(value) is not int
        or not MINIMUM_WINDOW_SECONDS <= value <= MAXIMUM_WINDOW_SECONDS
    ):
        raise ValueError("observation window must be 60 to 300 seconds")
    return value


def _memory_free():
    _gc.collect()
    reader = getattr(_gc, "mem_free", None)
    if not callable(reader):
        return None
    value = reader()
    _require(type(value) is int and value >= 0, "invalid heap reading")
    return value


def _plain_ticks_ms():
    return 0


def _plain_ticks_add(value, delta):
    return value + delta


def _plain_ticks_diff(newer, older):
    return newer - older


def _load_wifi_runtime():
    """Load only the production Wi-Fi boundary after explicit arming."""

    import tools.phase7_network_smoke as support
    import board_config
    from app.network_manager import NetworkManager
    import hardware.micropython_wifi as wifi_module
    import time

    sleep_ms = getattr(time, "sleep_ms", None)
    if not callable(sleep_ms):
        sleep = getattr(time, "sleep", None)
        if callable(sleep):
            sleep_ms = lambda milliseconds: sleep(milliseconds / 1000.0)
    return (
        support,
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
    """Load the HTTP stack only after the AP owns its native allocations."""

    from adapters.micropython_http_server import MicroPythonHTTPServer

    return MicroPythonHTTPServer, None


def _assert_redacted(password, *values):
    if password in repr(values):
        raise RuntimeError("Phase-8 phone HTTP smoke leaked its temporary key")


def _sanitized_raise(error):
    if isinstance(error, KeyboardInterrupt):
        raise KeyboardInterrupt() from None
    if isinstance(error, SystemExit):
        raise SystemExit() from None
    if isinstance(error, MemoryError):
        raise MemoryError() from None
    raise RuntimeError("Phase-8 phone HTTP smoke failed") from None


def _sleep_checked(sleep_ms, milliseconds):
    result = sleep_ms(milliseconds)
    if result is not None:
        raise RuntimeError("sleep_ms returned a value")


def _cleanup_http_server(server):
    """Terminally close the HTTP owner, retaining any cleanup failure."""

    if server is None:
        return True, None
    first_error = None
    clean = False
    for _ in range(2):
        try:
            result = server.deinit()
            if result is not None and first_error is None:
                first_error = RuntimeError("HTTP deinit returned a value")
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


def _wifi_lease_released(wifi_module):
    return (
        wifi_module is not None
        and getattr(wifi_module, "_WIFI_LEASED", None) is False
        and getattr(wifi_module, "_WIFI_LEASE_POISONED", None) is False
    )


def run(
    confirmation,
    temporary_password,
    window_seconds=DEFAULT_WINDOW_SECONDS,
):
    """Serve one validated phone request, then prove complete cleanup."""

    if type(confirmation) is not str or confirmation != PHONE_HTTP_CONFIRMATION:
        raise RuntimeError("exact Phase-8 phone HTTP confirmation is required")
    password = _validate_password(temporary_password)
    window_seconds = _validate_window_seconds(window_seconds)

    memory_before = _memory_free()
    (
        support,
        board_config,
        NetworkManager,
        wifi_module,
        factory,
        ticks_ms,
        ticks_add,
        ticks_diff,
        sleep_ms,
    ) = _load_wifi_runtime()
    support._verify_hardware_locks(board_config)
    support._verify_platform(board_config)
    for function in (ticks_ms, ticks_add, ticks_diff, sleep_ms):
        _require(callable(function), "required MicroPython timing API is missing")
    support._check_platform_ticks(ticks_ms, ticks_add, ticks_diff)
    memory_after_wifi_import = _memory_free()
    _require(
        type(memory_before) is int
        and type(memory_after_wifi_import) is int
        and memory_before >= MINIMUM_FREE_HEAP_BYTES
        and memory_after_wifi_import >= MINIMUM_FREE_HEAP_BYTES,
        "free heap is unavailable or below 32 KiB",
    )

    port = None
    manager = None
    network_module = None
    server = None
    handler = None
    primary = None
    server_cleanup_error = None
    server_cleanup_ok = False
    radio_cleanup_ok = False
    ap_client_confirmed = False
    response_completed = False
    completed_responses = 0
    rejected_requests = 0
    valid_peer_ip = None
    memory_after_ap_ready = None
    memory_after_http_import = None
    try:
        board_config.WIFI_RADIO_APPROVED = True
        port = factory()
        network_module = support._load_network_module()
        _require(
            support._interfaces_inactive(network_module),
            "a WLAN interface was active before the phone test",
        )
        configuration = {
            "hostname": "heater",
            "access_point": {
                "ssid": AP_SSID,
                "password": password,
            },
            "known_networks": [],
        }
        manager = NetworkManager(
            port,
            configuration,
            ticks_add=ticks_add,
            ticks_diff=ticks_diff,
            ap_check_interval_ms=AP_CHECK_INTERVAL_MS,
        )
        configuration = None
        started_ms = ticks_ms()
        _require(manager.start(started_ms) is True, "manager did not start")
        startup_deadline = ticks_add(started_ms, STARTUP_TIMEOUT_MS)

        ap_ready = False
        while not ap_ready:
            now_ms = ticks_ms()
            action = manager.step(now_ms)
            snapshot = manager.snapshot()
            events = manager.drain_events()
            _assert_redacted(password, action, snapshot, events)
            access_point = snapshot["access_point"]
            if access_point["active"] is True:
                _require(
                    access_point["ip"] == AP_IP,
                    "access point did not use 192.168.4.1",
                )
                _require(
                    access_point["clients"] == 0,
                    "an AP client was present before the test became ready",
                )
                ap_ready = True
                break
            if ticks_diff(now_ms, startup_deadline) >= 0:
                raise RuntimeError("access point startup timed out")
            _sleep_checked(sleep_ms, POLL_INTERVAL_MS)

        # Release startup snapshots before loading the HTTP bytecode.  The
        # ESP32 Wi-Fi driver must obtain its larger native allocations first;
        # importing parser/JSON/server modules ahead of this point can leave
        # enough total bytes but no viable AP allocation.
        action = None
        snapshot = None
        events = None
        access_point = None
        memory_after_ap_ready = _memory_free()
        _require(
            type(memory_after_ap_ready) is int
            and memory_after_ap_ready >= MINIMUM_FREE_HEAP_BYTES,
            "free heap after AP startup is below 32 KiB",
        )
        MicroPythonHTTPServer, socket_factory = _load_http_runtime()
        memory_after_http_import = _memory_free()
        _require(
            type(memory_after_http_import) is int
            and memory_after_http_import >= MINIMUM_FREE_HEAP_BYTES,
            "free heap after HTTP import is below 32 KiB",
        )
        handler = _Phase8RadioCheckHandler()
        server = MicroPythonHTTPServer(
            handler,
            AP_IP,
            socket_factory=socket_factory,
            request_handler=handler.handle,
            ticks_ms=ticks_ms,
            ticks_add=ticks_add,
            ticks_diff=ticks_diff,
        )
        _require(server.start() is True, "HTTP server did not start")
        server_snapshot = server.snapshot()
        _require(
            server_snapshot["started"] is True
            and server_snapshot["closed"] is False
            and server_snapshot["client_count"] == 0,
            "HTTP listener truth is invalid",
        )

        print(PHONE_HTTP_READY_TOKEN)
        print("ssid={}".format(AP_SSID))
        print("url={}".format(CHECK_URL))
        print("window_seconds={}".format(window_seconds))
        print("Connect the phone and open the exact URL now.")

        observation_deadline = ticks_add(ticks_ms(), window_seconds * 1000)
        while True:
            now_ms = ticks_ms()
            action = manager.step(now_ms)
            network_snapshot = manager.snapshot()
            events = manager.drain_events()
            _assert_redacted(password, action, network_snapshot, events)
            access_point = network_snapshot["access_point"]
            _require(
                access_point["active"] is True
                and access_point["ip"] == AP_IP,
                "access point truth changed during HTTP observation",
            )
            if action is not None and action != "ap_checked":
                raise RuntimeError(
                    "access point changed state during HTTP observation"
                )
            if action == "ap_checked":
                clients = access_point["clients"]
                _require(
                    type(clients) is int and 0 <= clients <= 4,
                    "access point client count is invalid",
                )
                if clients > 1:
                    raise RuntimeError("more than one AP client was detected")
                if clients == 1 and not ap_client_confirmed:
                    ap_client_confirmed = True
                    print(PHONE_HTTP_CLIENT_TOKEN)
                    print("clients=1")

            server.step()
            server_snapshot = server.snapshot()
            _assert_redacted(password, server_snapshot)
            _require(
                server_snapshot["started"] is True
                and server_snapshot["closed"] is False,
                "HTTP server stopped during observation",
            )
            _require(
                server_snapshot["faulted"] is False
                and server_snapshot["parse_errors"] == 0
                and server_snapshot["timeouts"] == 0
                and server_snapshot["socket_errors"] == 0,
                "HTTP server reported an unsafe transport result",
            )
            if handler.valid_requests > 1:
                raise RuntimeError("more than one valid HTTP request was seen")

            # With no live client and no parser/socket failures, equality
            # proves that every response returned by the handler was fully
            # written.  It therefore includes the one allowlisted response.
            response_completed = (
                handler.valid_requests == 1
                and server_snapshot["client_count"] == 0
                and server_snapshot["completed"] == handler.responses_returned
                and handler.responses_returned >= 1
            )
            if ap_client_confirmed and response_completed:
                completed_responses = server_snapshot["completed"]
                rejected_requests = handler.rejected_requests
                valid_peer_ip = handler.last_valid_peer_ip
                break
            if ticks_diff(now_ms, observation_deadline) >= 0:
                raise RuntimeError(
                    "phone client and completed HTTP response were not observed"
                )
            _sleep_checked(sleep_ms, POLL_INTERVAL_MS)
    except BaseException as error:
        primary = error
    finally:
        # Ownership order is binding: sockets first, then WLAN manager/port,
        # then the temporary approval is revoked even for BaseException/OOM.
        server_cleanup_ok, server_cleanup_error = _cleanup_http_server(server)
        if network_module is None and port is not None:
            try:
                network_module = support._load_network_module()
            except BaseException:
                network_module = None
        try:
            radio_cleanup_ok = support._cleanup_radio(
                manager, port, network_module
            )
        except BaseException as error:
            radio_cleanup_ok = False
            if server_cleanup_error is None:
                server_cleanup_error = error
        board_config.WIFI_RADIO_APPROVED = False

    if primary is not None:
        print(PHONE_HTTP_FAIL_TOKEN)
        _sanitized_raise(primary)
    if server_cleanup_error is not None:
        print(PHONE_HTTP_FAIL_TOKEN)
        _sanitized_raise(server_cleanup_error)
    if not server_cleanup_ok or not radio_cleanup_ok:
        print(PHONE_HTTP_FAIL_TOKEN)
        raise RuntimeError(
            "Phase-8 phone HTTP smoke failed: production cleanup was not confirmed"
        )
    if not (
        support._interfaces_inactive(network_module)
        and getattr(port, "cleanup_complete", False) is True
        and _wifi_lease_released(wifi_module)
        and board_config.WIFI_RADIO_APPROVED is False
    ):
        print(PHONE_HTTP_FAIL_TOKEN)
        raise RuntimeError(
            "Phase-8 phone HTTP smoke failed: WLAN ownership remained active"
        )

    memory_after_cleanup = _memory_free()
    if not (
        type(memory_after_cleanup) is int
        and memory_after_cleanup >= MINIMUM_FREE_HEAP_BYTES
    ):
        print(PHONE_HTTP_FAIL_TOKEN)
        raise RuntimeError(
            "Phase-8 phone HTTP smoke failed: free heap after cleanup is too low"
        )

    result = {
        "phase": 8,
        "scope": "manual_phone_http_ap",
        "ssid": AP_SSID,
        "ap_ip": AP_IP,
        "url": CHECK_URL,
        "clients_confirmed": 1 if ap_client_confirmed else 0,
        "valid_requests": 1,
        "valid_peer_ip": valid_peer_ip,
        "completed_responses": completed_responses,
        "rejected_requests": rejected_requests,
        "response_completed": bool(response_completed),
        "window_seconds": window_seconds,
        "http_cleanup_confirmed": True,
        "radio_cleanup_confirmed": True,
        "interfaces_inactive": True,
        "lease_released": True,
        "approval_restored": True,
        "memory_before": memory_before,
        "memory_after_import": memory_after_wifi_import,
        "memory_after_wifi_import": memory_after_wifi_import,
        "memory_after_ap_ready": memory_after_ap_ready,
        "memory_after_http_import": memory_after_http_import,
        "memory_after_cleanup": memory_after_cleanup,
    }
    _assert_redacted(password, result)
    print("http_response_completed=True")
    print("http_cleanup_confirmed=True")
    print("radio_cleanup_confirmed=True")
    print(PHONE_HTTP_PASS_TOKEN)
    return result
