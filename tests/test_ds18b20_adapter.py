import builtins
import inspect
import math
import runpy
import time
import unittest
from unittest import mock

import adapters.ds18b20_adapter as adapter_module
from adapters.ds18b20_adapter import (
    DS18B20Adapter,
    DS18B20BusError,
    ERROR_BUS,
    ERROR_CONVERSION,
    ERROR_POWER_ON_85,
    ERROR_READ,
    ERROR_SCAN,
    ERROR_VALUE,
    STATE_CLOSED,
    STATE_FAULTED,
    canonical_rom_id,
    dallas_crc8,
)
from app.temperature_manager import (
    SENSOR_ROLE_CABIN,
    SENSOR_ROLE_OUTSIDE,
    SENSOR_ROLE_ROOF_TENT,
    TemperatureManager,
)


ROM1 = bytes.fromhex("280102030405069e")
ROM2 = bytes.fromhex("2811223344556656")
ROM3 = bytes.fromhex("28aabbccddeeff0c")
ID1 = ROM1.hex()
ID2 = ROM2.hex()
ID3 = ROM3.hex()


class FakeBus:
    def __init__(self, scan_result=None, read_values=None):
        self.scan_result = list(scan_result or [])
        self.scan_plan = []
        self.convert_plan = []
        self.read_values = dict(read_values or {})
        self.calls = []
        self.deinit_plan = []

    @staticmethod
    def _resolve(value):
        if isinstance(value, BaseException):
            raise value
        return value

    def scan(self):
        self.calls.append(("scan",))
        if self.scan_plan:
            return self._resolve(self.scan_plan.pop(0))
        return self.scan_result

    def start_conversion(self):
        self.calls.append(("convert",))
        if self.convert_plan:
            return self._resolve(self.convert_plan.pop(0))
        return None

    def read_celsius(self, raw_rom):
        raw_rom = bytes(raw_rom)
        self.calls.append(("read", raw_rom))
        plan = self.read_values.get(raw_rom)
        if isinstance(plan, list):
            if not plan:
                raise AssertionError("read plan exhausted")
            return self._resolve(plan.pop(0))
        return self._resolve(plan)

    def deinit(self):
        self.calls.append(("deinit",))
        if self.deinit_plan:
            return self._resolve(self.deinit_plan.pop(0))
        return None


def manager_for(*rom_ids, **kwargs):
    roles = (
        SENSOR_ROLE_ROOF_TENT,
        SENSOR_ROLE_CABIN,
        SENSOR_ROLE_OUTSIDE,
    )
    return TemperatureManager(
        {role: rom_id for role, rom_id in zip(roles, rom_ids)},
        **kwargs
    )


def run_one_sensor_cycle(adapter, scan_at=0, convert_at=1, read_at=751):
    assert adapter.step(scan_at) == 1
    assert adapter.step(convert_at) == 1
    assert adapter.step(read_at) == 1


class TestDS18B20ROMValidation(unittest.TestCase):
    def test_known_rom_crc_and_canonical_id(self):
        for raw_rom in (ROM1, ROM2, ROM3):
            with self.subTest(raw_rom=raw_rom.hex()):
                self.assertEqual(dallas_crc8(raw_rom), 0)
                immutable, rom_id = canonical_rom_id(bytearray(raw_rom))
                self.assertIs(type(immutable), bytes)
                self.assertEqual(immutable, raw_rom)
                self.assertEqual(rom_id, raw_rom.hex())

    def test_invalid_rom_type_length_family_and_crc_are_rejected(self):
        invalid = (
            None,
            "280102030405069e",
            ROM1[:-1],
            ROM1 + b"x",
            bytes((0x10,)) + ROM1[1:],
            ROM1[:-1] + bytes((ROM1[-1] ^ 1,)),
        )
        for raw_rom in invalid:
            with self.subTest(raw_rom=raw_rom):
                with self.assertRaises(ValueError):
                    canonical_rom_id(raw_rom)


