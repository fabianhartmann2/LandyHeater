"""Disposable full-product preparation before the final Phase-8 bind.

Importing is inert.  ``prepare`` adopts the already-associated AP, provisions the
isolated production stores, builds and starts the real REST security runtime,
and constructs an inert production HTTP server over the persistent safe seam.
No socket is opened here.  The coordinator removes this module and collects
the heap before invoking ``server.start()``.
"""

import os as _os

from tools import phase8_full_rest_phone_stage2_seam as _seam


AP_IP = "192.168.4.1"
CONFIG_BASE_PATH = "/phase8_full_rest_phone_smoke_v1_config"
LEDGER_BASE_PATH = "/phase8_full_rest_phone_smoke_v1_ledger"
CONFIG_MAX_RECORD_BYTES = 12 * 1024
STORAGE_SUFFIXES = (".a", ".b", ".tmp")
PRODUCTION_CONFIG_BASE_PATH = "/landy_heater_config"
PRODUCTION_LEDGER_BASE_PATH = "/landy_heater_scheduler"
MINIMUM_FREE_HEAP_BYTES = 32 * 1024

_PRODUCT_FROZEN_ORIGINS = (
    ("adapters.config_file_store", "adapters/config_file_store.py"),
    ("app.application_state", "app/application_state.py"),
    ("app.configuration_bootstrap", "app/configuration_bootstrap.py"),
    ("app.heater_controller", "app/heater_controller.py"),
    ("app.network_composition", "app/network_composition.py"),
    ("app.network_configuration", "app/network_configuration.py"),
    ("app.network_manager", "app/network_manager.py"),
    ("app.scheduler", "app/scheduler.py"),
    ("app.scheduler_controller_gateway", "app/scheduler_controller_gateway.py"),
    ("app.temperature_manager", "app/temperature_manager.py"),
    ("hardware.micropython_wifi", "hardware/micropython_wifi.py"),
    ("protocol.autoterm_protocol", "protocol/autoterm_protocol.py"),
    ("services.config_manager", "services/config_manager.py"),
    ("services.configuration_errors", "services/configuration_errors.py"),
    ("services.time_service", "services/time_service.py"),
)
_REST_FROZEN_ORIGINS = (
    ("adapters.micropython_http_server", "adapters/micropython_http_server.py"),
    ("app.configuration_api_gateway", "app/configuration_api_gateway.py"),
    ("app.manual_control_gateway", "app/manual_control_gateway.py"),
    ("app.rest_application", "app/rest_application.py"),
    ("app.rest_composition", "app/rest_composition.py"),
    ("services.http_protocol", "services/http_protocol.py"),
    ("services.rest_rate_limiter", "services/rest_rate_limiter.py"),
    ("services.rest_security", "services/rest_security.py"),
    ("services.strict_json", "services/strict_json.py"),
)


def _require(condition, message):
    if not condition:
        raise RuntimeError("Phase-8 full REST phone smoke failed: {}".format(
            message
        ))


def _load_product_runtime(capsule):
    from adapters.config_file_store import AtomicJSONConfigStore
    from app.configuration_bootstrap import build_configured_runtime
    from app.heater_controller import HeaterController
    from app.network_composition import ConfiguredNetworkRuntime
    from app.scheduler_controller_gateway import SchedulerControllerGateway
    from services.config_manager import (
        ConfigManager,
        default_configuration,
        default_scheduler_ledger,
    )

    core = _seam.ProductHandles()
    core.support = capsule.support
    core.board_config = capsule.board_config
    core.wifi_module = capsule.wifi_module
    core.AtomicJSONConfigStore = AtomicJSONConfigStore
    core.ConfigManager = ConfigManager
    core.default_configuration = default_configuration
    core.default_scheduler_ledger = default_scheduler_ledger
    core.build_configured_runtime = build_configured_runtime
    core.ConfiguredNetworkRuntime = ConfiguredNetworkRuntime
    core.SchedulerControllerGateway = SchedulerControllerGateway
    core.HeaterController = HeaterController
    core.ticks_ms = capsule.ticks_ms
    core.ticks_add = capsule.ticks_add
    core.ticks_diff = capsule.ticks_diff
    core.sleep_ms = capsule.sleep_ms
    return core


