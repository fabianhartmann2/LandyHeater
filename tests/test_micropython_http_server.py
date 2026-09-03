import ast
import inspect
import json
import unittest

import adapters.micropython_http_server as server_module
from adapters.micropython_http_server import (
    LISTEN_BACKLOG,
    MAX_CLIENTS,
    RECV_BUDGET_BYTES,
    SEND_BUDGET_BYTES,
    MicroPythonHTTPServer,
    MicroPythonHTTPServerSocketError,
    MicroPythonHTTPServerStateError,
)


AP_IP = "192.168.4.1"
PEER_IP = "192.168.4.2"


class Clock:
    def __init__(self, now=0, modulus=None):
        self.now = now
        self.modulus = modulus

    def ticks_ms(self):
        return self.now

    def ticks_add(self, value, delta):
        result = value + delta
        if self.modulus is not None:
            result %= self.modulus
        return result

    def ticks_diff(self, newer, older):
        difference = newer - older
        if self.modulus is not None:
            half = self.modulus // 2
            difference = (difference + half) % self.modulus - half
        return difference


class Response:
    def __init__(self, status=200, body=None, headers=None, content_type=None):
        self.status = status
        self.body = {"ok": True} if body is None else body
        self.headers = {} if headers is None else headers
        if content_type is not None:
            self.content_type = content_type


class FakeApplication:
    def __init__(self, result=None, failure=None, callback=None):
        self.result = Response() if result is None else result
        self.failure = failure
        self.callback = callback
        self.requests = []
        self.deinit_calls = 0

    def handle(self, request):
        self.requests.append(request)
        if self.callback is not None:
            return self.callback(request)
        if self.failure is not None:
            raise self.failure
        return self.result

    def deinit(self):
        self.deinit_calls += 1


class FakeClientSocket:
    def __init__(
        self,
        recv_events=None,
        send_events=None,
        close_events=None,
        name="client",
        operation_log=None,
        local_address=(AP_IP, 80),
    ):
        self.recv_events = list(recv_events or [])
        self.send_events = list(send_events or [])
        self.close_events = list(close_events or [])
        self.name = name
        self.operation_log = operation_log
        self.local_address = local_address
        self.blocking_values = []
        self.recv_sizes = []
        self.send_sizes = []
        self.written = bytearray()
        self.close_calls = 0
        self.closed = False

    def setblocking(self, value):
        self.blocking_values.append(value)
        return None

    def getsockname(self):
        return self.local_address

    def recv(self, size):
        if self.operation_log is not None:
            self.operation_log.append((self.name, "recv"))
        self.recv_sizes.append(size)
        if not self.recv_events:
            raise OSError(11)
        event = self.recv_events.pop(0)
        if isinstance(event, BaseException):
            raise event
        if callable(event):
            return event(size)
        return event

    def send(self, payload):
        if self.operation_log is not None:
            self.operation_log.append((self.name, "send"))
        offered = bytes(payload)
        self.send_sizes.append(len(offered))
        if self.send_events:
            event = self.send_events.pop(0)
            if isinstance(event, BaseException):
                raise event
            result = event(payload) if callable(event) else event
        else:
            result = len(offered)
        if type(result) is int and 0 < result <= len(offered):
            self.written.extend(offered[:result])
        return result

    def close(self):
        if self.operation_log is not None:
            self.operation_log.append((self.name, "close"))
        self.close_calls += 1
        if self.close_events:
            event = self.close_events.pop(0)
            if isinstance(event, BaseException):
                raise event
            if callable(event):
                result = event()
            else:
                result = event
            if result is not None:
                return result
        self.closed = True
        return None


class FakeListener:
    def __init__(
        self,
        accept_events=None,
        close_events=None,
        operation_log=None,
    ):
        self.accept_events = list(accept_events or [])
        self.close_events = list(close_events or [])
        self.operation_log = operation_log
        self.blocking_values = []
        self.bind_values = []
        self.listen_values = []
        self.accept_calls = 0
        self.close_calls = 0
        self.closed = False

    def setblocking(self, value):
        self.blocking_values.append(value)
        return None

    def bind(self, address):
        self.bind_values.append(address)
        return None

    def listen(self, backlog):
        self.listen_values.append(backlog)
        return None

    def accept(self):
        if self.operation_log is not None:
            self.operation_log.append(("listener", "accept"))
        self.accept_calls += 1
        if not self.accept_events:
            raise OSError(11)
        event = self.accept_events.pop(0)
        if isinstance(event, BaseException):
            raise event
        if callable(event):
            return event()
        if isinstance(event, FakeClientSocket):
            return event, (PEER_IP, 50000 + self.accept_calls)
        return event

    def close(self):
        if self.operation_log is not None:
            self.operation_log.append(("listener", "close"))
        self.close_calls += 1
        if self.close_events:
            event = self.close_events.pop(0)
            if isinstance(event, BaseException):
                raise event
            if callable(event):
                result = event()
            else:
                result = event
            if result is not None:
                return result
        self.closed = True
        return None


class Factory:
    def __init__(self, listener):
        self.listener = listener
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.listener


def get_request(target="/api/v1/status"):
    return (
        "GET {} HTTP/1.1\r\nHost: {}\r\n\r\n".format(target, AP_IP)
    ).encode("ascii")


def post_request(body=b"{}", target="/api/v1/heater/stop"):
    return (
        "POST {} HTTP/1.1\r\nHost: {}\r\n"
        "Content-Type: application/json\r\nContent-Length: {}\r\n\r\n".format(
            target, AP_IP, len(body)
        )
    ).encode("ascii") + body


def response_json(wire):
    head, body = bytes(wire).split(b"\r\n\r\n", 1)
    return head, json.loads(body.decode("utf-8"))


class ServerFixture:
    def __init__(
        self,
        clients=None,
        application=None,
        clock=None,
        listener=None,
        request_handler=None,
        **server_options
    ):
        self.clock = Clock() if clock is None else clock
        self.application = (
            FakeApplication() if application is None else application
        )
        self.listener = (
            FakeListener(clients) if listener is None else listener
        )
        self.factory = Factory(self.listener)
        self.server = MicroPythonHTTPServer(
            self.application,
            AP_IP,
            socket_factory=self.factory,
            request_handler=request_handler,
            ticks_ms=self.clock.ticks_ms,
            ticks_diff=self.clock.ticks_diff,
            ticks_add=self.clock.ticks_add,
            **server_options
        )

    def start(self):
        self.server.start()
        return self

    def pump(self, predicate, maximum=100):
        for _ in range(maximum):
            if predicate():
                return
            self.server.step()
        self.fail("server did not reach expected state")

    @staticmethod
    def fail(message):
        raise AssertionError(message)


