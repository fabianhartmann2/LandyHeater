"""Tiny owner coordinating AP association and full-product REST stages.

Import is inert.  The coordinator preallocates the ownership capsule, unloads
the AP-only module before importing the full stage, and retains cleanup
authority until the full stage explicitly claims the live AP lifetime.
"""

import gc as _gc
import sys as _sys


FULL_REST_PHONE_CONFIRMATION = "PHASE8_FULL_REST_PHONE_CONFIRM_V1"
FULL_REST_PHONE_AP_READY_TOKEN = "PHASE8_FULL_REST_PHONE_AP_READY_V1"
FULL_REST_PHONE_CLIENT_TOKEN = "PHASE8_FULL_REST_PHONE_CLIENT_SEEN_V1"
FULL_REST_PHONE_READY_TOKEN = "PHASE8_FULL_REST_PHONE_READY_V1"
FULL_REST_PHONE_PASS_TOKEN = "PHASE8_FULL_REST_PHONE_SMOKE_PASS_V1"
FULL_REST_PHONE_FAIL_TOKEN = "PHASE8_FULL_REST_PHONE_SMOKE_FAIL_V1"

AP_SSID = "Landy Heater"
AP_IP = "192.168.4.1"
STATUS_PATH = "/api/v1/status"
STATUS_URL = "http://192.168.4.1/api/v1/status"

DEFAULT_WINDOW_SECONDS = 180

_STAGE1_MODULE = "tools.phase8_full_rest_phone_stage1"
_STAGE2_SEAM_MODULE = "tools.phase8_full_rest_phone_stage2_seam"
_STAGE2_PREPARE_MODULE = "tools.phase8_full_rest_phone_stage2_prepare"
_STAGE2_MODULE = "tools.phase8_full_rest_phone_stage2"
_STAGE2_DIAGNOSTICS_MODULE = (
    "tools.phase8_full_rest_phone_stage2_diagnostics"
)
_LATE_ONLY_MODULES = (
    _STAGE2_SEAM_MODULE,
    _STAGE2_PREPARE_MODULE,
    _STAGE2_MODULE,
    _STAGE2_DIAGNOSTICS_MODULE,
    "adapters.micropython_http_server",
    "adapters.config_file_store",
    "app.application_state",
    "app.configuration_bootstrap",
    "app.configuration_api_gateway",
    "app.heater_controller",
    "app.manual_control_gateway",
    "app.network_composition",
    "app.rest_application",
    "app.rest_composition",
    "app.scheduler",
    "app.scheduler_controller_gateway",
    "app.temperature_manager",
    "protocol.autoterm_protocol",
    "services.config_manager",
    "services.configuration_errors",
    "services.http_protocol",
    "services.rest_rate_limiter",
    "services.rest_security",
    "services.strict_json",
    "services.time_service",
)


def _support_require(condition, message):
    if not condition:
        raise RuntimeError("Phase-8 full REST phone smoke failed: {}".format(
            message
        ))


def _verify_platform(board_config):
    """Reject every target except the approved ESP32 MicroPython v1.28.0."""

    import os

    implementation = getattr(_sys, "implementation", None)
    _support_require(
        getattr(implementation, "name", None) == "micropython",
        "MicroPython is required",
    )
    version = getattr(implementation, "version", ())
    try:
        version_triplet = (version[0], version[1], version[2])
    except (IndexError, TypeError):
        version_triplet = None
    _support_require(
        version_triplet == (1, 28, 0),
        "MicroPython 1.28.0 is required",
    )
    _support_require(
        getattr(_sys, "platform", None) == "esp32",
        "ESP32 is required",
    )
    machine_name = getattr(os.uname(), "machine", "")
    _support_require(
        type(machine_name) is str and "esp32" in machine_name.lower(),
        "ESP32 machine identity is missing",
    )
    _support_require(
        board_config.MICROPYTHON_TARGET == "ESP32_GENERIC"
        and board_config.MICROPYTHON_VERSION == "1.28.0",
        "board firmware profile differs",
    )
    return True


def _verify_hardware_locks(board_config):
    """Prove that the runner can open only the two WLAN interfaces."""

    _support_require(
        board_config.WIFI_RADIO_APPROVED is False,
        "the delivered Wi-Fi lock is not false",
    )
    _support_require(
        board_config.UART_PROTOCOL_TX_ENABLED is False,
        "UART protocol transmission is not locked",
    )
    _support_require(
        board_config.ONEWIRE_PIN is None
        and board_config.ONEWIRE_PIN_APPROVED is False,
        "1-Wire is not locked",
    )
    _support_require(
        board_config.I2C_SDA_PIN is None
        and board_config.I2C_SCL_PIN is None
        and board_config.I2C_PINS_APPROVED is False,
        "I2C is not locked",
    )
    return True


