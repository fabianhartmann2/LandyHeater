import ast
import inspect
import unittest

import app.rest_composition as composition_module
from app.rest_composition import (
    build_rest_http_server,
    build_rest_runtime,
    build_web_http_server,
)
from services.http_protocol import parse_request
from services.rest_security import INGRESS_ACCESS_POINT


class _ConfigManager:
    def __init__(self):
        self.generation = 2
        self.ledger_generation = 2
        self.timer_start_allowed = True

    def snapshot(self):
        raise AssertionError("construction must not read privileged config")

    def public_snapshot(self):
        raise AssertionError("construction must not read public config")

    def public_status(self):
        raise AssertionError("construction must not read config status")

    def commit(self, candidate, expected_generation):
        raise AssertionError("construction must not write config")


class _Scheduler:
    armed = False
    active_occurrence_key = None

    def disarm(self):
        raise AssertionError("construction must not mutate scheduler")

    def public_snapshot(self):
        return {
            "armed": False,
            "faulted": False,
            "active_occurrence_key": None,
            "event_count": 0,
        }

    def next_occurrence(self):
        return None


class _TemperatureManager:
    def snapshot(self):
        return {}


class _TimeService:
    def snapshot(self):
        return {}


class _ConfiguredRuntime:
    def __init__(self):
        self.scheduler = _Scheduler()
        self.temperature_manager = _TemperatureManager()
        self.time_service = _TimeService()

    def snapshot(self):
        return {
            "configuration_generation": 2,
            "ledger_generation": 2,
            "setup_complete": True,
            "persistent_start_gate_open": True,
            "quick_start": {
                "mode": "power",
                "target_temperature": None,
                "power_level": 2,
                "runtime_minutes": 15,
            },
            "clock_valid": False,
            "scheduler_armed": False,
        }

    def restart_required(self, config_manager):
        return False


class _Controller:
    requested_on = False
    request_revision = 0
    maximum_runtime_minutes = 120

    def manual_start_available(self, *args, **kwargs):
        return False

    def request_start(self, *args, **kwargs):
        raise AssertionError("construction must not request heat")

    def requested_matches(self, *args, **kwargs):
        return False

    def update_active_session(self, *args, **kwargs):
        raise AssertionError("construction must not update a session")

    def public_snapshot(self):
        return {}


class _SchedulerGateway:
    def request_manual_stop(self):
        raise AssertionError("construction must not request stop")

    def snapshot(self):
        return {
            "faulted": False,
            "last_error": None,
            "pending_override_key": None,
            "applied": 0,
            "rejected": 0,
            "manual_stops": 0,
            "checkpoints": 0,
            "checkpoint_failures": 0,
        }


