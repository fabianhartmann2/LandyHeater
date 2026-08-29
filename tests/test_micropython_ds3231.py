import ast
import builtins
import inspect
import runpy
import sys
import types
import unittest
from unittest import mock

import board_config
import hardware.micropython_ds3231 as hardware_module
from adapters.ds3231_adapter import DS3231BusError
from hardware.micropython_ds3231 import (
    DS3231HardwareCleanupError,
    MicroPythonDS3231,
    open_ds3231_from_board_config,
)


class FakePin:
    IN = 88
    instances = []
    cleanup_plan = []

    def __init__(self, number, mode=None, **kwargs):
        self.number = number
        self.mode = mode
        self.kwargs = dict(kwargs)
        self.init_calls = []
        self.plan = list(type(self).cleanup_plan)
        type(self).instances.append(self)

    def init(self, mode, **kwargs):
        self.init_calls.append((mode, dict(kwargs)))
        if self.plan:
            result = self.plan.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return None


class FakeI2C:
    instances = []
    constructor_error = None
    deinit_plan = []

    def __init__(self, i2c_id=0, **kwargs):
        if type(self).constructor_error is not None:
            raise type(self).constructor_error
        self.i2c_id = i2c_id
        self.kwargs = dict(kwargs)
        self.calls = []
        self.registers = bytearray(0x13)
        self.deinit_calls = 0
        self.plan = list(type(self).deinit_plan)
        type(self).instances.append(self)

    def readfrom_mem(self, address, register, length, **kwargs):
        self.calls.append(("read", address, register, length))
        return bytes(self.registers[register : register + length])

    def writeto_mem(self, address, register, data, **kwargs):
        self.calls.append(("write", address, register, bytes(data)))
        self.registers[register : register + len(data)] = data
        return None

    def deinit(self):
        self.deinit_calls += 1
        if self.plan:
            result = self.plan.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return None


def fake_machine_module():
    module = types.ModuleType("machine")
    module.Pin = FakePin
    module.I2C = FakeI2C
    return module


def approved_board():
    return mock.patch.multiple(
        board_config,
        I2C_ID=0,
        I2C_SDA_PIN=21,
        I2C_SCL_PIN=22,
        I2C_PINS_APPROVED=True,
        I2C_FREQUENCY_HZ=100000,
        I2C_TIMEOUT_US=50000,
        DS3231_I2C_ADDRESS=0x68,
    )