def _load_rest_runtime():
    from app.rest_composition import build_rest_http_server, build_rest_runtime

    return build_rest_runtime, build_rest_http_server, None


def _storage_filesystem():
    return None


def _paths(bases):
    return tuple(
        base + suffix for base in bases for suffix in STORAGE_SUFFIXES
    )


def _storage_paths():
    return _paths((CONFIG_BASE_PATH, LEDGER_BASE_PATH))


def _production_paths():
    return _paths((PRODUCTION_CONFIG_BASE_PATH, PRODUCTION_LEDGER_BASE_PATH))


def _missing_file(error):
    code = getattr(error, "errno", None)
    if code is None and getattr(error, "args", None):
        code = error.args[0]
    return code == 2


def _assert_files_absent(filesystem):
    stat = _os.stat if filesystem is None else filesystem.stat
    for path in _storage_paths():
        try:
            stat(path)
        except OSError as error:
            if _missing_file(error):
                continue
            raise
        raise RuntimeError("an isolated Phase-8 smoke file remains")
    return True


def _path_exists(filesystem, path):
    stat = _os.stat if filesystem is None else filesystem.stat
    try:
        stat(path)
    except OSError as error:
        if _missing_file(error):
            return False
        raise
    return True


def _stat_signature(filesystem, paths):
    stat = _os.stat if filesystem is None else filesystem.stat
    result = []
    for path in paths:
        try:
            value = stat(path)
        except OSError as error:
            if _missing_file(error):
                result.append(None)
                continue
            raise
        result.append(tuple(value))
    return tuple(result)


def _store_write_truth(manager):
    status = manager.status()
    config_store = status.get("config_store")
    ledger_store = status.get("ledger_store")
    _require(
        type(config_store) is dict and type(ledger_store) is dict,
        "storage status is malformed",
    )
    truth = (
        status.get("generation"), status.get("ledger_generation"),
        config_store.get("writes"), ledger_store.get("writes"),
    )
    _require(
        all(type(value) is int and value >= 0 for value in truth),
        "storage generation/write truth is malformed",
    )
    return truth


def _verify_frozen_origins(expected):
    import sys

    _require(
        type(sys.path) is list and sys.path and sys.path[0] == ".frozen",
        "frozen module path is not first",
    )
    for module_name, expected_file in expected:
        module = sys.modules.get(module_name)
        _require(module is not None, "a critical frozen module is absent")
        origin = getattr(module, "__file__", None)
        _require(
            origin == expected_file and not origin.startswith("/"),
            "a critical module did not load from frozen firmware",
        )
    return True


def _assert_storage_sealed(filesystem):
    for base in (CONFIG_BASE_PATH, LEDGER_BASE_PATH):
        _require(_path_exists(filesystem, base + ".a"), "A slot is absent")
        _require(_path_exists(filesystem, base + ".b"), "B slot is absent")
        _require(
            not _path_exists(filesystem, base + ".tmp"),
            "temporary storage file remained after commit",
        )
    return True


def _new_store(store_type, base_path, maximum, filesystem):
    keywords = {}
    if maximum is not None:
        keywords["max_record_bytes"] = maximum
    if filesystem is not None:
        keywords["filesystem"] = filesystem
    return store_type(base_path, **keywords)


