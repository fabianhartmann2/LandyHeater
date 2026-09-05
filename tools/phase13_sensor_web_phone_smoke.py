"""Bounded live DS18B20-to-REST/Web-UI phone acceptance gate.

Importing this module is inert. ``run`` copies the trusted production
configuration into disposable A/B stores, starts the approved sensor runtime
and a single AP-bound HTTP listener, and permits only browser GET requests.
Production storage, heater UART, I2C and protocol TX remain untouched.
"""

import gc as _gc
import os as _os
import sys as _sys


CONFIRMATION = "PHASE13_SENSOR_WEB_PHONE_USB_ONLY_V1"
AP_READY_TOKEN = "PHASE13_SENSOR_WEB_AP_READY_V1"
UI_READY_TOKEN = "PHASE13_SENSOR_WEB_UI_READY_V1"
PASS_TOKEN = "PHASE13_SENSOR_WEB_PHONE_PASS_V1"
FAIL_TOKEN = "PHASE13_SENSOR_WEB_PHONE_FAIL_V1"

AP_IP = "192.168.4.1"
AP_PASSWORD = "Phase7RadioOnly!92"
WINDOW_SECONDS = 300
POLL_MS = 25
STARTUP_TIMEOUT_MS = 30000
HOLD_AFTER_READY_MS = 8000
MINIMUM_HEAP_BYTES = 32 * 1024
CONFIG_MAX_RECORD_BYTES = 12 * 1024

PRODUCTION_CONFIG = "/landy_heater_config"
PRODUCTION_LEDGER = "/landy_heater_scheduler"
TEST_CONFIG = "/phase13_sensor_web_config"
TEST_LEDGER = "/phase13_sensor_web_ledger"
SUFFIXES = (".a", ".b", ".tmp")
EXPECTED_ASSIGNMENTS = {
    "roof_tent": "286ed3bd0b000013",
    "cabin": "28875f270d00006d",
    "outside": "28159f270d000090",
}

_FROZEN_ORIGINS = (
    ("board_config", "board_config.py"),
    ("app.sensor_composition", "app/sensor_composition.py"),
    ("app.temperature_manager", "app/temperature_manager.py"),
    ("hardware.micropython_ds18b20", "hardware/micropython_ds18b20.py"),
    ("hardware.micropython_wifi", "hardware/micropython_wifi.py"),
    ("app.rest_application", "app/rest_application.py"),
    ("app.rest_composition", "app/rest_composition.py"),
    ("app.web_application", "app/web_application.py"),
    ("adapters.micropython_http_server", "adapters/micropython_http_server.py"),
)


def _require(condition, message):
    if not condition:
        raise RuntimeError("Phase-13 sensor Web gate failed: {}".format(message))


def _paths(*bases):
    return tuple(base + suffix for base in bases for suffix in SUFFIXES)


def _missing(error):
    code = getattr(error, "errno", None)
    if code is None and getattr(error, "args", None):
        code = error.args[0]
    return code == 2


def _stat_signature(paths):
    result = []
    for path in paths:
        try:
            result.append(tuple(_os.stat(path)))
        except OSError as error:
            if not _missing(error):
                raise
            result.append(None)
    return tuple(result)


def _remove_test_files():
    for path in _paths(TEST_CONFIG, TEST_LEDGER):
        try:
            _os.remove(path)
        except OSError as error:
            if not _missing(error):
                return False
    return _stat_signature(_paths(TEST_CONFIG, TEST_LEDGER)) == (None,) * 6


def _heap():
    _gc.collect()
    value = _gc.mem_free()
    _require(
        type(value) is int and value >= MINIMUM_HEAP_BYTES,
        "free heap is below 32 KiB",
    )
    return value


