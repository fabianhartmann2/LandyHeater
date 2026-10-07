"""Bounded AP/Web-UI acceptance gate for volatile browser time.

Importing is inert. ``run`` copies the trusted production configuration into
disposable A/B stores, starts one AP-bound HTTP listener and accepts exactly
one kind of mutation: ``PUT /api/v1/time/browser-sync``.  Heater protocol,
UART, I2C and production storage remain untouched.
"""

import gc as _gc
import os as _os
import sys as _sys


CONFIRMATION = "BROWSER_TIME_PHONE_USB_ONLY_HEATER_OFF_V1"
AP_READY_TOKEN = "BROWSER_TIME_PHONE_AP_READY_V1"
SYNC_TOKEN = "BROWSER_TIME_PHONE_SYNC_ACCEPTED_V1"
PASS_TOKEN = "BROWSER_TIME_PHONE_PASS_V1"
FAIL_TOKEN = "BROWSER_TIME_PHONE_FAIL_V1"

AP_IP = "192.168.4.1"
AP_PASSWORD = "Phase7RadioOnly!92"
WINDOW_SECONDS = 300
POLL_MS = 25
STARTUP_TIMEOUT_MS = 30000
HOLD_AFTER_READY_MS = 3000
MINIMUM_HEAP_BYTES = 32 * 1024
CONFIG_MAX_RECORD_BYTES = 12 * 1024

PRODUCTION_CONFIG = "/landy_heater_config"
PRODUCTION_LEDGER = "/landy_heater_scheduler"
TEST_CONFIG = "/browser_time_phone_config"
TEST_LEDGER = "/browser_time_phone_ledger"
SUFFIXES = (".a", ".b", ".tmp")

_FROZEN_ORIGINS = (
    ("board_config", "board_config.py"),
    ("hardware.micropython_wifi", "hardware/micropython_wifi.py"),
    ("services.time_service", "services/time_service.py"),
    ("app.rest_application", "app/rest_application.py"),
    ("app.rest_composition", "app/rest_composition.py"),
    ("app.web_application", "app/web_application.py"),
    ("adapters.micropython_http_server", "adapters/micropython_http_server.py"),
)


def _require(condition, message):
    if not condition:
        raise RuntimeError("Browser-time phone gate failed: {}".format(message))


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


def _trusted_browser_time(body):
    if type(body) is not dict:
        return False
    clock = body.get("time")
    return (
        type(clock) is dict
        and clock.get("valid") is True
        and clock.get("source") == "browser"
        and clock.get("volatile_browser_time") is True
        and clock.get("timer_trusted") is True
        and clock.get("rtc_write_pending") is False
        and clock.get("rtc_commit_pending") is False
        and type(clock.get("utc_seconds")) is int
        and type(clock.get("local")) is dict
    )


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
        "controller",
        "protocol",
        "root_reads",
        "invalid_status_reads",
        "trusted_status_reads",
        "sync_attempts",
        "sync_successes",
        "blocked_mutations",
        "read_only_probes",
    )

    def __init__(self, application, controller, protocol):
        self.application = application
        self.controller = controller
        self.protocol = protocol
        self.root_reads = 0
        self.invalid_status_reads = 0
        self.trusted_status_reads = 0
        self.sync_attempts = 0
        self.sync_successes = 0
        self.blocked_mutations = 0
        self.read_only_probes = 0

    def _safety(self):
        _require(self.controller.requested_on is False, "heater was requested")
        _require(self.controller.request_revision == 0, "heater revision changed")
        _require(self.protocol.calls == 0, "heater protocol was accessed")

    def handle(self, request, peer_ip, ingress, local_ip):
        from app.web_application import WebResponse

        method = getattr(request, "method", None)
        path = getattr(request, "path", None)
        if method in ("HEAD", "OPTIONS"):
            self.read_only_probes += 1
            return WebResponse(
                405, b"", "text/plain; charset=utf-8", {"Allow": "GET"}
            )
        if method == "PUT" and path == "/api/v1/time/browser-sync":
            self.sync_attempts += 1
            response = self.application.handle(
                request, peer_ip, ingress, local_ip
            )
            if (
                getattr(response, "status", None) == 200
                and _trusted_browser_time(getattr(response, "body", None))
            ):
                self.sync_successes += 1
                print(SYNC_TOKEN)
            self._safety()
            return response
        if method != "GET":
            self.blocked_mutations += 1
            self._safety()
            return WebResponse(
                405, b"", "text/plain; charset=utf-8", {"Allow": "GET, PUT"}
            )
        response = self.application.handle(request, peer_ip, ingress, local_ip)
        if path == "/" and getattr(response, "status", None) == 200:
            self.root_reads += 1
        elif (
            path == "/api/v1/status"
            and getattr(response, "status", None) == 200
        ):
            body = getattr(response, "body", None)
            if _trusted_browser_time(body):
                self.trusted_status_reads += 1
            elif type(body) is dict and body.get("time", {}).get("valid") is False:
                self.invalid_status_reads += 1
        self._safety()
        return response


