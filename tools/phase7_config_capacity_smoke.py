"""USB-only Phase-7 maximum practical configuration capacity probe.

The probe performs no radio or GPIO operation.  It writes only two isolated
A/B namespaces, loads the largest useful printable schema-v2 shape through a
fresh ConfigManager, removes those exact files in ``finally`` and prints its
pass token only after cleanup has been confirmed.
"""

import gc as _gc
import os as _os


SOFTWARE_ONLY_CONFIRMATION = "PHASE7_CONFIG_CAPACITY_SMOKE_V1"
PHASE7_CONFIG_CAPACITY_PASS_TOKEN = "PHASE7_CONFIG_CAPACITY_PASS_V1"
CONFIG_BASE_PATH = "/phase7_config_capacity_v1_config"
LEDGER_BASE_PATH = "/phase7_config_capacity_v1_ledger"
CONFIG_RECORD_BYTES = 12 * 1024
MINIMUM_FREE_HEAP_BYTES = 32 * 1024


def _require(condition, message):
    if not condition:
        raise RuntimeError("Phase-7 config capacity failed: {}".format(message))


def _heap():
    _gc.collect()
    reader = getattr(_gc, "mem_free", None)
    if not callable(reader):
        return None
    value = reader()
    _require(type(value) is int and value >= 0, "invalid heap reading")
    _require(
        value >= MINIMUM_FREE_HEAP_BYTES,
        "free heap is below 32 KiB",
    )
    return value


def _require_heap_floor(value):
    if value is None:
        return None
    _require(
        type(value) is int and value >= MINIMUM_FREE_HEAP_BYTES,
        "free heap is below 32 KiB",
    )
    return value


def _paths():
    result = []
    for base in (CONFIG_BASE_PATH, LEDGER_BASE_PATH):
        for suffix in (".a", ".b", ".tmp"):
            result.append(base + suffix)
    return tuple(result)


def _remove_exact_files():
    for path in _paths():
        try:
            _os.remove(path)
        except OSError as error:
            code = getattr(error, "errno", None)
            if code is None and getattr(error, "args", None):
                code = error.args[0]
            if code != 2:
                raise


def _assert_absent():
    for path in _paths():
        try:
            _os.stat(path)
        except OSError as error:
            code = getattr(error, "errno", None)
            if code is None and getattr(error, "args", None):
                code = error.args[0]
            if code == 2:
                continue
            raise
        raise RuntimeError("capacity smoke file remains: {}".format(path))


def _maximum_useful_configuration(default_configuration):
    candidate = default_configuration()
    candidate["system"]["setup_complete"] = True
    candidate["heater"]["maximum_runtime_minutes"] = 120
    candidate["network"]["access_point"]["password"] = "A" * 63
    profiles = []
    for index in range(8):
        marker = "{:02d}".format(index)
        profiles.append({
            "id": ("profile-" + marker + ("I" * 16))[:16],
            "ssid": ("station-" + marker + ("S" * 16))[:16],
            "password": marker + ("P" * 14),
        })
    candidate["network"]["known_networks"] = profiles
    assignments = candidate["sensors"]["assignments"]
    assignments["roof_tent"] = "1" * 16
    assignments["cabin"] = "2" * 16
    assignments["outside"] = "3" * 16
    candidate["time"] = {
        "timezone_name": "Fixed-Zone-" + ("Z" * 5),
        "timezone_rule": "fixed",
        "timezone_rule_version": 1,
        "standard_utc_offset_minutes": 0,
    }
    timers = []
    for index in range(32):
        marker = "{:02d}".format(index)
        timers.append({
            "id": ("timer-" + marker + ("I" * 20))[:16],
            "name": ("Timer " + marker + " " + ("N" * 32))[:32],
            "enabled": True,
            "weekdays": [0, 1, 2, 3, 4, 5, 6],
            "start": "{:02d}:{:02d}".format(index % 24, index % 60),
            "mode": "power",
            "target_temperature": None,
            "power_level": 9,
            "runtime_minutes": 120,
        })
    candidate["timers"] = timers
    return candidate


