import ast
import contextlib
import io
import json
import runpy
import sys
import unittest
from unittest import mock

import board_config
from adapters.micropython_http_server import MicroPythonHTTPServer
from app.network_manager import NetworkManager
from hardware.micropython_wifi import open_wifi_from_board_config
from services.http_protocol import parse_request
from tests.test_micropython_http_server import (
    Factory,
    FakeClientSocket,
    FakeListener,
)
from tests.test_phase7_network_smoke import _fake_network
import hardware.micropython_wifi as wifi_module
import tools.phase7_network_smoke as radio_support
import tools.phase8_phone_http_smoke as smoke


_TEST_PASSWORD = "PhoneHttpOnly!42"


class _Clock:
    def __init__(self):
        self.now_ms = 0
        self.sleeps = []

    def ticks_ms(self):
        return self.now_ms

    @staticmethod
    def ticks_add(value, delta):
        return value + delta

    @staticmethod
    def ticks_diff(newer, older):
        return newer - older

    def sleep_ms(self, milliseconds):
        self.sleeps.append(milliseconds)
        self.now_ms += milliseconds
        return None


def _script_ap_clients(fake, counts):
    access_point = fake.interfaces[fake.IF_AP]
    original = access_point.status
    values = list(counts)
    calls = {"count": 0}

    def status(selector=None):
        if selector != "stations":
            return original(selector)
        index = calls["count"]
        calls["count"] += 1
        value = values[-1] if index >= len(values) else values[index]
        return [(b"phone",)] * value

    access_point.status = status
    return calls


def _request(path=smoke.CHECK_PATH, host=smoke.AP_IP, method="GET"):
    return (
        "{} {} HTTP/1.1\r\nHost: {}\r\n\r\n".format(method, path, host)
    ).encode("ascii")


def _wire_json(client):
    head, body = bytes(client.written).split(b"\r\n\r\n", 1)
    return head, json.loads(body.decode("utf-8"))