class TestDS18B20AdapterContract(unittest.TestCase):
    def test_constructor_validates_ports_and_timings_without_io(self):
        bus = FakeBus()
        manager = TemperatureManager(max_discovered_sensors=3)
        adapter = DS18B20Adapter(bus, manager, max_sensors=3)
        self.assertEqual(bus.calls, [])
        self.assertEqual(adapter.status()["scans"], 0)

        for broken in (None, object()):
            with self.subTest(broken=broken):
                with self.assertRaises(ValueError):
                    DS18B20Adapter(broken, manager)
                with self.assertRaises(ValueError):
                    DS18B20Adapter(bus, broken)
        for kwargs in (
            {"conversion_wait_ms": 0},
            {"conversion_wait_ms": True},
            {"conversion_wait_ms": 749},
            {"poll_interval_ms": 749},
            {"discovery_interval_ms": 999},
            {"max_sensors": 0},
            {"max_sensors": 4},
            {"ticks_diff": lambda a, b: a - b},
            {"ticks_diff": "bad", "ticks_add": lambda a, b: a + b},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    DS18B20Adapter(bus, manager, **kwargs)
        self.assertEqual(bus.calls, [])

    def test_validated_timing_and_capacity_are_read_only(self):
        adapter = DS18B20Adapter(FakeBus(), TemperatureManager())
        expected = (
            ("conversion_wait_ms", 750),
            ("poll_interval_ms", 1000),
            ("discovery_interval_ms", 30000),
            ("max_sensors", 16),
        )
        for name, value in expected:
            with self.subTest(name=name):
                self.assertEqual(getattr(adapter, name), value)
                with self.assertRaises(AttributeError):
                    setattr(adapter, name, 1)

    def test_module_import_has_no_hardware_or_heater_side_effect(self):
        source = inspect.getsource(adapter_module)
        for forbidden in (
            "import machine",
            "import onewire",
            "import ds18x20",
            "import board_config",
            "import protocol",
            "import app.heater_controller",
        ):
            self.assertNotIn(forbidden, source)

        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in (
                "machine",
                "onewire",
                "ds18x20",
                "board_config",
                "protocol",
            ):
                raise AssertionError("hardware/control import attempted")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            namespace = runpy.run_path(adapter_module.__file__)
        self.assertIn("DS18B20Adapter", namespace)

    def test_step_never_uses_blocking_sleep(self):
        bus = FakeBus([ROM1], {ROM1: 0.0})
        adapter = DS18B20Adapter(bus, manager_for(ID1))
        with mock.patch.object(
            time, "sleep", side_effect=AssertionError("blocking sleep")
        ):
            run_one_sensor_cycle(adapter)
        self.assertEqual(adapter.status()["valid_readings"], 1)

    def test_scan_convert_and_exact_750ms_deadline_are_separate(self):
        bus = FakeBus([ROM1], {ROM1: 20.5})
        manager = manager_for(ID1)
        adapter = DS18B20Adapter(bus, manager)

        self.assertEqual(adapter.step(0), 1)
        self.assertEqual(bus.calls, [("scan",)])
        self.assertEqual(adapter.step(0), 0)
        self.assertEqual(adapter.step(1), 1)
        self.assertEqual(bus.calls[-1], ("convert",))
        self.assertEqual(adapter.step(750), 0)
        self.assertFalse(any(call[0] == "read" for call in bus.calls))
        self.assertEqual(adapter.step(751), 1)
        self.assertEqual(bus.calls[-1], ("read", ROM1))
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 751)["value_c"],
            20.5,
        )

    def test_three_sensors_are_read_once_over_three_steps(self):
        bus = FakeBus(
            [ROM3, ROM1, ROM2],
            {ROM1: 10, ROM2: 20, ROM3: 30},
        )
        manager = manager_for(ID1, ID2, ID3)
        adapter = DS18B20Adapter(bus, manager, max_sensors=3)
        self.assertEqual(adapter.step(0), 1)
        self.assertEqual(adapter.step(1), 1)
        for now_ms in (751, 752, 753):
            self.assertEqual(adapter.step(now_ms), 1)
        reads = [call[1] for call in bus.calls if call[0] == "read"]
        self.assertEqual(reads, [ROM1, ROM2, ROM3])
        self.assertEqual(adapter.status()["completed_cycles"], 1)
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_OUTSIDE, 753)["value_c"],
            30.0,
        )

    def test_raw_scan_buffers_are_copied_before_later_reads(self):
        mutable = bytearray(ROM1)
        bus = FakeBus([mutable], {ROM1: 19})
        adapter = DS18B20Adapter(bus, manager_for(ID1))
        self.assertEqual(adapter.step(0), 1)
        mutable[:] = b"\x00" * 8
        self.assertEqual(adapter.step(1), 1)
        self.assertEqual(adapter.step(751), 1)
        self.assertEqual(bus.calls[-1], ("read", ROM1))

    def test_tick_wrap_preserves_conversion_deadline(self):
        period = 2048
        half = period // 2

        def ticks_diff(newer, older):
            return ((newer - older + half) % period) - half

        def ticks_add(value, delta):
            return (value + delta) % period

        bus = FakeBus([ROM1], {ROM1: 18})
        adapter = DS18B20Adapter(
            bus,
            manager_for(ID1, ticks_diff=ticks_diff),
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
        )
        self.assertEqual(adapter.step(1900), 1)
        self.assertEqual(adapter.step(1901), 1)
        self.assertEqual(adapter.step((1901 + 749) % period), 0)
        self.assertEqual(adapter.step((1901 + 750) % period), 1)
        self.assertEqual(adapter.status()["valid_readings"], 1)

    def test_late_poll_performs_one_action_without_catch_up(self):
        bus = FakeBus([ROM1], {ROM1: [20, 21]})
        adapter = DS18B20Adapter(bus, manager_for(ID1))
        run_one_sensor_cycle(adapter)
        before = len(bus.calls)
        self.assertEqual(adapter.step(100000), 1)
        self.assertEqual(len(bus.calls), before + 1)
        self.assertEqual(bus.calls[-1], ("scan",))

    def test_delayed_read_keeps_conversion_timestamp_and_is_already_failed(self):
        bus = FakeBus([ROM1], {ROM1: 20})
        manager = manager_for(ID1)
        adapter = DS18B20Adapter(bus, manager)
        self.assertEqual(adapter.step(0), 1)
        self.assertEqual(adapter.step(1), 1)
        self.assertEqual(adapter.step(400000), 1)
        sensor = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 400000)
        self.assertEqual(sensor["last_valid_ms"], 751)
        self.assertEqual(sensor["age_ms"], 399249)
        self.assertEqual(sensor["health"], "failed")
        self.assertEqual(
            adapter.status()["devices"][0]["last_sample_ms"], 751
        )

    def test_backward_bool_and_repeated_timestamps_do_not_touch_bus(self):
        bus = FakeBus([ROM1])
        adapter = DS18B20Adapter(bus, manager_for(ID1))
        self.assertEqual(adapter.step(100), 1)
        before = list(bus.calls)
        self.assertEqual(adapter.step(100), 0)
        self.assertEqual(bus.calls, before)
        with self.assertRaises(ValueError):
            adapter.step(99)
        with self.assertRaises(ValueError):
            adapter.step(True)
        self.assertEqual(bus.calls, before)


