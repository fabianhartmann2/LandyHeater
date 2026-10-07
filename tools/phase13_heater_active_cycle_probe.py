"""Bounded, supervised Phase-13 heater START/SHUTDOWN acceptance gate.

Importing this module is inert. The tool is intentionally unusable while the
product UART pin and protocol-TX flags remain closed. ``arm()`` requires 12 V
off and starts the normal configured runtime without stepping it or writing.
``run_cycle()`` is one continuous operation: synchronize OFF, request power
level 1 for seven minutes, require RUNNING, allow automatic expiry to request
controlled shutdown, and supervise until confirmed OFF.

There is no external-temperature or arbitrary-send surface. START and SHUTDOWN
are each capped at the controller's two-attempt limit. Active testing requires
an accessible external 12-V cutoff.
"""

import os as _os
import sys as _sys


ARM_CONFIRMATION = (
    "DFR0975U_USB_ONLY_DIRECT_TX_ACTIVE_CYCLE_ARM_HEATER_12V_OFF_V1"
)
RUN_CONFIRMATION = (
    "DFR0975U_DIRECT_TX_HEATER_POWER1_SEVEN_MINUTES_AUTO_SHUTDOWN_V1"
)
POWER_REMOVED_CONFIRMATION = (
    "DFR0975U_ACTIVE_CYCLE_ABORT_HEATER_12V_CONFIRMED_OFF_V1"
)
ARMED_TOKEN = "DFR0975U_ACTIVE_CYCLE_ARMED_NO_WRITE_V1"
PASS_TOKEN = "DFR0975U_ACTIVE_CYCLE_RUNNING_TO_OFF_PASS_V1"

POWER_LEVEL = 1
RUNTIME_MINUTES = 7
POLL_DELAY_MS = 10
SYNC_TIMEOUT_MS = 15000
TOTAL_CYCLE_TIMEOUT_MS = 20 * 60 * 1000
FORCE_STOP_AFTER_MS = 8 * 60 * 1000
MAX_CONTROL_ATTEMPTS = 2
MAX_INIT_REQUESTS = 3
MAX_STATUS_REQUESTS = 1400

CONFIG_BASE_PATH = "/landy_heater_config"
LEDGER_BASE_PATH = "/landy_heater_scheduler"
_STORE_PATHS = tuple(
    base + suffix
    for base in (CONFIG_BASE_PATH, LEDGER_BASE_PATH)
    for suffix in (".a", ".b", ".tmp")
)

_runtime = None
_protocol = None
_storage_before = None
_armed = False
_cycle_started = False


def _require(condition, message):
    if not condition:
        raise RuntimeError("Phase-13 active heater cycle failed: {}".format(message))


def _missing_file(error):
    code = getattr(error, "errno", None)
    if code is None and getattr(error, "args", None):
        code = error.args[0]
    return code == 2


def _stat_signature(paths=_STORE_PATHS):
    result = []
    for path in paths:
        try:
            result.append(tuple(_os.stat(path)))
        except OSError as error:
            if not _missing_file(error):
                raise
            result.append(None)
    return tuple(result)


class _ActiveCycleProtocolPort:
    """Fixed command envelope around the production protocol service."""

    def __init__(self, service):
        self._service = service
        self.init_requests = 0
        self.status_requests = 0
        self.start_requests = 0
        self.shutdown_requests = 0

    def poll_inbound(self, now_ms=None):
        return self._service.poll_inbound(now_ms)

    def validate_inbound_frame(self, frame):
        return self._service.validate_inbound_frame(frame)

    def transport_status(self):
        return self._service.transport_status()

    def drain_activity(self, max_events):
        return self._service.drain_activity(max_events)

    def reset_inbound(self):
        return self._service.reset_inbound()

    def request_initialization(self):
        _require(self.init_requests < MAX_INIT_REQUESTS, "INIT bound exceeded")
        self.init_requests += 1
        return self._service.request_initialization()

    def request_status(self):
        _require(
            self.status_requests < MAX_STATUS_REQUESTS,
            "STATUS bound exceeded",
        )
        self.status_requests += 1
        return self._service.request_status()

    def request_start(self, mode, target_temperature=None, power_level=None):
        from protocol.autoterm_protocol import CONTROL_MODE_POWER

        _require(mode == CONTROL_MODE_POWER, "START mode differs")
        _require(target_temperature is None, "START temperature is not allowed")
        _require(power_level == POWER_LEVEL, "START power differs")
        _require(
            self.start_requests < MAX_CONTROL_ATTEMPTS,
            "START attempt bound exceeded",
        )
        self.start_requests += 1
        return self._service.request_start(
            mode,
            target_temperature=target_temperature,
            power_level=power_level,
        )

    def request_shutdown(self):
        _require(
            self.shutdown_requests < MAX_CONTROL_ATTEMPTS,
            "SHUTDOWN attempt bound exceeded",
        )
        self.shutdown_requests += 1
        return self._service.request_shutdown()

    def deinit(self):
        return self._service.deinit()

    def force_close(self):
        return self._service.deinit()


