import contextlib
import io
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import board_config

from tools.uart_loopback_smoke import (
    LOOPBACK_CONFIRMATION,
    LOOPBACK_PAYLOAD,
    _open_loopback_uart,
    run,
)


class FakeClock:
    def __init__(self, now=0, modulus=None):
        self.now = now
        self.modulus = modulus

    def ticks_ms(self):
        return self.now

    def ticks_diff(self, current, previous):
        if self.modulus is None:
            return current - previous
        half = self.modulus // 2
        return ((current - previous + half) % self.modulus) - half

    def sleep_ms(self, milliseconds):
        self.now += milliseconds
        if self.modulus is not None:
            self.now %= self.modulus


class LoopbackUART:
    DEFAULT_WRITE = object()

    def __init__(
        self,
        echo=True,
        write_result=DEFAULT_WRITE,
        extra_echo=b"",
        max_read_bytes=None,
    ):
        self.echo = echo
        self.write_result = write_result
        self.extra_echo = bytes(extra_echo)
        self.max_read_bytes = max_read_bytes
        self.rx = bytearray()
        self.writes = []
        self.deinitialized = False

    def any(self):
        return len(self.rx)

    def read(self, count):
        if self.max_read_bytes is not None:
            count = min(count, self.max_read_bytes)
        chunk = bytes(self.rx[:count])
        del self.rx[:count]
        return chunk

    def write(self, data):
        raw = bytes(data)
        self.writes.append(raw)
        if self.echo:
            self.rx.extend(raw + self.extra_echo)
        if self.write_result is self.DEFAULT_WRITE:
            return len(raw)
        return self.write_result

    def deinit(self):
        self.deinitialized = True


def config_with(**overrides):
    names = (
        "BOARD_SKU",
        "UART_ID",
        "UART_TX_PIN",
        "UART_RX_PIN",
        "UART_BAUDRATE",
        "UART_BITS",
        "UART_PARITY",
        "UART_STOP_BITS",
        "UART_PROTOCOL_TX_ENABLED",
        "UART_DRIVER_TIMEOUT_MS",
        "UART_DRIVER_TIMEOUT_CHAR_MS",
        "UART_INVERT",
        "UART_RX_BUFFER_SIZE",
    )
    values = {name: getattr(board_config, name) for name in names}
    values.update(overrides)
    values["require_uart_configuration"] = lambda: None
    return SimpleNamespace(**values)


def run_with(
    uart,
    clock=None,
    confirmation=LOOPBACK_CONFIRMATION,
    config_module=board_config,
    timeout_ms=20,
    quiet_ms=10,
):
    clock = clock or FakeClock()
    return run(
        confirmation,
        uart_factory=lambda: uart,
        config_module=config_module,
        timeout_ms=timeout_ms,
        quiet_ms=quiet_ms,
        ticks_ms=clock.ticks_ms,
        ticks_diff=clock.ticks_diff,
        sleep_ms=clock.sleep_ms,
    )


