"""Single location for board-specific hardware configuration.

The board was visually confirmed as DFRobot DFR0654 by its exposed D11/IO16
pin.  Only the UART-only bring-up pins are assigned.  Sensor and RTC pins stay
unassigned until their own integration phase.
"""

BOARD_VENDOR = "DFRobot"
BOARD_MODEL = "FireBeetle 2 ESP32-E"
BOARD_SKU = "DFR0654"
BOARD_HARDWARE_REVISION = "1.0"
BOARD_MODULE = "ESP32-WROOM-32E"
MICROPYTHON_TARGET = "ESP32_GENERIC"
MICROPYTHON_VERSION = "1.28.0"

# DFRobot documents UART2 on DFR0654 as TX=IO17/D10 and RX=IO16/D11.
# UART0 on IO1/IO3 remains reserved for USB flashing and the REPL.
UART_ID = 2
UART_TX_PIN = 17
UART_RX_PIN = 16
UART_BAUDRATE = 9600
UART_BITS = 8
UART_PARITY = None
UART_STOP_BITS = 1
# Protocol transmission stays blocked in the regular transport during USB and
# loopback bring-up.  The explicitly armed board-only loopback tool is separate.
UART_PROTOCOL_TX_ENABLED = False
UART_INTER_BYTE_TIMEOUT_MS = 200
# Reserved for the Phase-3 HeaterController.  The UART transport deliberately
# performs no request correlation, automatic retry or response supervision.
UART_RESPONSE_TIMEOUT_MS = 10000
UART_RX_BUFFER_SIZE = 512
UART_MAX_READ_BYTES = 512
UART_ACTIVITY_QUEUE_CAPACITY = 32
UART_MAX_EMPTY_READY_READS = 3
UART_DRIVER_TIMEOUT_MS = 0
UART_DRIVER_TIMEOUT_CHAR_MS = 0
UART_INVERT = 0

# Dedicated passive-capture profile.  This path never exposes UART write APIs.
# MicroPython's ESP32 UART2 driver still maps TX to GPIO17 while constructing
# the UART, so the RX-only factory immediately returns that pin to input mode.
# The primary safety barrier remains physical: GPIO17/D10 must not be wired.
UART_RX_ONLY_BUFFER_SIZE = 2048
UART_RX_ONLY_MAX_READ_BYTES = 128
UART_RX_ONLY_QUEUE_CAPACITY = 64
UART_RX_ONLY_MAX_EMPTY_READY_READS = 3
UART_RX_CAPTURE_MAX_DURATION_MS = 120000

# The 1-Wire adapter core is hardware independent.  A real pin remains locked
# until the board route, external 4.7-kOhm pull-up to 3.3 V and three-wire
# sensor supply have been checked as a separate hardware milestone.
ONEWIRE_PIN = None
ONEWIRE_PIN_APPROVED = False
ONEWIRE_CONVERSION_WAIT_MS = 750
ONEWIRE_POLL_INTERVAL_MS = 1000
ONEWIRE_DISCOVERY_INTERVAL_MS = 30000
ONEWIRE_MAX_SENSORS = 16

I2C_ID = 0
I2C_SDA_PIN = None
I2C_SCL_PIN = None
I2C_PINS_APPROVED = False
I2C_FREQUENCY_HZ = 100000
I2C_TIMEOUT_US = 50000
DS3231_I2C_ADDRESS = 0x68
DS3231_REFRESH_INTERVAL_MS = 60000
DS3231_RETRY_INTERVAL_MS = 5000

# The WLAN adapter is software-complete in Phase 7, but the regular boot path
# remains passive.  Only an explicitly confirmed board smoke may temporarily
# patch this lock in RAM; production activation follows later composition.
WIFI_RADIO_APPROVED = False
WIFI_COUNTRY_CODE = "CH"
WIFI_AP_MAX_CLIENTS = 4
WIFI_STA_RECONNECTS = 0


_EXPECTED_BOARD_SKU = "DFR0654"
_EXPECTED_UART_PROFILE = (2, 17, 16)
_EXPECTED_UART_SERIAL = (9600, 8, None, 1)
_USB_UART0_PINS = (1, 3)
_ESP32_FLASH_PINS = (6, 7, 8, 9, 10, 11)
_ESP32_STRAPPING_PINS = (0, 2, 5, 12, 15)
_ESP32_INPUT_ONLY_PINS = (34, 35, 36, 37, 38, 39)
_ESP32_WROOM_GPIO_PINS = (
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17,
    18, 19, 21, 22, 23, 25, 26, 27, 32, 33, 34, 35, 36, 37, 38, 39,
)


def _missing(names_and_values):
    return [name for name, value in names_and_values if value is None]


def missing_uart_pin_assignments():
    """Return only missing pins needed for UART-only bring-up."""

    return _missing((
        ("UART_TX_PIN", UART_TX_PIN),
        ("UART_RX_PIN", UART_RX_PIN),
    ))


