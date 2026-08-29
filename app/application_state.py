"""Hardware-independent application and session state.

The objects in this module contain application truth only.  Their public
attributes are read-only; mutations are performed through the validated
methods used by :mod:`app.heater_controller`.
"""


# Application-level names.  The protocol layer owns their mapping to wire
# values; these strings deliberately contain no Autoterm byte values.
CONTROL_MODE_POWER = "power"
CONTROL_MODE_ROOF_TENT_TEMPERATURE = "roof_tent_temperature"
CONTROL_MODE_CABIN_TEMPERATURE = "cabin_temperature"

COMMUNICATION_UNKNOWN = "unknown"
COMMUNICATION_OK = "ok"
COMMUNICATION_ERROR = "error"

HEATER_STATE_UNKNOWN = "unknown"
HEATER_STATE_OFF = "off"
HEATER_STATE_STARTING = "starting"
HEATER_STATE_RUNNING = "running"
HEATER_STATE_SHUTTING_DOWN = "shutting_down"
HEATER_STATE_TEMP_MONITORING = "temp_monitoring"

HEATER_STATE_BY_RAW = {
    0: HEATER_STATE_OFF,
    1: HEATER_STATE_STARTING,
    4: HEATER_STATE_RUNNING,
    5: HEATER_STATE_SHUTTING_DOWN,
    6: HEATER_STATE_TEMP_MONITORING,
}

SUPPORTED_CONTROL_MODES = (
    CONTROL_MODE_POWER,
    CONTROL_MODE_ROOF_TENT_TEMPERATURE,
    CONTROL_MODE_CABIN_TEMPERATURE,
)


def _require_integer(name, value, minimum, maximum=None):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("{} must be an integer".format(name))
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise ValueError("{} must be at least {}".format(name, minimum))
        raise ValueError(
            "{} must be between {} and {}".format(name, minimum, maximum)
        )


def validate_start_request(
    mode,
    target_temperature,
    power_level,
    runtime_minutes,
    source,
    maximum_runtime_minutes=None,
):
    """Validate one application start request without mutating state."""

    if mode not in SUPPORTED_CONTROL_MODES:
        raise ValueError("unsupported control mode: {}".format(mode))
    _require_integer("runtime_minutes", runtime_minutes, 1)
    if maximum_runtime_minutes is not None:
        _require_integer(
            "maximum_runtime_minutes", maximum_runtime_minutes, 1
        )
        if runtime_minutes > maximum_runtime_minutes:
            raise ValueError(
                "runtime_minutes exceeds configured maximum of {}".format(
                    maximum_runtime_minutes
                )
            )
    if not isinstance(source, str) or not source:
        raise ValueError("source must be a non-empty string")

    if mode == CONTROL_MODE_POWER:
        _require_integer("power_level", power_level, 1, 9)
        if target_temperature is not None:
            raise ValueError(
                "target_temperature is not valid in power mode"
            )
    else:
        _require_integer("target_temperature", target_temperature, 5, 30)
        if power_level is not None:
            raise ValueError(
                "power_level is not valid in temperature mode"
            )


class RequestedHeaterState:
    """Validated application intention, separate from hardware truth."""

    __slots__ = (
        "_on",
        "_mode",
        "_target_temperature",
        "_power_level",
        "_runtime_minutes",
        "_source",
    )

    def __init__(self):
        self._on = False
        self._mode = CONTROL_MODE_ROOF_TENT_TEMPERATURE
        self._target_temperature = 20
        self._power_level = None
        self._runtime_minutes = 60
        self._source = "manual"

    @property
    def on(self):
        return self._on

    @property
    def mode(self):
        return self._mode

    @property
    def target_temperature(self):
        return self._target_temperature

    @property
    def power_level(self):
        return self._power_level

    @property
    def runtime_minutes(self):
        return self._runtime_minutes

    @property
    def source(self):
        return self._source

    def request_start(
        self,
        mode,
        target_temperature=None,
        power_level=None,
        runtime_minutes=60,
        source="manual",
        maximum_runtime_minutes=None,
    ):
        validate_start_request(
            mode,
            target_temperature,
            power_level,
            runtime_minutes,
            source,
            maximum_runtime_minutes,
        )

        self._on = True
        self._mode = mode
        self._target_temperature = target_temperature
        self._power_level = power_level
        self._runtime_minutes = runtime_minutes
        self._source = source

    def request_stop(self):
        self._on = False

    def snapshot(self):
        return {
            "on": self._on,
            "mode": self._mode,
            "target_temperature": self._target_temperature,
            "power_level": self._power_level,
            "runtime_minutes": self._runtime_minutes,
            "source": self._source,
        }