def _valid_temperatures(body):
    if type(body) is not dict:
        return None
    temperatures = body.get("temperatures")
    if type(temperatures) is not dict:
        return None
    values = {}
    for role in ("roof_tent", "cabin", "outside"):
        reading = temperatures.get(role)
        if type(reading) is not dict:
            return None
        value = reading.get("value_c")
        if (
            type(value) is not float
            or not -55.0 <= value <= 125.0
            or reading.get("health") != "ok"
        ):
            return None
        values[role] = value
    return values


class _NullProtocolPort:
    __slots__ = ("calls",)

    def __init__(self):
        self.calls = 0

    def _forbidden(self):
        self.calls += 1
        raise RuntimeError("heater protocol access is forbidden")

    def validate_inbound_frame(self, frame):
        return self._forbidden()

    def request_initialization(self):
        return self._forbidden()

    def request_status(self):
        return self._forbidden()

    def request_start(self, *arguments, **keywords):
        return self._forbidden()

    def request_shutdown(self):
        return self._forbidden()


class _ObservedWeb:
    __slots__ = (
        "application",
        "root_reads",
        "settings_reads",
        "valid_status_reads",
        "values",
        "read_only_probes",
        "mutations",
    )

    def __init__(self, application):
        self.application = application
        self.root_reads = 0
        self.settings_reads = 0
        self.valid_status_reads = 0
        self.values = None
        self.read_only_probes = 0
        self.mutations = 0

    def handle(self, request, peer_ip, ingress, local_ip):
        method = getattr(request, "method", None)
        path = getattr(request, "path", None)
        if method in ("HEAD", "OPTIONS"):
            # Captive-portal/browser discovery may issue a read-only probe.
            # Keep it outside the product application, but do not turn an
            # ordinary discovery request into a latched HTTP-server fault.
            from app.web_application import WebResponse

            self.read_only_probes += 1
            return WebResponse(
                405,
                b"",
                "text/plain; charset=utf-8",
                {"Allow": "GET"},
            )
        if method != "GET":
            self.mutations += 1
            from app.web_application import WebResponse

            return WebResponse(
                405,
                b"",
                "text/plain; charset=utf-8",
                {"Allow": "GET"},
            )
        response = self.application.handle(request, peer_ip, ingress, local_ip)
        if path == "/" and getattr(response, "status", None) == 200:
            self.root_reads += 1
        elif path == "/api/v1/settings" and getattr(response, "status", None) == 200:
            body = getattr(response, "body", None)
            if (
                type(body) is dict
                and body.get("system", {}).get("setup_complete") is True
            ):
                self.settings_reads += 1
        elif path == "/api/v1/status" and getattr(response, "status", None) == 200:
            values = _valid_temperatures(getattr(response, "body", None))
            if values is not None:
                self.valid_status_reads += 1
                self.values = values
        return response


def _http_transport_healthy(snapshot):
    """Accept a fully accounted browser-cancelled response as non-fatal."""

    try:
        faulted = snapshot["faulted"]
        parse_errors = snapshot["parse_errors"]
        socket_errors = snapshot["socket_errors"]
        accepted = snapshot["accepted"]
        completed = snapshot["completed"]
        clients = snapshot["client_count"]
        reentries = snapshot["reentries"]
        last_error = snapshot["last_error"]
    except (KeyError, TypeError):
        return False
    if (
        faulted is not False
        or type(parse_errors) is not int
        or parse_errors != 0
        or type(socket_errors) is not int
        or socket_errors < 0
        or type(accepted) is not int
        or type(completed) is not int
        or type(clients) is not int
        or type(reentries) is not int
        or reentries != 0
        or min(accepted, completed, clients) < 0
        or completed + clients > accepted
    ):
        return False
    if socket_errors == 0:
        return True
    return (
        last_error == "client_send_failed"
        and socket_errors == accepted - completed - clients
    )


def _verify_frozen_origins():
    _require(
        type(_sys.path) is list and _sys.path and _sys.path[0] == ".frozen",
        "frozen modules do not have path precedence",
    )
    for name, expected in _FROZEN_ORIGINS:
        module = _sys.modules.get(name)
        origin = None if module is None else getattr(module, "__file__", None)
        _require(
            origin == expected and not origin.startswith("/"),
            "{} did not resolve from frozen firmware".format(name),
        )
    return True


