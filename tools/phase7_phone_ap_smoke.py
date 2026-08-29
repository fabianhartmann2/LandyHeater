"""Explicit, bounded phone-association smoke for the Phase-7 WPA2 AP.

Importing this module is inert.  :func:`run` requires an exact confirmation,
an explicitly supplied temporary WPA2 password and a bounded observation
window.  It opens only the production Wi-Fi factory/NetworkManager path, never
starts an HTTP server and emits its pass token only after both ESP32 WLAN
interfaces have independently been verified inactive.
"""

import gc as _gc


PHONE_AP_CONFIRMATION = "PHASE7_PHONE_AP_CONFIRM_V1"
PHONE_AP_READY_TOKEN = "PHASE7_PHONE_AP_READY_V1"
PHONE_AP_CLIENT_TOKEN = "PHASE7_PHONE_AP_CLIENT_SEEN_V1"
PHONE_AP_PASS_TOKEN = "PHASE7_PHONE_AP_SMOKE_PASS_V1"
PHONE_AP_FAIL_TOKEN = "PHASE7_PHONE_AP_SMOKE_FAIL_V1"
MINIMUM_PASSWORD_BYTES = 12
MAXIMUM_PASSWORD_BYTES = 63
MINIMUM_WINDOW_SECONDS = 60
MAXIMUM_WINDOW_SECONDS = 300
DEFAULT_WINDOW_SECONDS = 180
MINIMUM_FREE_HEAP_BYTES = 32 * 1024
POLL_INTERVAL_MS = 250
AP_CHECK_INTERVAL_MS = 1000
STARTUP_TIMEOUT_MS = 15000
REQUIRED_CLIENT_OBSERVATIONS = 3
POST_CONFIRM_HOLD_MS = 30000


def _require(condition, message):
    if not condition:
        raise RuntimeError("Phase-7 phone AP smoke failed: {}".format(message))


def _validate_password(value):
    if type(value) is not str:
        raise ValueError("temporary WPA2 password must be a string")
    try:
        encoded = value.encode("ascii")
    except (UnicodeError, ValueError):
        raise ValueError("temporary WPA2 password must be printable ASCII")
    if not MINIMUM_PASSWORD_BYTES <= len(encoded) <= MAXIMUM_PASSWORD_BYTES:
        raise ValueError("temporary WPA2 password must contain 12 to 63 bytes")
    for byte in encoded:
        if byte < 0x20 or byte > 0x7E:
            raise ValueError("temporary WPA2 password must be printable ASCII")
    return value


def _validate_window_seconds(value):
    if (
        type(value) is not int
        or not MINIMUM_WINDOW_SECONDS <= value <= MAXIMUM_WINDOW_SECONDS
    ):
        raise ValueError("observation window must be 60 to 300 seconds")
    return value


def _memory_free():
    _gc.collect()
    reader = getattr(_gc, "mem_free", None)
    if not callable(reader):
        return None
    value = reader()
    _require(type(value) is int and value >= 0, "invalid heap reading")
    return value


def _load_runtime():
    # The existing radio smoke owns the shared, independently tested cleanup
    # and platform checks.  Importing it is hardware-inert as well.
    import tools.phase7_network_smoke as support
    import board_config
    from app.network_manager import NetworkManager
    from hardware.micropython_wifi import open_wifi_from_board_config
    import time

    sleep_ms = getattr(time, "sleep_ms", None)
    if not callable(sleep_ms):
        sleep = getattr(time, "sleep", None)
        if callable(sleep):
            sleep_ms = lambda milliseconds: sleep(milliseconds / 1000.0)
    return (
        support,
        board_config,
        NetworkManager,
        open_wifi_from_board_config,
        getattr(time, "ticks_ms", None),
        getattr(time, "ticks_add", None),
        getattr(time, "ticks_diff", None),
        sleep_ms,
    )


def _assert_redacted(password, *values):
    if password in repr(values):
        raise RuntimeError("Phase-7 phone AP smoke leaked its temporary key")