def run(confirmation):
    if type(confirmation) is not str or confirmation != SOFTWARE_ONLY_CONFIRMATION:
        raise RuntimeError("exact Phase-7 capacity confirmation is required")

    before_import = _require_heap_floor(_heap())
    from adapters.config_file_store import AtomicJSONConfigStore
    from services.config_manager import (
        MAX_CONFIGURATION_CANONICAL_BYTES,
        ConfigManager,
        _canonical_json_size,
        default_configuration,
        default_scheduler_ledger,
        validate_configuration,
    )

    after_import = _require_heap_floor(_heap())
    _remove_exact_files()
    result = None
    try:
        candidate = validate_configuration(
            _maximum_useful_configuration(default_configuration)
        )
        canonical_bytes = _canonical_json_size(candidate)
        _require(
            7 * 1024 <= canonical_bytes <= MAX_CONFIGURATION_CANONICAL_BYTES,
            "capacity payload does not exercise the aggregate limit",
        )
        after_build = _require_heap_floor(_heap())
        manager = ConfigManager(
            AtomicJSONConfigStore(
                CONFIG_BASE_PATH, max_record_bytes=CONFIG_RECORD_BYTES
            ),
            AtomicJSONConfigStore(LEDGER_BASE_PATH),
        )
        _require(manager.load() is False, "config store was not empty")
        _require(
            manager.load_scheduler_checkpoint() is False,
            "ledger store was not empty",
        )
        _require(
            manager.checkpoint_scheduler(default_scheduler_ledger(), 0) is True,
            "ledger provisioning failed",
        )
        _require(manager.commit(candidate, 0) is True, "config commit failed")
        _require(manager.generation == 2, "config mirror is incomplete")
        _require(manager.ledger_generation == 2, "ledger mirror is incomplete")
        after_commit = _require_heap_floor(_heap())

        manager = None
        candidate = None
        _gc.collect()
        before_reload = _require_heap_floor(_heap())
        reloaded = ConfigManager(
            AtomicJSONConfigStore(
                CONFIG_BASE_PATH, max_record_bytes=CONFIG_RECORD_BYTES
            ),
            AtomicJSONConfigStore(LEDGER_BASE_PATH),
        )
        _require(reloaded.load() is True, "config reload was not trusted")
        _require(
            reloaded.load_scheduler_checkpoint() is True,
            "ledger reload was not trusted",
        )
        public = reloaded.public_snapshot()
        _require(
            len(public["configuration"]["timers"]) == 32,
            "timer capacity differs",
        )
        _require(
            len(public["configuration"]["network"]["known_networks"]) == 8,
            "network capacity differs",
        )
        rendered = repr(public)
        _require("A" * 63 not in rendered, "AP credential leaked")
        _require("00" + ("P" * 14) not in rendered, "STA credential leaked")
        after_reload = _require_heap_floor(_heap())
        result = {
            "phase": 7,
            "timers": 32,
            "networks": 8,
            "canonical_bytes": canonical_bytes,
            "before_import": before_import,
            "after_import": after_import,
            "after_build": after_build,
            "after_commit": after_commit,
            "before_reload": before_reload,
            "after_reload": after_reload,
        }
    finally:
        _remove_exact_files()
        _assert_absent()

    print(
        "PHASE 7 CONFIG CAPACITY PASS: timers=32 networks=8 bytes={} "
        "heap={}/{}/{}/{}/{}/{}".format(
            result["canonical_bytes"],
            result["before_import"],
            result["after_import"],
            result["after_build"],
            result["after_commit"],
            result["before_reload"],
            result["after_reload"],
        )
    )
    print(PHASE7_CONFIG_CAPACITY_PASS_TOKEN)
    return result