class TestConstructionAndLifecycle(unittest.TestCase):
    def test_import_and_construction_are_inert_and_bind_is_explicit(self):
        client = FakeClientSocket()
        listener = FakeListener([client])
        fixture = ServerFixture(listener=listener)
        self.assertEqual(fixture.factory.calls, 0)
        self.assertEqual(listener.bind_values, [])
        self.assertEqual(listener.listen_values, [])
        self.assertEqual(client.blocking_values, [])

        self.assertTrue(fixture.server.start())
        self.assertEqual(fixture.factory.calls, 1)
        self.assertEqual(listener.blocking_values, [False])
        self.assertEqual(listener.bind_values, [(AP_IP, 80)])
        self.assertEqual(listener.listen_values, [LISTEN_BACKLOG])
        self.assertFalse(fixture.server.start())
        self.assertEqual(fixture.factory.calls, 1)

    def test_wildcard_hostname_noncanonical_and_multicast_bind_are_rejected(self):
        app = FakeApplication()
        invalid = (
            "0.0.0.0",
            "landy.local",
            "192.168.004.1",
            "256.1.1.1",
            "224.0.0.1",
            "255.255.255.255",
        )
        for address in invalid:
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    MicroPythonHTTPServer(app, address)

    def test_wildcard_requires_trusted_per_connection_ingress_dispatch(self):
        app = FakeApplication()
        with self.assertRaises(ValueError):
            MicroPythonHTTPServer(app, "0.0.0.0")
        with self.assertRaises(ValueError):
            MicroPythonHTTPServer(app, "0.0.0.0", ap_address=AP_IP)

    def test_single_wildcard_listener_dispatches_ap_and_station_by_local_ip(self):
        calls = []

        def handler(request, peer_ip, ingress, local_ip):
            calls.append((request.path, peer_ip, ingress, local_ip))
            return Response()

        ap_client = FakeClientSocket(
            recv_events=[get_request()], local_address=(AP_IP, 80)
        )
        sta_client = FakeClientSocket(
            recv_events=[get_request()], local_address=("10.0.0.17", 80)
        )
        listener = FakeListener([ap_client, sta_client])
        clock = Clock()
        server = MicroPythonHTTPServer(
            FakeApplication(),
            "0.0.0.0",
            socket_factory=Factory(listener),
            request_handler=handler,
            ap_address=AP_IP,
            request_handler_uses_ingress=True,
            ticks_ms=clock.ticks_ms,
            ticks_diff=clock.ticks_diff,
            ticks_add=clock.ticks_add,
        )
        self.assertTrue(server.start())
        for _ in range(12):
            server.step()
        self.assertEqual(listener.bind_values, [("0.0.0.0", 80)])
        self.assertEqual(calls[0][2:], ("ap", AP_IP))
        self.assertEqual(calls[1][2:], ("sta", "10.0.0.17"))
        self.assertTrue(server.snapshot()["ingress_dispatch"])

    def test_start_failure_closes_provisional_listener_and_hides_error(self):
        class FailingListener(FakeListener):
            def bind(self, address):
                raise OSError("TOP-SECRET")

        listener = FailingListener()
        fixture = ServerFixture(listener=listener)
        with self.assertRaises(MicroPythonHTTPServerSocketError) as caught:
            fixture.server.start()
        self.assertEqual(str(caught.exception), "listener_start_failed")
        self.assertNotIn("TOP-SECRET", repr(caught.exception))
        self.assertTrue(listener.closed)
        self.assertFalse(fixture.server.started)

    def test_failed_provisional_close_is_retained_for_deinit_retry(self):
        class FailingListener(FakeListener):
            def bind(self, address):
                raise OSError("bind")

        listener = FailingListener(close_events=[OSError("close")])
        fixture = ServerFixture(listener=listener)
        with self.assertRaises(MicroPythonHTTPServerSocketError):
            fixture.server.start()
        self.assertFalse(listener.closed)
        self.assertIsNone(fixture.server.deinit())
        self.assertTrue(listener.closed)
        self.assertEqual(listener.close_calls, 2)

    def test_terminal_or_oom_provisional_close_resets_guard_and_is_retryable(self):
        class FailingListener(FakeListener):
            def bind(self, address):
                raise OSError("bind")

        for close_error, expected in (
            (KeyboardInterrupt(), KeyboardInterrupt),
            (MemoryError("oom"), MemoryError),
        ):
            with self.subTest(close_error=type(close_error).__name__):
                listener = FailingListener(close_events=[close_error])
                fixture = ServerFixture(listener=listener)
                with self.assertRaises(expected):
                    fixture.server.start()
                self.assertIsNone(fixture.server.deinit())
                self.assertTrue(listener.closed)
                self.assertEqual(listener.close_calls, 2)

    def test_step_before_start_is_inert(self):
        fixture = ServerFixture(clients=[FakeClientSocket()])
        self.assertFalse(fixture.server.step())
        self.assertEqual(fixture.listener.accept_calls, 0)

    def test_deinit_is_terminal_and_cannot_reopen_sockets(self):
        fixture = ServerFixture().start()
        fixture.server.deinit()
        self.assertTrue(fixture.server.closed)
        with self.assertRaises(MicroPythonHTTPServerStateError) as caught:
            fixture.server.start()
        self.assertEqual(caught.exception.code, "server_closed")
        self.assertEqual(fixture.factory.calls, 1)

    def test_nested_start_from_socket_factory_cannot_overwrite_listener(self):
        app = FakeApplication()
        listener = FakeListener()
        holder = {}

        def factory():
            with self.assertRaises(MicroPythonHTTPServerStateError):
                holder["server"].start()
            return listener

        server = MicroPythonHTTPServer(
            app,
            AP_IP,
            socket_factory=factory,
            ticks_ms=lambda: 0,
            ticks_diff=lambda newer, older: newer - older,
            ticks_add=lambda value, delta: value + delta,
        )
        holder["server"] = server
        with self.assertRaises(MicroPythonHTTPServerSocketError):
            server.start()
        self.assertTrue(listener.closed)
        self.assertFalse(server.started)
        self.assertEqual(server.snapshot()["reentries"], 1)

    def test_nested_deinit_from_bind_cancels_start_and_keeps_server_closed(self):
        holder = {}

        class ReentrantBindListener(FakeListener):
            def bind(self, address):
                self.bind_values.append(address)
                holder["server"].deinit()
                return None

        listener = ReentrantBindListener()
        fixture = ServerFixture(listener=listener)
        holder["server"] = fixture.server
        with self.assertRaises(MicroPythonHTTPServerSocketError):
            fixture.server.start()
        self.assertTrue(fixture.server.closed)
        self.assertTrue(listener.closed)
        self.assertFalse(fixture.server.started)
        with self.assertRaises(MicroPythonHTTPServerStateError):
            fixture.server.start()


