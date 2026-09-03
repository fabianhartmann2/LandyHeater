"""Bounded Phase-10.1 AP-captive and read-only STA discovery gate.

The gate reloads only the isolated configuration created by the explicit
Phase-10 integration setup. It never prints credentials, never touches the
production stores, and keeps every heater/peripheral capability closed.
"""

import gc as _gc
import os as _os

from tools import phase8_full_rest_phone_smoke as _base
from tools import phase10_integration_phone_smoke as _integration
from tools import phase10_setup_phone_smoke as _phase10


DISCOVERY_CONFIRMATION = "PHASE10_1_DISCOVERY_CONFIRM_V1"
DISCOVERY_READY_TOKEN = "PHASE10_1_DISCOVERY_READY_V1"
DISCOVERY_PASS_TOKEN = "PHASE10_1_DISCOVERY_PASS_V1"
DISCOVERY_FAIL_TOKEN = "PHASE10_1_DISCOVERY_FAIL_V1"

AP_IP = "192.168.4.1"
AP_PASSWORD = "Phase7RadioOnly!92"
MINIMUM_FREE_BYTES = 32 * 1024
POLL_INTERVAL_MS = 25
STARTUP_TIMEOUT_MS = 60000
MINIMUM_WINDOW_SECONDS = 180
MAXIMUM_WINDOW_SECONDS = 900

_CAPTIVE_PATHS = (
    "/generate_204",
    "/gen_204",
    "/hotspot-detect.html",
    "/library/test/success.html",
    "/connecttest.txt",
    "/ncsi.txt",
    "/canonical.html",
    "/success.txt",
)


def _require(condition, message):
    if not condition:
        raise RuntimeError("Phase-10.1 discovery smoke failed: {}".format(message))


def _window(value):
    if type(value) is not int or not MINIMUM_WINDOW_SECONDS <= value <= MAXIMUM_WINDOW_SECONDS:
        raise ValueError("window_seconds is outside its bounded range")
    return value


def _configured_for_test(configuration):
    try:
        network = configuration["network"]
        profiles = network["known_networks"]
        profile = profiles[0]
        ap_password = network["access_point"]["password"]
        station_ssid = profile["ssid"]
        station_password = profile["password"]
    except (KeyError, IndexError, TypeError):
        return False
    return (
        configuration.get("system", {}).get("setup_complete") is True
        and ap_password == AP_PASSWORD
        and type(profiles) is list
        and len(profiles) == 1
        and type(station_ssid) is str
        and bool(station_ssid)
        and type(station_password) is str
        and 8 <= len(station_password) <= 64
    )


def _memory_sample():
    import esp32

    _gc.collect()
    gc_free = _gc.mem_free()
    internal = esp32.idf_heap_info((1 << 2) | (1 << 11))
    dma = esp32.idf_heap_info((1 << 2) | (1 << 3) | (1 << 11))

    def summarize(regions):
        free = 0
        largest = 0
        for region in regions:
            free += region[1]
            if region[2] > largest:
                largest = region[2]
        return free, largest

    internal_free, internal_largest = summarize(internal)
    dma_free, dma_largest = summarize(dma)
    values = (gc_free, internal_free, internal_largest, dma_free, dma_largest)
    _require(
        all(type(value) is int and value >= MINIMUM_FREE_BYTES for value in values),
        "one or more memory floors failed",
    )
    return values


class _ObservedWeb:
    __slots__ = (
        "application",
        "controller",
        "protocol",
        "sta_root",
        "sta_security_denied",
        "sta_mutation_denied",
        "captive_redirects",
        "station_ip",
    )

    def __init__(self, application, controller, protocol, station_ip):
        self.application = application
        self.controller = controller
        self.protocol = protocol
        self.sta_root = 0
        self.sta_security_denied = 0
        self.sta_mutation_denied = 0
        self.captive_redirects = 0
        self.station_ip = station_ip

    def handle(self, request, peer_ip, ingress, local_ip):
        response = self.application.handle(request, peer_ip, ingress, local_ip)
        method = getattr(request, "method", None)
        path = getattr(request, "path", None)
        host = request.headers.get("host")
        if ingress == "ap" and path in _CAPTIVE_PATHS:
            if method == "GET" and response.status == 302:
                self.captive_redirects += 1
        elif ingress == "sta" and local_ip == self.station_ip:
            if method == "GET" and path == "/" and host == "heater.local":
                if response.status == 200:
                    self.sta_root += 1
            elif method == "GET" and path == "/api/v1/security-context":
                if response.status == 503:
                    self.sta_security_denied += 1
            elif method == "POST" and path == "/api/v1/heater/stop":
                if response.status in (403, 503):
                    self.sta_mutation_denied += 1
        _require(self.controller.requested_on is False, "Requested State changed")
        _require(self.protocol.calls == 0, "heater protocol was accessed")
        _require(
            not _phase10._contains_password(getattr(response, "body", None)),
            "a credential field leaked into a response",
        )
        return response


