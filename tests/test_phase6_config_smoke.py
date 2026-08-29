import ast
import contextlib
import io
import os
import runpy
import tempfile
import unittest
from unittest import mock

import tools.phase6_config_smoke as smoke_module
from tools.phase6_config_smoke import (
    MAX_ITERATIONS,
    PHASE6_PASS_TOKEN,
    SOFTWARE_ONLY_CONFIRMATION,
)


class TestPhase6ConfigSmoke(unittest.TestCase):
    def _run_in_temp(self, iterations=1):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config_base = os.path.join(
            directory.name, "phase6_usb_config_smoke_v1_config"
        )
        ledger_base = os.path.join(
            directory.name, "phase6_usb_config_smoke_v1_ledger"
        )
        patches = (
            mock.patch.object(
                smoke_module, "FLASH_CONFIG_BASE_PATH", config_base
            ),
            mock.patch.object(
                smoke_module, "FLASH_LEDGER_BASE_PATH", ledger_base
            ),
        )
        output = io.StringIO()
        with patches[0], patches[1], contextlib.redirect_stdout(output):
            result = smoke_module.run(
                SOFTWARE_ONLY_CONFIRMATION, iterations=iterations
            )
        return result, output.getvalue(), directory.name

    def test_full_memory_and_isolated_flash_roundtrip_passes(self):
        result, output, directory = self._run_in_temp(2)
        self.assertEqual(result["phase"], 6)
        self.assertEqual(result["scope"], "usb_only_configuration_storage")
        self.assertEqual(result["passed"], 2)
        self.assertEqual(result["configuration_generation"], 2)
        self.assertEqual(result["ledger_generation"], 4)
        self.assertEqual(result["flash_config_writes"], 2)
        self.assertEqual(result["flash_ledger_writes"], 4)
        self.assertIs(result["platform_ticks_checked"], False)
        self.assertIs(result["memory_checked"], False)
        self.assertEqual(output.splitlines()[-1], PHASE6_PASS_TOKEN)
        self.assertEqual(os.listdir(directory), [])

    def test_run_never_reads_wall_time_or_sleeps(self):
        with mock.patch("time.time", side_effect=AssertionError("wall time")):
            with mock.patch(
                "time.monotonic", side_effect=AssertionError("monotonic")
            ):
                with mock.patch(
                    "time.sleep", side_effect=AssertionError("sleep")
                ):
                    result, output, _ = self._run_in_temp(1)
        self.assertEqual(result["passed"], 1)
        self.assertEqual(output.splitlines()[-1], PHASE6_PASS_TOKEN)

    def test_confirmation_is_exact_and_type_strict_before_core_or_cleanup(self):
        class EqualitySpoof:
            def __ne__(self, other):
                return False

        with mock.patch.object(
            smoke_module,
            "_load_core",
            side_effect=AssertionError("core loaded"),
        ) as loader:
            with mock.patch.object(
                smoke_module,
                "_cleanup_flash_files",
                side_effect=AssertionError("cleanup started"),
            ) as cleanup:
                for value in (None, True, "yes", EqualitySpoof()):
                    with self.subTest(value=value):
                        with self.assertRaises(RuntimeError):
                            smoke_module.run(value, 1)
        loader.assert_not_called()
        cleanup.assert_not_called()

    def test_iteration_count_is_strictly_bounded(self):
        for value in (None, False, True, 0, -1, MAX_ITERATIONS + 1, "4"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    smoke_module.run(SOFTWARE_ONLY_CONFIRMATION, value)

    def test_failure_or_heap_false_pass_never_prints_token(self):
        output = io.StringIO()
        with mock.patch.object(
            smoke_module,
            "_exercise",
            side_effect=RuntimeError("synthetic persistence failure"),
        ):
            with contextlib.redirect_stdout(output):
                with self.assertRaises(RuntimeError):
                    smoke_module.run(SOFTWARE_ONLY_CONFIRMATION, 1)
        self.assertNotIn(PHASE6_PASS_TOKEN, output.getvalue())

        for measurements, platform_ticks in (
            ((100000, 80000, 78000, 70000), True),
            ((100000, 80000, 78000, 76000), False),
        ):
            with self.subTest(
                measurements=measurements, platform_ticks=platform_ticks
            ):
                output = io.StringIO()
                with mock.patch.object(
                    smoke_module, "_memory_free", side_effect=measurements
                ):
                    with mock.patch.object(
                        smoke_module,
                        "_check_platform_ticks",
                        return_value=platform_ticks,
                    ):
                        with mock.patch.object(
                            smoke_module,
                            "_run_flash_roundtrip",
                            return_value={
                                "config_writes": 2,
                                "ledger_writes": 4,
                            },
                        ):
                            with contextlib.redirect_stdout(output):
                                with self.assertRaises(RuntimeError):
                                    smoke_module.run(
                                        SOFTWARE_ONLY_CONFIRMATION, 1
                                    )
                self.assertNotIn(PHASE6_PASS_TOKEN, output.getvalue())

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
            "app.composition",
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
                    "tools/phase6_config_smoke.py",
                    run_name="phase6_config_smoke_import_test",
                )
        self.assertIn("run", namespace)
        self.assertEqual(output.getvalue(), "")

    def test_source_dependency_closure_is_hardware_free(self):
        with open(
            "tools/phase6_config_smoke.py", "r", encoding="utf-8"
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
                "os",
                "time",
                "adapters.config_file_store",
                "app.configuration_bootstrap",
                "app.scheduler_controller_gateway",
                "services.config_manager",
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
