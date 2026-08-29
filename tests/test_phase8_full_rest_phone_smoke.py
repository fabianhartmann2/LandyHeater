import ast
import contextlib
import inspect
import io
import json
import os
import runpy
import sys
import types
import unittest
from unittest import mock

import board_config
from adapters.micropython_http_server import MicroPythonHTTPServer
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

# Retain a test handle without making the failure-only module resident on
# success paths.  Production loads this module only after cleanup.
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


class _MutatingRestRuntime:
    def __init__(self, runtime, mutator):
        self._runtime = runtime
        self._mutator = mutator

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
        response = self._runtime.handle(request, peer_ip)
        self._mutator(response)
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
    def _probe_builder(listener):
        def build(handler, address, **keywords):
            if "socket_factory" not in keywords:
                keywords["socket_factory"] = Factory(listener)
            return MicroPythonHTTPServer(
                handler,
                address,
                **keywords
            )
        return build

    @staticmethod
    def _rest_core(listener, response_mutator=None, socket_factory=None):
        if response_mutator is None:
            runtime_builder = build_rest_runtime
        else:
            def build(*arguments, **keywords):
                return _MutatingRestRuntime(
                    build_rest_runtime(*arguments, **keywords),
                    response_mutator,
                )
            runtime_builder = build
        return (
            runtime_builder,
            build_rest_http_server,
            Factory(listener) if socket_factory is None else socket_factory
        )

    def _execute(
        self,
        fake,
        probe_listener,
        final_listener,
        filesystem=None,
        clock=None,
        stage1_memory=(150000, 140000, 130000, 120000, 110000, 105000, 100000),
        stage2_memory=(
            90000, 85000, 80000, 75000, 70000, 95000, 90000,
        ),
        stage2_loader=None,
        seam_loader=None,
        prepare_loader=None,
        diagnostics_loader=None,
        unload=None,
        unload_module=None,
        capsule=None,
        response_mutator=None,
        stage1_cleanup=None,
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
        stage2_memory = tuple(stage2_memory)
        if len(stage2_memory) != 7:
            raise ValueError("stage2_memory must contain seven checkpoints")
        probe_socket_module = types.SimpleNamespace(
            AF_INET=2,
            SOCK_STREAM=1,
            socket=lambda family, kind: probe_listener,
        )
        output = io.StringIO()
        result = None
        error = None
        patches = (
            mock.patch.dict(
                sys.modules,
                {"network": fake, "socket": probe_socket_module},
            ),
            mock.patch.object(smoke, "_load_stage1", return_value=stage1),
            mock.patch.object(smoke, "_require_stage2_unloaded", return_value=True),
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
                stage1, "_load_wifi_runtime", return_value=self._wifi_runtime(clock)
            ),
            mock.patch.object(
                stage1,
                "_load_http_runtime",
                return_value=self._probe_builder(probe_listener),
            ),
            mock.patch.object(stage1, "_verify_frozen_origins", return_value=True),
            mock.patch.object(stage1, "_memory_free", side_effect=stage1_memory),
            mock.patch.object(
                stage2_prepare,
                "_load_rest_runtime",
                return_value=self._rest_core(
                    final_listener,
                    response_mutator,
                    final_socket_factory,
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
            if stage1_cleanup is not None:
                stack.enter_context(mock.patch.object(
                    stage1, "_cleanup_http_server", side_effect=stage1_cleanup
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

    def _assert_stage1_failure_is_clean(
        self,
        result,
        error,
        output,
        fake,
        probe_listener,
        filesystem,
        secret=None,
    ):
        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(str(error), "Phase-8 full REST phone smoke failed")
        self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
        self.assertNotIn(smoke.FULL_REST_PHONE_IP_PASS_TOKEN, output)
        self.assertNotIn(smoke.FULL_REST_PHONE_READY_TOKEN, output)
        self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
        rendered = output + repr(error)
        self.assertNotIn(_TEST_PASSWORD, rendered)
        if secret is not None:
            self.assertNotIn(secret, rendered)
        self.assertTrue(probe_listener.closed)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
        self.assertIs(wifi_module._WIFI_LEASED, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
        for path in stage2._storage_paths():
            self.assertNotIn(path, filesystem.files)
        return _failure_diagnostics(output)

    def test_three_stage_real_composition_uses_one_ap_lifetime(self):
        fake = _fake_network()
        client_calls = _script_ap_clients(fake, (0, 1, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )], name="probe")
        status = FakeClientSocket(recv_events=[_request(
            smoke.STATUS_PATH, smoke.AP_IP
        )], name="status")
        probe_listener = FakeListener(accept_events=[probe])
        final_listener = FakeListener(accept_events=[status])

        result, error, output, clock, filesystem = self._execute(
            fake, probe_listener, final_listener
        )

        self.assertIsNone(error)
        self.assertEqual(result["valid_status_requests"], 1)
        self.assertEqual(result["link_peer_ip"], "192.168.4.2")
        self.assertEqual(result["valid_peer_ip"], result["link_peer_ip"])
        self.assertIs(result["link_probe_cleanup_confirmed"], True)
        self.assertIs(result["single_wifi_lifetime_confirmed"], True)
        self.assertEqual(result["memory_before_http_start"], 80000)
        self.assertEqual(result["memory_after_proof_before_listen"], 75000)
        self.assertEqual(result["memory_after_http_bind"], 70000)
        self.assertGreaterEqual(result["memory_before_http_start"], 40 * 1024)
        self.assertGreaterEqual(
            result["memory_after_proof_before_listen"], 32 * 1024
        )
        self.assertGreaterEqual(result["memory_after_http_bind"], 32 * 1024)
        self.assertEqual(probe_listener.bind_values, [(smoke.AP_IP, 8080)])
        self.assertEqual(final_listener.bind_values, [(smoke.AP_IP, 80)])
        self.assertTrue(probe_listener.closed)
        self.assertTrue(final_listener.closed)
        self.assertTrue(probe.closed)
        self.assertTrue(status.closed)
        self.assertGreaterEqual(client_calls["count"], 4)
        probe_head, probe_body = _wire_json(probe)
        status_head, status_body = _wire_json(status)
        self.assertIn(b"HTTP/1.1 200 OK", probe_head)
        self.assertIs(probe_body["ip_check"]["ap_peer_validated"], True)
        self.assertIn(stage2._STATUS_PROOF_HEADER_LINE.rstrip(b"\r\n"), status_head)
        self.assertIs(status_body["configuration"]["setup_complete"], False)
        self.assertEqual(output.splitlines()[-1], smoke.FULL_REST_PHONE_PASS_TOKEN)
        lines = output.splitlines()
        for earlier, later in (
            (smoke.FULL_REST_PHONE_AP_READY_TOKEN, smoke.FULL_REST_PHONE_IP_READY_TOKEN),
            (smoke.FULL_REST_PHONE_IP_READY_TOKEN, smoke.FULL_REST_PHONE_CLIENT_TOKEN),
            (smoke.FULL_REST_PHONE_CLIENT_TOKEN, smoke.FULL_REST_PHONE_IP_PASS_TOKEN),
            (smoke.FULL_REST_PHONE_IP_PASS_TOKEN, smoke.FULL_REST_PHONE_READY_TOKEN),
        ):
            self.assertLess(lines.index(earlier), lines.index(later))
        self.assertNotIn(_TEST_PASSWORD, output + repr(result))
        self.assertNotIn(stage2.FULL_REST_PHONE_FAILURE_STAGE_TOKEN, output)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
        self.assertIs(wifi_module._WIFI_LEASED, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
        for path in stage2._storage_paths():
            self.assertNotIn(path, filesystem.files)

    def test_split_heap_boundaries_stop_at_the_exact_ownership_gate(self):
        failures = (
            (
                "prestart",
                (90000, 85000, 40959, 75000, 70000, 95000, 90000),
                (0, 0, 0),
                0,
            ),
            (
                "proof_prelisten",
                (90000, 85000, 40960, 32767, 70000, 95000, 90000),
                (1, 1, 0),
                1,
            ),
            (
                "poststart",
                (90000, 85000, 40960, 32768, 32767, 95000, 90000),
                (1, 1, 1),
                2,
            ),
        )
        for name, checkpoints, socket_progress, expected_proof_loads in failures:
            with self.subTest(name=name):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1, 1, 1))
                probe = FakeClientSocket(recv_events=[_request(
                    smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
                )])
                listener = FakeListener()
                proof_loads = {"count": 0}

                def load_proof():
                    proof_loads["count"] += 1
                    return stage2

                result, error, output, _, filesystem = self._execute(
                    fake,
                    FakeListener(accept_events=[probe]),
                    listener,
                    stage2_memory=checkpoints,
                    stage2_loader=load_proof,
                )

                self.assertIsNone(result)
                self.assertIsInstance(error, RuntimeError)
                self.assertEqual(proof_loads["count"], expected_proof_loads)
                self.assertEqual(len(listener.blocking_values), socket_progress[0])
                self.assertEqual(len(listener.bind_values), socket_progress[1])
                self.assertEqual(len(listener.listen_values), socket_progress[2])
                self.assertEqual(listener.accept_calls, 0)
                self.assertNotIn(smoke.FULL_REST_PHONE_READY_TOKEN, output)
                self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
                self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
                self.assertNotIn(_TEST_PASSWORD, output + repr(error))
                self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
                self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
                self.assertIs(wifi_module._WIFI_LEASED, False)
                self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
                for path in stage2._storage_paths():
                    self.assertNotIn(path, filesystem.files)

        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        status = FakeClientSocket(recv_events=[_request(
            smoke.STATUS_PATH, smoke.AP_IP
        )])
        result, error, output, _, _ = self._execute(
            fake,
            FakeListener(accept_events=[probe]),
            FakeListener(accept_events=[status]),
            stage2_memory=(
                90000, 85000, 40960, 32768, 32768, 95000, 90000,
            ),
        )
        self.assertIsNone(error)
        self.assertEqual(result["memory_before_http_start"], 40960)
        self.assertEqual(result["memory_after_proof_before_listen"], 32768)
        self.assertEqual(result["memory_after_http_bind"], 32768)
        self.assertEqual(output.splitlines()[-1], smoke.FULL_REST_PHONE_PASS_TOKEN)

    def test_http_bind_failures_emit_only_safe_progress_diagnostics(self):
        secret_text = "socket-secret-" + _TEST_PASSWORD

        class FailingListener(FakeListener):
            def __init__(self, operation, error):
                super().__init__()
                self.operation = operation
                self.error = error

            def setblocking(self, value):
                self.blocking_values.append(value)
                if self.operation == "setblocking":
                    raise self.error
                return None

            def bind(self, address):
                self.bind_values.append(address)
                if self.operation == "bind":
                    raise self.error
                return None

            def listen(self, backlog):
                self.listen_values.append(backlog)
                if self.operation == "listen":
                    raise self.error
                return None

        cases = (
            ("factory", OSError(23, secret_text), (0, 0, 0, 0), 23),
            ("setblocking", OSError(secret_text), (1, 0, 0, 0), -1),
            ("bind", OSError(True, secret_text), (1, 1, 0, 0), -1),
            ("listen", OSError(98, secret_text), (1, 1, 1, 0), 98),
        )
        for operation, injected_error, progress, expected_errno in cases:
            with self.subTest(operation=operation):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1, 1, 1))
                probe = FakeClientSocket(recv_events=[_request(
                    smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
                )])
                listener = FailingListener(operation, injected_error)
                factory_calls = {"count": 0}
                diagnostics_loads = {"count": 0}
                filesystem = _MemoryFileSystem()

                def socket_factory():
                    factory_calls["count"] += 1
                    if operation == "factory":
                        raise injected_error
                    return listener

                def load_diagnostics():
                    diagnostics_loads["count"] += 1
                    self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
                    self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
                    self.assertIs(wifi_module._WIFI_LEASED, False)
                    self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
                    for path in stage2._storage_paths():
                        self.assertNotIn(path, filesystem.files)
                    return stage2_diagnostics

                result, error, output, _, filesystem = self._execute(
                    fake,
                    FakeListener(accept_events=[probe]),
                    listener,
                    filesystem=filesystem,
                    final_socket_factory=socket_factory,
                    stage2_memory=(
                        90000, 85000, 77777, 75000, 70000, 95000, 90000,
                    ),
                    diagnostic_memory=(66666,),
                    diagnostics_loader=load_diagnostics,
                )

                self.assertIsNone(result)
                self.assertIsInstance(error, RuntimeError)
                self.assertEqual(
                    str(error), "Phase-8 full REST phone smoke failed"
                )
                rendered = output + repr(error)
                self.assertNotIn(_TEST_PASSWORD, rendered)
                self.assertNotIn(secret_text, rendered)
                self.assertNotIn(str(injected_error), rendered)
                self.assertNotIn(smoke.FULL_REST_PHONE_READY_TOKEN, output)
                self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
                self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
                diagnostics = _failure_diagnostics(output)
                expected_diagnostics = {
                    "stage": "http_bind",
                    "http_faulted": "1",
                    "http_clients": "0",
                    "http_accepted": "0",
                    "http_completed": "0",
                    "http_parse_errors": "0",
                    "http_timeouts": "0",
                    "http_socket_errors": "0",
                    "http_last_error": "other",
                    "listener_factory_returned": str(progress[0]),
                    "listener_setblocking_returned": str(progress[1]),
                    "listener_bind_returned": str(progress[2]),
                    "listener_listen_returned": str(progress[3]),
                    "listener_errno": str(expected_errno),
                    "observer_faulted": (
                        "0" if operation == "listen" else "-1"
                    ),
                    "observer_open_clients": (
                        "0" if operation == "listen" else "-1"
                    ),
                    "target_headers": (
                        "0" if operation == "listen" else "-1"
                    ),
                    "target_wires": (
                        "0" if operation == "listen" else "-1"
                    ),
                    "target_completions": (
                        "0" if operation == "listen" else "-1"
                    ),
                    "target_failures": (
                        "0" if operation == "listen" else "-1"
                    ),
                    "status_valid": (
                        "0" if operation == "listen" else "-1"
                    ),
                    "status_success": (
                        "0" if operation == "listen" else "-1"
                    ),
                    "status_marked": (
                        "0" if operation == "listen" else "-1"
                    ),
                    "status_rejected": (
                        "0" if operation == "listen" else "-1"
                    ),
                    "status_responses": (
                        "0" if operation == "listen" else "-1"
                    ),
                    "candidate_active": "0",
                    "ap_client_confirmed": "1",
                    "post_bind_peer_confirmed": "0",
                    "response_completed": "0",
                    "stage1_client_seen": "0",
                    "stage1_ap_clients": "-1",
                    "stage1_action": "none",
                    "stage1_http_started": "-1",
                    "stage1_http_closed": "-1",
                    "stage1_http_faulted": "-1",
                    "stage1_http_clients": "-1",
                    "stage1_http_accepted": "-1",
                    "stage1_http_completed": "-1",
                    "stage1_http_parse_errors": "-1",
                    "stage1_http_timeouts": "-1",
                    "stage1_http_socket_errors": "-1",
                    "stage1_http_last_error": "none",
                    "stage1_http_reentries": "-1",
                    "stage1_accept_errno": "-1",
                    "stage1_valid": "-1",
                    "stage1_rejected": "-1",
                    "stage1_responses": "-1",
                    "stage1_cleanup_confirmed": "1",
                    "memory_before": "150000",
                    "memory_after_product_imports": "90000",
                    "memory_after_configuration_adoption": "85000",
                    "memory_after_wifi_factory": "130000",
                    "memory_after_ap_ready": "120000",
                    "memory_after_ip_bind": "110000",
                    "memory_after_ip_response": "105000",
                    "memory_after_ip_cleanup": "100000",
                    "memory_before_http_start": "77777",
                    "memory_after_http_bind": "-1",
                    "memory_after_response": "-1",
                    "memory_after_cleanup": "-1",
                    "memory_after_failure_cleanup": "66666",
                }
                self.assertEqual(diagnostics, expected_diagnostics)
                self.assertEqual(factory_calls["count"], 1)
                self.assertEqual(diagnostics_loads["count"], 1)
                self.assertEqual(
                    listener.blocking_values,
                    [] if operation == "factory" else [False],
                )
                self.assertEqual(
                    listener.bind_values,
                    [] if operation in ("factory", "setblocking")
                    else [(smoke.AP_IP, 80)],
                )
                self.assertEqual(
                    listener.listen_values,
                    [] if operation != "listen" else [2],
                )
                self.assertEqual(
                    getattr(listener, "closed", False),
                    operation != "factory",
                )
                self.assertIs(wifi_module._WIFI_LEASED, False)
                self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
                for path in stage2._storage_paths():
                    self.assertNotIn(path, filesystem.files)

    def test_accept_diagnostics_classify_errno_contract_and_would_block(self):
        allowlist = frozenset((
            "none", "accept_failed", "accept_contract_failed", "other",
        ))
        self.assertNotIn(
            "accept_errno", stage2_seam.LateSocketFactory.__slots__
        )

        class CapturingSeam:
            def __init__(self):
                self.states = []

            def __getattr__(self, name):
                return getattr(stage2_seam, name)

            def Stage2State(self):
                state = stage2_seam.Stage2State()
                self.states.append(state)
                return state

        for errno in (11, 35, 10035):
            with self.subTest(would_block_errno=errno):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1, 1, 1))
                probe = FakeClientSocket(recv_events=[_request(
                    smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
                )])
                status = FakeClientSocket(recv_events=[_request(
                    smoke.STATUS_PATH, smoke.AP_IP
                )])
                listener = FakeListener(
                    accept_events=[OSError(errno), status]
                )
                seam_proxy = CapturingSeam()
                result, error, output, _, _ = self._execute(
                    fake,
                    FakeListener(accept_events=[probe]),
                    listener,
                    seam_loader=lambda: seam_proxy,
                )
                self.assertIsNone(error)
                self.assertIsNotNone(result)
                self.assertEqual(
                    output.splitlines()[-1],
                    smoke.FULL_REST_PHONE_PASS_TOKEN,
                )
                self.assertNotIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
                self.assertEqual(
                    seam_proxy.states[0].socket_factory.listener_errno, -1
                )

        secret_text = "accept-diagnostic-secret-" + _TEST_PASSWORD
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        status = FakeClientSocket(recv_events=[_request(
            smoke.STATUS_PATH, smoke.AP_IP
        )])
        listener = FakeListener(
            accept_events=[OSError(113, secret_text), status]
        )
        seam_proxy = CapturingSeam()
        result, error, output, _, _ = self._execute(
            fake,
            FakeListener(accept_events=[probe]),
            listener,
            seam_loader=lambda: seam_proxy,
        )
        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(
            output.splitlines()[-1], smoke.FULL_REST_PHONE_PASS_TOKEN
        )
        self.assertNotIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
        self.assertNotIn(secret_text, output)
        self.assertEqual(
            seam_proxy.states[0].socket_factory.listener_errno, 113
        )

        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        listener = FakeListener(accept_events=[
            OSError(113, secret_text),
            OSError(12, secret_text),
        ])
        seam_proxy = CapturingSeam()
        result, error, output, _, _ = self._execute(
            fake,
            FakeListener(accept_events=[probe]),
            listener,
            seam_loader=lambda: seam_proxy,
        )
        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
        self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
        self.assertNotIn(secret_text, output + repr(error))
        diagnostics = _failure_diagnostics(output)
        self.assertEqual(diagnostics["http_last_error"], "accept_failed")
        self.assertEqual(diagnostics["listener_errno"], "12")
        self.assertEqual(
            seam_proxy.states[0].socket_factory.listener_errno, 12
        )

        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        listener = FakeListener(
            accept_events=[OSError(113, secret_text)] * 3000
        )
        filesystem = _MemoryFileSystem()
        seam_proxy = CapturingSeam()
        result, error, output, _, filesystem = self._execute(
            fake,
            FakeListener(accept_events=[probe]),
            listener,
            filesystem=filesystem,
            seam_loader=lambda: seam_proxy,
        )
        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
        self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
        self.assertNotIn(secret_text, output + repr(error))
        diagnostics = _failure_diagnostics(output)
        self.assertEqual(diagnostics["stage"], "observe_timeout")
        self.assertEqual(diagnostics["http_accepted"], "0")
        self.assertEqual(diagnostics["http_completed"], "0")
        self.assertEqual(diagnostics["http_socket_errors"], "0")
        self.assertEqual(diagnostics["listener_errno"], "113")
        self.assertTrue(listener.closed)
        self.assertIs(wifi_module._WIFI_LEASED, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
        for path in stage2._storage_paths():
            self.assertNotIn(path, filesystem.files)

        cases = (
            ("numeric_12", OSError(12, secret_text), "accept_failed", 12),
            ("numeric_23", OSError(23, secret_text), "accept_failed", 23),
            ("numeric_103", OSError(103, secret_text), "accept_failed", 103),
            ("nonnumeric", OSError(secret_text), "accept_failed", -1),
            (
                "runtime_contract",
                RuntimeError(secret_text),
                "accept_contract_failed",
                -1,
            ),
            (
                "malformed_contract",
                None,
                "accept_contract_failed",
                -1,
            ),
        )
        for name, accept_error, expected_last_error, expected_errno in cases:
            with self.subTest(name=name):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1, 1, 1))
                probe = FakeClientSocket(recv_events=[_request(
                    smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
                )])
                raw_client = None
                if name == "malformed_contract":
                    raw_client = FakeClientSocket(name="malformed_secret")
                    accept_event = [
                        raw_client,
                        ("192.168.4.2", 50001),
                        secret_text,
                    ]
                else:
                    accept_event = accept_error
                listener = FakeListener(accept_events=[accept_event])
                filesystem = _MemoryFileSystem()
                seam_proxy = CapturingSeam()
                result, error, output, _, _ = self._execute(
                    fake,
                    FakeListener(accept_events=[probe]),
                    listener,
                    filesystem=filesystem,
                    seam_loader=lambda: seam_proxy,
                )

                self.assertIsNone(result)
                self.assertIsInstance(error, RuntimeError)
                self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
                self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
                rendered = output + repr(error)
                self.assertNotIn(_TEST_PASSWORD, rendered)
                self.assertNotIn(secret_text, rendered)
                diagnostics = _failure_diagnostics(output)
                self.assertEqual(
                    diagnostics["http_last_error"], expected_last_error
                )
                self.assertIn(diagnostics["http_last_error"], allowlist)
                self.assertEqual(
                    diagnostics["listener_errno"], str(expected_errno)
                )
                self.assertEqual(
                    seam_proxy.states[0].socket_factory.listener_errno,
                    expected_errno,
                )
                self.assertNotIn("accept_errno", diagnostics)
                self.assertTrue(listener.closed)
                if raw_client is not None:
                    self.assertTrue(raw_client.closed)
                    self.assertEqual(raw_client.close_calls, 1)
                self.assertIs(wifi_module._WIFI_LEASED, False)
                self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
                for path in stage2._storage_paths():
                    self.assertNotIn(path, filesystem.files)

        base_snapshot = {
            "faulted": False,
            "client_count": 0,
            "accepted": 0,
            "completed": 0,
            "parse_errors": 0,
            "timeouts": 0,
            "socket_errors": 0,
        }
        for raw_value, expected in (
            (None, "none"),
            ("accept_failed", "accept_failed"),
            ("accept_contract_failed", "accept_contract_failed"),
            (secret_text, "other"),
        ):
            with self.subTest(last_error=expected):
                snapshot = dict(base_snapshot, last_error=raw_value)
                values = stage2_diagnostics.capture(
                    "observe_http_transport",
                    snapshot,
                    None,
                    None,
                    True,
                    True,
                    False,
                )
                self.assertEqual(values["http_last_error"], expected)
                self.assertIn(values["http_last_error"], allowlist)
                self.assertNotIn("accept_errno", values)
                self.assertNotIn(secret_text, repr(values))

    def test_actual_lazy_proof_binds_after_prep_unload_and_arms_before_listen(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        status = FakeClientSocket(recv_events=[_request(
            smoke.STATUS_PATH, smoke.AP_IP
        )])
        probe_listener = FakeListener(accept_events=[probe])
        events = []
        states = []

        class SeamProxy:
            def __getattr__(self, name):
                return getattr(stage2_seam, name)

            def Stage2State(self):
                state = stage2_seam.Stage2State()
                states.append(state)
                return state

        class OrderedListener(FakeListener):
            def bind(inner_self, address):
                events.append("raw_bind")
                self.assertIn("prepare_unloaded", events)
                state = states[0]
                security = state.context.rest_runtime.security_policy.snapshot()
                self.assertIs(security["started"], True)
                self.assertIs(security["mutation_api_available"], True)
                self.assertIs(state.gate.armed, False)
                self.assertIs(state.proof_loaded, False)
                self.assertIsNone(state.context.gateway)
                self.assertIsNone(state.context.socket_observer)
                for slot_name in stage2_seam.PreparedContext.__slots__:
                    value = getattr(state.context, slot_name)
                    if value is not None:
                        self.assertNotEqual(
                            type(value).__module__, stage2_prepare.__name__
                        )
                return super().bind(address)

            def listen(inner_self, backlog):
                events.append("raw_listen")
                self.assertIn("proof_loaded", events)
                state = states[0]
                self.assertIs(state.proof_loaded, True)
                self.assertIs(state.gate.armed, True)
                self.assertIsNotNone(state.context.gateway)
                self.assertIsNotNone(state.context.socket_observer)
                return super().listen(backlog)

        final_listener = OrderedListener(accept_events=[status])
        proof_loads = {"count": 0}

        def unload(module):
            self.assertIs(module, stage1)
            events.append("stage1_unloaded")

        def load_seam():
            self.assertTrue(probe_listener.closed)
            self.assertIn("stage1_unloaded", events)
            events.append("seam_loaded")
            return SeamProxy()

        def load_prepare():
            self.assertIn("seam_loaded", events)
            events.append("prepare_loaded")
            return stage2_prepare

        def unload_prepare(module, module_name, attribute_name):
            self.assertIs(module, stage2_prepare)
            self.assertEqual(module_name, smoke._STAGE2_PREPARE_MODULE)
            self.assertEqual(
                attribute_name, "phase8_full_rest_phone_stage2_prepare"
            )
            events.append("prepare_unloaded")

        def prepare_proof(context):
            self.assertIs(context, states[0].context)
            self.assertNotIn("server", stage2_seam.PreparedContext.__slots__)
            stage2.prepare_proof(context)
            events.append("proof_loaded")

        proof_proxy = types.SimpleNamespace(prepare_proof=prepare_proof)

        def load_stage2():
            proof_loads["count"] += 1
            if proof_loads["count"] == 1:
                self.assertIn("raw_bind", events)
                self.assertNotIn("raw_listen", events)
                return proof_proxy
            else:
                self.assertIn("raw_listen", events)
                events.append("proof_reused")
            return stage2

        result, error, _, _, _ = self._execute(
            fake,
            probe_listener,
            final_listener,
            stage2_loader=load_stage2,
            seam_loader=load_seam,
            prepare_loader=load_prepare,
            unload=unload,
            unload_module=unload_prepare,
        )
        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(proof_loads["count"], 2)
        self.assertLess(events.index("stage1_unloaded"), events.index("seam_loaded"))
        self.assertLess(events.index("seam_loaded"), events.index("prepare_loaded"))
        self.assertLess(events.index("prepare_loaded"), events.index("prepare_unloaded"))
        self.assertLess(events.index("prepare_unloaded"), events.index("raw_bind"))
        self.assertLess(events.index("raw_bind"), events.index("proof_loaded"))
        self.assertLess(events.index("proof_loaded"), events.index("raw_listen"))
        self.assertLess(events.index("raw_listen"), events.index("proof_reused"))

    def test_stage1_wrong_host_never_hands_off(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP
        )])
        probe_listener = FakeListener(accept_events=[probe])
        loaded = {"calls": 0}

        def forbidden_loader():
            loaded["calls"] += 1
            raise AssertionError("stage2 loaded")

        result, error, output, _, _ = self._execute(
            fake,
            probe_listener,
            FakeListener(),
            stage2_loader=forbidden_loader,
        )
        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(loaded["calls"], 0)
        self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
        self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
        self.assertTrue(probe_listener.closed)
        self.assertIs(wifi_module._WIFI_LEASED, False)

    def test_stage1_partial_response_never_hands_off(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1))
        probe = FakeClientSocket(
            recv_events=[_request(smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080")],
            send_events=[lambda payload: min(24, len(payload))]
            + [OSError(11)] * 512,
        )
        loaded = {"calls": 0}

        def forbidden_loader():
            loaded["calls"] += 1
            return stage2.continue_run

        result, error, output, clock, _ = self._execute(
            fake,
            FakeListener(accept_events=[probe]),
            FakeListener(),
            stage2_loader=forbidden_loader,
        )
        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(loaded["calls"], 0)
        self.assertGreaterEqual(clock.now_ms, 1500)
        self.assertNotIn(smoke.FULL_REST_PHONE_IP_PASS_TOKEN, output)
        self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)

    def test_disconnect_after_client_seen_is_terminal(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 0))
        probe = FakeClientSocket(
            recv_events=[OSError(11)] * 100,
        )
        result, error, output, _, _ = self._execute(
            fake,
            FakeListener(accept_events=[probe]),
            FakeListener(),
        )
        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn(smoke.FULL_REST_PHONE_CLIENT_TOKEN, output)
        self.assertNotIn(smoke.FULL_REST_PHONE_IP_PASS_TOKEN, output)
        self.assertIs(wifi_module._WIFI_LEASED, False)

    def test_stage1_diagnostics_pin_disconnect_accept_errno_and_contract(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 0))
        listener = FakeListener()
        filesystem = _MemoryFileSystem()
        result, error, output, _, filesystem = self._execute(
            fake,
            listener,
            FakeListener(),
            filesystem=filesystem,
        )
        diagnostics = self._assert_stage1_failure_is_clean(
            result, error, output, fake, listener, filesystem
        )
        self.assertEqual(
            {
                name: diagnostics[name]
                for name in (
                    "stage",
                    "stage1_client_seen",
                    "stage1_ap_clients",
                    "stage1_action",
                    "stage1_http_started",
                    "stage1_http_closed",
                    "stage1_http_faulted",
                    "stage1_http_clients",
                    "stage1_http_accepted",
                    "stage1_http_completed",
                    "stage1_http_parse_errors",
                    "stage1_http_timeouts",
                    "stage1_http_socket_errors",
                    "stage1_http_last_error",
                    "stage1_http_reentries",
                    "stage1_accept_errno",
                    "stage1_valid",
                    "stage1_rejected",
                    "stage1_responses",
                    "stage1_cleanup_confirmed",
                )
            },
            {
                "stage": "stage1_observe_network_truth",
                "stage1_client_seen": "1",
                "stage1_ap_clients": "0",
                "stage1_action": "ap_checked",
                "stage1_http_started": "1",
                "stage1_http_closed": "0",
                "stage1_http_faulted": "0",
                "stage1_http_clients": "0",
                "stage1_http_accepted": "0",
                "stage1_http_completed": "0",
                "stage1_http_parse_errors": "0",
                "stage1_http_timeouts": "0",
                "stage1_http_socket_errors": "0",
                "stage1_http_last_error": "none",
                "stage1_http_reentries": "0",
                "stage1_accept_errno": "-1",
                "stage1_valid": "0",
                "stage1_rejected": "0",
                "stage1_responses": "0",
                "stage1_cleanup_confirmed": "1",
            },
        )

        secret = "stage1-accept-secret-" + _TEST_PASSWORD
        cases = (
            ("errno_12", OSError(12, secret), "accept_failed", 12),
            ("errno_23", OSError(23, secret), "accept_failed", 23),
            ("errno_103", OSError(103, secret), "accept_failed", 103),
            (
                "contract",
                RuntimeError(secret),
                "accept_contract_failed",
                -1,
            ),
        )
        for name, event, last_error, errno in cases:
            with self.subTest(name=name):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1, 1))
                listener = FakeListener(accept_events=[event])
                filesystem = _MemoryFileSystem()
                result, error, output, _, filesystem = self._execute(
                    fake,
                    listener,
                    FakeListener(),
                    filesystem=filesystem,
                )
                diagnostics = self._assert_stage1_failure_is_clean(
                    result,
                    error,
                    output,
                    fake,
                    listener,
                    filesystem,
                    secret,
                )
                self.assertEqual(
                    diagnostics["stage"], "stage1_observe_http_transport"
                )
                self.assertEqual(diagnostics["stage1_client_seen"], "0")
                self.assertEqual(diagnostics["stage1_http_started"], "1")
                self.assertEqual(diagnostics["stage1_http_closed"], "0")
                self.assertEqual(diagnostics["stage1_http_faulted"], "0")
                self.assertEqual(diagnostics["stage1_http_clients"], "0")
                self.assertEqual(diagnostics["stage1_http_accepted"], "0")
                self.assertEqual(diagnostics["stage1_http_completed"], "0")
                self.assertEqual(
                    diagnostics["stage1_http_socket_errors"], "1"
                )
                self.assertEqual(
                    diagnostics["stage1_http_last_error"], last_error
                )
                self.assertEqual(
                    diagnostics["stage1_accept_errno"], str(errno)
                )
                self.assertEqual(diagnostics["stage1_cleanup_confirmed"], "1")

        for errno in (11, 35, 10035):
            with self.subTest(would_block_errno=errno):
                event = OSError(errno, secret)
                raw = FakeListener(accept_events=[event])
                owner = types.SimpleNamespace(accept_errno=-1)
                wrapped = stage1._DiagnosticListener(raw, owner)
                with self.assertRaises(OSError) as raised:
                    wrapped.accept()
                self.assertIs(raised.exception, event)
                self.assertEqual(owner.accept_errno, -1)

        raw = FakeListener(accept_events=[
            OSError(113, secret),
            OSError(12, secret),
        ])
        owner = types.SimpleNamespace(accept_errno=-1)
        wrapped = stage1._DiagnosticListener(raw, owner)
        with self.assertRaises(OSError):
            wrapped.accept()
        self.assertEqual(owner.accept_errno, 113)
        with self.assertRaises(OSError):
            wrapped.accept()
        self.assertEqual(owner.accept_errno, 12)

        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        status = FakeClientSocket(recv_events=[_request(
            smoke.STATUS_PATH, smoke.AP_IP
        )])
        result, error, output, _, _ = self._execute(
            fake,
            FakeListener(accept_events=[OSError(113, secret), probe]),
            FakeListener(accept_events=[status]),
        )
        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(
            output.splitlines()[-1], smoke.FULL_REST_PHONE_PASS_TOKEN
        )
        self.assertNotIn(secret, output)

        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1))
        listener = FakeListener(
            accept_events=[OSError(11)] * 40
            + [OSError(113, secret), OSError(12, secret)]
        )
        filesystem = _MemoryFileSystem()
        result, error, output, _, filesystem = self._execute(
            fake,
            listener,
            FakeListener(),
            filesystem=filesystem,
        )
        diagnostics = self._assert_stage1_failure_is_clean(
            result,
            error,
            output,
            fake,
            listener,
            filesystem,
            secret,
        )
        self.assertIn(smoke.FULL_REST_PHONE_CLIENT_TOKEN, output)
        self.assertEqual(
            diagnostics["stage"], "stage1_observe_http_transport"
        )
        self.assertEqual(diagnostics["stage1_accept_errno"], "12")

        post_client_cases = (
            ("post_client_errno_103", OSError(103, secret), "accept_failed", 103),
            (
                "post_client_contract",
                RuntimeError(secret),
                "accept_contract_failed",
                -1,
            ),
        )
        for name, event, last_error, errno in post_client_cases:
            with self.subTest(name=name):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1, 1))
                listener = FakeListener(
                    accept_events=[OSError(11)] * 40 + [event]
                )
                filesystem = _MemoryFileSystem()
                result, error, output, _, filesystem = self._execute(
                    fake,
                    listener,
                    FakeListener(),
                    filesystem=filesystem,
                )
                diagnostics = self._assert_stage1_failure_is_clean(
                    result,
                    error,
                    output,
                    fake,
                    listener,
                    filesystem,
                    secret,
                )
                self.assertIn(smoke.FULL_REST_PHONE_CLIENT_TOKEN, output)
                self.assertEqual(
                    diagnostics["stage"], "stage1_observe_http_transport"
                )
                self.assertEqual(diagnostics["stage1_client_seen"], "1")
                self.assertEqual(diagnostics["stage1_ap_clients"], "1")
                self.assertEqual(diagnostics["stage1_http_accepted"], "0")
                self.assertEqual(
                    diagnostics["stage1_http_socket_errors"], "1"
                )
                self.assertEqual(
                    diagnostics["stage1_http_last_error"], last_error
                )
                self.assertEqual(
                    diagnostics["stage1_accept_errno"], str(errno)
                )
                self.assertEqual(
                    diagnostics["stage1_cleanup_confirmed"], "1"
                )

    def test_stage1_diagnostics_pin_post_client_transport_failures(self):
        secret = "stage1-transport-secret-" + _TEST_PASSWORD
        cases = (
            (
                "timeout",
                [OSError(11)] * 200,
                None,
                (0, 1, 0, 0, "none"),
            ),
            (
                "parse",
                [b"BROKEN " + secret.encode("ascii") + b"\r\n\r\n"],
                None,
                (0, 0, 1, 0, "none"),
            ),
            (
                "socket",
                [OSError(12, secret)],
                None,
                (0, 0, 0, 1, "other"),
            ),
            (
                "fault",
                [_request(smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080")],
                RuntimeError(secret),
                (1, 0, 0, 0, "other"),
            ),
        )
        for name, recv_events, handler_error, expected in cases:
            with self.subTest(name=name):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1, 1, 1))
                client = FakeClientSocket(
                    recv_events=recv_events,
                    name="stage1-{}".format(name),
                )
                listener = FakeListener(
                    accept_events=[OSError(11)] * 40 + [client]
                )
                filesystem = _MemoryFileSystem()
                handler_patch = contextlib.nullcontext()
                if handler_error is not None:
                    def fail_handler(self, request, peer_ip=None):
                        raise handler_error

                    handler_patch = mock.patch.object(
                        stage1._IPCheckHandler, "handle", fail_handler
                    )
                with handler_patch:
                    result, error, output, _, filesystem = self._execute(
                        fake,
                        listener,
                        FakeListener(),
                        filesystem=filesystem,
                    )
                diagnostics = self._assert_stage1_failure_is_clean(
                    result,
                    error,
                    output,
                    fake,
                    listener,
                    filesystem,
                    secret,
                )
                faulted, timeouts, parse_errors, socket_errors, last_error = (
                    expected
                )
                self.assertIn(smoke.FULL_REST_PHONE_CLIENT_TOKEN, output)
                self.assertEqual(
                    diagnostics["stage"], "stage1_observe_http_transport"
                )
                self.assertEqual(diagnostics["stage1_client_seen"], "1")
                self.assertEqual(diagnostics["stage1_ap_clients"], "1")
                self.assertEqual(diagnostics["stage1_http_started"], "1")
                self.assertEqual(diagnostics["stage1_http_closed"], "0")
                self.assertEqual(
                    diagnostics["stage1_http_faulted"], str(faulted)
                )
                self.assertEqual(diagnostics["stage1_http_accepted"], "1")
                self.assertEqual(diagnostics["stage1_http_completed"], "0")
                self.assertEqual(
                    diagnostics["stage1_http_timeouts"], str(timeouts)
                )
                self.assertEqual(
                    diagnostics["stage1_http_parse_errors"],
                    str(parse_errors),
                )
                self.assertEqual(
                    diagnostics["stage1_http_socket_errors"],
                    str(socket_errors),
                )
                self.assertEqual(
                    diagnostics["stage1_http_last_error"], last_error
                )
                self.assertEqual(diagnostics["stage1_accept_errno"], "-1")
                self.assertEqual(diagnostics["stage1_cleanup_confirmed"], "1")

    def test_stage1_failure_diagnostics_run_only_after_cleanup_and_survive_oom(self):
        secret = "stage1-diagnostic-oom-secret-" + _TEST_PASSWORD
        for failure_point in ("load", "capture", "emit"):
            with self.subTest(failure_point=failure_point):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1, 1))
                listener = FakeListener(
                    accept_events=[OSError(12, secret)]
                )
                filesystem = _MemoryFileSystem()
                loads = {"count": 0}

                def load_diagnostics():
                    loads["count"] += 1
                    self.assertTrue(listener.closed)
                    self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
                    self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
                    self.assertIs(wifi_module._WIFI_LEASED, False)
                    self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
                    if failure_point == "load":
                        raise MemoryError()
                    return stage2_diagnostics

                patch = contextlib.nullcontext()
                if failure_point in ("capture", "emit"):
                    patch = mock.patch.object(
                        stage2_diagnostics,
                        failure_point,
                        side_effect=MemoryError,
                    )
                with patch:
                    result, error, output, _, filesystem = self._execute(
                        fake,
                        listener,
                        FakeListener(),
                        filesystem=filesystem,
                        diagnostics_loader=load_diagnostics,
                    )

                self.assertIsNone(result)
                self.assertIsInstance(error, RuntimeError)
                self.assertNotIsInstance(error, MemoryError)
                self.assertEqual(
                    str(error), "Phase-8 full REST phone smoke failed"
                )
                self.assertEqual(loads["count"], 1)
                rendered = output + repr(error)
                self.assertNotIn(_TEST_PASSWORD, rendered)
                self.assertNotIn(secret, rendered)
                self.assertNotIn(str(OSError(12, secret)), rendered)
                self.assertNotIn(smoke.FULL_REST_PHONE_IP_PASS_TOKEN, output)
                self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
                lines = output.splitlines()
                failure_index = lines.index(
                    stage2.FULL_REST_PHONE_FAILURE_STAGE_TOKEN
                )
                self.assertEqual(
                    lines[failure_index + 1], smoke.FULL_REST_PHONE_FAIL_TOKEN
                )
                self.assertTrue(listener.closed)
                self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
                self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
                self.assertIs(wifi_module._WIFI_LEASED, False)
                self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
                for path in stage2._storage_paths():
                    self.assertNotIn(path, filesystem.files)

    def test_stage1_factory_retains_raw_listener_until_confirmed_close(self):
        class PublishFaultFactory(stage1._DiagnosticSocketFactory):
            __slots__ = ()

            def __setattr__(self, name, value):
                if name == "_orphan_port" and value is not None:
                    raise MemoryError()
                return super().__setattr__(name, value)

        publish_raw = FakeListener(
            close_events=[RuntimeError("publish-close-secret"), None]
        )
        publish_socket_module = types.SimpleNamespace(
            AF_INET=2,
            SOCK_STREAM=1,
            socket=lambda family, kind: publish_raw,
        )
        publish_factory = PublishFaultFactory()
        with mock.patch.dict(sys.modules, {"socket": publish_socket_module}):
            with self.assertRaises(MemoryError):
                publish_factory()
        self.assertIsNone(publish_factory._orphan_port)
        self.assertIs(publish_factory._raw_owner[0], publish_raw)
        self.assertIs(publish_factory.close_retained(), True)
        self.assertIsNone(publish_factory._raw_owner[0])
        self.assertTrue(publish_raw.closed)
        self.assertEqual(publish_raw.close_calls, 2)

        interrupt_raw = FakeListener(
            close_events=[
                RuntimeError("interrupt-close-secret"),
                "bad-close-contract",
                None,
            ]
        )
        interrupt_socket_module = types.SimpleNamespace(
            AF_INET=2,
            SOCK_STREAM=1,
            socket=lambda family, kind: interrupt_raw,
        )
        interrupt_factory = stage1._DiagnosticSocketFactory()
        source_lines, start_line = inspect.getsourcelines(
            stage1._DiagnosticSocketFactory.__call__
        )
        publish_line = next(
            start_line + index
            for index, line in enumerate(source_lines)
            if line.strip() == "owner[0] = raw"
        )
        interrupted = {"raised": False}

        def interrupt_before_publish(frame, event, argument):
            if (
                event == "line"
                and frame.f_code
                is stage1._DiagnosticSocketFactory.__call__.__code__
                and frame.f_lineno == publish_line
                and not interrupted["raised"]
            ):
                interrupted["raised"] = True
                raise KeyboardInterrupt()
            return interrupt_before_publish

        previous_trace = sys.gettrace()
        try:
            with mock.patch.dict(
                sys.modules, {"socket": interrupt_socket_module}
            ):
                sys.settrace(interrupt_before_publish)
                with self.assertRaises(KeyboardInterrupt):
                    interrupt_factory()
        finally:
            sys.settrace(previous_trace)
        self.assertIs(interrupted["raised"], True)
        self.assertIs(interrupt_factory._raw_owner[0], interrupt_raw)
        self.assertFalse(interrupt_raw.closed)
        self.assertEqual(interrupt_raw.close_calls, 2)
        self.assertIs(interrupt_factory.close_retained(), True)
        self.assertIsNone(interrupt_factory._raw_owner[0])
        self.assertTrue(interrupt_raw.closed)
        self.assertEqual(interrupt_raw.close_calls, 3)

        raw = FakeListener(
            close_events=[
                RuntimeError("close-secret"),
                "bad-close-contract",
                RuntimeError("close-secret"),
                "bad-close-contract",
                None,
            ]
        )
        socket_module = types.SimpleNamespace(
            AF_INET=2,
            SOCK_STREAM=1,
            socket=lambda family, kind: raw,
        )
        factory = stage1._DiagnosticSocketFactory()
        previous_retained = smoke._RETAINED_STAGE1_SOCKET_FACTORY
        smoke._RETAINED_STAGE1_SOCKET_FACTORY = None
        try:
            with mock.patch.dict(sys.modules, {"socket": socket_module}), \
                    mock.patch.object(
                        stage1,
                        "_DiagnosticListener",
                        side_effect=MemoryError,
                    ):
                with self.assertRaises(MemoryError):
                    factory()
            self.assertIs(factory._raw_owner[0], raw)
            self.assertFalse(raw.closed)

            capsule = smoke._OwnershipCapsule()
            capsule.stage1_socket_factory = factory
            self.assertIs(smoke._outer_cleanup(capsule), False)
            self.assertIs(
                smoke._RETAINED_STAGE1_SOCKET_FACTORY,
                factory,
            )
            self.assertIs(capsule.stage1_cleanup_confirmed, False)
            self.assertIs(factory._raw_owner[0], raw)

            self.assertIs(
                smoke._recover_retained_stage1_socket_factory(),
                True,
            )
            self.assertIsNone(smoke._RETAINED_STAGE1_SOCKET_FACTORY)
            self.assertIsNone(factory._raw_owner[0])
            self.assertIsNone(factory._orphan_port)
            self.assertTrue(raw.closed)
            self.assertEqual(raw.close_calls, 5)
            self.assertIs(factory.close_retained(), True)
            self.assertEqual(raw.close_calls, 5)
        finally:
            smoke._RETAINED_STAGE1_SOCKET_FACTORY = previous_retained

    def test_stage2_import_oom_keeps_coordinator_cleanup_authority(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        result, error, output, _, _ = self._execute(
            fake,
            FakeListener(accept_events=[probe]),
            FakeListener(),
            stage2_loader=MemoryError,
        )
        self.assertIsNone(result)
        self.assertIsInstance(error, MemoryError)
        self.assertIn(smoke.FULL_REST_PHONE_IP_PASS_TOKEN, output)
        self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(wifi_module._WIFI_LEASED, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

    def test_stage2_entry_failure_after_claim_cleans_exactly_once(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        cleanups = {"calls": 0}
        real_cleanup = smoke._cleanup_radio

        def counted_cleanup(*arguments):
            cleanups["calls"] += 1
            return real_cleanup(*arguments)

        def fail_after_claim(capsule, state, password, window):
            capsule.owner_state = "stage2"
            raise MemoryError()

        with mock.patch.object(
            smoke, "_cleanup_radio", side_effect=counted_cleanup
        ), mock.patch.object(
            stage2, "continue_run", side_effect=fail_after_claim
        ):
            result, error, output, _, _ = self._execute(
                fake,
                FakeListener(accept_events=[probe]),
                FakeListener(),
            )
        self.assertIsNone(result)
        self.assertIsInstance(error, MemoryError)
        self.assertEqual(cleanups["calls"], 1)
        self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
        self.assertIs(wifi_module._WIFI_LEASED, False)

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
        self.assertIsNone(state.context.gateway)
        self.assertIsNone(state.context.socket_observer)
        with self.assertRaises(RuntimeError):
            state.gate.handle("request", "192.168.4.2")
        state.gate.seal_security(runtime)
        self.assertIs(state.gate.application, state.gate)
        self.assertIsNot(state.gate.application, runtime.application)
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

    def test_listener_handoff_rejects_prearm_accept_reentry_and_baseexceptions(self):
        class Security:
            @staticmethod
            def snapshot():
                return {"started": True, "mutation_api_available": True}

        runtime = types.SimpleNamespace(
            application=object(), security_policy=Security()
        )

        state = stage2_seam.Stage2State()
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

    def test_accept_owns_raw_client_before_reentry_shape_claim_or_publication_failure(self):
        class Security:
            @staticmethod
            def snapshot():
                return {"started": True, "mutation_api_available": True}

        runtime = types.SimpleNamespace(
            application=object(), security_policy=Security()
        )

        def armed_factory(raw_listener, observer=None):
            state = stage2_seam.Stage2State()
            state.context.rest_runtime = runtime
            state.gate.seal_security(runtime)
            state.gate.arm(types.SimpleNamespace(handle=lambda *_: None))
            if observer is None:
                observer = stage2._SocketResponseObserver()
            state.context.socket_observer = observer
            factory = stage2_seam.LateSocketFactory(
                lambda: raw_listener, state
            )
            factory.observer = observer
            listener = factory()
            listener.setblocking(False)
            listener.bind((smoke.AP_IP, 80))
            return factory, listener, observer

        raw_client = FakeClientSocket(name="reentrant_raw")

        class ReentrantRawListener(FakeListener):
            def accept(inner_self):
                inner_self.accept_calls += 1
                try:
                    wrapped_listener.accept()
                except RuntimeError:
                    pass
                return raw_client, ("192.168.4.2", 50001)

        raw_listener = ReentrantRawListener()
        factory, wrapped_listener, _ = armed_factory(raw_listener)
        with self.assertRaises(RuntimeError):
            wrapped_listener.accept()
        self.assertIs(factory.listener._orphan_port, raw_client)
        self.assertIsNone(factory.deinit())
        self.assertTrue(raw_client.closed)
        self.assertEqual(raw_client.close_calls, 1)
        self.assertIsNone(factory.deinit())
        self.assertEqual(raw_client.close_calls, 1)

        for malformed in ("short", "long"):
            with self.subTest(malformed=malformed):
                raw_client = FakeClientSocket(name=malformed)
                accepted = [raw_client]
                if malformed == "long":
                    accepted.extend((("192.168.4.2", 50002), "extra"))
                raw_listener = FakeListener(accept_events=[accepted])
                factory, listener, _ = armed_factory(raw_listener)
                with self.assertRaises(RuntimeError):
                    listener.accept()
                self.assertIs(factory.listener._orphan_port, raw_client)
                self.assertIsNone(factory.deinit())
                self.assertTrue(raw_client.closed)
                self.assertEqual(raw_client.close_calls, 1)

        class ClaimOOMObserver:
            def __init__(self):
                self.clients = ()
                self.faulted = False

            def _mark_fault(self):
                self.faulted = True

            @staticmethod
            def claim_client(_port):
                raise MemoryError()

        raw_client = FakeClientSocket(name="claim_oom")
        raw_listener = FakeListener(accept_events=[raw_client])
        observer = ClaimOOMObserver()
        factory, listener, _ = armed_factory(raw_listener, observer)
        with self.assertRaises(MemoryError):
            listener.accept()
        self.assertIs(factory.listener._orphan_port, raw_client)
        self.assertIsNone(factory.deinit())
        self.assertTrue(raw_client.closed)
        self.assertEqual(raw_client.close_calls, 1)

        source_lines, first_line = inspect.getsourcelines(
            stage2_seam.LateListenerSocket.accept
        )
        publication_lines = {}
        for offset, source_line in enumerate(source_lines):
            stripped = source_line.strip()
            if stripped == "accepted[0] = client":
                publication_lines["list"] = first_line + offset
            elif stripped == "return (client, accepted[1])":
                publication_lines["tuple"] = first_line + offset
        self.assertEqual(frozenset(publication_lines), frozenset(("list", "tuple")))

        for container_type in ("list", "tuple"):
            with self.subTest(publication=container_type):
                raw_client = FakeClientSocket(name=container_type + "_oom")
                address = ("192.168.4.2", 50003)
                accepted = (
                    [raw_client, address]
                    if container_type == "list"
                    else (raw_client, address)
                )
                raw_listener = FakeListener(accept_events=[accepted])
                factory, listener, observer = armed_factory(raw_listener)
                target_line = publication_lines[container_type]

                def inject_oom(frame, event, argument):
                    if (
                        event == "line"
                        and frame.f_code
                        is stage2_seam.LateListenerSocket.accept.__code__
                        and frame.f_lineno == target_line
                    ):
                        sys.settrace(None)
                        raise MemoryError()
                    return inject_oom

                previous_trace = sys.gettrace()
                try:
                    sys.settrace(inject_oom)
                    with self.assertRaises(MemoryError):
                        listener.accept()
                finally:
                    sys.settrace(previous_trace)
                self.assertTrue(raw_client.closed)
                self.assertEqual(raw_client.close_calls, 1)
                self.assertEqual(observer.open_clients(), 0)
                self.assertIsNone(factory.listener._orphan_port)
                self.assertIsNone(factory.deinit())
                self.assertEqual(raw_client.close_calls, 1)

        for name, injected in (
            ("memory", MemoryError()),
            ("base", KeyboardInterrupt()),
        ):
            with self.subTest(name=name):
                state = stage2_seam.Stage2State()
                state.context.rest_runtime = runtime
                state.gate.seal_security(runtime)
                raw = FakeListener()
                factory = stage2_seam.LateSocketFactory(lambda: raw, state)
                listener = factory()
                listener.setblocking(False)
                listener.bind((smoke.AP_IP, 80))
                proof = types.SimpleNamespace(
                    prepare_proof=mock.Mock(side_effect=injected)
                )
                with mock.patch.object(
                    stage2_seam, "_load_proof", return_value=proof
                ):
                    with self.assertRaises(type(injected)):
                        listener.listen(2)
                proof.prepare_proof.assert_called_once_with(state.context)
                self.assertEqual(raw.listen_values, [])
                self.assertEqual(raw.accept_calls, 0)
                self.assertIs(state.gate.armed, False)
                self.assertIs(state.proof_loaded, False)
                self.assertIsNone(factory.deinit())
                self.assertTrue(raw.closed)

        state = stage2_seam.Stage2State()
        state.context.rest_runtime = runtime
        state.gate.seal_security(runtime)
        raw = FakeListener()
        factory = stage2_seam.LateSocketFactory(lambda: raw, state)
        listener = factory()
        listener.setblocking(False)
        listener.bind((smoke.AP_IP, 80))

        def reenter(_context):
            listener.accept()

        proof = types.SimpleNamespace(prepare_proof=reenter)
        with mock.patch.object(
            stage2_seam, "_load_proof", return_value=proof
        ):
            with self.assertRaises(RuntimeError):
                listener.listen(2)
        self.assertEqual(factory._reentries, 1)
        self.assertIs(factory._faulted, True)
        self.assertEqual(raw.accept_calls, 0)
        self.assertEqual(raw.listen_values, [])
        self.assertIsNone(factory.deinit())
        self.assertTrue(raw.closed)

    def test_confirmation_is_strict_and_import_is_inert(self):
        class EqualitySpoof:
            def __eq__(self, other):
                return True

        for confirmation in (None, EqualitySpoof(), "wrong"):
            with self.assertRaises(RuntimeError):
                smoke.run(confirmation, _TEST_PASSWORD, 60)
        with open(smoke.__file__, "r", encoding="utf-8") as stream:
            source = stream.read()
        tree = ast.parse(source)
        top_imports = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                top_imports.append(node.module)
        self.assertEqual(top_imports, ["gc", "sys"])

        fake_network = _fake_network()
        fake_network.interfaces[fake_network.IF_AP].enabled = False
        fake_network.interfaces[fake_network.IF_STA].enabled = False
        with mock.patch.dict(sys.modules, {"network": fake_network}), mock.patch.object(
            board_config, "WIFI_RADIO_APPROVED", False
        ):
            namespace = runpy.run_path(smoke.__file__, run_name="not_main")
        self.assertIn("run", namespace)
        self.assertIs(fake_network.interfaces[fake_network.IF_AP].enabled, False)
        self.assertIs(fake_network.interfaces[fake_network.IF_STA].enabled, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

    def test_failure_heap_diagnostic_never_collects(self):
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

    def test_bind_failure_survives_diagnostic_capture_memory_error(self):
        secret_text = "bind-diagnostic-secret-" + _TEST_PASSWORD

        class FailingBindListener(FakeListener):
            def bind(self, address):
                self.bind_values.append(address)
                raise OSError(77, secret_text)

        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        listener = FailingBindListener()
        filesystem = _MemoryFileSystem()

        with mock.patch.object(
            stage2_diagnostics,
            "capture",
            side_effect=MemoryError,
        ) as capture:
            result, error, output, _, _ = self._execute(
                fake,
                FakeListener(accept_events=[probe]),
                listener,
                filesystem=filesystem,
            )

        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertNotIsInstance(error, MemoryError)
        self.assertEqual(str(error), "Phase-8 full REST phone smoke failed")
        capture.assert_called_once()
        rendered = output + repr(error)
        self.assertNotIn(_TEST_PASSWORD, rendered)
        self.assertNotIn(secret_text, rendered)
        self.assertNotIn(str(OSError(77, secret_text)), rendered)
        self.assertNotIn(smoke.FULL_REST_PHONE_READY_TOKEN, output)
        self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
        self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
        lines = output.splitlines()
        diagnostic_index = lines.index(
            stage2.FULL_REST_PHONE_FAILURE_STAGE_TOKEN
        )
        self.assertEqual(
            lines[diagnostic_index + 1],
            stage2.FULL_REST_PHONE_FAIL_TOKEN,
        )
        self.assertEqual(listener.blocking_values, [False])
        self.assertEqual(listener.bind_values, [(smoke.AP_IP, 80)])
        self.assertEqual(listener.listen_values, [])
        self.assertTrue(listener.closed)
        self.assertTrue(fake.interfaces[fake.IF_AP].enabled is False)
        self.assertTrue(fake.interfaces[fake.IF_STA].enabled is False)
        self.assertIs(wifi_module._WIFI_LEASED, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
        for path in stage2._storage_paths():
            self.assertNotIn(path, filesystem.files)

    def test_stage1_unload_removes_registry_and_parent_attribute(self):
        import tools

        name = smoke._STAGE1_MODULE
        sys.modules[name] = stage1
        setattr(tools, "phase8_full_rest_phone_stage1", stage1)
        smoke._unload_stage1(stage1)
        self.assertNotIn(name, sys.modules)
        self.assertFalse(hasattr(tools, "phase8_full_rest_phone_stage1"))
        sys.modules[name] = stage1
        setattr(tools, "phase8_full_rest_phone_stage1", stage1)

    def test_full_late_module_denylist_and_stage1_frozen_origins(self):
        import tools

        expected = frozenset((
            smoke._STAGE2_SEAM_MODULE,
            smoke._STAGE2_PREPARE_MODULE,
            smoke._STAGE2_MODULE,
            smoke._STAGE2_DIAGNOSTICS_MODULE,
            "adapters.config_file_store",
            "app.application_state",
            "app.configuration_api_gateway",
            "app.configuration_bootstrap",
            "app.heater_controller",
            "app.manual_control_gateway",
            "app.network_composition",
            "app.rest_application",
            "app.rest_composition",
            "app.scheduler",
            "app.scheduler_controller_gateway",
            "app.temperature_manager",
            "protocol.autoterm_protocol",
            "services.config_manager",
            "services.configuration_errors",
            "services.rest_rate_limiter",
            "services.rest_security",
            "services.time_service",
        ))
        self.assertEqual(frozenset(smoke._LATE_ONLY_MODULES), expected)
        self.assertTrue(smoke._require_cold_late_modules({}))
        for module_name in expected:
            with self.subTest(module_name=module_name):
                with self.assertRaises(RuntimeError):
                    smoke._require_cold_late_modules({module_name: object()})
        for attribute_name in (
            "phase8_full_rest_phone_stage2_seam",
            "phase8_full_rest_phone_stage2_prepare",
            "phase8_full_rest_phone_stage2",
            "phase8_full_rest_phone_stage2_diagnostics",
        ):
            with self.subTest(attribute_name=attribute_name), mock.patch.object(
                smoke, "_require_cold_late_modules", return_value=True
            ), mock.patch.object(
                tools, attribute_name, object(), create=True
            ):
                with self.assertRaises(RuntimeError):
                    smoke._require_stage2_unloaded()

        origins = stage1._NETWORK_FROZEN_ORIGINS + stage1._HTTP_FROZEN_ORIGINS
        self.assertNotIn(("board_config", "board_config.py"), origins)
        self.assertNotIn(
            ("tools.phase7_network_smoke", "tools/phase7_network_smoke.py"),
            origins,
        )
        self.assertIn(("app.network_manager", "app/network_manager.py"), origins)
        self.assertIn(
            ("hardware.micropython_wifi", "hardware/micropython_wifi.py"),
            origins,
        )
        with open(stage1.__file__, "r", encoding="utf-8") as stream:
            stage1_tree = ast.parse(stream.read())
        imported_modules = set()
        for node in ast.walk(stage1_tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.add(node.module)
        self.assertNotIn("tools.phase7_network_smoke", imported_modules)
        self.assertIn(("services.strict_json", "services/strict_json.py"), origins)
        modules = {
            name: types.SimpleNamespace(__file__=origin)
            for name, origin in origins
        }
        frozen_sys = types.SimpleNamespace(path=[".frozen"], modules=modules)
        self.assertTrue(stage1._verify_frozen_origins(frozen_sys, origins))
        frozen_sys.path = [""]
        with self.assertRaises(RuntimeError):
            stage1._verify_frozen_origins(frozen_sys, origins)

    def test_six_module_upload_closure_is_self_contained_and_frozen(self):
        manifest_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "firmware",
            "phase8_frozen",
            "manifest.py",
        )
        declarations = []

        def package(name, base_path=None, opt=None, files=()):
            declarations.append((name, base_path, opt, tuple(files)))

        namespace = {
            "include": lambda path: None,
            "package": package,
        }
        with open(manifest_path, "rb") as stream:
            source = stream.read()
        exec(compile(source, manifest_path, "exec"), namespace)

        frozen_modules = set()
        for package_name, base_path, optimization, files in declarations:
            self.assertEqual(base_path, "../..")
            self.assertEqual(optimization, 0)
            for filename in files:
                module_tail = filename[:-3].replace("/", ".")
                if module_tail == "__init__":
                    frozen_modules.add(package_name)
                else:
                    frozen_modules.add(package_name + "." + module_tail)

        isolated_upload = frozenset((
            smoke._STAGE1_MODULE,
            smoke._STAGE2_SEAM_MODULE,
            smoke._STAGE2_PREPARE_MODULE,
            smoke._STAGE2_MODULE,
            smoke._STAGE2_DIAGNOSTICS_MODULE,
            "tools.phase8_full_rest_phone_smoke",
        ))
        available_modules = frozen_modules | isolated_upload
        frozen_contract = (
            stage1._NETWORK_FROZEN_ORIGINS
            + stage1._HTTP_FROZEN_ORIGINS
            + stage2_prepare._PRODUCT_FROZEN_ORIGINS
            + stage2_prepare._REST_FROZEN_ORIGINS
        )

        self.assertEqual(len(isolated_upload), 6)
        self.assertNotIn("tools.phase7_network_smoke", available_modules)
        self.assertNotIn(
            "tools.phase7_network_smoke",
            {module_name for module_name, _ in frozen_contract},
        )
        self.assertNotIn(
            "board_config",
            {module_name for module_name, _ in frozen_contract},
        )
        for helper_name in (
            "_verify_platform",
            "_verify_hardware_locks",
            "_check_platform_ticks",
            "_load_network_module",
            "_interfaces_inactive",
            "_cleanup_radio",
        ):
            self.assertTrue(callable(getattr(smoke, helper_name, None)))
        for module_name, origin in frozen_contract:
            with self.subTest(module_name=module_name):
                self.assertIn(module_name, frozen_modules)
                self.assertEqual(
                    origin,
                    module_name.replace(".", "/") + ".py",
                )
        self.assertEqual(
            frozen_modules & isolated_upload,
            set(),
        )
        project_root = os.path.dirname(os.path.dirname(__file__))
        for module_name in isolated_upload:
            module_path = os.path.join(
                project_root, *module_name.split(".")
            ) + ".py"
            self.assertTrue(os.path.isfile(module_path), module_name)
            with open(module_path, "r", encoding="utf-8") as stream:
                tree = ast.parse(stream.read())
            imported_tools = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_tools.update(
                        alias.name for alias in node.names
                        if alias.name.startswith("tools.")
                    )
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "tools"
                ):
                    imported_tools.update(
                        "tools." + alias.name for alias in node.names
                    )
            self.assertLessEqual(imported_tools, isolated_upload)
            self.assertNotIn("tools.phase7_network_smoke", imported_tools)

    def test_factory_and_manager_publish_failures_salvage_radio_ownership(self):
        for fault_name in ("port", "network_manager"):
            with self.subTest(fault_name=fault_name):
                fake = _fake_network()
                _script_ap_clients(fake, (0,))
                capsule = _FaultingCapsule(fault_name)
                result, error, output, _, _ = self._execute(
                    fake,
                    FakeListener(),
                    FakeListener(),
                    capsule=capsule,
                )
                self.assertIsNone(result)
                self.assertIsInstance(error, MemoryError)
                self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
                self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
                self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
                self.assertIs(wifi_module._WIFI_LEASED, False)
                self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

    def test_stage1_cleanup_failure_and_unload_failure_keep_outer_owner(self):
        original_probe_cleanup = stage1._cleanup_http_server

        def close_then_fail(server):
            original_probe_cleanup(server)
            raise MemoryError()

        for failure_kind in (
            "probe_cleanup",
            "probe_cleanup_after_close",
            "stage1_unload",
        ):
            with self.subTest(failure_kind=failure_kind):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1, 1))
                probe = FakeClientSocket(recv_events=[_request(
                    smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
                )])
                keywords = {}
                if failure_kind == "probe_cleanup":
                    keywords["stage1_cleanup"] = MemoryError
                elif failure_kind == "probe_cleanup_after_close":
                    keywords["stage1_cleanup"] = close_then_fail
                else:
                    keywords["unload"] = MemoryError
                result, error, output, _, _ = self._execute(
                    fake,
                    FakeListener(accept_events=[probe]),
                    FakeListener(),
                    **keywords
                )
                self.assertIsNone(result)
                self.assertIsInstance(error, MemoryError)
                self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
                self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
                self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
                self.assertIs(wifi_module._WIFI_LEASED, False)
                self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
                if failure_kind.startswith("probe_cleanup"):
                    diagnostics = _failure_diagnostics(output)
                    self.assertEqual(
                        diagnostics["stage"], "stage1_cleanup_http"
                    )
                    self.assertEqual(
                        diagnostics["stage1_client_seen"], "1"
                    )
                    self.assertEqual(
                        diagnostics["stage1_http_accepted"], "1"
                    )
                    self.assertEqual(
                        diagnostics["stage1_http_completed"], "1"
                    )
                    self.assertEqual(
                        diagnostics["stage1_http_started"], "1"
                    )
                    self.assertEqual(
                        diagnostics["stage1_http_closed"], "0"
                    )
                    self.assertEqual(diagnostics["stage1_valid"], "1")
                    self.assertEqual(diagnostics["stage1_responses"], "1")
                    self.assertEqual(
                        diagnostics["stage1_cleanup_confirmed"], "1"
                    )

    def test_stage1_cleanup_confirmation_requires_radio_cleanup(self):
        real_radio_cleanup = smoke._cleanup_radio
        secret = "stage1-radio-cleanup-secret-" + _TEST_PASSWORD

        for failure_kind in ("false", "raise"):
            with self.subTest(failure_kind=failure_kind):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1, 1))
                listener = FakeListener(
                    accept_events=[OSError(12, secret)]
                )
                cleanup_calls = {"count": 0}

                def cleanup_then_fail(*arguments):
                    cleanup_calls["count"] += 1
                    self.assertIs(real_radio_cleanup(*arguments), True)
                    if failure_kind == "raise":
                        raise RuntimeError(secret)
                    return False

                with mock.patch.object(
                    smoke,
                    "_cleanup_radio",
                    side_effect=cleanup_then_fail,
                ):
                    result, error, output, _, filesystem = self._execute(
                        fake,
                        listener,
                        FakeListener(),
                    )

                diagnostics = self._assert_stage1_failure_is_clean(
                    result,
                    error,
                    output,
                    fake,
                    listener,
                    filesystem,
                    secret,
                )
                self.assertEqual(cleanup_calls["count"], 1)
                self.assertEqual(
                    diagnostics["stage"], "stage1_observe_http_transport"
                )
                self.assertEqual(
                    diagnostics["stage1_cleanup_confirmed"], "0"
                )

    def test_split_transition_memory_and_baseexceptions_cleanup_all_owners(self):
        class StateConstructionFailureSeam:
            def __init__(self, error):
                self.error = error

            def __getattr__(self, name):
                return getattr(stage2_seam, name)

            def Stage2State(self):
                raise self.error

        for transition in (
            "seam_import",
            "state_construction",
            "prepare_import",
            "prepare_entry",
            "prepare_unload",
        ):
            for error_type in (MemoryError, KeyboardInterrupt):
                with self.subTest(
                    transition=transition, error_type=error_type.__name__
                ):
                    fake = _fake_network()
                    _script_ap_clients(fake, (0, 1, 1))
                    probe = FakeClientSocket(recv_events=[_request(
                        smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
                    )])
                    final_listener = FakeListener()
                    filesystem = _MemoryFileSystem()
                    proof_loads = {"count": 0}
                    injected = error_type()
                    keywords = {}

                    def forbidden_proof():
                        proof_loads["count"] += 1
                        raise AssertionError("proof loaded after transition failure")

                    if transition == "seam_import":
                        keywords["seam_loader"] = mock.Mock(
                            side_effect=injected
                        )
                    elif transition == "state_construction":
                        keywords["seam_loader"] = lambda: (
                            StateConstructionFailureSeam(injected)
                        )
                    elif transition == "prepare_import":
                        keywords["prepare_loader"] = mock.Mock(
                            side_effect=injected
                        )
                    elif transition == "prepare_entry":
                        failing_prepare = types.SimpleNamespace(
                            prepare=mock.Mock(side_effect=injected)
                        )
                        keywords["prepare_loader"] = lambda: failing_prepare
                    else:
                        keywords["unload_module"] = mock.Mock(
                            side_effect=injected
                        )

                    result, error, output, _, _ = self._execute(
                        fake,
                        FakeListener(accept_events=[probe]),
                        final_listener,
                        filesystem=filesystem,
                        stage2_loader=forbidden_proof,
                        **keywords
                    )

                    self.assertIsNone(result)
                    self.assertIsInstance(error, error_type)
                    self.assertEqual(proof_loads["count"], 0)
                    self.assertEqual(final_listener.bind_values, [])
                    self.assertEqual(final_listener.listen_values, [])
                    self.assertEqual(final_listener.accept_calls, 0)
                    self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
                    self.assertNotIn(smoke.FULL_REST_PHONE_READY_TOKEN, output)
                    self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
                    self.assertNotIn(_TEST_PASSWORD, output + repr(error))
                    self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
                    self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
                    self.assertIs(wifi_module._WIFI_LEASED, False)
                    self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
                    for path in stage2._storage_paths():
                        self.assertNotIn(path, filesystem.files)

    def test_success_cleanup_order_is_http_observer_gate_rest_radio_files(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        status = FakeClientSocket(recv_events=[_request(
            smoke.STATUS_PATH, smoke.AP_IP
        )])
        events = []
        real_http_cleanup = stage2._cleanup_http_server
        real_observer_cleanup = stage2._cleanup_observed_sockets
        real_gate_disarm = stage2_seam.DeferredReadOnlyRuntime.disarm
        real_rest_cleanup = stage2._cleanup_rest_runtime
        real_radio_cleanup = smoke._cleanup_radio
        real_file_cleanup = stage2._remove_exact_files

        def record(label, function):
            def wrapped(*arguments, **keywords):
                events.append(label)
                return function(*arguments, **keywords)
            return wrapped

        with mock.patch.object(
            stage2,
            "_cleanup_http_server",
            side_effect=record("http", real_http_cleanup),
        ), mock.patch.object(
            stage2,
            "_cleanup_observed_sockets",
            side_effect=record("observer", real_observer_cleanup),
        ), mock.patch.object(
            stage2_seam.DeferredReadOnlyRuntime,
            "disarm",
            autospec=True,
            side_effect=record("gate", real_gate_disarm),
        ), mock.patch.object(
            stage2,
            "_cleanup_rest_runtime",
            side_effect=record("rest", real_rest_cleanup),
        ), mock.patch.object(
            smoke,
            "_cleanup_radio",
            side_effect=record("radio", real_radio_cleanup),
        ), mock.patch.object(
            stage2,
            "_remove_exact_files",
            side_effect=record("files", real_file_cleanup),
        ):
            result, error, output, _, _ = self._execute(
                fake,
                FakeListener(accept_events=[probe]),
                FakeListener(accept_events=[status]),
            )

        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(
            events,
            ["http", "observer", "gate", "rest", "radio", "files"],
        )
        self.assertEqual(output.splitlines()[-1], smoke.FULL_REST_PHONE_PASS_TOKEN)

    def test_final_target_partial_or_send_failure_never_passes(self):
        cases = (
            (
                "partial_timeout",
                [lambda payload: len(payload)] + [OSError(11)] * 512,
            ),
            ("send_failure", [OSError(32, _TEST_PASSWORD)]),
        )
        for name, send_events in cases:
            with self.subTest(name=name):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1, 1, 1))
                probe = FakeClientSocket(recv_events=[_request(
                    smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
                )])
                target = FakeClientSocket(
                    recv_events=[_request(smoke.STATUS_PATH, smoke.AP_IP)],
                    send_events=send_events,
                    name=name,
                )
                result, error, output, _, filesystem = self._execute(
                    fake,
                    FakeListener(accept_events=[probe]),
                    FakeListener(accept_events=[target]),
                )
                self.assertIsNone(result)
                self.assertIsInstance(error, RuntimeError)
                self.assertIn(smoke.FULL_REST_PHONE_FAIL_TOKEN, output)
                self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
                self.assertNotIn(_TEST_PASSWORD, output + repr(error))
                self.assertTrue(target.closed)
                self.assertIs(wifi_module._WIFI_LEASED, False)
                self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
                for path in stage2._storage_paths():
                    self.assertNotIn(path, filesystem.files)

    def test_exact_wire_fsm_requires_canonical_send_length_and_successful_close(self):
        def canonical_wire(content_length):
            return (
                stage2._TARGET_RESPONSE_PREFIX
                + str(content_length).encode("ascii")
                + b"\r"
                + stage2._TARGET_RESPONSE_SUFFIX
                + b"x" * content_length
            )

        for content_length in (0, 1, 9, 10, 8192):
            with self.subTest(content_length=content_length):
                observer = stage2._SocketResponseObserver()
                raw = FakeClientSocket(name="canonical")
                client = observer.claim_client(raw)
                wire = canonical_wire(content_length)
                # One-byte sends place a transport boundary at every possible
                # position, including the Content-Length CR|LF boundary.
                for byte in wire:
                    self.assertEqual(client.send(bytes((byte,))), 1)
                self.assertIsNone(client.close())
                self.assertEqual(bytes(raw.written), wire)
                self.assertEqual(observer.target_headers, 1)
                self.assertEqual(observer.target_wires, 1)
                self.assertEqual(observer.target_completions, 1)
                self.assertEqual(observer.target_failures, 0)
                self.assertIs(observer.faulted, False)

        observer = stage2._SocketResponseObserver()
        raw = FakeClientSocket(name="partial")
        client = observer.claim_client(raw)
        partial = canonical_wire(10)[:-1]
        self.assertEqual(client.send(partial), len(partial))
        self.assertIsNone(client.close())
        self.assertEqual(observer.target_headers, 1)
        self.assertEqual(observer.target_wires, 0)
        self.assertEqual(observer.target_completions, 0)
        self.assertEqual(observer.target_failures, 1)

        observer = stage2._SocketResponseObserver()
        raw = FakeClientSocket(name="extra")
        client = observer.claim_client(raw)
        extra = canonical_wire(1) + b"!"
        self.assertEqual(client.send(extra), len(extra))
        self.assertIs(observer.faulted, True)
        self.assertIsNone(client.close())

        invalid_wires = (
            stage2._TARGET_RESPONSE_PREFIX
            + b"01\r"
            + stage2._TARGET_RESPONSE_SUFFIX
            + b"x",
            stage2._TARGET_RESPONSE_PREFIX
            + b"8193\r"
            + stage2._TARGET_RESPONSE_SUFFIX,
            b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n",
        )
        for index, wire in enumerate(invalid_wires):
            with self.subTest(invalid=index):
                observer = stage2._SocketResponseObserver()
                raw = FakeClientSocket(name="invalid")
                client = observer.claim_client(raw)
                self.assertEqual(client.send(wire), len(wire))
                self.assertIsNone(client.close())
                self.assertEqual(observer.target_headers, 0)
                self.assertEqual(observer.target_wires, 0)
                self.assertEqual(observer.target_completions, 0)

        observer = stage2._SocketResponseObserver()
        wire = canonical_wire(9)
        prefix_count = len(stage2._TARGET_RESPONSE_PREFIX)
        raw = FakeClientSocket(
            name="short_send", send_events=[prefix_count]
        )
        client = observer.claim_client(raw)
        self.assertEqual(client.send(wire), prefix_count)
        self.assertIsNone(client.close())
        self.assertEqual(bytes(raw.written), wire[:prefix_count])
        self.assertEqual(observer.target_headers, 0)
        self.assertEqual(observer.target_completions, 0)

        observer = stage2._SocketResponseObserver()
        raw = FakeClientSocket(
            name="close_retry", close_events=[OSError(5), None]
        )
        client = observer.claim_client(raw)
        wire = canonical_wire(1)
        self.assertEqual(client.send(wire), len(wire))
        with self.assertRaises(OSError):
            client.close()
        self.assertEqual(observer.target_completions, 0)
        self.assertEqual(observer.open_clients(), 1)
        self.assertIsNone(client.close())
        self.assertEqual(observer.target_completions, 1)
        self.assertEqual(observer.open_clients(), 0)

    def test_concurrent_captive_request_keeps_exact_target_proof(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        operations = []
        target = FakeClientSocket(
            recv_events=[_request(smoke.STATUS_PATH, smoke.AP_IP)],
            name="target",
            operation_log=operations,
        )
        captive = FakeClientSocket(
            recv_events=[_request("/generate_204", smoke.AP_IP)],
            name="captive",
            operation_log=operations,
        )
        result, error, output, _, _ = self._execute(
            fake,
            FakeListener(accept_events=[probe]),
            FakeListener(
                accept_events=[target, captive], operation_log=operations
            ),
        )
        self.assertIsNone(error)
        self.assertEqual(result["valid_status_requests"], 1)
        self.assertEqual(result["marked_status_responses"], 1)
        self.assertEqual(result["rejected_requests"], 1)
        self.assertEqual(result["completed_responses"], 2)
        target_head, _ = _wire_json(target)
        captive_head, captive_body = _wire_json(captive)
        self.assertIn(stage2._STATUS_PROOF_HEADER_LINE.rstrip(b"\r\n"), target_head)
        self.assertNotIn(stage2._STATUS_PROOF_HEADER_LINE.rstrip(b"\r\n"), captive_head)
        self.assertEqual(captive_body["error"]["code"], "not_found")
        self.assertEqual(output.splitlines()[-1], smoke.FULL_REST_PHONE_PASS_TOKEN)

    def test_concurrent_idle_browser_keeps_exact_target_proof(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        idle = FakeClientSocket(
            recv_events=[OSError(11)] * 512,
            name="idle_browser",
        )
        target = FakeClientSocket(
            recv_events=[_request(smoke.STATUS_PATH, smoke.AP_IP)],
            name="target",
        )

        result, error, output, _, _ = self._execute(
            fake,
            FakeListener(accept_events=[probe]),
            FakeListener(accept_events=[idle, target]),
        )

        self.assertIsNone(error)
        self.assertEqual(result["valid_status_requests"], 1)
        self.assertEqual(result["marked_status_responses"], 1)
        self.assertEqual(result["target_wire_completions"], 1)
        self.assertEqual(result["completed_responses"], 1)
        self.assertGreater(len(idle.recv_sizes), 0)
        self.assertTrue(idle.closed)
        self.assertTrue(target.closed)
        target_head, _ = _wire_json(target)
        self.assertIn(stage2._STATUS_PROOF_HEADER_LINE.rstrip(b"\r\n"), target_head)
        self.assertEqual(output.splitlines()[-1], smoke.FULL_REST_PHONE_PASS_TOKEN)
        self.assertIs(wifi_module._WIFI_LEASED, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

    def test_real_flow_rejects_live_and_stored_configuration_mismatch(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])

        proof_loads = {"count": 0}

        def prepare_with_mismatch(capsule, state, password, window_seconds):
            capsule.live_network_configuration["access_point"]["password"] = (
                "Different!42"
            )
            return stage2_prepare.prepare(
                capsule, state, password, window_seconds
            )

        prepare_proxy = types.SimpleNamespace(prepare=prepare_with_mismatch)

        def forbidden_proof_loader():
            proof_loads["count"] += 1
            raise AssertionError("proof loaded after configuration mismatch")

        final_listener = FakeListener()
        result, error, output, _, filesystem = self._execute(
            fake,
            FakeListener(accept_events=[probe]),
            final_listener,
            prepare_loader=lambda: prepare_proxy,
            stage2_loader=forbidden_proof_loader,
        )
        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertNotIn(smoke.FULL_REST_PHONE_READY_TOKEN, output)
        self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
        self.assertEqual(proof_loads["count"], 0)
        self.assertEqual(final_listener.bind_values, [])
        self.assertIs(wifi_module._WIFI_LEASED, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
        for path in stage2._storage_paths():
            self.assertNotIn(path, filesystem.files)

    def test_invalid_status_headers_schema_and_secrets_are_never_exposed(self):
        def nonempty_headers(response):
            response.headers["X-Unexpected"] = "public"

        def secret_header(response):
            response.headers["X-Debug"] = _TEST_CSRF_TOKEN_HEX

        def corrupt_schema(response):
            response.body["request_id"] = "corrupt"

        def csrf_exact_alias(response):
            response.body["warnings"].append(_TEST_CSRF_TOKEN_HEX)

        def csrf_embedded_uppercase_alias(response):
            response.body["warnings"].append(
                "debug=" + _TEST_CSRF_TOKEN_HEX.upper() + "!"
            )

        def csrf_unicode_prefix_alias(response):
            response.body["warnings"].append(
                "\N{LATIN SMALL LETTER E WITH ACUTE}" + _TEST_CSRF_TOKEN_HEX
            )

        def csrf_unicode_suffix_alias(response):
            response.body["warnings"].append(
                _TEST_CSRF_TOKEN_HEX + "\N{LATIN SMALL LETTER E WITH ACUTE}"
            )

        def csrf_unicode_key_alias(response):
            response.body[
                "\N{LATIN SMALL LETTER E WITH ACUTE}"
                + _TEST_CSRF_TOKEN_HEX
                + "!"
            ] = "public"

        def secret_body(response):
            response.body["unexpected"] = _TEST_PASSWORD

        for name, mutator in (
            ("nonempty_headers", nonempty_headers),
            ("secret_header", secret_header),
            ("corrupt_schema", corrupt_schema),
            ("csrf_exact_alias", csrf_exact_alias),
            ("csrf_embedded_uppercase_alias", csrf_embedded_uppercase_alias),
            ("csrf_unicode_prefix_alias", csrf_unicode_prefix_alias),
            ("csrf_unicode_suffix_alias", csrf_unicode_suffix_alias),
            ("csrf_unicode_key_alias", csrf_unicode_key_alias),
            ("secret_body", secret_body),
        ):
            with self.subTest(name=name):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 1, 1, 1))
                probe = FakeClientSocket(recv_events=[_request(
                    smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
                )])
                target = FakeClientSocket(recv_events=[_request(
                    smoke.STATUS_PATH, smoke.AP_IP
                )])
                protocol_ports = []
                observed_revisions = []
                real_protocol_port = stage2_seam.NullProtocolPort

                def tracked_protocol_port():
                    port = real_protocol_port()
                    protocol_ports.append(port)
                    return port

                def guarded_mutator(response):
                    observed_revisions.append(
                        response.body["heater"]["request_revision"]
                    )
                    mutator(response)

                with mock.patch.object(
                    stage2_seam,
                    "NullProtocolPort",
                    side_effect=tracked_protocol_port,
                ):
                    result, error, output, _, filesystem = self._execute(
                        fake,
                        FakeListener(accept_events=[probe]),
                        FakeListener(accept_events=[target]),
                        response_mutator=guarded_mutator,
                    )
                self.assertIsNone(result)
                self.assertIsInstance(error, RuntimeError)
                self.assertNotIn(smoke.FULL_REST_PHONE_PASS_TOKEN, output)
                rendered = output + bytes(target.written).decode(
                    "utf-8", "replace"
                )
                self.assertNotIn(_TEST_PASSWORD, rendered)
                self.assertNotIn(_TEST_CSRF_TOKEN_HEX, rendered)
                self.assertNotIn(_TEST_CSRF_TOKEN_HEX.upper(), rendered)
                self.assertNotIn(stage2._STATUS_PROOF_HEADER_LINE, target.written)
                self.assertEqual(observed_revisions, [0])
                self.assertEqual(len(protocol_ports), 1)
                self.assertEqual(protocol_ports[0].calls, 0)
                self.assertIs(wifi_module._WIFI_LEASED, False)
                self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
                for path in stage2._storage_paths():
                    self.assertNotIn(path, filesystem.files)

    def test_mutation_probe_never_reaches_heater_uart_or_csrf(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1, 1))
        probe = FakeClientSocket(recv_events=[_request(
            smoke.IP_CHECK_PATH, smoke.AP_IP + ":8080"
        )])
        mutation = FakeClientSocket(recv_events=[(
            "POST /api/v1/heater/stop HTTP/1.1\r\n"
            "Host: 192.168.4.1\r\nContent-Length: 0\r\n\r\n"
        ).encode("ascii")])
        status = FakeClientSocket(recv_events=[_request(
            smoke.STATUS_PATH, smoke.AP_IP
        )])
        final_listener = FakeListener(accept_events=[])

        def delayed_status():
            if not mutation.closed:
                final_listener.accept_events.insert(0, delayed_status)
                raise OSError(11)
            return status, ("192.168.4.2", 50123)

        final_listener.accept_events.extend([mutation, delayed_status])
        result, error, output, _, _ = self._execute(
            fake,
            FakeListener(accept_events=[probe]),
            final_listener,
        )
        self.assertIsNone(error)
        self.assertEqual(result["rejected_requests"], 1)
        self.assertEqual(result["heater_request_revision"], 0)
        self.assertEqual(result["protocol_calls"], 0)
        mutation_head, mutation_body = _wire_json(mutation)
        self.assertIn(b"HTTP/1.1 404 Not Found", mutation_head)
        self.assertEqual(mutation_body["error"]["code"], "not_found")
        rendered = output + bytes(mutation.written).decode("utf-8")
        self.assertNotIn("csrf_token", rendered)
        self.assertNotIn(_TEST_PASSWORD, rendered)
        self.assertEqual(output.splitlines()[-1], smoke.FULL_REST_PHONE_PASS_TOKEN)


if __name__ == "__main__":
    unittest.main()
