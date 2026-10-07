"""Two-stage source-mounted product heater-runtime synchronization probe.

Importing this module is inert. ``arm()`` requires heater 12 V to be off,
loads the existing production configuration without committing it and starts
the normal ``ConfiguredHeaterRuntime`` around a bench-only UART service. It
does not call ``step()`` and therefore sends nothing.

``trigger()`` is a separate, exact confirmation. Its UART boundary accepts
one canonical INIT and one canonical STATUS request only. START, SHUTDOWN,
temperature and repeated synchronization writes are impossible through this
probe. Success additionally requires a valid synchronized OFF status before
the runtime may close. Product approval flags remain closed throughout.
"""

import os as _os
import sys as _sys

from tools import dfr0975u_uart_direct_init_probe as _init_probe


ARM_CONFIRMATION = (
    "DFR0975U_USB_ONLY_CONFIGURED_HEATER_RUNTIME_GREEN_RX13_WHITE_TX14_"
    "D12_DISCONNECTED_HEATER_OFF_V1"
)
TRIGGER_CONFIRMATION = (
    "DFR0975U_CONFIGURED_HEATER_RUNTIME_12V_IDLE_INIT_STATUS_ONLY_V1"
)
ARMED_TOKEN = "DFR0975U_CONFIGURED_HEATER_RUNTIME_ARMED_NO_WRITE_V1"
PASS_TOKEN = "DFR0975U_CONFIGURED_HEATER_RUNTIME_OFF_PASS_V1"

EXPECTED_INIT_REQUEST = bytes((0xAA, 0x03, 0x00, 0x00, 0x04, 0x9F, 0x3D))
EXPECTED_STATUS_REQUEST = bytes((0xAA, 0x03, 0x00, 0x00, 0x0F, 0x58, 0x7C))
CONFIG_BASE_PATH = "/landy_heater_config"
LEDGER_BASE_PATH = "/landy_heater_scheduler"
POLL_DELAY_MS = 2
MAXIMUM_WINDOW_MS = 10000
TX_DRAIN_TIMEOUT_MS = 500

_STORE_PATHS = tuple(
    base + suffix
    for base in (CONFIG_BASE_PATH, LEDGER_BASE_PATH)
    for suffix in (".a", ".b", ".tmp")
)

_runtime = None
_protocol = None
_uart_boundary = None
_storage_before = None
_armed = False


def _require(condition, message):
    if not condition:
        raise RuntimeError(
            "Phase-13 configured heater runtime failed: {}".format(message)
        )


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


class _SyncOnlyUART:
    """UART boundary permitting exactly one INIT and one STATUS write."""

    def __init__(
        self,
        uart,
        pin_class,
        tx_pin,
        rx_pin,
        ticks_ms,
        ticks_diff,
        sleep_ms,
    ):
        self._uart = uart
        self._pin_class = pin_class
        self._tx_pin = tx_pin
        self._rx_pin = rx_pin
        self._ticks_ms = ticks_ms
        self._ticks_diff = ticks_diff
        self._sleep_ms = sleep_ms
        self.init_writes = 0
        self.status_writes = 0
        self.closed = False

    def any(self):
        return self._uart.any()

    def read(self, count):
        return self._uart.read(count)

    def write(self, data):
        raw = bytes(data)
        if raw == EXPECTED_INIT_REQUEST and self.init_writes == 0:
            self.init_writes += 1
        elif (
            raw == EXPECTED_STATUS_REQUEST
            and self.init_writes == 1
            and self.status_writes == 0
        ):
            self.status_writes += 1
        else:
            raise RuntimeError("non-whitelisted or repeated UART write blocked")

        _require(
            self._pin_class(self._rx_pin).value() == 1,
            "green RX line is not idle-high immediately before write",
        )
        _require(
            self._pin_class(self._tx_pin).value() == 1,
            "white TX line is not idle-high immediately before write",
        )
        written = self._uart.write(raw)
        _require(
            type(written) is int and written == len(raw),
            "UART write was incomplete",
        )
        started = self._ticks_ms()
        while self._uart.txdone() is not True:
            _require(
                self._ticks_diff(self._ticks_ms(), started)
                < TX_DRAIN_TIMEOUT_MS,
                "UART TX drain timed out",
            )
            self._sleep_ms(POLL_DELAY_MS)
        return written

    def deinit(self):
        # The direct arm helper retains sole ownership of physical UART/pins.
        # Its cleanup runs after the configured runtime closes this facade.
        self.closed = True