def _sanitized_raise(error):
    if isinstance(error, KeyboardInterrupt):
        raise KeyboardInterrupt() from None
    if isinstance(error, SystemExit):
        raise SystemExit() from None
    if isinstance(error, MemoryError):
        raise MemoryError() from None
    raise RuntimeError("Phase-7 phone AP smoke failed") from None


def _sleep_checked(sleep_ms, milliseconds):
    result = sleep_ms(milliseconds)
    if result is not None:
        raise RuntimeError("sleep_ms returned a value")


def run(
    confirmation,
    temporary_password,
    window_seconds=DEFAULT_WINDOW_SECONDS,
):
    """Wait for one stable phone association, then prove radio cleanup."""

    if type(confirmation) is not str or confirmation != PHONE_AP_CONFIRMATION:
        raise RuntimeError("exact Phase-7 phone AP confirmation is required")
    password = _validate_password(temporary_password)
    window_seconds = _validate_window_seconds(window_seconds)

    memory_before = _memory_free()
    (
        support,
        board_config,
        NetworkManager,
        factory,
        ticks_ms,
        ticks_add,
        ticks_diff,
        sleep_ms,
    ) = _load_runtime()
    support._verify_hardware_locks(board_config)
    support._verify_platform(board_config)
    for function in (ticks_ms, ticks_add, ticks_diff, sleep_ms):
        _require(callable(function), "required MicroPython timing API is missing")
    support._check_platform_ticks(ticks_ms, ticks_add, ticks_diff)
    memory_after_import = _memory_free()
    _require(
        type(memory_before) is int
        and type(memory_after_import) is int
        and memory_before >= MINIMUM_FREE_HEAP_BYTES
        and memory_after_import >= MINIMUM_FREE_HEAP_BYTES,
        "free heap is unavailable or below 32 KiB",
    )

    port = None
    manager = None
    network_module = None
    primary = None
    detected_clients = 0
    ap_ip = None
    consecutive_observations = 0
    try:
        board_config.WIFI_RADIO_APPROVED = True
        port = factory()
        network_module = support._load_network_module()
        _require(
            support._interfaces_inactive(network_module),
            "a WLAN interface was active before the phone test",
        )
        configuration = {
            "hostname": "heater",
            "access_point": {
                "ssid": "Landy Heater",
                "password": password,
            },
            "known_networks": [],
        }
        manager = NetworkManager(
            port,
            configuration,
            ticks_add=ticks_add,
            ticks_diff=ticks_diff,
            ap_check_interval_ms=AP_CHECK_INTERVAL_MS,
        )
        started_ms = ticks_ms()
        _require(manager.start(started_ms) is True, "manager did not start")
        startup_deadline = ticks_add(started_ms, STARTUP_TIMEOUT_MS)

        while ap_ip is None:
            now_ms = ticks_ms()
            action = manager.step(now_ms)
            snapshot = manager.snapshot()
            _assert_redacted(password, action, snapshot, manager.drain_events())
            access_point = snapshot["access_point"]
            if access_point["active"] is True:
                ap_ip = access_point["ip"]
                _require(
                    type(ap_ip) is str and ap_ip and ap_ip != "0.0.0.0",
                    "access point has no direct IP address",
                )
                break
            if ticks_diff(now_ms, startup_deadline) >= 0:
                raise RuntimeError("access point startup timed out")
            _sleep_checked(sleep_ms, POLL_INTERVAL_MS)

        baseline = manager.snapshot()["access_point"]
        _require(
            baseline["clients"] == 0,
            "an access-point client was present before the test began",
        )
        print(PHONE_AP_READY_TOKEN)
        print("ssid=Landy Heater")
        print("ap_ip={}".format(ap_ip))
        print("window_seconds={}".format(window_seconds))
        print("Connect the phone now; no web page is expected.")

        observation_deadline = ticks_add(
            ticks_ms(), window_seconds * 1000
        )
        while consecutive_observations < REQUIRED_CLIENT_OBSERVATIONS:
            now_ms = ticks_ms()
            action = manager.step(now_ms)
            snapshot = manager.snapshot()
            events = manager.drain_events()
            _assert_redacted(password, action, snapshot, events)
            if action is not None and action != "ap_checked":
                raise RuntimeError(
                    "access point changed state during phone observation"
                )
            if action == "ap_checked":
                access_point = snapshot["access_point"]
                _require(
                    access_point["active"] is True
                    and access_point["ip"] == ap_ip,
                    "access point truth changed during observation",
                )
                clients = access_point["clients"]
                _require(
                    type(clients) is int and 0 <= clients <= 4,
                    "access point client count is invalid",
                )
                if clients == 1:
                    if consecutive_observations == 0:
                        print(PHONE_AP_CLIENT_TOKEN)
                        print("clients=1")
                    consecutive_observations += 1
                    if clients > detected_clients:
                        detected_clients = clients
                elif clients > 1:
                    raise RuntimeError("more than one AP client was detected")
                else:
                    consecutive_observations = 0
            if consecutive_observations >= REQUIRED_CLIENT_OBSERVATIONS:
                break
            if ticks_diff(now_ms, observation_deadline) >= 0:
                raise RuntimeError("no phone joined the test access point")
            _sleep_checked(sleep_ms, POLL_INTERVAL_MS)

        print("PHONE_CLIENT_CONFIRMED clients={}".format(detected_clients))
        hold_deadline = ticks_add(ticks_ms(), POST_CONFIRM_HOLD_MS)
        while True:
            now_ms = ticks_ms()
            action = manager.step(now_ms)
            snapshot = manager.snapshot()
            events = manager.drain_events()
            _assert_redacted(password, action, snapshot, events)
            if action is not None and action != "ap_checked":
                raise RuntimeError(
                    "access point changed state during stability hold"
                )
            if action == "ap_checked":
                access_point = snapshot["access_point"]
                _require(
                    access_point["active"] is True
                    and access_point["ip"] == ap_ip
                    and access_point["clients"] == 1,
                    "phone association was not stable during confirmation",
                )
                if ticks_diff(now_ms, hold_deadline) >= 0:
                    break
            _sleep_checked(sleep_ms, POLL_INTERVAL_MS)
    except BaseException as error:
        primary = error
    finally:
        board_config.WIFI_RADIO_APPROVED = False
        if network_module is None and port is not None:
            try:
                network_module = support._load_network_module()
            except BaseException:
                network_module = None
        cleanup_ok = support._cleanup_radio(manager, port, network_module)

    if primary is not None:
        print(PHONE_AP_FAIL_TOKEN)
        _sanitized_raise(primary)
    if not cleanup_ok:
        print(PHONE_AP_FAIL_TOKEN)
        raise RuntimeError(
            "Phase-7 phone AP smoke failed: production cleanup was not confirmed"
        )
    _require(
        board_config.WIFI_RADIO_APPROVED is False,
        "Wi-Fi approval was not restored",
    )
    memory_after_cleanup = _memory_free()
    if not (
        type(memory_after_cleanup) is int
        and memory_after_cleanup >= MINIMUM_FREE_HEAP_BYTES
    ):
        print(PHONE_AP_FAIL_TOKEN)
        raise RuntimeError(
            "Phase-7 phone AP smoke failed: free heap after cleanup is too low"
        )

    result = {
        "phase": 7,
        "scope": "manual_phone_ap_association",
        "ssid": "Landy Heater",
        "ap_ip": ap_ip,
        "clients_confirmed": detected_clients,
        "window_seconds": window_seconds,
        "stable_seconds": POST_CONFIRM_HOLD_MS // 1000,
        "radio_cleanup_confirmed": True,
        "approval_restored": True,
        "memory_before": memory_before,
        "memory_after_import": memory_after_import,
        "memory_after_cleanup": memory_after_cleanup,
    }
    _assert_redacted(password, result)
    print("radio_cleanup_confirmed=True")
    print(PHONE_AP_PASS_TOKEN)
    return result