def _provision_configuration(core, password, filesystem):
    config_store = _new_store(
        core.AtomicJSONConfigStore, CONFIG_BASE_PATH,
        CONFIG_MAX_RECORD_BYTES, filesystem,
    )
    ledger_store = _new_store(
        core.AtomicJSONConfigStore, LEDGER_BASE_PATH, None, filesystem,
    )
    manager = core.ConfigManager(config_store, ledger_store)
    _require(manager.load() is False, "isolated config store was not empty")
    _require(
        manager.load_scheduler_checkpoint() is False,
        "isolated ledger store was not empty",
    )
    _require(
        manager.checkpoint_scheduler(core.default_scheduler_ledger(), 0) is True,
        "isolated ledger provisioning failed",
    )
    configuration = core.default_configuration()
    configuration["network"]["access_point"]["password"] = password
    configuration["network"]["known_networks"] = []
    _require(
        manager.commit(configuration, 0) is True,
        "isolated configuration provisioning failed",
    )
    _require(
        manager.generation == 2 and manager.ledger_generation == 2,
        "isolated A/B generations differ",
    )
    _assert_storage_sealed(filesystem)
    manager = core.ConfigManager(
        _new_store(
            core.AtomicJSONConfigStore, CONFIG_BASE_PATH,
            CONFIG_MAX_RECORD_BYTES, filesystem,
        ),
        _new_store(
            core.AtomicJSONConfigStore, LEDGER_BASE_PATH, None, filesystem,
        ),
    )
    _require(manager.load() is True, "isolated config reload was not trusted")
    _require(
        manager.load_scheduler_checkpoint() is True,
        "isolated ledger reload was not trusted",
    )
    _require(
        manager.network_start_allowed is True
        and manager.timer_start_allowed is False,
        "trusted runtime gates did not open",
    )
    runtime = core.build_configured_runtime(
        manager, ticks_diff=core.ticks_diff, ticks_add=core.ticks_add,
    )
    _require(
        runtime.scheduler.armed is False and runtime.time_service.valid is False,
        "configuration bootstrap did not remain cold",
    )
    return manager, runtime


