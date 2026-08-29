import copy
import json
import unittest
from unittest import mock

import adapters.config_file_store as config_store_module
from adapters.config_file_store import AtomicJSONConfigStore
from services.config_manager import (
    CONFIG_SCHEMA_VERSION,
    LOAD_FIRST_BOOT,
    LOAD_INVALID,
    LOAD_OK,
    LOAD_RECOVERY_REQUIRED,
    SCHEDULER_LEDGER_SCHEMA_VERSION,
    ConfigManager,
    ConfigurationConflictError,
    ConfigurationValidationError,
    default_configuration,
    default_scheduler_ledger,
    migrate_configuration_v1_to_v2,
    validate_configuration,
    validate_scheduler_ledger,
)
from services.time_service import civil_to_utc_seconds
from tests.test_config_file_store import MemoryFileSystem


_EXPECTED_RECOVERY_MINUTE = (
    civil_to_utc_seconds(2026, 8, 9, 14, 30, 0) // 60
)


def _fingerprint(payload):
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    value = 0
    for byte in data:
        value = ((value * 33) ^ byte) & 0xFFFFFFFF
    return value


def record(slot, generation, payload, fingerprint=None):
    canonical_payload = json.dumps(payload, separators=(",", ":"))
    if fingerprint is None:
        fingerprint = _fingerprint(payload)
    return {
        "slot": slot,
        "generation": generation,
        "payload": copy.deepcopy(payload),
        "fingerprint": fingerprint,
        "canonical_payload": canonical_payload,
    }


class MemoryStore:
    def __init__(self, records=None, invalid_slots=0, failure=None):
        self.records = [] if records is None else copy.deepcopy(records)
        self.invalid_slots = invalid_slots
        self.failure = failure
        self.load_calls = 0
        self.commit_calls = []
        self.reseal_calls = []
        self.on_load = None
        self.on_commit = None

    def load_records(self):
        self.load_calls += 1
        if self.on_load is not None:
            self.on_load()
        if self.failure is not None:
            raise self.failure
        return tuple(copy.deepcopy(self.records))

    def commit(self, payload, generation, expected_generation):
        self.commit_calls.append(
            (copy.deepcopy(payload), generation, expected_generation)
        )
        if self.on_commit is not None:
            self.on_commit()
        if self.failure is not None:
            raise self.failure
        latest = 0
        for item in self.records:
            latest = max(latest, item["generation"])
        if latest != expected_generation or generation != latest + 1:
            raise RuntimeError("fake store generation conflict")
        target = "a"
        if self.records:
            slots = {item["slot"] for item in self.records}
            if len(self.records) == 1:
                target = "b" if "a" in slots else "a"
            else:
                oldest = min(self.records, key=lambda item: item["generation"])
                target = oldest["slot"]
                self.records = [
                    item for item in self.records if item["slot"] != target
                ]
        self.records.append(record(target, generation, payload))
        return True

    def status(self):
        return {
            "invalid_slots": self.invalid_slots,
            "writes": len(self.commit_calls),
        }

    def _recovery_signature(self):
        tokens = []
        for item in self.records:
            tokens.append((
                item["slot"],
                item["generation"],
                item["fingerprint"],
                item["canonical_payload"],
            ))
        return (tuple(tokens), self.invalid_slots)

    def inspect_recovery(self):
        records = self.load_records()
        return records, self._recovery_signature()

    def reseal(self, payload, expected_recovery_signature=None):
        self.reseal_calls.append(copy.deepcopy(payload))
        if (
            expected_recovery_signature is not None
            and expected_recovery_signature != self._recovery_signature()
        ):
            raise RuntimeError("fake store recovery view changed")
        if self.on_commit is not None:
            self.on_commit()
        if self.failure is not None:
            raise self.failure
        latest = 0
        for item in self.records:
            latest = max(latest, item["generation"])
        first = latest + 1
        second = latest + 2
        self.records = [
            record("a", first, payload),
            record("b", second, payload),
        ]
        self.commit_calls.append((copy.deepcopy(payload), first, latest))
        self.commit_calls.append((copy.deepcopy(payload), second, first))
        return second


def timer(timer_id="weekday", weekdays=None):
    return {
        "id": timer_id,
        "name": "Morning heater",
        "enabled": True,
        "weekdays": [6] if weekdays is None else weekdays,
        "start": "14:30",
        "mode": "power",
        "target_temperature": None,
        "power_level": 5,
        "runtime_minutes": 30,
    }


def configured_document(with_timer=False):
    candidate = default_configuration()
    candidate["network"]["access_point"]["password"] = "test-device-secret"
    candidate["system"]["setup_complete"] = True
    if with_timer:
        candidate["timers"] = [timer()]
    return candidate


def occurrence(timer_id="weekday", status="consumed"):
    minute_id = civil_to_utc_seconds(2026, 8, 9, 14, 30, 0) // 60
    return {
        "timer_id": timer_id,
        "occurrence_key": timer_id + "|2026-08-09|14:30",
        "local_minute_id": minute_id,
        "status": status,
        "overridden": status == "overridden",
    }


def ledger(items=None, high_water=None):
    if items is None:
        items = []
    if high_water is None and items:
        high_water = max(item["local_minute_id"] for item in items)
    return {
        "schema_version": SCHEDULER_LEDGER_SCHEMA_VERSION,
        "consumed_local_high_water": high_water,
        "occurrences": copy.deepcopy(items),
    }


class EqualitySpoof:
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False


