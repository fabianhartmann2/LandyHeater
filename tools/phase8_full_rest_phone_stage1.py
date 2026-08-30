"""AP-only first stage for the Phase-8 full-product phone smoke.

Importing this module is inert.  ``prepare()`` opens only the production
Wi-Fi boundary, starts the Landy Heater access point, and waits for exactly
one associated station.  It deliberately imports no HTTP implementation and
opens no socket.  The sole IPv4/TCP proof is the later real
``GET /api/v1/status`` request on the one product listener.
"""

import gc as _gc


FULL_REST_PHONE_AP_READY_TOKEN = "PHASE8_FULL_REST_PHONE_AP_READY_V1"
FULL_REST_PHONE_CLIENT_TOKEN = "PHASE8_FULL_REST_PHONE_CLIENT_SEEN_V1"

AP_SSID = "Landy Heater"
AP_IP = "192.168.4.1"
MINIMUM_FREE_HEAP_BYTES = 32 * 1024
POLL_INTERVAL_MS = 25
AP_CHECK_INTERVAL_MS = 1000
STARTUP_TIMEOUT_MS = 15000

_NETWORK_FROZEN_ORIGINS = (
    ("app.network_manager", "app/network_manager.py"),
    ("hardware.micropython_wifi", "hardware/micropython_wifi.py"),
)


def _require(condition, message):
    if not condition:
        raise RuntimeError(
            "Phase-8 full REST phone smoke failed: {}".format(message)
        )


def _plain_ticks_ms():
    return 0


def _plain_ticks_add(value, delta):
    return value + delta


def _plain_ticks_diff(newer, older):
    return newer - older


def _memory_free():
    _gc.collect()
    reader = getattr(_gc, "mem_free", None)
    if not callable(reader):
        return None
    value = reader()
    _require(type(value) is int and value >= 0, "invalid heap reading")
    return value


def _require_heap(value, checkpoint):
    _require(
        type(value) is int and value >= MINIMUM_FREE_HEAP_BYTES,
        "free heap at {} is unavailable or below 32 KiB".format(checkpoint),
    )
    return value


def _load_wifi_runtime():
    """Load only the production Wi-Fi boundary before full composition."""

    import sys
    import time
    import board_config
    from app.network_manager import NetworkManager
    import hardware.micropython_wifi as wifi_module

    sleep_ms = getattr(time, "sleep_ms", None)
    if not callable(sleep_ms):
        sleep = getattr(time, "sleep", None)
        if callable(sleep):
            sleep_ms = lambda milliseconds: sleep(milliseconds / 1000.0)
    return (
        sys,
        board_config,
        NetworkManager,
        wifi_module,
        wifi_module.open_wifi_from_board_config,
        getattr(time, "ticks_ms", _plain_ticks_ms),
        getattr(time, "ticks_add", _plain_ticks_add),
        getattr(time, "ticks_diff", _plain_ticks_diff),
        sleep_ms,
    )


def _verify_frozen_origins(sys_module, expected):
    if (
        type(sys_module.path) is not list
        or not sys_module.path
        or sys_module.path[0] != ".frozen"
    ):
        raise RuntimeError("frozen module path is not first")
    for module_name, origin in expected:
        module = sys_module.modules.get(module_name)
        actual = None if module is None else getattr(module, "__file__", None)
        if actual != origin or actual.startswith("/"):
            raise RuntimeError("required frozen module origin is invalid")
    return True


def _assert_redacted(password, *values):
    if password in repr(values):
        raise RuntimeError("Phase-8 full REST phone smoke leaked its key")


def _sleep_checked(sleep_ms, milliseconds):
    result = sleep_ms(milliseconds)
    if result is not None:
        raise RuntimeError("sleep_ms returned a value")