class TestMicroPythonDS3231(unittest.TestCase):
    def setUp(self):
        FakePin.instances = []
        FakePin.cleanup_plan = []
        FakeI2C.instances = []
        FakeI2C.constructor_error = None
        FakeI2C.deinit_plan = []
        hardware_module._I2C_LEASED = False
        hardware_module._I2C_LEASE_POISONED = False

    def test_module_import_is_hardware_free(self):
        tree = ast.parse(inspect.getsource(hardware_module))
        top_imports = []
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                top_imports.extend(alias.name for alias in statement.names)
            elif isinstance(statement, ast.ImportFrom):
                top_imports.append(statement.module)
        self.assertNotIn("machine", top_imports)
        real_import = builtins.__import__

        def guard(name, *args, **kwargs):
            if name == "machine":
                raise AssertionError("hardware import attempted")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guard):
            namespace = runpy.run_path(hardware_module.__file__)
        self.assertIn("MicroPythonDS3231", namespace)

    def test_delivered_lock_fails_before_machine_import(self):
        real_import = builtins.__import__
        attempted = []

        def guard(name, *args, **kwargs):
            if name == "machine":
                attempted.append(name)
                raise AssertionError("machine import attempted")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guard):
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                open_ds3231_from_board_config()
        self.assertEqual(attempted, [])

    def test_public_factory_has_no_unlock_or_injection_arguments(self):
        self.assertEqual(
            tuple(inspect.signature(open_ds3231_from_board_config).parameters),
            (),
        )

    def test_factory_constructs_exact_stack_without_bus_io(self):
        with approved_board(), mock.patch.dict(
            sys.modules, {"machine": fake_machine_module()}
        ):
            port = open_ds3231_from_board_config()
        self.assertEqual([pin.number for pin in FakePin.instances], [21, 22])
        i2c = FakeI2C.instances[0]
        self.assertEqual(i2c.i2c_id, 0)
        self.assertEqual(
            i2c.kwargs,
            {
                "sda": FakePin.instances[0],
                "scl": FakePin.instances[1],
                "freq": 100000,
                "timeout": 50000,
            },
        )
        self.assertEqual(i2c.calls, [])
        self.assertFalse(port.closed)
        self.assertIsNone(port.deinit())
        self.assertTrue(port.cleanup_complete)
        self.assertEqual(i2c.deinit_calls, 1)
        for pin in FakePin.instances:
            self.assertEqual(
                pin.init_calls,
                [(FakePin.IN, {"pull": None, "hold": False})],
            )

    def test_double_open_is_blocked_until_confirmed_cleanup(self):
        with approved_board(), mock.patch.dict(
            sys.modules, {"machine": fake_machine_module()}
        ):
            first = open_ds3231_from_board_config()
            with self.assertRaisesRegex(RuntimeError, "already owned"):
                open_ds3231_from_board_config()
            first.deinit()
            second = open_ds3231_from_board_config()
            second.deinit()
        self.assertEqual(len(FakeI2C.instances), 2)

    def test_direct_port_closes_before_retryable_cleanup(self):
        i2c = FakeI2C()
        i2c.plan = [OSError("busy"), None]
        sda = FakePin(21)
        scl = FakePin(22)
        port = MicroPythonDS3231(i2c, sda, scl, FakePin.IN)
        with self.assertRaises(OSError):
            port.deinit()
        self.assertTrue(port.closed)
        with self.assertRaises(DS3231BusError):
            port.status()
        self.assertIsNone(port.deinit())
        self.assertTrue(port.cleanup_complete)

    def test_i2c_cleanup_failure_does_not_skip_pin_release(self):
        i2c = FakeI2C()
        i2c.plan = [OSError("busy"), OSError("still busy")]
        sda = FakePin(21)
        scl = FakePin(22)
        port = MicroPythonDS3231(i2c, sda, scl, FakePin.IN)

        with self.assertRaisesRegex(OSError, "busy"):
            port.deinit()

        self.assertTrue(port.closed)
        self.assertFalse(port.cleanup_complete)
        self.assertEqual(
            sda.init_calls,
            [(FakePin.IN, {"pull": None, "hold": False})],
        )
        self.assertEqual(
            scl.init_calls,
            [(FakePin.IN, {"pull": None, "hold": False})],
        )
        with self.assertRaisesRegex(OSError, "still busy"):
            port.deinit()
        self.assertEqual(len(sda.init_calls), 1)
        self.assertEqual(len(scl.init_calls), 1)

    def test_factory_base_exception_cleans_both_pins(self):
        FakeI2C.constructor_error = KeyboardInterrupt()
        with approved_board(), mock.patch.dict(
            sys.modules, {"machine": fake_machine_module()}
        ):
            with self.assertRaises(KeyboardInterrupt):
                open_ds3231_from_board_config()
        self.assertEqual(len(FakePin.instances), 2)
        for pin in FakePin.instances:
            self.assertEqual(pin.init_calls[-1][0], FakePin.IN)

    def test_failed_lease_claim_does_not_leave_a_ghost_owner(self):
        with approved_board(), mock.patch.dict(
            sys.modules, {"machine": fake_machine_module()}
        ):
            with mock.patch.object(
                MicroPythonDS3231,
                "_claim_lease",
                side_effect=RuntimeError("claim failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "claim failed"):
                    open_ds3231_from_board_config()
            self.assertFalse(hardware_module._I2C_LEASED)
            self.assertFalse(hardware_module._I2C_LEASE_POISONED)
            port = open_ds3231_from_board_config()
            port.deinit()

    def test_persistent_factory_cleanup_preserves_both_errors(self):
        primary = RuntimeError("i2c setup")
        cleanup = OSError("pin stuck")
        FakeI2C.constructor_error = primary
        FakePin.cleanup_plan = [cleanup, cleanup]
        with approved_board(), mock.patch.dict(
            sys.modules, {"machine": fake_machine_module()}
        ):
            with self.assertRaises(DS3231HardwareCleanupError) as context:
                open_ds3231_from_board_config()
        self.assertIs(context.exception.primary_error, primary)
        self.assertIs(context.exception.cleanup_error, cleanup)
        self.assertTrue(hardware_module._I2C_LEASE_POISONED)


if __name__ == "__main__":
    unittest.main()
