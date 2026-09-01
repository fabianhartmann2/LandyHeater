"""Single location for board-specific hardware configuration.

The active profile is the physically confirmed DFRobot DFR0975-U V1.0 with
an ESP32-S3-WROOM-1U-N16R8 module. Pin assignments record intended routes,
but every hardware approval and protocol-transmit flag is delivered closed.

The historical DFR0654 identity remains an explicit validation profile. This
module uses no runtime profile imports, so a target cannot silently load pin
assumptions for a different MCU family.
"""

BOARD_VENDOR = "DFRobot"
BOARD_MODEL = "FireBeetle 2 ESP32-S3-U"
BOARD_SKU = "DFR0975-U"
BOARD_HARDWARE_REVISION = "1.0"
BOARD_MODULE = "ESP32-S3-WROOM-1U-N16R8"
MICROPYTHON_TARGET = "ESP32_GENERIC_S3"
MICROPYTHON_VARIANT = "SPIRAM_OCT"
MICROPYTHON_BUILD_BOARD = "DFR0975U_N16R8"
MICROPYTHON_VERSION = "1.28.0"

# UART2 is explicitly routed through the ESP32-S3 GPIO matrix. D10/D11 retain
# the FireBeetle header positions, but their S3 GPIO numbers are GPIO14/GPIO13.
UART_ID = 2
UART_TX_PIN = 14
UART_RX_PIN = 13
UART_PINS_APPROVED = False
UART_BAUDRATE = 9600
UART_BITS = 8
UART_PARITY = None
UART_STOP_BITS = 1
UART_PROTOCOL_TX_ENABLED = False
# Future TX must pass a tri-state/level interface whose active-high enable has
# an external pull-down. Software approval remains closed in this profile.
UART_TX_GATE_PIN = 12
UART_TX_GATE_ACTIVE_LEVEL = 1
UART_TX_GATE_APPROVED = False
UART_INTER_BYTE_TIMEOUT_MS = 200
UART_RESPONSE_TIMEOUT_MS = 10000
UART_RX_BUFFER_SIZE = 512
UART_MAX_READ_BYTES = 512
UART_ACTIVITY_QUEUE_CAPACITY = 32
UART_MAX_EMPTY_READY_READS = 3
UART_DRIVER_TIMEOUT_MS = 0
UART_DRIVER_TIMEOUT_CHAR_MS = 0
UART_INVERT = 0

# The existing passive-capture implementation remains DFR0654-only and will
# reject this active S3 profile. These limits are retained for that old path.
UART_RX_ONLY_BUFFER_SIZE = 2048
UART_RX_ONLY_MAX_READ_BYTES = 128
UART_RX_ONLY_QUEUE_CAPACITY = 64
UART_RX_ONLY_MAX_EMPTY_READY_READS = 3
UART_RX_CAPTURE_MAX_DURATION_MS = 120000

# A0/GPIO4 is reserved for the three-wire DS18B20 bus. The external 4.7-kOhm
# pull-up and physical route still require their own approval.
ONEWIRE_PIN = 4
ONEWIRE_PIN_APPROVED = False
ONEWIRE_CONVERSION_WAIT_MS = 750
ONEWIRE_POLL_INTERVAL_MS = 1000
ONEWIRE_DISCOVERY_INTERVAL_MS = 30000
ONEWIRE_MAX_SENSORS = 16

# I2C1 on A4/A5 is separate from the V1.0 board's AXP313A bus on GPIO1/2.
I2C_ID = 1
I2C_SDA_PIN = 10
I2C_SCL_PIN = 11
I2C_PINS_APPROVED = False
I2C_FREQUENCY_HZ = 100000
I2C_TIMEOUT_US = 50000
DS3231_I2C_ADDRESS = 0x68
DS3231_REFRESH_INTERVAL_MS = 60000
DS3231_RETRY_INTERVAL_MS = 5000

# The normal boot path remains passive. An explicitly confirmed radio smoke
# may temporarily patch this lock in RAM and must restore it during cleanup.
WIFI_RADIO_APPROVED = False
WIFI_COUNTRY_CODE = "CH"
WIFI_AP_MAX_CLIENTS = 4
WIFI_STA_RECONNECTS = 0


_PROFILE_DFR0654 = "dfr0654-v1.0"
_PROFILE_DFR0975U = "dfr0975-u-v1.0-n16r8"

_DFR0654_IDENTITY = (
    "DFRobot",
    "FireBeetle 2 ESP32-E",
    "DFR0654",
    "1.0",
    "ESP32-WROOM-32E",
    "ESP32_GENERIC",
    None,
    "ESP32_GENERIC",
    "1.28.0",
)
_DFR0975U_IDENTITY = (
    "DFRobot",
    "FireBeetle 2 ESP32-S3-U",
    "DFR0975-U",
    "1.0",
    "ESP32-S3-WROOM-1U-N16R8",
    "ESP32_GENERIC_S3",
    "SPIRAM_OCT",
    "DFR0975U_N16R8",
    "1.28.0",
)