def run(confirmation, window_seconds=600):
    if confirmation != DISCOVERY_CONFIRMATION:
        raise RuntimeError("exact Phase-10.1 discovery confirmation is required")
    window_seconds = _window(window_seconds)
    board_config = None
    network_module = None
    port = None
    manager = None
    network_manager = None
    rest_runtime = None
    discovery = None
    gateway = None
    protocol = None
    controller = None
    primary = None
    stage = "preflight"
    production_baseline = None
    samples = []
    dns_answered = 0
    try:
        import time
        import board_config
        import hardware.micropython_wifi as wifi_module
        from adapters.micropython_captive_dns import MicroPythonCaptiveDNS
        from adapters.micropython_http_server import MicroPythonHTTPServer
        from app.configuration_bootstrap import build_configured_runtime
        from app.discovery_composition import ConfiguredDiscoveryRuntime
        from app.heater_controller import HeaterController
        from app.network_composition import build_configured_network
        from app.rest_composition import build_rest_runtime
        from app.scheduler_controller_gateway import SchedulerControllerGateway
        from app.web_application import Phase9WebApplication

        ticks_ms = time.ticks_ms
        ticks_add = time.ticks_add
        ticks_diff = time.ticks_diff
        sleep_ms = time.sleep_ms
        _base._verify_hardware_locks(board_config)
        _base._verify_platform(board_config)
        network_module = _base._load_network_module()
        _require(_base._interfaces_inactive(network_module), "radio was active before gate")
        production_baseline = _integration._stat_signature(
            _integration._production_paths()
        )
        manager = _integration._new_manager()
        configuration = manager.snapshot()["configuration"]
        _require(_configured_for_test(configuration), "isolated network setup is invalid")
        configured_runtime = build_configured_runtime(
            manager, ticks_diff=ticks_diff, ticks_add=ticks_add
        )
        _require(configured_runtime.scheduler.armed is False, "scheduler was armed")
        samples.append(_memory_sample())

        stage = "network_start"
        board_config.WIFI_RADIO_APPROVED = True
        port = wifi_module.open_wifi_from_board_config()
        network_runtime = build_configured_network(
            manager, port, ticks_diff=ticks_diff, ticks_add=ticks_add
        )
        network_manager = network_runtime.manager
        _require(network_manager.start(ticks_ms()) is True, "network did not start")
        deadline = ticks_add(ticks_ms(), window_seconds * 1000)
        startup_deadline = ticks_add(ticks_ms(), STARTUP_TIMEOUT_MS)
        station_ip = None
        while station_ip is None:
            now = ticks_ms()
            if ticks_diff(now, startup_deadline) >= 0:
                raise RuntimeError("station DHCP timed out")
            network_manager.step(now)
            snapshot = network_manager.snapshot()
            network_manager.drain_events()
            _require(snapshot["faulted"] is False, "network manager faulted")
            ap = snapshot["access_point"]
            sta = snapshot["station"]
            if (
                ap["active"] is True
                and ap["ip"] == AP_IP
                and sta["connected"] is True
                and sta["ip"] not in (None, "0.0.0.0")
                and snapshot["mdns"]["ready"] is True
            ):
                station_ip = sta["ip"]
                break
            sleep_ms(POLL_INTERVAL_MS)
        samples.append(_memory_sample())

        stage = "composition"
        protocol = _integration._NullProtocolPort()
        controller = HeaterController(
            protocol,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
            maximum_runtime_minutes=configuration["heater"]["maximum_runtime_minutes"],
            temperature_manager=configured_runtime.temperature_manager,
        )
        scheduler_gateway = SchedulerControllerGateway(
            configured_runtime.scheduler,
            controller,
            ticks_ms=ticks_ms,
            persistence=manager,
        )
        rest_runtime = build_rest_runtime(
            manager,
            configured_runtime,
            controller,
            scheduler_gateway,
            _os.urandom,
            (AP_IP, "heater.local"),
            "ap",
            configured_network_runtime=network_runtime,
            ticks_ms=ticks_ms,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
        )
        _require(rest_runtime.start() is True, "REST security did not start")
        web = Phase9WebApplication(rest_runtime, AP_IP)
        gateway = _ObservedWeb(web, controller, protocol, station_ip)
        ap_http = MicroPythonHTTPServer(
            web,
            AP_IP,
            request_handler=gateway.handle,
            request_ingress="ap",
            request_handler_uses_ingress=True,
            ticks_ms=ticks_ms,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
        )
        station_http = MicroPythonHTTPServer(
            web,
            station_ip,
            request_handler=gateway.handle,
            request_ingress="sta",
            request_handler_uses_ingress=True,
            ticks_ms=ticks_ms,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
        )
        dns = MicroPythonCaptiveDNS(AP_IP)
        discovery = ConfiguredDiscoveryRuntime(ap_http, dns, station_http)
        _require(discovery.start() is True, "discovery services did not start")
        samples.append(_memory_sample())
        print(DISCOVERY_READY_TOKEN)
        print("sta_ip={}".format(station_ip))
        print("home_url=http://heater.local/")
        print("ap_ssid=Landy Heater")
        print("window_seconds={}".format(window_seconds))

        stage = "observe"
        while True:
            now = ticks_ms()
            if ticks_diff(now, deadline) >= 0:
                raise RuntimeError("discovery observation timed out")
            network_manager.step(now)
            network_manager.drain_events()
            snapshot = network_manager.snapshot()
            _require(
                snapshot["faulted"] is False
                and snapshot["access_point"]["active"] is True
                and snapshot["station"]["connected"] is True
                and snapshot["station"]["ip"] == station_ip
                and snapshot["mdns"]["ready"] is True,
                "AP, station or mDNS truth changed",
            )
            discovery.step()
            ds = discovery.snapshot()
            ap_hs = ds["ap_http"]
            sta_hs = ds["station_http"]
            _require(
                ds["faulted"] is False
                and ap_hs["faulted"] is False
                and sta_hs["faulted"] is False
                and ap_hs["parse_errors"] == 0
                and sta_hs["parse_errors"] == 0
                and ap_hs["reentries"] == 0
                and sta_hs["reentries"] == 0,
                "discovery transport faulted",
            )
            if (
                ds["dns"]["answered"] >= 1
                and gateway.captive_redirects >= 1
                and gateway.sta_root >= 1
                and gateway.sta_security_denied >= 1
                and gateway.sta_mutation_denied >= 1
                and ap_hs["client_count"] == 0
                and sta_hs["client_count"] == 0
            ):
                dns_answered = ds["dns"]["answered"]
                break
            sleep_ms(POLL_INTERVAL_MS)
        samples.append(_memory_sample())
        _require(
            _integration._stat_signature(_integration._production_paths())
            == production_baseline,
            "production storage changed",
        )
        stage = "pass"
    except BaseException as error:
        primary = error
        print("PHASE10_1_DISCOVERY_FAILURE_V1")
        print("stage={}".format(stage))
        print("error_type={}".format(type(error).__name__))
        print("error={}".format(str(error)))
        if discovery is not None:
            try:
                print("discovery={}".format(discovery.snapshot()))
            except BaseException:
                print("discovery=unavailable")
    finally:
        if discovery is not None:
            try:
                discovery.deinit()
            except BaseException:
                pass
        if rest_runtime is not None:
            try:
                rest_runtime.deinit()
            except BaseException:
                pass
        radio_ok = True
        if network_manager is not None or port is not None:
            try:
                radio_ok = _base._cleanup_radio(network_manager, port, network_module)
            except BaseException:
                radio_ok = False
        if board_config is not None:
            board_config.WIFI_RADIO_APPROVED = False
        if primary is None:
            _require(radio_ok is True, "radio cleanup failed")
            _require(_integration._remove_isolated_files() is True, "isolated cleanup failed")
            samples.append(_memory_sample())
    if primary is not None:
        print("isolated_retry_state_retained=True")
        print(DISCOVERY_FAIL_TOKEN)
        if isinstance(primary, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise primary
        raise RuntimeError("Phase-10.1 discovery smoke failed") from None
    print("dns_answered={}".format(dns_answered))
    print("captive_redirects={}".format(gateway.captive_redirects))
    print("station_root_reads={}".format(gateway.sta_root))
    print("station_security_denied={}".format(gateway.sta_security_denied))
    print("station_mutation_denied={}".format(gateway.sta_mutation_denied))
    print("memory_samples={}".format(len(samples)))
    print("cleanup_confirmed=True")
    print(DISCOVERY_PASS_TOKEN)
    return {"phase": "10.1", "station_ip": station_ip}


__all__ = (
    "DISCOVERY_CONFIRMATION",
    "DISCOVERY_READY_TOKEN",
    "DISCOVERY_PASS_TOKEN",
    "DISCOVERY_FAIL_TOKEN",
    "run",
)
