import ast
import builtins
import inspect
import runpy
import sys
import types
import unittest
from unittest import mock

import board_config
import hardware.micropython_ds18b20 as hardware_module
from adapters.ds18b20_adapter import (
    DS18B20Adapter,
    DS18B20BusError,
    dallas_crc8,
)
from app.temperature_manager import (
    SENSOR_ROLE_ROOF_TENT,
    TemperatureManager,
)
from hardware.micropython_ds18b20 import (
    DS18B20HardwareCleanupError,
    DS18B20HardwareError,
    ERROR_CONVERSION,
    ERROR_DRIVER_CONTRACT,
    ERROR_LINE_LOW,
    ERROR_READ,
    ERROR_SCAN,
    MicroPythonDS18B20Bus,
    open_ds18b20_adapter_from_board_config,
)


ROM1 = bytes.fromhex("280102030405069e")
ID1 = ROM1.hex()


def rom_for_family(family):
    prefix = bytes((family, 1, 2, 3, 4, 5, 6))
    raw_rom = prefix + bytes((dallas_crc8(prefix),))
    assert dallas_crc8(raw_rom) == 0
    return raw_rom


class FakeDriver:
    def __init__(self, scan_result=None, read_result=20.0):
        self.scan_result = [] if scan_result is None else scan_result
        self.read_result = read_result
        self.convert_result = None
        self.calls = []

    @staticmethod
    def _resolve(value):
        if isinstance(value, BaseException):
            raise value
        return value

    def scan(self):
        self.calls.append(("scan",))
        return self._resolve(self.scan_result)

    def convert_temp(self):
        self.calls.append(("convert_temp",))
        return self._resolve(self.convert_result)

    def read_temp(self, raw_rom):
        raw_rom = bytes(raw_rom)
        self.calls.append(("read_temp", raw_rom))
        return self._resolve(self.read_result)


class FakePin:
    IN = 77

    def __init__(self):
        self.init_calls = []
        self.init_plan = []
        self.value_calls = 0
        self.value_plan = []
        self.level = 1

    def init(self, mode, **kwargs):
        self.init_calls.append((mode, dict(kwargs)))
        if self.init_plan:
            result = self.init_plan.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return None

    def value(self):
        self.value_calls += 1
        if self.value_plan:
            result = self.value_plan.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return self.level


class FakeOneWireError(Exception):
    pass


def bus_for(
    driver=None,
    pin=None,
    max_sensors=16,
    onewire_error_type=FakeOneWireError,
):
    if driver is None:
        driver = FakeDriver()
    if pin is None:
        pin = FakePin()
    return MicroPythonDS18B20Bus(
        driver,
        pin,
        FakePin.IN,
        max_sensors=max_sensors,
        onewire_error_type=onewire_error_type,
    )


