"""Cold composition and lifecycle owner for the product DS18B20 bus.

Importing and constructing this module performs no hardware I/O.  ``start``
opens the guarded MicroPython adapter exactly once, while ``step`` advances at
most one already-cooperative bus action.  The shared ``TemperatureManager`` is
the only handoff to REST, the Web UI and heater policy.
"""

import time as _time


_SENSOR_ROLES = ("roof_tent", "cabin", "outside")


class SensorRuntimeError(RuntimeError):
    pass


def _plain_ticks_ms():
    return 0


_platform_ticks_ms = getattr(_time, "ticks_ms", _plain_ticks_ms)


def _require_generation(value):
    if type(value) is not int or value < 0:
        raise ValueError("configuration generation is malformed")
    return value


def _validate_assignments(temperature_manager):
    assignments = getattr(temperature_manager, "assignments", None)
    if type(assignments) is not dict or frozenset(assignments) != frozenset(
        _SENSOR_ROLES
    ):
        raise ValueError("sensor assignments are malformed")
    used = set()
    for role in _SENSOR_ROLES:
        rom_id = assignments[role]
        if type(rom_id) is not str or not rom_id or len(rom_id) > 64:
            raise ValueError("all three sensor roles must be assigned")
        if rom_id in used:
            raise ValueError("sensor assignments must be unique")
        used.add(rom_id)
    return assignments


def _open_product_adapter(temperature_manager):
    from hardware.micropython_ds18b20 import (
        open_ds18b20_adapter_from_board_config,
    )

    return open_ds18b20_adapter_from_board_config(temperature_manager)


