import ast
import inspect
import unittest

import app.configuration_bootstrap as bootstrap_module
from app.configuration_bootstrap import build_configured_runtime
from services.config_manager import (
    ConfigManager,
    ConfigurationStateError,
    default_configuration,
    default_scheduler_ledger,
)
from tests.test_config_manager import (
    MemoryStore,
    configured_document,
    ledger,
    occurrence,
    record,
)


def provisioned_manager(configuration=None, ledger_value=None):
    if configuration is None:
        configuration = configured_document(True)
    if ledger_value is None:
        ledger_value = default_scheduler_ledger()
    config_store = MemoryStore()
    ledger_store = MemoryStore()
    manager = ConfigManager(config_store, ledger_store)
    manager.load()
    manager.load_scheduler_checkpoint()
    manager.checkpoint_scheduler(ledger_value, 0)
    manager.commit(configuration, 0)
    return manager


class TestConfigurationBootstrap(unittest.TestCase):
    def test_module_is_hardware_protocol_and_controller_free(self):
        tree = ast.parse(inspect.getsource(bootstrap_module))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        for forbidden in (
            "machine",
            "board_config",
            "hardware",
            "protocol",
            "app.heater_controller",
            "app.composition",
        ):
            self.assertFalse(
                any(name == forbidden or name.startswith(forbidden + ".") for name in imports)
            )

    def test_first_boot_build_is_cold_disarmed_and_has_no_timer(self):
        manager = ConfigManager(MemoryStore(), MemoryStore())
        manager.load()
        manager.load_scheduler_checkpoint()
        runtime = build_configured_runtime(
            manager,
            ticks_diff=lambda newer, older: newer - older,
            ticks_add=lambda value, delta: value + delta,
        )
        status = runtime.snapshot()
        self.assertFalse(status["setup_complete"])
        self.assertFalse(status["persistent_start_gate_open"])
        self.assertFalse(status["clock_valid"])
        self.assertFalse(status["scheduler_armed"])
        self.assertEqual(runtime.scheduler.snapshot()["timers"], [])

    def test_trusted_configuration_builds_all_pure_models_without_start(self):
        configuration = configured_document(True)
        configuration["heater"]["maximum_runtime_minutes"] = 90
        configuration["heater"]["quick_start"]["runtime_minutes"] = 45
        configuration["sensors"]["assignments"]["roof_tent"] = "28-roof"
        configuration["sensors"]["stale_after_ms"] = 20000
        configuration["sensors"]["failed_after_ms"] = 200000
        manager = provisioned_manager(configuration)

        runtime = build_configured_runtime(
            manager,
            ticks_diff=lambda newer, older: newer - older,
            ticks_add=lambda value, delta: value + delta,
        )
        self.assertTrue(runtime.persistent_start_gate_open)
        self.assertFalse(runtime.time_service.valid)
        self.assertEqual(runtime.time_service.timezone_name, "Europe/Zurich")
        self.assertEqual(runtime.time_service.timezone_rule, "europe_zurich")
        self.assertEqual(runtime.temperature_manager.stale_after_ms, 20000)
        self.assertEqual(runtime.temperature_manager.failed_after_ms, 200000)
        self.assertEqual(
            runtime.temperature_manager.assignments["roof_tent"], "28-roof"
        )
        self.assertEqual(runtime.scheduler.maximum_runtime_minutes, 90)
        self.assertEqual(len(runtime.scheduler.snapshot()["timers"]), 1)
        self.assertFalse(runtime.scheduler.armed)
        self.assertIsNone(runtime.scheduler.active_occurrence_key)

    def test_trusted_history_restores_only_consumed_safety_latches(self):
        item = occurrence()
        manager = provisioned_manager(
            configured_document(True), ledger([item])
        )
        runtime = build_configured_runtime(manager)
        self.assertEqual(
            runtime.scheduler.export_persistent_history(),
            {
                "consumed_local_high_water": item["local_minute_id"],
                "occurrences": [item],
            },
        )
        self.assertFalse(runtime.scheduler.armed)
        self.assertIsNone(runtime.scheduler.active_occurrence_key)

    def test_deleted_timer_diagnostic_is_filtered_but_high_water_remains(self):
        deleted = occurrence("deleted")
        manager = provisioned_manager(
            configured_document(True), ledger([deleted])
        )
        runtime = build_configured_runtime(manager)
        history = runtime.scheduler.export_persistent_history()
        self.assertEqual(history["occurrences"], [])
        self.assertEqual(
            history["consumed_local_high_water"], deleted["local_minute_id"]
        )

    def test_quick_start_is_data_only_and_snapshot_is_detached(self):
        manager = provisioned_manager()
        runtime = build_configured_runtime(manager)
        first = runtime.snapshot()
        self.assertEqual(first["quick_start"]["power_level"], None)
        self.assertFalse(runtime.scheduler.armed)
        first["quick_start"]["runtime_minutes"] = 1
        self.assertEqual(runtime.snapshot()["quick_start"]["runtime_minutes"], 60)

    def test_runtime_reports_restart_after_persisted_constructor_change(self):
        manager = provisioned_manager()
        runtime = build_configured_runtime(manager)
        self.assertFalse(runtime.restart_required(manager))
        candidate = manager.snapshot()["configuration"]
        candidate["sensors"]["stale_after_ms"] = 25000
        manager.commit(candidate, manager.generation)
        self.assertTrue(runtime.restart_required(manager))

    def test_healthy_live_ledger_checkpoint_does_not_require_restart(self):
        manager = provisioned_manager()
        runtime = build_configured_runtime(manager)
        item = occurrence()
        history = {
            "consumed_local_high_water": item["local_minute_id"],
            "occurrences": [item],
        }

        self.assertTrue(
            manager.checkpoint_scheduler_history(
                history, manager.ledger_generation
            )
        )
        self.assertNotEqual(
            manager.ledger_generation, runtime.ledger_generation
        )
        self.assertTrue(manager.timer_start_allowed)
        self.assertFalse(runtime.restart_required(manager))

    def test_runtime_reports_restart_when_same_generation_loses_trust(self):
        manager = provisioned_manager()
        runtime = build_configured_runtime(manager)
        manager._config_store.failure = OSError("write failed")
        candidate = manager.snapshot()["configuration"]
        candidate["sensors"]["stale_after_ms"] = 25000
        with self.assertRaises(OSError):
            manager.commit(candidate, manager.generation)
        self.assertTrue(runtime.restart_required(manager))

    def test_setup_incomplete_never_opens_persistent_start_gate(self):
        configuration = default_configuration()
        configuration["timers"] = [configured_document(True)["timers"][0]]
        manager = provisioned_manager(configuration)
        self.assertFalse(manager.timer_start_allowed)
        runtime = build_configured_runtime(manager)
        self.assertFalse(runtime.persistent_start_gate_open)
        self.assertFalse(runtime.scheduler.armed)

    def test_recovery_configuration_can_build_for_diagnostics_but_has_no_timers(self):
        damaged_store = MemoryStore(
            [record("a", 2, configured_document(True))]
        )
        manager = ConfigManager(damaged_store, MemoryStore())
        manager.load()
        manager.load_scheduler_checkpoint()
        runtime = build_configured_runtime(manager)
        self.assertFalse(runtime.persistent_start_gate_open)
        self.assertEqual(runtime.scheduler.snapshot()["timers"], [])

    def test_generation_race_discards_the_cold_staged_runtime(self):
        manager = provisioned_manager()

        class RacingManager:
            def __init__(self, inner):
                self.inner = inner
                self.raced = False

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def scheduler_checkpoint(self):
                value = self.inner.scheduler_checkpoint()
                self.raced = True
                return value

            @property
            def ledger_generation(self):
                return self.inner.ledger_generation + (1 if self.raced else 0)

        with self.assertRaisesRegex(ConfigurationStateError, "changed during build"):
            build_configured_runtime(RacingManager(manager))

    def test_bootstrap_never_refetches_validated_ledger_history(self):
        manager = provisioned_manager(
            configured_document(True), ledger([occurrence()])
        )

        class RefetchTrap:
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def scheduler_history_for_restore(self):
                raise AssertionError("bootstrap refetched scheduler history")

        runtime = build_configured_runtime(RefetchTrap(manager))
        self.assertEqual(
            runtime.scheduler.export_persistent_history()["occurrences"],
            [occurrence()],
        )


if __name__ == "__main__":
    unittest.main()