class ActualHeaterState:
    """Latest CRC-validated heater truth and communication health."""

    __slots__ = (
        "_communication",
        "_initialized",
        "_synchronized",
        "_heater_state",
        "_heater_state_raw",
        "_voltage",
        "_glow_plug_raw",
        "_fan_raw",
        "_last_status_ms",
    )

    def __init__(self):
        self._communication = COMMUNICATION_UNKNOWN
        self._initialized = False
        self._synchronized = False
        self._heater_state = HEATER_STATE_UNKNOWN
        self._heater_state_raw = None
        self._voltage = None
        self._glow_plug_raw = None
        self._fan_raw = None
        self._last_status_ms = None

    @property
    def communication(self):
        return self._communication

    @property
    def initialized(self):
        return self._initialized

    @property
    def synchronized(self):
        return self._synchronized

    @property
    def heater_state(self):
        return self._heater_state

    @property
    def heater_state_raw(self):
        return self._heater_state_raw

    @property
    def last_status_ms(self):
        return self._last_status_ms

    def mark_initialized(self):
        self._communication = COMMUNICATION_OK
        self._initialized = True
        self._synchronized = False
        self._heater_state = HEATER_STATE_UNKNOWN
        self._heater_state_raw = None

    def update_from_status(self, status, now_ms):
        raw_state = status.get("heater_state")
        if not isinstance(raw_state, int) or isinstance(raw_state, bool):
            raw_state = None

        self._communication = COMMUNICATION_OK
        self._heater_state_raw = raw_state
        self._heater_state = HEATER_STATE_BY_RAW.get(
            raw_state, HEATER_STATE_UNKNOWN
        )
        self._voltage = status.get("voltage")
        self._glow_plug_raw = status.get("glow_plug_raw")
        self._fan_raw = status.get("fan_raw")
        self._last_status_ms = now_ms
        self._synchronized = (
            self._initialized and self._heater_state != HEATER_STATE_UNKNOWN
        )

    def mark_communication_error(self):
        self._communication = COMMUNICATION_ERROR
        self._initialized = False
        self._synchronized = False
        self._heater_state = HEATER_STATE_UNKNOWN
        self._heater_state_raw = None

    def snapshot(self):
        return {
            "communication": self._communication,
            "initialized": self._initialized,
            "synchronized": self._synchronized,
            "heater_state": self._heater_state,
            "heater_state_raw": self._heater_state_raw,
            "voltage": self._voltage,
            "glow_plug_raw": self._glow_plug_raw,
            "fan_raw": self._fan_raw,
            "last_status_ms": self._last_status_ms,
        }


class HeaterSession:
    """Bounded monotonic-time supervision for one requested operation."""

    __slots__ = (
        "session_id",
        "source",
        "mode",
        "target",
        "started_at_ms",
        "expires_at_ms",
        "runtime_minutes",
        "confirmed_active",
        "expired",
    )

    def __init__(
        self,
        session_id,
        source,
        mode,
        target,
        started_at_ms,
        expires_at_ms,
        runtime_minutes,
    ):
        self.session_id = session_id
        self.source = source
        self.mode = mode
        self.target = target
        self.started_at_ms = started_at_ms
        self.expires_at_ms = expires_at_ms
        self.runtime_minutes = runtime_minutes
        self.confirmed_active = False
        self.expired = False

    def mark_confirmed_active(self):
        self.confirmed_active = True

    def snapshot(self):
        return {
            "id": self.session_id,
            "source": self.source,
            "mode": self.mode,
            "target": self.target,
            "started_at_ms": self.started_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "runtime_minutes": self.runtime_minutes,
            "confirmed_active": self.confirmed_active,
            "expired": self.expired,
        }