class ConfiguredSensorRuntime:
    """Own one configured DS18B20 adapter and its bounded lifecycle."""

    __slots__ = (
        "_config_manager",
        "_configured_runtime",
        "_temperature_manager",
        "_configuration_generation",
        "_adapter_factory",
        "_ticks_ms",
        "_adapter",
        "_started",
        "_closed",
        "_cleanup_complete",
        "_faulted",
        "_last_error",
        "_starts",
        "_steps",
        "_actions",
        "_cleanup_errors",
    )

    def __init__(
        self,
        config_manager,
        configured_runtime,
        temperature_manager,
        configuration_generation,
        adapter_factory,
        ticks_ms,
    ):
        self._config_manager = config_manager
        self._configured_runtime = configured_runtime
        self._temperature_manager = temperature_manager
        self._configuration_generation = configuration_generation
        self._adapter_factory = adapter_factory
        self._ticks_ms = ticks_ms
        self._adapter = None
        self._started = False
        self._closed = False
        self._cleanup_complete = False
        self._faulted = False
        self._last_error = None
        self._starts = 0
        self._steps = 0
        self._actions = 0
        self._cleanup_errors = 0

    @property
    def temperature_manager(self):
        return self._temperature_manager

    @property
    def started(self):
        return self._started

    @property
    def closed(self):
        return self._closed

    @property
    def faulted(self):
        return self._faulted

    def restart_required(self, config_manager=None):
        if config_manager is None:
            config_manager = self._config_manager
        generation = _require_generation(
            getattr(config_manager, "generation", None)
        )
        checker = getattr(self._configured_runtime, "restart_required", None)
        if not callable(checker):
            raise SensorRuntimeError("configured runtime restart gate is missing")
        required = checker(config_manager)
        if type(required) is not bool:
            raise SensorRuntimeError("configured runtime restart gate is malformed")
        return generation != self._configuration_generation or required

    @staticmethod
    def _validate_adapter(adapter):
        for name in ("step", "status", "deinit"):
            if not callable(getattr(adapter, name, None)):
                raise SensorRuntimeError("sensor adapter is malformed")
        status = adapter.status()
        if type(status) is not dict:
            raise SensorRuntimeError("sensor adapter status is malformed")
        return adapter

    def _cleanup_adapter(self):
        adapter = self._adapter
        self._started = False
        if adapter is None:
            self._cleanup_complete = True
            return True
        last_error = None
        for _ in range(2):
            try:
                result = adapter.deinit()
                if result is not True:
                    raise SensorRuntimeError(
                        "sensor adapter cleanup contract failed"
                    )
                self._cleanup_complete = True
                return True
            except MemoryError:
                raise
            except BaseException as error:
                last_error = error
        self._cleanup_errors += 1
        self._cleanup_complete = False
        self._faulted = True
        self._last_error = "sensor_cleanup_failed"
        if isinstance(last_error, SensorRuntimeError):
            raise last_error
        raise SensorRuntimeError("sensor cleanup failed") from None

    def start(self):
        if self._closed:
            raise SensorRuntimeError("sensor runtime is closed")
        if self._faulted:
            raise SensorRuntimeError("sensor runtime is faulted")
        if self._started:
            return False
        if self.restart_required():
            raise SensorRuntimeError("sensor configuration changed before start")
        _validate_assignments(self._temperature_manager)

        adapter = None
        try:
            adapter = self._adapter_factory(self._temperature_manager)
            self._adapter = adapter
            self._cleanup_complete = False
            self._validate_adapter(adapter)
            if self.restart_required():
                raise SensorRuntimeError(
                    "sensor configuration changed during start"
                )
        except MemoryError:
            self._adapter = adapter
            if adapter is not None:
                try:
                    self._cleanup_adapter()
                except BaseException:
                    pass
            self._faulted = True
            self._last_error = "sensor_start_failed"
            raise
        except BaseException:
            self._adapter = adapter
            cleanup_ok = True
            if adapter is not None:
                try:
                    cleanup_ok = self._cleanup_adapter()
                except BaseException:
                    cleanup_ok = False
            self._faulted = True
            if cleanup_ok:
                self._last_error = "sensor_start_failed"
            raise SensorRuntimeError("sensor start failed") from None

        self._started = True
        self._starts += 1
        return True

    def step(self):
        if self._closed or not self._started:
            return False
        if self.restart_required():
            self._faulted = True
            self._last_error = "sensor_configuration_changed"
            self._cleanup_adapter()
            raise SensorRuntimeError("sensor configuration changed")
        now_ms = self._ticks_ms()
        if type(now_ms) is not int:
            self._faulted = True
            self._last_error = "sensor_clock_failed"
            self._cleanup_adapter()
            raise SensorRuntimeError("sensor clock is malformed")
        try:
            result = self._adapter.step(now_ms)
            if type(result) is not int or result not in (0, 1):
                raise SensorRuntimeError("sensor adapter step is malformed")
        except MemoryError:
            self._faulted = True
            self._last_error = "sensor_step_failed"
            try:
                self._cleanup_adapter()
            except BaseException:
                pass
            raise
        except BaseException:
            self._faulted = True
            self._last_error = "sensor_step_failed"
            try:
                self._cleanup_adapter()
            except BaseException:
                pass
            raise SensorRuntimeError("sensor step failed") from None
        self._steps += 1
        self._actions += result
        return bool(result)

    def deinit(self):
        if self._closed and self._cleanup_complete:
            return None
        self._closed = True
        self._cleanup_adapter()
        return None

    def snapshot(self):
        adapter_status = None
        if self._adapter is not None:
            adapter_status = self._adapter.status()
            if type(adapter_status) is not dict:
                raise SensorRuntimeError("sensor adapter status is malformed")
        return {
            "configuration_generation": self._configuration_generation,
            "restart_required": self.restart_required(),
            "started": self._started,
            "closed": self._closed,
            "cleanup_complete": self._cleanup_complete,
            "faulted": self._faulted,
            "last_error": self._last_error,
            "starts": self._starts,
            "steps": self._steps,
            "actions": self._actions,
            "cleanup_errors": self._cleanup_errors,
            "adapter": adapter_status,
        }


def build_configured_sensor_runtime(
    config_manager,
    configured_runtime,
    adapter_factory=None,
    ticks_ms=None,
):
    """Build an inert sensor owner bound to one configuration generation."""

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
    temperature_manager = getattr(configured_runtime, "temperature_manager", None)
    if temperature_manager is None:
        raise ValueError("configured runtime has no temperature manager")
    _validate_assignments(temperature_manager)
    if adapter_factory is None:
        adapter_factory = _open_product_adapter
    if not callable(adapter_factory):
        raise ValueError("adapter_factory must be callable")
    if ticks_ms is None:
        ticks_ms = _platform_ticks_ms
    if not callable(ticks_ms):
        raise ValueError("ticks_ms must be callable")
    return ConfiguredSensorRuntime(
        config_manager,
        configured_runtime,
        temperature_manager,
        generation,
        adapter_factory,
        ticks_ms,
    )
