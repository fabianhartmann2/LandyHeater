"""Bounded cooperative HTTP socket adapter for MicroPython.

The adapter owns only its listening socket and the client sockets accepted
from it.  Importing the module and constructing :class:`MicroPythonHTTPServer`
perform no I/O.  In particular this layer never creates, activates or
deactivates a WLAN interface; the caller must pass the explicit IPv4 address
of the already configured access point.

One call to :meth:`MicroPythonHTTPServer.step` performs at most one bounded
``accept``, ``recv`` or ``send`` operation.  Request parsing, strict JSON
response encoding and application routing happen after that single socket
operation and therefore remain suitable for a cooperative main loop.
"""

import time as _time

from services.http_protocol import (
    MAX_BODY_BYTES,
    MAX_HEADER_BLOCK_BYTES,
    MAX_REQUEST_LINE_BYTES,
    MAX_RESPONSE_BODY_BYTES,
    MAX_STATIC_RESPONSE_BODY_BYTES,
    MAX_RESPONSE_HEADER_BLOCK_BYTES,
    HttpParseError,
    HttpResponseEncodeError,
    encode_bytes_response,
    encode_json_response,
    parse_request,
)
from services.strict_json import StrictJSONError, encode_json_bytes


MAX_CLIENTS = 2
LISTEN_BACKLOG = 2
RECV_BUDGET_BYTES = 256
SEND_BUDGET_BYTES = 256
MAX_REQUEST_BYTES = (
    MAX_REQUEST_LINE_BYTES + 2 + MAX_HEADER_BLOCK_BYTES + MAX_BODY_BYTES
)
MAX_RESPONSE_WIRE_BYTES = (
    64 + MAX_RESPONSE_HEADER_BLOCK_BYTES + MAX_STATIC_RESPONSE_BODY_BYTES
)

DEFAULT_FIRST_BYTE_TIMEOUT_MS = 1500
DEFAULT_HEADER_IDLE_TIMEOUT_MS = 1000
DEFAULT_HEADER_ABSOLUTE_TIMEOUT_MS = 5000
DEFAULT_BODY_IDLE_TIMEOUT_MS = 1500
DEFAULT_BODY_ABSOLUTE_TIMEOUT_MS = 10000
DEFAULT_WRITE_IDLE_TIMEOUT_MS = 1500
DEFAULT_WRITE_ABSOLUTE_TIMEOUT_MS = 10000
MAX_TIMEOUT_MS = 60000

_WOULD_BLOCK_ERRNOS = (11, 35, 10035)
# ESP-IDF/newlib reports an incoming TCP connection which was aborted before
# ``accept()`` completed as ECONNABORTED=113.  ESP-lwIP can also produce this
# result when allocating the pending connection's PCB or netconn fails.  Both
# cases are local to that pending connection; the listening socket remains
# valid and must stay available for the next client.  Do not apply this
# exception to recv/send paths.
_ABORTED_ACCEPT_ERRNOS = (113,)
_INCOMPLETE_HEADER_CODE = "incomplete_headers"
_INCOMPLETE_BODY_CODE = "incomplete_body"
_SAFE_PARSE_ERROR_CODES = (
    "body_not_allowed",
    "body_too_large",
    "content_length_required",
    "duplicate_header",
    "forbidden_header",
    "header_block_too_large",
    "header_line_too_long",
    "invalid_content_length",
    "invalid_header",
    "invalid_header_name",
    "invalid_header_value",
    "invalid_line_ending",
    "invalid_method",
    "invalid_request_line",
    "invalid_request_type",
    "invalid_target",
    "missing_host",
    "obsolete_header_fold",
    "request_line_too_long",
    "target_too_long",
    "too_many_headers",
    "unexpected_data_after_body",
    "unsupported_http_version",
)
_SAFE_PARSE_STATUSES = (
    400,
    411,
    413,
    414,
    431,
)


def _plain_ticks_ms():
    return 0


def _plain_ticks_diff(newer, older):
    return newer - older


def _plain_ticks_add(value, delta):
    return value + delta


_platform_ticks_ms = getattr(_time, "ticks_ms", _plain_ticks_ms)
_platform_ticks_diff = getattr(_time, "ticks_diff", _plain_ticks_diff)
_platform_ticks_add = getattr(_time, "ticks_add", _plain_ticks_add)


class MicroPythonHTTPServerError(RuntimeError):
    """Base class for fixed, non-sensitive socket-adapter failures."""

    def __init__(self, code):
        self.code = code

    def __str__(self):
        return self.code


class MicroPythonHTTPServerStateError(MicroPythonHTTPServerError):
    """The server lifecycle or an injected dependency is invalid."""


class MicroPythonHTTPServerSocketError(MicroPythonHTTPServerError):
    """A listener lifecycle operation failed."""


def _require_callable(value, name):
    if not callable(value):
        raise ValueError("{} must be callable".format(name))
    return value


