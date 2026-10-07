import ast
import inspect
import textwrap
import unittest

from tools import dfr0975u_uart_direct_init_probe as probe


REAL_INIT = bytes.fromhex("AA 04 05 00 04 12 8A 00 3D D6 CB A6")


class TestDFR0975UUARTDirectInitProbe(unittest.TestCase):
    def test_wrong_arm_confirmation_stops_before_target_imports(self):
        with self.assertRaisesRegex(RuntimeError, "exact arm confirmation"):
            probe.arm("wrong")

    def test_wrong_trigger_confirmation_stops_before_target_imports(self):
        with self.assertRaisesRegex(RuntimeError, "exact trigger confirmation"):
            probe.trigger("wrong")
        with self.assertRaisesRegex(RuntimeError, "exact trigger confirmation"):
            probe.trigger_bounded("wrong")

    def test_exact_request_is_verified_init_and_response_is_strict(self):
        self.assertEqual(probe.EXPECTED_REQUEST.hex(), "aa030000049f3d")
        parsed = probe.validate_init_response(REAL_INIT)
        self.assertTrue(parsed["crc_valid"])
        damaged = bytearray(REAL_INIT)
        damaged[-1] ^= 1
        with self.assertRaisesRegex(RuntimeError, "CRC"):
            probe.validate_init_response(damaged)

    def test_source_has_one_write_site_strict_bound_and_no_dangerous_command(self):
        source = inspect.getsource(probe)
        self.assertEqual(source.count("uart.write(EXPECTED_REQUEST)"), 1)
        self.assertEqual(probe.MAX_BOUNDED_INIT_ATTEMPTS, 3)
        self.assertEqual(probe.INIT_RETRY_INTERVAL_MS, 1000)
        self.assertIn("1 <= max_attempts <= 3", source)
        self.assertIn("TRIGGER_CONFIRMATION, 1, PASS_TOKEN", source)
        self.assertIn("MAX_BOUNDED_INIT_ATTEMPTS", inspect.getsource(probe.trigger_bounded))
        self.assertNotIn("build_start", source)
        self.assertNotIn("request_start", source)
        self.assertNotIn("build_shutdown", source)
        self.assertNotIn("request_shutdown", source)
        self.assertNotIn("build_external_temperature", source)
        self.assertNotIn("request_external_temperature", source)
        self.assertNotIn("write_flash", source)
        self.assertNotIn("erase_flash", source)
        self.assertNotIn("open(", source)
        self.assertIn("writes=0", source)
        self.assertIn("green RX line is not idle-high", source)
        self.assertIn("white TX line is not idle-high", source)
        self.assertIn("green RX line is not idle-high immediately before write", source)
        self.assertIn("white TX line is not idle-high immediately before write", source)

    def test_trigger_imports_pin_for_prewrite_pad_checks(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(probe._trigger)))
        imports_pin = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "machine"
            and any(alias.name == "Pin" for alias in node.names)
            for node in ast.walk(tree)
        )
        self.assertTrue(imports_pin)


if __name__ == "__main__":
    unittest.main()