class TestMicroPythonDS18B20Bus(unittest.TestCase):
    def test_constructor_validates_port_without_io(self):
        driver = FakeDriver([ROM1])
        pin = FakePin()
        port = bus_for(driver, pin, max_sensors=3)
        self.assertEqual(driver.calls, [])
        self.assertEqual(pin.init_calls, [])
        self.assertEqual(port.max_sensors, 3)
        with self.assertRaises(AttributeError):
            port.max_sensors = 1
        for broken_driver in (None, object()):
            with self.subTest(driver=broken_driver):
                with self.assertRaises(ValueError):
                    MicroPythonDS18B20Bus(
                        broken_driver, pin, FakePin.IN
                    )
        with self.assertRaises(ValueError):
            MicroPythonDS18B20Bus(driver, object(), FakePin.IN)
        with self.assertRaises(ValueError):
            MicroPythonDS18B20Bus(driver, pin, None)
        with self.assertRaises(ValueError):
            MicroPythonDS18B20Bus(
                driver, pin, FakePin.IN, max_sensors=True
            )
        with self.assertRaises(ValueError):
            MicroPythonDS18B20Bus(
                driver,
                pin,
                FakePin.IN,
                onewire_error_type="not an exception class",
            )

    def test_scan_copies_and_preserves_official_family_mix_for_core(self):
        rom10 = bytearray(rom_for_family(0x10))
        rom22 = bytearray(rom_for_family(0x22))
        rom28 = bytearray(ROM1)
        result = bus_for(FakeDriver([rom10, rom22, rom28])).scan()
        self.assertEqual(
            result, (bytes(rom10), bytes(rom22), bytes(rom28))
        )
        self.assertTrue(all(type(raw_rom) is bytes for raw_rom in result))
        rom10[:] = rom22[:] = rom28[:] = b"\x00" * 8
        self.assertEqual(result[2], ROM1)

    def test_scan_contract_and_driver_errors_are_normalized(self):
        invalid = (
            (value for value in (ROM1,)),
            ["not bytes"],
            [ROM1, ROM1],
        )
        for result in invalid:
            with self.subTest(result=result):
                max_sensors = 1 if isinstance(result, list) else 16
                with self.assertRaises(DS18B20BusError) as context:
                    bus_for(
                        FakeDriver(result), max_sensors=max_sensors
                    ).scan()
                self.assertEqual(
                    str(context.exception), ERROR_DRIVER_CONTRACT
                )
        for expected in (OSError("bus"), FakeOneWireError("presence")):
            with self.subTest(expected=expected):
                with self.assertRaises(DS18B20BusError) as context:
                    bus_for(FakeDriver(expected)).scan()
                self.assertEqual(str(context.exception), ERROR_SCAN)
        with self.assertRaises(MemoryError):
            bus_for(FakeDriver(MemoryError("allocation"))).scan()
        unexpected = AssertionError("driver bug")
        with self.assertRaises(AssertionError) as context:
            bus_for(FakeDriver(unexpected)).scan()
        self.assertIs(context.exception, unexpected)

    def test_convert_temp_requires_official_none_result(self):
        driver = FakeDriver()
        port = bus_for(driver)
        self.assertIsNone(port.start_conversion())
        self.assertEqual(driver.calls, [("convert_temp",)])
        driver.convert_result = False
        with self.assertRaises(DS18B20BusError) as context:
            port.start_conversion()
        self.assertEqual(str(context.exception), ERROR_DRIVER_CONTRACT)
        for expected in (OSError("convert"), FakeOneWireError("presence")):
            driver.convert_result = expected
            with self.subTest(expected=expected):
                with self.assertRaises(DS18B20BusError) as context:
                    port.start_conversion()
                self.assertEqual(str(context.exception), ERROR_CONVERSION)
        driver.convert_result = MemoryError("allocation")
        with self.assertRaises(MemoryError):
            port.start_conversion()
        driver.convert_result = ValueError("driver contract bug")
        with self.assertRaises(ValueError):
            port.start_conversion()

    def test_read_temp_crc_exception_is_normalized_without_fake_value(self):
        driver = FakeDriver(read_result=0.0)
        port = bus_for(driver)
        self.assertEqual(port.read_celsius(bytearray(ROM1)), 0.0)
        self.assertEqual(driver.calls[-1], ("read_temp", ROM1))
        driver.read_result = Exception("CRC error")
        with self.assertRaises(DS18B20BusError) as context:
            port.read_celsius(ROM1)
        self.assertEqual(str(context.exception), ERROR_READ)
        driver.read_result = MemoryError("allocation")
        with self.assertRaises(MemoryError):
            port.read_celsius(ROM1)
        driver.read_result = Exception("not the official CRC error")
        with self.assertRaises(Exception) as context:
            port.read_celsius(ROM1)
        self.assertEqual(str(context.exception), "not the official CRC error")
        for expected in (OSError("read"), FakeOneWireError("presence")):
            driver.read_result = expected
            with self.subTest(expected=expected):
                with self.assertRaises(DS18B20BusError) as context:
                    port.read_celsius(ROM1)
                self.assertEqual(str(context.exception), ERROR_READ)
        with self.assertRaises(DS18B20BusError) as context:
            port.read_celsius("not bytes")
        self.assertEqual(str(context.exception), ERROR_DRIVER_CONTRACT)

    def test_stuck_low_line_cannot_be_reported_as_zero_celsius(self):
        driver = FakeDriver(read_result=0.0)
        pin = FakePin()
        pin.value_plan = [1, 0]
        with self.assertRaises(DS18B20BusError) as context:
            bus_for(driver, pin).read_celsius(ROM1)
        self.assertEqual(str(context.exception), ERROR_LINE_LOW)
        self.assertEqual(driver.calls, [("read_temp", ROM1)])

        driver = FakeDriver([ROM1])
        pin = FakePin()
        pin.level = 0
        with self.assertRaises(DS18B20BusError) as context:
            bus_for(driver, pin).scan()
        self.assertEqual(str(context.exception), ERROR_LINE_LOW)
        self.assertEqual(driver.calls, [])

        for action in ("scan", "convert"):
            driver = FakeDriver([ROM1])
            pin = FakePin()
            pin.value_plan = [1, 0]
            port = bus_for(driver, pin)
            with self.subTest(action=action):
                with self.assertRaises(DS18B20BusError) as context:
                    if action == "scan":
                        port.scan()
                    else:
                        port.start_conversion()
                self.assertEqual(str(context.exception), ERROR_LINE_LOW)

    def test_deinit_sets_pin_input_and_retries_until_confirmed(self):
        pin = FakePin()
        pin.init_plan.extend((OSError("busy"), None))
        port = bus_for(pin=pin)
        with self.assertRaises(DS18B20HardwareError):
            port.deinit()
        self.assertTrue(port.closed)
        self.assertFalse(port.cleanup_complete)
        with self.assertRaises(DS18B20BusError):
            port.scan()
        self.assertIsNone(port.deinit())
        self.assertTrue(port.cleanup_complete)
        expected = (FakePin.IN, {"pull": None, "hold": False})
        self.assertEqual(pin.init_calls, [expected, expected])
        self.assertIsNone(port.deinit())
        self.assertEqual(len(pin.init_calls), 2)

    def test_deinit_non_none_and_base_exception_remain_retryable(self):
        pin = FakePin()
        pin.init_plan = [False, None]
        port = bus_for(pin=pin)
        with self.assertRaises(DS18B20HardwareError):
            port.deinit()
        self.assertTrue(port.closed)
        self.assertFalse(port.cleanup_complete)
        self.assertIsNone(port.deinit())

        pin = FakePin()
        primary = KeyboardInterrupt()
        pin.init_plan = [primary, None]
        port = bus_for(pin=pin)
        with self.assertRaises(KeyboardInterrupt) as context:
            port.deinit()
        self.assertIs(context.exception, primary)
        self.assertTrue(port.closed)
        self.assertFalse(port.cleanup_complete)
        self.assertIsNone(port.deinit())

    def test_mixed_official_family_scan_is_rejected_atomically_by_core(self):
        driver = FakeDriver([rom_for_family(0x10), ROM1])
        manager = TemperatureManager(
            {SENSOR_ROLE_ROOF_TENT: ID1},
            max_discovered_sensors=2,
        )
        adapter = DS18B20Adapter(
            bus_for(driver, max_sensors=2), manager, max_sensors=2
        )
        self.assertEqual(adapter.step(0), 1)
        self.assertEqual(adapter.status()["scan_errors"], 1)
        self.assertEqual(manager.snapshot(0)["discovered_rom_ids"], ())

    def test_bad_rom_crc_and_duplicates_reach_strict_core_atomically(self):
        bad_crc = ROM1[:-1] + bytes((ROM1[-1] ^ 0x01,))
        for scan_result in ([bad_crc], [ROM1, ROM1]):
            with self.subTest(scan_result=scan_result):
                manager = TemperatureManager(
                    {SENSOR_ROLE_ROOF_TENT: ID1},
                    max_discovered_sensors=3,
                )
                adapter = DS18B20Adapter(
                    bus_for(FakeDriver(scan_result), max_sensors=3),
                    manager,
                    max_sensors=3,
                )
                self.assertEqual(adapter.step(0), 1)
                self.assertEqual(adapter.status()["scan_errors"], 1)
                self.assertEqual(manager.snapshot(0)["discovered_rom_ids"], ())

    def test_wrapper_and_core_complete_one_realistic_cycle(self):
        driver = FakeDriver([bytearray(ROM1)], read_result=0.0)
        manager = TemperatureManager({SENSOR_ROLE_ROOF_TENT: ID1})
        adapter = DS18B20Adapter(bus_for(driver), manager)
        self.assertEqual(adapter.step(0), 1)
        self.assertEqual(adapter.step(1), 1)
        self.assertEqual(adapter.step(751), 1)
        sensor = manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 751)
        self.assertEqual(sensor["value_c"], 0.0)
        self.assertEqual(
            driver.calls,
            [("scan",), ("convert_temp",), ("read_temp", ROM1)],
        )


