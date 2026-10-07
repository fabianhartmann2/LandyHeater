"""Cold lifecycle owner for product heater protocol synchronization.

Construction performs no hardware access. ``start()`` alone opens the guarded
protocol service and creates ``HeaterController`` with requested OFF state.
Each ``step()`` polls RX before advancing the sole control authority.  The
default product factory remains unavailable while board TX locks are closed.
"""

import time as _time


class HeaterRuntimeError(RuntimeError):
    pass


def _plain_ticks_ms():
    return 0


def _plain_ticks_diff(newer, older):
    return newer - older


def _plain_ticks_add(ticks, delta):
    return ticks + delta


_platform_ticks_ms = getattr(_time, "ticks_ms", _plain_ticks_ms)
_platform_ticks_diff = getattr(_time, "ticks_diff", _plain_ticks_diff)
_platform_ticks_add = getattr(_time, "ticks_add", _plain_ticks_add)


def _require_generation(value):
    if type(value) is not int or value < 0:
        raise ValueError("configuration generation is malformed")
    return value


def _open_product_protocol_service():
    from app.composition import open_tx_enabled_protocol_service

    return open_tx_enabled_protocol_service()


def _build_product_controller(
    protocol_port,
    maximum_runtime_minutes,
    temperature_manager,
    ticks_diff,
    ticks_add,
):
    from app.heater_controller import HeaterController

    return HeaterController(
        protocol_port,
        ticks_diff=ticks_diff,
        ticks_add=ticks_add,
        maximum_runtime_minutes=maximum_runtime_minutes,
        temperature_manager=temperature_manager,
    )


