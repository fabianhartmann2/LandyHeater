import ast
import contextlib
import io
import json
import os
import runpy
import sys
import types
import unittest
from unittest import mock

import adapters.micropython_http_server as http_adapter
import board_config
from app.network_manager import NetworkManager
from app.rest_composition import build_rest_http_server, build_rest_runtime
from hardware.micropython_wifi import open_wifi_from_board_config
import hardware.micropython_wifi as wifi_module
from tests.test_micropython_http_server import (
    Factory,
    FakeClientSocket,
    FakeListener,
)
from tests.test_phase7_network_smoke import _fake_network
import tools.phase8_full_rest_phone_smoke as smoke
import tools.phase8_full_rest_phone_stage1 as stage1
import tools.phase8_full_rest_phone_stage2 as stage2
import tools.phase8_full_rest_phone_stage2_diagnostics as stage2_diagnostics
import tools.phase8_full_rest_phone_stage2_prepare as stage2_prepare
import tools.phase8_full_rest_phone_stage2_seam as stage2_seam


# Keep a test handle while preserving the production rule that the
# failure-only formatter is not resident on a successful target path.
sys.modules.pop(stage2_diagnostics.__name__, None)
_tools_package = sys.modules.get("tools")
if getattr(
    _tools_package, "phase8_full_rest_phone_stage2_diagnostics", None
) is stage2_diagnostics:
    delattr(_tools_package, "phase8_full_rest_phone_stage2_diagnostics")


_TEST_PASSWORD = "FullRestPhone!42"
_TEST_CSRF_TOKEN_HEX = bytes(range(32)).hex()


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


class _MemoryStream:
    def __init__(self, filesystem, path, mode):
        self.filesystem = filesystem
        self.path = path
        self.mode = mode
        self.closed = False
        if mode == "rb":
            if path not in filesystem.files:
                raise OSError(2, "missing")
            self.data = bytearray(filesystem.files[path])
        elif mode == "wb":
            self.data = bytearray()
        else:
            raise OSError("unsupported mode")
        self.offset = 0

    def read(self, maximum=-1):
        if maximum is None or maximum < 0:
            maximum = len(self.data) - self.offset
        result = bytes(self.data[self.offset:self.offset + maximum])
        self.offset += len(result)
        return result

    def write(self, data):
        payload = bytes(data)
        self.data.extend(payload)
        return len(payload)

    def flush(self):
        return None

    def close(self):
        if not self.closed and self.mode == "wb":
            self.filesystem.files[self.path] = bytes(self.data)
        self.closed = True
        return None


class _MemoryFileSystem:
    def __init__(self):
        self.files = {}

    def open(self, path, mode):
        return _MemoryStream(self, path, mode)

    def stat(self, path):
        if path not in self.files:
            raise OSError(2, "missing")
        return (0, 0, 0, 0, 0, 0, len(self.files[path]), 0, 1, 0)

    def remove(self, path):
        if path not in self.files:
            raise OSError(2, "missing")
        del self.files[path]
        return None

    def rename(self, source, target):
        if source not in self.files:
            raise OSError(2, "missing")
        self.files[target] = self.files.pop(source)
        return None

    def sync(self):
        return None


class _FaultingCapsule(smoke._OwnershipCapsule):
    __slots__ = ("_fault_name", "_fault_armed")

    def __init__(self, fault_name):
        self._fault_armed = False
        self._fault_name = fault_name
        super().__init__()
        self._fault_armed = True

    def __setattr__(self, name, value):
        if (
            name not in ("_fault_name", "_fault_armed")
            and getattr(self, "_fault_armed", False)
            and name == self._fault_name
            and value is not None
        ):
            self._fault_armed = False
            raise MemoryError()
        return super().__setattr__(name, value)


class _InstrumentedRestRuntime:
    """Host-only proxy for one exact handler or response fault."""

    def __init__(self, runtime, response_mutator=None, failure=None):
        self._runtime = runtime
        self._response_mutator = response_mutator
        self._failure = failure

    @property
    def application(self):
        return self._runtime.application

    @property
    def security_policy(self):
        return self._runtime.security_policy

    def start(self):
        return self._runtime.start()

    def deinit(self):
        return self._runtime.deinit()

    def snapshot(self):
        return self._runtime.snapshot()

    def handle(self, request, peer_ip):
        if self._failure is not None:
            raise self._failure
        response = self._runtime.handle(request, peer_ip)
        if self._response_mutator is not None:
            self._response_mutator(response)
        return response


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


def _request(path, host, method="GET"):
    return "{} {} HTTP/1.1\r\nHost: {}\r\n\r\n".format(
        method, path, host
    ).encode("ascii")


def _wire_json(client):
    head, body = bytes(client.written).split(b"\r\n\r\n", 1)
    return head, json.loads(body.decode("utf-8"))


def _failure_diagnostics(output):
    lines = output.splitlines()
    start = lines.index(stage2.FULL_REST_PHONE_FAILURE_STAGE_TOKEN) + 1
    values = {}
    for line in lines[start:]:
        if line == stage2.FULL_REST_PHONE_FAIL_TOKEN:
            break
        name, value = line.split("=", 1)
        values[name] = value
    return values


