"""Cold, hardware-free composition for the Phase-8 REST application.

The builder wires the existing application models and safety gateways but does
not create a socket, start Wi-Fi, generate a CSRF token or touch the heater
protocol.  The owner explicitly calls :meth:`ConfiguredRestRuntime.start`
before accepting requests and owns the separate HTTP socket adapter.
"""

class ConfiguredRestRuntime:
    """Read-only handles for one cold REST application composition."""

    __slots__ = (
        "_application",
        "_configuration_gateway",
        "_manual_gateway",
        "_security_policy",
        "_rate_limiter",
        "_configuration_generation",
    )

    def __init__(
        self,
        application,
        configuration_gateway,
        manual_gateway,
        security_policy,
        rate_limiter,
        configuration_generation,
    ):
        self._application = application
        self._configuration_gateway = configuration_gateway
        self._manual_gateway = manual_gateway
        self._security_policy = security_policy
        self._rate_limiter = rate_limiter
        self._configuration_generation = configuration_generation

    @property
    def application(self):
        return self._application

    @property
    def configuration_gateway(self):
        return self._configuration_gateway

    @property
    def manual_gateway(self):
        return self._manual_gateway

    @property
    def security_policy(self):
        return self._security_policy

    @property
    def rate_limiter(self):
        return self._rate_limiter

    @property
    def configuration_generation(self):
        return self._configuration_generation

    def start(self):
        """Generate the boot-ephemeral mutation token explicitly."""

        result = self._security_policy.start()
        if type(result) is not bool:
            raise RuntimeError("REST security start result is malformed")
        return result

    def deinit(self):
        """Erase the CSRF token; sockets and Wi-Fi remain separately owned."""

        result = self._security_policy.deinit()
        if result is not None:
            raise RuntimeError("REST security deinit result is malformed")
        return None

    def handle(self, request, peer_ip, ingress=None, local_ip=None):
        """Production handler passed to the peer-aware socket adapter."""

        return self._application.handle(request, peer_ip, ingress, local_ip)

    def snapshot(self):
        return {
            "configuration_generation": self._configuration_generation,
            "security": self._security_policy.snapshot(),
            "rate_limit": self._rate_limiter.snapshot(),
            "application": self._application.snapshot(),
            "configuration_gateway": self._configuration_gateway.snapshot(),
            "manual_gateway": self._manual_gateway.snapshot(),
        }


def _runtime_member(runtime, name):
    try:
        value = getattr(runtime, name)
    except MemoryError:
        raise
    except BaseException:
        raise ValueError(
            "configured_runtime must expose {}".format(name)
        ) from None
    if value is None:
        raise ValueError("configured_runtime must expose {}".format(name))
    return value