class FactoryPin:
    IN = 88
    OPEN_DRAIN = 89
    PULL_UP = 90
    instances = []
    cleanup_plan = []
    constructor_plan = []

    def __init__(self, number, mode=None, **kwargs):
        self.number = number
        self.constructor_mode = mode
        self.constructor_kwargs = dict(kwargs)
        self.init_calls = []
        self.init_plan = list(type(self).cleanup_plan)
        self.level = 1
        type(self).instances.append(self)
        if type(self).constructor_plan:
            result = type(self).constructor_plan.pop(0)
            if isinstance(result, BaseException):
                raise result

    def init(self, mode, **kwargs):
        self.init_calls.append((mode, dict(kwargs)))
        if mode == type(self).IN and self.init_plan:
            result = self.init_plan.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return None

    def value(self):
        return self.level


class FactoryOneWire:
    instances = []

    def __init__(self, pin):
        self.pin = pin
        type(self).instances.append(self)


class FactoryDS18X20(FakeDriver):
    instances = []
    constructor_error = None

    def __init__(self, one_wire):
        if type(self).constructor_error is not None:
            raise type(self).constructor_error
        super().__init__([bytearray(ROM1)], 19.5)
        self.one_wire = one_wire
        type(self).instances.append(self)


def fake_hardware_modules():
    machine = types.ModuleType("machine")
    machine.Pin = FactoryPin
    onewire = types.ModuleType("onewire")
    onewire.OneWire = FactoryOneWire
    onewire.OneWireError = FakeOneWireError
    ds18x20 = types.ModuleType("ds18x20")
    ds18x20.DS18X20 = FactoryDS18X20
    return {"machine": machine, "onewire": onewire, "ds18x20": ds18x20}


