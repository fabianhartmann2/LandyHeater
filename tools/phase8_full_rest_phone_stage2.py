"""Late-loaded full-product stage for the Phase-8 phone REST smoke.

Importing this module is inert.  ``run()`` is the only entry point that reads
or writes the isolated smoke files, opens Wi-Fi or binds a socket.  The runner
uses the production configuration A/B store, ConfigManager, configuration and
network bootstraps, RestApplication composition, NetworkManager/Wi-Fi port and
MicroPythonHTTPServer in one target lifetime.

Only the real ``GET /api/v1/status`` route is forwarded to RestApplication.
The real security lifecycle obtains one system-random CSRF token so its heap is
represented, but the outer smoke gate rejects every mutation/security route
and never exposes that token.  HeaterController stays cold over a protocol
tripwire; no UART, heater transport, I2C or 1-Wire hardware owner is opened.
"""

import gc as _gc
import os as _os


FULL_REST_PHONE_READY_TOKEN = "PHASE8_FULL_REST_PHONE_READY_V1"
FULL_REST_PHONE_PASS_TOKEN = "PHASE8_FULL_REST_PHONE_SMOKE_PASS_V1"
FULL_REST_PHONE_FAIL_TOKEN = "PHASE8_FULL_REST_PHONE_SMOKE_FAIL_V1"
FULL_REST_PHONE_FAILURE_STAGE_TOKEN = (
    "PHASE8_FULL_REST_PHONE_FAILURE_STAGE_V1"
)

AP_SSID = "Landy Heater"
AP_IP = "192.168.4.1"
STATUS_PATH = "/api/v1/status"
STATUS_URL = "http://192.168.4.1/api/v1/status"

CONFIG_BASE_PATH = "/phase8_full_rest_phone_smoke_v1_config"
LEDGER_BASE_PATH = "/phase8_full_rest_phone_smoke_v1_ledger"
STORAGE_SUFFIXES = (".a", ".b", ".tmp")
PRODUCTION_CONFIG_BASE_PATH = "/landy_heater_config"
PRODUCTION_LEDGER_BASE_PATH = "/landy_heater_scheduler"

MINIMUM_FREE_HEAP_BYTES = 32 * 1024
POLL_INTERVAL_MS = 25

_STATUS_PROOF_HEADER_NAME = "X-Landy-Phase8-Status-Proof"
_STATUS_PROOF_HEADER_VALUE = "validated-v1"
_STATUS_PROOF_HEADER_LINE = (
    b"X-Landy-Phase8-Status-Proof: validated-v1\r\n"
)
_MAX_OBSERVED_RESPONSE_BODY_BYTES = 8192
_TARGET_RESPONSE_PREFIX = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: application/json; charset=utf-8\r\n"
    b"Content-Length: "
)
_TARGET_RESPONSE_SUFFIX = (
    b"\nConnection: close\r\n"
    b"Cache-Control: no-store\r\n"
    b"X-Content-Type-Options: nosniff\r\n"
    + _STATUS_PROOF_HEADER_LINE
    + b"\r\n"
)


class _Response:
    __slots__ = ("status", "body", "headers")

    def __init__(self, status, body, headers=None):
        self.status = status
        self.body = body
        self.headers = {} if headers is None else headers


class _ObservedClientSocket:
    """Track the exact canonical target wire without allocating a buffer."""

    __slots__ = (
        "_observer", "_port", "_leased", "_phase", "_index",
        "_content_length", "_length_digits",
    )

    def __init__(self, observer):
        self._observer = observer
        self._port = None
        self._leased = False
        self._reset_response()

    def _reset_response(self):
        self._phase = 1
        self._index = 0
        self._content_length = None
        self._length_digits = 0

    def _claim(self, port):
        if self._leased or self._port is not None:
            self._observer._mark_fault()
            raise RuntimeError("observed client slot is already leased")
        self._reset_response()
        self._port = port
        self._leased = True
        return self

    def _release(self):
        self._port = None
        self._leased = False
        self._reset_response()

    @property
    def leased(self):
        return self._leased

    def setblocking(self, value):
        if not self._observer._begin_operation():
            raise RuntimeError("observed socket operation reentered")
        try:
            setter = getattr(self._port, "setblocking", None)
            if callable(setter):
                return setter(value)
            setter = getattr(self._port, "settimeout", None)
            if not callable(setter):
                raise AttributeError("accepted socket has no nonblocking API")
            if value is False:
                return setter(0)
            if value is True:
                return setter(None)
            raise ValueError("setblocking requires a boolean")
        finally:
            self._observer._end_operation()

    def recv(self, maximum):
        if not self._observer._begin_operation():
            raise RuntimeError("observed socket operation reentered")
        try:
            return self._port.recv(maximum)
        finally:
            self._observer._end_operation()

    def _observe_sent(self, payload, sent):
        for offset in range(sent):
            value = payload[offset]
            phase = self._phase
            if phase == 0:
                break
            if phase == 1:
                if value != _TARGET_RESPONSE_PREFIX[self._index]:
                    self._phase = 0
                    break
                self._index += 1
                if self._index == len(_TARGET_RESPONSE_PREFIX):
                    self._phase = 2
                    self._index = 0
                    self._content_length = 0
            elif phase == 2:
                if 48 <= value <= 57:
                    if (
                        self._length_digits >= 4
                        or (
                            self._length_digits > 0
                            and self._content_length == 0
                        )
                    ):
                        self._phase = 0
                        break
                    self._content_length = (
                        self._content_length * 10 + value - 48
                    )
                    self._length_digits += 1
                    if self._content_length > _MAX_OBSERVED_RESPONSE_BODY_BYTES:
                        self._phase = 0
                        break
                elif value == 13 and self._length_digits:
                    self._phase = 3
                    self._index = 0
                else:
                    self._phase = 0
                    break
            elif phase == 3:
                if value != _TARGET_RESPONSE_SUFFIX[self._index]:
                    self._phase = 0
                    break
                self._index += 1
                if self._index == len(_TARGET_RESPONSE_SUFFIX):
                    self._observer._header_observed()
                    self._phase = 4
                    self._index = 0
                    if self._content_length == 0:
                        self._phase = 5
                        self._observer._wire_completed()
            elif phase == 4:
                self._index += 1
                if self._index == self._content_length:
                    self._phase = 5
                    self._observer._wire_completed()
            else:
                self._observer._mark_fault()
                break

    def send(self, payload):
        if not self._observer._begin_operation():
            raise RuntimeError("observed socket operation reentered")
        try:
            result = self._port.send(payload)
            try:
                if type(result) is int and 0 < result <= len(payload):
                    self._observe_sent(payload, result)
            except (MemoryError, KeyboardInterrupt, SystemExit):
                self._observer._mark_fault()
                raise
            except Exception:
                self._observer._mark_fault()
                raise
            return result
        finally:
            self._observer._end_operation()

    def _close_unguarded(self):
        if not self._leased:
            return None
        result = self._port.close()
        if result is None:
            self._observer._client_closed(self._phase >= 4, self._phase == 5)
            self._release()
        return result

    def close(self):
        if not self._observer._begin_operation():
            raise RuntimeError("observed socket operation reentered")
        try:
            return self._close_unguarded()
        finally:
            self._observer._end_operation()