def missing_pin_assignments():
    """Return all pins needed by the eventual complete hardware stack."""

    return _missing((
        ("UART_TX_PIN", UART_TX_PIN),
        ("UART_RX_PIN", UART_RX_PIN),
        ("ONEWIRE_PIN", ONEWIRE_PIN),
        ("I2C_SDA_PIN", I2C_SDA_PIN),
        ("I2C_SCL_PIN", I2C_SCL_PIN),
    ))


def require_uart_configuration():
    """Fail safely unless the confirmed DFR0654 UART profile is intact."""

    missing = missing_uart_pin_assignments()
    if missing:
        raise RuntimeError(
            "UART pins are not configured: {}".format(", ".join(missing))
        )

    if BOARD_SKU != _EXPECTED_BOARD_SKU:
        raise RuntimeError(
            "Unsupported or unconfirmed board SKU: {}".format(BOARD_SKU)
        )

    profile = (UART_ID, UART_TX_PIN, UART_RX_PIN)
    if profile != _EXPECTED_UART_PROFILE:
        raise RuntimeError(
            "DFR0654 UART profile must be UART2 TX=17 RX=16"
        )

    serial_profile = (UART_BAUDRATE, UART_BITS, UART_PARITY, UART_STOP_BITS)
    if serial_profile != _EXPECTED_UART_SERIAL:
        raise RuntimeError("Autoterm UART profile must be 9600/8N1")

    if not isinstance(UART_PROTOCOL_TX_ENABLED, bool):
        raise RuntimeError("UART_PROTOCOL_TX_ENABLED must be boolean")
    if UART_INVERT != 0:
        raise RuntimeError("Autoterm UART signals must not be inverted")

    if UART_TX_PIN == UART_RX_PIN:
        raise RuntimeError("UART TX and RX pins must be different")
    if UART_TX_PIN in _USB_UART0_PINS or UART_RX_PIN in _USB_UART0_PINS:
        raise RuntimeError("UART0 USB/REPL pins IO1 and IO3 are reserved")


def require_onewire_configuration():
    """Fail safely until one dedicated DFR0654 1-Wire pin is approved."""

    if ONEWIRE_PIN is None:
        raise RuntimeError("ONEWIRE_PIN is not configured")
    if ONEWIRE_PIN_APPROVED is not True:
        raise RuntimeError("ONEWIRE_PIN has not been electrically approved")
    if BOARD_SKU != _EXPECTED_BOARD_SKU:
        raise RuntimeError(
            "Unsupported or unconfirmed board SKU: {}".format(BOARD_SKU)
        )
    if not isinstance(ONEWIRE_PIN, int) or isinstance(ONEWIRE_PIN, bool):
        raise RuntimeError("ONEWIRE_PIN must be an integer GPIO number")
    if ONEWIRE_PIN not in _ESP32_WROOM_GPIO_PINS:
        raise RuntimeError("ONEWIRE_PIN is not an ESP32-WROOM GPIO")

    if ONEWIRE_PIN in _USB_UART0_PINS:
        raise RuntimeError("UART0 USB/REPL pins IO1 and IO3 are reserved")
    if ONEWIRE_PIN in _ESP32_FLASH_PINS:
        raise RuntimeError("ESP32 flash pins IO6 through IO11 are reserved")
    if ONEWIRE_PIN in _ESP32_STRAPPING_PINS:
        raise RuntimeError("ESP32 boot-strapping pins are not approved")
    if ONEWIRE_PIN in _ESP32_INPUT_ONLY_PINS:
        raise RuntimeError("1-Wire requires an output-capable GPIO")
    if ONEWIRE_PIN in (UART_TX_PIN, UART_RX_PIN):
        raise RuntimeError("1-Wire must not share the Autoterm UART pins")
    if ONEWIRE_PIN in (I2C_SDA_PIN, I2C_SCL_PIN):
        raise RuntimeError("1-Wire must not share the RTC I2C pins")

    timing_values = (
        ("ONEWIRE_CONVERSION_WAIT_MS", ONEWIRE_CONVERSION_WAIT_MS),
        ("ONEWIRE_POLL_INTERVAL_MS", ONEWIRE_POLL_INTERVAL_MS),
        ("ONEWIRE_DISCOVERY_INTERVAL_MS", ONEWIRE_DISCOVERY_INTERVAL_MS),
        ("ONEWIRE_MAX_SENSORS", ONEWIRE_MAX_SENSORS),
    )
    for name, value in timing_values:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise RuntimeError("{} must be a positive integer".format(name))
    if ONEWIRE_CONVERSION_WAIT_MS < 750:
        raise RuntimeError("DS18B20 conversion wait must be at least 750 ms")
    if ONEWIRE_POLL_INTERVAL_MS < ONEWIRE_CONVERSION_WAIT_MS:
        raise RuntimeError("1-Wire poll interval is shorter than conversion")
    if ONEWIRE_DISCOVERY_INTERVAL_MS < ONEWIRE_POLL_INTERVAL_MS:
        raise RuntimeError("1-Wire discovery interval is too short")