def _http_transport_healthy(snapshot):
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
        or parse_errors != 0
        or reentries != 0
        or min(socket_errors, accepted, completed, clients) < 0
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
    """Run one AP/Web-UI browser-time acceptance gate."""

    _require(confirmation == CONFIRMATION, "exact confirmation is required")
    _require(
        _stat_signature(_paths(TEST_CONFIG, TEST_LEDGER)) == (None,) * 6,
        "isolated test files already exist",
    )

    # ``mpremote run`` prepends the transient script directory.  Product
    # modules must nevertheless resolve from the immutable firmware image,
    # especially because the VFS intentionally retains a safe board profile.
    original_sys_path = tuple(_sys.path)
    if ".frozen" in _sys.path:
        _sys.path.remove(".frozen")
    _sys.path.insert(0, ".frozen")

    import network
    import time
    import board_config
    from adapters.config_file_store import AtomicJSONConfigStore
    from adapters.micropython_captive_dns import MicroPythonCaptiveDNS
    from adapters.micropython_http_server import MicroPythonHTTPServer
    from app.configuration_bootstrap import build_configured_runtime
    from app.heater_controller import HeaterController
    from app.network_composition import build_configured_network
    from app.rest_composition import build_rest_runtime
    from app.scheduler_controller_gateway import SchedulerControllerGateway
    from app.web_application import Phase9WebApplication
    from hardware.micropython_wifi import open_wifi_from_board_config
    from services.config_manager import ConfigManager, default_scheduler_ledger

    ticks_ms = time.ticks_ms
    ticks_add = time.ticks_add
    ticks_diff = time.ticks_diff
    sleep_ms = time.sleep_ms
    production_paths = _paths(PRODUCTION_CONFIG, PRODUCTION_LEDGER)
    production_before = _stat_signature(production_paths)
    manager = None
    port = None
    network_manager = None
    protocol = None
    controller = None
    rest_runtime = None
    server = None
    dns = None
    observer = None
    primary = None
    failure_http = None
    stage = "preflight"
    heaps = [_heap()]
    try:
        expected_gates = {
            "UART_PINS_APPROVED": True,
            "UART_PROTOCOL_TX_ENABLED": True,
            "UART_DIRECT_TX_APPROVED": True,
            "UART_TX_GATE_APPROVED": False,
            "I2C_PINS_APPROVED": False,
            "WIFI_RADIO_APPROVED": False,
        }
        for name, expected in expected_gates.items():
            _require(
                getattr(board_config, name, None) is expected,
                "{} differs from the frozen heater profile".format(name),
            )
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
        configuration["system"]["setup_complete"] = True
        configuration["network"]["access_point"]["password"] = AP_PASSWORD
        configuration["network"]["known_networks"] = []
        configuration["timers"] = []

        stage = "isolated_configuration"
        manager = _new_manager(
            ConfigManager, AtomicJSONConfigStore, TEST_CONFIG, TEST_LEDGER
        )
        _require(manager.load() is False, "isolated configuration was not empty")
        _require(
            manager.load_scheduler_checkpoint() is False,
            "isolated scheduler ledger was not empty",
        )
        _require(
            manager.checkpoint_scheduler(default_scheduler_ledger(), 0) is True,
            "isolated scheduler provisioning failed",
        )
        _require(
            manager.commit(configuration, 0) is True,
            "isolated configuration provisioning failed",
        )
        manager = _new_manager(
            ConfigManager, AtomicJSONConfigStore, TEST_CONFIG, TEST_LEDGER
        )
        _require(manager.load() is True, "isolated configuration reload failed")
        _require(
            manager.load_scheduler_checkpoint() is True,
            "isolated scheduler reload failed",
        )
        configured = build_configured_runtime(
            manager, ticks_diff=ticks_diff, ticks_add=ticks_add
        )
        _require(configured.scheduler.armed is False, "scheduler was armed")
        _require(
            configured.time_service.snapshot(ticks_ms())["valid"] is False,
            "clock was valid before browser synchronization",
        )

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
            network_manager.step(now)
            network_manager.drain_events()
            state = network_manager.snapshot()
            _require(state["faulted"] is False, "network manager faulted")
            ap = state["access_point"]
            if ap["active"] is True and ap["ip"] == AP_IP:
                break
            _require(
                ticks_diff(now, startup_deadline) < 0,
                "access point startup timed out",
            )
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
        observer = _ObservedWeb(web, controller, protocol)
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
        heaps.append(_heap())
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
            action = network_manager.step(now)
            network_manager.drain_events()
            state = network_manager.snapshot()
            _require(state["faulted"] is False, "network manager faulted")
            ap = state["access_point"]
            _require(
                ap["active"] is True and ap["ip"] == AP_IP,
                "access point state changed",
            )
            _require(
                type(ap["clients"]) is int and 0 <= ap["clients"] <= 1,
                "unexpected AP client count",
            )
            if ap["clients"] == 1:
                phone_seen = True
            _require(action in (None, "ap_checked"), "network changed state")
            dns.step()
            server.step()
            http = server.snapshot()
            if not _http_transport_healthy(http):
                failure_http = http
            _require(failure_http is None, "HTTP transport faulted")
            observer._safety()
            complete = (
                phone_seen
                and observer.root_reads >= 1
                and observer.invalid_status_reads >= 1
                and observer.sync_successes == 1
                and observer.trusted_status_reads >= 1
                and observer.blocked_mutations == 0
            )
            if complete and ready_at is None:
                ready_at = now
            if (
                ready_at is not None
                and ticks_diff(now, ready_at) >= HOLD_AFTER_READY_MS
                and http["client_count"] == 0
            ):
                break
            _require(
                ticks_diff(now, deadline) < 0,
                "browser-time observation timed out",
            )
            sleep_ms(POLL_MS)

        stage = "postcheck"
        heaps.append(_heap())
        _require(
            _stat_signature(production_paths) == production_before,
            "production storage changed",
        )
    except BaseException as error:
        primary = error
    finally:
        for owner in (server, dns, rest_runtime, network_manager):
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
        try:
            radios_off = _interfaces_inactive(network)
        except BaseException:
            radios_off = False
        production_unchanged = (
            _stat_signature(production_paths) == production_before
        )
        _sys.path[:] = original_sys_path

    if primary is not None:
        print(FAIL_TOKEN)
        print("stage={}".format(stage))
        print("error_type={}".format(type(primary).__name__))
        message = str(primary)
        if message.startswith("Browser-time phone gate failed:"):
            print("error={}".format(message))
        if failure_http is not None:
            print("http={}".format(failure_http))
        if isinstance(primary, MemoryError):
            raise MemoryError() from None
        raise RuntimeError("Browser-time phone gate failed") from None

    _require(cleanup_ok, "isolated test cleanup failed")
    _require(radios_off, "a WLAN interface remained active")
    _require(production_unchanged, "production storage changed during cleanup")
    _require(
        board_config.WIFI_RADIO_APPROVED is False,
        "temporary Wi-Fi approval remained open",
    )
    _require(
        all(value >= MINIMUM_HEAP_BYTES for value in heaps),
        "a heap checkpoint failed",
    )
    print("sync_attempts={}".format(observer.sync_attempts))
    print("trusted_status_reads={}".format(observer.trusted_status_reads))
    print("production_storage_unchanged=True")
    print("isolated_files_removed=True")
    print("radio_http_cleanup=True")
    print(PASS_TOKEN)
    return {
        "sync_attempts": observer.sync_attempts,
        "trusted_status_reads": observer.trusted_status_reads,
    }


if __name__ == "__main__":
    run(CONFIRMATION)
