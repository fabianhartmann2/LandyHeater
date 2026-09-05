"""Explicit, bounded ESP32-v1.28 Phase-7 radio smoke.

Importing this module is inert: it imports neither ``board_config`` nor the
MicroPython ``network`` or ``machine`` modules.  :func:`run` is the only entry
point.  It requires an exact confirmation token, verifies that every unrelated
hardware path remains locked, and temporarily opens the Phase-7 WLAN lock only
in RAM.

Each iteration exercises the production WLAN factory and NetworkManager.  A
short-lived WPA2 access point is brought up before one deliberately absent
station profile is attempted.  No password is returned, printed, or included
in an error.  A pass is emitted only after the manager and port have closed and
both singleton WLAN interfaces have independently reported ``active()==False``.
"""

import gc as _gc


RADIO_SMOKE_CONFIRMATION = "PHASE7_WIFI_RADIO_SMOKE_CONFIRM_V1"
PHASE7_PASS_TOKEN = "PHASE7_WIFI_RADIO_SMOKE_PASS_V1"
DEFAULT_ITERATIONS = 1
MAX_ITERATIONS = 4
MINIMUM_FREE_HEAP_BYTES = 32 * 1024
EXPECTED_MACHINE_NAME = "DFRobot DFR0975-U N16R8 with ESP32S3"

# These credentials exist only for the seconds-long explicit board smoke.
# They are intentionally private and must never be copied into any result,
# event, diagnostic line, or exception.
_SMOKE_AP_PASSWORD = "Phase7RadioOnly!92"
_SMOKE_STA_PASSWORD = "Phase7NoStation!92"
_ABSENT_STA_SSID = "Landy-P7-No-Such-AP-7F3A"
_ABSENT_PROFILE_ID = "phase7-radio-smoke-no-ap"


def _require(condition, message):
    if not condition:
        raise RuntimeError("phase7 network smoke failed: {}".format(message))


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
    if type(value) is not int or value < 0:
        raise RuntimeError("gc.mem_free() returned an invalid value")
    return value


def _load_runtime():
    """Load the production network path only after explicit confirmation."""

    import board_config
    from app.network_manager import NetworkManager
    from hardware.micropython_wifi import open_wifi_from_board_config
    import time

    return (
        board_config,
        NetworkManager,
        open_wifi_from_board_config,
        getattr(time, "ticks_ms", _plain_ticks_ms),
        getattr(time, "ticks_add", _plain_ticks_add),
        getattr(time, "ticks_diff", _plain_ticks_diff),
    )


def _verify_platform(board_config):
    """Reject every target except the approved ESP32 MicroPython v1.28.0."""

    import os
    import sys

    implementation = getattr(sys, "implementation", None)
    _require(
        getattr(implementation, "name", None) == "micropython",
        "MicroPython is required",
    )
    version = getattr(implementation, "version", ())
    try:
        version_triplet = (version[0], version[1], version[2])
    except (IndexError, TypeError):
        version_triplet = None
    _require(version_triplet == (1, 28, 0), "MicroPython 1.28.0 is required")
    _require(getattr(sys, "platform", None) == "esp32", "ESP32 is required")
    uname = os.uname()
    machine_name = getattr(uname, "machine", "")
    _require(
        machine_name == EXPECTED_MACHINE_NAME,
        "DFR0975-U firmware machine identity differs",
    )
    _require(
        board_config.BOARD_SKU == "DFR0975-U"
        and board_config.BOARD_HARDWARE_REVISION == "1.0"
        and board_config.BOARD_MODULE == "ESP32-S3-WROOM-1U-N16R8"
        and board_config.MICROPYTHON_TARGET == "ESP32_GENERIC_S3"
        and board_config.MICROPYTHON_VARIANT == "SPIRAM_OCT"
        and board_config.MICROPYTHON_BUILD_BOARD == "DFR0975U_N16R8"
        and board_config.MICROPYTHON_VERSION == "1.28.0",
        "board firmware profile differs",
    )
    return True


def _verify_hardware_locks(board_config):
    """Prove that this smoke owns only the two WLAN interfaces."""

    _require(
        board_config.WIFI_RADIO_APPROVED is False,
        "the delivered Wi-Fi lock is not false",
    )
    _require(
        board_config.UART_PROTOCOL_TX_ENABLED is False,
        "UART protocol transmission is not locked",
    )
    _require(
        board_config.UART_PINS_APPROVED is False
        and board_config.UART_TX_GATE_PIN == 12
        and board_config.UART_TX_GATE_ACTIVE_LEVEL == 1
        and board_config.UART_TX_GATE_APPROVED is False,
        "UART pins or TX gate are not locked",
    )
    _require(
        board_config.ONEWIRE_PIN == 4
        and board_config.ONEWIRE_PIN_APPROVED is True,
        "approved 1-Wire route differs",
    )
    _require(
        board_config.I2C_ID == 1
        and board_config.I2C_SDA_PIN == 10
        and board_config.I2C_SCL_PIN == 11
        and board_config.I2C_PINS_APPROVED is False,
        "I2C is not locked",
    )
    return True