def _radios_inactive(network_module):
    return (
        network_module.WLAN(network_module.STA_IF).active() is False
        and network_module.WLAN(network_module.AP_IF).active() is False
    )


def _validate_active_profile(board_config):
    _require(
        board_config.UART_TX_INTERFACE == "direct_level_shifter",
        "direct TX interface is not selected",
    )
    _require(
        board_config.UART_DIRECT_TX_APPROVED is True,
        "direct TX risk approval is closed",
    )
    _require(board_config.UART_PINS_APPROVED is True, "UART pins are closed")
    _require(
        board_config.UART_PROTOCOL_TX_ENABLED is True,
        "protocol TX is closed",
    )
    _require(
        board_config.UART_TX_GATE_APPROVED is False,
        "nonexistent gate is marked approved",
    )
    board_config.require_uart_configuration()


def _controller_state(runtime):
    snapshot = runtime.snapshot()
    controller = snapshot.get("controller")
    _require(type(controller) is dict, "controller snapshot is malformed")
    actual = controller.get("actual")
    requested = controller.get("requested")
    _require(type(actual) is dict, "actual state is malformed")
    _require(type(requested) is dict, "requested state is malformed")
    return snapshot, controller, actual, requested


def arm(confirmation):
    """Open the approved product runtime with heater 12 V off; write nothing."""

    global _runtime, _protocol, _storage_before, _armed, _cycle_started
    _require(confirmation == ARM_CONFIRMATION, "exact arm confirmation required")
    _require(_sys.platform == "esp32", "target is not ESP32 MicroPython")
    _require(not _armed and _runtime is None, "probe is already armed")

    import board_config
    import network
    from time import ticks_add, ticks_diff, ticks_ms
    from adapters.config_file_store import AtomicJSONConfigStore
    from app.composition import open_tx_enabled_protocol_service
    from app.configuration_bootstrap import build_configured_runtime
    from app.heater_composition import build_configured_heater_runtime
    from services.config_manager import ConfigManager

    _validate_active_profile(board_config)
    _require(_radios_inactive(network), "a WLAN interface is active")
    storage_before = _stat_signature()
    manager = ConfigManager(
        AtomicJSONConfigStore(CONFIG_BASE_PATH),
        AtomicJSONConfigStore(LEDGER_BASE_PATH),
    )
    _require(manager.load() is True, "production configuration is not trusted")
    _require(
        manager.load_scheduler_checkpoint() is True,
        "scheduler ledger is not trusted",
    )
    configured = build_configured_runtime(manager)
    protocol = None
    runtime = None
    try:
        def protocol_factory():
            return _ActiveCycleProtocolPort(open_tx_enabled_protocol_service())

        runtime = build_configured_heater_runtime(
            manager,
            configured,
            protocol_factory=protocol_factory,
            ticks_ms=ticks_ms,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
        )
        _require(runtime.start() is True, "configured runtime did not start")
        protocol = runtime.protocol_port
        status = protocol.transport_status()
        _require(status.get("tx_enabled") is True, "transport TX is locked")
        _require(status.get("tx_frames") == 0, "arming wrote to UART")
        _require(_stat_signature() == storage_before, "storage changed while arming")
    except BaseException:
        if runtime is not None:
            try:
                runtime.deinit()
            except BaseException:
                pass
        raise

    _runtime = runtime
    _protocol = protocol
    _storage_before = storage_before
    _armed = True
    _cycle_started = False
    print("writes=0")
    print("runtime_minutes={}".format(RUNTIME_MINUTES))
    print("power_level={}".format(POWER_LEVEL))
    print(ARMED_TOKEN)
    return True


def _wait_for_initial_off(ticks_ms, ticks_diff, ticks_add, sleep_ms):
    deadline = ticks_add(ticks_ms(), SYNC_TIMEOUT_MS)
    while True:
        _runtime.step()
        snapshot, controller, actual, requested = _controller_state(_runtime)
        if (
            controller.get("phase") == "ready"
            and actual.get("synchronized") is True
            and actual.get("heater_state") == "off"
            and requested.get("on") is False
        ):
            return snapshot
        _require(ticks_diff(deadline, ticks_ms()) > 0, "initial OFF sync timed out")
        sleep_ms(POLL_DELAY_MS)


