import contextlib
import unittest
from unittest import mock

import board_config


@contextlib.contextmanager
def _historical_dfr0654_profile():
    values = {
        "BOARD_VENDOR": "DFRobot",
        "BOARD_MODEL": "FireBeetle 2 ESP32-E",
        "BOARD_SKU": "DFR0654",
        "BOARD_HARDWARE_REVISION": "1.0",
        "BOARD_MODULE": "ESP32-WROOM-32E",
        "MICROPYTHON_TARGET": "ESP32_GENERIC",
        "MICROPYTHON_VARIANT": None,
        "MICROPYTHON_BUILD_BOARD": "ESP32_GENERIC",
        "MICROPYTHON_VERSION": "1.28.0",
        "UART_ID": 2,
        "UART_TX_PIN": 17,
        "UART_RX_PIN": 16,
        "UART_PINS_APPROVED": True,
    }
    with contextlib.ExitStack() as stack:
        for name, value in values.items():
            stack.enter_context(mock.patch.object(board_config, name, value))
        yield


class TestDFR0975UBoardConfig(unittest.TestCase):
    def test_confirmed_identity_and_firmware_profile(self):
        self.assertEqual(board_config.BOARD_VENDOR, "DFRobot")
        self.assertEqual(board_config.BOARD_MODEL, "FireBeetle 2 ESP32-S3-U")
        self.assertEqual(board_config.BOARD_SKU, "DFR0975-U")
        self.assertEqual(board_config.BOARD_HARDWARE_REVISION, "1.0")
        self.assertEqual(
            board_config.BOARD_MODULE, "ESP32-S3-WROOM-1U-N16R8"
        )
        self.assertEqual(board_config.MICROPYTHON_TARGET, "ESP32_GENERIC_S3")
        self.assertEqual(board_config.MICROPYTHON_VARIANT, "SPIRAM_OCT")
        self.assertEqual(
            board_config.MICROPYTHON_BUILD_BOARD, "DFR0975U_N16R8"
        )
        self.assertEqual(board_config.MICROPYTHON_VERSION, "1.28.0")
        self.assertEqual(
            board_config._active_profile(), board_config._PROFILE_DFR0975U
        )

    def test_planned_routes_are_complete_but_every_lock_is_closed(self):
        self.assertEqual(
            (
                board_config.UART_ID,
                board_config.UART_TX_PIN,
                board_config.UART_RX_PIN,
            ),
            (2, 14, 13),
        )
        self.assertEqual(
            (
                board_config.UART_TX_GATE_PIN,
                board_config.UART_TX_GATE_ACTIVE_LEVEL,
            ),
            (12, 1),
        )
        self.assertEqual(
            (
                board_config.I2C_ID,
                board_config.I2C_SDA_PIN,
                board_config.I2C_SCL_PIN,
            ),
            (1, 10, 11),
        )
        self.assertEqual(board_config.ONEWIRE_PIN, 4)
        self.assertEqual(board_config.missing_uart_pin_assignments(), [])
        self.assertEqual(board_config.missing_pin_assignments(), [])
        for name in (
            "UART_PINS_APPROVED",
            "UART_PROTOCOL_TX_ENABLED",
            "UART_TX_GATE_APPROVED",
            "I2C_PINS_APPROVED",
            "ONEWIRE_PIN_APPROVED",
            "WIFI_RADIO_APPROVED",
        ):
            self.assertIs(getattr(board_config, name), False, name)

    def test_uart_route_requires_approval_and_retains_9600_8n1(self):
        with self.assertRaisesRegex(RuntimeError, "not been electrically"):
            board_config.require_uart_configuration()
        with mock.patch.object(board_config, "UART_PINS_APPROVED", True):
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

    def test_tx_enable_requires_approved_active_high_hardware_gate(self):
        patches = (
            mock.patch.object(board_config, "UART_PINS_APPROVED", True),
            mock.patch.object(board_config, "UART_PROTOCOL_TX_ENABLED", True),
        )
        with patches[0], patches[1]:
            with self.assertRaisesRegex(RuntimeError, "hardware gate"):
                board_config.require_uart_configuration()
            with mock.patch.object(
                board_config, "UART_TX_GATE_APPROVED", True
            ):
                self.assertIsNone(board_config.require_uart_configuration())

        with mock.patch.object(
            board_config, "UART_PINS_APPROVED", True
        ), mock.patch.object(board_config, "UART_TX_GATE_ACTIVE_LEVEL", 0):
            with self.assertRaisesRegex(RuntimeError, "active-high"):
                board_config.require_uart_configuration()
        with mock.patch.object(
            board_config, "UART_PINS_APPROVED", True
        ), mock.patch.object(board_config, "UART_TX_GATE_ACTIVE_LEVEL", True):
            with self.assertRaisesRegex(RuntimeError, "must be 0 or 1"):
                board_config.require_uart_configuration()

    def test_invalid_uart_profile_and_flag_types_fail_closed(self):
        for name, value in (
            ("UART_ID", 1),
            ("UART_TX_PIN", 13),
            ("UART_RX_PIN", 14),
            ("UART_BAUDRATE", 115200),
            ("UART_PINS_APPROVED", 1),
            ("UART_PROTOCOL_TX_ENABLED", 0),
            ("UART_TX_GATE_APPROVED", 0),
            ("UART_INVERT", 1),
        ):
            with self.subTest(name=name, value=value), mock.patch.object(
                board_config, "UART_PINS_APPROVED", True
            ), mock.patch.object(board_config, name, value):
                with self.assertRaises(RuntimeError):
                    board_config.require_uart_configuration()

    def test_s3_hard_denylist_and_allocated_pins_are_disjoint(self):
        allocated = {
            board_config.UART_TX_PIN,
            board_config.UART_RX_PIN,
            board_config.UART_TX_GATE_PIN,
            board_config.I2C_SDA_PIN,
            board_config.I2C_SCL_PIN,
            board_config.ONEWIRE_PIN,
        }
        self.assertEqual(len(allocated), 6)
        self.assertTrue(
            allocated.isdisjoint(board_config._DFR0975U_HARD_DENY_PINS)
        )
        for pin in board_config._DFR0975U_HARD_DENY_PINS:
            with self.subTest(pin=pin), self.assertRaises(RuntimeError):
                board_config._require_product_pin(
                    board_config._PROFILE_DFR0975U, "TEST_PIN", pin
                )
        for pin in allocated:
            self.assertIsNone(
                board_config._require_product_pin(
                    board_config._PROFILE_DFR0975U, "TEST_PIN", pin
                )
            )

    def test_cross_function_pin_conflicts_fail_closed(self):
        with mock.patch.object(
            board_config, "UART_PINS_APPROVED", True
        ), mock.patch.object(
            board_config, "UART_TX_GATE_PIN", board_config.UART_TX_PIN
        ):
            with self.assertRaises(RuntimeError):
                board_config.require_uart_configuration()

        with mock.patch.object(
            board_config, "ONEWIRE_PIN_APPROVED", True
        ), mock.patch.object(
            board_config, "ONEWIRE_PIN", board_config.I2C_SDA_PIN
        ):
            with self.assertRaises(RuntimeError):
                board_config.require_onewire_configuration()

        with mock.patch.object(
            board_config, "I2C_PINS_APPROVED", True
        ), mock.patch.object(
            board_config, "I2C_SDA_PIN", board_config.UART_TX_GATE_PIN
        ):
            with self.assertRaises(RuntimeError):
                board_config.require_i2c_configuration()

    def test_onewire_profile_is_bounded_and_independently_locked(self):
        with self.assertRaisesRegex(RuntimeError, "not been electrically"):
            board_config.require_onewire_configuration()
        with mock.patch.object(board_config, "ONEWIRE_PIN_APPROVED", True):
            self.assertIsNone(board_config.require_onewire_configuration())
        self.assertEqual(board_config.ONEWIRE_CONVERSION_WAIT_MS, 750)
        self.assertEqual(board_config.ONEWIRE_POLL_INTERVAL_MS, 1000)
        self.assertEqual(board_config.ONEWIRE_DISCOVERY_INTERVAL_MS, 30000)
        self.assertEqual(board_config.ONEWIRE_MAX_SENSORS, 16)

        with mock.patch.object(
            board_config, "ONEWIRE_PIN_APPROVED", True
        ), mock.patch.object(board_config, "ONEWIRE_CONVERSION_WAIT_MS", 749):
            with self.assertRaises(RuntimeError):
                board_config.require_onewire_configuration()

    def test_i2c1_profile_is_strict_and_independently_locked(self):
        with self.assertRaisesRegex(RuntimeError, "not been electrically"):
            board_config.require_i2c_configuration()
        with mock.patch.object(board_config, "I2C_PINS_APPROVED", True):
            self.assertIsNone(board_config.require_i2c_configuration())
        self.assertEqual(board_config.I2C_FREQUENCY_HZ, 100000)
        self.assertEqual(board_config.I2C_TIMEOUT_US, 50000)
        self.assertEqual(board_config.DS3231_I2C_ADDRESS, 0x68)

        for name, value in (
            ("I2C_ID", 0),
            ("I2C_ID", True),
            ("I2C_SDA_PIN", True),
            ("I2C_SDA_PIN", 1),
            ("I2C_SCL_PIN", 20),
            ("I2C_FREQUENCY_HZ", 400000),
            ("I2C_TIMEOUT_US", 40000),
            ("DS3231_I2C_ADDRESS", 0x69),
        ):
            with self.subTest(name=name, value=value), mock.patch.object(
                board_config, "I2C_PINS_APPROVED", True
            ), mock.patch.object(board_config, name, value):
                with self.assertRaises(RuntimeError):
                    board_config.require_i2c_configuration()

    def test_wifi_and_complete_hardware_guards_remain_closed(self):
        with self.assertRaisesRegex(RuntimeError, "not been explicitly"):
            board_config.require_wifi_configuration()
        with self.assertRaises(RuntimeError):
            board_config.require_hardware_configuration()

        approvals = (
            mock.patch.object(board_config, "UART_PINS_APPROVED", True),
            mock.patch.object(board_config, "UART_TX_GATE_APPROVED", True),
            mock.patch.object(board_config, "ONEWIRE_PIN_APPROVED", True),
            mock.patch.object(board_config, "I2C_PINS_APPROVED", True),
            mock.patch.object(board_config, "WIFI_RADIO_APPROVED", True),
        )
        with approvals[0], approvals[1], approvals[2], approvals[3], approvals[4]:
            self.assertIsNone(board_config.require_hardware_configuration())

        without_gate = (
            mock.patch.object(board_config, "UART_PINS_APPROVED", True),
            mock.patch.object(board_config, "ONEWIRE_PIN_APPROVED", True),
            mock.patch.object(board_config, "I2C_PINS_APPROVED", True),
            mock.patch.object(board_config, "WIFI_RADIO_APPROVED", True),
        )
        with without_gate[0], without_gate[1], without_gate[2], without_gate[3]:
            with self.assertRaisesRegex(RuntimeError, "approved UART TX gate"):
                board_config.require_hardware_configuration()

    def test_wifi_profile_remains_strict_after_s3_migration(self):
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

    def test_unknown_internal_profile_never_falls_back_to_dfr0654(self):
        with self.assertRaisesRegex(RuntimeError, "Unknown board profile"):
            board_config._require_product_pin("unknown", "TEST_PIN", 18)

    def test_unsupported_identity_combination_fails_closed(self):
        with mock.patch.object(
            board_config, "BOARD_MODULE", "ESP32-S3-WROOM-1-N16R8"
        ), mock.patch.object(board_config, "UART_PINS_APPROVED", True):
            with self.assertRaisesRegex(RuntimeError, "Unsupported"):
                board_config.require_uart_configuration()

    def test_historical_dfr0654_validation_branch_is_preserved(self):
        with _historical_dfr0654_profile():
            self.assertEqual(
                board_config._active_profile(), board_config._PROFILE_DFR0654
            )
            self.assertIsNone(board_config.require_uart_configuration())
            with mock.patch.object(
                board_config, "ONEWIRE_PIN", 18
            ), mock.patch.object(
                board_config, "ONEWIRE_PIN_APPROVED", True
            ), mock.patch.object(
                board_config, "I2C_ID", 0
            ), mock.patch.object(
                board_config, "I2C_SDA_PIN", 21
            ), mock.patch.object(
                board_config, "I2C_SCL_PIN", 22
            ), mock.patch.object(board_config, "I2C_PINS_APPROVED", True):
                self.assertIsNone(board_config.require_onewire_configuration())
                self.assertIsNone(board_config.require_i2c_configuration())


if __name__ == "__main__":
    unittest.main()