class _SocketResponseObserver:
    __slots__ = (
        "clients", "faulted", "accepted", "closed", "target_headers",
        "target_wires", "target_completions", "target_failures",
        "operation_active", "reentries",
    )

    def __init__(self):
        self.clients = (
            _ObservedClientSocket(self),
            _ObservedClientSocket(self),
        )
        self.faulted = False
        self.accepted = 0
        self.closed = 0
        self.target_headers = 0
        self.target_wires = 0
        self.target_completions = 0
        self.target_failures = 0
        self.operation_active = False
        self.reentries = 0

    def _begin_operation(self):
        if self.operation_active:
            self.reentries += 1
            self._mark_fault()
            return False
        self.operation_active = True
        return True

    def _end_operation(self):
        self.operation_active = False

    def _mark_fault(self):
        self.faulted = True

    def claim_client(self, port):
        for client in self.clients:
            if not client.leased:
                self.accepted += 1
                return client._claim(port)
        self._mark_fault()
        raise RuntimeError("observed client capacity exceeded")

    def _header_observed(self):
        self.target_headers += 1

    def _wire_completed(self):
        self.target_wires += 1

    def _client_closed(self, target, wire_complete):
        self.closed += 1
        if target:
            if wire_complete:
                self.target_completions += 1
            else:
                self.target_failures += 1

    def open_clients(self):
        count = 0
        for client in self.clients:
            if client.leased:
                count += 1
        return count