def _check_platform_ticks(ticks_ms, ticks_add, ticks_diff):
    for function in (ticks_ms, ticks_add, ticks_diff):
        _require(callable(function), "wrap-safe tick primitive is unavailable")
    now_ms = ticks_ms()
    _require(type(now_ms) is int, "ticks_ms() returned a non-integer")
    future_ms = ticks_add(now_ms, 37)
    _require(
        type(future_ms) is int and ticks_diff(future_ms, now_ms) == 37,
        "wrap-safe tick primitives are inconsistent",
    )
    return True


def _check_memory(before, after_import, after_cleanup):
    values = (before, after_import, after_cleanup)
    _require(
        all(type(value) is int for value in values),
        "MicroPython heap measurements are unavailable",
    )
    for value in values:
        _require(value >= MINIMUM_FREE_HEAP_BYTES, "free heap is below 32 KiB")
    return True


def _load_network_module():
    # This helper is called only after the approved production factory has
    # opened its driver lease.  Merely importing this smoke remains inert.
    return __import__("network")


def _network_constant(network_module, name):
    wlan = getattr(network_module, "WLAN", None)
    value = getattr(wlan, name, None)
    if value is None:
        value = getattr(network_module, name, None)
    _require(type(value) is int, "network interface constant is malformed")
    return value


def _interface_objects(network_module):
    wlan = getattr(network_module, "WLAN", None)
    _require(callable(wlan), "network.WLAN is unavailable")
    return (
        wlan(_network_constant(network_module, "IF_STA")),
        wlan(_network_constant(network_module, "IF_AP")),
    )


def _interfaces_inactive(network_module):
    try:
        interfaces = _interface_objects(network_module)
        for interface in interfaces:
            active = getattr(interface, "active", None)
            if not callable(active) or active() is not False:
                return False
        return True
    except BaseException:
        return False


def _emergency_radio_off(network_module):
    """Best-effort fail-safe used only when production cleanup did not prove off."""

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
    """Close through production owners, independently verify, then fail safe."""

    manager_ok = _retry_deinit(manager, port)
    port_ok = _retry_deinit(port, port)
    cleanup_complete = (
        port is None or getattr(port, "cleanup_complete", False) is True
    )
    inactive = (
        network_module is not None
        and _interfaces_inactive(network_module)
    )
    production_ok = manager_ok and port_ok and cleanup_complete and inactive
    if not inactive and network_module is not None:
        _emergency_radio_off(network_module)
    # Emergency shutdown protects the board but can never turn a failed
    # production cleanup into a passing result.
    return bool(production_ok)


def _assert_redacted(*values):
    rendered = repr(values)
    _require(
        _SMOKE_AP_PASSWORD not in rendered
        and _SMOKE_STA_PASSWORD not in rendered,
        "a smoke credential escaped into diagnostics",
    )


def _sanitized_raise(error):
    if isinstance(error, KeyboardInterrupt):
        raise KeyboardInterrupt() from None
    if isinstance(error, SystemExit):
        raise SystemExit() from None
    if isinstance(error, MemoryError):
        raise MemoryError() from None
    # Do not retain a vendor exception context: a driver is allowed to echo a
    # key in its own message, while the smoke contract never is.
    raise RuntimeError("phase7 network smoke failed") from None


def _run_iteration(
    NetworkManager,
    factory,
    ticks_ms,
    ticks_add,
    ticks_diff,
):
    port = None
    manager = None
    network_module = None
    primary = None
    summary = None
    try:
        port = factory()
        network_module = _load_network_module()
        _require(
            _interfaces_inactive(network_module),
            "a WLAN interface was active before the smoke",
        )
        configuration = {
            "hostname": "heater",
            "access_point": {
                "ssid": "Landy Heater",
                "password": _SMOKE_AP_PASSWORD,
            },
            "known_networks": [{
                "id": _ABSENT_PROFILE_ID,
                "ssid": _ABSENT_STA_SSID,
                "password": _SMOKE_STA_PASSWORD,
            }],
        }
        manager = NetworkManager(
            port,
            configuration,
            ticks_add=ticks_add,
            ticks_diff=ticks_diff,
            ap_check_interval_ms=10,
            station_poll_interval_ms=5,
            connection_timeout_ms=20,
            profile_gap_ms=1,
            round_backoff_ms=1,
        )
        start_ms = ticks_ms()
        _require(manager.start(start_ms) is True, "manager did not start")
        actions = (
            manager.step(start_ms),
            manager.step(ticks_add(start_ms, 1)),
            manager.step(ticks_add(start_ms, 2)),
            manager.step(ticks_add(start_ms, 3)),
            manager.step(ticks_add(start_ms, 13)),
        )
        _require(
            actions
            == (
                "hostname_configured",
                "ap_available",
                "station_ready",
                "station_connecting",
                "ap_checked",
            ),
            "AP-before-STA action order differs",
        )

        # The final manager action performs a fresh AP driver read after the
        # station attempt was started.  A direct port observation adds an
        # independent check of both the AP IP and AP-only mDNS truth.
        ap_status = port.access_point_status()
        station_status = port.station_status()
        snapshot = manager.snapshot()
        events = manager.drain_events()
        _assert_redacted(ap_status, station_status, snapshot, events, actions)

        _require(ap_status.get("active") is True, "AP stopped during STA work")
        ap_ip = ap_status.get("ip")
        _require(
            type(ap_ip) is str and ap_ip and ap_ip != "0.0.0.0",
            "AP has no direct IP address",
        )
        _require(
            station_status.get("connected") is False
            and station_status.get("mdns_ready") is False,
            "the absent station profile unexpectedly established mDNS",
        )
        _require(
            snapshot["access_point"]["ssid"] == "Landy Heater"
            and snapshot["access_point"]["active"] is True
            and snapshot["access_point"]["ip"] == ap_ip,
            "manager AP truth differs from the driver",
        )
        _require(
            snapshot["counters"]["attempts"] == 1,
            "exactly one station attempt was not recorded",
        )
        _require(
            snapshot["mdns"]["ready"] is False
            and snapshot["mdns"]["ap_only_guaranteed"] is False,
            "AP-only mDNS was incorrectly reported ready",
        )
        summary = {"ap_ip": ap_ip, "station_attempts": 1}
    except BaseException as error:
        primary = error
    finally:
        if network_module is None and port is not None:
            try:
                network_module = _load_network_module()
            except BaseException:
                network_module = None
        cleanup_ok = _cleanup_radio(manager, port, network_module)

    if primary is not None:
        _sanitized_raise(primary)
    _require(cleanup_ok, "production radio cleanup was not confirmed")
    _require(summary is not None, "iteration produced no result")
    return summary


