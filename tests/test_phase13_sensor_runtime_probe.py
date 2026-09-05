import ast
import builtins
import inspect
import runpy
import types
import unittest
from unittest import mock

import tools.phase13_sensor_runtime_probe as probe


class TestPhase13SensorRuntimeProbe(unittest.TestCase):
    def test_import_is_inert(self):
        source = inspect.getsource(probe)
        tree = ast.parse(source)
        imported = []
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                imported.extend(alias.name for alias in statement.names)
            elif isinstance(statement, ast.ImportFrom):
                imported.append(statement.module)
        self.assertNotIn("board_config", imported)
        self.assertNotIn("network", imported)
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name in ("board_config", "network", "machine", "onewire", "ds18x20"):
                raise AssertionError("hardware import attempted")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            namespace = runpy.run_path(probe.__file__)
        self.assertIn("run", namespace)

    def test_wrong_confirmation_fails_before_hardware_import(self):
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name in ("board_config", "network", "machine", "onewire", "ds18x20"):
                raise AssertionError("hardware import attempted")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            with self.assertRaisesRegex(RuntimeError, "exact USB-only"):
                probe.run("wrong")

    def test_temperature_result_requires_exact_roles_and_healthy_values(self):
        sensors = {
            role: {
                "rom_id": rom_id,
                "value_c": float(index + 10),
                "usable": True,
                "health": "healthy",
            }
            for index, (role, rom_id) in enumerate(
                probe.EXPECTED_ASSIGNMENTS.items()
            )
        }
        snapshot = {
            "assignments": dict(probe.EXPECTED_ASSIGNMENTS),
            "discovered_rom_ids": tuple(probe.EXPECTED_ASSIGNMENTS.values()),
            "sensors": sensors,
        }
        values = probe._validate_temperature_snapshot(snapshot)
        self.assertEqual(values["roof_tent"], 10.0)

        broken = dict(snapshot)
        broken["discovered_rom_ids"] = tuple(
            list(probe.EXPECTED_ASSIGNMENTS.values())[:2]
        )
        with self.assertRaisesRegex(RuntimeError, "exact three"):
            probe._validate_temperature_snapshot(broken)

    def test_board_profile_opens_only_onewire(self):
        config = types.SimpleNamespace(
            BOARD_SKU="DFR0975-U",
            BOARD_HARDWARE_REVISION="1.0",
            BOARD_MODULE="ESP32-S3-WROOM-1U-N16R8",
            MICROPYTHON_TARGET="ESP32_GENERIC_S3",
            MICROPYTHON_BUILD_BOARD="DFR0975U_N16R8",
            MICROPYTHON_VARIANT="SPIRAM_OCT",
            MICROPYTHON_VERSION="1.28.0",
            UART_PINS_APPROVED=False,
            UART_PROTOCOL_TX_ENABLED=False,
            UART_TX_GATE_APPROVED=False,
            I2C_PINS_APPROVED=False,
            WIFI_RADIO_APPROVED=False,
            ONEWIRE_PIN=4,
            ONEWIRE_PIN_APPROVED=True,
            require_onewire_configuration=mock.Mock(return_value=None),
        )
        self.assertTrue(probe._validate_board_profile(config))
        config.I2C_PINS_APPROVED = True
        with self.assertRaisesRegex(RuntimeError, "I2C_PINS_APPROVED is open"):
            probe._validate_board_profile(config)


if __name__ == "__main__":
    unittest.main()