class _ReadOnlyStatusGateway:
    """Forward only one exact phone GET to the real REST runtime."""

    __slots__ = (
        "runtime", "valid_status_requests", "successful_status_responses",
        "marked_status_responses", "rejected_requests", "responses_returned",
        "last_valid_peer_ip", "status_body_validated", "secret_checked",
        "secret_cleared", "_secret", "_not_found", "_method_not_allowed",
    )

    def __init__(self, runtime, temporary_password, csrf_secret):
        self.runtime = runtime
        self.valid_status_requests = 0
        self.successful_status_responses = 0
        self.marked_status_responses = 0
        self.rejected_requests = 0
        self.responses_returned = 0
        self.last_valid_peer_ip = None
        self.status_body_validated = False
        self.secret_checked = False
        self.secret_cleared = False
        self._secret = (
            bytearray(temporary_password.encode("ascii")), csrf_secret
        )
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

    @staticmethod
    def _is_ap_host(value):
        return type(value) is str and value in (AP_IP, AP_IP + ":80")

    def _fixed(self, response):
        self.rejected_requests += 1
        self.responses_returned += 1
        return response

    @staticmethod
    def _marked_status_response(response):
        headers = getattr(response, "headers", None)
        if type(headers) is not dict or headers:
            raise RuntimeError("status response marker contract failed")
        headers[_STATUS_PROOF_HEADER_NAME] = _STATUS_PROOF_HEADER_VALUE
        return response

    @staticmethod
    def _forbidden_public_field(value, depth=0):
        if depth > 16:
            return True
        if type(value) is dict:
            for key, item in value.items():
                if key in ("password", "csrf_token", "protocol"):
                    return True
                if _ReadOnlyStatusGateway._forbidden_public_field(
                    item, depth + 1
                ):
                    return True
        elif type(value) in (list, tuple):
            for item in value:
                if _ReadOnlyStatusGateway._forbidden_public_field(
                    item, depth + 1
                ):
                    return True
        return False

    @staticmethod
    def _contains_bytes(value, secret):
        if secret is None:
            return False
        if type(value) is str:
            try:
                value = value.encode("utf-8")
            except (UnicodeError, ValueError):
                return True
        if type(value) not in (bytes, bytearray):
            return False
        if len(value) < len(secret):
            return False
        last = len(value) - len(secret)
        for offset in range(last + 1):
            difference = 0
            for index, byte in enumerate(secret):
                difference |= value[offset + index] ^ byte
            if difference == 0:
                return True
        return False

    @staticmethod
    def _contains_hex(value, secret):
        if type(value) is not str or secret is None:
            return False
        width = len(secret) * 2
        if len(value) < width:
            return False
        digits = "0123456789abcdef"
        try:
            value = value.lower()
            value.encode("ascii")
        except (UnicodeError, ValueError):
            return True
        for offset in range(len(value) - width + 1):
            difference = 0
            for index, byte in enumerate(secret):
                position = offset + index * 2
                difference |= ord(value[position]) ^ ord(digits[byte >> 4])
                difference |= ord(value[position + 1]) ^ ord(
                    digits[byte & 15]
                )
            if difference == 0:
                return True
        return False

    @staticmethod
    def _contains_secret(value, secrets, depth=0):
        if depth > 16:
            return True
        for secret in secrets:
            if _ReadOnlyStatusGateway._contains_bytes(value, secret):
                return True
        if _ReadOnlyStatusGateway._contains_hex(value, secrets[1]):
            return True
        if type(value) is dict:
            for key, item in value.items():
                if _ReadOnlyStatusGateway._contains_secret(
                    key, secrets, depth + 1
                ) or _ReadOnlyStatusGateway._contains_secret(
                    item, secrets, depth + 1
                ):
                    return True
        elif type(value) in (list, tuple):
            for item in value:
                if _ReadOnlyStatusGateway._contains_secret(
                    item, secrets, depth + 1
                ):
                    return True
        return False

    def clear_secret(self):
        for secret in self._secret:
            if secret is not None:
                for index in range(len(secret)):
                    secret[index] = 0
        self.secret_cleared = True

    def _validate_status_body(self, body):
        if type(body) is not dict or body.get("api_version") != 1:
            return False
        if self._forbidden_public_field(body):
            return False
        if not self.secret_checked and self._contains_secret(body, self._secret):
            return False
        if type(body.get("request_id")) is not int:
            return False
        configuration = body.get("configuration")
        heater = body.get("heater")
        network = body.get("network")
        scheduler = body.get("scheduler")
        if not all(type(value) is dict for value in (
            configuration, heater, network, scheduler
        )):
            return False
        requested = heater.get("requested")
        actual = heater.get("actual")
        state = network.get("state")
        station = None if type(state) is not dict else state.get("station")
        valid = (
            configuration.get("stored_generation") == 2
            and configuration.get("runtime_generation") == 2
            and configuration.get("ledger_generation") == 2
            and configuration.get("setup_complete") is False
            and configuration.get("runtime_setup_complete") is False
            and configuration.get("restart_required") is False
            and configuration.get("timer_start_allowed") is False
            and configuration.get("network_start_allowed") is True
            and heater.get("phase") == "unsynchronized"
            and heater.get("request_revision") == 0
            and type(requested) is dict
            and requested.get("on") is False
            and type(actual) is dict
            and actual.get("initialized") is False
            and actual.get("synchronized") is False
            and heater.get("session") is None
            and heater.get("control_transition_pending") is False
            and heater.get("control_faulted") is False
            and scheduler.get("armed") is False
            and scheduler.get("faulted") is False
            and scheduler.get("timer_count") == 0
            and scheduler.get("active_occurrence_key") is None
            and scheduler.get("next_occurrence") is None
            and network.get("available") is True
            and type(state) is dict
            and type(state.get("access_point")) is dict
            and state["access_point"].get("active") is True
            and state["access_point"].get("ip") == AP_IP
            and type(station) is dict
            and station.get("connected") is False
            and station.get("ip") is None
            and station.get("known_networks") == []
        )
        if not valid:
            return False
        if not self.secret_checked:
            self.secret_checked = True
            self.clear_secret()
        return True

    def handle(self, request, peer_ip=None):
        exact_target = (
            getattr(request, "path", None) == STATUS_PATH
            and getattr(request, "target", None) == STATUS_PATH
            and getattr(request, "query", None) is None
        )
        if (
            not exact_target
            or not self._is_ap_host(getattr(request, "host", None))
            or not self._is_ap_peer(peer_ip)
        ):
            return self._fixed(self._not_found)
        if getattr(request, "method", None) != "GET":
            return self._fixed(self._method_not_allowed)
        if self.valid_status_requests != 0:
            return self._fixed(self._not_found)

        response = self.runtime.handle(request, peer_ip)
        self.valid_status_requests += 1
        self.responses_returned += 1
        self.last_valid_peer_ip = peer_ip
        valid = (
            getattr(response, "status", None) == 200
            and self._validate_status_body(getattr(response, "body", None))
        )
        if valid:
            response = self._marked_status_response(response)
            self.successful_status_responses += 1
            self.marked_status_responses += 1
            self.status_body_validated = True
        else:
            self.rejected_requests += 1
            response = self._not_found
        return response


def _require(condition, message):
    if not condition:
        raise RuntimeError(
            "Phase-8 full REST phone smoke failed: {}".format(message)
        )


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


def _memory_free():
    _gc.collect()
    reader = getattr(_gc, "mem_free", None)
    if not callable(reader):
        return None
    value = reader()
    _require(type(value) is int and value >= 0, "invalid heap reading")
    return value


def _diagnostic_memory_free():
    """Read free heap without collection and never alter the primary result."""

    try:
        reader = getattr(_gc, "mem_free", None)
        if not callable(reader):
            return -1
        value = reader()
        if (
            type(value) is int
            and 0 <= value <= 1000000
        ):
            return value
    except BaseException:
        pass
    return -1


def _require_heap(value, checkpoint):
    _require(
        type(value) is int and value >= MINIMUM_FREE_HEAP_BYTES,
        "free heap at {} is unavailable or below 32 KiB".format(checkpoint),
    )
    return value


def _storage_paths():
    paths = []
    for base in (CONFIG_BASE_PATH, LEDGER_BASE_PATH):
        for suffix in STORAGE_SUFFIXES:
            paths.append(base + suffix)
    return tuple(paths)


def _production_paths():
    paths = []
    for base in (PRODUCTION_CONFIG_BASE_PATH, PRODUCTION_LEDGER_BASE_PATH):
        for suffix in STORAGE_SUFFIXES:
            paths.append(base + suffix)
    return tuple(paths)


def _missing_file(error):
    code = getattr(error, "errno", None)
    if code is None and getattr(error, "args", None):
        code = error.args[0]
    return code == 2


def _remove_exact_files(filesystem):
    remover = _os.remove if filesystem is None else filesystem.remove
    for path in _storage_paths():
        try:
            remover(path)
        except OSError as error:
            if not _missing_file(error):
                raise


def _assert_files_absent(filesystem):
    stat = _os.stat if filesystem is None else filesystem.stat
    for path in _storage_paths():
        try:
            stat(path)
        except OSError as error:
            if _missing_file(error):
                continue
            raise
        raise RuntimeError("an isolated Phase-8 smoke file remains")
    return True


