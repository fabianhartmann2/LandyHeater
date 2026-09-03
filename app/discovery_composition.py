"""Cold composition for one HTTP listener plus AP-only captive DNS.

This module owns no radio and performs no I/O at import or construction time.
The caller starts Wi-Fi and REST security first, then explicitly starts this
runtime.  One ``step()`` services either HTTP or DNS, alternating fairly.
"""


class DiscoveryRuntimeError(RuntimeError):
    pass


class ConfiguredDiscoveryRuntime:
    __slots__ = (
        "_http",
        "_dns",
        "_next_service",
        "_started",
        "_closed",
        "_faulted",
        "_last_error",
    )

    def __init__(self, http_server, dns_server):
        for owner, methods in (
            (http_server, ("start", "step", "deinit", "snapshot")),
            (dns_server, ("start", "step", "deinit", "snapshot")),
        ):
            for method in methods:
                if not callable(getattr(owner, method, None)):
                    raise ValueError("discovery dependency is malformed")
        self._http = http_server
        self._dns = dns_server
        self._next_service = 0
        self._started = False
        self._closed = False
        self._faulted = False
        self._last_error = None

    @property
    def http_server(self):
        return self._http

    @property
    def dns_server(self):
        return self._dns

    def start(self):
        if self._closed:
            raise DiscoveryRuntimeError("discovery_closed")
        if self._started:
            return False
        http_started = False
        try:
            http_started = self._http.start()
            if http_started is not True:
                raise DiscoveryRuntimeError("http_start_contract_failed")
            if self._dns.start() is not True:
                raise DiscoveryRuntimeError("dns_start_contract_failed")
        except BaseException:
            self._faulted = True
            self._last_error = "discovery_start_failed"
            if http_started:
                try:
                    self._http.deinit()
                except BaseException:
                    pass
            raise
        self._next_service = 0
        self._started = True
        return True

    def step(self):
        if not self._started or self._closed:
            return False
        service = self._http if self._next_service == 0 else self._dns
        self._next_service = 1 - self._next_service
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
        for service in (self._dns, self._http):
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
            "next_service": "http" if self._next_service == 0 else "dns",
            "http": self._http.snapshot(),
            "dns": self._dns.snapshot(),
        }


def build_discovery_runtime(
    rest_runtime,
    ap_address,
    http_socket_factory=None,
    dns_socket_factory=None,
    ticks_ms=None,
    ticks_diff=None,
    ticks_add=None,
):
    """Build one inert multi-interface HTTP/AP-DNS runtime."""

    from adapters.micropython_captive_dns import MicroPythonCaptiveDNS
    from app.rest_composition import build_web_http_server

    http = build_web_http_server(
        rest_runtime,
        ap_address,
        socket_factory=http_socket_factory,
        ticks_ms=ticks_ms,
        ticks_diff=ticks_diff,
        ticks_add=ticks_add,
        station_access=True,
    )
    dns = MicroPythonCaptiveDNS(ap_address, socket_factory=dns_socket_factory)
    return ConfiguredDiscoveryRuntime(http, dns)