def prepare(capsule, password, window_seconds):
    """Start one AP lifetime and publish a confirmed station association."""

    support = None
    board_config = None
    wifi_module = None
    port = None
    network_module = None
    manager = None
    live_configuration = None
    failure_stage = "stage1_preflight"
    client_seen = False
    last_clients = -1
    last_action = "none"
    memory_before = _memory_free()
    memory_after_wifi_factory = None
    memory_after_ap_ready = None
    memory_after_client_association = None

    try:
        support = capsule.support
        _require(support is not None, "radio safety support is unavailable")
        for name in (
            "_verify_hardware_locks",
            "_verify_platform",
            "_check_platform_ticks",
            "_load_network_module",
            "_interfaces_inactive",
            "_cleanup_radio",
        ):
            _require(
                callable(getattr(support, name, None)),
                "radio safety support contract is invalid",
            )
        (
            sys_module,
            board_config,
            NetworkManager,
            wifi_module,
            factory,
            ticks_ms,
            ticks_add,
            ticks_diff,
            sleep_ms,
        ) = _load_wifi_runtime()
        capsule.board_config = board_config
        capsule.wifi_module = wifi_module
        support._verify_hardware_locks(board_config)
        support._verify_platform(board_config)
        support._check_platform_ticks(ticks_ms, ticks_add, ticks_diff)
        _verify_frozen_origins(sys_module, _NETWORK_FROZEN_ORIGINS)
        _require_heap(_memory_free(), "Wi-Fi-only imports")

        board_config.WIFI_RADIO_APPROVED = True
        port = factory()
        capsule.port = port
        network_module = support._load_network_module()
        capsule.network_module = network_module
        _require(
            support._interfaces_inactive(network_module),
            "a WLAN interface was active before the phone test",
        )
        memory_after_wifi_factory = _require_heap(
            _memory_free(), "Wi-Fi factory construction"
        )
        live_configuration = {
            "hostname": "heater",
            "access_point": {"ssid": AP_SSID, "password": password},
            "known_networks": [],
        }
        manager = NetworkManager(
            port,
            live_configuration,
            ticks_add=ticks_add,
            ticks_diff=ticks_diff,
            ap_check_interval_ms=AP_CHECK_INTERVAL_MS,
        )
        capsule.network_manager = manager
        capsule.live_network_configuration = live_configuration
        capsule.ticks_ms = ticks_ms
        capsule.ticks_add = ticks_add
        capsule.ticks_diff = ticks_diff
        capsule.sleep_ms = sleep_ms

        failure_stage = "stage1_ap_startup"
        started_ms = ticks_ms()
        _require(manager.start(started_ms) is True, "network did not start")
        startup_deadline = ticks_add(started_ms, STARTUP_TIMEOUT_MS)
        access_point = None
        while True:
            now_ms = ticks_ms()
            action = manager.step(now_ms)
            snapshot = manager.snapshot()
            events = manager.drain_events()
            _assert_redacted(password, action, snapshot, events)
            access_point = snapshot["access_point"]
            clients = access_point["clients"]
            if access_point["active"] is True:
                _require(
                    access_point["ip"] == AP_IP
                    and type(clients) is int
                    and 0 <= clients <= 1,
                    "access point readiness truth is invalid",
                )
                last_clients = clients
                break
            if ticks_diff(now_ms, startup_deadline) >= 0:
                raise RuntimeError("access point startup timed out")
            _sleep_checked(sleep_ms, POLL_INTERVAL_MS)

        action = None
        snapshot = None
        events = None
        access_point = None
        memory_after_ap_ready = _require_heap(_memory_free(), "AP readiness")
        observation_deadline = ticks_add(ticks_ms(), window_seconds * 1000)
        capsule.observation_deadline = observation_deadline
        print(FULL_REST_PHONE_AP_READY_TOKEN)
        print("ssid={}".format(AP_SSID))
        print("window_seconds={}".format(window_seconds))
        print("Connect one phone with Automatic IP and wait for READY.")

        while True:
            failure_stage = "stage1_observe_deadline"
            now_ms = ticks_ms()
            if ticks_diff(now_ms, observation_deadline) >= 0:
                raise RuntimeError("one associated phone was not observed")
            failure_stage = "stage1_observe_network_step"
            action = manager.step(now_ms)
            snapshot = manager.snapshot()
            events = manager.drain_events()
            _assert_redacted(password, action, snapshot, events)
            failure_stage = "stage1_observe_network_truth"
            access_point = snapshot["access_point"]
            clients = access_point["clients"]
            _require(
                access_point["active"] is True
                and access_point["ip"] == AP_IP
                and type(clients) is int
                and 0 <= clients <= 1,
                "access point truth changed during association",
            )
            if action is not None and action != "ap_checked":
                last_action = "other"
                raise RuntimeError("network changed state during association")
            last_action = "ap_checked" if action == "ap_checked" else "none"
            last_clients = clients
            if clients == 1:
                client_seen = True
                print(FULL_REST_PHONE_CLIENT_TOKEN)
                print("clients=1")
                break
            _sleep_checked(sleep_ms, POLL_INTERVAL_MS)

        failure_stage = "stage1_confirm_association"
        association_truth = port.access_point_status()
        _assert_redacted(password, association_truth)
        _require(
            association_truth == {"active": True, "ip": AP_IP, "clients": 1},
            "direct phone association truth is invalid",
        )
        memory_after_client_association = _require_heap(
            _memory_free(), "client association"
        )

        capsule.memory_before = memory_before
        capsule.memory_after_wifi_factory = memory_after_wifi_factory
        capsule.memory_after_ap_ready = memory_after_ap_ready
        capsule.memory_after_client_association = (
            memory_after_client_association
        )
        capsule.association_confirmed = True
        capsule.associated_clients = 1
        capsule.stage1_failure_stage = None
        capsule.stage1_client_seen = True
        capsule.stage1_ap_clients = 1
        capsule.stage1_action = last_action
        return None
    except BaseException:
        if capsule.port is None and port is not None:
            capsule.port = port
        if capsule.network_manager is None and manager is not None:
            capsule.network_manager = manager
        if capsule.network_module is None and network_module is not None:
            capsule.network_module = network_module
        if capsule.support is None and support is not None:
            capsule.support = support
        if capsule.board_config is None and board_config is not None:
            capsule.board_config = board_config
        if capsule.wifi_module is None and wifi_module is not None:
            capsule.wifi_module = wifi_module
        try:
            capsule.stage1_failure_stage = failure_stage
            capsule.stage1_client_seen = client_seen
            capsule.stage1_ap_clients = last_clients
            capsule.stage1_action = last_action
            capsule.memory_before = memory_before
            capsule.memory_after_wifi_factory = memory_after_wifi_factory
            capsule.memory_after_ap_ready = memory_after_ap_ready
            capsule.memory_after_client_association = (
                memory_after_client_association
            )
        except BaseException:
            pass
        raise