def _stat_signature(filesystem, paths):
    stat = _os.stat if filesystem is None else filesystem.stat
    result = []
    for path in paths:
        try:
            value = stat(path)
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


def _rest_read_only_truth(runtime, serving):
    snapshot = runtime.snapshot()
    application = snapshot.get("application")
    configuration = snapshot.get("configuration_gateway")
    manual = snapshot.get("manual_gateway")
    security = snapshot.get("security")
    if not all(type(value) is dict for value in (
        application, configuration, manual, security
    )):
        return False
    if (
        application.get("mutations") != 0
        or application.get("errors") != 0
        or configuration.get("commits") != 0
        or configuration.get("noops") != 0
        or manual.get("starts") != 0
        or manual.get("stops") != 0
    ):
        return False
    if serving:
        return (
            application.get("requests", 0) >= 1
            and security.get("started") is True
            and security.get("mutation_api_available") is True
        )
    return (
        security.get("started") is False
        and security.get("mutation_api_available") is False
    )


def _assert_redacted(password, *values):
    if password in repr(values):
        raise RuntimeError("Phase-8 full REST phone smoke leaked its key")


def _sanitized_raise(error):
    if isinstance(error, KeyboardInterrupt):
        raise KeyboardInterrupt() from None
    if isinstance(error, SystemExit):
        raise SystemExit() from None
    if isinstance(error, MemoryError):
        raise MemoryError() from None
    raise RuntimeError("Phase-8 full REST phone smoke failed") from None


def _load_failure_diagnostics():
    from tools import phase8_full_rest_phone_stage2_diagnostics

    return phase8_full_rest_phone_stage2_diagnostics


def _capture_failure_diagnostics(
    stage,
    server_snapshot,
    observed_socket_factory,
    gateway,
    ap_client_confirmed,
    post_bind_peer_confirmed,
    response_completed,
    heap_values=None,
):
    return _load_failure_diagnostics().capture(
        stage,
        server_snapshot,
        observed_socket_factory,
        gateway,
        ap_client_confirmed,
        post_bind_peer_confirmed,
        response_completed,
        heap_values,
    )


def _emit_failure_diagnostics(values):
    try:
        _load_failure_diagnostics().emit(values)
    except BaseException:
        try:
            print(FULL_REST_PHONE_FAILURE_STAGE_TOKEN)
        except BaseException:
            pass


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


def _cleanup_observed_sockets(factory):
    if factory is None:
        return True, None
    first_error = None
    clean = False
    for _ in range(2):
        try:
            result = factory.deinit()
            if result is not None and first_error is None:
                first_error = RuntimeError(
                    "observed socket deinit returned a value"
                )
        except BaseException as error:
            if first_error is None:
                first_error = error
        try:
            clean = (
                factory.listener.active is False
                and factory._orphan_listener is None
                and factory._operation_active is False
                and factory.observer.open_clients() == 0
            )
        except BaseException as error:
            clean = False
            if first_error is None:
                first_error = error
        if clean:
            break
    return bool(clean and first_error is None), first_error