def _check_platform_ticks(ticks_ms, ticks_add, ticks_diff):
    for function in (ticks_ms, ticks_add, ticks_diff):
        _support_require(
            callable(function), "wrap-safe tick primitive is unavailable"
        )
    now_ms = ticks_ms()
    _support_require(
        type(now_ms) is int, "ticks_ms() returned a non-integer"
    )
    future_ms = ticks_add(now_ms, 37)
    _support_require(
        type(future_ms) is int and ticks_diff(future_ms, now_ms) == 37,
        "wrap-safe tick primitives are inconsistent",
    )
    return True


def _load_network_module():
    return __import__("network")


def _network_constant(network_module, name):
    wlan = getattr(network_module, "WLAN", None)
    value = getattr(wlan, name, None)
    if value is None:
        value = getattr(network_module, name, None)
    _support_require(
        type(value) is int, "network interface constant is malformed"
    )
    return value


def _interface_objects(network_module):
    wlan = getattr(network_module, "WLAN", None)
    _support_require(callable(wlan), "network.WLAN is unavailable")
    return (
        wlan(_network_constant(network_module, "IF_STA")),
        wlan(_network_constant(network_module, "IF_AP")),
    )


def _interfaces_inactive(network_module):
    try:
        for interface in _interface_objects(network_module):
            active = getattr(interface, "active", None)
            if not callable(active) or active() is not False:
                return False
        return True
    except BaseException:
        return False


def _emergency_radio_off(network_module):
    try:
        station, access_point = _interface_objects(network_module)
    except BaseException:
        return False
    for _ in range(2):
        try:
            disconnect = getattr(station, "disconnect", None)
            if callable(disconnect):
                disconnect()
        except BaseException:
            pass
        for interface in (station, access_point):
            try:
                interface.active(False)
            except BaseException:
                pass
        if _interfaces_inactive(network_module):
            return True
    return False


def _retry_deinit(owner, port):
    if owner is None:
        return True
    for _ in range(2):
        try:
            result = owner.deinit()
            if result is not None:
                continue
            if port is None or getattr(port, "cleanup_complete", False) is True:
                return True
        except BaseException:
            pass
    return False


def _cleanup_radio(manager, port, network_module):
    """Close production owners, verify both interfaces, then fail safe."""

    manager_ok = _retry_deinit(manager, port)
    port_ok = _retry_deinit(port, port)
    cleanup_complete = (
        port is None or getattr(port, "cleanup_complete", False) is True
    )
    inactive = (
        network_module is not None and _interfaces_inactive(network_module)
    )
    production_ok = manager_ok and port_ok and cleanup_complete and inactive
    if not inactive and network_module is not None:
        _emergency_radio_off(network_module)
    return bool(production_ok)


class _OwnershipCapsule:
    __slots__ = (
        "support",
        "board_config",
        "wifi_module",
        "port",
        "network_module",
        "network_manager",
        "live_network_configuration",
        "ticks_ms",
        "ticks_add",
        "ticks_diff",
        "sleep_ms",
        "observation_deadline",
        "memory_before",
        "memory_after_wifi_factory",
        "memory_after_ap_ready",
        "memory_after_client_association",
        "association_confirmed",
        "associated_clients",
        "stage1_failure_stage",
        "stage1_client_seen",
        "stage1_ap_clients",
        "stage1_action",
        "owner_state",
    )

    def __init__(self):
        for name in _OwnershipCapsule.__slots__:
            setattr(self, name, None)
        self.association_confirmed = False
        self.associated_clients = -1
        self.stage1_failure_stage = "stage1_preflight"
        self.stage1_client_seen = False
        self.stage1_ap_clients = -1
        self.stage1_action = "none"
        self.owner_state = "coordinator"


def _validate_password(value):
    if type(value) is not str:
        raise ValueError("temporary WPA2 password must be a string")
    try:
        encoded = value.encode("ascii")
    except (UnicodeError, ValueError):
        raise ValueError("temporary WPA2 password must be printable ASCII")
    if not 12 <= len(encoded) <= 63:
        raise ValueError("temporary WPA2 password must contain 12 to 63 bytes")
    for byte in encoded:
        if byte < 0x20 or byte > 0x7E:
            raise ValueError("temporary WPA2 password must be printable ASCII")
    return value


def _validate_window_seconds(value):
    if (
        type(value) is not int
        or not 60 <= value <= 300
    ):
        raise ValueError("observation window must be 60 to 300 seconds")
    return value