class TestPhase8PhoneHTTPSmoke(unittest.TestCase):
    def setUp(self):
        board_config.WIFI_RADIO_APPROVED = False
        wifi_module._WIFI_LEASED = False
        wifi_module._WIFI_LEASE_POISONED = False

    def tearDown(self):
        board_config.WIFI_RADIO_APPROVED = False
        wifi_module._WIFI_LEASED = False
        wifi_module._WIFI_LEASE_POISONED = False

    @staticmethod
    def _wifi_runtime(clock):
        return (
            radio_support,
            board_config,
            NetworkManager,
            wifi_module,
            open_wifi_from_board_config,
            clock.ticks_ms,
            clock.ticks_add,
            clock.ticks_diff,
            clock.sleep_ms,
        )

    def _execute(
        self,
        fake,
        listener,
        clock=None,
        memory=(100000, 90000, 140000, 48000, 85000),
        cleanup=None,
        http_loader=None,
    ):
        if clock is None:
            clock = _Clock()
        if http_loader is None:
            http_loader = lambda: (
                MicroPythonHTTPServer,
                Factory(listener),
            )
        output = io.StringIO()
        result = None
        error = None
        patches = [
            mock.patch.dict(sys.modules, {"network": fake}),
            mock.patch.object(
                smoke,
                "_load_wifi_runtime",
                return_value=self._wifi_runtime(clock),
            ),
            mock.patch.object(
                smoke,
                "_load_http_runtime",
                side_effect=http_loader,
            ),
            mock.patch.object(
                radio_support, "_verify_platform", return_value=True
            ),
            mock.patch.object(smoke, "_memory_free", side_effect=memory),
        ]
        if cleanup is not None:
            patches.append(mock.patch.object(
                radio_support, "_cleanup_radio", side_effect=cleanup
            ))
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with contextlib.redirect_stdout(output):
                try:
                    result = smoke.run(
                        smoke.PHONE_HTTP_CONFIRMATION,
                        _TEST_PASSWORD,
                        window_seconds=60,
                    )
                except BaseException as caught:
                    error = caught
        return result, error, output.getvalue(), clock

    def test_real_wifi_manager_and_http_server_complete_one_phone_request(self):
        fake = _fake_network()
        client_calls = _script_ap_clients(fake, (0, 1))
        operations = []
        client = FakeClientSocket(
            recv_events=[_request()],
            name="phone",
            operation_log=operations,
        )
        listener = FakeListener(
            accept_events=[client], operation_log=operations
        )

        result, error, output, clock = self._execute(fake, listener)

        self.assertIsNone(error)
        self.assertEqual(result["phase"], 8)
        self.assertEqual(result["scope"], "manual_phone_http_ap")
        self.assertEqual(result["ssid"], "Landy Heater")
        self.assertEqual(result["ap_ip"], "192.168.4.1")
        self.assertEqual(result["url"], smoke.CHECK_URL)
        self.assertEqual(result["clients_confirmed"], 1)
        self.assertEqual(result["valid_requests"], 1)
        self.assertEqual(result["valid_peer_ip"], "192.168.4.2")
        self.assertIs(result["response_completed"], True)
        self.assertIs(result["http_cleanup_confirmed"], True)
        self.assertIs(result["radio_cleanup_confirmed"], True)
        self.assertIs(result["lease_released"], True)
        self.assertEqual(result["memory_after_wifi_import"], 90000)
        self.assertEqual(result["memory_after_ap_ready"], 140000)
        self.assertEqual(result["memory_after_http_import"], 48000)
        self.assertGreaterEqual(client_calls["count"], 2)
        self.assertTrue(clock.sleeps)
        self.assertTrue(all(value == 25 for value in clock.sleeps))

        head, body = _wire_json(client)
        self.assertIn(b"HTTP/1.1 200 OK", head)
        self.assertEqual(body["api_version"], 1)
        self.assertEqual(body["phase"], 8)
        self.assertIs(body["radio_check"]["ap_peer_validated"], True)
        self.assertIs(body["radio_check"]["heater_control_enabled"], False)
        self.assertIs(body["radio_check"]["uart_enabled"], False)
        self.assertIs(body["radio_check"]["sensor_buses_enabled"], False)
        self.assertEqual(body["radio_check"]["result"], "ok")
        rendered_wire = bytes(client.written).decode("utf-8")
        for forbidden in (
            _TEST_PASSWORD,
            smoke.PHONE_HTTP_CONFIRMATION,
            smoke.PHONE_HTTP_CLIENT_TOKEN,
            smoke.PHONE_HTTP_PASS_TOKEN,
        ):
            self.assertNotIn(forbidden, rendered_wire)

        self.assertIn(smoke.PHONE_HTTP_READY_TOKEN, output)
        self.assertIn("url={}".format(smoke.CHECK_URL), output)
        self.assertIn(smoke.PHONE_HTTP_CLIENT_TOKEN, output)
        self.assertEqual(output.splitlines()[-1], smoke.PHONE_HTTP_PASS_TOKEN)
        self.assertNotIn(_TEST_PASSWORD, output + repr(result))
        self.assertTrue(listener.closed)
        self.assertTrue(client.closed)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
        self.assertIs(wifi_module._WIFI_LEASED, False)
        self.assertIs(wifi_module._WIFI_LEASE_POISONED, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

    def test_timeout_without_http_request_never_passes_and_cleans_both_owners(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1))
        listener = FakeListener()

        result, error, output, clock = self._execute(
            fake,
            listener,
            memory=(100000, 90000, 140000, 48000),
        )

        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertGreaterEqual(clock.now_ms, 60000)
        self.assertIn(smoke.PHONE_HTTP_CLIENT_TOKEN, output)
        self.assertIn(smoke.PHONE_HTTP_FAIL_TOKEN, output)
        self.assertNotIn(smoke.PHONE_HTTP_PASS_TOKEN, output)
        self.assertNotIn(_TEST_PASSWORD, output + repr(error))
        self.assertTrue(listener.closed)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
        self.assertIs(wifi_module._WIFI_LEASED, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

    def test_wrong_route_gets_fixed_404_but_can_never_satisfy_smoke(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1))
        client = FakeClientSocket(recv_events=[_request("/favicon.ico")])
        listener = FakeListener(accept_events=[client])

        result, error, output, _ = self._execute(
            fake,
            listener,
            memory=(100000, 90000, 140000, 48000),
        )

        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        head, body = _wire_json(client)
        self.assertIn(b"HTTP/1.1 404 Not Found", head)
        self.assertEqual(body, {
            "api_version": 1,
            "error": {"code": "not_found", "message": "Not found"},
        })
        self.assertNotIn(smoke.PHONE_HTTP_PASS_TOKEN, output)
        self.assertIn(smoke.PHONE_HTTP_FAIL_TOKEN, output)
        self.assertNotIn(_TEST_PASSWORD, output + repr(error))
        self.assertTrue(listener.closed)
        self.assertTrue(client.closed)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)

    def test_partial_nonblocking_send_must_finish_before_pass(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1))
        client = FakeClientSocket(
            recv_events=[_request()],
            send_events=[1, OSError(11), 2, 3, 5, 8],
        )
        listener = FakeListener(accept_events=[client])

        result, error, output, _ = self._execute(fake, listener)

        self.assertIsNone(error)
        self.assertIs(result["response_completed"], True)
        self.assertEqual(result["completed_responses"], 1)
        self.assertGreaterEqual(len(client.send_sizes), 7)
        head, body = _wire_json(client)
        self.assertIn(b"HTTP/1.1 200 OK", head)
        self.assertEqual(body["radio_check"]["result"], "ok")
        self.assertEqual(output.splitlines()[-1], smoke.PHONE_HTTP_PASS_TOKEN)
        self.assertTrue(client.closed)
        self.assertTrue(listener.closed)

    def test_cleanup_failure_never_passes_but_still_turns_radio_off(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1))
        order = []
        client = FakeClientSocket(
            recv_events=[_request()], operation_log=order, name="phone"
        )
        listener = FakeListener(accept_events=[client], operation_log=order)
        original_cleanup = radio_support._cleanup_radio

        def clean_but_report_failure(manager, port, network_module):
            order.append(("wifi", "cleanup"))
            original_cleanup(manager, port, network_module)
            return False

        result, error, output, _ = self._execute(
            fake,
            listener,
            memory=(100000, 90000, 140000, 48000),
            cleanup=clean_but_report_failure,
        )

        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn(smoke.PHONE_HTTP_FAIL_TOKEN, output)
        self.assertNotIn(smoke.PHONE_HTTP_PASS_TOKEN, output)
        self.assertLess(
            order.index(("listener", "close")),
            order.index(("wifi", "cleanup")),
        )
        self.assertTrue(listener.closed)
        self.assertTrue(client.closed)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
        self.assertIs(wifi_module._WIFI_LEASED, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

    def test_oom_and_terminal_baseexception_always_cleanup_both_owners(self):
        for error_type in (MemoryError, SystemExit, KeyboardInterrupt):
            with self.subTest(error_type=error_type):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1))
                client = FakeClientSocket(recv_events=[
                    error_type("do not leak {}".format(_TEST_PASSWORD))
                ])
                listener = FakeListener(accept_events=[client])

                result, error, output, _ = self._execute(
                    fake,
                    listener,
                    memory=(100000, 90000, 140000, 48000),
                )

                self.assertIsNone(result)
                self.assertIsInstance(error, error_type)
                self.assertEqual(error.args, ())
                self.assertIn(smoke.PHONE_HTTP_FAIL_TOKEN, output)
                self.assertNotIn(smoke.PHONE_HTTP_PASS_TOKEN, output)
                self.assertNotIn(_TEST_PASSWORD, output + repr(error))
                self.assertTrue(listener.closed)
                self.assertTrue(client.closed)
                self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
                self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
                self.assertIs(wifi_module._WIFI_LEASED, False)
                self.assertIs(wifi_module._WIFI_LEASE_POISONED, False)
                self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

    def test_http_import_is_after_confirmed_ap_and_oom_cleans_wifi(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0,))
        listener = FakeListener()
        observed = {"called": 0, "ap_active": None, "configured": None}

        def fail_after_observing_ap():
            observed["called"] += 1
            observed["ap_active"] = fake.interfaces[fake.IF_AP].enabled
            observed["configured"] = fake.ap_values.get("ssid")
            raise MemoryError("do not leak {}".format(_TEST_PASSWORD))

        result, error, output, _ = self._execute(
            fake,
            listener,
            memory=(100000, 90000, 140000),
            http_loader=fail_after_observing_ap,
        )

        self.assertIsNone(result)
        self.assertIsInstance(error, MemoryError)
        self.assertEqual(error.args, ())
        self.assertEqual(observed, {
            "called": 1,
            "ap_active": True,
            "configured": "Landy Heater",
        })
        self.assertIn("ap.active.true", fake.log)
        self.assertIn(smoke.PHONE_HTTP_FAIL_TOKEN, output)
        self.assertNotIn(smoke.PHONE_HTTP_READY_TOKEN, output)
        self.assertNotIn(smoke.PHONE_HTTP_PASS_TOKEN, output)
        self.assertNotIn(_TEST_PASSWORD, output + repr(error))
        self.assertEqual(listener.accept_calls, 0)
        self.assertEqual(listener.close_calls, 0)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
        self.assertIs(wifi_module._WIFI_LEASED, False)
        self.assertIs(wifi_module._WIFI_LEASE_POISONED, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

    def test_a_second_valid_request_is_a_hard_failure(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1))
        first = FakeClientSocket(recv_events=[_request()], name="first")
        second = FakeClientSocket(recv_events=[_request()], name="second")
        listener = FakeListener(accept_events=[first, second])

        result, error, output, _ = self._execute(
            fake,
            listener,
            memory=(100000, 90000, 140000, 48000),
        )

        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn(smoke.PHONE_HTTP_FAIL_TOKEN, output)
        self.assertNotIn(smoke.PHONE_HTTP_PASS_TOKEN, output)
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        self.assertTrue(listener.closed)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)

    def test_handler_allowlist_is_exact_and_returns_only_fixed_errors(self):
        valid = parse_request(_request())
        handler = smoke._Phase8RadioCheckHandler()
        response = handler.handle(valid, "192.168.4.2")
        self.assertEqual(response.status, 200)
        self.assertEqual(handler.valid_requests, 1)

        cases = (
            (_request(method="DELETE"), "192.168.4.2", 405),
            (_request(host="192.168.4.1:80"), "192.168.4.2", 404),
            (_request(path=smoke.CHECK_PATH + "?x=1"), "192.168.4.2", 404),
            (_request(), "192.168.4.1", 404),
            (_request(), "192.168.4.01", 404),
            (_request(), "192.168.4.255", 404),
            (_request(), "10.0.0.2", 404),
        )
        for wire, peer_ip, expected in cases:
            with self.subTest(peer_ip=peer_ip, status=expected):
                isolated = smoke._Phase8RadioCheckHandler()
                response = isolated.handle(parse_request(wire), peer_ip)
                self.assertEqual(response.status, expected)
                self.assertEqual(isolated.valid_requests, 0)
                rendered = repr(response.body) + repr(response.headers)
                for forbidden in (
                    _TEST_PASSWORD,
                    smoke.PHONE_HTTP_CONFIRMATION,
                    smoke.PHONE_HTTP_CLIENT_TOKEN,
                    smoke.PHONE_HTTP_PASS_TOKEN,
                    peer_ip,
                ):
                    self.assertNotIn(forbidden, rendered)

    def test_confirmation_password_and_window_bounds_are_pre_hardware(self):
        class EqualitySpoof:
            def __eq__(self, other):
                return True

        cases = (
            (None, _TEST_PASSWORD, 60, RuntimeError),
            (True, _TEST_PASSWORD, 60, RuntimeError),
            (EqualitySpoof(), _TEST_PASSWORD, 60, RuntimeError),
            (smoke.PHONE_HTTP_CONFIRMATION, None, 60, ValueError),
            (smoke.PHONE_HTTP_CONFIRMATION, "A" * 11, 60, ValueError),
            (smoke.PHONE_HTTP_CONFIRMATION, "A" * 64, 60, ValueError),
            (smoke.PHONE_HTTP_CONFIRMATION, "ABCDEFGHIJK\n", 60, ValueError),
            (smoke.PHONE_HTTP_CONFIRMATION, "ABCDEFGHIJKé", 60, ValueError),
            (smoke.PHONE_HTTP_CONFIRMATION, _TEST_PASSWORD, True, ValueError),
            (smoke.PHONE_HTTP_CONFIRMATION, _TEST_PASSWORD, 59, ValueError),
            (smoke.PHONE_HTTP_CONFIRMATION, _TEST_PASSWORD, 301, ValueError),
        )
        with mock.patch.object(
            smoke, "_memory_free", side_effect=AssertionError("heap read")
        ) as heap:
            with mock.patch.object(
                smoke,
                "_load_wifi_runtime",
                side_effect=AssertionError("Wi-Fi loaded"),
            ) as wifi_loader:
                with mock.patch.object(
                    smoke,
                    "_load_http_runtime",
                    side_effect=AssertionError("HTTP loaded"),
                ) as http_loader:
                    for confirmation, password, window, error_type in cases:
                        with self.subTest(password=password, window=window):
                            with self.assertRaises(error_type):
                                smoke.run(confirmation, password, window)
        heap.assert_not_called()
        wifi_loader.assert_not_called()
        http_loader.assert_not_called()

        self.assertEqual(smoke._validate_password("A" * 12), "A" * 12)
        self.assertEqual(smoke._validate_password("A" * 63), "A" * 63)
        self.assertEqual(smoke._validate_window_seconds(60), 60)
        self.assertEqual(smoke._validate_window_seconds(300), 300)

    def test_import_is_inert_and_has_no_product_or_hardware_import(self):
        real_import = __import__
        forbidden = (
            "board_config",
            "network",
            "machine",
            "socket",
            "hardware",
            "app",
            "adapters",
            "services",
            "protocol",
            "tools.phase7_network_smoke",
        )

        def guarded_import(name, *args, **kwargs):
            for blocked in forbidden:
                if name == blocked or name.startswith(blocked + "."):
                    raise AssertionError("forbidden import: {}".format(name))
            return real_import(name, *args, **kwargs)

        output = io.StringIO()
        with mock.patch("builtins.__import__", side_effect=guarded_import):
            with contextlib.redirect_stdout(output):
                namespace = runpy.run_path(
                    "tools/phase8_phone_http_smoke.py",
                    run_name="phase8_phone_http_smoke_import_test",
                )
        self.assertIn("run", namespace)
        self.assertEqual(output.getvalue(), "")
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

        with open(
            "tools/phase8_phone_http_smoke.py", "r", encoding="utf-8"
        ) as source:
            tree = ast.parse(source.read())
        top_level_imports = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level_imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                top_level_imports.append(node.module)
        self.assertEqual(top_level_imports, ["gc"])


if __name__ == "__main__":
    unittest.main()
