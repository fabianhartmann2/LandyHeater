import ast
import inspect
import unittest

import app.network_composition as composition_module
from app.network_composition import build_configured_network
from services.config_manager import (
    ConfigManager,
    ConfigurationStateError,
    default_configuration,
    default_scheduler_ledger,
)
from tests.test_config_manager import MemoryStore
from tests.test_network_manager import FakePort


def provisioned_manager():
    manager = ConfigManager(MemoryStore(), MemoryStore())
    manager.load()
    manager.load_scheduler_checkpoint()
    manager.checkpoint_scheduler(default_scheduler_ledger(), 0)
    candidate = default_configuration()
    candidate["network"]["access_point"]["password"] = "unique-device-secret"
    manager.commit(candidate, 0)
    return manager


class TestNetworkComposition(unittest.TestCase):
    def test_import_and_construction_are_hardware_and_io_free(self):
        tree = ast.parse(inspect.getsource(composition_module))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        for forbidden in (
            "network", "machine", "board_config", "hardware", "protocol",
            "app.heater_controller",
            "services.config_manager",
        ):
            self.assertNotIn(forbidden, imports)
        port = FakePort()
        runtime = build_configured_network(provisioned_manager(), port)
        self.assertEqual(port.calls, [])
        self.assertFalse(runtime.manager.running)

    def test_unprovisioned_or_migrating_configuration_never_builds(self):
        manager = ConfigManager(MemoryStore(), MemoryStore())
        manager.load()
        manager.load_scheduler_checkpoint()
        with self.assertRaisesRegex(ConfigurationStateError, "not trusted"):
            build_configured_network(manager, FakePort())

    def test_generation_lease_reports_restart_after_commit(self):
        manager = provisioned_manager()
        runtime = build_configured_network(manager, FakePort())
        self.assertFalse(runtime.restart_required(manager))
        candidate = manager.snapshot()["configuration"]
        candidate["network"]["known_networks"] = [{
            "id": "home",
            "ssid": "Home",
            "password": "another-secret",
        }]
        manager.commit(candidate, manager.generation)
        self.assertTrue(runtime.restart_required(manager))

    def test_reentrant_generation_change_discards_staged_manager(self):
        manager = provisioned_manager()
        original = manager.network_configuration_for_runtime

        class RacingManager:
            generation = manager.generation
            network_start_allowed = True

            def network_configuration_for_runtime(self):
                value = original()
                self.generation += 1
                return value

        with self.assertRaisesRegex(ConfigurationStateError, "changed"):
            build_configured_network(RacingManager(), FakePort())


if __name__ == "__main__":
    unittest.main()