class ConfiguredHeaterRuntime:
    """Own one protocol service/controller pair and its safe lifecycle."""

    __slots__ = (
        "_config_manager",
        "_configured_runtime",
        "_configuration_generation",
        "_protocol_factory",
        "_controller_factory",
        "_maximum_runtime_minutes",
        "_temperature_manager",
        "_ticks_ms",
        "_ticks_diff",
        "_ticks_add",
        "_protocol",
        "_controller",
        "_started",
        "_closed",
        "_cleanup_complete",
        "_faulted",
        "_restart_pending",
        "_last_error",
        "_starts",
        "_steps",
        "_rx_frames",
        "_controller_operations",
        "_cleanup_errors",
    )

    def __init__(
        self,
        config_manager,
        configured_runtime,
        configuration_generation,
        protocol_factory,
        controller_factory,
        maximum_runtime_minutes,
        temperature_manager,
        ticks_ms,
        ticks_diff,
        ticks_add,
    ):
        self._config_manager = config_manager
        self._configured_runtime = configured_runtime
        self._configuration_generation = configuration_generation
        self._protocol_factory = protocol_factory
        self._controller_factory = controller_factory
        self._maximum_runtime_minutes = maximum_runtime_minutes
        self._temperature_manager = temperature_manager
        self._ticks_ms = ticks_ms
        self._ticks_diff = ticks_diff
        self._ticks_add = ticks_add
        self._protocol = None
        self._controller = None
        self._started = False
        self._closed = False
        self._cleanup_complete = False
        self._faulted = False
        self._restart_pending = False
        self._last_error = None
        self._starts = 0
        self._steps = 0
        self._rx_frames = 0
        self._controller_operations = 0
        self._cleanup_errors = 0

    @property
    def started(self):
        return self._started

    @property
    def closed(self):
        return self._closed

    @property
    def faulted(self):
        return self._faulted

    @property
    def controller(self):
        return self._controller

    @property
    def protocol_port(self):
        return self._protocol

    def restart_required(self, config_manager=None):
        if config_manager is None:
            config_manager = self._config_manager
        generation = _require_generation(
            getattr(config_manager, "generation", None)
        )
        checker = getattr(self._configured_runtime, "restart_required", None)
        if not callable(checker):
            raise HeaterRuntimeError("configured runtime restart gate is missing")
        required = checker(config_manager)
        if type(required) is not bool:
            raise HeaterRuntimeError("configured runtime restart gate is malformed")
        return generation != self._configuration_generation or required

    @staticmethod
    def _validate_protocol(protocol):
        for name in (
            "poll_inbound",
            "validate_inbound_frame",
            "transport_status",
            "drain_activity",
            "reset_inbound",
            "request_initialization",
            "request_status",
            "request_start",
            "request_shutdown",
            "deinit",
        ):
            if not callable(getattr(protocol, name, None)):
                raise HeaterRuntimeError("protocol service is malformed")
        status = protocol.transport_status()
        if type(status) is not dict:
            raise HeaterRuntimeError("protocol status is malformed")
        return protocol

    @staticmethod
    def _validate_controller(controller):
        for name in (
            "step",
            "handle_frame",
            "snapshot",
            "report_communication_error",
            "request_stop",
        ):
            if not callable(getattr(controller, name, None)):
                raise HeaterRuntimeError("heater controller is malformed")
        snapshot = controller.snapshot()
        if type(snapshot) is not dict:
            raise HeaterRuntimeError("heater controller snapshot is malformed")
        requested = snapshot.get("requested")
        if type(requested) is not dict or requested.get("on") is not False:
            raise HeaterRuntimeError("heater controller did not start requested OFF")
        return controller

    def _cleanup_protocol(self):
        protocol = self._protocol
        self._started = False
        if protocol is None:
            self._cleanup_complete = True
            return True
        last_error = None
        for _ in range(2):
            try:
                protocol.deinit()
                self._cleanup_complete = True
                return True
            except MemoryError:
                raise
            except BaseException as error:
                last_error = error
        self._cleanup_errors += 1
        self._cleanup_complete = False
        self._faulted = True
        self._last_error = "heater_cleanup_failed"
        if isinstance(last_error, HeaterRuntimeError):
            raise last_error
        raise HeaterRuntimeError("heater cleanup failed") from None

    def start(self):
        if self._closed:
            raise HeaterRuntimeError("heater runtime is closed")
        if self._faulted:
            raise HeaterRuntimeError("heater runtime is faulted")
        if self._started:
            return False
        if self.restart_required():
            raise HeaterRuntimeError("heater configuration changed before start")

        protocol = None
        controller = None
        try:
            protocol = self._protocol_factory()
            self._protocol = protocol
            self._cleanup_complete = False
            self._validate_protocol(protocol)
            controller = self._controller_factory(
                protocol,
                self._maximum_runtime_minutes,
                self._temperature_manager,
                self._ticks_diff,
                self._ticks_add,
            )
            self._controller = controller
            self._validate_controller(controller)
            if self.restart_required():
                raise HeaterRuntimeError(
                    "heater configuration changed during start"
                )
        except MemoryError:
            self._protocol = protocol
            if protocol is not None:
                try:
                    self._cleanup_protocol()
                except BaseException:
                    pass
            self._faulted = True
            self._last_error = "heater_start_failed"
            raise
        except BaseException:
            self._protocol = protocol
            cleanup_ok = True
            if protocol is not None:
                try:
                    cleanup_ok = self._cleanup_protocol()
                except BaseException:
                    cleanup_ok = False
            self._controller = controller
            self._faulted = True
            if cleanup_ok:
                self._last_error = "heater_start_failed"
            raise HeaterRuntimeError("heater start failed") from None

        self._started = True
        self._starts += 1
        return True

    def _request_restart_stop(self):
        if self._restart_pending:
            return
        self._restart_pending = True
        try:
            self._controller.request_stop()
        except BaseException:
            self._faulted = True
            self._last_error = "heater_restart_stop_failed"
            raise HeaterRuntimeError("heater restart stop failed") from None

    def step(self):
        if self._closed or not self._started:
            return False
        if self._faulted:
            raise HeaterRuntimeError("heater runtime is faulted")
        if self.restart_required():
            self._request_restart_stop()

        now_ms = self._ticks_ms()
        if type(now_ms) is not int:
            self._faulted = True
            self._last_error = "heater_clock_failed"
            self._cleanup_protocol()
            raise HeaterRuntimeError("heater clock is malformed")

        frames = []
        try:
            frames = self._protocol.poll_inbound(now_ms)
            if type(frames) not in (list, tuple):
                raise HeaterRuntimeError("protocol poll result is malformed")
        except MemoryError:
            self._faulted = True
            self._last_error = "heater_poll_failed"
            self._cleanup_protocol()
            raise
        except BaseException:
            self._controller.report_communication_error(
                "protocol poll failed", now_ms
            )
            frames = []

        try:
            handled = 0
            for frame in frames:
                if self._controller.handle_frame(frame, now_ms):
                    handled += 1
            status = self._protocol.transport_status()
            if type(status) is not dict:
                raise HeaterRuntimeError("protocol status is malformed")
            if status.get("rx_faulted") is True:
                self._controller.report_communication_error(
                    "protocol RX faulted", now_ms
                )
            operations = self._controller.step(now_ms)
            if type(operations) not in (list, tuple) or len(operations) > 1:
                raise HeaterRuntimeError("controller step result is malformed")
        except MemoryError:
            self._faulted = True
            self._last_error = "heater_step_failed"
            self._cleanup_protocol()
            raise
        except BaseException:
            self._faulted = True
            self._last_error = "heater_step_failed"
            self._cleanup_protocol()
            raise HeaterRuntimeError("heater step failed") from None

        self._steps += 1
        self._rx_frames += len(frames)
        self._controller_operations += len(operations)
        return bool(frames or operations or handled)

    def _confirmed_safe_to_close(self):
        if self._controller is None or self._steps == 0:
            return True
        snapshot = self._controller.snapshot()
        if type(snapshot) is not dict:
            return False
        requested = snapshot.get("requested")
        actual = snapshot.get("actual")
        return (
            type(requested) is dict
            and requested.get("on") is False
            and type(actual) is dict
            and actual.get("synchronized") is True
            and actual.get("heater_state") == "off"
        )

    def deinit(self):
        if self._closed and self._cleanup_complete:
            return None
        if self._started and not self._confirmed_safe_to_close():
            try:
                self._controller.request_stop()
            except BaseException:
                self._faulted = True
                self._last_error = "heater_stop_before_close_failed"
            raise HeaterRuntimeError("heater is not confirmed off")
        self._closed = True
        self._cleanup_protocol()
        return None

    def snapshot(self):
        controller = None
        protocol = None
        if self._controller is not None:
            try:
                controller = self._controller.snapshot()
                if type(controller) is not dict:
                    raise ValueError
            except BaseException:
                controller = {"available": False}
        if self._protocol is not None:
            try:
                protocol = self._protocol.transport_status()
                if type(protocol) is not dict:
                    raise ValueError
                protocol = dict(protocol)
            except BaseException:
                protocol = {"available": False}
        return {
            "configuration_generation": self._configuration_generation,
            "restart_required": self.restart_required(),
            "restart_pending": self._restart_pending,
            "started": self._started,
            "closed": self._closed,
            "cleanup_complete": self._cleanup_complete,
            "faulted": self._faulted,
            "last_error": self._last_error,
            "starts": self._starts,
            "steps": self._steps,
            "rx_frames": self._rx_frames,
            "controller_operations": self._controller_operations,
            "cleanup_errors": self._cleanup_errors,
            "controller": controller,
            "protocol": protocol,
        }