def _unload_stage1(module):
    removed = _sys.modules.pop(_STAGE1_MODULE, None)
    package = _sys.modules.get("tools")
    if package is not None:
        name = "phase8_full_rest_phone_stage1"
        if getattr(package, name, None) in (module, removed):
            try:
                delattr(package, name)
            except AttributeError:
                pass
    if _STAGE1_MODULE in _sys.modules:
        raise RuntimeError("AP stage remained in the module registry")
    if package is not None and hasattr(
        package, "phase8_full_rest_phone_stage1"
    ):
        raise RuntimeError("AP stage remained on its parent package")
    return None


def _unload_module(module, module_name, attribute_name):
    removed = _sys.modules.pop(module_name, None)
    package = _sys.modules.get("tools")
    if package is not None and getattr(package, attribute_name, None) in (
        module,
        removed,
    ):
        try:
            delattr(package, attribute_name)
        except AttributeError:
            pass
    if module_name in _sys.modules:
        raise RuntimeError("a disposable stage remained in the module registry")
    if package is not None and hasattr(package, attribute_name):
        raise RuntimeError("a disposable stage remained on its parent package")
    return None


def _require_cold_late_modules(registry):
    for module_name in _LATE_ONLY_MODULES:
        if module_name in registry:
            raise RuntimeError(
                "a late product module was resident before AP association"
            )
    return True


def _require_stage2_unloaded():
    _require_cold_late_modules(_sys.modules)
    package = _sys.modules.get("tools")
    if package is not None:
        for name in (
            "phase8_full_rest_phone_stage2_seam",
            "phase8_full_rest_phone_stage2_prepare",
            "phase8_full_rest_phone_stage2",
            "phase8_full_rest_phone_stage2_diagnostics",
        ):
            if hasattr(package, name):
                raise RuntimeError("a late stage package attribute was preloaded")
    return True


def _require_proof_cold():
    for module_name in (_STAGE2_MODULE, _STAGE2_DIAGNOSTICS_MODULE):
        if module_name in _sys.modules:
            raise RuntimeError("a post-bind module loaded before HTTP bind")
    package = _sys.modules.get("tools")
    if package is not None:
        for name in (
            "phase8_full_rest_phone_stage2",
            "phase8_full_rest_phone_stage2_diagnostics",
        ):
            if hasattr(package, name):
                raise RuntimeError("a post-bind package attribute loaded early")
    return True


def _outer_cleanup(capsule):
    clean = True
    support = capsule.support
    if support is not None:
        try:
            clean = bool(support._cleanup_radio(
                capsule.network_manager,
                capsule.port,
                capsule.network_module,
            )) and clean
        except BaseException:
            clean = False
    elif capsule.port is not None or capsule.network_manager is not None:
        clean = False
    if capsule.board_config is not None:
        try:
            capsule.board_config.WIFI_RADIO_APPROVED = False
        except BaseException:
            clean = False
    wifi_module = capsule.wifi_module
    if wifi_module is not None:
        clean = (
            getattr(wifi_module, "_WIFI_LEASED", None) is False
            and getattr(wifi_module, "_WIFI_LEASE_POISONED", None) is False
            and clean
        )
    return bool(clean)


def _sanitized_raise(error):
    if isinstance(error, KeyboardInterrupt):
        raise KeyboardInterrupt() from None
    if isinstance(error, SystemExit):
        raise SystemExit() from None
    if isinstance(error, MemoryError):
        raise MemoryError() from None
    raise RuntimeError("Phase-8 full REST phone smoke failed") from None


def _load_stage1():
    from tools import phase8_full_rest_phone_stage1

    return phase8_full_rest_phone_stage1


def _load_stage2():
    from tools import phase8_full_rest_phone_stage2

    return phase8_full_rest_phone_stage2


def _load_stage2_seam():
    from tools import phase8_full_rest_phone_stage2_seam

    return phase8_full_rest_phone_stage2_seam


def _load_stage2_prepare():
    from tools import phase8_full_rest_phone_stage2_prepare

    return phase8_full_rest_phone_stage2_prepare


def _load_stage2_diagnostics():
    from tools import phase8_full_rest_phone_stage2_diagnostics

    return phase8_full_rest_phone_stage2_diagnostics


def _emit_outer_failure(
    capsule, state, snapshot, stage, cleanup_confirmed
):
    try:
        diagnostics = _load_stage2_diagnostics()
        context = None if state is None else state.context
        after_cleanup = diagnostics.memory_free_no_collect()
        heaps = (
            capsule.memory_before,
            capsule.memory_after_wifi_factory,
            capsule.memory_after_ap_ready,
            capsule.memory_after_client_association,
            None if context is None else context.memory_after_product_imports,
            None if context is None else context.memory_after_configuration_adoption,
            None if context is None else context.memory_before_http_start,
            None if context is None else context.memory_after_proof_before_listen,
            None,
            None,
            None,
            after_cleanup,
        )
        values = diagnostics.capture(
            stage,
            snapshot,
            None if state is None else state.socket_factory,
            None if context is None else context.gateway,
            capsule.association_confirmed is True,
            False,
            False,
            heaps,
            (
                capsule.stage1_client_seen,
                capsule.stage1_ap_clients,
                capsule.stage1_action,
                capsule.association_confirmed,
            ),
            cleanup_confirmed,
        )
        diagnostics.emit(values)
    except BaseException:
        try:
            print("PHASE8_FULL_REST_PHONE_FAILURE_STAGE_V1")
        except BaseException:
            pass