class TestCooperativeSocketBudgets(unittest.TestCase):
    def test_aborted_pending_accept_is_transient_only_on_listener(self):
        secret = "aborted-accept-secret"
        client = FakeClientSocket([get_request()])
        listener = FakeListener([
            OSError(113, secret),
            OSError(113, secret),
            client,
        ])
        fixture = ServerFixture(listener=listener).start()

        fixture.server.step()
        after_abort = fixture.server.snapshot()
        self.assertEqual(after_abort["accept_actions"], 1)
        self.assertEqual(after_abort["accepted"], 0)
        self.assertEqual(after_abort["client_count"], 0)
        self.assertEqual(after_abort["socket_errors"], 0)
        self.assertIsNone(after_abort["last_error"])
        self.assertNotIn(secret, repr(after_abort))

        fixture.server.step()
        repeated_abort = fixture.server.snapshot()
        self.assertEqual(repeated_abort["accept_actions"], 2)
        self.assertEqual(repeated_abort["accepted"], 0)
        self.assertEqual(repeated_abort["socket_errors"], 0)

        fixture.pump(lambda: client.closed)
        completed = fixture.server.snapshot()
        self.assertEqual(completed["accepted"], 1)
        self.assertEqual(completed["completed"], 1)
        self.assertEqual(completed["socket_errors"], 0)

        for errno in (12, 23, 103, 118):
            with self.subTest(non_transient_accept_errno=errno):
                listener = FakeListener([OSError(errno, secret)])
                failed = ServerFixture(listener=listener).start()
                failed.server.step()
                snapshot = failed.server.snapshot()
                self.assertEqual(snapshot["accepted"], 0)
                self.assertEqual(snapshot["socket_errors"], 1)
                self.assertEqual(snapshot["last_error"], "accept_failed")
                self.assertNotIn(secret, repr(snapshot))

        recv_client = FakeClientSocket([OSError(113, secret)])
        recv_fixture = ServerFixture([recv_client]).start()
        recv_fixture.server.step()
        recv_fixture.server.step()
        recv_snapshot = recv_fixture.server.snapshot()
        self.assertTrue(recv_client.closed)
        self.assertEqual(recv_snapshot["socket_errors"], 1)
        self.assertEqual(recv_snapshot["last_error"], "client_recv_failed")
        self.assertNotIn(secret, repr(recv_snapshot))

        send_client = FakeClientSocket(
            [get_request()], send_events=[OSError(113, secret)]
        )
        send_fixture = ServerFixture([send_client]).start()
        send_fixture.pump(lambda: send_client.closed)
        send_snapshot = send_fixture.server.snapshot()
        self.assertTrue(send_client.closed)
        self.assertEqual(send_snapshot["completed"], 0)
        self.assertEqual(send_snapshot["socket_errors"], 1)
        self.assertEqual(send_snapshot["last_error"], "client_send_failed")
        self.assertNotIn(secret, repr(send_snapshot))

    def test_each_step_performs_at_most_one_accept_recv_or_send(self):
        first = FakeClientSocket([get_request()])
        second = FakeClientSocket([get_request("/second")])
        fixture = ServerFixture(clients=[first, second]).start()
        previous = fixture.server.snapshot()
        for _ in range(30):
            fixture.server.step()
            current = fixture.server.snapshot()
            delta = sum(
                current[name] - previous[name]
                for name in (
                    "accept_actions", "recv_actions", "send_actions"
                )
            )
            self.assertLessEqual(delta, 1)
            previous = current
            if first.closed and second.closed:
                break
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)

    def test_two_clients_are_the_hard_cap_and_third_is_not_accepted(self):
        clients = [
            FakeClientSocket(name="one"),
            FakeClientSocket(name="two"),
            FakeClientSocket(name="three"),
        ]
        fixture = ServerFixture(clients=clients).start()
        for _ in range(20):
            fixture.server.step()
        snapshot = fixture.server.snapshot()
        self.assertEqual(snapshot["client_count"], MAX_CLIENTS)
        self.assertEqual(snapshot["accepted"], MAX_CLIENTS)
        self.assertEqual(fixture.listener.accept_calls, MAX_CLIENTS)
        self.assertEqual(clients[2].blocking_values, [])

    def test_receive_and_partial_send_sizes_never_exceed_fixed_budgets(self):
        application = FakeApplication(
            Response(body={"payload": "x" * 700})
        )
        client = FakeClientSocket(
            [get_request()],
            send_events=[1, 7, 31, 256, 256, 256, 256],
        )
        fixture = ServerFixture([client], application=application).start()
        fixture.pump(lambda: client.closed)
        self.assertEqual(client.recv_sizes, [RECV_BUDGET_BYTES])
        self.assertTrue(client.send_sizes)
        self.assertLessEqual(max(client.send_sizes), SEND_BUDGET_BYTES)
        self.assertTrue(bytes(client.written).startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertIn(b"Connection: close\r\n", client.written)

    def test_queued_and_progressing_writer_keeps_the_next_turn(self):
        clock = Clock(0)
        operations = []
        application = FakeApplication(
            Response(body={"payload": "x" * 700})
        )
        client = FakeClientSocket(
            [get_request()], name="target", operation_log=operations
        )
        listener = FakeListener(
            [client], operation_log=operations
        )
        fixture = ServerFixture(
            application=application,
            clock=clock,
            listener=listener,
            write_idle_timeout_ms=4,
            write_absolute_timeout_ms=20,
        ).start()

        fixture.server.step()  # accept
        fixture.server.step()  # receive and queue the response
        clock.now = 3
        fixture.server.step()  # first bounded send
        clock.now = 4
        fixture.server.step()  # positive partial progress keeps priority

        self.assertEqual(
            operations[:4],
            [
                ("listener", "accept"),
                ("target", "recv"),
                ("target", "send"),
                ("target", "send"),
            ],
        )
        snapshot = fixture.server.snapshot()
        self.assertEqual(snapshot["accept_actions"], 1)
        self.assertEqual(snapshot["recv_actions"], 1)
        self.assertEqual(snapshot["send_actions"], 2)
        self.assertEqual(snapshot["timeouts"], 0)
        self.assertEqual(snapshot["client_phases"], ["write"])

    def test_would_block_writer_yields_to_the_other_client(self):
        operations = []
        first = FakeClientSocket(
            [OSError(11), get_request()],
            send_events=[128, OSError(11)],
            name="first",
            operation_log=operations,
        )
        second = FakeClientSocket(
            [get_request("/second")],
            name="second",
            operation_log=operations,
        )
        listener = FakeListener(
            [first, second], operation_log=operations
        )
        fixture = ServerFixture(
            listener=listener,
            application=FakeApplication(
                Response(body={"payload": "x" * 700})
            ),
        ).start()

        fixture.server.step()  # accept first
        fixture.server.step()  # first receive would block
        fixture.server.step()  # accept second
        fixture.server.step()  # first receive queues its response
        fixture.server.step()  # prioritized first writer makes progress
        fixture.server.step()  # same writer would block and loses priority
        fixture.server.step()  # fairness advances to second client

        self.assertEqual(
            operations[-3:],
            [
                ("first", "send"),
                ("first", "send"),
                ("second", "recv"),
            ],
        )
        snapshot = fixture.server.snapshot()
        self.assertEqual(snapshot["send_actions"], 2)
        self.assertEqual(snapshot["socket_errors"], 0)
        self.assertEqual(snapshot["client_count"], 2)

    def test_send_zero_bool_negative_and_oversize_are_contract_failures(self):
        for sent in (0, True, -1, SEND_BUDGET_BYTES + 1):
            with self.subTest(sent=sent):
                client = FakeClientSocket([get_request()], [sent])
                fixture = ServerFixture([client]).start()
                fixture.pump(lambda: client.closed)
                self.assertEqual(fixture.server.snapshot()["completed"], 0)
                self.assertIn(
                    fixture.server.snapshot()["last_error"],
                    ("client_send_closed", "client_send_contract_failed"),
                )


class TestFramingAndErrors(unittest.TestCase):
    def test_crlf_split_at_terminal_carriage_return_is_not_rejected(self):
        client = FakeClientSocket([
            b"GET /api/v1/status HTTP/1.1\r",
            ("\nHost: {}\r\n\r\n".format(AP_IP)).encode("ascii"),
        ])
        fixture = ServerFixture([client]).start()
        fixture.pump(lambda: client.closed)
        self.assertEqual(len(fixture.application.requests), 1)
        head, body = response_json(client.written)
        self.assertIn(b"HTTP/1.1 200 OK", head)
        self.assertEqual(body, {"ok": True})

    def test_body_can_arrive_in_multiple_bounded_reads(self):
        body = b'{"value":1}'
        head = post_request(body, "/write")[:-len(body)]
        client = FakeClientSocket([head + body[:2], body[2:6], body[6:]])
        fixture = ServerFixture([client]).start()
        fixture.pump(lambda: client.closed)
        self.assertEqual(len(fixture.application.requests), 1)
        self.assertEqual(fixture.application.requests[0].body, body)

    def test_parser_failure_has_only_fixed_safe_json_and_never_calls_app(self):
        secret = b"TOP-SECRET-RAW-REQUEST"
        client = FakeClientSocket([
            b"GET / HTTP/1.1\nHost: " + secret + b"\n\n"
        ])
        fixture = ServerFixture([client]).start()
        fixture.pump(lambda: client.closed)
        self.assertEqual(fixture.application.requests, [])
        head, body = response_json(client.written)
        self.assertIn(b"HTTP/1.1 400 Bad Request", head)
        self.assertEqual(body["error"]["request_id"], 0)
        self.assertEqual(body["error"]["message"], "Invalid HTTP request")
        self.assertNotIn(secret, bytes(client.written))

    def test_pipelined_second_request_is_rejected_without_dispatch(self):
        client = FakeClientSocket([get_request() + get_request("/again")])
        fixture = ServerFixture([client]).start()
        fixture.pump(lambda: client.closed)
        self.assertEqual(fixture.application.requests, [])
        _, body = response_json(client.written)
        self.assertEqual(body["error"]["code"], "unexpected_data_after_body")

    def test_truncated_request_closes_without_response_or_dispatch(self):
        client = FakeClientSocket([
            b"GET / HTTP/1.1\r\nHost: 192.168.4.1\r\n",
            b"",
        ])
        fixture = ServerFixture([client]).start()
        fixture.pump(lambda: client.closed)
        self.assertEqual(fixture.application.requests, [])
        self.assertEqual(client.written, b"")
        self.assertEqual(fixture.server.snapshot()["parse_errors"], 1)

    def test_invalid_peer_address_is_closed_and_never_stored(self):
        client = FakeClientSocket([get_request()])
        listener = FakeListener([
            (client, ("peer.example", 1234)),
        ])
        fixture = ServerFixture(listener=listener).start()
        fixture.server.step()
        self.assertTrue(client.closed)
        snapshot = fixture.server.snapshot()
        self.assertEqual(snapshot["accepted"], 0)
        self.assertEqual(snapshot["client_count"], 0)
        self.assertNotIn("peer", repr(snapshot).lower())

    def test_malformed_accept_pair_still_closes_socket_at_index_zero(self):
        client = FakeClientSocket()
        listener = FakeListener([(client,)])
        fixture = ServerFixture(listener=listener).start()
        fixture.server.step()
        self.assertTrue(client.closed)
        self.assertEqual(fixture.server.snapshot()["client_count"], 0)

    def test_terminal_nonblocking_setup_retains_socket_for_cleanup(self):
        class TerminalClient(FakeClientSocket):
            def setblocking(self, value):
                raise KeyboardInterrupt()

        client = TerminalClient(
            close_events=[OSError("close"), OSError("retry"), None]
        )
        fixture = ServerFixture([client]).start()
        with self.assertRaises(KeyboardInterrupt):
            fixture.server.step()
        self.assertEqual(fixture.server.snapshot()["client_count"], 1)
        self.assertIsNone(fixture.server.deinit())
        self.assertTrue(client.closed)
        self.assertEqual(client.close_calls, 3)

    def test_rejected_peer_close_failure_is_retained_for_cleanup_retry(self):
        client = FakeClientSocket(
            [get_request()], close_events=[OSError("close once")]
        )
        listener = FakeListener([
            (client, ("peer.example", 1234)),
        ])
        fixture = ServerFixture(listener=listener).start()
        fixture.server.step()
        self.assertEqual(fixture.server.snapshot()["client_count"], 1)
        self.assertEqual(fixture.server.snapshot()["client_phases"], ["closing"])
        self.assertIsNone(fixture.server.deinit())
        self.assertTrue(client.closed)

    def test_rejected_peer_close_oom_keeps_raw_owner_until_deinit(self):
        client = FakeClientSocket(
            [get_request()],
            close_events=[MemoryError("oom"), OSError("retry"), None],
        )
        listener = FakeListener([
            (client, ("peer.example", 1234)),
        ])
        fixture = ServerFixture(listener=listener).start()
        with self.assertRaises(MemoryError) as caught:
            fixture.server.step()
        self.assertEqual(repr(caught.exception), "MemoryError()")
        self.assertEqual(fixture.server.snapshot()["client_count"], 1)
        self.assertIsNone(fixture.server.deinit())
        self.assertTrue(client.closed)
        self.assertEqual(client.close_calls, 3)

    def test_optional_peer_handler_gets_validated_ip_and_default_stays_compatible(self):
        received = []

        def handler(request, peer_ip):
            received.append((request.path, peer_ip))
            return Response(body={"peer_seen": True})

        client = FakeClientSocket([get_request("/peer")])
        fixture = ServerFixture([client], request_handler=handler).start()
        fixture.pump(lambda: client.closed)
        self.assertEqual(received, [("/peer", PEER_IP)])
        self.assertEqual(fixture.application.requests, [])
        self.assertNotIn(PEER_IP.encode("ascii"), client.written)


class TestDeadlines(unittest.TestCase):
    @staticmethod
    def wrap_options():
        return {
            "first_byte_timeout_ms": 5,
            "header_idle_timeout_ms": 4,
            "header_absolute_timeout_ms": 12,
            "body_idle_timeout_ms": 4,
            "body_absolute_timeout_ms": 12,
            "write_idle_timeout_ms": 4,
            "write_absolute_timeout_ms": 12,
        }

    def test_first_byte_deadline_is_exact_and_wrap_safe(self):
        clock = Clock(250, 256)
        client = FakeClientSocket()
        fixture = ServerFixture(
            [client], clock=clock, **self.wrap_options()
        ).start()
        fixture.server.step()  # accept, deadline wraps to 255
        clock.now = 254
        fixture.server.step()
        self.assertFalse(client.closed)
        self.assertNotIn("write", fixture.server.snapshot()["client_phases"])

        clock.now = 255
        fixture.pump(lambda: client.closed)
        head, body = response_json(client.written)
        self.assertIn(b"HTTP/1.1 408 Request Timeout", head)
        self.assertEqual(body["error"]["code"], "request_timeout")

    def test_slowloris_progress_cannot_extend_header_absolute_deadline(self):
        clock = Clock(250, 256)
        client = FakeClientSocket([b"G", b"E", b"T", b" "])
        fixture = ServerFixture(
            [client], clock=clock, **self.wrap_options()
        ).start()
        fixture.server.step()  # accept
        clock.now = 251
        fixture.pump(lambda: len(client.recv_sizes) >= 1, maximum=3)
        # Header absolute deadline is 7 after wrap.  Each byte refreshes only
        # the idle deadline, never this absolute deadline.
        for timestamp, count in ((254, 2), (1, 3), (4, 4)):
            clock.now = timestamp
            fixture.pump(lambda count=count: len(client.recv_sizes) >= count, maximum=3)
        clock.now = 7
        fixture.pump(lambda: client.closed)
        _, body = response_json(client.written)
        self.assertEqual(body["error"]["code"], "request_timeout")

    def test_body_idle_deadline_and_write_deadline_close_stalled_clients(self):
        options = self.wrap_options()
        clock = Clock(20, 256)
        body = b'{"value":1}'
        head = post_request(body, "/write")[:-len(body)]
        body_client = FakeClientSocket([head + b"{"])
        fixture = ServerFixture(
            [body_client], clock=clock, **options
        ).start()
        fixture.server.step()
        clock.now = 21
        fixture.pump(
            lambda: "body" in fixture.server.snapshot()["client_phases"],
            maximum=3,
        )
        clock.now = 25
        fixture.pump(lambda: body_client.closed)
        _, timeout_body = response_json(body_client.written)
        self.assertEqual(timeout_body["error"]["code"], "request_timeout")

        write_clock = Clock(40, 256)
        write_client = FakeClientSocket(
            [get_request()], send_events=[OSError(11), OSError(11)]
        )
        write_fixture = ServerFixture(
            [write_client], clock=write_clock, **options
        ).start()
        write_fixture.server.step()
        write_clock.now = 41
        write_fixture.pump(
            lambda: "write" in write_fixture.server.snapshot()["client_phases"],
            maximum=3,
        )
        write_clock.now = 45
        write_fixture.server.step()
        self.assertTrue(write_client.closed)
        write_snapshot = write_fixture.server.snapshot()
        self.assertEqual(write_snapshot["completed"], 0)
        self.assertEqual(write_snapshot["send_actions"], 0)
        self.assertEqual(write_client.send_sizes, [])

    def test_slow_handler_and_encoder_get_fresh_write_deadline(self):
        for slow_stage in ("handler", "encoder"):
            with self.subTest(slow_stage=slow_stage):
                clock = Clock(100)

                def callback(request):
                    if slow_stage == "handler":
                        clock.now += 2000
                    return Response(body={"fresh": True})

                client = FakeClientSocket([get_request()])
                fixture = ServerFixture(
                    [client],
                    application=FakeApplication(callback=callback),
                    clock=clock,
                ).start()
                fixture.server.step()  # accept at 100 ms

                original = server_module.encode_json_bytes

                def slow_encode(*args, **kwargs):
                    clock.now += 2000
                    return original(*args, **kwargs)

                if slow_stage == "encoder":
                    server_module.encode_json_bytes = slow_encode
                try:
                    fixture.server.step()  # receive, route and encode
                finally:
                    server_module.encode_json_bytes = original

                fixture.pump(lambda: client.closed)
                snapshot = fixture.server.snapshot()
                self.assertEqual(snapshot["accepted"], 1)
                self.assertEqual(snapshot["completed"], 1)
                self.assertEqual(snapshot["timeouts"], 0)
                self.assertEqual(snapshot["send_actions"], 1)
                head, body = response_json(client.written)
                self.assertIn(b"HTTP/1.1 200 OK", head)
                self.assertIs(body["fresh"], True)

    def test_slow_handler_fresh_write_deadline_is_wrap_safe(self):
        clock = Clock(3500, 4096)

        def callback(request):
            clock.now = (clock.now + 2000) % clock.modulus
            return Response(body={"wrapped": True})

        client = FakeClientSocket([get_request()])
        fixture = ServerFixture(
            [client],
            application=FakeApplication(callback=callback),
            clock=clock,
        ).start()
        fixture.server.step()
        fixture.server.step()
        self.assertEqual(clock.now, 1404)
        fixture.pump(lambda: client.closed)

        snapshot = fixture.server.snapshot()
        self.assertEqual(snapshot["accepted"], 1)
        self.assertEqual(snapshot["completed"], 1)
        self.assertEqual(snapshot["timeouts"], 0)
        self.assertEqual(snapshot["send_actions"], 1)
        _, body = response_json(client.written)
        self.assertIs(body["wrapped"], True)


class TestFaultContainment(unittest.TestCase):
    def test_fresh_write_timestamp_failure_closes_client_but_keeps_listener(self):
        for injected, expected in (
            (MemoryError("FRESH TICK SECRET"), MemoryError),
            (KeyboardInterrupt(), KeyboardInterrupt),
        ):
            with self.subTest(error=expected.__name__):
                class FreshTimestampFailureClock(Clock):
                    def __init__(self, error):
                        super().__init__(0)
                        self.calls = 0
                        self.error = error

                    def ticks_ms(self):
                        self.calls += 1
                        if self.calls == 3:
                            raise self.error
                        return self.now

                clock = FreshTimestampFailureClock(injected)
                client = FakeClientSocket([get_request()])
                fixture = ServerFixture([client], clock=clock).start()
                fixture.server.step()  # first timestamp accepts the client
                with self.assertRaises(expected) as caught:
                    fixture.server.step()  # second step's fresh timestamp

                self.assertEqual(clock.calls, 3)
                if expected is MemoryError:
                    self.assertEqual(repr(caught.exception), "MemoryError()")
                snapshot = fixture.server.snapshot()
                self.assertTrue(client.closed)
                self.assertEqual(snapshot["client_count"], 0)
                self.assertEqual(snapshot["completed"], 0)
                self.assertEqual(snapshot["send_actions"], 0)
                self.assertFalse(snapshot["operation_active"])
                self.assertTrue(snapshot["started"])
                self.assertFalse(fixture.listener.closed)
                self.assertIsNone(fixture.server.deinit())
                self.assertTrue(fixture.listener.closed)

    def test_fresh_write_timestamp_reentry_fails_closed(self):
        holder = {}

        class ReenteringFreshTimestampClock(Clock):
            def __init__(self):
                super().__init__(0)
                self.calls = 0

            def ticks_ms(self):
                self.calls += 1
                if self.calls == 3:
                    self.assert_reentry()
                return self.now

            @staticmethod
            def assert_reentry():
                if holder["server"].step() is not False:
                    raise AssertionError("nested step unexpectedly succeeded")

        clock = ReenteringFreshTimestampClock()
        client = FakeClientSocket([get_request()])
        fixture = ServerFixture([client], clock=clock).start()
        holder["server"] = fixture.server
        fixture.server.step()
        self.assertFalse(fixture.server.step())

        snapshot = fixture.server.snapshot()
        self.assertEqual(clock.calls, 3)
        self.assertTrue(client.closed)
        self.assertEqual(client.written, b"")
        self.assertEqual(snapshot["client_count"], 0)
        self.assertEqual(snapshot["completed"], 0)
        self.assertEqual(snapshot["send_actions"], 0)
        self.assertEqual(snapshot["reentries"], 1)
        self.assertTrue(snapshot["faulted"])
        self.assertFalse(snapshot["operation_active"])
        self.assertTrue(snapshot["started"])
        self.assertFalse(fixture.listener.closed)
        self.assertIsNone(fixture.server.deinit())
        self.assertTrue(fixture.listener.closed)

    def test_application_memory_error_closes_client_and_is_context_free(self):
        client = FakeClientSocket([get_request()])
        app = FakeApplication(failure=MemoryError("ALLOCATOR SECRET"))
        fixture = ServerFixture([client], application=app).start()
        fixture.server.step()  # accept
        with self.assertRaises(MemoryError) as caught:
            fixture.server.step()
        self.assertEqual(repr(caught.exception), "MemoryError()")
        self.assertTrue(client.closed)
        self.assertFalse(fixture.server.snapshot()["operation_active"])

    def test_receive_memory_error_closes_client_and_is_context_free(self):
        client = FakeClientSocket([MemoryError("RECV SECRET")])
        fixture = ServerFixture([client]).start()
        fixture.server.step()
        with self.assertRaises(MemoryError) as caught:
            fixture.server.step()
        self.assertEqual(repr(caught.exception), "MemoryError()")
        self.assertTrue(client.closed)

    def test_terminal_receive_error_closes_all_clients_before_reraise(self):
        first = FakeClientSocket([OSError(11), KeyboardInterrupt()])
        second = FakeClientSocket()
        fixture = ServerFixture([first, second]).start()
        fixture.server.step()
        fixture.server.step()
        fixture.server.step()
        with self.assertRaises(KeyboardInterrupt):
            fixture.server.step()
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        self.assertEqual(fixture.server.snapshot()["client_count"], 0)

    def test_oom_cleanup_attempts_all_clients_even_on_terminal_close(self):
        first = FakeClientSocket(close_events=[KeyboardInterrupt(), None])
        second = FakeClientSocket()
        fixture = ServerFixture([first, second]).start()
        fixture.server.step()
        fixture.server.step()
        fixture.server.step()

        def fail_ticks():
            raise MemoryError("ticks")

        fixture.server._MicroPythonHTTPServer__ticks_ms = fail_ticks
        with self.assertRaises(MemoryError) as caught:
            fixture.server.step()
        self.assertEqual(repr(caught.exception), "MemoryError()")
        self.assertFalse(first.closed)
        self.assertTrue(second.closed)
        self.assertIsNone(fixture.server.deinit())
        self.assertTrue(first.closed)

    def test_memory_error_while_building_timeout_response_closes_client(self):
        clock = Clock(0)
        client = FakeClientSocket()
        fixture = ServerFixture(
            [client],
            clock=clock,
            first_byte_timeout_ms=1,
        ).start()
        fixture.server.step()
        clock.now = 1
        original = server_module.encode_json_bytes
        server_module.encode_json_bytes = lambda *args, **kwargs: (
            (_ for _ in ()).throw(MemoryError("ENCODER SECRET"))
        )
        try:
            with self.assertRaises(MemoryError) as caught:
                fixture.server.step()
        finally:
            server_module.encode_json_bytes = original
        self.assertEqual(repr(caught.exception), "MemoryError()")
        self.assertTrue(client.closed)

    def test_unexpected_application_error_becomes_fixed_500(self):
        client = FakeClientSocket([get_request()])
        app = FakeApplication(failure=RuntimeError("TOP-SECRET"))
        fixture = ServerFixture([client], application=app).start()
        fixture.pump(lambda: client.closed)
        head, body = response_json(client.written)
        self.assertIn(b"HTTP/1.1 500 Internal Server Error", head)
        self.assertEqual(body["error"]["code"], "application_handle_failed")
        self.assertNotIn(b"TOP-SECRET", client.written)

    def test_malformed_application_response_closes_without_false_500_ack(self):
        client = FakeClientSocket([get_request()])
        app = FakeApplication(Response(body={"unsupported": object()}))
        fixture = ServerFixture([client], application=app).start()
        fixture.pump(lambda: client.closed)
        self.assertEqual(client.written, b"")
        self.assertEqual(
            fixture.server.snapshot()["last_error"],
            "response_contract_failed",
        )

    def test_static_byte_response_uses_same_bounded_listener(self):
        client = FakeClientSocket([get_request("/assets/app.js")])
        app = FakeApplication(Response(
            body=b'"use strict";',
            headers={"Content-Security-Policy": "default-src 'self'"},
            content_type="application/javascript; charset=utf-8",
        ))
        fixture = ServerFixture([client], application=app).start()
        fixture.pump(lambda: client.closed)
        head, body = bytes(client.written).split(b"\r\n\r\n", 1)
        self.assertIn(
            b"Content-Type: application/javascript; charset=utf-8", head
        )
        self.assertIn(b"Content-Security-Policy: default-src 'self'", head)
        self.assertEqual(body, b'"use strict";')

    def test_committed_mutation_is_never_misreported_by_encoder_500(self):
        committed = []

        def callback(request):
            committed.append(True)
            return Response(body={"unsupported": object()})

        client = FakeClientSocket([get_request()])
        fixture = ServerFixture(
            [client], application=FakeApplication(callback=callback)
        ).start()
        fixture.pump(lambda: client.closed)
        self.assertEqual(committed, [True])
        self.assertEqual(client.written, b"")

    def test_encoder_oom_after_commit_closes_without_false_ack(self):
        committed = []

        def callback(request):
            committed.append(True)
            return Response(body={"changed": True})

        client = FakeClientSocket([get_request()])
        fixture = ServerFixture(
            [client], application=FakeApplication(callback=callback)
        ).start()
        fixture.server.step()
        original = server_module.encode_json_bytes

        def fail_encode(*args, **kwargs):
            raise MemoryError("ENCODER SECRET")

        server_module.encode_json_bytes = fail_encode
        try:
            with self.assertRaises(MemoryError) as caught:
                fixture.server.step()
        finally:
            server_module.encode_json_bytes = original
        self.assertEqual(committed, [True])
        self.assertEqual(repr(caught.exception), "MemoryError()")
        self.assertTrue(client.closed)
        self.assertEqual(client.written, b"")

    def test_send_error_closes_without_retrying_or_second_response(self):
        client = FakeClientSocket([get_request()], [OSError(32)])
        fixture = ServerFixture([client]).start()
        fixture.pump(lambda: client.closed)
        self.assertEqual(len(client.send_sizes), 1)
        self.assertEqual(client.written, b"")
        self.assertEqual(fixture.server.snapshot()["completed"], 0)

    def test_reentrant_step_is_rejected_and_outer_client_is_closed(self):
        holder = {}

        def callback(request):
            self.assertFalse(holder["server"].step())
            return Response()

        client = FakeClientSocket([get_request()])
        app = FakeApplication(callback=callback)
        fixture = ServerFixture([client], application=app).start()
        holder["server"] = fixture.server
        fixture.server.step()
        fixture.server.step()
        self.assertTrue(client.closed)
        snapshot = fixture.server.snapshot()
        self.assertEqual(snapshot["reentries"], 1)
        self.assertTrue(snapshot["faulted"])
        self.assertEqual(client.written, b"")

    def test_reentrant_step_from_recv_callback_cannot_dispatch_mutation(self):
        holder = {}

        def recv_and_reenter(size):
            self.assertFalse(holder["server"].step())
            return get_request("/must-not-dispatch")

        client = FakeClientSocket([recv_and_reenter])
        fixture = ServerFixture([client]).start()
        holder["server"] = fixture.server
        fixture.server.step()
        fixture.server.step()
        self.assertEqual(fixture.application.requests, [])
        self.assertTrue(client.closed)
        self.assertEqual(fixture.server.snapshot()["reentries"], 1)

    def test_deinit_inside_request_handler_discards_response_and_is_terminal(self):
        holder = {}

        def callback(request):
            holder["server"].deinit()
            return Response(body={"must_not_be_sent": True})

        client = FakeClientSocket([get_request()])
        app = FakeApplication(callback=callback)
        fixture = ServerFixture([client], application=app).start()
        holder["server"] = fixture.server
        fixture.server.step()
        fixture.server.step()
        self.assertTrue(fixture.server.closed)
        self.assertTrue(fixture.listener.closed)
        self.assertTrue(client.closed)
        self.assertEqual(client.written, b"")
        self.assertEqual(fixture.server.snapshot()["completed"], 0)

    def test_deinit_inside_recv_callback_prevents_late_dispatch(self):
        holder = {}

        def recv_and_close(size):
            holder["server"].deinit()
            return get_request()

        client = FakeClientSocket([recv_and_close])
        fixture = ServerFixture([client]).start()
        holder["server"] = fixture.server
        fixture.server.step()
        fixture.server.step()
        self.assertTrue(fixture.server.closed)
        self.assertEqual(fixture.application.requests, [])
        self.assertEqual(client.written, b"")


class TestCleanup(unittest.TestCase):
    def test_deinit_closes_listener_first_then_clients_and_is_idempotent(self):
        operations = []
        clients = [
            FakeClientSocket(name="one", operation_log=operations),
            FakeClientSocket(name="two", operation_log=operations),
        ]
        listener = FakeListener(clients, operation_log=operations)
        fixture = ServerFixture(listener=listener).start()
        fixture.server.step()
        fixture.server.step()
        fixture.server.step()
        operations[:] = []

        self.assertIsNone(fixture.server.deinit())
        self.assertEqual(operations[0], ("listener", "close"))
        self.assertIn(("one", "close"), operations)
        self.assertIn(("two", "close"), operations)
        counts = (listener.close_calls, clients[0].close_calls, clients[1].close_calls)
        self.assertIsNone(fixture.server.deinit())
        self.assertEqual(
            counts,
            (listener.close_calls, clients[0].close_calls, clients[1].close_calls),
        )
        self.assertFalse(fixture.server.started)
        self.assertEqual(fixture.application.deinit_calls, 0)

    def test_deinit_attempts_every_socket_and_retries_only_failed_closes(self):
        operations = []
        first = FakeClientSocket(
            close_events=[OSError("first")],
            name="one",
            operation_log=operations,
        )
        second = FakeClientSocket(name="two", operation_log=operations)
        listener = FakeListener(
            [first, second],
            close_events=[OSError("listener")],
            operation_log=operations,
        )
        fixture = ServerFixture(listener=listener).start()
        fixture.server.step()
        fixture.server.step()
        fixture.server.step()
        operations[:] = []

        with self.assertRaises(MicroPythonHTTPServerSocketError):
            fixture.server.deinit()
        self.assertEqual(operations[0], ("listener", "close"))
        self.assertIn(("one", "close"), operations)
        self.assertIn(("two", "close"), operations)
        self.assertEqual(second.close_calls, 1)
        self.assertFalse(fixture.server.started)

        self.assertIsNone(fixture.server.deinit())
        self.assertEqual(listener.close_calls, 2)
        self.assertEqual(first.close_calls, 2)
        self.assertEqual(second.close_calls, 1)

    def test_deinit_continues_cleanup_before_reraising_terminal_exception(self):
        client = FakeClientSocket()
        listener = FakeListener(
            [client], close_events=[KeyboardInterrupt()]
        )
        fixture = ServerFixture(listener=listener).start()
        fixture.server.step()
        with self.assertRaises(KeyboardInterrupt):
            fixture.server.deinit()
        self.assertTrue(client.closed)
        self.assertIsNone(fixture.server.deinit())
        self.assertTrue(listener.closed)

    def test_nested_deinit_from_close_callback_does_not_recurse(self):
        holder = {}

        def nested_deinit():
            holder["server"].deinit()
            return None

        listener = FakeListener(close_events=[nested_deinit])
        fixture = ServerFixture(listener=listener).start()
        holder["server"] = fixture.server
        self.assertIsNone(fixture.server.deinit())
        self.assertEqual(listener.close_calls, 1)
        self.assertTrue(listener.closed)
        self.assertTrue(fixture.server.closed)

    def test_client_close_callback_can_deinit_without_recursive_close(self):
        holder = {}

        def nested_deinit():
            holder["server"].deinit()
            return None

        client = FakeClientSocket(
            recv_events=[b""], close_events=[nested_deinit]
        )
        fixture = ServerFixture([client]).start()
        holder["server"] = fixture.server
        fixture.server.step()
        fixture.server.step()
        self.assertTrue(fixture.server.closed)
        self.assertTrue(client.closed)
        self.assertEqual(client.close_calls, 1)

    def test_deinit_does_not_touch_application_or_any_wlan_capability(self):
        class WLANTrap:
            def active(self, value=None):
                raise AssertionError("HTTP must not own WLAN")

            def deinit(self):
                raise AssertionError("HTTP must not deinit WLAN")

        wlan = WLANTrap()
        fixture = ServerFixture().start()
        fixture.application.wlan = wlan
        fixture.server.deinit()
        self.assertEqual(fixture.application.deinit_calls, 0)


class TestSourceBoundaries(unittest.TestCase):
    def test_socket_import_is_lazy_and_forbidden_layers_are_absent(self):
        source = inspect.getsource(server_module)
        tree = ast.parse(source)
        imports = []
        forbidden_attributes = set()
        socket_import_nodes = []
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
                    if alias.name == "socket":
                        socket_import_nodes.append(node)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Attribute):
                forbidden_attributes.add(node.attr)

        for forbidden in (
            "network", "machine", "hardware", "protocol.autoterm_protocol"
        ):
            self.assertNotIn(forbidden, imports)
        self.assertEqual(len(socket_import_nodes), 1)
        parent = parents[socket_import_nodes[0]]
        while parent is not None and not isinstance(parent, ast.FunctionDef):
            parent = parents.get(parent)
        self.assertIsNotNone(parent)
        self.assertEqual(parent.name, "_default_socket_factory")
        for forbidden in (
            "active", "disconnect", "sendall", "makefile", "sleep",
            "send_start", "send_stop", "step_protocol",
        ):
            self.assertNotIn(forbidden, forbidden_attributes)


if __name__ == "__main__":
    unittest.main()
