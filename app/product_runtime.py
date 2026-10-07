"""Top-level cooperative owner for the configured Landy Heater product.

Construction is inert apart from the explicit production factory at the end
of this module.  ``ProductRuntime.start`` opens each hardware owner in a fixed
order and does not arm timers until AP recovery, REST security and the Web UI
are live.  One call to ``step`` advances each owner at most once.
"""


AP_ADDRESS = "192.168.4.1"
MDNS_HOSTNAME = "heater.local"
STARTUP_TIMEOUT_MS = 30000
LOOP_DELAY_MS = 10


class ProductRuntimeError(RuntimeError):
    pass


def _callable(owner, name):
    value = getattr(owner, name, None)
    if not callable(value):
        raise ValueError("product dependency must provide {}()".format(name))
    return value


class ProductRuntime:
    """Own all live product services and their ordered lifecycle."""

    __slots__ = (
        "_config_manager",
        "_configured",
        "_sensor",
        "_heater",
        "_network_runtime",
        "_network",
        "_rest_factory",
        "_discovery_factory",
        "_station_http_factory",
        "_scheduler_gateway_factory",
        "_ticks_ms",
        "_ticks_diff",
        "_ticks_add",
        "_sleep_ms",
        "_radio_release",
        "_rest",
        "_discovery",
        "_scheduler_gateway",
        "_station_ip",
        "_station_failed_ip",
        "_network_available",
        "_discovery_available",
        "_diagnostics_available",
        "_started",
        "_closed",
        "_faulted",
        "_restart_required",
        "_last_error",
        "_steps",
        "_station_attaches",
        "_station_detaches",
        "_station_attach_failures",
    )

    def __init__(
        self,
        config_manager,
        configured_runtime,
        sensor_runtime,
        heater_runtime,
        network_runtime,
        rest_factory,
        discovery_factory,
        station_http_factory,
        scheduler_gateway_factory,
        ticks_ms,
        ticks_diff,
        ticks_add,
        sleep_ms,
        radio_release=None,
    ):
        for owner, methods in (
            (config_manager, ("status",)),
            (configured_runtime, ("restart_required",)),
            (sensor_runtime, ("start", "step", "deinit", "snapshot")),
            (heater_runtime, ("start", "step", "deinit", "snapshot")),
            (network_runtime, ("restart_required",)),
        ):
            for name in methods:
                _callable(owner, name)
        network = getattr(network_runtime, "manager", None)
        for name in ("start", "step", "deinit", "snapshot"):
            _callable(network, name)
        scheduler = getattr(configured_runtime, "scheduler", None)
        for name in ("arm", "disarm", "snapshot"):
            _callable(scheduler, name)
        for factory in (
            rest_factory,
            discovery_factory,
            station_http_factory,
            scheduler_gateway_factory,
            ticks_ms,
            ticks_diff,
            ticks_add,
            sleep_ms,
        ):
            if not callable(factory):
                raise ValueError("product factory/clock is not callable")
        if radio_release is not None and not callable(radio_release):
            raise ValueError("radio_release must be callable")

        self._config_manager = config_manager
        self._configured = configured_runtime
        self._sensor = sensor_runtime
        self._heater = heater_runtime
        self._network_runtime = network_runtime
        self._network = network
        self._rest_factory = rest_factory
        self._discovery_factory = discovery_factory
        self._station_http_factory = station_http_factory
        self._scheduler_gateway_factory = scheduler_gateway_factory
        self._ticks_ms = ticks_ms
        self._ticks_diff = ticks_diff
        self._ticks_add = ticks_add
        self._sleep_ms = sleep_ms
        self._radio_release = radio_release
        self._rest = None
        self._discovery = None
        self._scheduler_gateway = None
        self._station_ip = None
        self._station_failed_ip = None
        self._network_available = False
        self._discovery_available = False
        self._diagnostics_available = False
        self._started = False
        self._closed = False
        self._faulted = False
        self._restart_required = False
        self._last_error = None
        self._steps = 0
        self._station_attaches = 0
        self._station_detaches = 0
        self._station_attach_failures = 0

    @property
    def started(self):
        return self._started

    @property
    def closed(self):
        return self._closed

    @property
    def faulted(self):
        return self._faulted

    def _require_snapshot(self, owner, name):
        value = owner.snapshot()
        if type(value) is not dict:
            raise ProductRuntimeError("{} snapshot is malformed".format(name))
        return value

    def _safe_deinit(self, owner):
        if owner is None:
            return True
        try:
            return owner.deinit() is None
        except BaseException:
            return False

    def _release_radio_gate(self):
        if self._radio_release is None:
            return True
        try:
            return self._radio_release() is None
        except BaseException:
            return False

    def _cleanup_after_failed_start(self):
        scheduler = self._configured.scheduler
        try:
            scheduler.disarm()
        except BaseException:
            pass
        ok = True
        for owner in (
            self._discovery,
            self._rest,
            self._network,
            self._heater,
            self._sensor,
        ):
            ok = self._safe_deinit(owner) and ok
        ok = self._release_radio_gate() and ok
        self._started = False
        self._closed = True
        return ok

    def start(self):
        if self._closed:
            raise ProductRuntimeError("product runtime is closed")
        if self._faulted:
            raise ProductRuntimeError("product runtime is faulted")
        if self._started:
            return False

        try:
            if self._sensor.start() is not True:
                raise ProductRuntimeError("sensor runtime did not start")
            if self._heater.start() is not True:
                raise ProductRuntimeError("heater runtime did not start")
            now_ms = self._ticks_ms()
            if type(now_ms) is not int:
                raise ProductRuntimeError("product clock is malformed")
            if self._network.start(now_ms) is not True:
                raise ProductRuntimeError("network runtime did not start")
            self._network_available = True

            deadline = self._ticks_add(now_ms, STARTUP_TIMEOUT_MS)
            ap_address = None
            while ap_address is None:
                now_ms = self._ticks_ms()
                self._network.step(now_ms)
                state = self._require_snapshot(self._network, "network")
                if state.get("faulted") is True:
                    raise ProductRuntimeError("network faulted during startup")
                access_point = state.get("access_point")
                if type(access_point) is not dict:
                    raise ProductRuntimeError("AP startup truth is malformed")
                if access_point.get("active") is True:
                    ap_address = access_point.get("ip")
                    if type(ap_address) is not str or not ap_address:
                        raise ProductRuntimeError("active AP has no address")
                    if ap_address != AP_ADDRESS:
                        raise ProductRuntimeError("AP address differs")
                    break
                if self._ticks_diff(now_ms, deadline) >= 0:
                    raise ProductRuntimeError("AP startup timed out")
                self._sleep_ms(LOOP_DELAY_MS)

            controller = getattr(self._heater, "controller", None)
            protocol = getattr(self._heater, "protocol_port", None)
            if controller is None or protocol is None:
                raise ProductRuntimeError("heater ports are unavailable")
            self._scheduler_gateway = self._scheduler_gateway_factory(
                self._configured.scheduler,
                controller,
                self._config_manager,
            )
            _callable(self._scheduler_gateway, "step")
            _callable(self._scheduler_gateway, "snapshot")
            self._rest = self._rest_factory(
                controller,
                self._scheduler_gateway,
                protocol,
            )
            for name in ("start", "step_diagnostics", "deinit", "snapshot"):
                _callable(self._rest, name)
            if self._rest.start() is not True:
                raise ProductRuntimeError("REST security did not start")
            self._diagnostics_available = True
            self._discovery = self._discovery_factory(
                self._rest, ap_address
            )
            for name in (
                "start",
                "step",
                "deinit",
                "snapshot",
                "attach_station_http",
                "detach_station_http",
            ):
                _callable(self._discovery, name)
            if self._discovery.start() is not True:
                raise ProductRuntimeError("Web discovery did not start")
            self._discovery_available = True
            if self._configured.scheduler.arm() is not True:
                raise ProductRuntimeError("scheduler did not arm")
        except BaseException as error:
            self._faulted = True
            self._last_error = "product_start_failed"
            cleanup_ok = self._cleanup_after_failed_start()
            if not cleanup_ok:
                self._last_error = "product_start_cleanup_failed"
            if isinstance(error, MemoryError):
                raise
            raise ProductRuntimeError("product start failed") from None

        self._started = True
        return True

    def _detach_station(self):
        if self._station_ip is None:
            return False
        self._discovery.detach_station_http()
        self._station_ip = None
        self._station_failed_ip = None
        self._station_detaches += 1
        return True

    def _sync_station_listener(self, network_snapshot):
        station = network_snapshot.get("station")
        mdns = network_snapshot.get("mdns")
        if type(station) is not dict or type(mdns) is not dict:
            raise ProductRuntimeError("station discovery truth is malformed")
        desired_ip = None
        if (
            station.get("connected") is True
            and mdns.get("ready") is True
            and type(station.get("ip")) is str
            and station.get("ip") not in ("", "0.0.0.0")
        ):
            desired_ip = station["ip"]

        if self._station_ip is not None and self._station_ip != desired_ip:
            self._detach_station()
        if desired_ip is None:
            self._station_failed_ip = None
            return False
        if self._station_ip == desired_ip:
            return False
        if self._station_failed_ip == desired_ip:
            return False
        try:
            server = self._station_http_factory(
                self._rest, desired_ip, AP_ADDRESS
            )
            if self._discovery.attach_station_http(server) is not True:
                raise ProductRuntimeError("station listener was not attached")
        except BaseException:
            self._station_failed_ip = desired_ip
            self._station_attach_failures += 1
            return False
        self._station_ip = desired_ip
        self._station_failed_ip = None
        self._station_attaches += 1
        return True

    def step(self):
        if not self._started or self._closed:
            return False
        if self._faulted:
            raise ProductRuntimeError("product runtime is faulted")

        now_ms = self._ticks_ms()
        if type(now_ms) is not int:
            self._faulted = True
            self._last_error = "product_clock_failed"
            raise ProductRuntimeError("product clock is malformed")
        restart_required = self._configured.restart_required(
            self._config_manager
        )
        if type(restart_required) is not bool:
            raise ProductRuntimeError("restart gate is malformed")
        if restart_required:
            self._restart_required = True
            self._configured.scheduler.disarm()
        else:
            self._sensor.step()

        network_snapshot = None
        if self._network_available:
            try:
                self._network.step(now_ms)
                network_snapshot = self._require_snapshot(
                    self._network, "network"
                )
                if network_snapshot.get("faulted") is True:
                    raise ProductRuntimeError("network runtime faulted")
                access_point = network_snapshot.get("access_point")
                if type(access_point) is not dict:
                    raise ProductRuntimeError(
                        "recovery AP truth is malformed"
                    )
            except MemoryError:
                raise
            except BaseException:
                self._network_available = False
                self._last_error = "network_runtime_degraded"

        if self._discovery_available:
            try:
                if network_snapshot is not None:
                    self._sync_station_listener(network_snapshot)
                self._discovery.step()
                discovery_snapshot = self._require_snapshot(
                    self._discovery, "discovery"
                )
                if discovery_snapshot.get("faulted") is True:
                    raise ProductRuntimeError("Web discovery faulted")
            except MemoryError:
                raise
            except BaseException:
                self._discovery_available = False
                self._last_error = "web_discovery_degraded"

        if not self._restart_required:
            self._scheduler_gateway.step()
        self._heater.step()
        if self._diagnostics_available:
            try:
                self._rest.step_diagnostics(now_ms)
            except MemoryError:
                raise
            except BaseException:
                self._diagnostics_available = False
                self._last_error = "diagnostics_degraded"
        self._steps += 1
        return True

    def deinit(self):
        if self._closed:
            return None
        self._configured.scheduler.disarm()
        controller = getattr(self._heater, "controller", None)
        if controller is not None and getattr(controller, "requested_on", False):
            controller.request_stop()
            raise ProductRuntimeError("heater stop is pending")
        # The heater owner is the only component that can authoritatively
        # confirm physical OFF.  Never tear down network/sensors and abandon
        # its UART while that confirmation is still pending.
        try:
            self._heater.deinit()
        except BaseException:
            raise ProductRuntimeError("heater is not confirmed off") from None
        failed = False
        for owner in (
            self._discovery,
            self._rest,
            self._network,
            self._sensor,
        ):
            failed = (not self._safe_deinit(owner)) or failed
        failed = (not self._release_radio_gate()) or failed
        self._started = False
        self._closed = True
        if failed:
            self._faulted = True
            self._last_error = "product_cleanup_failed"
            raise ProductRuntimeError("product cleanup failed")
        return None

    def shutdown_step(self):
        """Advance one fail-safe shutdown attempt without abandoning UART."""

        if self._closed:
            return True
        self._configured.scheduler.disarm()
        controller = getattr(self._heater, "controller", None)
        if controller is not None and getattr(controller, "requested_on", False):
            try:
                controller.request_stop()
            except BaseException:
                pass
        try:
            self._heater.step()
        except BaseException:
            pass
        try:
            self.deinit()
        except ProductRuntimeError:
            return False
        return True

    def snapshot(self):
        return {
            "started": self._started,
            "closed": self._closed,
            "faulted": self._faulted,
            "restart_required": self._restart_required,
            "last_error": self._last_error,
            "steps": self._steps,
            "station_ip": self._station_ip,
            "network_available": self._network_available,
            "discovery_available": self._discovery_available,
            "diagnostics_available": self._diagnostics_available,
            "station_attaches": self._station_attaches,
            "station_detaches": self._station_detaches,
            "station_attach_failures": self._station_attach_failures,
        }

    def run_forever(self):
        if not self._started:
            self.start()
        while self._started and not self._closed:
            try:
                self.step()
            except BaseException:
                self._faulted = True
                if self._last_error is None:
                    self._last_error = "product_step_failed"
                raise
            self._sleep_ms(LOOP_DELAY_MS)
        return None


