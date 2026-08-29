import unittest
from unittest import mock

import board_config


class TestDFR0654BoardConfig(unittest.TestCase):
    def test_confirmed_board_identity_and_micropython_target(self):
        self.assertEqual(board_config.BOARD_VENDOR, "DFRobot")
        self.assertEqual(board_config.BOARD_SKU, "DFR0654")
        self.assertEqual(board_config.BOARD_HARDWARE_REVISION, "1.0")
        self.assertEqual(board_config.BOARD_MODULE, "ESP32-WROOM-32E")
        self.assertEqual(board_config.MICROPYTHON_TARGET, "ESP32_GENERIC")
        self.assertEqual(board_config.MICROPYTHON_VERSION, "1.28.0")

    def test_official_uart2_profile_is_selected(self):
        self.assertEqual(
            (
                board_config.UART_ID,
                board_config.UART_TX_PIN,
                board_config.UART_RX_PIN,
            ),
            (2, 17, 16),
        )
        self.assertEqual(board_config.missing_uart_pin_assignments(), [])
        self.assertIsNone(board_config.require_uart_configuration())
        self.assertEqual(
            (
                board_config.UART_BAUDRATE,
                board_config.UART_BITS,
                board_config.UART_PARITY,
                board_config.UART_STOP_BITS,
            ),
            (9600, 8, None, 1),
        )

    def test_invalid_serial_profile_and_nonboolean_tx_flag_fail_closed(self):
        with mock.patch.object(board_config, "UART_BAUDRATE", 115200):
            with self.assertRaises(RuntimeError):
                board_config.require_uart_configuration()
        with mock.patch.object(board_config, "UART_PROTOCOL_TX_ENABLED", 0):
            with self.assertRaises(RuntimeError):
                board_config.require_uart_configuration()
        with mock.patch.object(board_config, "UART_INVERT", 1):
            with self.assertRaises(RuntimeError):
                board_config.require_uart_configuration()

    def test_uart0_usb_boot_and_strapping_pins_are_not_used(self):
        assigned = {board_config.UART_TX_PIN, board_config.UART_RX_PIN}
        self.assertTrue(assigned.isdisjoint({0, 1, 2, 3, 5, 12, 15}))
        self.assertNotEqual(board_config.UART_TX_PIN, board_config.UART_RX_PIN)

    def test_autoterm_protocol_transmission_remains_disabled(self):
        self.assertIs(board_config.UART_PROTOCOL_TX_ENABLED, False)

    def test_rx_only_capture_limits_are_bounded(self):
        self.assertEqual(board_config.UART_RX_ONLY_BUFFER_SIZE, 2048)
        self.assertEqual(board_config.UART_RX_ONLY_MAX_READ_BYTES, 128)
        self.assertEqual(board_config.UART_RX_ONLY_QUEUE_CAPACITY, 64)
        self.assertEqual(board_config.UART_RX_CAPTURE_MAX_DURATION_MS, 120000)
        self.assertGreaterEqual(
            board_config.UART_RX_ONLY_BUFFER_SIZE,
            board_config.UART_RX_ONLY_MAX_READ_BYTES,
        )

    def test_sensor_and_rtc_integration_stays_locked(self):
        self.assertIsNone(board_config.ONEWIRE_PIN)
        self.assertIs(board_config.ONEWIRE_PIN_APPROVED, False)
        self.assertIsNone(board_config.I2C_SDA_PIN)
        self.assertIsNone(board_config.I2C_SCL_PIN)
        self.assertIs(board_config.I2C_PINS_APPROVED, False)
        with self.assertRaises(RuntimeError):
            board_config.require_onewire_configuration()
        with self.assertRaises(RuntimeError):
            board_config.require_i2c_configuration()
        with self.assertRaises(RuntimeError):
            board_config.require_hardware_configuration()

    def test_onewire_profile_is_bounded_but_not_yet_authorized(self):
        self.assertEqual(board_config.ONEWIRE_CONVERSION_WAIT_MS, 750)
        self.assertEqual(board_config.ONEWIRE_POLL_INTERVAL_MS, 1000)
        self.assertEqual(board_config.ONEWIRE_DISCOVERY_INTERVAL_MS, 30000)
        self.assertEqual(board_config.ONEWIRE_MAX_SENSORS, 16)

        with mock.patch.object(board_config, "ONEWIRE_PIN", 18):
            with self.assertRaises(RuntimeError):
                board_config.require_onewire_configuration()

        with mock.patch.object(board_config, "ONEWIRE_PIN", 18), mock.patch.object(
            board_config, "ONEWIRE_PIN_APPROVED", True
        ):
            self.assertIsNone(board_config.require_onewire_configuration())

    def test_onewire_validator_rejects_conflicts_and_bad_timing(self):
        for pin in (0, 1, 6, 16, 17, 20, 24, 28, 31, 34, True, -1, 40):
            with self.subTest(pin=pin), mock.patch.object(
                board_config, "ONEWIRE_PIN", pin
            ), mock.patch.object(board_config, "ONEWIRE_PIN_APPROVED", True):
                with self.assertRaises(RuntimeError):
                    board_config.require_onewire_configuration()

        with mock.patch.object(board_config, "ONEWIRE_PIN", 18), mock.patch.object(
            board_config, "ONEWIRE_PIN_APPROVED", True
        ), mock.patch.object(board_config, "ONEWIRE_CONVERSION_WAIT_MS", 749):
            with self.assertRaises(RuntimeError):
                board_config.require_onewire_configuration()

    def test_complete_hardware_guard_also_requires_onewire_approval(self):
        patches = (
            mock.patch.object(board_config, "ONEWIRE_PIN", 18),
            mock.patch.object(board_config, "I2C_SDA_PIN", 21),
            mock.patch.object(board_config, "I2C_SCL_PIN", 22),
        )
        with patches[0], patches[1], patches[2]:
            with self.assertRaises(RuntimeError):
                board_config.require_hardware_configuration()
            with mock.patch.object(
                board_config, "ONEWIRE_PIN_APPROVED", True
            ), mock.patch.object(
                board_config, "I2C_PINS_APPROVED", True
            ), mock.patch.object(
                board_config, "WIFI_RADIO_APPROVED", True
            ):
                self.assertIsNone(
                    board_config.require_hardware_configuration()
                )

    def test_wifi_profile_is_strict_and_explicitly_locked(self):
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
        self.assertEqual(board_config.WIFI_COUNTRY_CODE, "CH")
        self.assertEqual(board_config.WIFI_AP_MAX_CLIENTS, 4)
        self.assertEqual(board_config.WIFI_STA_RECONNECTS, 0)
        with self.assertRaisesRegex(RuntimeError, "not been explicitly approved"):
            board_config.require_wifi_configuration()
        with mock.patch.object(board_config, "WIFI_RADIO_APPROVED", True):
            self.assertIsNone(board_config.require_wifi_configuration())
        for name, value in (
            ("WIFI_RADIO_APPROVED", 1),
            ("WIFI_COUNTRY_CODE", "ch"),
            ("WIFI_COUNTRY_CODE", "CHE"),
            ("WIFI_AP_MAX_CLIENTS", True),
            ("WIFI_AP_MAX_CLIENTS", 0),
            ("WIFI_AP_MAX_CLIENTS", 5),
            ("WIFI_STA_RECONNECTS", -1),
            ("WIFI_STA_RECONNECTS", False),
        ):
            with self.subTest(name=name, value=value), mock.patch.object(
                board_config, "WIFI_RADIO_APPROVED", True
            ), mock.patch.object(board_config, name, value):
                with self.assertRaises(RuntimeError):
                    board_config.require_wifi_configuration()

    def test_i2c_profile_is_strict_and_independently_locked(self):
        self.assertEqual(board_config.I2C_ID, 0)
        self.assertEqual(board_config.I2C_FREQUENCY_HZ, 100000)
        self.assertEqual(board_config.I2C_TIMEOUT_US, 50000)
        self.assertEqual(board_config.DS3231_I2C_ADDRESS, 0x68)

        approved = (
            mock.patch.object(board_config, "I2C_SDA_PIN", 21),
            mock.patch.object(board_config, "I2C_SCL_PIN", 22),
            mock.patch.object(board_config, "I2C_PINS_APPROVED", True),
        )
        with approved[0], approved[1], approved[2]:
            self.assertIsNone(board_config.require_i2c_configuration())

        for name, value in (
            ("I2C_ID", True),
            ("I2C_ID", False),
            ("I2C_SDA_PIN", True),
            ("I2C_SDA_PIN", 17),
            ("I2C_SDA_PIN", 34),
            ("I2C_FREQUENCY_HZ", 400000),
            ("I2C_TIMEOUT_US", 0),
            ("I2C_TIMEOUT_US", 40000),
            ("DS3231_I2C_ADDRESS", 0x69),
        ):
            with self.subTest(name=name, value=value), mock.patch.object(
                board_config, "I2C_SDA_PIN", 21
            ), mock.patch.object(
                board_config, "I2C_SCL_PIN", 22
            ), mock.patch.object(
                board_config, "I2C_PINS_APPROVED", True
            ), mock.patch.object(board_config, name, value):
                with self.assertRaises(RuntimeError):
                    board_config.require_i2c_configuration()

        with mock.patch.object(
            board_config, "I2C_SDA_PIN", 21
        ), mock.patch.object(
            board_config, "I2C_SCL_PIN", 21
        ), mock.patch.object(board_config, "I2C_PINS_APPROVED", True):
            with self.assertRaises(RuntimeError):
                board_config.require_i2c_configuration()


if __name__ == "__main__":
    unittest.main()