def _interfaces_inactive(network_module):
    return (
        network_module.WLAN(network_module.STA_IF).active() is False
        and network_module.WLAN(network_module.AP_IF).active() is False
    )


def _new_manager(ConfigManager, AtomicJSONConfigStore, config_base, ledger_base):
    return ConfigManager(
        AtomicJSONConfigStore(
            config_base, max_record_bytes=CONFIG_MAX_RECORD_BYTES
        ),
        AtomicJSONConfigStore(ledger_base),
    )


def run(confirmation):
    """Run one read-only real-sensor Web-UI phone gate."""

    _require(confirmation == CONFIRMATION, "exact confirmation is required")
    _require(_stat_signature(_paths(TEST_CONFIG, TEST_LEDGER)) == (None,) * 6,
             "isolated test files already exist")

    import network
    import time
    import board_config
    from adapters.config_file_store import AtomicJSONConfigStore
    from adapters.micropython_captive_dns import MicroPythonCaptiveDNS
    from adapters.micropython_http_server import MicroPythonHTTPServer
    from app.configuration_bootstrap import build_configured_runtime
    from app.heater_controller import HeaterController
    from app.network_composition import build_configured_network
    from app.scheduler_controller_gateway import SchedulerControllerGateway
    from app.sensor_composition import build_configured_sensor_runtime
    from app.web_application import Phase9WebApplication
    from app.rest_composition import build_rest_runtime
    from hardware.micropython_wifi import open_wifi_from_board_config
    from services.config_manager import ConfigManager, default_scheduler_ledger

    ticks_ms = time.ticks_ms
    ticks_add = time.ticks_add
    ticks_diff = time.ticks_diff
    sleep_ms = time.sleep_ms
    production_paths = _paths(PRODUCTION_CONFIG, PRODUCTION_LEDGER)
    production_before = _stat_signature(production_paths)
    manager = None
    configured = None
    sensor_runtime = None
    port = None
    network_runtime = None
    network_manager = None
    protocol = None
    controller = None
    rest_runtime = None
    server = None
    dns = None
    observer = None
    failure_http = None
    primary = None
    stage = "preflight"
    heap_samples = [_heap()]
    try:
        _require(board_config.ONEWIRE_PIN == 4, "1-Wire route differs")
        _require(board_config.ONEWIRE_PIN_APPROVED is True, "1-Wire is closed")
        for name in (
            "UART_PINS_APPROVED",
            "UART_PROTOCOL_TX_ENABLED",
            "UART_TX_GATE_APPROVED",
            "I2C_PINS_APPROVED",
            "WIFI_RADIO_APPROVED",
        ):
            _require(getattr(board_config, name, None) is False,
                     "{} is open before start".format(name))
        _require(_interfaces_inactive(network), "a radio is active before start")

        production = _new_manager(
            ConfigManager,
            AtomicJSONConfigStore,
            PRODUCTION_CONFIG,
            PRODUCTION_LEDGER,
        )
        _require(production.load() is True, "production configuration is untrusted")
        _require(
            production.load_scheduler_checkpoint() is True,
            "production scheduler ledger is untrusted",
        )
        configuration = production.snapshot()["configuration"]
        _require(
            configuration["sensors"]["assignments"] == EXPECTED_ASSIGNMENTS,
            "production sensor assignments differ",
        )
        configuration["system"]["setup_complete"] = True
        configuration["network"]["access_point"]["password"] = AP_PASSWORD
        configuration["network"]["known_networks"] = []

        stage = "isolated_configuration"
        manager = _new_manager(
            ConfigManager, AtomicJSONConfigStore, TEST_CONFIG, TEST_LEDGER
        )
        _require(manager.load() is False, "isolated config was not empty")
        _require(
            manager.load_scheduler_checkpoint() is False,
            "isolated ledger was not empty",
        )
        _require(
            manager.checkpoint_scheduler(default_scheduler_ledger(), 0) is True,
            "isolated ledger provisioning failed",
        )
        _require(manager.commit(configuration, 0) is True,
                 "isolated configuration provisioning failed")
        manager = _new_manager(
            ConfigManager, AtomicJSONConfigStore, TEST_CONFIG, TEST_LEDGER
        )
        _require(manager.load() is True, "isolated config reload failed")
        _require(manager.load_scheduler_checkpoint() is True,
                 "isolated ledger reload failed")
        configured = build_configured_runtime(
            manager, ticks_diff=ticks_diff, ticks_add=ticks_add
        )
        _require(configured.scheduler.armed is False, "scheduler was armed")
        _require(configured.temperature_manager.assignments == EXPECTED_ASSIGNMENTS,
                 "runtime sensor assignments differ")

        stage = "sensor_start"
        sensor_runtime = build_configured_sensor_runtime(manager, configured)
        _require(sensor_runtime.start() is True, "sensor runtime did not start")

        stage = "network_start"
        board_config.WIFI_RADIO_APPROVED = True
        port = open_wifi_from_board_config()
        network_runtime = build_configured_network(
            manager, port, ticks_diff=ticks_diff, ticks_add=ticks_add
        )
        network_manager = network_runtime.manager
        now = ticks_ms()
        _require(network_manager.start(now) is True, "network did not start")
        startup_deadline = ticks_add(now, STARTUP_TIMEOUT_MS)
        while True:
            now = ticks_ms()
            sensor_runtime.step()
            network_manager.step(now)
            network_manager.drain_events()
            state = network_manager.snapshot()
            _require(state["faulted"] is False, "network manager faulted")
            ap = state["access_point"]
            if ap["active"] is True and ap["ip"] == AP_IP:
                break
            _require(ticks_diff(now, startup_deadline) < 0,
                     "access point startup timed out")
            sleep_ms(POLL_MS)

        stage = "web_start"
        protocol = _NullProtocolPort()
        controller = HeaterController(
            protocol,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
            maximum_runtime_minutes=configuration["heater"][
                "maximum_runtime_minutes"
            ],
            temperature_manager=configured.temperature_manager,
        )
        scheduler_gateway = SchedulerControllerGateway(
            configured.scheduler, controller, ticks_ms=ticks_ms
        )
        rest_runtime = build_rest_runtime(
            manager,
            configured,
            controller,
            scheduler_gateway,
            _os.urandom,
            (AP_IP,),
            "ap",
            configured_network_runtime=network_runtime,
            ticks_ms=ticks_ms,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
            mem_free=_gc.mem_free,
        )
        _require(rest_runtime.start() is True, "REST security did not start")
        web = Phase9WebApplication(rest_runtime, AP_IP)
        observer = _ObservedWeb(web)
        server = MicroPythonHTTPServer(
            web,
            AP_IP,
            request_handler=observer.handle,
            request_ingress="ap",
            request_handler_uses_ingress=True,
            ticks_ms=ticks_ms,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
        )
        dns = MicroPythonCaptiveDNS(AP_IP)
        _require(server.start() is True, "HTTP listener did not start")
        _require(dns.start() is True, "captive DNS did not start")
        _verify_frozen_origins()
        heap_samples.append(_heap())
        print(AP_READY_TOKEN)
        print("ssid=Landy Heater")
        print("url=http://192.168.4.1/")
        print("window_seconds={}".format(WINDOW_SECONDS))

        stage = "browser_observation"
        deadline = ticks_add(ticks_ms(), WINDOW_SECONDS * 1000)
        ready_at = None
        phone_seen = False
        while True:
            now = ticks_ms()
            sensor_runtime.step()
            action = network_manager.step(now)
            network_manager.drain_events()
            state = network_manager.snapshot()
            _require(state["faulted"] is False, "network manager faulted")
            ap = state["access_point"]
            _require(ap["active"] is True and ap["ip"] == AP_IP,
                     "access point state changed")
            _require(type(ap["clients"]) is int and 0 <= ap["clients"] <= 1,
                     "unexpected AP client count")
            if ap["clients"] == 1:
                phone_seen = True
            _require(action in (None, "ap_checked"), "network changed state")
            dns.step()
            server.step()
            http = server.snapshot()
            if not _http_transport_healthy(http):
                failure_http = http
            _require(failure_http is None, "HTTP transport faulted")
            _require(controller.requested_on is False,
                     "heater requested state changed")
            _require(protocol.calls == 0, "heater protocol was accessed")
            _require(observer.mutations == 0, "a mutation was observed")
            complete = (
                phone_seen
                and observer.root_reads >= 1
                and observer.settings_reads >= 1
                and observer.valid_status_reads >= 2
            )
            if complete and ready_at is None:
                ready_at = now
                values = observer.values
                print(UI_READY_TOKEN)
                print("roof_tent_c={:.4f}".format(values["roof_tent"]))
                print("cabin_c={:.4f}".format(values["cabin"]))
                print("outside_c={:.4f}".format(values["outside"]))
            if (
                ready_at is not None
                and ticks_diff(now, ready_at) >= HOLD_AFTER_READY_MS
                and http["client_count"] == 0
            ):
                break
            _require(ticks_diff(now, deadline) < 0,
                     "live UI temperature observation timed out")
            sleep_ms(POLL_MS)

        stage = "postcheck"
        heap_samples.append(_heap())
        _require(_stat_signature(production_paths) == production_before,
                 "production storage changed")
    except BaseException as error:
        primary = error
    finally:
        for owner in (server, dns, rest_runtime, sensor_runtime, network_manager):
            if owner is not None:
                try:
                    owner.deinit()
                except BaseException:
                    pass
        if network_manager is None and port is not None:
            try:
                port.deinit()
            except BaseException:
                pass
        if "board_config" in _sys.modules:
            board_config.WIFI_RADIO_APPROVED = False
        cleanup_ok = _remove_test_files()
        radios_off = False
        try:
            radios_off = _interfaces_inactive(network)
        except BaseException:
            pass
        production_unchanged = _stat_signature(production_paths) == production_before

    if primary is not None:
        print(FAIL_TOKEN)
        print("stage={}".format(stage))
        print("error_type={}".format(type(primary).__name__))
        message = str(primary)
        if message.startswith("Phase-13 sensor Web gate failed:"):
            print("error={}".format(message))
        if failure_http is not None:
            print(
                "http={} {} {} {} {} {} {}".format(
                    failure_http.get("last_error") or "none",
                    failure_http.get("accepted"),
                    failure_http.get("completed"),
                    failure_http.get("client_count"),
                    failure_http.get("parse_errors"),
                    failure_http.get("timeouts"),
                    failure_http.get("socket_errors"),
                )
            )
        if isinstance(primary, MemoryError):
            raise MemoryError() from None
        raise RuntimeError("Phase-13 sensor Web gate failed") from None
    _require(cleanup_ok, "isolated test cleanup failed")
    _require(radios_off, "a WLAN interface remained active")
    _require(production_unchanged, "production storage changed during cleanup")
    _require(board_config.WIFI_RADIO_APPROVED is False,
             "temporary Wi-Fi approval remained open")
    _require(all(value >= MINIMUM_HEAP_BYTES for value in heap_samples),
             "a heap checkpoint failed")
    values = observer.values
    print("status_reads={}".format(observer.valid_status_reads))
    print("production_storage_unchanged=True")
    print("isolated_files_removed=True")
    print("radio_sensor_http_cleanup=True")
    print(PASS_TOKEN)
    return {"values_c": values, "status_reads": observer.valid_status_reads}