def approved_board_patches():
    return mock.patch.multiple(
        board_config,
        ONEWIRE_PIN=18,
        ONEWIRE_PIN_APPROVED=True,
        ONEWIRE_MAX_SENSORS=3,
        ONEWIRE_CONVERSION_WAIT_MS=750,
        ONEWIRE_POLL_INTERVAL_MS=1000,
        ONEWIRE_DISCOVERY_INTERVAL_MS=30000,
    )


class TestMicroPythonDS18B20Factory(unittest.TestCase):
    def setUp(self):
        FactoryPin.instances = []
        FactoryPin.cleanup_plan = []
        FactoryPin.constructor_plan = []
        FactoryOneWire.instances = []
        FactoryDS18X20.instances = []
        FactoryDS18X20.constructor_error = None

    def test_module_import_is_hardware_free(self):
        source = inspect.getsource(hardware_module)
        tree = ast.parse(source)
        top_level_imports = []
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                top_level_imports.extend(
                    alias.name.split(".")[0] for alias in statement.names
                )
            elif isinstance(statement, ast.ImportFrom):
                top_level_imports.append(
                    (statement.module or "").split(".")[0]
                )
        self.assertNotIn("machine", top_level_imports)
        self.assertNotIn("onewire", top_level_imports)
        self.assertNotIn("ds18x20", top_level_imports)
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in ("machine", "onewire", "ds18x20"):
                raise AssertionError("hardware import attempted")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            namespace = runpy.run_path(hardware_module.__file__)
        self.assertIn("MicroPythonDS18B20Bus", namespace)

    def test_closed_board_lock_fails_before_hardware_import(self):
        real_import = builtins.__import__
        hardware_imports = []

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in ("machine", "onewire", "ds18x20"):
                hardware_imports.append(name)
                raise AssertionError("hardware import attempted")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(
            board_config, "ONEWIRE_PIN_APPROVED", False
        ), mock.patch("builtins.__import__", side_effect=guarded_import):
            with self.assertRaisesRegex(RuntimeError, "not been electrically"):
                open_ds18b20_adapter_from_board_config(TemperatureManager())
        self.assertEqual(hardware_imports, [])
        self.assertEqual(FactoryPin.instances, [])

    def test_invalid_manager_fails_before_hardware_import(self):
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in ("machine", "onewire", "ds18x20"):
                raise AssertionError("hardware import attempted")
            return real_import(name, *args, **kwargs)

        with approved_board_patches(), mock.patch(
            "builtins.__import__", side_effect=guarded_import
        ):
            with self.assertRaises(ValueError):
                open_ds18b20_adapter_from_board_config(object())

    def test_approved_factory_uses_official_api_without_starting_io_cycle(self):
        manager = TemperatureManager(
            {SENSOR_ROLE_ROOF_TENT: ID1},
            max_discovered_sensors=3,
        )
        with approved_board_patches(), mock.patch.dict(
            sys.modules, fake_hardware_modules()
        ):
            adapter = open_ds18b20_adapter_from_board_config(manager)
        self.assertEqual(len(FactoryPin.instances), 1)
        pin = FactoryPin.instances[0]
        self.assertEqual((pin.number, pin.constructor_mode), (18, None))
        self.assertEqual(pin.constructor_kwargs, {})
        self.assertEqual(
            pin.init_calls[0],
            (
                FactoryPin.OPEN_DRAIN,
                {
                    "pull": FactoryPin.PULL_UP,
                    "value": 1,
                    "hold": False,
                },
            ),
        )
        driver = FactoryDS18X20.instances[0]
        self.assertEqual(driver.calls, [])
        self.assertEqual(adapter.conversion_wait_ms, 750)
        self.assertEqual(adapter.poll_interval_ms, 1000)
        self.assertEqual(adapter.max_sensors, 3)
        self.assertEqual(adapter.step(0), 1)
        self.assertEqual(adapter.step(1), 1)
        self.assertEqual(adapter.step(751), 1)
        self.assertEqual(
            manager.sensor_snapshot(SENSOR_ROLE_ROOF_TENT, 751)["value_c"],
            19.5,
        )
        self.assertTrue(adapter.deinit())
        self.assertEqual(
            pin.init_calls[-1],
            (FactoryPin.IN, {"pull": None, "hold": False}),
        )

    def test_base_exception_after_pin_open_is_cleaned_up(self):
        FactoryDS18X20.constructor_error = KeyboardInterrupt()
        with approved_board_patches(), mock.patch.dict(
            sys.modules, fake_hardware_modules()
        ):
            with self.assertRaises(KeyboardInterrupt):
                open_ds18b20_adapter_from_board_config(
                    TemperatureManager(max_discovered_sensors=3)
                )
        self.assertEqual(
            FactoryPin.instances[0].init_calls,
            [
                (
                    FactoryPin.OPEN_DRAIN,
                    {
                        "pull": FactoryPin.PULL_UP,
                        "value": 1,
                        "hold": False,
                    },
                ),
                (FactoryPin.IN, {"pull": None, "hold": False}),
            ],
        )

    def test_persistent_cleanup_failure_is_reported(self):
        primary = RuntimeError("driver setup")
        cleanup_final = OSError("still busy")
        FactoryDS18X20.constructor_error = primary
        FactoryPin.cleanup_plan = [OSError("busy"), cleanup_final]
        with approved_board_patches(), mock.patch.dict(
            sys.modules, fake_hardware_modules()
        ):
            with self.assertRaises(DS18B20HardwareCleanupError) as context:
                open_ds18b20_adapter_from_board_config(
                    TemperatureManager(max_discovered_sensors=3)
                )
        self.assertIs(context.exception.primary_error, primary)
        self.assertIs(context.exception.cleanup_error, cleanup_final)
        self.assertEqual(len(FactoryPin.instances[0].init_calls), 3)

    def test_pin_constructor_failure_gets_bounded_cleanup_attempt(self):
        primary = KeyboardInterrupt()
        FactoryPin.constructor_plan = [primary, None]
        with approved_board_patches(), mock.patch.dict(
            sys.modules, fake_hardware_modules()
        ):
            with self.assertRaises(KeyboardInterrupt) as context:
                open_ds18b20_adapter_from_board_config(
                    TemperatureManager(max_discovered_sensors=3)
                )
        self.assertIs(context.exception, primary)
        self.assertEqual(len(FactoryPin.instances), 2)
        cleanup_pin = FactoryPin.instances[1]
        self.assertEqual(cleanup_pin.constructor_mode, FactoryPin.IN)
        self.assertEqual(
            cleanup_pin.constructor_kwargs, {"pull": None, "hold": False}
        )

    def test_transient_factory_cleanup_preserves_primary_exception(self):
        primary = RuntimeError("driver setup")
        FactoryDS18X20.constructor_error = primary
        FactoryPin.cleanup_plan = [OSError("busy"), None]
        with approved_board_patches(), mock.patch.dict(
            sys.modules, fake_hardware_modules()
        ):
            with self.assertRaises(RuntimeError) as context:
                open_ds18b20_adapter_from_board_config(
                    TemperatureManager(max_discovered_sensors=3)
                )
        self.assertIs(context.exception, primary)
        self.assertEqual(len(FactoryPin.instances[0].init_calls), 3)

    def test_public_factory_has_no_hardware_or_unlock_arguments(self):
        parameters = inspect.signature(
            open_ds18b20_adapter_from_board_config
        ).parameters
        self.assertEqual(tuple(parameters), ("temperature_manager",))


if __name__ == "__main__":
    unittest.main()