def _cleanup_rest_runtime(runtime):
    if runtime is None:
        return True, None
    first_error = None
    clean = False
    for _ in range(2):
        try:
            result = runtime.deinit()
            if result is not None and first_error is None:
                first_error = RuntimeError("REST deinit returned a value")
        except BaseException as error:
            if first_error is None:
                first_error = error
        try:
            security = runtime.security_policy.snapshot()
            clean = (
                type(security) is dict
                and security.get("started") is False
                and security.get("mutation_api_available") is False
                and security.get("operation_active") is False
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


def _first_error(current, candidate):
    return current if current is not None else candidate


def prepare_proof(context):
    """Publish the read-only gateway and wire observer without server access."""

    _require(
        context.gateway is None and context.socket_observer is None,
        "proof owners were already published",
    )
    rest_runtime = context.rest_runtime
    random_provider = context.random_provider
    password = context.password
    security = rest_runtime.security_policy.snapshot()
    _require(
        security["started"] is True
        and security["mutation_api_available"] is True
        and random_provider.calls == 1
        and random_provider.last_count == 32
        and type(random_provider.secret) is bytearray
        and len(random_provider.secret) == 32,
        "REST security changed before proof composition",
    )
    gateway = _ReadOnlyStatusGateway(
        rest_runtime, password, random_provider.secret
    )
    context.gateway = gateway
    observer = _SocketResponseObserver()
    context.socket_observer = observer
    context.password = None
    return None


def continue_run(capsule, state, temporary_password, window_seconds):
    """Adopt the proven live AP and serve the full read-only status path."""

    password = _validate_password(temporary_password)
    window_seconds = _validate_window_seconds(window_seconds)

    context = state.context
    core = context.core
    filesystem = context.filesystem
    config_manager = context.config_manager
    configured_runtime = context.configured_runtime
    network_runtime = context.network_runtime
    network_manager = context.network_manager
    port = context.port
    network_module = context.network_module
    controller = context.controller
    protocol_port = context.protocol_port
    scheduler_gateway = context.scheduler_gateway
    rest_runtime = context.rest_runtime
    gateway = context.gateway
    server = state.server
    server_snapshot = None
    socket_observer = context.socket_observer
    observed_socket_factory = state.socket_factory
    random_provider = context.random_provider
    primary = None
    cleanup_error = None
    http_cleanup_ok = False
    observer_cleanup_ok = False
    rest_cleanup_ok = False
    radio_cleanup_ok = False
    files_cleanup_ok = False
    storage_owned = context.storage_owned
    ap_client_confirmed = capsule.ip_peer is not None
    post_bind_peer_confirmed = False
    response_completed = False
    completed_responses = 0
    observed_timeouts = 0
    target_wire_completions = 0
    valid_peer_ip = None
    memory_before = capsule.memory_before
    memory_after_product_imports = context.memory_after_product_imports
    memory_after_configuration_adoption = (
        context.memory_after_configuration_adoption
    )
    memory_after_wifi_factory = capsule.memory_after_wifi_factory
    memory_after_ap_ready = capsule.memory_after_ap_ready
    memory_after_ip_bind = capsule.memory_after_ip_bind
    memory_after_ip_response = capsule.memory_after_ip_response
    memory_after_ip_cleanup = capsule.memory_after_ip_cleanup
    memory_before_http_start = context.memory_before_http_start
    memory_after_proof_before_listen = (
        context.memory_after_proof_before_listen
    )
    memory_after_http_bind = None
    memory_after_response = None
    memory_after_cleanup = None
    memory_after_failure_cleanup = -1
    storage_write_baseline = context.storage_write_baseline
    production_stat_baseline = context.production_stat_baseline
    failure_stage = "confirm_association"
    failure_diagnostics = None
    cleanup_failure_stage = None

    try:
        _require(
            capsule.owner_state == "coordinator",
            "hardware ownership was already claimed",
        )
        capsule.owner_state = "stage2"
        failure_stage = "confirm_association"
        _require(
            state.proof_loaded is True
            and state.gate.armed is True
            and server is not None
            and server.started is True
            and gateway is not None
            and socket_observer is not None
            and observed_socket_factory is not None,
            "prepared HTTP ownership state is invalid",
        )
        security = rest_runtime.security_policy.snapshot()
        _require(
            security["started"] is True
            and security["mutation_api_available"] is True
            and random_provider.calls == 1
            and random_provider.last_count == 32
            and type(random_provider.secret) is bytearray
            and len(random_provider.secret) == 32
            and controller.requested_on is False
            and controller.request_revision == 0
            and protocol_port.calls == 0,
            "prepared REST security state is invalid",
        )
        peer_truth = port.access_point_status()
        _assert_redacted(password, peer_truth)
        _require(
            type(peer_truth) is dict
            and peer_truth.get("active") is True
            and peer_truth.get("ip") == AP_IP
            and peer_truth.get("clients") == 1,
            "phone association was lost after HTTP bind",
        )
        post_bind_peer_confirmed = True
        peer_truth = None
        observation_deadline = context.observation_deadline
        if core.ticks_diff(core.ticks_ms(), observation_deadline) >= 0:
            failure_stage = "observe_timeout"
            raise RuntimeError("manual observation window expired")
        server_snapshot = server.snapshot()
        _require(
            server_snapshot["started"] is True
            and server_snapshot["closed"] is False
            and server_snapshot["faulted"] is False
            and server_snapshot["client_count"] == 0
            and server_snapshot["accepted"] == 0
            and server_snapshot["completed"] == 0
            and server_snapshot["parse_errors"] == 0
            and server_snapshot["timeouts"] == 0
            and server_snapshot["socket_errors"] == 0
            and server_snapshot["reentries"] == 0
            and server_snapshot["accept_actions"] == 0
            and server_snapshot["recv_actions"] == 0
            and server_snapshot["send_actions"] == 0,
            "HTTP listener truth is invalid before READY",
        )
        _require(
            socket_observer.faulted is False
            and socket_observer.operation_active is False
            and socket_observer.reentries == 0
            and observed_socket_factory._faulted is False
            and observed_socket_factory.calls == 1
            and observed_socket_factory.factory_returned == 1
            and observed_socket_factory.setblocking_returned == 1
            and observed_socket_factory.bind_returned == 1
            and observed_socket_factory.listen_returned == 1
            and observed_socket_factory._orphan_listener is None
            and observed_socket_factory._operation_active is False
            and observed_socket_factory._reentries == 0
            and observed_socket_factory.listener.active is True
            and socket_observer.accepted == 0
            and socket_observer.open_clients() == 0,
            "HTTP response observer did not bind cleanly",
        )
        action = None
        network_snapshot = None
        events = None
        access_point = None
        memory_after_http_bind = _require_heap(
            _memory_free(), "HTTP bind with associated phone"
        )

        print(FULL_REST_PHONE_READY_TOKEN)
        print("url={}".format(STATUS_URL))
        print("window_seconds={}".format(window_seconds))
        print("Open the exact URL now.")
        while True:
            now_ms = core.ticks_ms()
            if core.ticks_diff(now_ms, observation_deadline) >= 0:
                failure_stage = "observe_timeout"
                raise RuntimeError(
                    "phone client and complete status response were not observed"
                )
            failure_stage = "observe_network"
            action = network_manager.step(now_ms)
            network_snapshot = network_manager.snapshot()
            events = network_manager.drain_events()
            _assert_redacted(password, action, network_snapshot, events)
            access_point = network_snapshot["access_point"]
            _require(
                access_point["active"] is True
                and access_point["ip"] == AP_IP,
                "access point truth changed during observation",
            )
            if action is not None and action != "ap_checked":
                raise RuntimeError("network changed state during observation")
            if action == "ap_checked":
                clients = access_point["clients"]
                _require(
                    clients == 1,
                    "phone association was lost during observation",
                )

            failure_stage = "observe_http_step"
            completed_before_step = server_snapshot["completed"]
            target_completions_before_step = (
                socket_observer.target_completions
            )
            server.step()
            server_snapshot = server.snapshot()
            _assert_redacted(password, server_snapshot)
            _require(
                server_snapshot["started"] is True
                and server_snapshot["closed"] is False,
                "HTTP server stopped during observation",
            )
            failure_stage = "observe_http_transport"
            _require(
                server_snapshot["faulted"] is False
                and server_snapshot["parse_errors"] == 0
                and server_snapshot["socket_errors"] == 0
                and server_snapshot["reentries"] == 0
                and 0 <= server_snapshot["timeouts"]
                <= 2 * server_snapshot["accepted"],
                "HTTP server reported an unsafe transport result",
            )
            observer_open_clients = socket_observer.open_clients()
            _require(
                socket_observer.faulted is False
                and socket_observer.operation_active is False
                and socket_observer.reentries == 0
                and observed_socket_factory._orphan_listener is None
                and observed_socket_factory._operation_active is False
                and observed_socket_factory._reentries == 0
                and socket_observer.accepted == server_snapshot["accepted"]
                and socket_observer.closed + observer_open_clients
                == socket_observer.accepted
                and observer_open_clients == server_snapshot["client_count"]
                and socket_observer.target_failures == 0
                and socket_observer.target_headers
                <= gateway.marked_status_responses
                and socket_observer.target_wires
                <= socket_observer.target_headers
                and socket_observer.target_completions
                <= socket_observer.target_wires,
                "HTTP response observer truth is invalid",
            )
            failure_stage = "observe_security"
            security = rest_runtime.security_policy.snapshot()
            _require(
                security["started"] is True
                and security["mutation_api_available"] is True
                and random_provider.calls == 1
                and controller.requested_on is False
                and controller.request_revision == 0
                and protocol_port.calls == 0,
                "read-only safety truth changed during observation",
            )

            # The sole schema-valid status response carries a fixed public
            # marker.  Its preallocated socket wrapper recognizes that marker
            # only in bounded response-header bytes actually accepted by the
            # underlying send(), parses canonical Content-Length, counts the
            # exact wire length, and records completion only after close()
            # succeeds.  One server step can perform only that final send, so
            # the matching +1 completed transition binds the proof to the
            # target socket.  Unmarked browser sockets and their timeouts are
            # independent and remain owned by ordered HTTP cleanup.
            failure_stage = "observe_route_binding"
            _require(
                gateway.marked_status_responses
                == gateway.successful_status_responses
                and gateway.marked_status_responses <= 1,
                "status response marker accounting diverged",
            )
            target_completion_delta = (
                socket_observer.target_completions
                - target_completions_before_step
            )
            if target_completion_delta != 0:
                _require(
                    target_completion_delta == 1
                    and target_completions_before_step == 0
                    and socket_observer.target_headers == 1
                    and socket_observer.target_wires == 1
                    and socket_observer.target_completions == 1
                    and gateway.valid_status_requests == 1
                    and gateway.successful_status_responses == 1
                    and gateway.marked_status_responses == 1
                    and server_snapshot["completed"]
                    == completed_before_step + 1,
                    "target response completion was not route-bound",
                )
                response_completed = True
            if (
                gateway.marked_status_responses == 1
                and socket_observer.target_completions == 0
                and server_snapshot["client_count"] == 0
            ):
                raise RuntimeError(
                    "marked target response closed before completion"
                )
            if response_completed:
                _require(
                    socket_observer.target_headers == 1
                    and socket_observer.target_wires == 1
                    and socket_observer.target_completions == 1
                    and socket_observer.target_failures == 0,
                    "target response completion truth changed",
                )

            if ap_client_confirmed and response_completed:
                failure_stage = "observe_post_response"
                completed_responses = server_snapshot["completed"]
                observed_timeouts = server_snapshot["timeouts"]
                target_wire_completions = (
                    socket_observer.target_completions
                )
                valid_peer_ip = gateway.last_valid_peer_ip
                _require(
                    valid_peer_ip == capsule.ip_peer,
                    "full status peer differs from the proven link peer",
                )
                network_snapshot = None
                events = None
                access_point = None
                server_snapshot = None
                memory_after_response = _require_heap(
                    _memory_free(), "complete request and response"
                )
                _require(
                    _store_write_truth(config_manager)
                    == storage_write_baseline,
                    "isolated storage changed during the request",
                )
                scheduler_truth = scheduler_gateway.snapshot()
                _require(
                    _rest_read_only_truth(rest_runtime, True)
                    and scheduler_truth["applied"] == 0
                    and scheduler_truth["rejected"] == 0
                    and scheduler_truth["manual_stops"] == 0
                    and scheduler_truth["checkpoints"] == 0,
                    "a mutation gateway changed during the status request",
                )
                _require(
                    _stat_signature(filesystem, _production_paths())
                    == production_stat_baseline,
                    "production storage changed during the smoke",
                )
                break
            _sleep_checked(core.sleep_ms, POLL_INTERVAL_MS)
    except BaseException as error:
        primary = error
        try:
            if server is not None:
                candidate_snapshot = server.snapshot()
                if type(candidate_snapshot) is dict:
                    server_snapshot = candidate_snapshot
        except BaseException:
            # Preserve the primary failure and any last known safe snapshot.
            pass
    finally:
        # Binding ownership order: HTTP adapter, its transparent raw-socket
        # observer, REST security, Wi-Fi owners, RAM approval, then only the
        # six exact isolated storage filenames.
        http_cleanup_ok, error = _cleanup_http_server(server)
        if not http_cleanup_ok or error is not None:
            cleanup_failure_stage = "cleanup_http"
        cleanup_error = _first_error(cleanup_error, error)
        observer_cleanup_ok, error = _cleanup_observed_sockets(
            observed_socket_factory
        )
        if (
            (not observer_cleanup_ok or error is not None)
            and cleanup_failure_stage is None
        ):
            cleanup_failure_stage = "cleanup_http"
        cleanup_error = _first_error(cleanup_error, error)
        try:
            state.gate.disarm()
            gate_cleanup_ok = state.gate.armed is False
        except BaseException as error:
            gate_cleanup_ok = False
            if cleanup_failure_stage is None:
                cleanup_failure_stage = "cleanup_http"
            cleanup_error = _first_error(cleanup_error, error)
        observer_cleanup_ok = observer_cleanup_ok and gate_cleanup_ok
        if gateway is not None:
            try:
                gateway.clear_secret()
            except BaseException as error:
                if cleanup_failure_stage is None:
                    cleanup_failure_stage = "cleanup_rest"
                cleanup_error = _first_error(cleanup_error, error)
        if random_provider is not None:
            try:
                random_provider.clear()
            except BaseException as error:
                if cleanup_failure_stage is None:
                    cleanup_failure_stage = "cleanup_rest"
                cleanup_error = _first_error(cleanup_error, error)
        rest_cleanup_ok, error = _cleanup_rest_runtime(rest_runtime)
        if (
            (not rest_cleanup_ok or error is not None)
            and cleanup_failure_stage is None
        ):
            cleanup_failure_stage = "cleanup_rest"
        cleanup_error = _first_error(cleanup_error, error)

        cleanup_support = (
            core.support if core is not None else capsule.support
        )
        cleanup_board_config = (
            core.board_config if core is not None else capsule.board_config
        )
        if cleanup_support is not None:
            if network_module is None and port is not None:
                try:
                    network_module = cleanup_support._load_network_module()
                except BaseException as error:
                    if cleanup_failure_stage is None:
                        cleanup_failure_stage = "cleanup_wifi"
                    cleanup_error = _first_error(cleanup_error, error)
            if network_manager is None and port is None:
                radio_cleanup_ok = True
            else:
                try:
                    radio_cleanup_ok = cleanup_support._cleanup_radio(
                        network_manager, port, network_module
                    )
                except BaseException as error:
                    radio_cleanup_ok = False
                    if cleanup_failure_stage is None:
                        cleanup_failure_stage = "cleanup_wifi"
                    cleanup_error = _first_error(cleanup_error, error)
                if not radio_cleanup_ok and cleanup_failure_stage is None:
                    cleanup_failure_stage = "cleanup_wifi"
            try:
                cleanup_board_config.WIFI_RADIO_APPROVED = False
            except BaseException as error:
                if cleanup_failure_stage is None:
                    cleanup_failure_stage = "cleanup_wifi"
                cleanup_error = _first_error(cleanup_error, error)
        else:
            radio_cleanup_ok = port is None

        if storage_owned:
            try:
                _remove_exact_files(filesystem)
                files_cleanup_ok = _assert_files_absent(filesystem)
            except BaseException as error:
                files_cleanup_ok = False
                if cleanup_failure_stage is None:
                    cleanup_failure_stage = "cleanup_storage"
                cleanup_error = _first_error(cleanup_error, error)
        else:
            try:
                files_cleanup_ok = _assert_files_absent(filesystem)
            except BaseException:
                # Pre-existing files are deliberately preserved.  The primary
                # absent-first failure remains the reported, sanitized reason.
                files_cleanup_ok = False
        if (
            http_cleanup_ok
            and observer_cleanup_ok
            and rest_cleanup_ok
            and radio_cleanup_ok
            and files_cleanup_ok
            and cleanup_error is None
            and cleanup_board_config is not None
            and cleanup_board_config.WIFI_RADIO_APPROVED is False
            and _wifi_lease_released(capsule.wifi_module)
        ):
            state.cleanup_confirmed = True
            capsule.owner_state = "released"

    if primary is not None:
        memory_after_failure_cleanup = _diagnostic_memory_free()
        try:
            failure_diagnostics = _capture_failure_diagnostics(
                failure_stage,
                server_snapshot,
                observed_socket_factory,
                gateway,
                ap_client_confirmed,
                post_bind_peer_confirmed,
                response_completed,
                (
                    memory_before,
                    memory_after_product_imports,
                    memory_after_configuration_adoption,
                    memory_after_wifi_factory,
                    memory_after_ap_ready,
                    memory_after_ip_bind,
                    memory_after_ip_response,
                    memory_after_ip_cleanup,
                    memory_before_http_start,
                    memory_after_http_bind,
                    memory_after_response,
                    memory_after_cleanup,
                    memory_after_failure_cleanup,
                ),
            )
        except BaseException:
            # Diagnostics are subordinate to the original, sanitized failure.
            failure_diagnostics = None
        _emit_failure_diagnostics(failure_diagnostics)
        print(FULL_REST_PHONE_FAIL_TOKEN)
        _sanitized_raise(primary)
    if cleanup_error is not None:
        failure_diagnostics = _capture_failure_diagnostics(
            cleanup_failure_stage,
            None,
            observed_socket_factory,
            gateway,
            ap_client_confirmed,
            post_bind_peer_confirmed,
            response_completed,
        )
        _emit_failure_diagnostics(failure_diagnostics)
        print(FULL_REST_PHONE_FAIL_TOKEN)
        _sanitized_raise(cleanup_error)
    if not (
        http_cleanup_ok
        and observer_cleanup_ok
        and rest_cleanup_ok
        and radio_cleanup_ok
        and files_cleanup_ok
    ):
        failure_diagnostics = _capture_failure_diagnostics(
            "cleanup_state",
            None,
            observed_socket_factory,
            gateway,
            ap_client_confirmed,
            post_bind_peer_confirmed,
            response_completed,
        )
        _emit_failure_diagnostics(failure_diagnostics)
        print(FULL_REST_PHONE_FAIL_TOKEN)
        raise RuntimeError(
            "Phase-8 full REST phone smoke failed: cleanup was not confirmed"
        )

    try:
        failure_stage = "postflight_safe_state"
        core.support._verify_hardware_locks(core.board_config)
        observer_open_clients = socket_observer.open_clients()
        safe = (
            core.support._interfaces_inactive(network_module)
            and getattr(port, "cleanup_complete", False) is True
            and _wifi_lease_released(core.wifi_module)
            and core.board_config.WIFI_RADIO_APPROVED is False
            and core.board_config.UART_PROTOCOL_TX_ENABLED is False
            and core.board_config.ONEWIRE_PIN is None
            and core.board_config.ONEWIRE_PIN_APPROVED is False
            and core.board_config.I2C_SDA_PIN is None
            and core.board_config.I2C_SCL_PIN is None
            and core.board_config.I2C_PINS_APPROVED is False
            and controller.requested_on is False
            and controller.request_revision == 0
            and protocol_port.calls == 0
            and random_provider.calls == 1
            and rest_runtime.security_policy.snapshot()[
                "mutation_api_available"
            ] is False
            and _rest_read_only_truth(rest_runtime, False)
            and scheduler_gateway.snapshot()["applied"] == 0
            and scheduler_gateway.snapshot()["rejected"] == 0
            and scheduler_gateway.snapshot()["manual_stops"] == 0
            and scheduler_gateway.snapshot()["checkpoints"] == 0
            and socket_observer.faulted is False
            and socket_observer.operation_active is False
            and socket_observer.reentries == 0
            and observed_socket_factory.listener.active is False
            and observed_socket_factory._orphan_listener is None
            and observed_socket_factory._operation_active is False
            and observed_socket_factory._reentries == 0
            and observed_socket_factory._faulted is False
            and state.gate.armed is False
            and observer_open_clients == 0
            and socket_observer.accepted == socket_observer.closed
            and ap_client_confirmed
            and post_bind_peer_confirmed
            and socket_observer.target_headers == 1
            and socket_observer.target_wires == 1
            and socket_observer.target_completions == 1
            and socket_observer.target_failures == 0
            and gateway.marked_status_responses == 1
            and gateway.secret_checked is True
            and gateway.secret_cleared is True
            and _store_write_truth(config_manager) == storage_write_baseline
            and _stat_signature(filesystem, _production_paths())
            == production_stat_baseline
            and _assert_files_absent(filesystem)
        )
        _require(safe, "independent safe-state check failed")
        memory_after_cleanup = _require_heap(
            _memory_free(), "ordered cleanup"
        )
        checkpoints = (
            memory_after_product_imports,
            memory_after_configuration_adoption,
            memory_after_wifi_factory,
            memory_after_ap_ready,
            memory_after_ip_bind,
            memory_after_ip_response,
            memory_after_ip_cleanup,
            memory_after_proof_before_listen,
            memory_after_http_bind,
            memory_after_response,
            memory_after_cleanup,
        )
        _require(
            type(memory_before_http_start) is int
            and memory_before_http_start >= 40 * 1024,
            "pre-bind heap boundary was not retained",
        )
        _require(
            all(
                type(value) is int and value >= MINIMUM_FREE_HEAP_BYTES
                for value in checkpoints
            ),
            "section 27.7 heap checkpoint set is incomplete",
        )
        _require(
            ap_client_confirmed
            and post_bind_peer_confirmed
            and response_completed
            and gateway.successful_status_responses == 1
            and gateway.status_body_validated is True,
            "full status acceptance facts are incomplete",
        )
    except BaseException as error:
        failure_diagnostics = _capture_failure_diagnostics(
            failure_stage,
            None,
            observed_socket_factory,
            gateway,
            ap_client_confirmed,
            post_bind_peer_confirmed,
            response_completed,
        )
        _emit_failure_diagnostics(failure_diagnostics)
        print(FULL_REST_PHONE_FAIL_TOKEN)
        _sanitized_raise(error)

    result = {
        "phase": 8,
        "scope": "manual_phone_full_product_rest_ap",
        "ssid": AP_SSID,
        "ap_ip": AP_IP,
        "url": STATUS_URL,
        "clients_confirmed": 1,
        "association_confirmed_before_rest": True,
        "association_confirmed_after_bind": True,
        "link_probe_url": "http://192.168.4.1:8080/api/v1/phase8-link-check",
        "link_peer_ip": capsule.ip_peer,
        "link_probe_completed_responses": capsule.probe_completed,
        "link_probe_rejected_requests": capsule.probe_rejected,
        "link_probe_cleanup_confirmed": capsule.stage1_cleanup_confirmed,
        "single_wifi_lifetime_confirmed": True,
        "valid_status_requests": gateway.valid_status_requests,
        "successful_status_responses": gateway.successful_status_responses,
        "marked_status_responses": gateway.marked_status_responses,
        "rejected_requests": gateway.rejected_requests,
        "valid_peer_ip": valid_peer_ip,
        "completed_responses": completed_responses,
        "observed_http_timeouts": observed_timeouts,
        "target_wire_completions": target_wire_completions,
        "response_completed": True,
        "status_body_validated": True,
        "configuration_generation": config_manager.generation,
        "ledger_generation": config_manager.ledger_generation,
        "storage_slots_provisioned": True,
        "mutation_api_available_during_serve": True,
        "mutation_api_available_after_cleanup": False,
        "mutation_routes_exposed": False,
        "mutations_tested": False,
        "heater_requested_on": False,
        "heater_request_revision": 0,
        "protocol_calls": 0,
        "http_cleanup_confirmed": True,
        "socket_observer_cleanup_confirmed": True,
        "rest_cleanup_confirmed": True,
        "radio_cleanup_confirmed": True,
        "interfaces_inactive": True,
        "lease_released": True,
        "approval_restored": True,
        "isolated_files_removed": True,
        "isolated_storage_unchanged_during_request": True,
        "production_storage_unchanged": True,
        "status_secret_free": True,
        "frozen_origins_confirmed": True,
        "window_seconds": window_seconds,
        "memory_before": memory_before,
        "memory_after_product_imports": memory_after_product_imports,
        "memory_after_configuration_adoption": (
            memory_after_configuration_adoption
        ),
        "memory_after_wifi_factory": memory_after_wifi_factory,
        "memory_after_ap_ready": memory_after_ap_ready,
        "memory_after_ip_bind": memory_after_ip_bind,
        "memory_after_ip_response": memory_after_ip_response,
        "memory_after_ip_cleanup": memory_after_ip_cleanup,
        "memory_before_http_start": memory_before_http_start,
        "memory_after_proof_before_listen": memory_after_proof_before_listen,
        "memory_after_http_bind": memory_after_http_bind,
        "memory_after_response": memory_after_response,
        "memory_after_cleanup": memory_after_cleanup,
    }
    _assert_redacted(password, result)
    print("full_rest_status_response_completed=True")
    print("mutation_routes_exposed=False")
    print("mutation_api_available_after_cleanup=False")
    print("isolated_files_removed=True")
    print("http_rest_radio_cleanup_confirmed=True")
    print(FULL_REST_PHONE_PASS_TOKEN)
    return result
