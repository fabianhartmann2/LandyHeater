"""Small persistent ownership seam for the late Phase-8 REST stages.

This VFS module is imported only after the Stage-1 DHCP/TCP proof.  The
disposable preparation module publishes only frozen production objects into
the preallocated context below.  The production HTTP adapter then performs
the real bind through this socket facade.  Its listen call loads and seals the
read-only proof before delegating the real listen operation.
"""

import gc as _gc
import os as _os


AP_IP = "192.168.4.1"
MINIMUM_FREE_HEAP_BYTES = 32 * 1024
MINIMUM_PRE_BIND_HEAP_BYTES = 40 * 1024
_PROOF_MODULE = "tools.phase8_full_rest_phone_stage2"
_DIAGNOSTIC_ERRNO_LIMIT = 65535
_ABORTED_ACCEPT_ERRNO = 113
_OWNED_FILES = tuple(
    base + suffix
    for base in (
        "/phase8_full_rest_phone_smoke_v1_config",
        "/phase8_full_rest_phone_smoke_v1_ledger",
    )
    for suffix in (".a", ".b", ".tmp")
)


def _require(condition, message):
    if not condition:
        raise RuntimeError("Phase-8 full REST phone smoke failed: {}".format(
            message
        ))


def memory_free():
    collector = getattr(_gc, "collect", None)
    reader = getattr(_gc, "mem_free", None)
    _require(callable(collector) and callable(reader), "GC heap API is missing")
    collector()
    value = reader()
    _require(type(value) is int and value >= 0, "GC heap result is invalid")
    return value


def require_heap(value, minimum, checkpoint):
    _require(
        type(value) is int and value >= minimum,
        "free heap is below the {} boundary".format(checkpoint),
    )
    return value


def _bounded_os_errno(error):
    if not isinstance(error, OSError):
        return -1
    value = getattr(error, "errno", None)
    if type(value) is not int:
        arguments = getattr(error, "args", None)
        if type(arguments) is tuple and arguments and type(arguments[0]) is int:
            value = arguments[0]
    if type(value) is int and 0 <= value <= _DIAGNOSTIC_ERRNO_LIMIT:
        return value
    return -1


class ProductHandles:
    __slots__ = (
        "support", "board_config", "wifi_module", "AtomicJSONConfigStore",
        "ConfigManager", "default_configuration", "default_scheduler_ledger",
        "build_configured_runtime", "ConfiguredNetworkRuntime",
        "SchedulerControllerGateway", "HeaterController", "ticks_ms",
        "ticks_add", "ticks_diff", "sleep_ms",
    )


class CountedSystemRandom:
    __slots__ = ("calls", "last_count", "secret")

    def __init__(self):
        self.calls = 0
        self.last_count = None
        self.secret = None

    def __call__(self, count):
        self.calls += 1
        self.last_count = count
        value = _os.urandom(count)
        self.secret = bytearray(value)
        return value

    def clear(self):
        if self.secret is not None:
            for index in range(len(self.secret)):
                self.secret[index] = 0
        return None


class NullProtocolPort:
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


class PreparedContext:
    """Preallocated cross-stage state; deliberately contains no server."""

    __slots__ = (
        "core", "filesystem", "config_manager", "configured_runtime",
        "network_runtime", "network_manager", "port", "network_module",
        "controller", "protocol_port", "scheduler_gateway", "rest_runtime",
        "random_provider", "gateway", "socket_observer", "storage_owned",
        "storage_write_baseline", "production_stat_baseline",
        "memory_after_product_imports", "memory_after_configuration_adoption",
        "memory_before_http_start", "memory_after_proof_before_listen",
        "observation_deadline", "password", "window_seconds",
        "failure_stage",
    )

    def __init__(self):
        for name in PreparedContext.__slots__:
            setattr(self, name, None)
        self.storage_owned = False