class TestConfigurationValidation(unittest.TestCase):
    def test_default_is_valid_detached_and_has_no_start_authority(self):
        first = default_configuration()
        second = validate_configuration(first)
        self.assertEqual(second, first)
        self.assertFalse(second["system"]["setup_complete"])
        self.assertEqual(second["timers"], [])
        first["heater"]["quick_start"]["runtime_minutes"] = 1
        self.assertEqual(second["heater"]["quick_start"]["runtime_minutes"], 60)

    def test_setup_complete_requires_an_individual_ap_password(self):
        candidate = default_configuration()
        candidate["system"]["setup_complete"] = True
        with self.assertRaisesRegex(ValueError, "password is required"):
            validate_configuration(candidate)
        candidate["network"]["access_point"]["password"] = (
            "individual-device-secret"
        )
        self.assertTrue(
            validate_configuration(candidate)["system"]["setup_complete"]
        )

    def test_exact_shape_and_json_primitive_types_are_required(self):
        for mutation in (
            lambda value: value.update({"unknown": 1}),
            lambda value: value.pop("system"),
            lambda value: value.__setitem__("schema_version", True),
            lambda value: value.__setitem__("schema_version", EqualitySpoof()),
            lambda value: value["system"].__setitem__("setup_complete", 1),
            lambda value: value["heater"].__setitem__(
                "maximum_runtime_minutes", True
            ),
        ):
            with self.subTest(mutation=mutation):
                candidate = default_configuration()
                mutation(candidate)
                with self.assertRaises(ConfigurationValidationError):
                    validate_configuration(candidate)

    def test_canonical_payload_size_is_bounded_before_storage(self):
        candidate = default_configuration()
        candidate["timers"] = []
        for index in range(32):
            candidate["timers"].append(timer(
                ("\x02" * 61) + "{:03d}".format(index)
            ))
            candidate["timers"][-1]["name"] = "\x01" * 80
        candidate["sensors"]["assignments"] = {
            "roof_tent": "\x03" * 64,
            "cabin": "\x04" * 64,
            "outside": "\x05" * 64,
        }
        with self.assertRaisesRegex(
            ConfigurationValidationError, "canonical payload"
        ):
            validate_configuration(candidate)

    def test_runtime_cap_applies_to_quick_start_and_every_timer(self):
        candidate = configured_document(True)
        candidate["heater"]["maximum_runtime_minutes"] = 20
        with self.assertRaises(ConfigurationValidationError):
            validate_configuration(candidate)
        candidate["heater"]["quick_start"]["runtime_minutes"] = 20
        candidate["timers"][0]["runtime_minutes"] = 21
        with self.assertRaises(ConfigurationValidationError):
            validate_configuration(candidate)
        candidate["timers"][0]["runtime_minutes"] = 20
        self.assertEqual(
            validate_configuration(candidate)["timers"][0]["runtime_minutes"],
            20,
        )

    def test_timer_fields_are_strict_and_weekdays_are_canonical(self):
        candidate = configured_document()
        candidate["timers"] = [timer(weekdays=[6, 0, 3])]
        normalized = validate_configuration(candidate)
        self.assertEqual(normalized["timers"][0]["weekdays"], [0, 3, 6])
        candidate["timers"][0]["id"] = "bad|id"
        with self.assertRaises(ConfigurationValidationError):
            validate_configuration(candidate)
        candidate = configured_document()
        candidate["timers"] = [timer()]
        candidate["timers"][0]["start"] = "١٤:٣٠"
        with self.assertRaises(ConfigurationValidationError):
            validate_configuration(candidate)

    def test_duplicate_timer_ids_and_more_than_32_are_rejected(self):
        candidate = configured_document()
        candidate["timers"] = [timer("same"), timer("same")]
        with self.assertRaises(ConfigurationValidationError):
            validate_configuration(candidate)
        candidate["timers"] = [timer("t{:02d}".format(index)) for index in range(33)]
        with self.assertRaises(ConfigurationValidationError):
            validate_configuration(candidate)

    def test_sensor_assignments_normalize_and_remain_unique(self):
        candidate = default_configuration()
        assignments = candidate["sensors"]["assignments"]
        assignments["roof_tent"] = " 28ABCDEF "
        normalized = validate_configuration(candidate)
        self.assertEqual(
            normalized["sensors"]["assignments"]["roof_tent"], "28abcdef"
        )
        assignments["cabin"] = "28abcdef"
        with self.assertRaises(ConfigurationValidationError):
            validate_configuration(candidate)

    def test_sensor_thresholds_have_order_and_hard_bound(self):
        candidate = default_configuration()
        candidate["sensors"]["failed_after_ms"] = candidate["sensors"][
            "stale_after_ms"
        ]
        with self.assertRaises(ConfigurationValidationError):
            validate_configuration(candidate)
        candidate = default_configuration()
        candidate["sensors"]["failed_after_ms"] = 3600001
        with self.assertRaises(ConfigurationValidationError):
            validate_configuration(candidate)

    def test_timezone_tuple_and_rule_version_are_atomic(self):
        candidate = default_configuration()
        candidate["time"]["timezone_rule"] = "fixed"
        with self.assertRaises(ConfigurationValidationError):
            validate_configuration(candidate)
        candidate = default_configuration()
        candidate["time"]["timezone_rule_version"] += 1
        with self.assertRaises(ConfigurationValidationError):
            validate_configuration(candidate)