class _SyncOnlyProtocolPort:
    """Narrow protocol facade that rejects every heater control command."""

    def __init__(self, service):
        self._service = service

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
        return self._service.request_initialization()

    def request_status(self):
        return self._service.request_status()

    def request_start(self, *args, **kwargs):
        raise RuntimeError("START is unavailable in synchronization probe")

    def request_shutdown(self):
        raise RuntimeError("SHUTDOWN is unavailable in synchronization probe")

    def deinit(self):
        return self._service.deinit()

    def force_close(self):
        return self._service.deinit()


def _radios_inactive(network_module):
    return (
        network_module.WLAN(network_module.STA_IF).active() is False
        and network_module.WLAN(network_module.AP_IF).active() is False
    )


def _release():
    """Release the runtime, UART and pins; safe to repeat."""

    global _runtime, _protocol, _uart_boundary, _storage_before, _armed
    errors = []
    runtime = _runtime
    protocol = _protocol
    _runtime = None
    _protocol = None
    _uart_boundary = None
    _storage_before = None
    _armed = False

    if runtime is not None:
        try:
            runtime.deinit()
        except BaseException:
            # After I/O the product owner correctly refuses cleanup without an
            # OFF confirmation. The probe still must physically release UART.
            if protocol is not None:
                try:
                    protocol.force_close()
                except BaseException as error:
                    errors.append(error)
    try:
        _init_probe._release()
    except BaseException as error:
        errors.append(error)
    if errors:
        raise RuntimeError(
            "Phase-13 heater runtime cleanup failed: {}".format(
                "; ".join(str(error) for error in errors)
            )
        )


def arm(confirmation):
    """Load production configuration and start cold runtime; send nothing."""

    global _runtime, _protocol, _uart_boundary, _storage_before, _armed
    _require(confirmation == ARM_CONFIRMATION, "exact arm confirmation required")
    _require(_sys.platform == "esp32", "target is not ESP32 MicroPython")
    _require(not _armed and _runtime is None, "probe is already armed")

    import board_config
    import network
    from machine import Pin
    from time import sleep_ms, ticks_add, ticks_diff, ticks_ms
    from adapters.config_file_store import AtomicJSONConfigStore
    from app.configuration_bootstrap import build_configured_runtime
    from app.heater_composition import build_configured_heater_runtime
    from protocol.autoterm_service import (
        AutotermProtocolService,
        _SERVICE_TRANSMIT_CAPABILITY,
    )
    from protocol.uart_transport import UARTTransport, _TX_AUTHORIZATION
    from services.config_manager import ConfigManager

    _init_probe._require_closed_product_flags(board_config)
    _require(_radios_inactive(network), "a WLAN interface is active")
    storage_before = _stat_signature()
    primary = None
    try:
        _init_probe.arm(_init_probe.ARM_CONFIRMATION)
        raw_uart = _init_probe._uart
        _require(raw_uart is not None, "direct UART owner is unavailable")

        manager = ConfigManager(
            AtomicJSONConfigStore(CONFIG_BASE_PATH),
            AtomicJSONConfigStore(LEDGER_BASE_PATH),
        )
        _require(manager.load() is True, "production configuration is not trusted")
        _require(
            manager.load_scheduler_checkpoint() is True,
            "scheduler ledger is not trusted",
        )
        _require(manager.faulted is False, "configuration manager is faulted")
        configured = build_configured_runtime(manager)

        uart_boundary = _SyncOnlyUART(
            raw_uart,
            Pin,
            board_config.UART_TX_PIN,
            board_config.UART_RX_PIN,
            ticks_ms,
            ticks_diff,
            sleep_ms,
        )
        transport = UARTTransport(
            uart_boundary,
            inter_byte_timeout_ms=board_config.UART_INTER_BYTE_TIMEOUT_MS,
            max_read_bytes=board_config.UART_MAX_READ_BYTES,
            activity_queue_capacity=board_config.UART_ACTIVITY_QUEUE_CAPACITY,
            max_empty_ready_reads=board_config.UART_MAX_EMPTY_READY_READS,
            _tx_authorization=_TX_AUTHORIZATION,
            ticks_ms=ticks_ms,
            ticks_diff=ticks_diff,
        )
        service = AutotermProtocolService(
            transport,
            _transmit_capability=_SERVICE_TRANSMIT_CAPABILITY,
        )
        protocol = _SyncOnlyProtocolPort(service)
        runtime = build_configured_heater_runtime(
            manager,
            configured,
            protocol_factory=lambda: protocol,
            ticks_ms=ticks_ms,
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
        )
        _require(runtime.start() is True, "configured runtime did not start")
        _require(
            uart_boundary.init_writes == 0 and uart_boundary.status_writes == 0,
            "arming unexpectedly wrote to UART",
        )
        _require(
            _stat_signature() == storage_before,
            "production storage changed while arming",
        )
        _runtime = runtime
        _protocol = protocol
        _uart_boundary = uart_boundary
        _storage_before = storage_before
        _armed = True
    except BaseException as error:
        primary = error

    if primary is not None:
        try:
            _release()
        except BaseException as cleanup_error:
            raise cleanup_error
        raise primary

    print("writes=0")
    print("configuration_generation={}".format(manager.generation))
    print("storage_unchanged=True")
    print("product_tx_flags_closed=True")
    print("radios_inactive=True")
    print(ARMED_TOKEN)
    return True