class DeferredReadOnlyRuntime:
    """Public builder shape whose dispatch target is armed exactly once."""

    __slots__ = ("application", "_gateway", "_armed", "_sealed")

    def __init__(self):
        self.application = None
        self._gateway = None
        self._armed = False
        self._sealed = False

    @property
    def armed(self):
        return self._armed

    def seal_security(self, runtime):
        _require(not self._sealed and not self._armed, "REST gate was already sealed")
        security = runtime.security_policy.snapshot()
        _require(
            security.get("started") is True
            and security.get("mutation_api_available") is True,
            "REST security is not ready before HTTP bind",
        )
        # The production adapter retains both application.handle and the
        # explicit request_handler.  Point both at this same fail-closed gate;
        # never leave a direct production-application fallback reachable.
        self.application = self
        self._sealed = True
        return None

    def arm(self, gateway):
        _require(self._sealed and not self._armed, "REST gate arm is invalid")
        _require(callable(getattr(gateway, "handle", None)), "REST gateway is invalid")
        self._gateway = gateway
        self._armed = True
        return None

    def disarm(self):
        self._armed = False
        self._gateway = None
        self.application = None
        self._sealed = False
        return None

    def handle(self, request, peer_ip):
        if not self._armed or self._gateway is None:
            raise RuntimeError("REST request gate is not armed")
        return self._gateway.handle(request, peer_ip)


class Stage2State:
    __slots__ = (
        "context", "gate", "socket_factory", "server", "proof_loaded",
        "cleanup_confirmed",
    )

    def __init__(self):
        self.context = PreparedContext()
        self.gate = DeferredReadOnlyRuntime()
        self.socket_factory = None
        self.server = None
        self.proof_loaded = False
        self.cleanup_confirmed = False


def _load_proof():
    from tools import phase8_full_rest_phone_stage2

    return phase8_full_rest_phone_stage2


class LateListenerSocket:
    """Own the raw listener and load proof only after its successful bind."""

    __slots__ = ("_factory", "_port", "_orphan_port")

    def __init__(self, factory):
        self._factory = factory
        self._port = None
        self._orphan_port = None

    @property
    def active(self):
        return self._port is not None or self._orphan_port is not None

    def claim(self, port):
        if self._port is not None:
            self._factory._mark_fault()
            raise RuntimeError("observed listener is already active")
        self._port = port
        return self

    def setblocking(self, value):
        self._factory._begin_operation()
        try:
            setter = getattr(self._port, "setblocking", None)
            if callable(setter):
                result = setter(value)
            else:
                setter = getattr(self._port, "settimeout", None)
                if not callable(setter):
                    raise AttributeError("listener has no nonblocking API")
                result = setter(0 if value is False else None)
            self._factory._require_clean_callback()
            self._factory.setblocking_returned = 1
            return result
        except BaseException as error:
            self._factory._record_listener_failure(error)
            raise
        finally:
            self._factory._end_operation()

    def bind(self, address):
        self._factory._begin_operation()
        try:
            result = self._port.bind(address)
            self._factory._require_clean_callback()
            self._factory.bind_returned = 1
            return result
        except BaseException as error:
            self._factory._record_listener_failure(error)
            raise
        finally:
            self._factory._end_operation()

    def listen(self, backlog):
        self._factory._begin_operation()
        try:
            self._factory._prepare_proof()
            result = self._port.listen(backlog)
            self._factory._require_clean_callback()
            self._factory.listen_returned = 1
            return result
        except BaseException as error:
            self._factory._record_listener_failure(error)
            raise
        finally:
            self._factory._end_operation()

    def accept(self):
        self._factory._begin_operation()
        try:
            if not self._factory.gate.armed or self._factory.observer is None:
                self._factory._mark_fault()
                raise RuntimeError("HTTP accept attempted before proof arm")
            if self._orphan_port is not None:
                self._factory._mark_fault()
                raise RuntimeError("accepted raw socket ownership is occupied")
            try:
                accepted = self._port.accept()
            except OSError as error:
                code = _bounded_os_errno(error)
                if (
                    code == _ABORTED_ACCEPT_ERRNO
                    and not self._factory._listener_failure_recorded
                ):
                    # Preserve a transient abort for a successful run, but do
                    # not make it sticky: a later fatal accept error must win.
                    self._factory.listener_errno = code
                elif code != 11 and code != 35 and code != 10035:
                    self._factory._record_listener_failure(error)
                raise
            if type(accepted) not in (tuple, list):
                self._factory._mark_fault()
                raise RuntimeError("accepted socket result is malformed")
            if not accepted:
                self._factory._mark_fault()
                raise RuntimeError("accepted socket result is empty")
            # Publish the identifiable raw socket before any callback-state or
            # exact-shape validation can raise or allocate.
            self._orphan_port = accepted[0]
            self._factory._require_clean_callback()
            if len(accepted) != 2:
                self._factory._mark_fault()
                raise RuntimeError("accepted socket result is malformed")
            client = self._factory.observer.claim_client(self._orphan_port)
            self._orphan_port = None
            try:
                if type(accepted) is list:
                    accepted[0] = client
                    return accepted
                return (client, accepted[1])
            except BaseException:
                # The observer owns the raw client once orphan is cleared.
                try:
                    client._close_unguarded()
                except BaseException:
                    pass
                raise
        finally:
            self._factory._end_operation()

    def _close_unguarded(self):
        first_error = None
        invalid_result = None
        for name in ("_port", "_orphan_port"):
            port = getattr(self, name)
            if port is None:
                continue
            try:
                result = port.close()
                if result is None:
                    setattr(self, name, None)
                else:
                    invalid_result = result
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
        return invalid_result

    def close(self):
        self._factory._begin_operation()
        try:
            return self._close_unguarded()
        finally:
            self._factory._end_operation()