def build_rest_runtime(
    config_manager,
    configured_runtime,
    controller,
    scheduler_gateway,
    random_bytes,
    allowed_hosts,
    ingress,
    configured_network_runtime=None,
    ticks_ms=None,
    ticks_diff=None,
    ticks_add=None,
    mem_free=None,
):
    """Build one inert REST runtime from already-constructed application ports.

    Supplying tick helpers is all-or-nothing so every REST/manual-control
    decision uses one coherent wrap-safe clock domain.
    """

    # Keep importing the cold composition root cheap enough for the ESP32 to
    # bring up and verify Wi-Fi first.  The large REST graph becomes resident
    # only when an owner explicitly builds the runtime after that gate.
    from app.configuration_api_gateway import ConfigurationAPIGateway
    from app.manual_control_gateway import ManualControlGateway
    from app.rest_application import RestApplication
    from services.rest_rate_limiter import RestRateLimiter
    from services.rest_security import RestSecurityPolicy

    tick_values = (ticks_ms, ticks_diff, ticks_add)
    if any(value is not None for value in tick_values):
        if not all(callable(value) for value in tick_values):
            raise ValueError(
                "ticks_ms, ticks_diff and ticks_add must be callable together"
            )
    if mem_free is not None and not callable(mem_free):
        raise ValueError("mem_free must be callable")

    generation = getattr(config_manager, "generation", None)
    if type(generation) is not int or generation < 0:
        raise ValueError("config_manager generation is malformed")

    scheduler = _runtime_member(configured_runtime, "scheduler")
    temperature_manager = _runtime_member(
        configured_runtime, "temperature_manager"
    )
    time_service = _runtime_member(configured_runtime, "time_service")
    network_manager = None
    if configured_network_runtime is not None:
        if not callable(
            getattr(configured_network_runtime, "restart_required", None)
        ):
            raise ValueError(
                "configured_network_runtime must provide restart_required()"
            )
        network_manager = _runtime_member(
            configured_network_runtime, "manager"
        )

    configuration_gateway = ConfigurationAPIGateway(
        config_manager,
        scheduler,
        configured_runtime=configured_runtime,
        configured_network_runtime=configured_network_runtime,
    )
    manual_gateway = ManualControlGateway(
        controller,
        scheduler_gateway,
        config_manager,
        configured_runtime,
        ticks_ms=ticks_ms,
        ticks_add=ticks_add,
    )
    security_policy = RestSecurityPolicy(
        random_bytes,
        allowed_hosts,
        ingress,
    )
    rate_limiter = RestRateLimiter(ticks_diff=ticks_diff)
    application = RestApplication(
        configuration_gateway,
        manual_gateway,
        config_manager,
        configured_runtime,
        controller,
        temperature_manager,
        time_service,
        scheduler,
        scheduler_gateway,
        security_policy,
        network_manager=network_manager,
        ticks_ms=ticks_ms,
        ticks_diff=ticks_diff,
        mem_free=mem_free,
        rate_limiter=rate_limiter,
    )

    if getattr(config_manager, "generation", None) != generation:
        raise RuntimeError("configuration changed during REST composition")
    return ConfiguredRestRuntime(
        application,
        configuration_gateway,
        manual_gateway,
        security_policy,
        rate_limiter,
        generation,
    )


def build_rest_http_server(
    rest_runtime,
    ap_bind_address,
    port=80,
    socket_factory=None,
    ticks_ms=None,
    ticks_diff=None,
    ticks_add=None,
):
    """Build an inert AP-bound server with mandatory peer-aware dispatch.

    The adapter import is lazy and its constructor performs no socket I/O.  A
    caller still invokes ``start()`` explicitly after Wi-Fi and REST security
    are ready.
    """

    if not callable(getattr(rest_runtime, "handle", None)):
        raise ValueError("rest_runtime must provide handle(request, peer_ip)")
    application = getattr(rest_runtime, "application", None)
    if application is None:
        raise ValueError("rest_runtime must expose application")

    from adapters.micropython_http_server import MicroPythonHTTPServer

    return MicroPythonHTTPServer(
        application,
        ap_bind_address,
        port=port,
        socket_factory=socket_factory,
        request_handler=rest_runtime.handle,
        ticks_ms=ticks_ms,
        ticks_diff=ticks_diff,
        ticks_add=ticks_add,
    )


def build_web_http_server(
    rest_runtime,
    bind_address,
    port=80,
    socket_factory=None,
    ticks_ms=None,
    ticks_diff=None,
    ticks_add=None,
    request_ingress=None,
    captive_ap_address=None,
):
    """Build one inert, explicitly bound UI/API listener.

    ``request_ingress`` is an immutable property of this concrete listener.
    Discovery composes one AP listener and, after DHCP, one station listener;
    both use port 80 without relying on unavailable socket introspection.
    """

    if not callable(getattr(rest_runtime, "handle", None)):
        raise ValueError("rest_runtime must provide handle(request, peer_ip)")
    from adapters.micropython_http_server import MicroPythonHTTPServer
    from app.web_application import Phase9WebApplication

    if request_ingress not in (None, "ap", "sta"):
        raise ValueError("request_ingress must be ap, sta or None")
    if captive_ap_address is None:
        captive_ap_address = bind_address
    application = Phase9WebApplication(rest_runtime, captive_ap_address)
    return MicroPythonHTTPServer(
        application,
        bind_address,
        port=port,
        socket_factory=socket_factory,
        request_handler=application.handle,
        request_ingress=request_ingress,
        request_handler_uses_ingress=request_ingress is not None,
        ticks_ms=ticks_ms,
        ticks_diff=ticks_diff,
        ticks_add=ticks_add,
    )
