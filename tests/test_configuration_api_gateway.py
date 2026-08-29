import ast
import copy
import inspect
import unittest

import app.configuration_api_gateway as gateway_module
from app.configuration_api_gateway import (
    ConfigurationAPIConflictError,
    ConfigurationAPIGateway,
    ConfigurationAPIInvariantError,
    ConfigurationAPINotFoundError,
    ConfigurationAPIResourceConflictError,
    ConfigurationAPIValidationError,
)
from services.config_manager import (
    ConfigManager,
    default_configuration,
    default_scheduler_ledger,
)
from tests.test_config_manager import MemoryStore, record


def timer(timer_id="morning", power=5):
    return {
        "id": timer_id,
        "name": "Morning",
        "enabled": True,
        "weekdays": [0, 1, 2, 3, 4],
        "start": "06:30",
        "mode": "power",
        "target_temperature": None,
        "power_level": power,
        "runtime_minutes": 30,
    }


class FakeScheduler:
    def __init__(self):
        self.armed = True
        self.calls = 0
        self.result = None
        self.on_disarm = None

    def disarm(self):
        self.calls += 1
        changed = self.armed
        self.armed = False
        if self.on_disarm is not None:
            self.on_disarm()
        if self.result is not None:
            return self.result
        return changed


class FakeRuntime:
    def __init__(self, generation):
        self.generation = generation
        self.result = None

    def restart_required(self, manager):
        if self.result is not None:
            return self.result
        return manager.generation != self.generation


def build_manager(configuration=None):
    if configuration is None:
        configuration = default_configuration()
    config_store = MemoryStore([
        record("a", 1, configuration),
        record("b", 2, configuration),
    ])
    ledger = default_scheduler_ledger()
    ledger_store = MemoryStore([
        record("a", 1, ledger),
        record("b", 2, ledger),
    ])
    manager = ConfigManager(config_store, ledger_store)
    if not manager.load() or not manager.load_scheduler_checkpoint():
        raise AssertionError("test manager failed to load")
    return manager, config_store, ledger_store


def build_gateway(configuration=None):
    manager, store, ledger = build_manager(configuration)
    scheduler = FakeScheduler()
    runtime = FakeRuntime(manager.generation)
    network_runtime = FakeRuntime(manager.generation)
    gateway = ConfigurationAPIGateway(
        manager,
        scheduler,
        configured_runtime=runtime,
        configured_network_runtime=network_runtime,
    )
    return gateway, manager, store, ledger, scheduler, runtime, network_runtime


