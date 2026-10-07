import inspect
import unittest

from tools import dfr0975u_uart_direct_status_probe as probe


REAL_OFF_STATUS = bytes.fromhex(
    "AA 04 13 00 0F 00 01 00 1E 7F 00 80 01 2F "
    "00 00 00 00 00 00 00 00 00 60 6D A0"
)


class TestDFR0975UUARTDirectStatusProbe(unittest.TestCase):
    def test_wrong_confirmations_stop_before_hardware(self):
        with self.assertRaisesRegex(RuntimeError, "exact arm confirmation"):
            probe.arm("wrong")
        with self.assertRaisesRegex(RuntimeError, "exact trigger confirmation"):
            probe.trigger("wrong")

    def test_exact_requests_and_real_status_response(self):
        self.assertEqual(probe.EXPECTED_INIT_REQUEST.hex(), "aa030000049f3d")
        self.assertEqual(probe.EXPECTED_STATUS_REQUEST.hex(), "aa0300000f587c")
        parsed = probe.validate_status_response(REAL_OFF_STATUS)
        self.assertTrue(parsed["crc_valid"])
        self.assertEqual(parsed["status"]["heater_state_name"], "off")
        damaged = bytearray(REAL_OFF_STATUS)
        damaged[-1] ^= 1
        with self.assertRaisesRegex(RuntimeError, "CRC"):
            probe.validate_status_response(damaged)

    def test_source_is_bounded_and_has_no_dangerous_command_surface(self):
        source = inspect.getsource(probe)
        self.assertEqual(source.count("_write_exact("), 3)
        self.assertIn("EXPECTED_INIT_REQUEST", source)
        self.assertIn("EXPECTED_STATUS_REQUEST", source)
        self.assertNotIn("build_start", source)
        self.assertNotIn("request_start", source)
        self.assertNotIn("build_shutdown", source)
        self.assertNotIn("request_shutdown", source)
        self.assertNotIn("build_external_temperature", source)
        self.assertNotIn("request_external_temperature", source)
        self.assertNotIn("write_flash", source)
        self.assertNotIn("erase_flash", source)
        self.assertNotIn("open(", source)


if __name__ == "__main__":
    unittest.main()