def _require_timeout(value, name):
    if type(value) is not int or value < 1 or value > MAX_TIMEOUT_MS:
        raise ValueError(
            "{} must be an integer from 1 to {}".format(
                name, MAX_TIMEOUT_MS
            )
        )
    return value


def _canonical_ipv4(value):
    if type(value) is not str or not value or len(value) > 15:
        raise ValueError("ap_bind_address must be an explicit IPv4 address")
    parts = value.split(".")
    if len(parts) != 4:
        raise ValueError("ap_bind_address must be an explicit IPv4 address")
    octets = []
    for part in parts:
        if not part or (len(part) > 1 and part[0] == "0"):
            raise ValueError(
                "ap_bind_address must be a canonical IPv4 address"
            )
        for character in part:
            if character < "0" or character > "9":
                raise ValueError(
                    "ap_bind_address must be an explicit IPv4 address"
                )
        number = int(part)
        if number > 255:
            raise ValueError(
                "ap_bind_address must be an explicit IPv4 address"
            )
        octets.append(number)
    if octets[0] == 0:
        raise ValueError("wildcard HTTP binding is forbidden")
    if octets == [255, 255, 255, 255] or octets[0] >= 224:
        raise ValueError("ap_bind_address must be a unicast IPv4 address")
    return value


def _default_socket_factory():
    # Deliberately lazy: importing this adapter never imports or opens socket.
    import socket as socket_module

    return socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)


def _accepted_peer_ip(address):
    if type(address) not in (tuple, list) or len(address) != 2:
        raise ValueError("accepted peer address is invalid")
    peer_ip = _canonical_ipv4(address[0])
    peer_port = address[1]
    if type(peer_port) is not int or peer_port < 0 or peer_port > 65535:
        raise ValueError("accepted peer address is invalid")
    return peer_ip


def _would_block(error):
    args = getattr(error, "args", ())
    return bool(args) and args[0] in _WOULD_BLOCK_ERRNOS


def _aborted_accept(error):
    args = getattr(error, "args", ())
    return (
        bool(args)
        and type(args[0]) is int
        and args[0] in _ABORTED_ACCEPT_ERRNOS
    )


def _make_nonblocking(port):
    setblocking = getattr(port, "setblocking", None)
    if callable(setblocking):
        result = setblocking(False)
    else:
        settimeout = getattr(port, "settimeout", None)
        if not callable(settimeout):
            raise MicroPythonHTTPServerStateError(
                "socket_nonblocking_contract_failed"
            )
        result = settimeout(0)
    if result is not None:
        raise MicroPythonHTTPServerStateError(
            "socket_nonblocking_contract_failed"
        )


def _fixed_error_response(status, code, message):
    body = encode_json_bytes(
        {
            "api_version": 1,
            "error": {
                "code": code,
                "message": message,
                "request_id": 0,
            },
        },
        max_bytes=512,
        max_depth=4,
        max_nodes=16,
    )
    return encode_json_response(status, body)


class _Client:
    __slots__ = (
        "port",
        "peer_ip",
        "phase",
        "request",
        "response",
        "write_offset",
        "idle_deadline_ms",
        "absolute_deadline_ms",
    )

    def __init__(self, port, peer_ip, first_deadline_ms):
        self.port = port
        self.peer_ip = peer_ip
        self.phase = "first"
        self.request = bytearray()
        self.response = None
        self.write_offset = 0
        self.idle_deadline_ms = first_deadline_ms
        self.absolute_deadline_ms = first_deadline_ms