def run_cycle(confirmation):
    """Run one seven-minute power-1 cycle through confirmed shutdown/OFF."""

    global _cycle_started
    _require(confirmation == RUN_CONFIRMATION, "exact cycle confirmation required")
    _require(_sys.platform == "esp32", "target is not ESP32 MicroPython")
    _require(_armed and _runtime is not None, "probe is not armed")
    _require(not _cycle_started, "cycle already started")

    import board_config
    import network
    from time import sleep_ms, ticks_add, ticks_diff, ticks_ms
    from protocol.autoterm_protocol import CONTROL_MODE_POWER

    _validate_active_profile(board_config)
    _require(_radios_inactive(network), "a WLAN interface is active")
    _wait_for_initial_off(ticks_ms, ticks_diff, ticks_add, sleep_ms)
    controller = _runtime.controller
    _require(
        controller.request_start(
            CONTROL_MODE_POWER,
            power_level=POWER_LEVEL,
            runtime_minutes=RUNTIME_MINUTES,
            source="manual",
        ) is True,
        "START request was not accepted",
    )
    _cycle_started = True
    started_ms = ticks_ms()
    deadline = ticks_add(started_ms, TOTAL_CYCLE_TIMEOUT_MS)
    force_stop_due = ticks_add(started_ms, FORCE_STOP_AFTER_MS)
    saw_starting = False
    saw_running = False
    saw_shutdown = False

    while True:
        _runtime.step()
        snapshot, controller_state, actual, requested = _controller_state(_runtime)
        state = actual.get("heater_state")
        saw_starting = saw_starting or state == "starting"
        saw_running = saw_running or state == "running"
        saw_shutdown = saw_shutdown or state == "shutting_down"

        if ticks_diff(ticks_ms(), force_stop_due) >= 0 and requested.get("on") is True:
            controller.request_stop()
        if (
            saw_running
            and requested.get("on") is False
            and actual.get("synchronized") is True
            and state == "off"
        ):
            break
        _require(ticks_diff(deadline, ticks_ms()) > 0, "cycle did not return OFF")
        sleep_ms(POLL_DELAY_MS)

    _require(saw_starting, "STARTING state was never observed")
    _require(saw_running, "RUNNING state was never observed")
    _require(saw_shutdown, "SHUTTING_DOWN state was never observed")
    _require(1 <= _protocol.start_requests <= 2, "START count differs")
    _require(1 <= _protocol.shutdown_requests <= 2, "SHUTDOWN count differs")
    _require(_stat_signature() == _storage_before, "production storage changed")
    _require(_radios_inactive(network), "a WLAN interface became active")
    result = {
        "phase": controller_state.get("phase"),
        "heater_state": state,
        "voltage": actual.get("voltage"),
        "runtime_minutes": RUNTIME_MINUTES,
        "power_level": POWER_LEVEL,
        "start_requests": _protocol.start_requests,
        "shutdown_requests": _protocol.shutdown_requests,
        "status_requests": _protocol.status_requests,
    }
    _runtime.deinit()
    _clear_globals()
    print("phase={}".format(result["phase"]))
    print("heater_state=off")
    print("start_requests={}".format(result["start_requests"]))
    print("shutdown_requests={}".format(result["shutdown_requests"]))
    print("storage_unchanged=True")
    print(PASS_TOKEN)
    return result


def _clear_globals():
    global _runtime, _protocol, _storage_before, _armed, _cycle_started
    _runtime = None
    _protocol = None
    _storage_before = None
    _armed = False
    _cycle_started = False


def cancel_before_cycle():
    """Close an armed, never-stepped runtime while 12 V remains off."""

    _require(_armed and not _cycle_started, "safe pre-cycle cancel is unavailable")
    _runtime.deinit()
    _clear_globals()
    print("DFR0975U_ACTIVE_CYCLE_CANCELLED_NO_WRITE_V1")
    return True


def emergency_power_removed(confirmation):
    """Release UART only after the operator has physically removed heater 12 V."""

    _require(
        confirmation == POWER_REMOVED_CONFIRMATION,
        "exact power-removed confirmation required",
    )
    if _protocol is not None:
        _protocol.force_close()
    _clear_globals()
    print("DFR0975U_ACTIVE_CYCLE_RELEASED_AFTER_12V_OFF_V1")
    return True
