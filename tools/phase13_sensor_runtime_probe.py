"""Bounded USB-only Phase-13 product sensor-runtime probe.

Importing this module is inert. ``run`` reads the existing production A/B
configuration without committing it, constructs the normal cold product
models, explicitly starts only the approved 1-Wire owner, and requires three
complete error-free sampling cycles.  It never enables Wi-Fi, I2C, UART or
heater protocol transmission and always returns GPIO4 to an input.
"""

import os as _os


PHASE13_SENSOR_CONFIRMATION = "PHASE13_SENSOR_RUNTIME_USB_ONLY_V1"
PHASE13_SENSOR_PASS_TOKEN = "PHASE13_SENSOR_RUNTIME_PASS_V1"

CONFIG_BASE_PATH = "/landy_heater_config"
LEDGER_BASE_PATH = "/landy_heater_scheduler"
POLL_DELAY_MS = 25
MAXIMUM_WINDOW_MS = 12000
REQUIRED_CYCLES = 3

EXPECTED_ASSIGNMENTS = {
    "roof_tent": "286ed3bd0b000013",
    "cabin": "28875f270d00006d",
    "outside": "28159f270d000090",
}

_STORE_PATHS = tuple(
    base + suffix
    for base in (CONFIG_BASE_PATH, LEDGER_BASE_PATH)
    for suffix in (".a", ".b", ".tmp")
)


def _require(condition, message):
    if not condition:
        raise RuntimeError("Phase-13 sensor runtime failed: {}".format(message))


def _missing_file(error):
    code = getattr(error, "errno", None)
    if code is None and getattr(error, "args", None):
        code = error.args[0]
    return code == 2


def _stat_signature(paths=_STORE_PATHS):
    result = []
    for path in paths:
        try:
            result.append(tuple(_os.stat(path)))
        except OSError as error:
            if not _missing_file(error):
                raise
            result.append(None)
    return tuple(result)


def _validate_board_profile(config):
    identity = (
        config.BOARD_SKU,
        config.BOARD_HARDWARE_REVISION,
        config.BOARD_MODULE,
        config.MICROPYTHON_TARGET,
        config.MICROPYTHON_BUILD_BOARD,
        config.MICROPYTHON_VARIANT,
        config.MICROPYTHON_VERSION,
    )
    _require(
        identity
        == (
            "DFR0975-U",
            "1.0",
            "ESP32-S3-WROOM-1U-N16R8",
            "ESP32_GENERIC_S3",
            "DFR0975U_N16R8",
            "SPIRAM_OCT",
            "1.28.0",
        ),
        "board identity differs",
    )
    for name in (
        "UART_PINS_APPROVED",
        "UART_PROTOCOL_TX_ENABLED",
        "UART_TX_GATE_APPROVED",
        "I2C_PINS_APPROVED",
        "WIFI_RADIO_APPROVED",
    ):
        _require(getattr(config, name, None) is False, "{} is open".format(name))
    _require(config.ONEWIRE_PIN == 4, "1-Wire route differs")
    _require(config.ONEWIRE_PIN_APPROVED is True, "1-Wire route is not approved")
    config.require_onewire_configuration()
    return True


def _interfaces_inactive(network_module):
    for interface_id in (network_module.STA_IF, network_module.AP_IF):
        if network_module.WLAN(interface_id).active() is not False:
            return False
    return True


def _validate_temperature_snapshot(snapshot):
    _require(type(snapshot) is dict, "temperature snapshot is malformed")
    _require(
        snapshot.get("assignments") == EXPECTED_ASSIGNMENTS,
        "persisted sensor roles differ",
    )
    discovered = snapshot.get("discovered_rom_ids")
    _require(type(discovered) is tuple, "discovery result is malformed")
    _require(
        frozenset(discovered) == frozenset(EXPECTED_ASSIGNMENTS.values())
        and len(discovered) == 3,
        "the exact three assigned sensors were not discovered",
    )
    sensors = snapshot.get("sensors")
    _require(type(sensors) is dict, "role readings are malformed")
    values = {}
    for role, rom_id in EXPECTED_ASSIGNMENTS.items():
        reading = sensors.get(role)
        _require(type(reading) is dict, "{} reading is missing".format(role))
        _require(reading.get("rom_id") == rom_id, "{} ROM differs".format(role))
        value = reading.get("value_c")
        _require(
            type(value) is float and -55.0 <= value <= 125.0,
            "{} value is invalid".format(role),
        )
        _require(reading.get("usable") is True, "{} is not usable".format(role))
        _require(reading.get("health") == "healthy", "{} is not healthy".format(role))
        values[role] = value
    return values


