import ast
import inspect
import os
import runpy
import unittest
from unittest import mock

import services.configuration_storage as storage_module
from services.configuration_storage import create_default_config_manager


class TestConfigurationStorageFactory(unittest.TestCase):
    def test_import_and_factory_construction_are_io_free(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("unexpected filesystem I/O")

        with mock.patch("builtins.open", side_effect=forbidden), mock.patch.object(
            os, "stat", side_effect=forbidden
        ), mock.patch.object(os, "remove", side_effect=forbidden), mock.patch.object(
            os, "rename", side_effect=forbidden
        ), mock.patch.object(os, "sync", side_effect=forbidden):
            namespace = runpy.run_path(
                storage_module.__file__, run_name="phase6_import_probe"
            )
            manager = namespace["create_default_config_manager"]()
            status = manager.status()

        self.assertEqual(
            status["config_store"]["base_path"], "/landy_heater_config"
        )
        self.assertEqual(
            status["ledger_store"]["base_path"], "/landy_heater_scheduler"
        )
        self.assertEqual(status["config_store"]["max_record_bytes"], 12 * 1024)
        self.assertEqual(status["ledger_store"]["max_record_bytes"], 24 * 1024)
        self.assertFalse(status["loaded"])
        self.assertFalse(status["ledger_loaded"])

    def test_public_factory_has_fixed_paths_and_no_unlock_arguments(self):
        manager = create_default_config_manager()
        status = manager.status()
        self.assertEqual(
            status["config_store"]["base_path"],
            storage_module.STATIC_CONFIG_BASE_PATH,
        )
        self.assertEqual(
            status["ledger_store"]["base_path"],
            storage_module.SCHEDULER_LEDGER_BASE_PATH,
        )
        self.assertEqual(inspect.signature(create_default_config_manager).parameters, {})

    def test_module_has_no_hardware_or_protocol_imports(self):
        tree = ast.parse(inspect.getsource(storage_module))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        for forbidden in ("machine", "board_config", "hardware", "protocol"):
            self.assertFalse(
                any(name == forbidden or name.startswith(forbidden + ".") for name in imports)
            )


if __name__ == "__main__":
    unittest.main()
