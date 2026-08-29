import ast
import contextlib
import io
import runpy
import unittest
from unittest import mock

from tools.phase5_integration_smoke import (
    MAX_ITERATIONS,
    PHASE5_PASS_TOKEN,
    SOFTWARE_ONLY_CONFIRMATION,
    run,
)


class TestPhase5IntegrationSmoke(unittest.TestCase):
    def test_deterministic_rtc_and_gateway_lifecycle_passes(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = run(SOFTWARE_ONLY_CONFIRMATION, iterations=2)

        self.assertEqual(result["phase"], 5)
        self.assertEqual(result["scope"], "usb_only_integration")
        self.assertEqual(result["iterations"], 2)
        self.assertEqual(result["passed"], 2)
        self.assertEqual(result["rtc_reads"], 2)
        self.assertEqual(result["rtc_writes"], 2)
        self.assertEqual(result["timer_starts"], 2)
        self.assertEqual(result["manual_stops"], 2)
        self.assertEqual(result["dst_checks"], 2)
        self.assertIs(result["platform_ticks_checked"], False)
        self.assertIs(result["memory_checked"], False)
        self.assertIn(
            "PHASE 5 USB-ONLY INTEGRATION SMOKE PASS: 2/2",
            output.getvalue(),
        )
        self.assertEqual(output.getvalue().splitlines()[-1], PHASE5_PASS_TOKEN)

    def test_run_never_reads_wall_time_or_sleeps(self):
        output = io.StringIO()
        with mock.patch("time.time", side_effect=AssertionError("wall time")):
            with mock.patch(
                "time.monotonic", side_effect=AssertionError("monotonic")
            ):
                with mock.patch(
                    "time.sleep", side_effect=AssertionError("sleep")
                ):
                    with contextlib.redirect_stdout(output):
                        result = run(SOFTWARE_ONLY_CONFIRMATION, iterations=1)
        self.assertEqual(result["passed"], 1)
        self.assertEqual(output.getvalue().splitlines()[-1], PHASE5_PASS_TOKEN)

    def test_iterations_are_strictly_bounded(self):
        for value in (None, False, True, 0, -1, MAX_ITERATIONS + 1, "4"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    run(SOFTWARE_ONLY_CONFIRMATION, value)

    def test_exact_confirmation_is_required_before_core_loading(self):
        class EqualitySpoof:
            def __ne__(self, other):
                return False

        with mock.patch(
            "tools.phase5_integration_smoke._load_software_core",
            side_effect=AssertionError("core loaded"),
        ) as loader:
            for confirmation in (None, True, "yes", EqualitySpoof()):
                with self.subTest(confirmation=confirmation):
                    with self.assertRaises(RuntimeError):
                        run(confirmation, 1)
        loader.assert_not_called()

    def test_failed_lifecycle_cannot_print_pass_token(self):
        output = io.StringIO()
        with mock.patch(
            "tools.phase5_integration_smoke._run_iteration",
            side_effect=RuntimeError("synthetic corruption"),
        ):
            with contextlib.redirect_stdout(output):
                with self.assertRaises(RuntimeError):
                    run(SOFTWARE_ONLY_CONFIRMATION, 1)
        self.assertNotIn(PHASE5_PASS_TOKEN, output.getvalue())

    def test_heap_collapse_or_unrecovered_drift_cannot_print_pass_token(self):
        variants = (
            (100000, 32000, 78000, 76000),
            (100000, 80000, 78000, 73000),
        )
        for measurements in variants:
            with self.subTest(measurements=measurements):
                output = io.StringIO()
                with mock.patch(
                    "tools.phase5_integration_smoke._memory_free",
                    side_effect=measurements,
                ):
                    with contextlib.redirect_stdout(output):
                        with self.assertRaises(RuntimeError):
                            run(SOFTWARE_ONLY_CONFIRMATION, 1)
                self.assertNotIn(PHASE5_PASS_TOKEN, output.getvalue())

        output = io.StringIO()
        with mock.patch(
            "tools.phase5_integration_smoke._memory_free",
            side_effect=(100000, 80000, 78000, 76000),
        ):
            with mock.patch(
                "tools.phase5_integration_smoke._check_platform_ticks",
                return_value=True,
            ):
                with contextlib.redirect_stdout(output):
                    result = run(SOFTWARE_ONLY_CONFIRMATION, 1)
        self.assertIs(result["memory_checked"], True)
        self.assertEqual(output.getvalue().splitlines()[-1], PHASE5_PASS_TOKEN)

        output = io.StringIO()
        with mock.patch(
            "tools.phase5_integration_smoke._memory_free",
            side_effect=(100000, 80000, 78000, 76000),
        ):
            with mock.patch(
                "tools.phase5_integration_smoke._check_platform_ticks",
                return_value=False,
            ):
                with contextlib.redirect_stdout(output):
                    with self.assertRaises(RuntimeError):
                        run(SOFTWARE_ONLY_CONFIRMATION, 1)
        self.assertNotIn(PHASE5_PASS_TOKEN, output.getvalue())

    def test_import_is_inert_and_hardware_independent(self):
        real_import = __import__
        forbidden = (
            "machine",
            "board_config",
            "onewire",
            "ds18x20",
            "hardware",
            "protocol",
            "app.heater_controller",
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
                    "tools/phase5_integration_smoke.py",
                    run_name="phase5_integration_smoke_import_test",
                )
        self.assertIn("run", namespace)
        self.assertEqual(output.getvalue(), "")

    def test_source_imports_only_hardware_free_dependencies(self):
        with open(
            "tools/phase5_integration_smoke.py", "r", encoding="utf-8"
        ) as handle:
            tree = ast.parse(handle.read())

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertEqual(
            imported,
            {
                "gc",
                "time",
                "adapters.ds3231_adapter",
                "app.scheduler",
                "app.scheduler_controller_gateway",
                "services.rtc_time_bridge",
                "services.time_service",
            },
        )
        forbidden_roots = {
            "machine",
            "board_config",
            "onewire",
            "ds18x20",
            "hardware",
            "protocol",
        }
        self.assertTrue(
            all(name.split(".")[0] not in forbidden_roots for name in imported)
        )


if __name__ == "__main__":
    unittest.main()