def run(confirmation):
    """Run the bounded product sensor probe and return non-secret facts."""

    _require(
        confirmation == PHASE13_SENSOR_CONFIRMATION,
        "exact USB-only confirmation is required",
    )

    import board_config
    import network
    from time import ticks_add, ticks_diff, ticks_ms, sleep_ms
    from adapters.config_file_store import AtomicJSONConfigStore
    from app.configuration_bootstrap import build_configured_runtime
    from app.sensor_composition import build_configured_sensor_runtime
    from services.config_manager import ConfigManager

    _validate_board_profile(board_config)
    _require(_interfaces_inactive(network), "a WLAN interface is active before start")
    storage_before = _stat_signature()
    manager = ConfigManager(
        AtomicJSONConfigStore(CONFIG_BASE_PATH),
        AtomicJSONConfigStore(LEDGER_BASE_PATH),
    )
    sensor_runtime = None
    primary = None
    values = None
    adapter_status = None
    try:
        _require(manager.load() is True, "production configuration is not trusted")
        _require(
            manager.load_scheduler_checkpoint() is True,
            "scheduler ledger is not trusted",
        )
        _require(manager.faulted is False, "configuration manager is faulted")
        configured = build_configured_runtime(manager)
        _require(
            configured.temperature_manager.assignments == EXPECTED_ASSIGNMENTS,
            "configured sensor roles differ",
        )
        sensor_runtime = build_configured_sensor_runtime(manager, configured)
        _require(sensor_runtime.start() is True, "sensor runtime did not start")
        deadline = ticks_add(ticks_ms(), MAXIMUM_WINDOW_MS)
        while True:
            sensor_runtime.step()
            runtime_snapshot = sensor_runtime.snapshot()
            adapter_status = runtime_snapshot["adapter"]
            if adapter_status.get("completed_cycles", 0) >= REQUIRED_CYCLES:
                break
            _require(
                ticks_diff(deadline, ticks_ms()) > 0,
                "three sampling cycles timed out",
            )
            sleep_ms(POLL_DELAY_MS)

        _require(runtime_snapshot["faulted"] is False, "sensor runtime faulted")
        _require(adapter_status.get("scans") >= 1, "discovery was not executed")
        _require(adapter_status.get("conversions") >= REQUIRED_CYCLES, "cycles differ")
        _require(adapter_status.get("valid_readings") >= 9, "valid read count differs")
        for counter in (
            "scan_errors",
            "conversion_errors",
            "invalid_readings",
            "read_errors",
            "manager_rejections",
            "manager_errors",
            "bus_contract_errors",
        ):
            _require(adapter_status.get(counter) == 0, "{} is nonzero".format(counter))
        values = _validate_temperature_snapshot(
            configured.temperature_manager.snapshot(ticks_ms())
        )
        _require(
            _stat_signature() == storage_before,
            "production storage changed during read-only sampling",
        )
        _require(_interfaces_inactive(network), "a WLAN interface became active")
    except BaseException as error:
        primary = error
        raise
    finally:
        if sensor_runtime is not None:
            try:
                sensor_runtime.deinit()
            except BaseException:
                if primary is None:
                    raise
        _require(_interfaces_inactive(network), "a WLAN interface remained active")

    print("cycles={}".format(adapter_status["completed_cycles"]))
    print("valid_readings={}".format(adapter_status["valid_readings"]))
    print("roof_tent_c={:.4f}".format(values["roof_tent"]))
    print("cabin_c={:.4f}".format(values["cabin"]))
    print("outside_c={:.4f}".format(values["outside"]))
    print("storage_unchanged=True")
    print("radios_inactive=True")
    print("gpio4_released=True")
    print(PHASE13_SENSOR_PASS_TOKEN)
    return {
        "cycles": adapter_status["completed_cycles"],
        "valid_readings": adapter_status["valid_readings"],
        "values_c": values,
    }