def build_configured_heater_runtime(
    config_manager,
    configured_runtime,
    protocol_factory=None,
    controller_factory=None,
    ticks_ms=None,
    ticks_diff=None,
    ticks_add=None,
):
    """Build an inert heater owner bound to one configuration generation."""

    generation = _require_generation(
        getattr(config_manager, "generation", None)
    )
    runtime_generation = _require_generation(
        getattr(configured_runtime, "configuration_generation", None)
    )
    if generation != runtime_generation:
        raise ValueError("configured runtime generation differs")
    checker = getattr(configured_runtime, "restart_required", None)
    if not callable(checker) or checker(config_manager) is not False:
        raise ValueError("configured runtime requires restart")
    scheduler = getattr(configured_runtime, "scheduler", None)
    maximum_runtime_minutes = getattr(
        scheduler, "maximum_runtime_minutes", None
    )
    if type(maximum_runtime_minutes) is not int or maximum_runtime_minutes <= 0:
        raise ValueError("configured maximum runtime is malformed")
    temperature_manager = getattr(
        configured_runtime, "temperature_manager", None
    )
    if temperature_manager is None:
        raise ValueError("configured runtime has no temperature manager")
    if protocol_factory is None:
        protocol_factory = _open_product_protocol_service
    if controller_factory is None:
        controller_factory = _build_product_controller
    if not callable(protocol_factory) or not callable(controller_factory):
        raise ValueError("heater factories must be callable")
    if ticks_ms is None:
        ticks_ms = _platform_ticks_ms
    if ticks_diff is None:
        ticks_diff = _platform_ticks_diff
    if ticks_add is None:
        ticks_add = _platform_ticks_add
    if not all(callable(value) for value in (ticks_ms, ticks_diff, ticks_add)):
        raise ValueError("heater clocks must be callable")
    return ConfiguredHeaterRuntime(
        config_manager,
        configured_runtime,
        generation,
        protocol_factory,
        controller_factory,
        maximum_runtime_minutes,
        temperature_manager,
        ticks_ms,
        ticks_diff,
        ticks_add,
    )