_DFR0654_UART_PROFILE = (2, 17, 16)
_DFR0975U_UART_PROFILE = (2, 14, 13)
_EXPECTED_UART_SERIAL = (9600, 8, None, 1)

_DFR0654_USB_UART0_PINS = (1, 3)
_DFR0654_FLASH_PINS = (6, 7, 8, 9, 10, 11)
_DFR0654_STRAPPING_PINS = (0, 2, 5, 12, 15)
_DFR0654_INPUT_ONLY_PINS = (34, 35, 36, 37, 38, 39)
_DFR0654_GPIO_PINS = (
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17,
    18, 19, 21, 22, 23, 25, 26, 27, 32, 33, 34, 35, 36, 37, 38, 39,
)

# CAM and GDI must remain disconnected when these product GPIOs are used. The
# denylist also records straps, native USB, module memory, onboard functions,
# recovery UART and connector-only routes that must never be selected here.
_DFR0975U_PRODUCT_GPIO_PINS = (
    4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 38,
)
_DFR0975U_HARD_DENY_PINS = (
    0, 1, 2, 3,
    19, 20, 21, 22, 23, 24, 25,
    26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37,
    39, 40, 41, 42, 43, 44, 45, 46, 47, 48,
)


def _identity():
    return (
        BOARD_VENDOR,
        BOARD_MODEL,
        BOARD_SKU,
        BOARD_HARDWARE_REVISION,
        BOARD_MODULE,
        MICROPYTHON_TARGET,
        MICROPYTHON_VARIANT,
        MICROPYTHON_BUILD_BOARD,
        MICROPYTHON_VERSION,
    )


def _active_profile():
    identity = _identity()
    if identity == _DFR0975U_IDENTITY:
        return _PROFILE_DFR0975U
    if identity == _DFR0654_IDENTITY:
        return _PROFILE_DFR0654
    raise RuntimeError(
        "Unsupported or internally inconsistent board profile: {}".format(
            BOARD_SKU
        )
    )


def _missing(names_and_values):
    return [name for name, value in names_and_values if value is None]


def _require_boolean(name, value):
    if not isinstance(value, bool):
        raise RuntimeError("{} must be boolean".format(name))


def _require_integer_pin(name, pin):
    if not isinstance(pin, int) or isinstance(pin, bool):
        raise RuntimeError("{} must be an integer GPIO number".format(name))


def _require_product_pin(profile, name, pin, output_required=True):
    _require_integer_pin(name, pin)
    if profile == _PROFILE_DFR0975U:
        if pin in _DFR0975U_HARD_DENY_PINS:
            raise RuntimeError(
                "{} uses a DFR0975-U reserved GPIO".format(name)
            )
        if pin not in _DFR0975U_PRODUCT_GPIO_PINS:
            raise RuntimeError(
                "{} is not an approved DFR0975-U product GPIO".format(name)
            )
        return

    if profile != _PROFILE_DFR0654:
        raise RuntimeError("Unknown board profile for GPIO validation")

    if pin not in _DFR0654_GPIO_PINS:
        raise RuntimeError("{} is not an ESP32-WROOM GPIO".format(name))
    if pin in _DFR0654_USB_UART0_PINS:
        raise RuntimeError("UART0 USB/REPL pins IO1 and IO3 are reserved")
    if pin in _DFR0654_FLASH_PINS:
        raise RuntimeError("ESP32 flash pins IO6 through IO11 are reserved")
    if pin in _DFR0654_STRAPPING_PINS:
        raise RuntimeError("ESP32 boot-strapping pins are not approved")
    if output_required and pin in _DFR0654_INPUT_ONLY_PINS:
        raise RuntimeError("{} requires an output-capable GPIO".format(name))


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
        ("UART_TX_GATE_PIN", UART_TX_GATE_PIN),
        ("ONEWIRE_PIN", ONEWIRE_PIN),
        ("I2C_SDA_PIN", I2C_SDA_PIN),
        ("I2C_SCL_PIN", I2C_SCL_PIN),
    ))