def build_product_runtime():
    """Load trusted production state and construct the real cold owner."""

    import gc
    import os
    import time
    import board_config
    from app.configuration_bootstrap import build_configured_runtime
    from app.discovery_composition import build_discovery_runtime
    from app.heater_composition import build_configured_heater_runtime
    from app.network_composition import build_configured_network
    from app.rest_composition import build_rest_runtime, build_web_http_server
    from app.scheduler_controller_gateway import SchedulerControllerGateway
    from app.sensor_composition import build_configured_sensor_runtime
    from hardware.micropython_wifi import open_wifi_from_board_config
    from services.configuration_storage import create_default_config_manager

    manager = create_default_config_manager()
    if manager.load() is not True:
        raise ProductRuntimeError("trusted production configuration is absent")
    if manager.load_scheduler_checkpoint() is not True:
        raise ProductRuntimeError("trusted scheduler ledger is absent")
    if manager.faulted:
        raise ProductRuntimeError("production storage is faulted")

    configured = build_configured_runtime(
        manager, ticks_diff=time.ticks_diff, ticks_add=time.ticks_add
    )
    sensor = build_configured_sensor_runtime(
        manager, configured, ticks_ms=time.ticks_ms
    )
    heater = build_configured_heater_runtime(
        manager,
        configured,
        ticks_ms=time.ticks_ms,
        ticks_diff=time.ticks_diff,
        ticks_add=time.ticks_add,
    )

    board_config.WIFI_RADIO_APPROVED = True
    port = None
    try:
        port = open_wifi_from_board_config()
        network_runtime = build_configured_network(
            manager,
            port,
            ticks_diff=time.ticks_diff,
            ticks_add=time.ticks_add,
        )
    except BaseException:
        if port is not None:
            try:
                port.deinit()
            except BaseException:
                pass
        board_config.WIFI_RADIO_APPROVED = False
        raise

    def scheduler_gateway_factory(scheduler, controller, persistence):
        return SchedulerControllerGateway(
            scheduler,
            controller,
            ticks_ms=time.ticks_ms,
            persistence=persistence,
        )

    def rest_factory(controller, scheduler_gateway, protocol):
        return build_rest_runtime(
            manager,
            configured,
            controller,
            scheduler_gateway,
            os.urandom,
            (AP_ADDRESS, MDNS_HOSTNAME),
            "ap",
            configured_network_runtime=network_runtime,
            ticks_ms=time.ticks_ms,
            ticks_diff=time.ticks_diff,
            ticks_add=time.ticks_add,
            mem_free=gc.mem_free,
            protocol_transport=protocol,
            protocol_parser=protocol.validate_inbound_frame,
        )

    def discovery_factory(rest_runtime, ap_address):
        return build_discovery_runtime(
            rest_runtime,
            ap_address,
            ticks_ms=time.ticks_ms,
            ticks_diff=time.ticks_diff,
            ticks_add=time.ticks_add,
        )

    def station_http_factory(rest_runtime, station_address, ap_address):
        return build_web_http_server(
            rest_runtime,
            station_address,
            ticks_ms=time.ticks_ms,
            ticks_diff=time.ticks_diff,
            ticks_add=time.ticks_add,
            request_ingress="sta",
            captive_ap_address=ap_address,
        )

    def release_radio():
        board_config.WIFI_RADIO_APPROVED = False
        return None

    try:
        return ProductRuntime(
            manager,
            configured,
            sensor,
            heater,
            network_runtime,
            rest_factory,
            discovery_factory,
            station_http_factory,
            scheduler_gateway_factory,
            time.ticks_ms,
            time.ticks_diff,
            time.ticks_add,
            time.sleep_ms,
            radio_release=release_radio,
        )
    except BaseException:
        try:
            network_runtime.manager.deinit()
        except BaseException:
            pass
        release_radio()
        raise
