"""Cold composition for explicit AP/STA HTTP plus AP-only captive DNS.

This module owns no radio and performs no I/O at import or construction time.
The caller starts Wi-Fi and REST security first, then explicitly starts this
runtime.  One ``step()`` services exactly one configured socket owner, fairly.
"""


class DiscoveryRuntimeError(RuntimeError):
    pass


class ConfiguredDiscoveryRuntime:
    __slots__ = (
        "_ap_http",
        "_station_http",
        "_dns",
        "_services",
        "_service_names",
        "_next_service",
        "_started",
        "_closed",
        "_faulted",
        "_last_error",
    )

    def __init__(self, ap_http_server, dns_server, station_http_server=None):
        owners = [ap_http_server]
        names = ["ap_http"]
        if station_http_server is not None:
            owners.append(station_http_server)
            names.append("station_http")
        owners.append(dns_server)
        names.append("dns")
        for owner in owners:
            methods = ("start", "step", "deinit", "snapshot")
            for method in methods:
                if not callable(getattr(owner, method, None)):
                    raise ValueError("discovery dependency is malformed")
        self._ap_http = ap_http_server
        self._station_http = station_http_server
        self._dns = dns_server
        self._services = tuple(owners)
        self._service_names = tuple(names)
        self._next_service = 0
        self._started = False
        self._closed = False
        self._faulted = False
        self._last_error = None

    @property
    def http_server(self):
        return self._ap_http

    @property
    def ap_http_server(self):
        return self._ap_http

    @property
    def station_http_server(self):
        return self._station_http

    @property
    def dns_server(self):
        return self._dns

    def start(self):
        if self._closed:
            raise DiscoveryRuntimeError("discovery_closed")
        if self._started:
            return False
        started = []
        try:
            for index, service in enumerate(self._services):
                if service.start() is not True:
                    raise DiscoveryRuntimeError(
                        "{}_start_contract_failed".format(
                            self._service_names[index]
                        )
                    )
                started.append(service)
        except BaseException:
            self._faulted = True
            self._last_error = "discovery_start_failed"
            for service in reversed(started):
                try:
                    service.deinit()
                except BaseException:
                    pass
            raise
        self._next_service = 0
        self._started = True
        return True

    def step(self):
        if not self._started or self._closed:
            return False
        service = self._services[self._next_service]
        self._next_service = (self._next_service + 1) % len(self._services)
        try:
            return bool(service.step())
        except MemoryError:
            raise
        except BaseException:
            self._faulted = True
            self._last_error = "discovery_step_failed"
            return False

    def deinit(self):
        self._started = False
        self._closed = True
        failed = False
        for service in reversed(self._services):
            try:
                if service.deinit() is not None:
                    failed = True
            except MemoryError:
                raise
            except BaseException:
                failed = True
        if failed:
            self._faulted = True
            self._last_error = "discovery_cleanup_failed"
            raise DiscoveryRuntimeError("discovery_cleanup_failed")
        return None

    def snapshot(self):
        return {
            "started": self._started,
            "closed": self._closed,
            "faulted": self._faulted,
            "last_error": self._last_error,
            "next_service": self._service_names[self._next_service],
            "ap_http": self._ap_http.snapshot(),
            "station_http": (
                None
                if self._station_http is None
                else self._station_http.snapshot()
            ),
            "dns": self._dns.snapshot(),
        }


def build_discovery_runtime(
    rest_runtime,
    ap_address,
    station_address=None,
    ap_http_socket_factory=None,
    station_http_socket_factory=None,
    dns_socket_factory=None,
    ticks_ms=None,
    ticks_diff=None,
    ticks_add=None,
):
    """Build inert explicit-interface HTTP and AP-DNS socket owners.

    AP-only operation is available by omitting ``station_address``.  When a
    validated DHCP address is supplied, the second HTTP listener binds that
    exact address at the same port 80 and carries fixed read-only STA ingress.
    """

    from adapters.micropython_captive_dns import MicroPythonCaptiveDNS
    from app.rest_composition import build_web_http_server

    ap_http = build_web_http_server(
        rest_runtime,
        ap_address,
        socket_factory=ap_http_socket_factory,
        ticks_ms=ticks_ms,
        ticks_diff=ticks_diff,
        ticks_add=ticks_add,
        request_ingress="ap",
        captive_ap_address=ap_address,
    )
    station_http = None
    if station_address is not None:
        if station_address == ap_address:
            raise ValueError("station_address must differ from ap_address")
        station_http = build_web_http_server(
            rest_runtime,
            station_address,
            socket_factory=station_http_socket_factory,
            ticks_ms=ticks_ms,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
            request_ingress="sta",
            captive_ap_address=ap_address,
        )
    dns = MicroPythonCaptiveDNS(ap_address, socket_factory=dns_socket_factory)
    return ConfiguredDiscoveryRuntime(ap_http, dns, station_http)
