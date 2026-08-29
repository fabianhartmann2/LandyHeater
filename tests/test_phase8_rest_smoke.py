import ast
import contextlib
import io
import runpy
import unittest
from unittest import mock

import tools.phase8_rest_smoke as smoke


class _EqualitySpoof:
    def __eq__(self, other):
        return True


class TestPhase8RestSmoke(unittest.TestCase):
    def _run(self, iterations=1):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = smoke.run(
                smoke.SOFTWARE_ONLY_CONFIRMATION,
                iterations=iterations,
            )
        return result, output.getvalue()

    def test_full_fake_socket_rest_roundtrip_passes_after_cleanup(self):
        result, output = self._run(2)
        self.assertEqual(result["phase"], 8)
        self.assertEqual(result["scope"], "usb_only_rest_fake_socket")
        self.assertEqual(result["passed"], 2)
        self.assertEqual(result["requests"], 6)
        self.assertEqual(result["mutations"], 2)
        self.assertEqual(result["errors"], 2)
        self.assertEqual(result["server_completed"], 6)
        self.assertEqual(result["peer_count"], 1)
        self.assertEqual(result["stop_bypasses"], 2)
        self.assertEqual(result["maximum_step_actions"], 1)
        self.assertTrue(result["cleanup_confirmed"])
        self.assertFalse(result["platform_ticks_checked"])
        self.assertFalse(result["memory_checked"])
        self.assertEqual(output.splitlines()[-1], smoke.PHASE8_PASS_TOKEN)

        csrf = "".join("{:02x}".format(value) for value in range(32))
        self.assertNotIn(csrf, output)
        self.assertNotIn(csrf, repr(result))
        self.assertNotIn("csrf_token", output)
        self.assertNotIn("password", output.lower())

    def test_confirmation_is_exact_and_type_strict_before_any_work(self):
        with mock.patch.object(
            smoke, "_memory_free", side_effect=AssertionError("heap read")
        ) as heap, mock.patch.object(
            smoke, "_load_core", side_effect=AssertionError("core loaded")
        ) as loader:
            for value in (None, True, 1, "wrong", _EqualitySpoof()):
                with self.subTest(value=value):
                    with self.assertRaises(RuntimeError):
                        smoke.run(value, 1)
        heap.assert_not_called()
        loader.assert_not_called()

    def test_iterations_are_strictly_bounded_before_any_work(self):
        invalid = (None, False, True, 0, -1, smoke.MAX_ITERATIONS + 1, "4")
        with mock.patch.object(
            smoke, "_memory_free", side_effect=AssertionError("heap read")
        ) as heap, mock.patch.object(
            smoke, "_load_core", side_effect=AssertionError("core loaded")
        ) as loader:
            for value in invalid:
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        smoke.run(smoke.SOFTWARE_ONLY_CONFIRMATION, value)
        heap.assert_not_called()
        loader.assert_not_called()

    def test_import_is_inert_and_forbidden_hardware_never_imports(self):
        real_import = __import__
        forbidden = (
            "machine",
            "network",
            "board_config",
            "hardware",
            "protocol",
            "socket",
        )

        def guarded_import(name, *args, **kwargs):
            for blocked in forbidden:
                if name == blocked or name.startswith(blocked + "."):
                    raise AssertionError(
                        "forbidden import attempted: {}".format(name)
                    )
            return real_import(name, *args, **kwargs)

        output = io.StringIO()
        with mock.patch("builtins.__import__", side_effect=guarded_import):
            with contextlib.redirect_stdout(output):
                namespace = runpy.run_path(
                    "tools/phase8_rest_smoke.py",
                    run_name="phase8_rest_smoke_import_test",
                )
        self.assertIn("run", namespace)
        self.assertEqual(output.getvalue(), "")

    def test_source_import_closure_excludes_hardware_protocol_and_socket(self):
        with open(
            "tools/phase8_rest_smoke.py", "r", encoding="utf-8"
        ) as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {
            "machine",
            "network",
            "board_config",
            "hardware",
            "protocol",
            "socket",
        }
        self.assertTrue(
            all(name.split(".")[0] not in forbidden for name in imported)
        )

    def test_resident_rest_imports_do_not_eagerly_load_full_config_schema(self):
        for path in (
            "app/configuration_api_gateway.py",
            "app/rest_application.py",
        ):
            with self.subTest(path=path), open(
                path, "r", encoding="utf-8"
            ) as handle:
                tree = ast.parse(handle.read())
            top_level = set()
            for node in tree.body:
                if isinstance(node, ast.Import):
                    top_level.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top_level.add(node.module)
            self.assertNotIn("services.config_manager", top_level)

    def test_micropython_exceptions_never_call_builtin_init_methods(self):
        forbidden_owners = {"Exception", "ValueError", "RuntimeError"}
        for path in (
            "services/http_protocol.py",
            "services/rest_rate_limiter.py",
            "app/rest_application.py",
            "adapters/micropython_http_server.py",
        ):
            with self.subTest(path=path), open(
                path, "r", encoding="utf-8"
            ) as handle:
                tree = ast.parse(handle.read())
            bad_calls = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                if (
                    isinstance(function, ast.Attribute)
                    and function.attr == "__init__"
                    and isinstance(function.value, ast.Name)
                    and function.value.id in forbidden_owners
                ):
                    bad_calls.append(function.value.id)
            self.assertEqual(bad_calls, [])

    def test_oom_and_terminal_failure_cleanup_without_false_pass(self):
        original_builder = smoke._build_fixture
        for failure, expected in (
            (MemoryError("synthetic OOM"), MemoryError),
            (KeyboardInterrupt(), KeyboardInterrupt),
        ):
            captured = {}

            def capture(core):
                fixture = original_builder(core)
                captured["fixture"] = fixture
                return fixture

            output = io.StringIO()
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(
                    smoke, "_build_fixture", side_effect=capture
                ), mock.patch.object(
                    smoke, "_exercise_iteration", side_effect=failure
                ), contextlib.redirect_stdout(output):
                    with self.assertRaises(expected):
                        smoke.run(smoke.SOFTWARE_ONLY_CONFIRMATION, 1)
                fixture = captured["fixture"]
                self.assertTrue(fixture.server.closed)
                self.assertTrue(fixture.listener.closed)
                self.assertFalse(
                    fixture.runtime.security_policy.snapshot()[
                        "mutation_api_available"
                    ]
                )
                self.assertNotIn(smoke.PHASE8_PASS_TOKEN, output.getvalue())

    def test_heap_failure_cleans_up_and_never_prints_pass(self):
        captured = {}
        original_builder = smoke._build_fixture

        def capture(core):
            fixture = original_builder(core)
            captured["fixture"] = fixture
            return fixture

        output = io.StringIO()
        measurements = (100000, 90000, 80000, 60000)
        with mock.patch.object(
            smoke, "_memory_free", side_effect=measurements
        ), mock.patch.object(
            smoke, "_build_fixture", side_effect=capture
        ), contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, "did not recover"):
                smoke.run(smoke.SOFTWARE_ONLY_CONFIRMATION, 1)
        self.assertTrue(captured["fixture"].server.closed)
        self.assertTrue(captured["fixture"].listener.closed)
        self.assertNotIn(smoke.PHASE8_PASS_TOKEN, output.getvalue())

    def test_pass_token_is_printed_only_after_server_and_token_cleanup(self):
        captured = {}
        events = []
        original_builder = smoke._build_fixture
        original_cleanup = smoke._cleanup_fixture

        def capture(core):
            fixture = original_builder(core)
            captured["fixture"] = fixture
            return fixture

        def cleanup(fixture):
            result = original_cleanup(fixture)
            events.append("cleanup")
            return result

        def recording_print(*values, **keywords):
            events.append(values[0])
            if values[0] == smoke.PHASE8_PASS_TOKEN:
                fixture = captured["fixture"]
                self.assertTrue(fixture.server.closed)
                self.assertTrue(fixture.listener.closed)
                self.assertFalse(
                    fixture.runtime.security_policy.snapshot()[
                        "mutation_api_available"
                    ]
                )

        with mock.patch.object(
            smoke, "_build_fixture", side_effect=capture
        ), mock.patch.object(
            smoke, "_cleanup_fixture", side_effect=cleanup
        ), mock.patch("builtins.print", side_effect=recording_print):
            result = smoke.run(smoke.SOFTWARE_ONLY_CONFIRMATION, 1)
        self.assertTrue(result["cleanup_confirmed"])
        self.assertLess(events.index("cleanup"), events.index(smoke.PHASE8_PASS_TOKEN))


if __name__ == "__main__":
    unittest.main()