class TestDS18B20AdapterFailures(unittest.TestCase):
    def test_invalid_scan_is_atomic_and_keeps_previous_discovery(self):
        bus = FakeBus([ROM1], {ROM1: 20})
        manager = manager_for(ID1, max_discovered_sensors=3)
        adapter = DS18B20Adapter(bus, manager, max_sensors=3)
        self.assertEqual(adapter.step(0), 1)
        before = adapter.status()["discovered_rom_ids"]

        invalid_results = (
            [ROM1, ROM1],
            [ROM1[:-1]],
            [bytes((0x10,)) + ROM1[1:]],
            (rom for rom in (ROM1,)),
            [ROM1, ROM2, ROM3, ROM1],
        )
        for index, invalid in enumerate(invalid_results, 1):
            bus.scan_result = invalid
            adapter.request_discovery()
            self.assertEqual(adapter.step(index), 1)
            self.assertEqual(
                adapter.status()["discovered_rom_ids"], before
            )
            self.assertEqual(
                manager.snapshot(index)["discovered_rom_ids"], before
            )
        self.assertEqual(adapter.status()["scan_errors"], len(invalid_results))

    def test_empty_scan_and_scan_error_have_distinct_presence_results(self):
        bus = FakeBus([ROM1])
        manager = manager_for(ID1)
        adapter = DS18B20Adapter(bus, manager)
        self.assertEqual(adapter.step(0), 1)
        self.assertTrue(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 0)["present"]
        )

        bus.scan_plan.append(DS18B20BusError("offline"))
        adapter.request_discovery()
        self.assertEqual(adapter.step(1), 1)
        self.assertEqual(adapter.status()["discovered_rom_ids"], (ID1,))
        self.assertTrue(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 1)["present"]
        )

        bus.scan_result = []
        adapter.request_discovery()
        self.assertEqual(adapter.step(2), 1)
        self.assertEqual(adapter.status()["discovered_rom_ids"], ())
        self.assertFalse(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 2)["present"]
        )

    def test_conversion_error_marks_assignments_and_is_rate_limited(self):
        bus = FakeBus([ROM1])
        bus.convert_plan.append(DS18B20BusError("conversion"))
        manager = manager_for(ID1)
        adapter = DS18B20Adapter(bus, manager)
        self.assertEqual(adapter.step(0), 1)
        self.assertEqual(adapter.step(1), 1)
        self.assertEqual(adapter.status()["last_error"], ERROR_CONVERSION)
        self.assertEqual(adapter.step(2), 0)
        self.assertFalse(any(call[0] == "read" for call in bus.calls))
        sensor = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 2)
        self.assertEqual(sensor["last_error"], ERROR_CONVERSION)
        self.assertEqual(adapter.step(1000), 0)
        self.assertEqual(adapter.step(1001), 1)
        self.assertEqual(
            len([call for call in bus.calls if call[0] == "convert"]), 2
        )

    def test_false_conversion_result_never_authorizes_a_read(self):
        bus = FakeBus([ROM1], {ROM1: 20})
        bus.convert_plan.append(False)
        manager = manager_for(ID1)
        adapter = DS18B20Adapter(bus, manager)
        self.assertEqual(adapter.step(0), 1)
        self.assertEqual(adapter.step(1), 1)
        self.assertEqual(adapter.step(751), 0)
        self.assertFalse(any(call[0] == "read" for call in bus.calls))
        self.assertIsNone(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 751)["value_c"]
        )

    def test_conversion_error_is_visible_for_unassigned_discovered_sensor(self):
        bus = FakeBus([ROM1])
        bus.convert_plan.append(False)
        adapter = DS18B20Adapter(bus, TemperatureManager())
        self.assertEqual(adapter.step(0), 1)
        self.assertEqual(adapter.step(1), 1)
        device = adapter.status()["devices"][0]
        self.assertEqual(device["last_error"], ERROR_CONVERSION)
        self.assertEqual(device["invalid_readings"], 1)
        self.assertFalse(device["trusted"])

    def test_one_read_error_does_not_block_later_sensor(self):
        bus = FakeBus(
            [ROM1, ROM2],
            {ROM1: DS18B20BusError("CRC"), ROM2: 22},
        )
        manager = manager_for(ID1, ID2)
        adapter = DS18B20Adapter(bus, manager)
        self.assertEqual(adapter.step(0), 1)
        self.assertEqual(adapter.step(1), 1)
        self.assertEqual(adapter.step(751), 1)
        self.assertEqual(adapter.step(752), 1)
        self.assertEqual(adapter.status()["read_errors"], 1)
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 752)["last_error"],
            ERROR_READ,
        )
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_CABIN, 752)["value_c"],
            22.0,
        )

    def test_invalid_values_never_create_a_fake_zero(self):
        invalid = (None, True, "20", math.nan, math.inf, -math.inf, -55.01, 125.01)
        for value in invalid:
            with self.subTest(value=value):
                bus = FakeBus([ROM1], {ROM1: value})
                manager = manager_for(ID1)
                adapter = DS18B20Adapter(bus, manager)
                run_one_sensor_cycle(adapter)
                sensor = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 751)
                self.assertIsNone(sensor["value_c"])
                self.assertEqual(sensor["last_error"], ERROR_VALUE)

        for value in (-55, 0.0, 125):
            with self.subTest(valid=value):
                bus = FakeBus([ROM1], {ROM1: value})
                manager = manager_for(ID1)
                adapter = DS18B20Adapter(bus, manager)
                run_one_sensor_cycle(adapter)
                self.assertEqual(
                    manager.sensor_snapshot(
                        SENSOR_ROLE_ROOF_TENT, 751
                    )["value_c"],
                    float(value),
                )

    def test_first_85_is_rejected_until_a_non_sentinel_sample(self):
        bus = FakeBus([ROM1], {ROM1: [85.0, 20.0, 85.0]})
        manager = manager_for(ID1)
        adapter = DS18B20Adapter(bus, manager)
        run_one_sensor_cycle(adapter)
        self.assertIsNone(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 751)["value_c"]
        )
        self.assertEqual(adapter.status()["last_error"], ERROR_POWER_ON_85)

        self.assertEqual(adapter.step(1001), 1)
        self.assertEqual(adapter.step(1751), 1)
        self.assertEqual(adapter.step(2001), 1)
        self.assertEqual(adapter.step(2751), 1)
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 2751)["value_c"],
            85.0,
        )

    def test_disappearance_resets_85_degree_trust(self):
        bus = FakeBus([ROM1], {ROM1: [20.0, 85.0]})
        manager = manager_for(ID1)
        adapter = DS18B20Adapter(bus, manager)
        run_one_sensor_cycle(adapter)

        bus.scan_result = []
        adapter.request_discovery()
        self.assertEqual(adapter.step(752), 1)
        bus.scan_result = [ROM1]
        adapter.request_discovery()
        self.assertEqual(adapter.step(753), 1)
        self.assertEqual(adapter.step(754), 1)
        self.assertEqual(adapter.step(1504), 1)
        self.assertEqual(adapter.status()["last_error"], ERROR_POWER_ON_85)
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 1504)["value_c"],
            20.0,
        )

    def test_read_error_resets_85_degree_trust_without_a_new_scan(self):
        bus = FakeBus(
            [ROM1],
            {
                ROM1: [
                    20.0,
                    DS18B20BusError("sensor unplugged"),
                    85.0,
                ]
            },
        )
        manager = manager_for(ID1)
        adapter = DS18B20Adapter(bus, manager)
        run_one_sensor_cycle(adapter)
        self.assertEqual(adapter.step(1001), 1)
        self.assertEqual(adapter.step(1751), 1)
        self.assertFalse(adapter.status()["devices"][0]["trusted"])
        self.assertEqual(adapter.step(2001), 1)
        self.assertEqual(adapter.step(2751), 1)
        self.assertEqual(adapter.status()["last_error"], ERROR_POWER_ON_85)
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 2751)["value_c"],
            20.0,
        )

    def test_fully_successful_followup_cycle_clears_global_read_error(self):
        bus = FakeBus(
            [ROM1],
            {ROM1: [DS18B20BusError("CRC"), 21.0]},
        )
        adapter = DS18B20Adapter(bus, manager_for(ID1))
        run_one_sensor_cycle(adapter)
        self.assertEqual(adapter.status()["last_error"], ERROR_READ)
        self.assertEqual(adapter.step(1001), 1)
        self.assertEqual(adapter.step(1751), 1)
        status = adapter.status()
        self.assertIsNone(status["last_error"])
        self.assertIsNone(status["devices"][0]["last_error"])

    def test_unexpected_bus_and_manager_errors_fault_until_explicit_reset(self):
        bus = FakeBus([ROM1])
        bus.scan_plan.append(MemoryError("bus allocation"))
        adapter = DS18B20Adapter(bus, manager_for(ID1))
        with self.assertRaises(MemoryError):
            adapter.step(0)
        self.assertEqual(adapter.state, STATE_FAULTED)
        before = list(bus.calls)
        self.assertEqual(adapter.step(1), 0)
        self.assertEqual(bus.calls, before)
        self.assertTrue(adapter.reset_fault())
        self.assertEqual(adapter.step(2), 1)

        class FailingManager:
            assignments = {}
            max_discovered_sensors = 16

            def record_discovery(self, rom_ids, now_ms):
                raise MemoryError("manager")

            def record_valid(self, rom_id, value, now_ms):
                return True

            def record_failure(self, rom_id, now_ms, reason):
                return True

        second = DS18B20Adapter(FakeBus([ROM1]), FailingManager())
        with self.assertRaises(MemoryError):
            second.step(0)
        self.assertEqual(second.state, STATE_FAULTED)

    def test_discovery_allocation_failure_is_atomic_and_fault_latched(self):
        bus = FakeBus([ROM1])
        manager = manager_for(ID1)
        adapter = DS18B20Adapter(bus, manager)
        with mock.patch.object(
            DS18B20Adapter,
            "_new_device",
            side_effect=MemoryError("device allocation"),
        ):
            with self.assertRaises(MemoryError):
                adapter.step(0)
        self.assertEqual(adapter.state, STATE_FAULTED)
        self.assertEqual(manager.snapshot(0)["discovered_rom_ids"], ())
        before = list(bus.calls)
        self.assertEqual(adapter.step(1), 0)
        self.assertEqual(bus.calls, before)

    def test_fault_cleanup_oom_cannot_prevent_or_rearm_fault_latch(self):
        bus = FakeBus([ROM1])
        bus.scan_plan.append(MemoryError("original bus failure"))
        adapter = DS18B20Adapter(bus, manager_for(ID1))
        with mock.patch.object(
            adapter,
            "_clear_all_trust",
            side_effect=MemoryError("trust cleanup"),
        ):
            with self.assertRaisesRegex(MemoryError, "original bus failure"):
                adapter.step(0)
            self.assertEqual(adapter.state, STATE_FAULTED)
            self.assertEqual(adapter.status()["bus_contract_errors"], 1)
            self.assertEqual(adapter.status()["trust_cleanup_errors"], 1)
            with self.assertRaisesRegex(MemoryError, "trust cleanup"):
                adapter.reset_fault()
            self.assertEqual(adapter.state, STATE_FAULTED)

    def test_deinit_closes_immediately_and_retries_failed_cleanup(self):
        bus = FakeBus()
        bus.deinit_plan.extend((OSError("busy"), None))
        adapter = DS18B20Adapter(bus, TemperatureManager())
        with self.assertRaises(OSError):
            adapter.deinit()
        self.assertTrue(adapter.closed)
        self.assertEqual(adapter.state, STATE_CLOSED)
        self.assertEqual(adapter.step(0), 0)
        self.assertTrue(adapter.deinit())
        self.assertTrue(adapter.status()["cleanup_complete"])
        self.assertEqual(
            len([call for call in bus.calls if call[0] == "deinit"]), 2
        )

    def test_false_deinit_result_is_not_reported_as_cleanup_success(self):
        bus = FakeBus()
        bus.deinit_plan.extend((False, None))
        adapter = DS18B20Adapter(bus, TemperatureManager())
        with self.assertRaises(DS18B20BusError):
            adapter.deinit()
        self.assertFalse(adapter.status()["cleanup_complete"])
        self.assertTrue(adapter.deinit())