class TestLedgerValidation(unittest.TestCase):
    def test_valid_ledger_is_sorted_and_detached(self):
        a = occurrence("a")
        z = occurrence("z", "overridden")
        candidate = ledger([z, a])
        normalized = validate_scheduler_ledger(candidate)
        self.assertEqual(
            [item["timer_id"] for item in normalized["occurrences"]], ["a", "z"]
        )
        candidate["occurrences"].clear()
        self.assertEqual(len(normalized["occurrences"]), 2)

    def test_ledger_shape_schema_key_minute_and_status_are_strict(self):
        mutations = []
        mutations.append(lambda value: value.__setitem__("schema_version", True))
        mutations.append(lambda value: value.update({"unknown": 1}))
        mutations.append(
            lambda value: value["occurrences"][0].__setitem__(
                "occurrence_key", "weekday|2026-08-09|14:31"
            )
        )
        mutations.append(
            lambda value: value["occurrences"][0].__setitem__("overridden", True)
        )
        mutations.append(
            lambda value: value.__setitem__(
                "consumed_local_high_water",
                value["occurrences"][0]["local_minute_id"] - 1,
            )
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                candidate = ledger([occurrence()])
                mutation(candidate)
                with self.assertRaises(ConfigurationValidationError):
                    validate_scheduler_ledger(candidate)
        unicode_key = ledger([occurrence()])
        unicode_key["occurrences"][0]["occurrence_key"] = (
            "weekday|٢٠٢٦-٠٨-٠٩|١٤:٣٠"
        )
        with self.assertRaises(ConfigurationValidationError):
            validate_scheduler_ledger(unicode_key)

    def test_duplicate_timer_or_occurrence_is_rejected(self):
        first = occurrence("same")
        second = occurrence("same")
        with self.assertRaises(ConfigurationValidationError):
            validate_scheduler_ledger(ledger([first, second]))


class TestConfigManagerLoad(unittest.TestCase):
    def test_constructor_is_io_free_and_first_boot_is_safe(self):
        config_store = MemoryStore()
        ledger_store = MemoryStore()
        manager = ConfigManager(config_store, ledger_store)
        self.assertEqual(config_store.load_calls, 0)
        self.assertEqual(ledger_store.load_calls, 0)
        self.assertFalse(manager.load())
        self.assertFalse(manager.load_scheduler_checkpoint())
        status = manager.status()
        self.assertEqual(status["load_status"], LOAD_FIRST_BOOT)
        self.assertEqual(status["ledger_load_status"], LOAD_FIRST_BOOT)
        self.assertFalse(status["timer_start_allowed"])
        self.assertEqual(manager.snapshot()["configuration"]["timers"], [])

    def test_single_generation_one_is_rollback_ambiguous_and_never_arms(self):
        payload = configured_document(True)
        manager = ConfigManager(MemoryStore([record("a", 1, payload)]), MemoryStore())
        self.assertFalse(manager.load())
        snapshot = manager.snapshot()
        self.assertEqual(snapshot["generation"], 1)
        self.assertEqual(snapshot["configuration"]["timers"], [])
        self.assertFalse(snapshot["configuration"]["system"]["setup_complete"])
        self.assertEqual(manager.status()["load_status"], LOAD_RECOVERY_REQUIRED)
        self.assertFalse(manager.timer_start_allowed)

    def test_two_consecutive_generations_select_newest(self):
        old = configured_document()
        new = configured_document(True)
        store = MemoryStore([record("a", 4, old), record("b", 5, new)])
        manager = ConfigManager(store, MemoryStore())
        self.assertTrue(manager.load())
        self.assertEqual(manager.generation, 5)
        self.assertEqual(len(manager.snapshot()["configuration"]["timers"]), 1)

    def test_single_newer_or_generation_gap_requires_recovery_and_strips_timers(self):
        payload = configured_document(True)
        scenarios = (
            [record("a", 2, payload)],
            [record("a", 1, default_configuration()), record("b", 3, payload)],
        )
        for records in scenarios:
            with self.subTest(records=records):
                manager = ConfigManager(MemoryStore(records), MemoryStore())
                self.assertFalse(manager.load())
                self.assertEqual(manager.status()["load_status"], LOAD_RECOVERY_REQUIRED)
                safe = manager.snapshot()["configuration"]
                self.assertFalse(safe["system"]["setup_complete"])
                self.assertEqual(safe["timers"], [])
                self.assertTrue(manager.status()["config_faulted"])

    def test_equal_generation_requires_exact_stored_fingerprint(self):
        first = configured_document()
        first["timers"] = [timer(weekdays=[6, 0])]
        second = configured_document()
        second["timers"] = [timer(weekdays=[0, 6])]
        manager = ConfigManager(
            MemoryStore([record("a", 1, first), record("b", 1, second)]),
            MemoryStore(),
        )
        self.assertFalse(manager.load())
        self.assertEqual(manager.status()["load_status"], LOAD_INVALID)
        self.assertTrue(manager.status()["config_faulted"])

    def test_identical_generation_mirror_is_valid(self):
        payload = configured_document(True)
        fingerprint = _fingerprint(payload)
        store = MemoryStore(
            [record("a", 2, payload, fingerprint), record("b", 2, payload, fingerprint)]
        )
        manager = ConfigManager(store, MemoryStore())
        self.assertTrue(manager.load())
        self.assertEqual(manager.generation, 2)

    def test_invalid_slot_or_semantic_payload_never_arms(self):
        invalid = {"schema_version": CONFIG_SCHEMA_VERSION}
        manager = ConfigManager(
            MemoryStore([record("a", 1, invalid)], invalid_slots=1), MemoryStore()
        )
        self.assertFalse(manager.load())
        self.assertEqual(manager.status()["load_status"], LOAD_INVALID)
        self.assertFalse(manager.timer_start_allowed)

    def test_duplicate_store_slot_is_a_contract_fault(self):
        payload = default_configuration()
        manager = ConfigManager(
            MemoryStore([
                record("a", 1, payload),
                record("a", 2, payload),
            ]),
            MemoryStore(),
        )
        with self.assertRaisesRegex(RuntimeError, "slot more than once"):
            manager.load()
        self.assertTrue(manager.status()["config_faulted"])
        self.assertFalse(manager.timer_start_allowed)

    def test_migration_loads_but_fences_until_new_generation_commit(self):
        old = {"schema_version": 0, "legacy": True}

        def migrate(payload):
            self.assertTrue(payload["legacy"])
            result = configured_document()
            result.pop("network")
            result["schema_version"] = 1
            return result

        store = MemoryStore([
            record("a", 1, old),
            record("b", 2, old),
        ])
        empty_ledger = default_scheduler_ledger()
        manager = ConfigManager(
            store,
            MemoryStore([
                record("a", 1, empty_ledger),
                record("b", 2, empty_ledger),
            ]),
            migrations={0: migrate},
        )
        self.assertTrue(manager.load())
        self.assertTrue(manager.load_scheduler_checkpoint())
        self.assertTrue(manager.status()["migration_pending"])
        self.assertTrue(manager.commit(manager.snapshot()["configuration"], 2))
        self.assertFalse(manager.status()["migration_pending"])
        self.assertEqual(manager.generation, 3)

    def test_released_v1_migrates_explicitly_without_inventing_a_secret(self):
        old = configured_document(True)
        old.pop("network")
        old["schema_version"] = 1
        store = MemoryStore([
            record("a", 1, old),
            record("b", 2, old),
        ])
        trusted_ledger = default_scheduler_ledger()
        manager = ConfigManager(
            store,
            MemoryStore([
                record("a", 1, trusted_ledger),
                record("b", 2, trusted_ledger),
            ]),
        )
        self.assertTrue(manager.load())
        self.assertTrue(manager.load_scheduler_checkpoint())
        snapshot = manager.snapshot()["configuration"]
        self.assertEqual(snapshot["schema_version"], CONFIG_SCHEMA_VERSION)
        self.assertFalse(snapshot["system"]["setup_complete"])
        self.assertIsNone(snapshot["network"]["access_point"]["password"])
        self.assertTrue(manager.status()["migration_pending"])
        self.assertFalse(manager.timer_start_allowed)
        self.assertFalse(manager.network_start_allowed)
        self.assertTrue(manager.commit(snapshot, 2))
        self.assertFalse(manager.status()["migration_pending"])
        self.assertFalse(manager.network_start_allowed)

    def test_released_v1_is_fully_validated_before_migration(self):
        base = configured_document()
        base.pop("network")
        base["schema_version"] = 1
        for invalid_system in ({}, [], {"setup_complete": 1}, {
            "setup_complete": None
        }):
            with self.subTest(system=invalid_system):
                old = copy.deepcopy(base)
                old["system"] = invalid_system
                manager = ConfigManager(MemoryStore([
                    record("a", 1, old), record("b", 2, old)
                ]), MemoryStore())
                self.assertFalse(manager.load())
                self.assertEqual(manager.status()["load_status"], LOAD_INVALID)
                self.assertFalse(manager.network_start_allowed)

    def test_oversized_released_v1_requires_explicit_recovery(self):
        old = configured_document()
        old.pop("network")
        old["schema_version"] = 1
        old["timers"] = []
        for index in range(32):
            item = timer(("\x02" * 20) + "{:03d}".format(index))
            item["name"] = "\x01" * 80
            old["timers"].append(item)
        old["sensors"]["assignments"]["roof_tent"] = "\x03" * 4
        old_record = config_store_module._encode_record(old, 1, 24 * 1024)
        self.assertGreater(len(old_record), 8 * 1024)
        with self.assertRaisesRegex(
            ConfigurationValidationError, "canonical payload"
        ):
            migrate_configuration_v1_to_v2(old)

    def test_public_snapshot_redacts_every_wifi_password(self):
        config = configured_document()
        config["network"]["known_networks"] = [
            {"id": "home", "ssid": "Home", "password": "home-password"},
            {"id": "open", "ssid": "Open", "password": None},
        ]
        manager = ConfigManager(
            MemoryStore([record("a", 1, config), record("b", 2, config)]),
            MemoryStore(),
        )
        self.assertTrue(manager.load())
        public = manager.public_snapshot()
        rendered = repr(public)
        self.assertNotIn("test-device-secret", rendered)
        self.assertNotIn("home-password", rendered)
        ap = public["configuration"]["network"]["access_point"]
        self.assertEqual(ap["password_configured"], True)
        profiles = public["configuration"]["network"]["known_networks"]
        self.assertEqual(
            [item["password_configured"] for item in profiles],
            [True, False],
        )
        profiles[0]["ssid"] = "changed"
        self.assertEqual(
            manager.public_snapshot()["configuration"]["network"]
            ["known_networks"][0]["ssid"],
            "Home",
        )

    def test_network_gate_is_independent_from_scheduler_ledger(self):
        config = configured_document()
        manager = ConfigManager(
            MemoryStore([record("a", 1, config), record("b", 2, config)]),
            MemoryStore(),
        )
        self.assertTrue(manager.load())
        self.assertTrue(manager.network_start_allowed)
        self.assertFalse(manager.timer_start_allowed)
        runtime = manager.network_configuration_for_runtime()
        self.assertEqual(runtime["generation"], 2)
        self.assertEqual(
            runtime["network"]["access_point"]["password"],
            "test-device-secret",
        )


class TestConfigManagerCommitAndFaultDomains(unittest.TestCase):
    def _provisioned_manager(self):
        config_store = MemoryStore()
        ledger_store = MemoryStore()
        manager = ConfigManager(config_store, ledger_store)
        manager.load()
        manager.load_scheduler_checkpoint()
        manager.checkpoint_scheduler(default_scheduler_ledger(), 0)
        manager.commit(configured_document(True), 0)
        return manager, config_store, ledger_store

    def test_first_commits_provision_both_domains_and_enable_gate(self):
        manager, config_store, ledger_store = self._provisioned_manager()
        self.assertEqual(manager.generation, 2)
        self.assertEqual(manager.ledger_generation, 2)
        self.assertEqual(len(config_store.commit_calls), 2)
        self.assertEqual(len(ledger_store.commit_calls), 2)
        self.assertTrue(manager.timer_start_allowed)

    def test_static_noop_performs_no_write_and_stale_generation_rejects(self):
        manager, config_store, _ = self._provisioned_manager()
        before = len(config_store.commit_calls)
        self.assertFalse(manager.commit(manager.snapshot()["configuration"], 2))
        self.assertEqual(len(config_store.commit_calls), before)
        with self.assertRaises(ConfigurationConflictError):
            manager.commit(default_configuration(), 0)

    def test_scheduler_history_wrapper_is_schema_free_and_durable(self):
        manager, _, ledger_store = self._provisioned_manager()
        item = occurrence()
        history = {
            "consumed_local_high_water": item["local_minute_id"],
            "occurrences": [item],
        }
        self.assertTrue(manager.checkpoint_scheduler_history(history, 2))
        self.assertEqual(len(ledger_store.commit_calls), 3)
        self.assertEqual(manager.scheduler_history_for_restore(), history)
        self.assertFalse(manager.checkpoint_scheduler_history(history, 3))

    def test_ledger_cannot_roll_back_but_covered_diagnostic_can_compact(self):
        manager, _, _ = self._provisioned_manager()
        item = occurrence()
        first = ledger([item])
        manager.checkpoint_scheduler(first, 2)
        rollback = default_scheduler_ledger()
        with self.assertRaises(ConfigurationConflictError):
            manager.checkpoint_scheduler(rollback, 3)
        compacted = ledger([], item["local_minute_id"])
        self.assertTrue(manager.checkpoint_scheduler(compacted, 3))

    def test_override_is_one_way_for_same_occurrence(self):
        manager, _, _ = self._provisioned_manager()
        consumed = ledger([occurrence()])
        manager.checkpoint_scheduler(consumed, 2)
        overridden = ledger([occurrence(status="overridden")])
        self.assertTrue(manager.checkpoint_scheduler(overridden, 3))
        with self.assertRaises(ConfigurationConflictError):
            manager.checkpoint_scheduler(consumed, 4)
        next_minute = civil_to_utc_seconds(2026, 8, 16, 14, 30, 0) // 60
        next_occurrence = {
            "timer_id": "weekday",
            "occurrence_key": "weekday|2026-08-16|14:30",
            "local_minute_id": next_minute,
            "status": "consumed",
            "overridden": False,
        }
        self.assertTrue(
            manager.checkpoint_scheduler(ledger([next_occurrence]), 4)
        )

    def test_config_success_never_clears_ledger_fault(self):
        base = default_configuration()
        config_store = MemoryStore([
            record("a", 1, base),
            record("b", 2, base),
        ])
        ledger_store = MemoryStore([record("a", 1, {"bad": True})])
        manager = ConfigManager(config_store, ledger_store)
        self.assertTrue(manager.load())
        self.assertFalse(manager.load_scheduler_checkpoint())
        candidate = manager.snapshot()["configuration"]
        candidate["heater"]["quick_start"]["runtime_minutes"] = 59
        self.assertTrue(manager.commit(candidate, 2))
        status = manager.status()
        self.assertTrue(status["ledger_faulted"])
        self.assertFalse(status["config_faulted"])
        self.assertTrue(status["faulted"])
        self.assertFalse(status["timer_start_allowed"])

    def test_ledger_success_never_clears_config_fault(self):
        config_store = MemoryStore([record("a", 2, configured_document(True))])
        empty = default_scheduler_ledger()
        ledger_store = MemoryStore([
            record("a", 1, empty),
            record("b", 2, empty),
        ])
        manager = ConfigManager(config_store, ledger_store)
        self.assertFalse(manager.load())
        manager.load_scheduler_checkpoint()
        status = manager.status()
        self.assertTrue(status["config_faulted"])
        self.assertFalse(status["ledger_faulted"])
        self.assertTrue(status["faulted"])

    def test_store_failure_latches_only_its_domain(self):
        manager, config_store, ledger_store = self._provisioned_manager()
        config_store.failure = MemoryError("config oom")
        candidate = manager.snapshot()["configuration"]
        candidate["heater"]["quick_start"]["runtime_minutes"] = 58
        with self.assertRaises(MemoryError):
            manager.commit(candidate, manager.generation)
        status = manager.status()
        self.assertTrue(status["config_faulted"])
        self.assertFalse(status["ledger_faulted"])
        config_store.failure = None
        ledger_store.failure = MemoryError("ledger oom")
        item = occurrence()
        with self.assertRaises(MemoryError):
            manager.checkpoint_scheduler(ledger([item]), manager.ledger_generation)
        status = manager.status()
        self.assertTrue(status["config_faulted"])
        self.assertTrue(status["ledger_faulted"])

    def test_oom_while_staging_recovery_closes_previously_open_gate(self):
        manager, config_store, ledger_store = self._provisioned_manager()
        self.assertTrue(manager.timer_start_allowed)
        config_store.records = [
            record("a", 2, configured_document(True))
        ]
        with mock.patch(
            "services.config_manager._safe_config_without_timers",
            side_effect=MemoryError("safe config oom"),
        ):
            with self.assertRaises(MemoryError):
                manager.load()
        self.assertTrue(manager.status()["config_faulted"])
        self.assertFalse(manager.timer_start_allowed)

        config_store.records = [
            record("a", 1, configured_document(True)),
            record("b", 2, configured_document(True)),
        ]
        manager.load()
        ledger_store.records = [
            record("a", 2, ledger([occurrence()]))
        ]
        with mock.patch(
            "services.config_manager.default_scheduler_ledger",
            side_effect=MemoryError("ledger default oom"),
        ):
            with self.assertRaises(MemoryError):
                manager.load_scheduler_checkpoint()
        self.assertTrue(manager.status()["ledger_faulted"])
        self.assertFalse(manager.timer_start_allowed)

    def test_existing_config_with_missing_ledger_needs_explicit_recovery(self):
        for setup_complete in (False, True):
            with self.subTest(setup_complete=setup_complete):
                configured = configured_document(True)
                configured["system"]["setup_complete"] = setup_complete
                ledger_store = MemoryStore()
                manager = ConfigManager(
                    MemoryStore([
                        record("a", 1, configured),
                        record("b", 2, configured),
                    ]),
                    ledger_store,
                )
                self.assertTrue(manager.load())
                self.assertFalse(manager.load_scheduler_checkpoint())
                before = len(ledger_store.commit_calls)
                with self.assertRaisesRegex(
                    RuntimeError, "missing scheduler ledger"
                ):
                    manager.checkpoint_scheduler(
                        default_scheduler_ledger(), 0
                    )
                self.assertEqual(len(ledger_store.commit_calls), before)
                self.assertFalse(manager.timer_start_allowed)

    def test_initial_configuration_requires_ledger_first(self):
        manager = ConfigManager(MemoryStore(), MemoryStore())
        manager.load()
        manager.load_scheduler_checkpoint()
        with self.assertRaisesRegex(RuntimeError, "ledger must be provisioned"):
            manager.commit(default_configuration(), 0)
        self.assertEqual(manager.generation, 0)
        self.assertFalse(manager.timer_start_allowed)

    def test_sensor_normalization_is_idempotently_byte_bounded(self):
        candidate = default_configuration()
        candidate["sensors"]["assignments"]["roof_tent"] = "\u0130" * 22
        with self.assertRaises(ConfigurationValidationError):
            validate_configuration(candidate)

    def test_real_ab_newest_slot_loss_never_reactivates_old_state(self):
        filesystem = MemoryFileSystem()
        config_store = AtomicJSONConfigStore(
            "/config", max_record_bytes=8192, filesystem=filesystem
        )
        ledger_store = AtomicJSONConfigStore(
            "/ledger", max_record_bytes=8192, filesystem=filesystem
        )
        manager = ConfigManager(config_store, ledger_store)
        manager.load()
        manager.load_scheduler_checkpoint()
        manager.checkpoint_scheduler(default_scheduler_ledger(), 0)
        manager.commit(configured_document(True), 0)
        self.assertTrue(manager.timer_start_allowed)

        consumed = ledger([occurrence()])
        manager.checkpoint_scheduler(consumed, manager.ledger_generation)
        without_timer = manager.snapshot()["configuration"]
        without_timer["timers"] = []
        manager.commit(without_timer, manager.generation)
        self.assertEqual(manager.generation, 3)
        self.assertEqual(manager.ledger_generation, 3)

        # Generation three replaced slot A.  Losing both newest A slots leaves
        # apparently valid generation-two data, but a lone slot is deliberately
        # rollback-ambiguous and can never authorize timers.
        del filesystem.files["/config.a"]
        del filesystem.files["/ledger.a"]
        rebooted = ConfigManager(
            AtomicJSONConfigStore(
                "/config", max_record_bytes=8192, filesystem=filesystem
            ),
            AtomicJSONConfigStore(
                "/ledger", max_record_bytes=8192, filesystem=filesystem
            ),
        )
        self.assertFalse(rebooted.load())
        self.assertFalse(rebooted.load_scheduler_checkpoint())
        self.assertFalse(rebooted.timer_start_allowed)
        self.assertEqual(
            rebooted.snapshot()["configuration"]["timers"], []
        )
        self.assertEqual(
            rebooted.scheduler_checkpoint()["ledger"],
            default_scheduler_ledger(),
        )

        # Recovery is possible only through the explicit reseal APIs.  The
        # configuration recovery itself remains setup-incomplete; a separate
        # confirmed whole-document commit is required to reopen the gate.
        self.assertTrue(
            rebooted.recover_scheduler_ledger(_EXPECTED_RECOVERY_MINUTE)
        )
        self.assertTrue(rebooted.recover_configuration(without_timer))
        self.assertFalse(rebooted.timer_start_allowed)
        self.assertFalse(rebooted.network_start_allowed)
        self.assertIsNone(
            rebooted.snapshot()["configuration"]["network"]
            ["access_point"]["password"]
        )
        self.assertTrue(
            rebooted.commit(without_timer, rebooted.generation)
        )
        self.assertTrue(rebooted.timer_start_allowed)

        verified = ConfigManager(
            AtomicJSONConfigStore(
                "/config", max_record_bytes=8192, filesystem=filesystem
            ),
            AtomicJSONConfigStore(
                "/ledger", max_record_bytes=8192, filesystem=filesystem
            ),
        )
        self.assertTrue(verified.load())
        self.assertTrue(verified.load_scheduler_checkpoint())
        self.assertTrue(verified.timer_start_allowed)
        self.assertEqual(
            verified.scheduler_checkpoint()["ledger"][
                "consumed_local_high_water"
            ],
            _EXPECTED_RECOVERY_MINUTE,
        )

    def test_live_durability_unknown_cannot_be_cleared_by_reload(self):
        filesystem = MemoryFileSystem()
        config_store = AtomicJSONConfigStore(
            "/config", max_record_bytes=8192, filesystem=filesystem
        )
        ledger_store = AtomicJSONConfigStore(
            "/ledger", max_record_bytes=8192, filesystem=filesystem
        )
        manager = ConfigManager(config_store, ledger_store)
        manager.load()
        manager.load_scheduler_checkpoint()
        manager.checkpoint_scheduler(default_scheduler_ledger(), 0)
        manager.commit(configured_document(True), 0)
        self.assertTrue(manager.timer_start_allowed)

        # The new slot is visible, but its final durability sync fails.
        filesystem.plan("sync", None, OSError(5, "final sync failed"))
        with self.assertRaises(OSError):
            manager.checkpoint_scheduler(
                ledger([occurrence()]), manager.ledger_generation
            )
        self.assertTrue(ledger_store.status()["durability_unknown"])
        self.assertFalse(manager.timer_start_allowed)

        self.assertFalse(manager.load_scheduler_checkpoint())
        self.assertEqual(
            manager.status()["ledger_load_status"],
            LOAD_RECOVERY_REQUIRED,
        )
        self.assertTrue(manager.status()["ledger_faulted"])
        self.assertFalse(manager.timer_start_allowed)
        self.assertTrue(
            manager.recover_scheduler_ledger(_EXPECTED_RECOVERY_MINUTE)
        )
        self.assertFalse(ledger_store.status()["durability_unknown"])
        self.assertTrue(manager.timer_start_allowed)

    def test_explicit_configuration_recovery_is_safe_and_reseals_slots(self):
        unsafe = configured_document(True)
        config_store = MemoryStore([record("a", 1, unsafe)])
        empty = default_scheduler_ledger()
        ledger_store = MemoryStore([
            record("a", 1, empty),
            record("b", 2, empty),
        ])
        manager = ConfigManager(config_store, ledger_store)
        self.assertFalse(manager.load())
        self.assertTrue(manager.load_scheduler_checkpoint())
        self.assertTrue(manager.recover_configuration(unsafe))
        recovered = manager.snapshot()["configuration"]
        self.assertFalse(recovered["system"]["setup_complete"])
        self.assertEqual(recovered["timers"], [])
        self.assertIsNone(
            recovered["network"]["access_point"]["password"]
        )
        self.assertFalse(manager.network_start_allowed)
        self.assertEqual(manager.generation, 3)
        self.assertFalse(manager.timer_start_allowed)
        self.assertEqual(len(config_store.reseal_calls), 1)

        # A later explicit whole-document commit may re-enable the confirmed
        # timer only after the ledger is already trusted.
        self.assertTrue(manager.commit(unsafe, manager.generation))
        self.assertTrue(manager.timer_start_allowed)

    def test_explicit_missing_ledger_recovery_sets_current_high_water(self):
        configured = configured_document(True)
        config_store = MemoryStore([
            record("a", 1, configured),
            record("b", 2, configured),
        ])
        ledger_store = MemoryStore()
        manager = ConfigManager(config_store, ledger_store)
        self.assertTrue(manager.load())
        self.assertFalse(manager.load_scheduler_checkpoint())
        self.assertTrue(
            manager.recover_scheduler_ledger(_EXPECTED_RECOVERY_MINUTE)
        )
        checkpoint = manager.scheduler_checkpoint()
        self.assertEqual(checkpoint["generation"], 2)
        self.assertEqual(
            checkpoint["ledger"]["consumed_local_high_water"],
            _EXPECTED_RECOVERY_MINUTE,
        )
        self.assertEqual(checkpoint["ledger"]["occurrences"], [])
        self.assertTrue(manager.timer_start_allowed)

    def test_recovery_never_lowers_a_valid_survivor_high_water(self):
        configured = configured_document(True)
        config_store = MemoryStore([
            record("a", 1, configured),
            record("b", 2, configured),
        ])
        survivor = default_scheduler_ledger()
        survivor["consumed_local_high_water"] = _EXPECTED_RECOVERY_MINUTE
        ledger_store = MemoryStore([record("a", 7, survivor)])
        manager = ConfigManager(config_store, ledger_store)

        self.assertTrue(manager.load())
        self.assertFalse(manager.load_scheduler_checkpoint())
        self.assertTrue(
            manager.recover_scheduler_ledger(
                _EXPECTED_RECOVERY_MINUTE - 100
            )
        )
        checkpoint = manager.scheduler_checkpoint()
        self.assertEqual(checkpoint["generation"], 9)
        self.assertEqual(
            checkpoint["ledger"]["consumed_local_high_water"],
            _EXPECTED_RECOVERY_MINUTE,
        )
        self.assertEqual(checkpoint["ledger"]["occurrences"], [])
        self.assertTrue(manager.timer_start_allowed)

        rebooted = ConfigManager(config_store, ledger_store)
        self.assertTrue(rebooted.load())
        self.assertTrue(rebooted.load_scheduler_checkpoint())
        self.assertEqual(
            rebooted.scheduler_checkpoint()["ledger"][
                "consumed_local_high_water"
            ],
            _EXPECTED_RECOVERY_MINUTE,
        )
        self.assertTrue(rebooted.timer_start_allowed)

    def test_recovery_view_change_cannot_hide_a_survivor_high_water(self):
        configured = configured_document(True)
        config_store = MemoryStore([
            record("a", 1, configured),
            record("b", 2, configured),
        ])
        filesystem = MemoryFileSystem()
        survivor = default_scheduler_ledger()
        survivor["consumed_local_high_water"] = _EXPECTED_RECOVERY_MINUTE
        filesystem.files["/ledger.a"] = config_store_module._encode_record(
            survivor, 7, 8192
        )
        ledger_store = AtomicJSONConfigStore(
            "/ledger", max_record_bytes=8192, filesystem=filesystem
        )
        manager = ConfigManager(config_store, ledger_store)

        self.assertTrue(manager.load())
        self.assertFalse(manager.load_scheduler_checkpoint())
        filesystem.plan("read", lambda value: value[:-1])
        with self.assertRaisesRegex(RuntimeError, "recovery view changed"):
            manager.recover_scheduler_ledger(
                _EXPECTED_RECOVERY_MINUTE - 100
            )
        self.assertFalse(manager.timer_start_allowed)
        self.assertEqual(filesystem.count("write"), 0)

        self.assertTrue(
            manager.recover_scheduler_ledger(
                _EXPECTED_RECOVERY_MINUTE - 100
            )
        )
        self.assertEqual(
            manager.scheduler_checkpoint()["ledger"][
                "consumed_local_high_water"
            ],
            _EXPECTED_RECOVERY_MINUTE,
        )
        self.assertTrue(manager.timer_start_allowed)

    def test_recovery_is_explicit_bounded_and_not_a_normal_reset(self):
        manager, config_store, ledger_store = self._provisioned_manager()
        with self.assertRaisesRegex(RuntimeError, "not required"):
            manager.recover_configuration(manager.snapshot()["configuration"])
        with self.assertRaisesRegex(RuntimeError, "not required"):
            manager.recover_scheduler_ledger(_EXPECTED_RECOVERY_MINUTE)
        self.assertEqual(config_store.reseal_calls, [])
        self.assertEqual(ledger_store.reseal_calls, [])

        configured = configured_document(True)
        damaged = ConfigManager(
            MemoryStore([
                record("a", 1, configured),
                record("b", 2, configured),
            ]),
            MemoryStore(),
        )
        damaged.load()
        damaged.load_scheduler_checkpoint()
        for value in (None, False, True, -1, 52596000, "13993350"):
            with self.subTest(value=value):
                with self.assertRaises((ValueError, RuntimeError)):
                    damaged.recover_scheduler_ledger(value)

    def test_reentrant_load_is_latched_even_when_callback_swallows_error(self):
        manager, config_store, _ = self._provisioned_manager()
        observed = []

        def reenter():
            config_store.on_load = None
            observed.append(manager.timer_start_allowed)
            try:
                manager.load()
            except RuntimeError:
                pass

        config_store.on_load = reenter
        with self.assertRaisesRegex(RuntimeError, "re-entered"):
            manager.load()
        self.assertEqual(observed, [False])
        self.assertTrue(manager.status()["operational_faulted"])
        self.assertFalse(manager.timer_start_allowed)

    def test_reentrant_commit_never_reopens_gate_after_outer_publish(self):
        manager, config_store, _ = self._provisioned_manager()
        candidate = manager.snapshot()["configuration"]
        candidate["heater"]["quick_start"]["runtime_minutes"] = 59
        observed = []

        def reenter():
            config_store.on_commit = None
            observed.append(manager.timer_start_allowed)
            try:
                manager.commit(candidate, manager.generation)
            except RuntimeError:
                pass

        config_store.on_commit = reenter
        with self.assertRaisesRegex(RuntimeError, "re-entered"):
            manager.commit(candidate, manager.generation)
        self.assertEqual(observed, [False])
        self.assertTrue(manager.status()["operational_faulted"])
        self.assertFalse(manager.timer_start_allowed)

    def test_untrusted_ledger_cannot_be_replaced_by_empty_normal_checkpoint(self):
        configured = configured_document(True)
        config_store = MemoryStore([
            record("a", 1, configured),
            record("b", 2, configured),
        ])
        ledger_store = MemoryStore([
            record("a", 2, ledger([occurrence()]))
        ])
        manager = ConfigManager(config_store, ledger_store)
        self.assertTrue(manager.load())
        self.assertFalse(manager.load_scheduler_checkpoint())
        self.assertEqual(manager.ledger_generation, 0)
        before = len(ledger_store.commit_calls)
        with self.assertRaisesRegex(RuntimeError, "explicit recovery"):
            manager.checkpoint_scheduler(default_scheduler_ledger(), 0)
        self.assertEqual(len(ledger_store.commit_calls), before)
        self.assertFalse(manager.timer_start_allowed)


class TestConfigManagerPublicStatus(unittest.TestCase):
    def test_public_status_omits_paths_and_error_strings(self):
        configuration = default_configuration()
        ledger = default_scheduler_ledger()
        manager = ConfigManager(
            MemoryStore([
                record("a", 1, configuration),
                record("b", 2, configuration),
            ]),
            MemoryStore([
                record("a", 1, ledger),
                record("b", 2, ledger),
            ]),
        )
        self.assertTrue(manager.load())
        self.assertTrue(manager.load_scheduler_checkpoint())
        public = manager.public_status()
        text = repr(public)
        self.assertNotIn("base_path", text)
        self.assertNotIn("last_error", text)
        self.assertEqual(public["generation"], manager.generation)
        self.assertEqual(
            public["ledger_generation"], manager.ledger_generation
        )
        self.assertTrue(public["config_store"]["available"])


if __name__ == "__main__":
    unittest.main()