class TestPhase8FullRestPhoneSmoke(unittest.TestCase):
    def setUp(self):
        board_config.WIFI_RADIO_APPROVED = False
        wifi_module._WIFI_LEASED = False
        wifi_module._WIFI_LEASE_POISONED = False

    def tearDown(self):
        board_config.WIFI_RADIO_APPROVED = False
        wifi_module._WIFI_LEASED = False
        wifi_module._WIFI_LEASE_POISONED = False

    def test_platform_guard_requires_exact_dfr0975u_build_tuple(self):
        fake_sys = types.SimpleNamespace(
            implementation=types.SimpleNamespace(
                name="micropython", version=(1, 28, 0)
            ),
            platform="esp32",
        )
        uname = types.SimpleNamespace(machine=smoke.EXPECTED_MACHINE_NAME)
        with mock.patch.object(smoke, "_sys", fake_sys), mock.patch(
            "os.uname", return_value=uname
        ):
            self.assertIs(smoke._verify_platform(board_config), True)
            with mock.patch.object(
                board_config, "MICROPYTHON_VARIANT", "SPIRAM"
            ):
                with self.assertRaisesRegex(RuntimeError, "profile differs"):
                    smoke._verify_platform(board_config)
            with mock.patch(
                "os.uname",
                return_value=types.SimpleNamespace(
                    machine="Generic ESP32 module"
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "identity differs"):
                    smoke._verify_platform(board_config)

    def test_hardware_lock_guard_checks_assigned_but_unapproved_routes(self):
        self.assertIs(smoke._verify_hardware_locks(board_config), True)
        for name, value in (
            ("UART_PROTOCOL_TX_ENABLED", True),
            ("UART_PINS_APPROVED", True),
            ("UART_TX_GATE_ACTIVE_LEVEL", 0),
            ("ONEWIRE_PIN", None),
            ("I2C_SCL_PIN", None),
        ):
            with self.subTest(name=name), mock.patch.object(
                board_config, name, value
            ):
                with self.assertRaises(RuntimeError):
                    smoke._verify_hardware_locks(board_config)

    @staticmethod
    def _wifi_runtime(clock):
        return (
            sys,
            board_config,
            NetworkManager,
            wifi_module,
            open_wifi_from_board_config,
            clock.ticks_ms,
            clock.ticks_add,
            clock.ticks_diff,
            clock.sleep_ms,
        )

    @staticmethod
    def _rest_core(
        listener,
        response_mutator=None,
        runtime_failure=None,
        socket_factory=None,
    ):
        if response_mutator is None and runtime_failure is None:
            runtime_builder = build_rest_runtime
        else:
            def runtime_builder(*arguments, **keywords):
                return _InstrumentedRestRuntime(
                    build_rest_runtime(*arguments, **keywords),
                    response_mutator=response_mutator,
                    failure=runtime_failure,
                )
        return (
            runtime_builder,
            build_rest_http_server,
            Factory(listener) if socket_factory is None else socket_factory,
        )

    def _execute(
        self,
        fake,
        listener,
        filesystem=None,
        clock=None,
        stage1_memory=(150000, 140000, 130000, 120000, 110000),
        stage2_memory=(90000, 85000, 80000, 75000, 70000, 95000, 90000),
        stage2_loader=None,
        seam_loader=None,
        prepare_loader=None,
        diagnostics_loader=None,
        unload=None,
        unload_module=None,
        capsule=None,
        response_mutator=None,
        runtime_failure=None,
        encoder_failure=None,
        final_socket_factory=None,
        diagnostic_memory=None,
    ):
        if filesystem is None:
            filesystem = _MemoryFileSystem()
        if clock is None:
            clock = _Clock()
        if stage2_loader is None:
            stage2_loader = lambda: stage2
        if seam_loader is None:
            seam_loader = lambda: stage2_seam
        if prepare_loader is None:
            prepare_loader = lambda: stage2_prepare
        if diagnostics_loader is None:
            diagnostics_loader = lambda: stage2_diagnostics
        if len(tuple(stage1_memory)) != 5:
            raise ValueError("stage1_memory must contain five checkpoints")
        stage2_memory = tuple(stage2_memory)
        if len(stage2_memory) != 7:
            raise ValueError("stage2_memory must contain seven checkpoints")

        output = io.StringIO()
        result = None
        error = None
        patches = (
            mock.patch.dict(sys.modules, {"network": fake}),
            mock.patch.object(smoke, "_load_stage1", return_value=stage1),
            mock.patch.object(
                smoke, "_require_stage2_unloaded", return_value=True
            ),
            mock.patch.object(smoke, "_require_proof_cold", return_value=True),
            mock.patch.object(smoke, "_load_stage2", side_effect=stage2_loader),
            mock.patch.object(
                smoke, "_load_stage2_seam", side_effect=seam_loader
            ),
            mock.patch.object(
                smoke, "_load_stage2_prepare", side_effect=prepare_loader
            ),
            mock.patch.object(
                smoke,
                "_load_stage2_diagnostics",
                side_effect=diagnostics_loader,
            ),
            mock.patch.object(
                stage2,
                "_load_failure_diagnostics",
                return_value=stage2_diagnostics,
            ),
            mock.patch.object(
                stage1,
                "_load_wifi_runtime",
                return_value=self._wifi_runtime(clock),
            ),
            mock.patch.object(stage1, "_verify_frozen_origins", return_value=True),
            mock.patch.object(stage1, "_memory_free", side_effect=stage1_memory),
            mock.patch.object(
                stage2_prepare,
                "_load_rest_runtime",
                return_value=self._rest_core(
                    listener,
                    response_mutator=response_mutator,
                    runtime_failure=runtime_failure,
                    socket_factory=final_socket_factory,
                ),
            ),
            mock.patch.object(
                stage2_prepare, "_verify_frozen_origins", return_value=True
            ),
            mock.patch.object(
                stage2_prepare, "_storage_filesystem", return_value=filesystem
            ),
            mock.patch.object(
                stage2_seam, "memory_free", side_effect=stage2_memory[:4]
            ),
            mock.patch.object(
                stage2, "_memory_free", side_effect=stage2_memory[4:]
            ),
            mock.patch.object(
                stage2_seam, "_load_proof", side_effect=stage2_loader
            ),
            mock.patch.object(
                stage2_seam._os,
                "urandom",
                side_effect=lambda count: bytes(range(count)),
            ),
            mock.patch.object(smoke, "_verify_platform", return_value=True),
        )
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            if encoder_failure is not None:
                stack.enter_context(mock.patch.object(
                    http_adapter,
                    "encode_json_bytes",
                    side_effect=encoder_failure,
                ))
            if diagnostic_memory is not None:
                stack.enter_context(mock.patch.object(
                    stage2,
                    "_diagnostic_memory_free",
                    side_effect=diagnostic_memory,
                ))
                stack.enter_context(mock.patch.object(
                    stage2_diagnostics,
                    "memory_free_no_collect",
                    side_effect=diagnostic_memory,
                ))
            if capsule is not None:
                stack.enter_context(mock.patch.object(
                    smoke, "_OwnershipCapsule", return_value=capsule
                ))
            if unload is not None:
                stack.enter_context(mock.patch.object(
                    smoke, "_unload_stage1", side_effect=unload
                ))
            if unload_module is not None:
                stack.enter_context(mock.patch.object(
                    smoke, "_unload_module", side_effect=unload_module
                ))
            with contextlib.redirect_stdout(output):
                try:
                    result = smoke.run(
                        smoke.FULL_REST_PHONE_CONFIRMATION,
                        _TEST_PASSWORD,
                        60,
                    )
                except BaseException as caught:
                    error = caught
        return result, error, output.getvalue(), clock, filesystem

    def _new_flow(
        self,
        *,
        accept_events=None,
        recv_events=None,
        send_events=None,
        response_mutator=None,
        runtime_failure=None,
        encoder_failure=None,
        stage1_memory=(150000, 140000, 130000, 120000, 110000),
        stage2_memory=(90000, 85000, 80000, 75000, 70000, 95000, 90000),
    ):
        fake = _fake_network()
        client_calls = _script_ap_clients(fake, (0, 1, 1, 1))
        client = None
        if accept_events is None:
            client = FakeClientSocket(
                recv_events=(
                    [_request(smoke.STATUS_PATH, smoke.AP_IP)]
                    if recv_events is None else recv_events
                ),
                send_events=send_events,
                name="status",
            )
            accept_events = [client]
        listener = FakeListener(accept_events=accept_events)
        execution = self._execute(
            fake,
            listener,
            response_mutator=response_mutator,
            runtime_failure=runtime_failure,
            encoder_failure=encoder_failure,
            stage1_memory=stage1_memory,
            stage2_memory=stage2_memory,
        )
        return execution, fake, client_calls, listener, client

    def _assert_safe_cleanup(
        self, fake, listener, filesystem, clients=()
    ):
        if listener.blocking_values:
            self.assertTrue(listener.closed)
        for client in clients:
            if client is not None:
                self.assertTrue(client.closed)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
        self.assertIs(wifi_module._WIFI_LEASED, False)
        self.assertIs(wifi_module._WIFI_LEASE_POISONED, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
        for path in stage2._storage_paths():
            self.assertNotIn(path, filesystem.files)

    def _assert_failed(
        self,
        execution,
        fake,
        listener,
        clients=(),
        secret=None,
    ):
        result, error, output, _, filesystem = execution
        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(str(error), "Phase-8 full REST phone smoke failed")
        self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
        self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
        rendered = output + repr(error)
        self.assertNotIn(_TEST_PASSWORD, rendered)
        if secret is not None:
            self.assertNotIn(secret, rendered)
        diagnostics = _failure_diagnostics(output)
        self.assertEqual(diagnostics["cleanup_success"], "1")
        self._assert_safe_cleanup(fake, listener, filesystem, clients)
        return diagnostics

    def test_complete_response_is_one_listener_full_product_pass(self):
        execution, fake, client_calls, listener, client = self._new_flow()
        result, error, output, _, filesystem = execution

        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(result["http_listener_count"], 1)
        self.assertEqual(result["http_listener_port"], 80)
        self.assertEqual(result["listener_factory_returned"], 1)
        self.assertEqual(result["listener_setblocking_returned"], 1)
        self.assertEqual(result["listener_bind_returned"], 1)
        self.assertEqual(result["listener_listen_returned"], 1)
        self.assertIs(
            result["association_confirmed_before_product_imports"], True
        )
        self.assertIs(result["association_confirmed_after_bind"], True)
        self.assertEqual(result["routed_requests"], 1)
        self.assertEqual(result["valid_status_requests"], 1)
        self.assertEqual(result["rest_application_entered"], 1)
        self.assertEqual(result["rest_application_returned"], 1)
        self.assertEqual(result["status_data_completed"], 1)
        self.assertEqual(result["status_validator_accepted"], 1)
        self.assertEqual(result["status_validator_rejected"], 0)
        self.assertIs(result["response_encoding_observed"], True)
        self.assertEqual(result["target_bytes_written"], len(client.written))
        self.assertEqual(
            result["target_bytes_written"],
            result["expected_response_wire_length"],
        )
        self.assertGreater(result["response_body_length"], 0)
        self.assertEqual(result["target_send_would_blocks"], 0)
        self.assertEqual(result["peer_eof_events"], 0)
        self.assertEqual(result["target_zero_send_events"], 0)
        self.assertIs(result["client_disconnect_observed"], False)
        self.assertEqual(
            result["target_send_attempts"],
            result["target_successful_send_calls"],
        )
        self.assertEqual(result["completed_responses"], 1)
        self.assertIs(result["write_timeout"], False)
        self.assertEqual(result["target_wire_completions"], 1)
        self.assertIs(result["target_socket_closed"], True)
        self.assertIs(result["client_connection_closed"], True)
        self.assertIs(result["cleanup_success"], True)
        self.assertEqual(listener.blocking_values, [False])
        self.assertEqual(listener.bind_values, [(smoke.AP_IP, 80)])
        self.assertEqual(listener.listen_values, [2])
        self.assertEqual(listener.close_calls, 1)
        self.assertGreaterEqual(client_calls["count"], 3)

        head, body = _wire_json(client)
        self.assertIn(b"HTTP/1.1 200 OK", head)
        self.assertNotIn(b"X-Landy-Phase8", head)
        self.assertEqual(body["api_version"], 1)
        self.assertIs(body["configuration"]["setup_complete"], False)
        self.assertIs(body["heater"]["requested"]["on"], False)

        expected_heaps = {
            "memory_before": 150000,
            "memory_after_wifi_factory": 130000,
            "memory_after_ap_ready": 120000,
            "memory_after_client_association": 110000,
            "memory_after_product_imports": 90000,
            "memory_after_configuration_adoption": 85000,
            "memory_before_http_start": 80000,
            "memory_after_proof_before_listen": 75000,
            "memory_after_http_bind": 70000,
            "memory_after_response": 95000,
            "memory_after_cleanup": 90000,
        }
        for name, value in expected_heaps.items():
            self.assertEqual(result[name], value)
            self.assertGreaterEqual(value, 32 * 1024)

        lines = output.splitlines()
        self.assertLess(
            lines.index(smoke.FULL_REST_PHONE_AP_READY_TOKEN),
            lines.index(smoke.FULL_REST_PHONE_CLIENT_TOKEN),
        )
        self.assertLess(
            lines.index(smoke.FULL_REST_PHONE_CLIENT_TOKEN),
            lines.index(smoke.FULL_REST_PHONE_READY_TOKEN),
        )
        self.assertEqual(lines[-1], smoke.FULL_REST_PHONE_PASS_TOKEN)
        self.assertNotIn(":8080", output)
        self.assertNotIn(_TEST_PASSWORD, output + repr(result))
        self.assertNotIn(stage2.FULL_REST_PHONE_FAILURE_STAGE_TOKEN, output)
        self._assert_safe_cleanup(fake, listener, filesystem, (client,))

    def test_accept_failure_boundary(self):
        secret = "accept-secret-" + _TEST_PASSWORD
        execution, fake, _, listener, _ = self._new_flow(
            accept_events=[OSError(12, secret)]
        )
        diagnostics = self._assert_failed(
            execution, fake, listener, secret=secret
        )
        self.assertEqual(diagnostics["stage"], "observe_http_transport")
        self.assertEqual(diagnostics["http_accept_actions"], "1")
        self.assertEqual(diagnostics["http_accepted"], "0")
        self.assertEqual(diagnostics["listener_errno"], "12")
        self.assertEqual(diagnostics["http_last_error"], "accept_failed")
        self.assertEqual(diagnostics["http_recv_actions"], "0")
        self.assertEqual(diagnostics["parsed_requests"], "0")
        self.assertEqual(diagnostics["target_send_attempts"], "0")
        self.assertEqual(diagnostics["target_bytes_written"], "0")

    def test_receive_failure_boundary(self):
        secret = "recv-secret-" + _TEST_PASSWORD
        execution, fake, _, listener, client = self._new_flow(
            recv_events=[OSError(103, secret)]
        )
        diagnostics = self._assert_failed(
            execution, fake, listener, (client,), secret
        )
        self.assertEqual(diagnostics["http_accepted"], "1")
        self.assertEqual(diagnostics["http_recv_actions"], "1")
        self.assertEqual(diagnostics["http_last_error"], "client_recv_failed")
        self.assertEqual(diagnostics["parsed_requests"], "0")
        self.assertEqual(diagnostics["rest_application_entered"], "0")
        self.assertEqual(diagnostics["target_send_attempts"], "0")
        self.assertEqual(diagnostics["observer_accepted"], "1")
        self.assertEqual(diagnostics["observer_closed"], "1")

    def test_rest_application_failure_boundary(self):
        secret = "rest-secret-" + _TEST_PASSWORD
        execution, fake, _, listener, client = self._new_flow(
            runtime_failure=RuntimeError(secret)
        )
        diagnostics = self._assert_failed(
            execution, fake, listener, (client,), secret
        )
        self.assertEqual(diagnostics["parsed_requests"], "1")
        self.assertEqual(diagnostics["rest_application_entered"], "1")
        self.assertEqual(diagnostics["rest_application_returned"], "0")
        self.assertEqual(diagnostics["status_data_completed"], "0")
        self.assertEqual(diagnostics["status_validator_result"], "not_run")
        self.assertEqual(
            diagnostics["http_last_error"], "application_handle_failed"
        )
        self.assertEqual(diagnostics["response_encoding_completed"], "0")
        self.assertEqual(diagnostics["target_bytes_written"], "0")

    def test_status_validator_rejection_boundary(self):
        def corrupt_status(response):
            response.body["request_id"] = "invalid"

        execution, fake, _, listener, client = self._new_flow(
            response_mutator=corrupt_status
        )
        diagnostics = self._assert_failed(
            execution, fake, listener, (client,)
        )
        self.assertEqual(diagnostics["parsed_requests"], "1")
        self.assertEqual(diagnostics["rest_application_entered"], "1")
        self.assertEqual(diagnostics["rest_application_returned"], "1")
        self.assertEqual(diagnostics["status_data_completed"], "1")
        self.assertEqual(diagnostics["status_validator_result"], "rejected")
        self.assertEqual(diagnostics["status_successful"], "0")
        self.assertEqual(diagnostics["status_rejected"], "1")
        self.assertEqual(diagnostics["response_encoding_completed"], "0")
        self.assertEqual(diagnostics["target_completions"], "0")
        self.assertEqual(diagnostics["target_failures"], "1")
        head, body = _wire_json(client)
        self.assertIn(b"HTTP/1.1 404 Not Found", head)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_response_encode_failure_boundary(self):
        secret = "encode-secret-" + _TEST_PASSWORD
        execution, fake, _, listener, client = self._new_flow(
            encoder_failure=RuntimeError(secret)
        )
        diagnostics = self._assert_failed(
            execution, fake, listener, (client,), secret
        )
        self.assertEqual(diagnostics["status_validator_result"], "accepted")
        self.assertEqual(diagnostics["status_data_completed"], "1")
        self.assertEqual(diagnostics["http_last_error"], "response_contract_failed")
        self.assertEqual(diagnostics["response_encoding_completed"], "0")
        self.assertEqual(diagnostics["target_send_attempts"], "0")
        self.assertEqual(diagnostics["target_bytes_written"], "0")
        self.assertEqual(bytes(client.written), b"")

    def test_zero_byte_send_boundary(self):
        execution, fake, _, listener, client = self._new_flow(
            send_events=[0]
        )
        diagnostics = self._assert_failed(
            execution, fake, listener, (client,)
        )
        self.assertEqual(diagnostics["status_validator_result"], "accepted")
        self.assertEqual(diagnostics["response_encoding_completed"], "1")
        self.assertGreater(int(diagnostics["expected_response_wire_length"]), 0)
        self.assertEqual(diagnostics["target_send_attempts"], "1")
        self.assertEqual(diagnostics["target_successful_send_calls"], "0")
        self.assertEqual(diagnostics["target_bytes_written"], "0")
        self.assertEqual(diagnostics["target_send_would_blocks"], "0")
        self.assertEqual(diagnostics["peer_eof_events"], "0")
        self.assertEqual(diagnostics["target_zero_send_events"], "1")
        self.assertEqual(diagnostics["client_disconnect_observed"], "1")
        self.assertEqual(diagnostics["http_last_error"], "client_send_closed")
        self.assertEqual(diagnostics["target_failures"], "1")

    def test_peer_eof_disconnect_boundary(self):
        execution, fake, _, listener, client = self._new_flow(
            recv_events=[b""]
        )
        diagnostics = self._assert_failed(
            execution, fake, listener, (client,)
        )
        self.assertEqual(diagnostics["http_accepted"], "1")
        self.assertEqual(diagnostics["http_recv_actions"], "1")
        self.assertEqual(diagnostics["http_parse_errors"], "1")
        self.assertEqual(diagnostics["http_last_error"], "truncated_request")
        self.assertEqual(diagnostics["parsed_requests"], "0")
        self.assertEqual(diagnostics["peer_eof_events"], "1")
        self.assertEqual(diagnostics["target_zero_send_events"], "0")
        self.assertEqual(diagnostics["client_disconnect_observed"], "1")
        self.assertEqual(diagnostics["target_socket_closed"], "0")

    def test_partial_response_boundary(self):
        execution, fake, _, listener, client = self._new_flow(
            send_events=[17, 0]
        )
        diagnostics = self._assert_failed(
            execution, fake, listener, (client,)
        )
        expected = int(diagnostics["expected_response_wire_length"])
        self.assertGreater(expected, 17)
        self.assertEqual(diagnostics["response_encoding_completed"], "1")
        self.assertEqual(diagnostics["target_send_attempts"], "2")
        self.assertEqual(diagnostics["target_successful_send_calls"], "1")
        self.assertEqual(diagnostics["target_bytes_written"], "17")
        self.assertEqual(diagnostics["target_send_would_blocks"], "0")
        self.assertEqual(diagnostics["http_completed"], "0")
        self.assertEqual(diagnostics["target_completions"], "0")
        self.assertEqual(diagnostics["target_failures"], "1")
        self.assertEqual(len(client.written), 17)

    def test_repeated_would_block_then_complete_response(self):
        execution, fake, _, listener, client = self._new_flow(
            send_events=[OSError(11), OSError(11), OSError(11)]
        )
        result, error, output, _, filesystem = execution
        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(result["target_send_would_blocks"], 3)
        self.assertEqual(
            result["target_send_attempts"],
            result["target_successful_send_calls"] + 3,
        )
        self.assertEqual(
            result["target_bytes_written"],
            result["expected_response_wire_length"],
        )
        self.assertEqual(result["completed_responses"], 1)
        self.assertEqual(result["observed_http_timeouts"], 0)
        self.assertEqual(output.splitlines()[-1], smoke.FULL_REST_PHONE_PASS_TOKEN)
        self._assert_safe_cleanup(fake, listener, filesystem, (client,))

    def test_write_timeout_boundary(self):
        execution, fake, _, listener, client = self._new_flow(
            send_events=[OSError(11)] * 128
        )
        diagnostics = self._assert_failed(
            execution, fake, listener, (client,)
        )
        attempts = int(diagnostics["target_send_attempts"])
        self.assertGreater(attempts, 1)
        self.assertEqual(diagnostics["response_encoding_completed"], "1")
        self.assertEqual(diagnostics["target_successful_send_calls"], "0")
        self.assertEqual(diagnostics["target_bytes_written"], "0")
        self.assertEqual(int(diagnostics["target_send_would_blocks"]), attempts)
        self.assertEqual(diagnostics["write_timeout"], "1")
        self.assertEqual(diagnostics["http_last_error"], "write_timeout")
        self.assertEqual(diagnostics["http_completed"], "0")
        self.assertEqual(diagnostics["target_failures"], "1")

    def test_all_heap_boundaries_fail_closed_at_32767(self):
        stage1_cases = (
            ("wifi_factory", 2),
            ("ap_ready", 3),
            ("association", 4),
        )
        for name, index in stage1_cases:
            with self.subTest(stage=name):
                values = [150000, 140000, 130000, 120000, 110000]
                values[index] = 32767
                execution, fake, _, listener, _ = self._new_flow(
                    stage1_memory=tuple(values)
                )
                result, error, output, _, filesystem = execution
                self.assertIsNone(result)
                self.assertIsInstance(error, RuntimeError)
                self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
                self.assertEqual(listener.bind_values, [])
                self._assert_safe_cleanup(fake, listener, filesystem)

        for index, name in enumerate((
            "product_imports",
            "configuration_adoption",
            "pre_bind",
            "proof_before_listen",
            "http_bind",
            "post_response",
            "post_cleanup",
        )):
            with self.subTest(stage=name):
                values = [90000, 85000, 80000, 75000, 70000, 95000, 90000]
                values[index] = 32767
                execution, fake, _, listener, client = self._new_flow(
                    stage2_memory=tuple(values)
                )
                result, error, output, _, filesystem = execution
                self.assertIsNone(result)
                self.assertIsInstance(error, RuntimeError)
                self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
                self._assert_safe_cleanup(
                    fake,
                    listener,
                    filesystem,
                    (client,) if listener.accept_calls else (),
                )

    def test_success_cleanup_order_is_http_observer_gate_rest_radio_files(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        client = FakeClientSocket(
            recv_events=[_request(smoke.STATUS_PATH, smoke.AP_IP)]
        )
        listener = FakeListener(accept_events=[client])
        events = []

        def record(label, function):
            def wrapped(*arguments, **keywords):
                events.append(label)
                return function(*arguments, **keywords)
            return wrapped

        with mock.patch.object(
            stage2,
            "_cleanup_http_server",
            side_effect=record("http", stage2._cleanup_http_server),
        ), mock.patch.object(
            stage2,
            "_cleanup_observed_sockets",
            side_effect=record("observer", stage2._cleanup_observed_sockets),
        ), mock.patch.object(
            stage2_seam.DeferredReadOnlyRuntime,
            "disarm",
            autospec=True,
            side_effect=record(
                "gate", stage2_seam.DeferredReadOnlyRuntime.disarm
            ),
        ), mock.patch.object(
            stage2,
            "_cleanup_rest_runtime",
            side_effect=record("rest", stage2._cleanup_rest_runtime),
        ), mock.patch.object(
            smoke,
            "_cleanup_radio",
            side_effect=record("radio", smoke._cleanup_radio),
        ), mock.patch.object(
            stage2,
            "_remove_exact_files",
            side_effect=record("files", stage2._remove_exact_files),
        ):
            result, error, output, _, filesystem = self._execute(
                fake, listener
            )

        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(
            events,
            ["http", "observer", "gate", "rest", "radio", "files"],
        )
        self.assertEqual(output.splitlines()[-1], smoke.FULL_REST_PHONE_PASS_TOKEN)
        self._assert_safe_cleanup(fake, listener, filesystem, (client,))

    def test_target_socket_binding_and_exact_wire_observer_fail_closed(self):
        observer = stage2._SocketResponseObserver()
        raw = FakeClientSocket(recv_events=[b"request"])
        client = observer.claim_client(raw)
        self.assertEqual(client.recv(256), b"request")
        observer.claim_status_request("192.168.4.2")
        self.assertIs(observer.target_client, client)
        self.assertEqual(observer.target_peer_ip, "192.168.4.2")

        body = b"x"
        wire = (
            stage2._TARGET_RESPONSE_PREFIX
            + b"1\r"
            + stage2._TARGET_RESPONSE_SUFFIX
            + body
        )
        self.assertEqual(client.send(wire), len(wire))
        self.assertIsNone(client.close())
        self.assertIs(observer.response_encoding_observed, True)
        self.assertEqual(observer.expected_response_wire_length, len(wire))
        self.assertEqual(observer.bytes_written, len(wire))
        self.assertEqual(observer.target_headers, 1)
        self.assertEqual(observer.target_wires, 1)
        self.assertEqual(observer.target_completions, 1)
        self.assertEqual(observer.target_failures, 0)

        missing = stage2._SocketResponseObserver()
        with self.assertRaises(RuntimeError):
            missing.claim_status_request("192.168.4.2")
        self.assertIs(missing.faulted, True)

        partial = stage2._SocketResponseObserver()
        raw = FakeClientSocket(recv_events=[b"request"], send_events=[9])
        client = partial.claim_client(raw)
        client.recv(256)
        partial.claim_status_request("192.168.4.2")
        self.assertEqual(client.send(wire), 9)
        self.assertIsNone(client.close())
        self.assertEqual(partial.bytes_written, 9)
        self.assertEqual(partial.target_completions, 0)
        self.assertEqual(partial.target_failures, 1)

    def test_deferred_gate_is_fail_closed_and_context_excludes_server(self):
        class Security:
            @staticmethod
            def snapshot():
                return {"started": True, "mutation_api_available": True}

        runtime = types.SimpleNamespace(
            application=object(), security_policy=Security()
        )
        gateway = types.SimpleNamespace(
            handle=lambda request, peer_ip: (request, peer_ip)
        )
        state = stage2_seam.Stage2State()

        self.assertNotIn("server", stage2_seam.PreparedContext.__slots__)
        with self.assertRaises(RuntimeError):
            state.gate.handle("request", "192.168.4.2")
        state.gate.seal_security(runtime)
        self.assertIs(state.gate.application, state.gate)
        with self.assertRaises(RuntimeError):
            state.gate.handle("request", "192.168.4.2")
        state.gate.arm(gateway)
        self.assertEqual(
            state.gate.handle("request", "192.168.4.2"),
            ("request", "192.168.4.2"),
        )
        state.gate.disarm()
        with self.assertRaises(RuntimeError):
            state.gate.handle("request", "192.168.4.2")

    def test_listener_cannot_accept_before_proof_arm(self):
        class Security:
            @staticmethod
            def snapshot():
                return {"started": True, "mutation_api_available": True}

        state = stage2_seam.Stage2State()
        runtime = types.SimpleNamespace(
            application=object(), security_policy=Security()
        )
        state.context.rest_runtime = runtime
        state.gate.seal_security(runtime)
        raw = FakeListener()
        factory = stage2_seam.LateSocketFactory(lambda: raw, state)
        listener = factory()
        listener.setblocking(False)
        listener.bind((smoke.AP_IP, 80))
        with self.assertRaises(RuntimeError):
            listener.accept()
        self.assertEqual(raw.accept_calls, 0)
        self.assertIs(factory._faulted, True)
        self.assertIsNone(factory.deinit())
        self.assertTrue(raw.closed)

    def test_confirmation_import_and_ap_stage_are_inert_and_http_free(self):
        class EqualitySpoof:
            def __eq__(self, other):
                return True

        for confirmation in (None, EqualitySpoof(), "wrong"):
            with self.assertRaises(RuntimeError):
                smoke.run(confirmation, _TEST_PASSWORD, 60)

        with open(smoke.__file__, "r", encoding="utf-8") as stream:
            coordinator_tree = ast.parse(stream.read())
        top_imports = []
        for node in coordinator_tree.body:
            if isinstance(node, ast.Import):
                top_imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                top_imports.append(node.module)
        self.assertEqual(top_imports, ["gc", "sys"])

        with open(stage1.__file__, "r", encoding="utf-8") as stream:
            stage1_source = stream.read()
        stage1_tree = ast.parse(stage1_source)
        imported = set()
        for node in ast.walk(stage1_tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertNotIn("socket", imported)
        self.assertFalse(any(name.startswith("adapters.") for name in imported))
        self.assertFalse(any("http" in name for name in imported))
        self.assertNotIn(":8080", stage1_source)

        fake_network = _fake_network()
        with mock.patch.dict(sys.modules, {"network": fake_network}), mock.patch.object(
            board_config, "WIFI_RADIO_APPROVED", False
        ):
            namespace = runpy.run_path(smoke.__file__, run_name="not_main")
        self.assertIn("run", namespace)
        self.assertIs(fake_network.interfaces[fake_network.IF_AP].enabled, False)
        self.assertIs(fake_network.interfaces[fake_network.IF_STA].enabled, False)

    def test_http_closure_stays_late_and_upload_set_is_self_contained(self):
        required_late = {
            "adapters.micropython_http_server",
            "services.http_protocol",
            "services.strict_json",
        }
        self.assertLessEqual(required_late, set(smoke._LATE_ONLY_MODULES))
        for module_name in required_late:
            with self.subTest(module_name=module_name):
                with self.assertRaises(RuntimeError):
                    smoke._require_cold_late_modules({module_name: object()})

        origins = stage1._NETWORK_FROZEN_ORIGINS
        self.assertEqual(
            set(name for name, _ in origins),
            {"app.network_manager", "hardware.micropython_wifi"},
        )
        self.assertFalse(hasattr(stage1, "_HTTP_FROZEN_ORIGINS"))

        manifest_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "firmware",
            "phase8_frozen",
            "manifest.py",
        )
        declarations = []

        def package(name, base_path=None, opt=None, files=()):
            declarations.append((name, base_path, opt, tuple(files)))

        with open(manifest_path, "rb") as stream:
            source = stream.read()
        exec(compile(source, manifest_path, "exec"), {
            "include": lambda path: None,
            "package": package,
        })
        frozen_modules = set()
        for package_name, base_path, optimization, files in declarations:
            self.assertEqual(base_path, "../..")
            self.assertEqual(optimization, 0)
            for filename in files:
                tail = filename[:-3].replace("/", ".")
                frozen_modules.add(
                    package_name if tail == "__init__"
                    else package_name + "." + tail
                )

        isolated_upload = {
            smoke._STAGE1_MODULE,
            smoke._STAGE2_SEAM_MODULE,
            smoke._STAGE2_PREPARE_MODULE,
            smoke._STAGE2_MODULE,
            smoke._STAGE2_DIAGNOSTICS_MODULE,
            "tools.phase8_full_rest_phone_smoke",
        }
        self.assertEqual(len(isolated_upload), 6)
        self.assertFalse(frozen_modules & isolated_upload)
        frozen_contract = (
            stage1._NETWORK_FROZEN_ORIGINS
            + stage2_prepare._PRODUCT_FROZEN_ORIGINS
            + stage2_prepare._REST_FROZEN_ORIGINS
        )
        for module_name, origin in frozen_contract:
            self.assertIn(module_name, frozen_modules)
            self.assertEqual(origin, module_name.replace(".", "/") + ".py")

    def test_failure_heap_diagnostics_never_collect(self):
        for module, reader in (
            (stage2, stage2._diagnostic_memory_free),
            (stage2_diagnostics, stage2_diagnostics.memory_free_no_collect),
        ):
            with self.subTest(module=module.__name__), mock.patch.object(
                module._gc, "collect"
            ) as collect, mock.patch.object(
                module._gc, "mem_free", return_value=77777, create=True
            ) as mem_free:
                self.assertEqual(reader(), 77777)
            collect.assert_not_called()
            mem_free.assert_called_once_with()

    def test_live_and_stored_configuration_mismatch_never_binds(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        listener = FakeListener()

        def prepare_with_mismatch(capsule, state, password, window_seconds):
            capsule.live_network_configuration["access_point"]["password"] = (
                "Different!42"
            )
            return stage2_prepare.prepare(
                capsule, state, password, window_seconds
            )

        prepare_proxy = types.SimpleNamespace(prepare=prepare_with_mismatch)
        result, error, output, _, filesystem = self._execute(
            fake,
            listener,
            prepare_loader=lambda: prepare_proxy,
        )
        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(listener.bind_values, [])
        self.assertNotIn(smoke.FULL_REST_PHONE_READY_TOKEN, output)
        self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
        self._assert_safe_cleanup(fake, listener, filesystem)

    def test_capsule_publication_faults_salvage_radio_ownership(self):
        for fault_name in ("port", "network_manager"):
            with self.subTest(fault_name=fault_name):
                fake = _fake_network()
                _script_ap_clients(fake, (0,))
                listener = FakeListener()
                capsule = _FaultingCapsule(fault_name)
                result, error, output, _, filesystem = self._execute(
                    fake, listener, capsule=capsule
                )
                self.assertIsNone(result)
                self.assertIsInstance(error, MemoryError)
                self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
                self._assert_safe_cleanup(fake, listener, filesystem)

    def test_outer_stage1_failure_reports_successful_cleanup(self):
        values = (150000, 140000, 130000, 120000, 32767)
        execution, fake, _, listener, _ = self._new_flow(
            stage1_memory=values
        )
        result, error, output, _, filesystem = execution
        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        diagnostics = _failure_diagnostics(output)
        self.assertEqual(diagnostics["stage"], "stage1_confirm_association")
        self.assertEqual(diagnostics["stage1_client_seen"], "1")
        self.assertEqual(diagnostics["stage1_ap_clients"], "1")
        self.assertEqual(diagnostics["cleanup_success"], "1")
        self.assertEqual(listener.bind_values, [])
        self._assert_safe_cleanup(fake, listener, filesystem)

    def test_failed_full_seam_cleanup_is_not_masked_by_radio_cleanup(self):
        class FailingCertificationSeam:
            def __init__(self):
                self.state = None
                self.real_cleanup_succeeded = False

            def __getattr__(self, name):
                return getattr(stage2_seam, name)

            def Stage2State(self):
                self.state = stage2_seam.Stage2State()
                return self.state

            def fallback_cleanup(self, capsule, state):
                self.real_cleanup_succeeded = stage2_seam.fallback_cleanup(
                    capsule, state
                )
                return False

        proxy = FailingCertificationSeam()
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        listener = FakeListener()
        capsule = smoke._OwnershipCapsule()
        result, error, output, _, filesystem = self._execute(
            fake,
            listener,
            seam_loader=lambda: proxy,
            stage2_loader=MemoryError,
            capsule=capsule,
        )
        self.assertIsNone(result)
        self.assertIsInstance(error, MemoryError)
        self.assertIs(proxy.real_cleanup_succeeded, True)
        diagnostics = _failure_diagnostics(output)
        self.assertEqual(diagnostics["cleanup_success"], "0")
        self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
        self._assert_safe_cleanup(fake, listener, filesystem)

    def test_mutation_route_is_rejected_without_rest_or_target_claim(self):
        class Runtime:
            def __init__(self):
                self.calls = 0

            def handle(self, request, peer_ip):
                self.calls += 1
                raise AssertionError("mutation reached RestApplication")

        observer = stage2._SocketResponseObserver()
        raw = FakeClientSocket(recv_events=[b"request"])
        client = observer.claim_client(raw)
        client.recv(256)
        runtime = Runtime()
        secret = bytearray(range(32))
        gateway = stage2._ReadOnlyStatusGateway(
            runtime, observer, _TEST_PASSWORD, secret
        )
        request = types.SimpleNamespace(
            path="/api/v1/heater/stop",
            target="/api/v1/heater/stop",
            query=None,
            host=smoke.AP_IP,
            method="POST",
        )
        response = gateway.handle(request, "192.168.4.2")
        self.assertEqual(response.status, 404)
        self.assertEqual(runtime.calls, 0)
        self.assertEqual(gateway.routed_requests, 1)
        self.assertEqual(gateway.rejected_requests, 1)
        self.assertIsNone(observer.current_recv_client)
        self.assertIsNone(observer.target_client)
        self.assertEqual(observer.send_attempts, 0)
        gateway.clear_secret()
        self.assertNotIn(_TEST_PASSWORD, repr(response.body))
        self.assertNotIn(_TEST_CSRF_TOKEN_HEX, repr(response.body))


if __name__ == "__main__":
    unittest.main()