def run(
    confirmation,
    temporary_password,
    window_seconds=DEFAULT_WINDOW_SECONDS,
):
    """Run both lazy stages under one exclusive hardware owner."""

    if (
        type(confirmation) is not str
        or confirmation != FULL_REST_PHONE_CONFIRMATION
    ):
        raise RuntimeError("exact Phase-8 full REST confirmation is required")
    password = _validate_password(temporary_password)
    window_seconds = _validate_window_seconds(window_seconds)
    capsule = _OwnershipCapsule()
    capsule.support = _sys.modules.get(__name__)
    if capsule.support is None:
        raise RuntimeError("coordinator ownership support is unavailable")
    stage1 = None
    seam = None
    state = None
    prepare = None
    primary = None
    cleanup_ok = False
    failure_snapshot = None
    failure_stage = "preflight_product"
    outer_diagnostics = False
    try:
        _require_stage2_unloaded()
        stage1 = _load_stage1()
        stage1.prepare(capsule, password, window_seconds)
        _unload_stage1(stage1)
        stage1 = None
        _gc.collect()
        _require_stage2_unloaded()
        seam = _load_stage2_seam()
        state = seam.Stage2State()
        prepare = _load_stage2_prepare()
        prepare.prepare(capsule, state, password, window_seconds)
        _unload_module(
            prepare,
            _STAGE2_PREPARE_MODULE,
            "phase8_full_rest_phone_stage2_prepare",
        )
        prepare = None
        _require_proof_cold()
        state.context.memory_before_http_start = seam.require_heap(
            seam.memory_free(),
            seam.MINIMUM_PRE_BIND_HEAP_BYTES,
            "pre-bind",
        )
        failure_stage = "http_bind"
        if state.server.start() is not True:
            raise RuntimeError("HTTP server did not start")
        if _STAGE2_DIAGNOSTICS_MODULE in _sys.modules:
            raise RuntimeError("failure diagnostics loaded on the success path")
        failure_stage = "confirm_association"
        stage2 = _load_stage2()
        if stage2 is not _sys.modules.get(_STAGE2_MODULE):
            raise RuntimeError("proof stage identity changed after HTTP start")
        result = stage2.continue_run(
            capsule, state, password, window_seconds
        )
        if capsule.owner_state != "released":
            raise RuntimeError("full stage did not release hardware ownership")
        return result
    except BaseException as error:
        primary = error
        outer_diagnostics = capsule.owner_state == "coordinator"
        if state is not None and state.context.failure_stage is not None:
            failure_stage = state.context.failure_stage
        elif (
            capsule.association_confirmed is not True
            and capsule.stage1_failure_stage is not None
        ):
            failure_stage = capsule.stage1_failure_stage
        try:
            if state is not None and state.server is not None:
                failure_snapshot = state.server.snapshot()
        except BaseException:
            pass
    finally:
        if prepare is not None:
            try:
                _unload_module(
                    prepare,
                    _STAGE2_PREPARE_MODULE,
                    "phase8_full_rest_phone_stage2_prepare",
                )
            except BaseException as error:
                if primary is None:
                    primary = error
        if stage1 is not None:
            try:
                _unload_stage1(stage1)
            except BaseException as error:
                if primary is None:
                    primary = error
        if capsule.owner_state != "released":
            fallback_attempted = seam is not None and state is not None
            if seam is not None and state is not None:
                try:
                    cleanup_ok = seam.fallback_cleanup(capsule, state)
                except BaseException:
                    cleanup_ok = False
            if not cleanup_ok:
                # Always attempt the radio fail-safe, but it cannot certify a
                # failed HTTP/socket/REST cleanup performed by the full seam.
                radio_cleanup_ok = _outer_cleanup(capsule)
                if not fallback_attempted:
                    cleanup_ok = radio_cleanup_ok

    if outer_diagnostics:
        _emit_outer_failure(
            capsule, state, failure_snapshot, failure_stage, cleanup_ok
        )
    print(FULL_REST_PHONE_FAIL_TOKEN)
    if primary is not None:
        _sanitized_raise(primary)
    if not cleanup_ok:
        raise RuntimeError("Phase-8 full REST phone cleanup failed")
    raise RuntimeError("Phase-8 full REST phone smoke failed")
