import inspect
import unittest

from tools import dfr0975u_uart_rx_capture as capture


class TestDFR0975UUARTRXCapture(unittest.TestCase):
    def test_wrong_confirmation_stops_before_target_imports(self):
        with self.assertRaisesRegex(RuntimeError, "exact confirmation"):
            capture.run("wrong")

    def test_unpowered_level_shifter_has_separate_confirmation_and_result(self):
        self.assertNotEqual(
            capture.CONFIRMATION,
            capture.UNPOWERED_LEVEL_SHIFTER_CONFIRMATION,
        )
        self.assertNotEqual(
            capture.PASS_TOKEN,
            capture.UNPOWERED_LEVEL_SHIFTER_PASS_TOKEN,
        )
        self.assertIn("HEATER_OFF", capture.UNPOWERED_LEVEL_SHIFTER_CONFIRMATION)
        self.assertIn("TX14_GATE12_DISCONNECTED",
                      capture.UNPOWERED_LEVEL_SHIFTER_CONFIRMATION)

    def test_powered_idle_has_separate_confirmation_and_result(self):
        self.assertNotEqual(
            capture.POWERED_IDLE_CONFIRMATION,
            capture.UNPOWERED_LEVEL_SHIFTER_CONFIRMATION,
        )
        self.assertNotEqual(
            capture.POWERED_IDLE_PASS_TOKEN,
            capture.UNPOWERED_LEVEL_SHIFTER_PASS_TOKEN,
        )
        self.assertIn("HEATER_12V_IDLE", capture.POWERED_IDLE_CONFIRMATION)
        self.assertIn("TX14_GATE12_DISCONNECTED",
                      capture.POWERED_IDLE_CONFIRMATION)

    def test_runner_is_bounded_inert_and_has_no_send_surface(self):
        source = inspect.getsource(capture)
        self.assertEqual(capture.DEFAULT_DURATION_MS, 2000)
        self.assertIn("UART_PROTOCOL_TX_ENABLED", source)
        self.assertIn("UART_TX_GATE_APPROVED", source)
        self.assertIn("UART_PINS_APPROVED", source)
        self.assertIn("Pin(board_config.UART_TX_PIN, Pin.IN", source)
        self.assertIn("Pin(board_config.UART_TX_GATE_PIN, Pin.IN", source)
        self.assertNotIn(".write(", source)
        self.assertNotIn("write_flash", source)
        self.assertNotIn("erase_flash", source)
        self.assertNotIn("open(", source)


if __name__ == "__main__":
    unittest.main()