class TestConfigurationAPIGateway(unittest.TestCase):
    def test_public_settings_redact_all_wifi_passwords(self):
        configuration = default_configuration()
        configuration["network"]["access_point"]["password"] = "AP-secret-123"
        configuration["network"]["known_networks"] = [{
            "id": "home",
            "ssid": "Home WiFi",
            "password": "station-secret",
        }]
        gateway, _, _, _, _, _, _ = build_gateway(configuration)

        public = gateway.settings_snapshot()
        text = repr(public)
        self.assertNotIn("AP-secret-123", text)
        self.assertNotIn("station-secret", text)
        self.assertNotIn("'password':", text)
        self.assertTrue(
            public["network"]["access_point"]["password_configured"]
        )
        self.assertTrue(
            public["network"]["known_networks"][0]["password_configured"]
        )

    def test_settings_commit_preserves_secrets_and_disarms_old_runtime(self):
        configuration = default_configuration()
        configuration["network"]["access_point"]["password"] = "AP-secret-123"
        gateway, manager, store, _, scheduler, runtime, _ = build_gateway(
            configuration
        )
        heater = copy.deepcopy(configuration["heater"])
        heater["quick_start"]["power_level"] = None
        heater["quick_start"]["mode"] = "cabin_temperature"
        heater["quick_start"]["target_temperature"] = 19

        result = gateway.patch_settings({"heater": heater}, 2)

        self.assertTrue(result["changed"])
        self.assertEqual(result["generation"], 3)
        self.assertTrue(result["restart_required"])
        self.assertFalse(scheduler.armed)
        self.assertGreaterEqual(scheduler.calls, 2)
        self.assertEqual(len(store.commit_calls), 1)
        self.assertEqual(
            manager.snapshot()["configuration"]["network"]["access_point"][
                "password"
            ],
            "AP-secret-123",
        )
        self.assertNotIn("AP-secret-123", repr(result))
        self.assertEqual(runtime.generation, 2)

    def test_semantic_noop_does_not_disarm_or_write(self):
        gateway, manager, store, _, scheduler, _, _ = build_gateway()
        heater = manager.public_snapshot()["configuration"]["heater"]

        result = gateway.patch_settings({"heater": heater}, 2)

        self.assertFalse(result["changed"])
        self.assertEqual(result["generation"], 2)
        self.assertFalse(result["restart_required"])
        self.assertTrue(scheduler.armed)
        self.assertEqual(scheduler.calls, 0)
        self.assertEqual(store.commit_calls, [])
        self.assertEqual(gateway.snapshot()["noops"], 1)

    def test_create_replace_and_delete_timer_are_whole_document_commits(self):
        gateway, manager, _, _, scheduler, _, _ = build_gateway()

        created = gateway.create_timer(timer(), 2)
        self.assertTrue(created["changed"])
        self.assertEqual(created["generation"], 3)
        self.assertEqual(
            [item["id"] for item in created["configuration"]["timers"]],
            ["morning"],
        )
        self.assertFalse(scheduler.armed)

        replacement = timer(power=7)
        replaced = gateway.replace_timer("morning", replacement, 3)
        self.assertTrue(replaced["changed"])
        self.assertEqual(replaced["generation"], 4)
        self.assertEqual(
            replaced["configuration"]["timers"][0]["power_level"], 7
        )

        deleted = gateway.delete_timer("morning", 4)
        self.assertTrue(deleted["changed"])
        self.assertEqual(deleted["generation"], 5)
        self.assertEqual(deleted["configuration"]["timers"], [])
        self.assertEqual(manager.generation, 5)

    def test_timer_snapshot_is_detached(self):
        configuration = default_configuration()
        configuration["timers"] = [timer()]
        gateway, manager, _, _, _, _, _ = build_gateway(configuration)

        first = gateway.timers_snapshot()
        first["timers"][0]["name"] = "mutated"
        second = gateway.timers_snapshot()
        self.assertEqual(second["timers"][0]["name"], "Morning")
        self.assertEqual(
            manager.snapshot()["configuration"]["timers"][0]["name"],
            "Morning",
        )

    def test_stale_generation_duplicate_missing_and_path_mismatch(self):
        gateway, _, _, _, _, _, _ = build_gateway()
        with self.assertRaises(ConfigurationAPIConflictError):
            gateway.create_timer(timer(), 1)

        gateway.create_timer(timer(), 2)
        with self.assertRaises(ConfigurationAPIResourceConflictError):
            gateway.create_timer(timer(), 3)
        with self.assertRaises(ConfigurationAPINotFoundError):
            gateway.replace_timer("missing", timer("missing"), 3)
        with self.assertRaises(ConfigurationAPINotFoundError):
            gateway.delete_timer("missing", 3)
        with self.assertRaises(ValueError):
            gateway.replace_timer("morning", timer("other"), 3)

    def test_invalid_patch_and_timer_are_rejected_before_store_io(self):
        gateway, _, store, _, scheduler, _, _ = build_gateway()
        with self.assertRaises(ValueError):
            gateway.patch_settings({}, 2)
        with self.assertRaises(ValueError):
            gateway.patch_settings({"network": {}}, 2)
        bad = timer()
        bad["weekdays"] = [0, 0]
        with self.assertRaises(ConfigurationAPIValidationError):
            gateway.create_timer(bad, 2)
        self.assertEqual(store.commit_calls, [])
        self.assertTrue(scheduler.armed)

    def test_commit_failure_leaves_scheduler_disarmed(self):
        gateway, _, store, _, scheduler, _, _ = build_gateway()
        store.failure = OSError("disk unavailable")

        with self.assertRaises(OSError):
            gateway.create_timer(timer(), 2)
        self.assertFalse(scheduler.armed)
        self.assertGreaterEqual(scheduler.calls, 2)

    def test_commit_callback_reentry_never_returns_success_and_disarms(self):
        gateway, _, store, _, scheduler, _, _ = build_gateway()

        def reenter():
            try:
                gateway.timers_snapshot()
            except ConfigurationAPIInvariantError:
                pass

        store.on_commit = reenter
        with self.assertRaises(ConfigurationAPIInvariantError):
            gateway.create_timer(timer(), 2)
        self.assertFalse(scheduler.armed)
        self.assertTrue(gateway.faulted)
        store.on_commit = None
        with self.assertRaises(ConfigurationAPIInvariantError):
            gateway.create_timer(timer("evening"), 3)
        self.assertTrue(gateway.reset_fault())
        self.assertFalse(gateway.faulted)
        self.assertTrue(gateway.create_timer(timer("evening"), 3)["changed"])

    def test_reentrant_rearm_during_commit_is_fenced_afterward(self):
        gateway, _, store, _, scheduler, _, _ = build_gateway()

        def rearm():
            scheduler.armed = True

        store.on_commit = rearm
        result = gateway.create_timer(timer(), 2)
        self.assertTrue(result["changed"])
        self.assertFalse(scheduler.armed)

    def test_nonboolean_scheduler_contract_fails_before_commit(self):
        gateway, _, store, _, scheduler, _, _ = build_gateway()
        scheduler.result = 1
        with self.assertRaises(ConfigurationAPIInvariantError):
            gateway.create_timer(timer(), 2)
        self.assertEqual(store.commit_calls, [])
        self.assertFalse(scheduler.armed)

    def test_malformed_public_projection_with_password_faults(self):
        gateway, manager, _, _, _, _, _ = build_gateway()
        original = manager.public_snapshot

        def unsafe_public():
            value = original()
            value["configuration"]["network"]["access_point"][
                "password"
            ] = "leak"
            return value

        manager.public_snapshot = unsafe_public
        with self.assertRaises(ConfigurationAPIInvariantError):
            gateway.settings_snapshot()
        self.assertTrue(gateway.faulted)

    def test_module_has_no_hardware_protocol_or_network_import(self):
        source = inspect.getsource(gateway_module)
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertNotIn("machine", imports)
        self.assertNotIn("hardware", imports)
        self.assertNotIn("network", imports)
        self.assertNotIn("protocol", imports)


if __name__ == "__main__":
    unittest.main()