def run(confirmation, iterations=DEFAULT_ITERATIONS):
    """Run the explicitly armed, bounded Phase-7 radio smoke."""

    if (
        type(confirmation) is not str
        or confirmation != RADIO_SMOKE_CONFIRMATION
    ):
        raise RuntimeError("exact Phase-7 Wi-Fi radio confirmation is required")
    if type(iterations) is not int or not 1 <= iterations <= MAX_ITERATIONS:
        raise ValueError("iterations must be an integer from 1 to 4")

    memory_before = _memory_free()
    runtime = _load_runtime()
    (
        board_config,
        NetworkManager,
        factory,
        ticks_ms,
        ticks_add,
        ticks_diff,
    ) = runtime
    _verify_hardware_locks(board_config)
    _verify_platform(board_config)
    memory_after_import = _memory_free()
    _require(
        type(memory_before) is int
        and type(memory_after_import) is int
        and memory_before >= MINIMUM_FREE_HEAP_BYTES
        and memory_after_import >= MINIMUM_FREE_HEAP_BYTES,
        "MicroPython free heap is unavailable or below 32 KiB",
    )
    ticks_checked = _check_platform_ticks(ticks_ms, ticks_add, ticks_diff)

    summaries = []
    primary = None
    try:
        board_config.WIFI_RADIO_APPROVED = True
        # The production factory re-validates this RAM-only approval before
        # importing or leasing MicroPython's network driver.
        for _ in range(iterations):
            summaries.append(_run_iteration(
                NetworkManager,
                factory,
                ticks_ms,
                ticks_add,
                ticks_diff,
            ))
    except BaseException as error:
        primary = error
    finally:
        board_config.WIFI_RADIO_APPROVED = False

    if primary is not None:
        _sanitized_raise(primary)
    _require(
        board_config.WIFI_RADIO_APPROVED is False,
        "Wi-Fi approval was not restored",
    )
    memory_after_cleanup = _memory_free()
    memory_checked = _check_memory(
        memory_before, memory_after_import, memory_after_cleanup
    )
    _require(ticks_checked is True, "platform ticks were not checked")
    _require(len(summaries) == iterations, "an iteration is missing")
    _assert_redacted(summaries)

    result = {
        "phase": 7,
        "scope": "explicit_wifi_radio",
        "iterations": iterations,
        "passed": iterations,
        "ap_ssid": "Landy Heater",
        "ap_ip": summaries[-1]["ap_ip"],
        "station_attempts": sum(
            item["station_attempts"] for item in summaries
        ),
        "ap_available_during_station_attempt": True,
        "ap_only_mdns_ready": False,
        "radio_cleanup_confirmed": True,
        "approval_restored": True,
        "platform_checked": True,
        "ticks_checked": True,
        "memory_checked": memory_checked,
        "memory_before": memory_before,
        "memory_after_import": memory_after_import,
        "memory_after_cleanup": memory_after_cleanup,
    }
    _assert_redacted(result)
    print("PHASE 7 WIFI RADIO SMOKE PASS: {}/{}".format(
        iterations, iterations
    ))
    print("ap_ssid=Landy Heater")
    print("ap_ip={}".format(result["ap_ip"]))
    print("station_attempts={}".format(result["station_attempts"]))
    print("radio_cleanup_confirmed=True")
    print(PHASE7_PASS_TOKEN)
    return result
