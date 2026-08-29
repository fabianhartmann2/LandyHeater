"""Privileged, secret-preserving configuration boundary for REST.

REST callers only ever receive :meth:`ConfigManager.public_snapshot`.  This
gateway alone stages a complete candidate from the privileged snapshot so an
omitted Wi-Fi credential remains unchanged.  Persisted changes are verified by
ConfigManager and immediately disarm the old Scheduler runtime; live apply is
intentionally deferred to a controlled composition rebuild.
"""

_SETTINGS_GROUPS = frozenset(("heater", "sensors", "time"))
_TIMER_FIELDS = frozenset((
    "id",
    "name",
    "enabled",
    "weekdays",
    "start",
    "mode",
    "target_temperature",
    "power_level",
    "runtime_minutes",
))


class ConfigurationAPIError(RuntimeError):
    pass


class ConfigurationAPIConflictError(ConfigurationAPIError):
    pass


class ConfigurationAPIValidationError(ConfigurationAPIError):
    pass


class ConfigurationAPIResourceConflictError(ConfigurationAPIConflictError):
    pass


class ConfigurationAPINotFoundError(ConfigurationAPIError):
    pass


class ConfigurationAPIInvariantError(ConfigurationAPIError):
    pass


def _require_integer(name, value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("{} must be an integer".format(name))
    return value


def _clone_json(value, depth=0):
    if depth > 16:
        raise ValueError("configuration value is too deeply nested")
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is list:
        return [_clone_json(item, depth + 1) for item in value]
    if type(value) is dict:
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("configuration keys must be strings")
            result[key] = _clone_json(item, depth + 1)
        return result
    raise ValueError("configuration contains non-JSON data")


def _exact_dict(name, value, fields):
    if type(value) is not dict or frozenset(value) != frozenset(fields):
        raise ValueError("{} has an invalid shape".format(name))
    return value


def _contains_secret_field(value):
    if type(value) is dict:
        for key, item in value.items():
            if key == "password":
                return True
            if _contains_secret_field(item):
                return True
    elif type(value) is list:
        for item in value:
            if _contains_secret_field(item):
                return True
    return False


def _validate_candidate(value, readback=False):
    """Load the heavyweight schema validator only for an actual write.

    Read-only REST requests, heater control and STOP do not need the complete
    ConfigManager schema graph.  Keeping this import behind the mutation
    boundary materially reduces the resident MicroPython heap while retaining
    the exact same validator for every persisted candidate and readback.
    """

    from services.configuration_errors import ConfigurationValidationError
    from services.config_manager import validate_configuration

    try:
        return validate_configuration(value)
    except ConfigurationValidationError:
        if readback:
            raise ConfigurationAPIInvariantError(
                "configuration readback is invalid"
            ) from None
        raise ConfigurationAPIValidationError(
            "configuration candidate is invalid"
        ) from None


class ConfigurationAPIGateway:
    """Whole-document settings and timer CRUD with generation fencing."""

    __slots__ = (
        "__config_manager",
        "__scheduler",
        "__configured_runtime",
        "__configured_network_runtime",
        "__operation_active",
        "__operation_reentered",
        "__faulted",
        "__last_error",
        "__commits",
        "__noops",
    )

    def __init__(
        self,
        config_manager,
        scheduler,
        configured_runtime=None,
        configured_network_runtime=None,
    ):
        for name in ("snapshot", "public_snapshot", "commit"):
            if not callable(getattr(config_manager, name, None)):
                raise ValueError(
                    "config_manager must provide {}()".format(name)
                )
        if not hasattr(config_manager, "generation"):
            raise ValueError("config_manager must expose generation")
        if not callable(getattr(scheduler, "disarm", None)):
            raise ValueError("scheduler must provide disarm()")
        if not hasattr(scheduler, "armed"):
            raise ValueError("scheduler must expose armed")
        for name, runtime in (
            ("configured_runtime", configured_runtime),
            ("configured_network_runtime", configured_network_runtime),
        ):
            if runtime is not None and not callable(
                getattr(runtime, "restart_required", None)
            ):
                raise ValueError("{} must provide restart_required()".format(name))

        self.__config_manager = config_manager
        self.__scheduler = scheduler
        self.__configured_runtime = configured_runtime
        self.__configured_network_runtime = configured_network_runtime
        self.__operation_active = False
        self.__operation_reentered = False
        self.__faulted = False
        self.__last_error = None
        self.__commits = 0
        self.__noops = 0

    @property
    def faulted(self):
        return self.__faulted

    def _begin_operation(self, require_healthy=False):
        if self.__operation_active:
            self.__operation_reentered = True
            raise ConfigurationAPIInvariantError(
                "configuration API operation was re-entered"
            )
        if require_healthy and self.__faulted:
            raise ConfigurationAPIInvariantError(
                "configuration API gateway is faulted"
            )
        self.__operation_active = True
        self.__operation_reentered = False

    def _finish_operation(self, primary_error):
        reentered = self.__operation_reentered
        self.__operation_active = False
        self.__operation_reentered = False
        if reentered:
            self.__faulted = True
            self.__last_error = "configuration_api_reentrancy_detected"
            self._ensure_scheduler_disarmed()
            if primary_error is None:
                raise ConfigurationAPIInvariantError(
                    "configuration API operation was re-entered"
                )

    def _ensure_scheduler_disarmed(self):
        armed = self.__scheduler.armed
        if type(armed) is not bool:
            raise ConfigurationAPIInvariantError(
                "scheduler armed state is malformed"
            )
        result = self.__scheduler.disarm()
        if type(result) is not bool:
            raise ConfigurationAPIInvariantError(
                "scheduler disarm returned a non-boolean"
            )
        if self.__scheduler.armed is not False:
            raise ConfigurationAPIInvariantError(
                "scheduler remained armed after configuration change"
            )
        return result

    def _runtime_restart_required(self):
        required = False
        for runtime in (
            self.__configured_runtime,
            self.__configured_network_runtime,
        ):
            if runtime is None:
                continue
            value = runtime.restart_required(self.__config_manager)
            if type(value) is not bool:
                raise ConfigurationAPIInvariantError(
                    "runtime restart gate returned a non-boolean"
                )
            required = required or value
        return required

    def _privileged_snapshot(self, expected_generation=None):
        generation = self.__config_manager.generation
        if type(generation) is not int:
            raise ConfigurationAPIInvariantError(
                "configuration generation is malformed"
            )
        if expected_generation is not None:
            _require_integer("expected_generation", expected_generation)
            if expected_generation != generation:
                raise ConfigurationAPIConflictError(
                    "configuration generation changed"
                )
        snapshot = _exact_dict(
            "privileged configuration snapshot",
            self.__config_manager.snapshot(),
            ("generation", "configuration"),
        )
        if snapshot["generation"] != generation:
            raise ConfigurationAPIConflictError(
                "configuration changed while staging"
            )
        candidate = _clone_json(snapshot["configuration"])
        if self.__operation_reentered:
            raise ConfigurationAPIInvariantError(
                "configuration snapshot callback re-entered the gateway"
            )
        if self.__config_manager.generation != generation:
            raise ConfigurationAPIConflictError(
                "configuration changed while staging"
            )
        return generation, candidate

    def _public_snapshot(self):
        snapshot = _exact_dict(
            "public configuration snapshot",
            self.__config_manager.public_snapshot(),
            ("generation", "configuration"),
        )
        if type(snapshot["generation"]) is not int:
            raise ConfigurationAPIInvariantError(
                "public configuration generation is malformed"
            )
        if _contains_secret_field(snapshot):
            self.__faulted = True
            self.__last_error = "configuration_secret_projection_failed"
            raise ConfigurationAPIInvariantError(
                "public configuration contains a secret field"
            )
        return _clone_json(snapshot)

    def _commit_candidate(self, expected_generation, previous, candidate):
        canonical = _validate_candidate(candidate)
        if canonical == previous:
            result = self.__config_manager.commit(
                canonical, expected_generation
            )
            if type(result) is not bool or result:
                raise ConfigurationAPIInvariantError(
                    "configuration no-op contract was violated"
                )
            if self.__config_manager.generation != expected_generation:
                raise ConfigurationAPIInvariantError(
                    "configuration generation changed on no-op"
                )
            self.__noops += 1
            public = self._public_snapshot()
            return {
                "changed": False,
                "generation": public["generation"],
                "restart_required": self._runtime_restart_required(),
                "configuration": public["configuration"],
            }

        self._ensure_scheduler_disarmed()
        primary_error = None
        try:
            result = self.__config_manager.commit(
                canonical, expected_generation
            )
            if type(result) is not bool or not result:
                raise ConfigurationAPIInvariantError(
                    "configuration commit was not confirmed"
                )
            if self.__operation_reentered:
                raise ConfigurationAPIInvariantError(
                    "configuration commit callback re-entered the gateway"
                )
            confirmed_generation = self.__config_manager.generation
            if (
                type(confirmed_generation) is not int
                or confirmed_generation <= expected_generation
            ):
                raise ConfigurationAPIInvariantError(
                    "configuration generation did not advance"
                )
            confirmed = _exact_dict(
                "confirmed configuration snapshot",
                self.__config_manager.snapshot(),
                ("generation", "configuration"),
            )
            confirmed_canonical = _validate_candidate(
                confirmed["configuration"], readback=True
            )
            if (
                confirmed["generation"] != confirmed_generation
                or confirmed_canonical != canonical
            ):
                raise ConfigurationAPIInvariantError(
                    "configuration readback differs"
                )
            public = self._public_snapshot()
            if public["generation"] != confirmed_generation:
                raise ConfigurationAPIInvariantError(
                    "public configuration readback differs"
                )
            self.__commits += 1
            return {
                "changed": True,
                "generation": confirmed_generation,
                "restart_required": True,
                "configuration": public["configuration"],
            }
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                self._ensure_scheduler_disarmed()
            except BaseException:
                self.__faulted = True
                self.__last_error = "configuration_runtime_fence_failed"
                if primary_error is None:
                    raise

    def settings_snapshot(self):
        self._begin_operation()
        primary_error = None
        try:
            public = self._public_snapshot()
            configuration = public["configuration"]
            return {
                "generation": public["generation"],
                "schema_version": configuration["schema_version"],
                "system": _clone_json(configuration["system"]),
                "heater": _clone_json(configuration["heater"]),
                "sensors": _clone_json(configuration["sensors"]),
                "time": _clone_json(configuration["time"]),
                "network": _clone_json(configuration["network"]),
                "restart_required": self._runtime_restart_required(),
            }
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def timers_snapshot(self):
        self._begin_operation()
        primary_error = None
        try:
            public = self._public_snapshot()
            return {
                "generation": public["generation"],
                "timers": _clone_json(public["configuration"]["timers"]),
                "restart_required": self._runtime_restart_required(),
            }
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def patch_settings(self, patch, expected_generation):
        self._begin_operation(require_healthy=True)
        primary_error = None
        try:
            if (
                type(patch) is not dict
                or not patch
                or not frozenset(patch).issubset(_SETTINGS_GROUPS)
            ):
                raise ValueError(
                    "settings patch must contain complete allowed groups"
                )
            generation, candidate = self._privileged_snapshot(
                expected_generation
            )
            previous = _clone_json(candidate)
            for group, value in patch.items():
                candidate[group] = _clone_json(value)
            return self._commit_candidate(
                generation, previous, candidate
            )
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def create_timer(self, timer, expected_generation):
        self._begin_operation(require_healthy=True)
        primary_error = None
        try:
            _exact_dict("timer", timer, _TIMER_FIELDS)
            generation, candidate = self._privileged_snapshot(
                expected_generation
            )
            previous = _clone_json(candidate)
            timer_id = timer["id"]
            for existing in candidate["timers"]:
                if existing["id"] == timer_id:
                    raise ConfigurationAPIResourceConflictError(
                        "timer already exists"
                    )
            candidate["timers"].append(_clone_json(timer))
            return self._commit_candidate(
                generation, previous, candidate
            )
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def replace_timer(self, timer_id, timer, expected_generation):
        self._begin_operation(require_healthy=True)
        primary_error = None
        try:
            if type(timer_id) is not str or not timer_id:
                raise ValueError("timer_id must be a non-empty string")
            _exact_dict("timer", timer, _TIMER_FIELDS)
            if timer["id"] != timer_id:
                raise ValueError("timer path id and body id differ")
            generation, candidate = self._privileged_snapshot(
                expected_generation
            )
            previous = _clone_json(candidate)
            index = None
            for position, existing in enumerate(candidate["timers"]):
                if existing["id"] == timer_id:
                    index = position
                    break
            if index is None:
                raise ConfigurationAPINotFoundError("timer does not exist")
            candidate["timers"][index] = _clone_json(timer)
            return self._commit_candidate(
                generation, previous, candidate
            )
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def delete_timer(self, timer_id, expected_generation):
        self._begin_operation(require_healthy=True)
        primary_error = None
        try:
            if type(timer_id) is not str or not timer_id:
                raise ValueError("timer_id must be a non-empty string")
            generation, candidate = self._privileged_snapshot(
                expected_generation
            )
            previous = _clone_json(candidate)
            remaining = [
                item for item in candidate["timers"]
                if item["id"] != timer_id
            ]
            if len(remaining) == len(candidate["timers"]):
                raise ConfigurationAPINotFoundError("timer does not exist")
            candidate["timers"] = remaining
            return self._commit_candidate(
                generation, previous, candidate
            )
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def reset_fault(self):
        """Clear a latched API fault only from a verified disarmed state."""

        self._begin_operation()
        primary_error = None
        try:
            if not self.__faulted:
                return False
            self._ensure_scheduler_disarmed()
            self._public_snapshot()
            if self.__scheduler.armed is not False:
                raise ConfigurationAPIInvariantError(
                    "scheduler became armed during API fault reset"
                )
            self.__faulted = False
            self.__last_error = None
            return True
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def snapshot(self):
        return {
            "faulted": self.__faulted,
            "last_error": self.__last_error,
            "commits": self.__commits,
            "noops": self.__noops,
            "operation_active": self.__operation_active,
        }
