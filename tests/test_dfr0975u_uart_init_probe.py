import inspect
import unittest

from tools import dfr0975u_uart_init_probe as probe


REAL_INIT = bytes.fromhex("AA 04 05 00 04 12 8A 00 3D D6 CB A6")


class FakeClock:
    def __init__(self):
        self.now = 0

    def ticks_ms(self):
        return self.now

    @staticmethod
    def ticks_diff(current, previous):
        return current - previous

    def sleep_ms(self, milliseconds):
        self.now += milliseconds


class FakeTransport:
    def __init__(self, batches):
        self.batches = list(batches)

    def poll(self, _now_ms):
        return self.batches.pop(0) if self.batches else []


class TestDFR0975UUARTInitProbe(unittest.TestCase):
    def test_wrong_confirmation_stops_before_target_imports(self):
        with self.assertRaisesRegex(RuntimeError, "exact confirmation"):
            probe.run("wrong")

    def test_exact_request_is_the_verified_init_vector(self):
        self.assertEqual(probe.EXPECTED_REQUEST.hex(), "aa030000049f3d")

    def test_validates_real_heater_init_response(self):
        parsed = probe.validate_init_response(REAL_INIT)
        self.assertTrue(parsed["crc_valid"])
        self.assertEqual(parsed["payload"], bytes.fromhex("12 8A 00 3D D6"))

    def test_rejects_crc_error_and_non_init(self):
        damaged = bytearray(REAL_INIT)
        damaged[-1] ^= 1
        with self.assertRaisesRegex(RuntimeError, "CRC"):
            probe.validate_init_response(damaged)
        with self.assertRaisesRegex(RuntimeError, "not INIT"):
            probe.validate_init_response(bytes.fromhex(
                "AA 04 13 00 0F 00 00 00 00 00 00 00 00 00 00 00 00 00 "
                "00 00 00 00 00 00 6D A0"
            ))

    def test_wait_rejects_noise_then_accepts_one_init(self):
        clock = FakeClock()
        transport = FakeTransport([[b"bad"], [], [REAL_INIT]])
        parsed, rejected = probe._wait_for_init_response(
            transport,
            20,
            clock.ticks_ms,
            clock.ticks_diff,
            clock.sleep_ms,
        )
        self.assertEqual(parsed["raw"], REAL_INIT)
        self.assertEqual(rejected, 1)

    def test_source_has_one_init_write_and_no_dangerous_command_surface(self):
        source = inspect.getsource(probe)
        self.assertEqual(source.count("transport.send_frame(EXPECTED_REQUEST)"), 1)
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
