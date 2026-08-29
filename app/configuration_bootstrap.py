"""Build a cold, hardware-free runtime from one trusted configuration.

This Phase-6 composition boundary performs no filesystem or hardware access.
The caller explicitly loads :class:`services.config_manager.ConfigManager`
first and passes it here.  Returned clocks start invalid, the Scheduler stays
disarmed and no HeaterController or protocol port is constructed.  In
particular, persisted quick-start defaults are data only and can never replay
an ON request during boot.
"""

from app.scheduler import Scheduler
from app.temperature_manager import TemperatureManager
from services.config_manager import (
    ConfigurationStateError,
    validate_configuration,
    validate_scheduler_ledger,
)
from services.time_service import TimeService


def _clone(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is list:
        return [_clone(item) for item in value]
    if type(value) is dict:
        return {key: _clone(item) for key, item in value.items()}
    raise ConfigurationStateError("configured runtime contains non-JSON data")


def _exact_snapshot(name, value, fields):
    if type(value) is not dict or frozenset(value) != frozenset(fields):
        raise ConfigurationStateError("{} snapshot is malformed".format(name))
    return value


class ConfiguredRuntime:
    """Read-only handles and cold-boot metadata for later composition."""

    __slots__ = (
        "_time_service",
        "_temperature_manager",
        "_scheduler",
        "_quick_start",
        "_configuration_generation",
        "_ledger_generation",
        "_setup_complete",
        "_persistent_start_gate_open",
    )

    def __init__(
        self,
        time_service,
        temperature_manager,
        scheduler,
        quick_start,
        configuration_generation,
        ledger_generation,
        setup_complete,
        persistent_start_gate_open,
    ):
        self._time_service = time_service
        self._temperature_manager = temperature_manager
        self._scheduler = scheduler
        self._quick_start = _clone(quick_start)
        self._configuration_generation = configuration_generation
        self._ledger_generation = ledger_generation
        self._setup_complete = setup_complete
        self._persistent_start_gate_open = persistent_start_gate_open

    @property
    def time_service(self):
        return self._time_service

    @property
    def temperature_manager(self):
        return self._temperature_manager

    @property
    def scheduler(self):
        return self._scheduler

    @property
    def configuration_generation(self):
        return self._configuration_generation

    @property
    def ledger_generation(self):
        return self._ledger_generation

    @property
    def persistent_start_gate_open(self):
        return self._persistent_start_gate_open

    def restart_required(self, config_manager):
        """Return whether static constructor settings or trust changed.

        A healthy Scheduler ledger checkpoint advances its own generation at
        runtime and is already reflected in the live Scheduler.  It must not
        be mistaken for a static configuration change that requires a cold
        composition rebuild.
        """

        generation = getattr(config_manager, "generation", None)
        ledger_generation = getattr(config_manager, "ledger_generation", None)
        if type(generation) is not int or type(ledger_generation) is not int:
            raise ConfigurationStateError(
                "config manager generations are unavailable"
            )
        persistent_gate = getattr(
            config_manager, "timer_start_allowed", None
        )
        if type(persistent_gate) is not bool:
            raise ConfigurationStateError(
                "config manager start gate is unavailable"
            )
        return (
            generation != self._configuration_generation
            or persistent_gate is not self._persistent_start_gate_open
        )

    def snapshot(self):
        return {
            "configuration_generation": self._configuration_generation,
            "ledger_generation": self._ledger_generation,
            "setup_complete": self._setup_complete,
            "persistent_start_gate_open": self._persistent_start_gate_open,
            "quick_start": _clone(self._quick_start),
            "clock_valid": self._time_service.valid,
            "scheduler_armed": self._scheduler.armed,
        }


def build_configured_runtime(config_manager, ticks_diff=None, ticks_add=None):
    """Build cold pure models from an already loaded ConfigManager.

    The operation is all-or-nothing from the caller's perspective.  A
    concurrent/re-entrant generation change is detected before any result is
    returned.  Created objects remain local and perform no I/O.
    """

    if (ticks_diff is None) != (ticks_add is None):
        raise ValueError("ticks_diff and ticks_add must be provided together")
    for method in ("snapshot", "scheduler_checkpoint"):
        if not callable(getattr(config_manager, method, None)):
            raise ValueError("config_manager must provide {}()".format(method))
    for attribute in (
        "generation",
        "ledger_generation",
        "timer_start_allowed",
    ):
        if not hasattr(config_manager, attribute):
            raise ValueError("config_manager must expose {}".format(attribute))

    config_generation = config_manager.generation
    ledger_generation = config_manager.ledger_generation
    persistent_gate = config_manager.timer_start_allowed
    if type(config_generation) is not int or type(ledger_generation) is not int:
        raise ConfigurationStateError("configuration generations are invalid")
    if type(persistent_gate) is not bool:
        raise ConfigurationStateError("persistent start gate is not boolean")

    config_snapshot = _exact_snapshot(
        "configuration",
        config_manager.snapshot(),
        ("generation", "configuration"),
    )
    ledger_snapshot = _exact_snapshot(
        "scheduler checkpoint",
        config_manager.scheduler_checkpoint(),
        ("generation", "ledger"),
    )
    if (
        config_snapshot["generation"] != config_generation
        or ledger_snapshot["generation"] != ledger_generation
    ):
        raise ConfigurationStateError("configuration changed while staging")

    configuration = validate_configuration(
        config_snapshot["configuration"]
    )
    canonical_ledger = validate_scheduler_ledger(
        ledger_snapshot["ledger"]
    )
    time_settings = configuration["time"]
    if ticks_diff is None:
        time_service = TimeService(
            timezone_name=time_settings["timezone_name"],
            utc_offset_minutes=time_settings["standard_utc_offset_minutes"],
            timezone_rule=time_settings["timezone_rule"],
        )
        temperature_manager = TemperatureManager(
            assignments=configuration["sensors"]["assignments"],
            stale_after_ms=configuration["sensors"]["stale_after_ms"],
            failed_after_ms=configuration["sensors"]["failed_after_ms"],
        )
        scheduler = Scheduler(
            time_service,
            configuration["heater"]["maximum_runtime_minutes"],
        )
    else:
        if not callable(ticks_diff) or not callable(ticks_add):
            raise ValueError("ticks_diff and ticks_add must be callable together")
        time_service = TimeService(
            ticks_diff=ticks_diff,
            timezone_name=time_settings["timezone_name"],
            utc_offset_minutes=time_settings["standard_utc_offset_minutes"],
            timezone_rule=time_settings["timezone_rule"],
        )
        temperature_manager = TemperatureManager(
            assignments=configuration["sensors"]["assignments"],
            ticks_diff=ticks_diff,
            stale_after_ms=configuration["sensors"]["stale_after_ms"],
            failed_after_ms=configuration["sensors"]["failed_after_ms"],
        )
        scheduler = Scheduler(
            time_service,
            configuration["heater"]["maximum_runtime_minutes"],
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
        )

    scheduler.replace_timers(configuration["timers"])
    if persistent_gate:
        # Restore exclusively from the same already-validated snapshot whose
        # generation was captured above.  A second manager callback here could
        # swap history re-entrantly without changing either generation.
        configured_timer_ids = {
            timer["id"] for timer in configuration["timers"]
        }
        history = {
            "consumed_local_high_water": canonical_ledger[
                "consumed_local_high_water"
            ],
            "occurrences": [
                {
                    "timer_id": item["timer_id"],
                    "occurrence_key": item["occurrence_key"],
                    "local_minute_id": item["local_minute_id"],
                    "status": item["status"],
                    "overridden": item["overridden"],
                }
                for item in canonical_ledger["occurrences"]
                if item["timer_id"] in configured_timer_ids
            ],
        }
        scheduler.restore_persistent_history(history)

    # A generation race invalidates the entire staged composition.  The
    # objects above are still cold/disarmed and can be safely discarded.
    if (
        config_manager.generation != config_generation
        or config_manager.ledger_generation != ledger_generation
        or config_manager.timer_start_allowed is not persistent_gate
    ):
        raise ConfigurationStateError("configuration changed during build")
    if scheduler.armed or time_service.valid:
        raise ConfigurationStateError("configured runtime did not remain cold")

    return ConfiguredRuntime(
        time_service,
        temperature_manager,
        scheduler,
        configuration["heater"]["quick_start"],
        config_generation,
        ledger_generation,
        configuration["system"]["setup_complete"],
        persistent_gate,
    )