class TestUARTLoopbackSmoke(unittest.TestCase):
    def test_payload_is_not_an_autoterm_frame(self):
        self.assertNotEqual(LOOPBACK_PAYLOAD[0], 0xAA)
        self.assertNotIn(bytes((0xAA,)), LOOPBACK_PAYLOAD)

    def test_exact_manual_confirmation_is_required_before_uart_open(self):
        opened = []
        with self.assertRaises(RuntimeError):
            run(
                "yes",
                uart_factory=lambda: opened.append(True),
                config_module=board_config,
            )
        self.assertEqual(opened, [])

    def test_default_factory_explicitly_sets_normal_uart_profile(self):
        created = []

        class CapturingUART(LoopbackUART):
            def __init__(self, *args, **kwargs):
                super().__init__()
                self.constructor_args = args
                self.constructor_kwargs = kwargs
                created.append(self)

        machine_module = ModuleType("machine")
        machine_module.UART = CapturingUART
        with mock.patch.dict(sys.modules, {"machine": machine_module}):
            uart = _open_loopback_uart(board_config)

        self.assertIs(uart, created[0])
        self.assertEqual(uart.constructor_args, (2,))
        self.assertEqual(uart.constructor_kwargs["baudrate"], 9600)
        self.assertEqual(uart.constructor_kwargs["bits"], 8)
        self.assertIsNone(uart.constructor_kwargs["parity"])
        self.assertEqual(uart.constructor_kwargs["stop"], 1)
        self.assertEqual(uart.constructor_kwargs["tx"], 17)
        self.assertEqual(uart.constructor_kwargs["rx"], 16)
        self.assertEqual(uart.constructor_kwargs["timeout"], 0)
        self.assertEqual(uart.constructor_kwargs["timeout_char"], 0)
        self.assertEqual(uart.constructor_kwargs["invert"], 0)
        self.assertEqual(uart.constructor_kwargs["flow"], 0)

    def test_success_writes_once_compares_exact_echo_and_deinitializes(self):
        uart = LoopbackUART()
        with contextlib.redirect_stdout(io.StringIO()):
            result = run_with(uart)
        self.assertEqual(uart.writes, [LOOPBACK_PAYLOAD])
        self.assertTrue(uart.deinitialized)
        self.assertEqual(result["board_sku"], "DFR0654")
        self.assertEqual((result["tx_pin"], result["rx_pin"]), (17, 16))

    def test_echo_may_arrive_in_multiple_chunks(self):
        uart = LoopbackUART(max_read_bytes=3)
        with contextlib.redirect_stdout(io.StringIO()):
            run_with(uart)
        self.assertEqual(uart.writes, [LOOPBACK_PAYLOAD])
        self.assertTrue(uart.deinitialized)

    def test_echo_at_timeout_boundary_is_rejected(self):
        clock = FakeClock()

        class DelayedUART(LoopbackUART):
            def __init__(self):
                super().__init__(echo=False)
                self.pending = None

            def write(self, data):
                raw = bytes(data)
                self.writes.append(raw)
                self.pending = raw
                return len(raw)

            def any(self):
                if self.pending is not None and clock.now >= 20:
                    self.rx.extend(self.pending)
                    self.pending = None
                return len(self.rx)

        uart = DelayedUART()
        with self.assertRaises(RuntimeError):
            run_with(uart, clock=clock, timeout_ms=20)
        self.assertEqual(uart.writes, [LOOPBACK_PAYLOAD])
        self.assertTrue(uart.deinitialized)

    def test_trailing_byte_is_not_a_false_pass(self):
        uart = LoopbackUART(extra_echo=b"\xAA")
        with self.assertRaises(RuntimeError):
            run_with(uart)
        self.assertTrue(uart.deinitialized)

    def test_ready_without_data_during_quiet_phase_is_not_a_false_pass(self):
        class EmptyReadyAfterEchoUART(LoopbackUART):
            def any(self):
                if not self.writes:
                    return len(self.rx)
                return len(self.rx) if self.rx else 1

            def read(self, count):
                if self.rx:
                    return super().read(count)
                return None

        uart = EmptyReadyAfterEchoUART()
        with self.assertRaises(RuntimeError):
            run_with(uart)
        self.assertEqual(uart.writes, [LOOPBACK_PAYLOAD])
        self.assertTrue(uart.deinitialized)

    def test_ticks_wrap_is_handled_during_quiet_phase(self):
        clock = FakeClock(now=60, modulus=64)
        uart = LoopbackUART()
        with contextlib.redirect_stdout(io.StringIO()):
            result = run_with(uart, clock=clock)
        self.assertGreaterEqual(result["elapsed_ms"], 10)
        self.assertTrue(uart.deinitialized)

    def test_missing_echo_times_out_and_deinitializes(self):
        uart = LoopbackUART(echo=False)
        with self.assertRaises(RuntimeError):
            run_with(uart)
        self.assertEqual(uart.writes, [LOOPBACK_PAYLOAD])
        self.assertTrue(uart.deinitialized)

    def test_short_write_fails_and_deinitializes(self):
        for result in (None, True, "16", 2):
            with self.subTest(result=result):
                uart = LoopbackUART(write_result=result)
                with self.assertRaises(RuntimeError):
                    run_with(uart)
                self.assertEqual(uart.writes, [LOOPBACK_PAYLOAD])
                self.assertTrue(uart.deinitialized)

    def test_full_uart_profile_and_exact_false_tx_flag_are_required(self):
        variants = (
            {"BOARD_SKU": "DFR1139"},
            {"UART_ID": 1},
            {"UART_TX_PIN": 16, "UART_RX_PIN": 17},
            {"UART_BAUDRATE": 115200},
            {"UART_BITS": 7},
            {"UART_PARITY": 0},
            {"UART_STOP_BITS": 2},
            {"UART_DRIVER_TIMEOUT_MS": 1},
            {"UART_DRIVER_TIMEOUT_CHAR_MS": 1},
            {"UART_INVERT": 1},
            {"UART_PROTOCOL_TX_ENABLED": 0},
        )
        for overrides in variants:
            with self.subTest(overrides=overrides):
                opened = []
                with self.assertRaises(RuntimeError):
                    run(
                        LOOPBACK_CONFIRMATION,
                        uart_factory=lambda: opened.append(True),
                        config_module=config_with(**overrides),
                    )
                self.assertEqual(opened, [])

    def test_nonquiet_input_blocks_write_and_deinitializes(self):
        uart = LoopbackUART()
        uart.rx.extend(b"unexpected")
        with self.assertRaises(RuntimeError):
            run_with(uart)
        self.assertEqual(uart.writes, [])
        self.assertTrue(uart.deinitialized)

    def test_missing_deinit_blocks_write(self):
        uart = LoopbackUART()
        uart.deinit = None
        with self.assertRaisesRegex(RuntimeError, "deinit"):
            run_with(uart)
        self.assertEqual(uart.writes, [])

    def test_invalid_any_and_read_values_fail_and_deinitialize(self):
        class InvalidAnyUART(LoopbackUART):
            def any(self):
                return True

        invalid_any = InvalidAnyUART()
        with self.assertRaises(RuntimeError):
            run_with(invalid_any)
        self.assertEqual(invalid_any.writes, [])
        self.assertTrue(invalid_any.deinitialized)

        class InvalidReadUART(LoopbackUART):
            def read(self, count):
                return "not bytes"

        invalid_read = InvalidReadUART()
        with self.assertRaises(RuntimeError):
            run_with(invalid_read)
        self.assertEqual(invalid_read.writes, [LOOPBACK_PAYLOAD])
        self.assertTrue(invalid_read.deinitialized)


if __name__ == "__main__":
    unittest.main()