class LateSocketFactory:
    __slots__ = (
        "_factory", "_state", "_orphan_listener", "_operation_active",
        "_reentries", "_faulted", "observer", "gate", "listener", "calls",
        "factory_returned", "setblocking_returned", "bind_returned",
        "listen_returned", "listener_errno", "_listener_failure_recorded",
    )

    def __init__(self, factory, state):
        _require(callable(factory), "real socket factory must be callable")
        self._factory = factory
        self._state = state
        self._orphan_listener = None
        self._operation_active = False
        self._reentries = 0
        self._faulted = False
        self.observer = None
        self.gate = state.gate
        self.listener = LateListenerSocket(self)
        self.calls = 0
        self.factory_returned = 0
        self.setblocking_returned = 0
        self.bind_returned = 0
        self.listen_returned = 0
        self.listener_errno = -1
        self._listener_failure_recorded = False

    def _mark_fault(self):
        self._faulted = True
        if self.observer is not None:
            self.observer._mark_fault()

    def _begin_operation(self):
        if self._operation_active:
            self._reentries += 1
            self._mark_fault()
            raise RuntimeError("observed socket operation reentered")
        self._operation_active = True

    def _end_operation(self):
        self._operation_active = False

    def _record_listener_failure(self, error):
        if not self._listener_failure_recorded:
            self._listener_failure_recorded = True
            self.listener_errno = _bounded_os_errno(error)

    def _require_clean_callback(self):
        _require(
            self._operation_active is True
            and self._faulted is False
            and self._reentries == 0,
            "observed socket callback reentered",
        )

    def _prepare_proof(self):
        _require(
            self.bind_returned == 1 and self.listen_returned == 0,
            "proof loader ran outside the bound listener phase",
        )
        context = self._state.context
        security = context.rest_runtime.security_policy.snapshot()
        _require(
            security.get("started") is True
            and security.get("mutation_api_available") is True,
            "REST security changed before proof load",
        )
        proof = _load_proof()
        proof.prepare_proof(context)
        self._require_clean_callback()
        _require(
            context.gateway is not None and context.socket_observer is not None,
            "proof stage did not publish its owners",
        )
        self.observer = context.socket_observer
        context.memory_after_proof_before_listen = require_heap(
            memory_free(), MINIMUM_FREE_HEAP_BYTES, "proof-before-listen"
        )
        self._state.proof_loaded = True
        # Arm is the final publication before the real listen call.
        self.gate.arm(context.gateway)

    def __call__(self):
        self._begin_operation()
        try:
            _require(self._orphan_listener is None, "raw listener ownership is occupied")
            self.calls += 1
            try:
                port = self._factory()
            except BaseException as error:
                self._record_listener_failure(error)
                raise
            # Own the raw listener before any validation can raise.
            self._orphan_listener = port
            self.factory_returned = 1
            self._require_clean_callback()
            try:
                result = self.listener.claim(port)
            except BaseException as error:
                self._record_listener_failure(error)
                close = getattr(port, "close", None)
                if callable(close):
                    try:
                        if close() is None:
                            self._orphan_listener = None
                    except BaseException:
                        pass
                raise
            self._orphan_listener = None
            return result
        finally:
            self._end_operation()

    def deinit(self):
        self._begin_operation()
        try:
            first_error = None
            try:
                if self.listener._close_unguarded() is not None:
                    first_error = RuntimeError("observed listener close contract failed")
            except BaseException as error:
                first_error = error
            if self.observer is not None:
                for client in self.observer.clients:
                    try:
                        if client._close_unguarded() is not None and first_error is None:
                            first_error = RuntimeError("observed client close contract failed")
                    except BaseException as error:
                        if first_error is None:
                            first_error = error
            if self._orphan_listener is not None:
                try:
                    if self._orphan_listener.close() is None:
                        self._orphan_listener = None
                except BaseException as error:
                    if first_error is None:
                        first_error = error
            if first_error is not None:
                raise first_error
            return None
        finally:
            self._end_operation()