class TestDS18B20AdapterIntegration(unittest.TestCase):
    def test_unassigned_live_value_is_bounded_and_later_assignable(self):
        bus = FakeBus([ROM1], {ROM1: [18.5, 19.0]})
        manager = TemperatureManager()
        adapter = DS18B20Adapter(bus, manager)
        run_one_sensor_cycle(adapter)
        first = adapter.status()
        self.assertEqual(first["manager_rejections"], 1)
        self.assertEqual(first["devices"][0]["value_c"], 18.5)
        self.assertIsNone(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 751)["value_c"]
        )
        first["devices"][0]["value_c"] = 99
        self.assertEqual(adapter.status()["devices"][0]["value_c"], 18.5)

        manager.configure_assignments({SENSOR_ROLE_ROOF_TENT: ID1})
        self.assertEqual(adapter.step(1001), 1)
        self.assertEqual(adapter.step(1751), 1)
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 1751)["value_c"],
            19.0,
        )

    def test_assignment_change_mid_cycle_cannot_cross_wire_roles(self):
        bus = FakeBus([ROM1, ROM2], {ROM1: 11, ROM2: 22})
        manager = manager_for(ID1, ID2)
        adapter = DS18B20Adapter(bus, manager)
        self.assertEqual(adapter.step(0), 1)
        self.assertEqual(adapter.step(1), 1)
        manager.configure_assignments(
            {
                SENSOR_ROLE_ROOF_TENT: ID2,
                SENSOR_ROLE_CABIN: ID1,
            }
        )
        self.assertEqual(adapter.step(751), 1)
        self.assertEqual(adapter.step(752), 1)
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 752)["value_c"],
            22.0,
        )
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_CABIN, 752)["value_c"],
            11.0,
        )

    def test_discovery_request_during_conversion_is_deferred_safely(self):
        bus = FakeBus([ROM1], {ROM1: 20})
        adapter = DS18B20Adapter(bus, manager_for(ID1))
        self.assertEqual(adapter.step(0), 1)
        self.assertEqual(adapter.step(1), 1)
        self.assertTrue(adapter.request_discovery())
        self.assertEqual(adapter.step(2), 0)
        self.assertEqual(adapter.step(751), 1)
        self.assertEqual(adapter.step(752), 1)
        self.assertEqual(bus.calls[-1], ("scan",))


if __name__ == "__main__":
    unittest.main()