def trigger(confirmation):
    """Synchronize the configured runtime and require confirmed heater OFF."""

    _require(
        confirmation == TRIGGER_CONFIRMATION,
        "exact trigger confirmation required",
    )
    _require(_sys.platform == "esp32", "target is not ESP32 MicroPython")
    _require(_armed and _runtime is not None, "probe is not armed")

    import board_config
    import network
    from time import sleep_ms, ticks_add, ticks_diff, ticks_ms

    _init_probe._require_closed_product_flags(board_config)
    _require(_radios_inactive(network), "a WLAN interface is active")
    deadline = ticks_add(ticks_ms(), MAXIMUM_WINDOW_MS)
    result = None
    primary = None
    try:
        while True:
            _runtime.step()
            snapshot = _runtime.snapshot()
            controller = snapshot.get("controller")
            actual = (
                controller.get("actual") if type(controller) is dict else None
            )
            if (
                type(controller) is dict
                and controller.get("phase") == "ready"
                and type(actual) is dict
                and actual.get("synchronized") is True
                and actual.get("heater_state") == "off"
            ):
                break
            _require(
                ticks_diff(deadline, ticks_ms()) > 0,
                "synchronization timed out",
            )
            sleep_ms(POLL_DELAY_MS)

        _require(_uart_boundary.init_writes == 1, "INIT write count differs")
        _require(_uart_boundary.status_writes == 1, "STATUS write count differs")
        protocol_status = snapshot.get("protocol")
        _require(type(protocol_status) is dict, "protocol status is malformed")
        _require(protocol_status.get("tx_frames") == 2, "TX frame count differs")
        _require(protocol_status.get("rx_frames") == 2, "RX frame count differs")
        _require(protocol_status.get("write_errors") == 0, "UART write failed")
        _require(protocol_status.get("read_errors") == 0, "UART read failed")
        _require(protocol_status.get("rx_faulted") is False, "UART RX faulted")
        _require(
            _stat_signature() == _storage_before,
            "production storage changed during synchronization",
        )
        result = {
            "configuration_generation": snapshot["configuration_generation"],
            "phase": controller["phase"],
            "heater_state": actual["heater_state"],
            "voltage": actual.get("voltage"),
            "init_writes": _uart_boundary.init_writes,
            "status_writes": _uart_boundary.status_writes,
            "rx_frames": protocol_status["rx_frames"],
            "tx_frames": protocol_status["tx_frames"],
        }
    except BaseException as error:
        primary = error

    cleanup = None
    try:
        _release()
    except BaseException as error:
        cleanup = error
    if cleanup is not None:
        raise cleanup
    if primary is not None:
        raise primary

    _init_probe._require_closed_product_flags(board_config)
    _require(_radios_inactive(network), "a WLAN interface became active")
    print("phase={}".format(result["phase"]))
    print("heater_state={}".format(result["heater_state"]))
    print("voltage={}".format(result["voltage"]))
    print("init_writes=1")
    print("status_writes=1")
    print("storage_unchanged=True")
    print("product_tx_flags_closed=True")
    print("radios_inactive=True")
    print(PASS_TOKEN)
    return result


def cancel():
    """Release an armed runtime without calling step or sending anything."""

    _release()
    print("DFR0975U_CONFIGURED_HEATER_RUNTIME_CANCELLED_NO_WRITE_V1")
    return True