def require_uart_configuration():
    """Fail safely unless the selected board UART route is approved."""

    missing = missing_uart_pin_assignments()
    if missing:
        raise RuntimeError(
            "UART pins are not configured: {}".format(", ".join(missing))
        )

    profile = _active_profile()
    _require_boolean("UART_PINS_APPROVED", UART_PINS_APPROVED)
    if UART_PINS_APPROVED is not True:
        raise RuntimeError("UART pins have not been electrically approved")

    expected = (
        _DFR0975U_UART_PROFILE
        if profile == _PROFILE_DFR0975U
        else _DFR0654_UART_PROFILE
    )
    actual = (UART_ID, UART_TX_PIN, UART_RX_PIN)
    if actual != expected:
        raise RuntimeError(
            "{} UART profile must be UART{} TX={} RX={}".format(
                BOARD_SKU, expected[0], expected[1], expected[2]
            )
        )

    serial_profile = (UART_BAUDRATE, UART_BITS, UART_PARITY, UART_STOP_BITS)
    if serial_profile != _EXPECTED_UART_SERIAL:
        raise RuntimeError("Autoterm UART profile must be 9600/8N1")

    _require_boolean("UART_PROTOCOL_TX_ENABLED", UART_PROTOCOL_TX_ENABLED)
    if UART_INVERT != 0:
        raise RuntimeError("Autoterm UART signals must not be inverted")
    if UART_TX_PIN == UART_RX_PIN:
        raise RuntimeError("UART TX and RX pins must be different")

    _require_product_pin(profile, "UART_TX_PIN", UART_TX_PIN)
    _require_product_pin(profile, "UART_RX_PIN", UART_RX_PIN, False)

    if profile == _PROFILE_DFR0975U:
        _require_product_pin(profile, "UART_TX_GATE_PIN", UART_TX_GATE_PIN)
        _require_boolean("UART_TX_GATE_APPROVED", UART_TX_GATE_APPROVED)
        if (
            type(UART_TX_GATE_ACTIVE_LEVEL) is not int
            or UART_TX_GATE_ACTIVE_LEVEL not in (0, 1)
        ):
            raise RuntimeError("UART_TX_GATE_ACTIVE_LEVEL must be 0 or 1")
        if UART_TX_GATE_ACTIVE_LEVEL != 1:
            raise RuntimeError("DFR0975-U UART TX gate must be active-high")
        if UART_TX_GATE_PIN in (UART_TX_PIN, UART_RX_PIN):
            raise RuntimeError("UART TX gate must use a dedicated GPIO")
        if (
            UART_PROTOCOL_TX_ENABLED is True
            and UART_TX_GATE_APPROVED is not True
        ):
            raise RuntimeError(
                "protocol TX requires an electrically approved hardware gate"
            )


def require_onewire_configuration():
    """Fail safely until the selected 1-Wire route is approved."""

    if ONEWIRE_PIN is None:
        raise RuntimeError("ONEWIRE_PIN is not configured")
    if ONEWIRE_PIN_APPROVED is not True:
        raise RuntimeError("ONEWIRE_PIN has not been electrically approved")
    profile = _active_profile()
    _require_product_pin(profile, "ONEWIRE_PIN", ONEWIRE_PIN)

    conflicts = (UART_TX_PIN, UART_RX_PIN, I2C_SDA_PIN, I2C_SCL_PIN)
    if profile == _PROFILE_DFR0975U:
        conflicts = conflicts + (UART_TX_GATE_PIN,)
    if ONEWIRE_PIN in conflicts:
        raise RuntimeError("1-Wire must use a dedicated GPIO")

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
    """Fail safely until the selected dedicated RTC route is approved."""

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
    profile = _active_profile()

    expected_i2c_id = 1 if profile == _PROFILE_DFR0975U else 0
    if (
        not isinstance(I2C_ID, int)
        or isinstance(I2C_ID, bool)
        or I2C_ID != expected_i2c_id
    ):
        raise RuntimeError(
            "{} RTC profile must use I2C{}".format(
                BOARD_SKU, expected_i2c_id
            )
        )

    _require_product_pin(profile, "I2C_SDA_PIN", I2C_SDA_PIN)
    _require_product_pin(profile, "I2C_SCL_PIN", I2C_SCL_PIN)
    if I2C_SDA_PIN == I2C_SCL_PIN:
        raise RuntimeError("I2C SDA and SCL pins must be different")

    conflicts = (UART_TX_PIN, UART_RX_PIN, ONEWIRE_PIN)
    if profile == _PROFILE_DFR0975U:
        conflicts = conflicts + (UART_TX_GATE_PIN,)
    if I2C_SDA_PIN in conflicts or I2C_SCL_PIN in conflicts:
        raise RuntimeError("RTC I2C pins must not share another function")

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
    """Fail safely until an explicit WLAN owner is approved."""

    if WIFI_RADIO_APPROVED is not True:
        raise RuntimeError("Wi-Fi radio has not been explicitly approved")
    _active_profile()
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
    """Fail safely until every assigned hardware route is approved."""

    missing = missing_pin_assignments()
    if missing:
        raise RuntimeError(
            "Hardware pins are not configured: {}".format(", ".join(missing))
        )
    require_uart_configuration()
    if (
        _active_profile() == _PROFILE_DFR0975U
        and UART_TX_GATE_APPROVED is not True
    ):
        raise RuntimeError(
            "complete DFR0975-U hardware requires an approved UART TX gate"
        )
    require_onewire_configuration()
    require_i2c_configuration()
    require_wifi_configuration()