def real_socket_factory():
    import socket

    return socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def _retry_deinit(owner, validator=None):
    if owner is None:
        return True
    for _ in range(2):
        try:
            result = owner.deinit()
            if result is not None:
                continue
            if validator is None or validator():
                return True
        except BaseException:
            pass
    return False


def _remove_owned_files(context):
    if context is None or context.storage_owned is not True:
        return True
    filesystem = _os if context.filesystem is None else context.filesystem
    clean = True
    for path in _OWNED_FILES:
        try:
            filesystem.remove(path)
        except OSError as error:
            code = getattr(error, "errno", None)
            if code is None and getattr(error, "args", None):
                code = error.args[0]
            if code != 2:
                clean = False
        except BaseException:
            clean = False
    for path in _OWNED_FILES:
        try:
            filesystem.stat(path)
            clean = False
        except OSError as error:
            code = getattr(error, "errno", None)
            if code is None and getattr(error, "args", None):
                code = error.args[0]
            if code != 2:
                clean = False
        except BaseException:
            clean = False
    return clean


def fallback_cleanup(capsule, state):
    """Best-effort idempotent outer cleanup; never grants acceptance."""

    if state is None:
        return False
    context = state.context
    server_ok = _retry_deinit(
        state.server,
        lambda: (
            state.server.snapshot().get("closed") is True
            and state.server.snapshot().get("started") is False
            and state.server.snapshot().get("client_count") == 0
        ),
    )
    factory_ok = _retry_deinit(
        state.socket_factory,
        lambda: (
            state.socket_factory.listener.active is False
            and state.socket_factory._orphan_listener is None
        ),
    )
    try:
        state.gate.disarm()
        gate_ok = state.gate.armed is False
    except BaseException:
        gate_ok = False
    gateway = context.gateway
    try:
        if gateway is not None:
            gateway.clear_secret()
        gateway_ok = True
    except BaseException:
        gateway_ok = False
    random_provider = context.random_provider
    try:
        if random_provider is not None:
            random_provider.clear()
        random_ok = True
    except BaseException:
        random_ok = False
    rest_runtime = context.rest_runtime
    rest_ok = _retry_deinit(
        rest_runtime,
        lambda: rest_runtime.security_policy.snapshot().get(
            "mutation_api_available"
        ) is False,
    )
    support = capsule.support
    try:
        radio_ok = bool(support._cleanup_radio(
            capsule.network_manager, capsule.port, capsule.network_module
        ))
    except BaseException:
        radio_ok = False
    try:
        capsule.board_config.WIFI_RADIO_APPROVED = False
        approval_ok = capsule.board_config.WIFI_RADIO_APPROVED is False
    except BaseException:
        approval_ok = False
    wifi_module = capsule.wifi_module
    lease_ok = (
        wifi_module is not None
        and getattr(wifi_module, "_WIFI_LEASED", None) is False
        and getattr(wifi_module, "_WIFI_LEASE_POISONED", None) is False
    )
    files_ok = _remove_owned_files(context)
    clean = bool(
        server_ok and factory_ok and gate_ok and gateway_ok and random_ok
        and rest_ok and radio_ok and approval_ok and lease_ok and files_ok
    )
    state.cleanup_confirmed = clean
    if clean:
        capsule.owner_state = "released"
    return clean
