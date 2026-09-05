"""Phase-13 DS18B20 product-runtime modules for DFR0975-U MicroPython 1.28."""

include("$(PORT_DIR)/boards/manifest.py")

PROJECT_ROOT = "../.."

module("board_config.py", base_path=PROJECT_ROOT, opt=0)

package(
    "adapters",
    base_path=PROJECT_ROOT,
    opt=0,
    files=(
        "__init__.py",
        "config_file_store.py",
        "ds18b20_adapter.py",
        "ds3231_adapter.py",
        "micropython_captive_dns.py",
        "micropython_http_server.py",
    ),
)

package(
    "app",
    base_path=PROJECT_ROOT,
    opt=0,
    files=(
        "__init__.py",
        "application_state.py",
        "composition.py",
        "configuration_api_gateway.py",
        "configuration_bootstrap.py",
        "discovery_composition.py",
        "heater_controller.py",
        "manual_control_gateway.py",
        "network_composition.py",
        "network_configuration.py",
        "network_manager.py",
        "rest_application.py",
        "rest_composition.py",
        "scheduler.py",
        "scheduler_controller_gateway.py",
        "sensor_composition.py",
        "temperature_manager.py",
        "web_application.py",
        "web_assets.py",
    ),
)

package(
    "hardware",
    base_path=PROJECT_ROOT,
    opt=0,
    files=(
        "__init__.py",
        "micropython_ds18b20.py",
        "micropython_ds3231.py",
        "micropython_wifi.py",
    ),
)

package(
    "protocol",
    base_path=PROJECT_ROOT,
    opt=0,
    files=(
        "__init__.py",
        "autoterm_frames.py",
        "autoterm_protocol.py",
        "autoterm_service.py",
        "crc16.py",
        "uart_transport.py",
    ),
)

package(
    "services",
    base_path=PROJECT_ROOT,
    opt=0,
    files=(
        "__init__.py",
        "config_manager.py",
        "configuration_errors.py",
        "configuration_storage.py",
        "diagnostics_hub.py",
        "http_protocol.py",
        "rest_rate_limiter.py",
        "rest_security.py",
        "rtc_time_bridge.py",
        "strict_json.py",
        "time_service.py",
    ),
)