def require_i2c_configuration():
    """Fail safely until dedicated DFR0654 RTC pins are approved."""

    missing = _missing((
        ("I2C_SDA_PIN", I2C_SDA_PIN),
        ("I2C_SCL_PIN", I2C_SCL_PIN),
    ))
    if missing:
        raise RuntimeError(
            "I2C pins are not configured: {}".format(", ".join(missing))
        )
    if I2C_PINS_APPROVED is not True:
        raise RuntimeError("I2C pins have not been electrically approved")
    if BOARD_SKU != _EXPECTED_BOARD_SKU:
        raise RuntimeError(
            "Unsupported or unconfirmed board SKU: {}".format(BOARD_SKU)
        )
    if (
        not isinstance(I2C_ID, int)
        or isinstance(I2C_ID, bool)
        or I2C_ID != 0
    ):
        raise RuntimeError("DFR0654 RTC profile must use I2C0")

    for name, pin in (
        ("I2C_SDA_PIN", I2C_SDA_PIN),
        ("I2C_SCL_PIN", I2C_SCL_PIN),
    ):
        if not isinstance(pin, int) or isinstance(pin, bool):
            raise RuntimeError("{} must be an integer GPIO".format(name))
        if pin not in _ESP32_WROOM_GPIO_PINS:
            raise RuntimeError("{} is not an ESP32-WROOM GPIO".format(name))
        if pin in _USB_UART0_PINS:
            raise RuntimeError("UART0 USB/REPL pins IO1 and IO3 are reserved")
        if pin in _ESP32_FLASH_PINS:
            raise RuntimeError("ESP32 flash pins IO6 through IO11 are reserved")
        if pin in _ESP32_STRAPPING_PINS:
            raise RuntimeError("ESP32 boot-strapping pins are not approved")
        if pin in _ESP32_INPUT_ONLY_PINS:
            raise RuntimeError("I2C requires output-capable GPIOs")
        if pin in (UART_TX_PIN, UART_RX_PIN):
            raise RuntimeError("RTC I2C must not share the Autoterm UART pins")
        if pin == ONEWIRE_PIN:
            raise RuntimeError("RTC I2C must not share the 1-Wire pin")
    if I2C_SDA_PIN == I2C_SCL_PIN:
        raise RuntimeError("I2C SDA and SCL pins must be different")

    integer_settings = (
        ("I2C_FREQUENCY_HZ", I2C_FREQUENCY_HZ),
        ("I2C_TIMEOUT_US", I2C_TIMEOUT_US),
        ("DS3231_I2C_ADDRESS", DS3231_I2C_ADDRESS),
        ("DS3231_REFRESH_INTERVAL_MS", DS3231_REFRESH_INTERVAL_MS),
        ("DS3231_RETRY_INTERVAL_MS", DS3231_RETRY_INTERVAL_MS),
    )
    for name, value in integer_settings:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise RuntimeError("{} must be a positive integer".format(name))
    if I2C_FREQUENCY_HZ != 100000:
        raise RuntimeError("DS3231 I2C frequency must be 100000 Hz")
    if I2C_TIMEOUT_US != 50000:
        raise RuntimeError("DS3231 I2C timeout must be 50000 us")
    if DS3231_I2C_ADDRESS != 0x68:
        raise RuntimeError("DS3231 I2C address must be 0x68")
    if DS3231_RETRY_INTERVAL_MS >= DS3231_REFRESH_INTERVAL_MS:
        raise RuntimeError("DS3231 retry interval must be shorter than refresh")


def require_wifi_configuration():
    """Fail safely until an explicit Phase-7 WLAN owner is approved."""

    if WIFI_RADIO_APPROVED is not True:
        raise RuntimeError("Wi-Fi radio has not been explicitly approved")
    if BOARD_SKU != _EXPECTED_BOARD_SKU:
        raise RuntimeError(
            "Unsupported or unconfirmed board SKU: {}".format(BOARD_SKU)
        )
    if (
        type(WIFI_COUNTRY_CODE) is not str
        or len(WIFI_COUNTRY_CODE) != 2
        or any(
            character < "A" or character > "Z"
            for character in WIFI_COUNTRY_CODE
        )
    ):
        raise RuntimeError("WIFI_COUNTRY_CODE must be two uppercase letters")
    if (
        type(WIFI_AP_MAX_CLIENTS) is not int
        or WIFI_AP_MAX_CLIENTS < 1
        or WIFI_AP_MAX_CLIENTS > 4
    ):
        raise RuntimeError("WIFI_AP_MAX_CLIENTS must be between 1 and 4")
    if type(WIFI_STA_RECONNECTS) is not int or WIFI_STA_RECONNECTS != 0:
        raise RuntimeError("Wi-Fi driver reconnects must be disabled")


def require_hardware_configuration():
    """Fail safely when a hardware phase is started without assigned pins."""

    missing = missing_pin_assignments()
    if missing:
        raise RuntimeError(
            "Hardware pins are not configured: {}".format(", ".join(missing))
        )
    require_uart_configuration()
    require_onewire_configuration()
    require_i2c_configuration()
    require_wifi_configuration()