class TestRestComposition(unittest.TestCase):
    def test_cold_module_import_does_not_eagerly_load_rest_graph(self):
        tree = ast.parse(inspect.getsource(composition_module))
        top_level_imports = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level_imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                top_level_imports.append(node.module)
        for deferred in (
            "app.configuration_api_gateway",
            "app.manual_control_gateway",
            "app.rest_application",
            "services.rest_rate_limiter",
            "services.rest_security",
        ):
            self.assertNotIn(deferred, top_level_imports)

    def _build(self, random_bytes=None, **overrides):
        if random_bytes is None:
            random_bytes = lambda count: b"r" * count
        arguments = {
            "config_manager": _ConfigManager(),
            "configured_runtime": _ConfiguredRuntime(),
            "controller": _Controller(),
            "scheduler_gateway": _SchedulerGateway(),
            "random_bytes": random_bytes,
            "allowed_hosts": ("192.168.4.1",),
            "ingress": INGRESS_ACCESS_POINT,
            "ticks_ms": lambda: 10,
            "ticks_diff": lambda a, b: a - b,
            "ticks_add": lambda a, b: a + b,
            "mem_free": lambda: 100000,
        }
        arguments.update(overrides)
        return build_rest_runtime(**arguments)

    def test_build_is_cold_and_security_start_is_explicit(self):
        calls = []

        def random_bytes(count):
            calls.append(count)
            return b"x" * count

        runtime = self._build(random_bytes=random_bytes)
        self.assertEqual(calls, [])
        self.assertFalse(
            runtime.snapshot()["security"]["mutation_api_available"]
        )
        self.assertTrue(runtime.start())
        self.assertEqual(calls, [32])
        self.assertFalse(runtime.start())
        self.assertIsNone(runtime.deinit())
        self.assertFalse(
            runtime.snapshot()["security"]["mutation_api_available"]
        )

    def test_builder_exposes_real_gateways_without_socket_or_radio_ownership(self):
        runtime = self._build()
        self.assertEqual(runtime.configuration_generation, 2)
        self.assertIs(runtime.application, runtime.application)
        self.assertFalse(runtime.configuration_gateway.faulted)
        self.assertFalse(runtime.manual_gateway.faulted)

    def test_server_factory_mandatorily_forwards_the_validated_peer(self):
        runtime = self._build()
        runtime.start()
        socket_calls = []

        def socket_factory():
            socket_calls.append(True)
            raise AssertionError("server construction must not open a socket")

        server = build_rest_http_server(
            runtime,
            "192.168.4.1",
            socket_factory=socket_factory,
            ticks_ms=lambda: 10,
            ticks_diff=lambda a, b: a - b,
            ticks_add=lambda a, b: a + b,
        )
        self.assertEqual(socket_calls, [])
        handler = server._MicroPythonHTTPServer__request_handler
        request = parse_request(
            b"GET /api/v1/security-context HTTP/1.1\r\n"
            b"Host: 192.168.4.1\r\n\r\n"
        )
        response = handler(request, "192.168.4.2")
        self.assertEqual(response.status, 200)
        self.assertTrue(response.body["mutation_api_available"])

    def test_web_server_factory_unifies_static_ui_and_api_on_one_listener(self):
        runtime = self._build()
        runtime.start()
        server = build_web_http_server(
            runtime,
            "192.168.4.1",
            socket_factory=lambda: (_ for _ in ()).throw(
                AssertionError("construction must not open a socket")
            ),
            ticks_ms=lambda: 10,
            ticks_diff=lambda a, b: a - b,
            ticks_add=lambda a, b: a + b,
        )
        handler = server._MicroPythonHTTPServer__request_handler
        home = handler(parse_request(
            b"GET / HTTP/1.1\r\nHost: 192.168.4.1\r\n\r\n"
        ), "192.168.4.2")
        self.assertEqual(home.status, 200)
        self.assertEqual(home.content_type, "text/html; charset=utf-8")
        api = handler(parse_request(
            b"GET /api/v1/security-context HTTP/1.1\r\n"
            b"Host: 192.168.4.1\r\n\r\n"
        ), "192.168.4.2")
        self.assertEqual(api.status, 200)
        self.assertIs(type(api.body), dict)

    def test_web_factory_uses_explicit_fixed_station_listener(self):
        runtime = self._build()
        runtime.start()
        server = build_web_http_server(
            runtime,
            "192.168.1.17",
            request_ingress="sta",
            captive_ap_address="192.168.4.1",
            socket_factory=lambda: (_ for _ in ()).throw(
                AssertionError("construction must not open a socket")
            ),
        )
        snapshot = server.snapshot()
        self.assertEqual(snapshot["bind_address"], "192.168.1.17")
        self.assertEqual(snapshot["request_ingress"], "sta")
        self.assertTrue(snapshot["ingress_dispatch"])

    def test_tick_helpers_are_all_or_nothing(self):
        for field in ("ticks_ms", "ticks_diff", "ticks_add"):
            with self.subTest(field=field):
                overrides = {
                    "ticks_ms": lambda: 1,
                    "ticks_diff": lambda a, b: a - b,
                    "ticks_add": lambda a, b: a + b,
                }
                overrides[field] = None
                with self.assertRaises(ValueError):
                    self._build(**overrides)

    def test_generation_change_during_construction_is_rejected(self):
        manager = _ConfigManager()

        class _ChangingController(_Controller):
            @property
            def requested_on(self):
                manager.generation = 3
                return False

        with self.assertRaisesRegex(RuntimeError, "changed"):
            self._build(config_manager=manager, controller=_ChangingController())

    def test_module_is_hardware_and_socket_free(self):
        with open("app/rest_composition.py", "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertTrue(
            imports.isdisjoint(
                {"machine", "network", "socket", "board_config", "hardware", "protocol"}
            )
        )


if __name__ == "__main__":
    unittest.main()