class MicroPythonHTTPServer:
    """Serve one injected REST application on one explicit AP IPv4 address.

    ``start()`` is the only method which calls the lazy socket factory and
    opens a listener.  ``step()`` is non-blocking and bounded.  ``deinit()``
    closes the listener first, then every client, and retains only sockets
    whose ``close()`` failed so cleanup can be retried.
    """

    __slots__ = (
        "__application_handle",
        "__request_handler",
        "__ap_bind_address",
        "__port",
        "__socket_factory",
        "__ticks_ms",
        "__ticks_diff",
        "__ticks_add",
        "__first_byte_timeout_ms",
        "__header_idle_timeout_ms",
        "__header_absolute_timeout_ms",
        "__body_idle_timeout_ms",
        "__body_absolute_timeout_ms",
        "__write_idle_timeout_ms",
        "__write_absolute_timeout_ms",
        "__listener",
        "__accepting",
        "__closed",
        "__clients",
        "__cleanup_ports",
        "__next_actor",
        "__lifecycle_active",
        "__lifecycle_reentered",
        "__operation_active",
        "__operation_reentered",
        "__faulted",
        "__last_error",
        "__accepted",
        "__completed",
        "__parse_errors",
        "__timeouts",
        "__socket_errors",
        "__reentries",
        "__accept_actions",
        "__recv_actions",
        "__send_actions",
    )

    def __init__(
        self,
        application,
        ap_bind_address,
        port=80,
        socket_factory=None,
        request_handler=None,
        ticks_ms=None,
        ticks_diff=None,
        ticks_add=None,
        first_byte_timeout_ms=DEFAULT_FIRST_BYTE_TIMEOUT_MS,
        header_idle_timeout_ms=DEFAULT_HEADER_IDLE_TIMEOUT_MS,
        header_absolute_timeout_ms=DEFAULT_HEADER_ABSOLUTE_TIMEOUT_MS,
        body_idle_timeout_ms=DEFAULT_BODY_IDLE_TIMEOUT_MS,
        body_absolute_timeout_ms=DEFAULT_BODY_ABSOLUTE_TIMEOUT_MS,
        write_idle_timeout_ms=DEFAULT_WRITE_IDLE_TIMEOUT_MS,
        write_absolute_timeout_ms=DEFAULT_WRITE_ABSOLUTE_TIMEOUT_MS,
    ):
        application_handle = getattr(application, "handle", None)
        _require_callable(application_handle, "application.handle")
        ap_bind_address = _canonical_ipv4(ap_bind_address)
        if type(port) is not int or port < 1 or port > 65535:
            raise ValueError("port must be an integer from 1 to 65535")
        if socket_factory is None:
            socket_factory = _default_socket_factory
        _require_callable(socket_factory, "socket_factory")
        if request_handler is not None:
            _require_callable(request_handler, "request_handler")
        if (ticks_diff is None) != (ticks_add is None):
            raise ValueError(
                "ticks_diff and ticks_add must be provided together"
            )
        if ticks_ms is None:
            ticks_ms = _platform_ticks_ms
        if ticks_diff is None:
            ticks_diff = _platform_ticks_diff
            ticks_add = _platform_ticks_add
        _require_callable(ticks_ms, "ticks_ms")
        _require_callable(ticks_diff, "ticks_diff")
        _require_callable(ticks_add, "ticks_add")

        self.__application_handle = application_handle
        self.__request_handler = request_handler
        self.__ap_bind_address = ap_bind_address
        self.__port = port
        self.__socket_factory = socket_factory
        self.__ticks_ms = ticks_ms
        self.__ticks_diff = ticks_diff
        self.__ticks_add = ticks_add
        self.__first_byte_timeout_ms = _require_timeout(
            first_byte_timeout_ms, "first_byte_timeout_ms"
        )
        self.__header_idle_timeout_ms = _require_timeout(
            header_idle_timeout_ms, "header_idle_timeout_ms"
        )
        self.__header_absolute_timeout_ms = _require_timeout(
            header_absolute_timeout_ms, "header_absolute_timeout_ms"
        )
        self.__body_idle_timeout_ms = _require_timeout(
            body_idle_timeout_ms, "body_idle_timeout_ms"
        )
        self.__body_absolute_timeout_ms = _require_timeout(
            body_absolute_timeout_ms, "body_absolute_timeout_ms"
        )
        self.__write_idle_timeout_ms = _require_timeout(
            write_idle_timeout_ms, "write_idle_timeout_ms"
        )
        self.__write_absolute_timeout_ms = _require_timeout(
            write_absolute_timeout_ms, "write_absolute_timeout_ms"
        )

        self.__listener = None
        self.__accepting = False
        self.__closed = False
        self.__clients = [None, None]
        self.__cleanup_ports = [None, None]
        self.__next_actor = 0
        self.__lifecycle_active = False
        self.__lifecycle_reentered = False
        self.__operation_active = False
        self.__operation_reentered = False
        self.__faulted = False
        self.__last_error = None
        self.__accepted = 0
        self.__completed = 0
        self.__parse_errors = 0
        self.__timeouts = 0
        self.__socket_errors = 0
        self.__reentries = 0
        self.__accept_actions = 0
        self.__recv_actions = 0
        self.__send_actions = 0

    @property
    def ap_bind_address(self):
        return self.__ap_bind_address

    @property
    def port(self):
        return self.__port

    @property
    def started(self):
        return self.__accepting and self.__listener is not None

    @property
    def closed(self):
        return self.__closed

    def _assert_start_live(self):
        if self.__closed or self.__lifecycle_reentered:
            raise MicroPythonHTTPServerStateError(
                "server_start_cancelled"
            )

    def _now(self):
        try:
            value = self.__ticks_ms()
        except MemoryError:
            raise MemoryError() from None
        except Exception:
            raise MicroPythonHTTPServerStateError(
                "ticks_source_failed"
            ) from None
        if type(value) is not int or value < 0:
            raise MicroPythonHTTPServerStateError(
                "ticks_source_contract_failed"
            )
        return value

    def _add(self, value, delta):
        try:
            result = self.__ticks_add(value, delta)
        except MemoryError:
            raise MemoryError() from None
        except Exception:
            raise MicroPythonHTTPServerStateError(
                "ticks_add_failed"
            ) from None
        if type(result) is not int or result < 0:
            raise MicroPythonHTTPServerStateError(
                "ticks_add_contract_failed"
            )
        return result

    def _due(self, now_ms, deadline_ms):
        try:
            difference = self.__ticks_diff(now_ms, deadline_ms)
        except MemoryError:
            raise MemoryError() from None
        except Exception:
            raise MicroPythonHTTPServerStateError(
                "ticks_diff_failed"
            ) from None
        if type(difference) is not int:
            raise MicroPythonHTTPServerStateError(
                "ticks_diff_contract_failed"
            )
        return difference >= 0

    @staticmethod
    def _listener_contract(listener):
        for name in ("bind", "listen", "accept", "close"):
            if not callable(getattr(listener, name, None)):
                raise MicroPythonHTTPServerStateError(
                    "listener_socket_contract_failed"
                )

    @staticmethod
    def _client_contract(client):
        for name in ("recv", "send", "close"):
            if not callable(getattr(client, name, None)):
                raise MicroPythonHTTPServerStateError(
                    "client_socket_contract_failed"
                )

    def _close_listener(self):
        listener = self.__listener
        if listener is None:
            return True
        self.__listener = None
        try:
            result = listener.close()
        except MemoryError:
            self.__listener = listener
            raise MemoryError() from None
        except Exception:
            self.__listener = listener
            self.__socket_errors += 1
            self.__last_error = "listener_close_failed"
            return False
        except BaseException:
            self.__listener = listener
            raise
        if result is not None:
            self.__listener = listener
            self.__socket_errors += 1
            self.__last_error = "listener_close_contract_failed"
            return False
        return True

    def _close_client(self, index):
        client = self.__clients[index]
        if client is None:
            return True
        client.phase = "closing"
        client.request = None
        client.response = None
        client.peer_ip = None
        port = client.port
        if port is None:
            self.__clients[index] = None
            return True
        client.port = None
        try:
            result = port.close()
        except MemoryError:
            client.port = port
            self.__clients[index] = client
            raise MemoryError() from None
        except Exception:
            client.port = port
            self.__clients[index] = client
            self.__socket_errors += 1
            self.__last_error = "client_close_failed"
            return False
        except BaseException:
            client.port = port
            self.__clients[index] = client
            raise
        if result is not None:
            client.port = port
            self.__clients[index] = client
            self.__socket_errors += 1
            self.__last_error = "client_close_contract_failed"
            return False
        self.__clients[index] = None
        return True

    def _close_cleanup_port(self, index):
        port = self.__cleanup_ports[index]
        if port is None:
            return True
        close = getattr(port, "close", None)
        if not callable(close):
            self.__socket_errors += 1
            self.__last_error = "rejected_socket_close_contract_failed"
            return False
        self.__cleanup_ports[index] = None
        try:
            result = close()
        except MemoryError:
            self.__cleanup_ports[index] = port
            raise MemoryError() from None
        except Exception:
            self.__cleanup_ports[index] = port
            self.__socket_errors += 1
            self.__last_error = "rejected_socket_close_failed"
            return False
        except BaseException:
            self.__cleanup_ports[index] = port
            raise
        if result is not None:
            self.__cleanup_ports[index] = port
            self.__socket_errors += 1
            self.__last_error = "rejected_socket_close_contract_failed"
            return False
        return True

    def _reject_accepted(self, port, slot):
        """Close a rejected accepted socket, retaining a failed close."""

        if slot is None:
            for index in range(MAX_CLIENTS):
                if (
                    self.__clients[index] is None
                    and self.__cleanup_ports[index] is None
                ):
                    slot = index
                    break
        if (
            slot is None
            or self.__clients[slot] is not None
            or (
                self.__cleanup_ports[slot] is not None
                and self.__cleanup_ports[slot] is not port
            )
        ):
            self.__faulted = True
            self.__last_error = "rejected_socket_cleanup_failed"
            return False
        # Ownership is recorded before close(), so even OOM or a terminal
        # close exception leaves a reference for the next deinit() attempt.
        self.__cleanup_ports[slot] = port
        closed = self._close_cleanup_port(slot)
        if not closed:
            self.__faulted = True
            self.__last_error = "rejected_socket_cleanup_failed"
        return closed

    def start(self):
        """Open and bind the lazy listener; repeated successful calls are inert."""

        if self.__closed:
            raise MicroPythonHTTPServerStateError("server_closed")
        if self.started:
            return False
        if self.__lifecycle_active:
            self.__lifecycle_reentered = True
            self.__reentries += 1
            self.__faulted = True
            self.__last_error = "server_lifecycle_reentrancy_detected"
            raise MicroPythonHTTPServerStateError(
                "server_lifecycle_reentrancy_detected"
            )
        if (
            self.__listener is not None
            or any(client is not None for client in self.__clients)
            or any(port is not None for port in self.__cleanup_ports)
        ):
            raise MicroPythonHTTPServerStateError(
                "socket_cleanup_incomplete"
            )

        listener = None
        succeeded = False
        failed = False
        out_of_memory = False
        terminal_error = None
        cleanup_terminal_error = None
        self.__lifecycle_active = True
        self.__lifecycle_reentered = False
        try:
            listener = self.__socket_factory()
            self.__listener = listener
            self._assert_start_live()
            self._listener_contract(listener)
            _make_nonblocking(listener)
            self._assert_start_live()
            result = listener.bind((self.__ap_bind_address, self.__port))
            self._assert_start_live()
            if result is not None:
                raise MicroPythonHTTPServerStateError(
                    "listener_bind_contract_failed"
                )
            result = listener.listen(LISTEN_BACKLOG)
            self._assert_start_live()
            if result is not None:
                raise MicroPythonHTTPServerStateError(
                    "listener_listen_contract_failed"
                )
            succeeded = True
        except MemoryError:
            out_of_memory = True
        except Exception:
            failed = True
        except BaseException as error:
            terminal_error = error
        finally:
            if not succeeded and listener is not None:
                try:
                    self._close_listener()
                except MemoryError:
                    out_of_memory = True
                except BaseException as error:
                    cleanup_terminal_error = error
            self.__lifecycle_active = False
            self.__lifecycle_reentered = False

        if out_of_memory:
            raise MemoryError() from None
        if terminal_error is not None:
            raise terminal_error
        if cleanup_terminal_error is not None:
            raise cleanup_terminal_error
        if failed:
            self.__faulted = True
            self.__last_error = "listener_start_failed"
            raise MicroPythonHTTPServerSocketError(
                "listener_start_failed"
            ) from None

        self.__accepting = True
        self.__faulted = False
        self.__last_error = None
        self.__next_actor = 0
        return True

    def _free_slot(self):
        for index in range(MAX_CLIENTS):
            if (
                self.__clients[index] is None
                and self.__cleanup_ports[index] is None
            ):
                return index
        return None

    def _accept_one(self, now_ms):
        self.__accept_actions += 1
        try:
            accepted = self.__listener.accept()
        except MemoryError:
            raise MemoryError() from None
        except OSError as error:
            if _would_block(error) or _aborted_accept(error):
                return False
            self.__socket_errors += 1
            self.__last_error = "accept_failed"
            return False
        except Exception:
            self.__socket_errors += 1
            self.__last_error = "accept_contract_failed"
            return False

        port = None
        slot = self._free_slot()
        try:
            if type(accepted) not in (tuple, list):
                port = accepted
                raise MicroPythonHTTPServerStateError(
                    "accept_contract_failed"
                )
            if accepted:
                port = accepted[0]
                if slot is None:
                    raise MicroPythonHTTPServerStateError(
                        "client_capacity_invariant_failed"
                    )
                # Own the raw socket before validation, nonblocking setup or
                # any other operation which can raise or reenter.
                self.__cleanup_ports[slot] = port
            if len(accepted) != 2:
                raise MicroPythonHTTPServerStateError(
                    "accept_contract_failed"
                )
            peer_ip = _accepted_peer_ip(accepted[1])
            if (
                self.__closed
                or not self.__accepting
                or self.__operation_reentered
            ):
                self._reject_accepted(port, slot)
                return False
            self._client_contract(port)
            _make_nonblocking(port)
            if (
                self.__closed
                or not self.__accepting
                or self.__operation_reentered
            ):
                self._reject_accepted(port, slot)
                return False
            deadline = self._add(now_ms, self.__first_byte_timeout_ms)
            if (
                self.__closed
                or not self.__accepting
                or self.__operation_reentered
            ):
                self._reject_accepted(port, slot)
                return False
            client = _Client(port, peer_ip, deadline)
            self.__clients[slot] = client
            self.__cleanup_ports[slot] = None
            self.__accepted += 1
            return True
        except MemoryError:
            if port is not None:
                self._reject_accepted(port, slot)
            raise MemoryError() from None
        except Exception:
            if port is not None:
                self._reject_accepted(port, slot)
            self.__socket_errors += 1
            self.__last_error = "accepted_socket_rejected"
            return False
        except BaseException:
            if port is not None:
                self._reject_accepted(port, slot)
            raise

    def _begin_headers(self, client, now_ms):
        client.phase = "headers"
        client.idle_deadline_ms = self._add(
            now_ms, self.__header_idle_timeout_ms
        )
        client.absolute_deadline_ms = self._add(
            now_ms, self.__header_absolute_timeout_ms
        )

    def _begin_body(self, client, now_ms):
        client.phase = "body"
        client.idle_deadline_ms = self._add(
            now_ms, self.__body_idle_timeout_ms
        )
        client.absolute_deadline_ms = self._add(
            now_ms, self.__body_absolute_timeout_ms
        )

    def _begin_write(self, client, response, now_ms):
        client.phase = "write"
        client.request = None
        client.response = response
        client.write_offset = 0
        client.idle_deadline_ms = self._add(
            now_ms, self.__write_idle_timeout_ms
        )
        client.absolute_deadline_ms = self._add(
            now_ms, self.__write_absolute_timeout_ms
        )

    def _queue_error(self, client, status, code, message, now_ms):
        response = _fixed_error_response(status, code, message)
        self._begin_write(client, response, now_ms)

    def _queue_parse_error(self, client, error, now_ms):
        status = error.status
        code = error.code
        if status not in _SAFE_PARSE_STATUSES or code not in _SAFE_PARSE_ERROR_CODES:
            status = 400
            code = "invalid_http_request"
        self.__parse_errors += 1
        self._queue_error(
            client,
            status,
            code,
            "Invalid HTTP request",
            now_ms,
        )

    def _queue_application_response(self, index, client, response, now_ms):
        try:
            status = response.status
            content_type = getattr(response, "content_type", None)
            if content_type is None:
                body = encode_json_bytes(
                    response.body,
                    max_bytes=MAX_RESPONSE_BODY_BYTES,
                )
                encoded = encode_json_response(
                    status,
                    body,
                    response.headers,
                )
            else:
                if type(response.body) is not bytes:
                    raise HttpResponseEncodeError(
                        "response_body_must_be_bytes"
                    )
                encoded = encode_bytes_response(
                    status,
                    response.body,
                    content_type,
                    response.headers,
                )
        except MemoryError:
            raise MemoryError() from None
        except (StrictJSONError, HttpResponseEncodeError):
            self.__faulted = True
            self.__last_error = "response_contract_failed"
            self._close_client(index)
            return False
        except Exception:
            self.__faulted = True
            self.__last_error = "response_contract_failed"
            self._close_client(index)
            return False
        now_ms = self._now()
        if (
            self.__operation_reentered
            or self.__closed
            or self.__clients[index] is not client
        ):
            self._close_client(index)
            return False
        self._begin_write(client, encoded, now_ms)
        return True

    def _parse_and_route(self, index, now_ms):
        client = self.__clients[index]
        if (
            client is None
            or self.__closed
            or self.__operation_reentered
        ):
            self._close_client(index)
            return
        # ``parse_request`` quite correctly rejects a bare CR.  At a socket
        # boundary, however, the final CR may simply be the first half of a
        # CRLF pair.  Wait for one more bounded receive unless the complete
        # header terminator is already present (a CR in the body is data).
        if (
            client.phase != "body"
            and client.request
            and client.request[-1] == 13
            and client.request.find(b"\r\n\r\n") < 0
        ):
            client.idle_deadline_ms = self._add(
                now_ms, self.__header_idle_timeout_ms
            )
            return
        try:
            request = parse_request(client.request)
        except HttpParseError as error:
            if error.code == _INCOMPLETE_HEADER_CODE:
                if client.phase == "first":
                    self._begin_headers(client, now_ms)
                elif client.phase == "headers":
                    client.idle_deadline_ms = self._add(
                        now_ms, self.__header_idle_timeout_ms
                    )
                return
            if error.code == _INCOMPLETE_BODY_CODE:
                if client.phase != "body":
                    self._begin_body(client, now_ms)
                else:
                    client.idle_deadline_ms = self._add(
                        now_ms, self.__body_idle_timeout_ms
                    )
                return
            self._queue_parse_error(client, error, now_ms)
            return
        except MemoryError:
            self._close_client(index)
            raise MemoryError() from None
        except Exception:
            self.__faulted = True
            self.__last_error = "request_parser_contract_failed"
            self._queue_error(
                client,
                500,
                "request_parser_failed",
                "Internal server error",
                now_ms,
            )
            return

        try:
            if self.__request_handler is None:
                response = self.__application_handle(request)
            else:
                response = self.__request_handler(request, client.peer_ip)
        except MemoryError:
            self._close_client(index)
            raise MemoryError() from None
        except Exception:
            if self.__closed or self.__clients[index] is not client:
                return
            self.__faulted = True
            self.__last_error = "application_handle_failed"
            self._queue_error(
                client,
                500,
                "application_handle_failed",
                "Internal server error",
                now_ms,
            )
            return
        except BaseException:
            self._close_client(index)
            raise

        if (
            self.__operation_reentered
            or self.__closed
            or self.__clients[index] is not client
        ):
            self._close_client(index)
            return
        self._queue_application_response(index, client, response, now_ms)

    def _recv_one(self, index, now_ms):
        client = self.__clients[index]
        self.__recv_actions += 1
        try:
            chunk = client.port.recv(RECV_BUDGET_BYTES)
        except MemoryError:
            self._close_client(index)
            raise MemoryError() from None
        except OSError as error:
            if _would_block(error):
                return False
            self.__socket_errors += 1
            self.__last_error = "client_recv_failed"
            self._close_client(index)
            return False
        except Exception:
            self.__socket_errors += 1
            self.__last_error = "client_recv_contract_failed"
            self._close_client(index)
            return False

        if (
            self.__closed
            or self.__operation_reentered
            or self.__clients[index] is not client
        ):
            self._close_client(index)
            return False
        if type(chunk) not in (bytes, bytearray, memoryview):
            self.__socket_errors += 1
            self.__last_error = "client_recv_contract_failed"
            self._close_client(index)
            return False
        if len(chunk) > RECV_BUDGET_BYTES:
            self.__socket_errors += 1
            self.__last_error = "client_recv_budget_exceeded"
            self._close_client(index)
            return False
        if not chunk:
            self.__parse_errors += 1
            self.__last_error = "truncated_request"
            self._close_client(index)
            return False

        if len(client.request) + len(chunk) > MAX_REQUEST_BYTES:
            self.__parse_errors += 1
            if client.phase == "body":
                self._queue_error(
                    client,
                    413,
                    "body_too_large",
                    "Invalid HTTP request",
                    now_ms,
                )
            else:
                self._queue_error(
                    client,
                    431,
                    "header_block_too_large",
                    "Invalid HTTP request",
                    now_ms,
                )
            return True

        try:
            client.request.extend(chunk)
        except MemoryError:
            self._close_client(index)
            raise MemoryError() from None
        if client.phase == "first":
            self._begin_headers(client, now_ms)
        self._parse_and_route(index, now_ms)
        return True

    def _send_one(self, index, now_ms):
        client = self.__clients[index]
        remaining = len(client.response) - client.write_offset
        if remaining <= 0:
            self.__completed += 1
            self._close_client(index)
            return False
        count = min(remaining, SEND_BUDGET_BYTES)
        payload = memoryview(client.response)[
            client.write_offset:client.write_offset + count
        ]
        self.__send_actions += 1
        try:
            sent = client.port.send(payload)
        except MemoryError:
            self._close_client(index)
            raise MemoryError() from None
        except OSError as error:
            if _would_block(error):
                return False
            self.__socket_errors += 1
            self.__last_error = "client_send_failed"
            self._close_client(index)
            return False
        except Exception:
            self.__socket_errors += 1
            self.__last_error = "client_send_contract_failed"
            self._close_client(index)
            return False

        if (
            self.__closed
            or self.__operation_reentered
            or self.__clients[index] is not client
        ):
            self._close_client(index)
            return False
        if type(sent) is not int or sent < 0 or sent > count:
            self.__socket_errors += 1
            self.__last_error = "client_send_contract_failed"
            self._close_client(index)
            return False
        if sent == 0:
            self.__socket_errors += 1
            self.__last_error = "client_send_closed"
            self._close_client(index)
            return False
        client.write_offset += sent
        client.idle_deadline_ms = self._add(
            now_ms, self.__write_idle_timeout_ms
        )
        if client.write_offset == len(client.response):
            self.__completed += 1
            self._close_client(index)
        return True

    def _expire_clients(self, now_ms):
        for index in range(MAX_CLIENTS):
            if self.__cleanup_ports[index] is not None:
                self._close_cleanup_port(index)
            client = self.__clients[index]
            if client is None:
                continue
            if client.phase == "closing":
                self._close_client(index)
                continue
            if client.phase == "write":
                if (
                    self._due(now_ms, client.idle_deadline_ms)
                    or self._due(now_ms, client.absolute_deadline_ms)
                ):
                    self.__timeouts += 1
                    self.__last_error = "write_timeout"
                    self._close_client(index)
                continue
            if (
                self._due(now_ms, client.idle_deadline_ms)
                or self._due(now_ms, client.absolute_deadline_ms)
            ):
                self.__timeouts += 1
                self._queue_error(
                    client,
                    408,
                    "request_timeout",
                    "Request timeout",
                    now_ms,
                )

    def _actor_eligible(self, actor):
        if actor == 0:
            return self.started and self._free_slot() is not None
        return self.__clients[actor - 1] is not None

    def _perform_actor(self, actor, now_ms):
        if actor == 0:
            return self._accept_one(now_ms)
        index = actor - 1
        client = self.__clients[index]
        if client is None:
            return False
        if client.phase == "closing":
            return self._close_client(index)
        if client.phase == "write":
            return self._send_one(index, now_ms)
        return self._recv_one(index, now_ms)

    def _close_all_clients(self):
        out_of_memory = False
        terminal_error = None
        for index in range(MAX_CLIENTS):
            try:
                self._close_cleanup_port(index)
            except MemoryError:
                out_of_memory = True
            except BaseException as error:
                if terminal_error is None:
                    terminal_error = error
            try:
                self._close_client(index)
            except MemoryError:
                out_of_memory = True
            except BaseException as error:
                if terminal_error is None:
                    terminal_error = error
        if out_of_memory:
            raise MemoryError() from None
        if terminal_error is not None:
            raise terminal_error

    def step(self):
        """Perform at most one bounded accept/receive/send action."""

        if self.__operation_active:
            self.__operation_reentered = True
            self.__reentries += 1
            self.__faulted = True
            self.__last_error = "server_reentrancy_detected"
            return False
        if not self.started:
            return False

        self.__operation_active = True
        self.__operation_reentered = False
        result = False
        out_of_memory = False
        cleanup_terminal_error = None
        terminal_error = None
        try:
            now_ms = self._now()
            if not self.__operation_reentered and not self.__closed:
                self._expire_clients(now_ms)
            if not self.__operation_reentered and not self.__closed:
                for offset in range(MAX_CLIENTS + 1):
                    actor = (
                        self.__next_actor + offset
                    ) % (MAX_CLIENTS + 1)
                    if not self._actor_eligible(actor):
                        continue
                    self.__next_actor = (
                        actor + 1
                    ) % (MAX_CLIENTS + 1)
                    result = self._perform_actor(actor, now_ms)
                    # A receive which queued a response, or a send which made
                    # positive partial progress, should keep that writer for
                    # the next cooperative turn.  This avoids inserting an
                    # unrelated listener poll between bounded response
                    # chunks.  Would-block returns False and therefore keeps
                    # the normal round-robin position selected above.
                    if (
                        result is True
                        and actor != 0
                        and not self.__operation_reentered
                        and not self.__closed
                    ):
                        client = self.__clients[actor - 1]
                        if client is not None and client.phase == "write":
                            self.__next_actor = actor
                    break
        except MemoryError:
            out_of_memory = True
        except BaseException as error:
            terminal_error = error
        finally:
            reentered = self.__operation_reentered

        try:
            if reentered:
                try:
                    self._close_all_clients()
                except MemoryError:
                    out_of_memory = True
                except BaseException as error:
                    cleanup_terminal_error = error
                result = False
            if out_of_memory:
                try:
                    self._close_all_clients()
                except BaseException:
                    pass
                raise MemoryError() from None
            if terminal_error is not None:
                try:
                    self._close_all_clients()
                except MemoryError:
                    raise MemoryError() from None
                except BaseException:
                    pass
                raise terminal_error
            if cleanup_terminal_error is not None:
                raise cleanup_terminal_error
            return result
        finally:
            self.__operation_active = False
            self.__operation_reentered = False

    def deinit(self):
        """Close listener first and retry any failed socket close later."""

        self.__closed = True
        self.__accepting = False
        if self.__lifecycle_active:
            self.__lifecycle_reentered = True
            self.__reentries += 1
            self.__faulted = True
            self.__last_error = "server_lifecycle_reentrancy_detected"
            return None
        self.__lifecycle_active = True
        self.__lifecycle_reentered = False
        close_failed = False
        out_of_memory = False
        terminal_error = None

        try:
            if not self._close_listener():
                close_failed = True
        except MemoryError:
            out_of_memory = True
        except BaseException as error:
            terminal_error = error

        for index in range(MAX_CLIENTS):
            try:
                if not self._close_cleanup_port(index):
                    close_failed = True
            except MemoryError:
                out_of_memory = True
            except BaseException as error:
                if terminal_error is None:
                    terminal_error = error
            try:
                if not self._close_client(index):
                    close_failed = True
            except MemoryError:
                out_of_memory = True
            except BaseException as error:
                if terminal_error is None:
                    terminal_error = error

        self.__lifecycle_active = False
        self.__lifecycle_reentered = False

        if out_of_memory:
            raise MemoryError() from None
        if terminal_error is not None:
            raise terminal_error
        if close_failed:
            self.__faulted = True
            raise MicroPythonHTTPServerSocketError(
                "socket_close_failed"
            ) from None
        return None

    def snapshot(self):
        phases = []
        client_count = 0
        for index, client in enumerate(self.__clients):
            if self.__cleanup_ports[index] is not None:
                client_count += 1
                phases.append("closing")
            if client is not None:
                client_count += 1
                phases.append(client.phase)
        return {
            "started": self.started,
            "closed": self.__closed,
            "faulted": self.__faulted,
            "last_error": self.__last_error,
            "operation_active": self.__operation_active,
            "client_count": client_count,
            "client_phases": phases,
            "accepted": self.__accepted,
            "completed": self.__completed,
            "parse_errors": self.__parse_errors,
            "timeouts": self.__timeouts,
            "socket_errors": self.__socket_errors,
            "reentries": self.__reentries,
            "accept_actions": self.__accept_actions,
            "recv_actions": self.__recv_actions,
            "send_actions": self.__send_actions,
            "max_clients": MAX_CLIENTS,
            "listen_backlog": LISTEN_BACKLOG,
            "recv_budget_bytes": RECV_BUDGET_BYTES,
            "send_budget_bytes": SEND_BUDGET_BYTES,
            "max_request_bytes": MAX_REQUEST_BYTES,
            "max_response_wire_bytes": MAX_RESPONSE_WIRE_BYTES,
        }


# Friendly spelling for callers which use ``Http`` rather than ``HTTP``.
MicroPythonHttpServer = MicroPythonHTTPServer