def prepare(capsule, state, temporary_password, window_seconds):
    """Publish a security-ready inert production server into ``state``."""

    _require(type(state) is _seam.Stage2State, "stage2 ownership state is invalid")
    context = state.context
    context.failure_stage = "preflight_product"
    _require(capsule.owner_state == "coordinator", "hardware owner changed")
    core = _load_product_runtime(capsule)
    context.core = core
    _require(
        core.board_config.WIFI_RADIO_APPROVED is True
        and core.board_config.UART_PROTOCOL_TX_ENABLED is False
        and core.board_config.UART_PINS_APPROVED is False
        and core.board_config.UART_TX_GATE_PIN == 12
        and core.board_config.UART_TX_GATE_ACTIVE_LEVEL == 1
        and core.board_config.UART_TX_GATE_APPROVED is False
        and core.board_config.ONEWIRE_PIN == 4
        and core.board_config.ONEWIRE_PIN_APPROVED is False
        and core.board_config.I2C_ID == 1
        and core.board_config.I2C_SDA_PIN == 10
        and core.board_config.I2C_SCL_PIN == 11
        and core.board_config.I2C_PINS_APPROVED is False,
        "live AP hardware approval boundary is invalid",
    )
    core.support._verify_platform(core.board_config)
    core.support._check_platform_ticks(core.ticks_ms, core.ticks_add, core.ticks_diff)
    _verify_frozen_origins(_PRODUCT_FROZEN_ORIGINS)
    filesystem = _storage_filesystem()
    context.filesystem = filesystem
    context.production_stat_baseline = _stat_signature(
        filesystem, _production_paths()
    )
    context.memory_after_product_imports = _seam.require_heap(
        _seam.memory_free(), MINIMUM_FREE_HEAP_BYTES, "intended product imports"
    )
    context.failure_stage = "preflight_storage"
    _assert_files_absent(filesystem)
    context.storage_owned = True
    config_manager, configured_runtime = _provision_configuration(
        core, temporary_password, filesystem
    )
    context.config_manager = config_manager
    context.configured_runtime = configured_runtime
    context.storage_write_baseline = _store_write_truth(config_manager)

    context.failure_stage = "preflight_wifi"
    _require(
        capsule.association_confirmed is True
        and capsule.associated_clients == 1
        and capsule.network_manager is not None
        and capsule.port is not None
        and getattr(capsule.network_manager, "running", None) is True
        and getattr(capsule.network_manager, "closed", None) is False,
        "live AP ownership capsule is invalid",
    )
    context.network_manager = capsule.network_manager
    context.port = capsule.port
    context.network_module = capsule.network_module
    generation = config_manager.generation
    gate = config_manager.network_start_allowed
    privileged = config_manager.network_configuration_for_runtime()
    _require(
        type(privileged) is dict
        and frozenset(privileged) == frozenset(("generation", "network"))
        and type(generation) is int and gate is True
        and privileged["generation"] == generation
        and privileged["network"] == capsule.live_network_configuration,
        "stored network configuration differs from the live AP",
    )
    network_runtime = core.ConfiguredNetworkRuntime(
        capsule.network_manager, generation
    )
    context.network_runtime = network_runtime
    manager_truth = capsule.network_manager.snapshot()
    _require(
        network_runtime.manager is capsule.network_manager
        and network_runtime.restart_required(config_manager) is False
        and config_manager.generation == generation
        and config_manager.network_start_allowed is True
        and manager_truth.get("running") is True
        and manager_truth.get("closed") is False
        and manager_truth.get("faulted") is False,
        "configured network adoption failed",
    )
    _require(
        capsule.port.access_point_status()
        == {"active": True, "ip": AP_IP, "clients": 1},
        "live AP truth changed before full composition",
    )
    context.memory_after_configuration_adoption = _seam.require_heap(
        _seam.memory_free(), MINIMUM_FREE_HEAP_BYTES,
        "configuration and live-network adoption",
    )
    if core.ticks_diff(core.ticks_ms(), capsule.observation_deadline) >= 0:
        context.failure_stage = "observe_timeout"
        raise RuntimeError("manual observation window expired")

    context.failure_stage = "rest_composition"
    configured_maximum = config_manager.public_snapshot()["configuration"][
        "heater"
    ]["maximum_runtime_minutes"]
    _require(
        type(configured_maximum) is int and configured_maximum > 0,
        "configured maximum runtime is invalid",
    )
    protocol_port = _seam.NullProtocolPort()
    context.protocol_port = protocol_port
    controller = core.HeaterController(
        protocol_port,
        ticks_diff=core.ticks_diff,
        ticks_add=core.ticks_add,
        maximum_runtime_minutes=configured_maximum,
        temperature_manager=configured_runtime.temperature_manager,
    )
    context.controller = controller
    scheduler_gateway = core.SchedulerControllerGateway(
        configured_runtime.scheduler,
        controller,
        ticks_ms=core.ticks_ms,
        persistence=config_manager,
    )
    context.scheduler_gateway = scheduler_gateway
    random_provider = _seam.CountedSystemRandom()
    context.random_provider = random_provider
    build_rest_runtime, build_http_server, socket_factory = _load_rest_runtime()
    rest_runtime = build_rest_runtime(
        config_manager, configured_runtime, controller, scheduler_gateway,
        random_provider, (AP_IP,), "ap",
        configured_network_runtime=network_runtime,
        ticks_ms=core.ticks_ms, ticks_diff=core.ticks_diff,
        ticks_add=core.ticks_add,
    )
    context.rest_runtime = rest_runtime
    _require(rest_runtime.start() is True, "REST security did not start")
    security = rest_runtime.security_policy.snapshot()
    _require(
        security["started"] is True
        and security["mutation_api_available"] is True
        and random_provider.calls == 1
        and random_provider.last_count == 32
        and type(random_provider.secret) is bytearray
        and len(random_provider.secret) == 32,
        "REST security lifecycle is invalid",
    )
    state.gate.seal_security(rest_runtime)
    real_factory = socket_factory or _seam.real_socket_factory
    observed_factory = _seam.LateSocketFactory(real_factory, state)
    state.socket_factory = observed_factory
    server = build_http_server(
        state.gate, AP_IP, socket_factory=observed_factory,
        ticks_ms=core.ticks_ms, ticks_diff=core.ticks_diff,
        ticks_add=core.ticks_add,
    )
    state.server = server
    _verify_frozen_origins(_REST_FROZEN_ORIGINS)
    if core.ticks_diff(core.ticks_ms(), capsule.observation_deadline) >= 0:
        context.failure_stage = "observe_timeout"
        raise RuntimeError("manual observation window expired")
    context.failure_stage = "confirm_association"
    _require(
        capsule.port.access_point_status()
        == {"active": True, "ip": AP_IP, "clients": 1},
        "phone association was lost before HTTP bind",
    )
    context.observation_deadline = capsule.observation_deadline
    context.password = temporary_password
    context.window_seconds = window_seconds
    context.failure_stage = "http_bind"
    return None
