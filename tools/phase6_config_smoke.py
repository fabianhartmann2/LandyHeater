"""USB-only Phase-6 persistence smoke test.

The runner exercises only the hardware-free configuration stack.  Importing
this module is inert.  A successful run uses two isolated flash namespaces,
removes only those exact smoke files afterward, and never imports board
configuration, ``machine``, hardware factories, protocol or HeaterController.
"""

import gc as _gc
import os as _os


SOFTWARE_ONLY_CONFIRMATION = "PHASE6_USB_CONFIG_SMOKE_V1"
PHASE6_PASS_TOKEN = "PHASE6_USB_CONFIG_SMOKE_PASS_V1"
DEFAULT_ITERATIONS = 4
MAX_ITERATIONS = 8
MINIMUM_FREE_HEAP_BYTES = 32 * 1024
MAXIMUM_HEAP_DRIFT_BYTES = 4096

FLASH_CONFIG_BASE_PATH = "/phase6_usb_config_smoke_v1_config"
FLASH_LEDGER_BASE_PATH = "/phase6_usb_config_smoke_v1_ledger"

_EXPECTED_MINUTE_ID = 13993350
_EXPECTED_KEY = "phase6-smoke|2026-08-09|14:30"


def _require(condition, message):
    if not condition:
        raise RuntimeError("phase6 config smoke failed: {}".format(message))


def _plain_ticks_diff(newer, older):
    return newer - older


def _plain_ticks_add(value, delta):
    return value + delta


def _memory_free():
    _gc.collect()
    reader = getattr(_gc, "mem_free", None)
    if not callable(reader):
        return None
    value = reader()
    if type(value) is not int or value < 0:
        raise RuntimeError("gc.mem_free() returned an invalid value")
    return value


def _check_platform_ticks():
    try:
        from time import ticks_add, ticks_diff, ticks_ms
    except ImportError:
        return False
    now_ms = ticks_ms()
    future_ms = ticks_add(now_ms, 23)
    _require(
        ticks_diff(future_ms, now_ms) == 23,
        "MicroPython tick primitives are inconsistent",
    )
    return True


def _check_memory(before, after_import, after_warmup, after):
    values = (before, after_import, after_warmup, after)
    available = tuple(value is not None for value in values)
    if not any(available):
        return False
    _require(all(available), "heap measurements are incomplete")
    for value in values[1:]:
        _require(
            value >= MINIMUM_FREE_HEAP_BYTES,
            "free heap after Phase-6 work is below 32 KiB",
        )
    allowed_drift = max(MAXIMUM_HEAP_DRIFT_BYTES, after_warmup // 50)
    _require(
        after >= after_warmup - allowed_drift,
        "free heap did not recover after bounded iterations",
    )
    return True


def _load_core():
    from adapters.config_file_store import AtomicJSONConfigStore
    from app.configuration_bootstrap import build_configured_runtime
    from app.scheduler_controller_gateway import SchedulerControllerGateway
    from services.config_manager import (
        ConfigManager,
        default_configuration,
        default_scheduler_ledger,
    )
    from services.time_service import CLOCK_SOURCE_RTC

    return (
        AtomicJSONConfigStore,
        ConfigManager,
        build_configured_runtime,
        SchedulerControllerGateway,
        default_configuration,
        default_scheduler_ledger,
        CLOCK_SOURCE_RTC,
    )


class _MemoryStream:
    __slots__ = ("_filesystem", "_path", "_mode", "_data", "_closed")

    def __init__(self, filesystem, path, mode):
        self._filesystem = filesystem
        self._path = path
        self._mode = mode
        self._closed = False
        if mode == "rb":
            if path not in filesystem.files:
                raise OSError(2, "missing synthetic file")
            self._data = bytearray(filesystem.files[path])
        elif mode == "wb":
            self._data = bytearray()
        else:
            raise OSError("unsupported synthetic file mode")

    def read(self, maximum):
        if self._closed or self._mode != "rb":
            raise OSError("synthetic file is not readable")
        return bytes(self._data[:maximum])

    def write(self, data):
        if self._closed or self._mode != "wb":
            raise OSError("synthetic file is not writable")
        payload = bytes(data)
        self._data.extend(payload)
        self._filesystem.write_bytes += len(payload)
        return len(payload)

    def flush(self):
        if self._closed:
            raise OSError("synthetic file is closed")
        return None

    def close(self):
        if self._closed:
            return None
        if self._mode == "wb":
            self._filesystem.files[self._path] = bytes(self._data)
        self._closed = True
        return None


class _MemoryFileSystem:
    __slots__ = ("files", "write_bytes", "renames", "syncs")

    def __init__(self):
        self.files = {}
        self.write_bytes = 0
        self.renames = 0
        self.syncs = 0

    def open(self, path, mode):
        return _MemoryStream(self, path, mode)

    def stat(self, path):
        if path not in self.files:
            raise OSError(2, "missing synthetic file")
        return (0, 0, 0, 0, 0, 0, len(self.files[path]), 0, 0, 0)

    def remove(self, path):
        if path not in self.files:
            raise OSError(2, "missing synthetic file")
        del self.files[path]
        return None

    def rename(self, source, target):
        if source not in self.files:
            raise OSError(2, "missing synthetic source")
        self.files[target] = self.files.pop(source)
        self.renames += 1
        return None

    def sync(self):
        self.syncs += 1
        return None


class _TickClock:
    __slots__ = ("value",)

    def __init__(self):
        self.value = 0

    def __call__(self):
        return self.value


class _FakeController:
    __slots__ = (
        "_on", "_mode", "_target", "_power", "_runtime", "_source",
        "_not_after_ms", "starts", "stops",
    )

    def __init__(self):
        self._on = False
        self._mode = "power"
        self._target = None
        self._power = 5
        self._runtime = 30
        self._source = "manual"
        self._not_after_ms = None
        self.starts = 0
        self.stops = 0

    @property
    def requested_on(self):
        return self._on

    @property
    def requested_source(self):
        return self._source

    def timer_start_available(self, now_ms, request=None):
        if type(now_ms) is not int or self._on:
            return False
        return request is None or now_ms <= request.not_after_ms

    def timer_session_complete(self, now_ms):
        return False

    def request_start(
        self,
        mode,
        target_temperature=None,
        power_level=None,
        runtime_minutes=60,
        source="manual",
        not_after_ms=None,
        now_ms=None,
    ):
        if (
            mode != "power"
            or target_temperature is not None
            or power_level != 5
            or runtime_minutes != 30
            or source != "timer"
            or type(not_after_ms) is not int
            or type(now_ms) is not int
            or now_ms > not_after_ms
            or self._on
        ):
            return False
        self._on = True
        self._mode = mode
        self._target = target_temperature
        self._power = power_level
        self._runtime = runtime_minutes
        self._source = source
        self._not_after_ms = not_after_ms
        self.starts += 1
        return True

    def request_stop(self):
        changed = self._on
        self._on = False
        self.stops += 1
        return changed

    def requested_matches(
        self,
        on,
        mode,
        target_temperature,
        power_level,
        runtime_minutes,
        source,
        not_after_ms=None,
    ):
        return (
            self._on is on
            and self._mode == mode
            and self._target == target_temperature
            and self._power == power_level
            and self._runtime == runtime_minutes
            and self._source == source
            and self._not_after_ms == not_after_ms
        )


def _timer_definition():
    return {
        "id": "phase6-smoke",
        "name": "Phase 6 Zürich\nquote\"slash\\\u0001",
        "enabled": True,
        "weekdays": [6],
        "start": "14:30",
        "mode": "power",
        "target_temperature": None,
        "power_level": 5,
        "runtime_minutes": 30,
    }


def _persistent_history(status="consumed"):
    return {
        "consumed_local_high_water": _EXPECTED_MINUTE_ID,
        "occurrences": [{
            "timer_id": "phase6-smoke",
            "occurrence_key": _EXPECTED_KEY,
            "local_minute_id": _EXPECTED_MINUTE_ID,
            "status": status,
            "overridden": status == "overridden",
        }],
    }


def _exercise(core, config_base, ledger_base, filesystem, corrupt_newest):
    (
        AtomicJSONConfigStore,
        ConfigManager,
        build_configured_runtime,
        SchedulerControllerGateway,
        default_configuration,
        default_scheduler_ledger,
        clock_source_rtc,
    ) = core
    config_store = AtomicJSONConfigStore(
        config_base, max_record_bytes=8192, filesystem=filesystem
    )
    ledger_store = AtomicJSONConfigStore(
        ledger_base, max_record_bytes=8192, filesystem=filesystem
    )
    manager = ConfigManager(config_store, ledger_store)
    _require(manager.load() is False, "first config load was not empty")
    _require(
        manager.load_scheduler_checkpoint() is False,
        "first scheduler-ledger load was not empty",
    )
    _require(manager.timer_start_allowed is False, "first boot opened timer gate")
    _require(
        config_store.status()["writes"] == 0
        and ledger_store.status()["writes"] == 0,
        "first boot wrote persistent state",
    )

    # The safety ledger is deliberately provisioned before setup_complete.
    _require(
        manager.checkpoint_scheduler(default_scheduler_ledger(), 0) is True,
        "initial ledger provisioning failed",
    )
    configuration = default_configuration()
    configuration["network"]["access_point"]["password"] = (
        "phase6-smoke-secret"
    )
    configuration["system"]["setup_complete"] = True
    configuration["timers"] = [_timer_definition()]
    _require(
        manager.commit(configuration, 0) is True,
        "initial configuration provisioning failed",
    )
    _require(
        manager.generation == 2 and manager.ledger_generation == 2,
        "initial dual-generation seal differs",
    )
    _require(manager.timer_start_allowed is True, "trusted timer gate stayed closed")
    _require(
        config_store.status()["writes"] == 2
        and ledger_store.status()["writes"] == 2,
        "initial A/B write counts differ",
    )

    writes_before_noop = (
        config_store.status()["writes"],
        ledger_store.status()["writes"],
    )
    _require(
        manager.commit(configuration, manager.generation) is False,
        "identical configuration was rewritten",
    )
    _require(
        manager.checkpoint_scheduler(
            default_scheduler_ledger(), manager.ledger_generation
        )
        is False,
        "identical ledger was rewritten",
    )
    _require(
        writes_before_noop
        == (
            config_store.status()["writes"],
            ledger_store.status()["writes"],
        ),
        "semantic no-op reached the filesystem",
    )

    runtime = build_configured_runtime(
        manager, ticks_diff=_plain_ticks_diff, ticks_add=_plain_ticks_add
    )
    _require(runtime.time_service.valid is False, "cold clock became valid")
    _require(runtime.scheduler.armed is False, "cold scheduler armed")
    _require(
        runtime.scheduler.active_occurrence_key is None,
        "cold scheduler restored an active occurrence",
    )

    # Exercise the real synchronous durable barrier: consumption is persisted
    # before Requested ON, and manual OFF is persisted as overridden.
    _require(
        runtime.time_service.set_utc_datetime(
            2026, 8, 9, 12, 29, 59, clock_source_rtc, 0
        )
        is True,
        "synthetic trusted RTC sample was rejected",
    )
    _require(runtime.scheduler.arm() is True, "scheduler did not arm")
    tick_clock = _TickClock()
    controller = _FakeController()
    gateway = SchedulerControllerGateway(
        runtime.scheduler,
        controller,
        ticks_ms=tick_clock,
        persistence=manager,
    )
    _require(gateway.step() is None, "scheduler baseline produced a start")
    tick_clock.value = 1000
    _require(gateway.step() is True, "durable timer start was not applied")
    _require(
        controller.requested_on is True and controller.starts == 1,
        "timer start did not commit Requested ON exactly once",
    )
    _require(manager.ledger_generation == 3, "consumption was not durable")
    _require(
        gateway.request_manual_stop() is True,
        "manual timer stop was not applied",
    )
    _require(
        controller.requested_on is False and controller.stops == 1,
        "manual stop did not leave Requested OFF",
    )
    _require(manager.ledger_generation == 4, "override was not durable")
    _require(gateway.step() is None, "overridden timer restarted")
    _require(controller.starts == 1, "overridden occurrence started twice")

    rebooted = ConfigManager(
        AtomicJSONConfigStore(
            config_base, max_record_bytes=8192, filesystem=filesystem
        ),
        AtomicJSONConfigStore(
            ledger_base, max_record_bytes=8192, filesystem=filesystem
        ),
    )
    _require(rebooted.load() is True, "reboot config did not load")
    _require(
        rebooted.load_scheduler_checkpoint() is True,
        "reboot ledger did not load",
    )
    _require(rebooted.timer_start_allowed is True, "reboot gate stayed closed")
    reboot_runtime = build_configured_runtime(
        rebooted, ticks_diff=_plain_ticks_diff, ticks_add=_plain_ticks_add
    )
    _require(
        reboot_runtime.scheduler.export_persistent_history()
        == _persistent_history("overridden"),
        "reboot history differs",
    )
    _require(
        reboot_runtime.scheduler.armed is False
        and reboot_runtime.scheduler.active_occurrence_key is None,
        "reboot restored executable scheduler state",
    )

    if corrupt_newest:
        ledger_slot = manager.status()["ledger_source_slot"]
        ledger_path = ledger_base + "." + ledger_slot
        newest_ledger = filesystem.files[ledger_path]
        filesystem.files[ledger_path] = b"corrupt newest ledger slot"
        damaged_ledger = ConfigManager(
            AtomicJSONConfigStore(
                config_base, max_record_bytes=8192, filesystem=filesystem
            ),
            AtomicJSONConfigStore(
                ledger_base, max_record_bytes=8192, filesystem=filesystem
            ),
        )
        _require(
            damaged_ledger.load() is True,
            "ledger corruption damaged the independent config domain",
        )
        _require(
            damaged_ledger.load_scheduler_checkpoint() is False,
            "corrupt newest ledger was trusted",
        )
        _require(
            damaged_ledger.timer_start_allowed is False,
            "corrupt newest ledger opened timer gate",
        )
        _require(
            damaged_ledger.scheduler_checkpoint()["ledger"]
            == default_scheduler_ledger(),
            "ledger recovery exposed rollback tombstones",
        )
        filesystem.files[ledger_path] = newest_ledger

        filesystem.files[config_base + ".b"] = b"corrupt newest slot"
        damaged = ConfigManager(
            AtomicJSONConfigStore(
                config_base, max_record_bytes=8192, filesystem=filesystem
            ),
            AtomicJSONConfigStore(
                ledger_base, max_record_bytes=8192, filesystem=filesystem
            ),
        )
        _require(damaged.load() is False, "corrupt newest config was trusted")
        damaged.load_scheduler_checkpoint()
        _require(
            damaged.timer_start_allowed is False,
            "corrupt newest config opened timer gate",
        )
        _require(
            damaged.snapshot()["configuration"]["timers"] == [],
            "recovery exposed rollback timers",
        )

    return {
        "configuration_generation": manager.generation,
        "ledger_generation": manager.ledger_generation,
        "config_writes": config_store.status()["writes"],
        "ledger_writes": ledger_store.status()["writes"],
    }


def _is_missing_error(error):
    code = getattr(error, "errno", None)
    if code is None and getattr(error, "args", None):
        code = error.args[0]
    return code == 2


def _smoke_paths():
    bases = (
        (FLASH_CONFIG_BASE_PATH, "config"),
        (FLASH_LEDGER_BASE_PATH, "ledger"),
    )
    for base, kind in bases:
        if (
            type(base) is not str
            or not base
            or len(base) > 180
            or "\x00" in base
            or not base.endswith(
                "phase6_usb_config_smoke_v1_" + kind
            )
        ):
            raise RuntimeError("Phase-6 smoke path is not isolated")
    return tuple(
        base + suffix
        for base, _ in bases
        for suffix in (".a", ".b", ".tmp")
    )


def _cleanup_flash_files():
    for path in _smoke_paths():
        try:
            result = _os.remove(path)
        except OSError as error:
            if _is_missing_error(error):
                continue
            raise
        if result is not None:
            raise RuntimeError("os.remove returned non-None")
    sync = getattr(_os, "sync", None)
    if not callable(sync):
        raise RuntimeError("os.sync is unavailable")
    result = sync()
    if result is not None:
        raise RuntimeError("os.sync returned non-None")


def _run_flash_roundtrip(core):
    _cleanup_flash_files()
    primary = None
    try:
        return _exercise(
            core,
            FLASH_CONFIG_BASE_PATH,
            FLASH_LEDGER_BASE_PATH,
            None,
            False,
        )
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            _cleanup_flash_files()
        except BaseException:
            if primary is None:
                raise


def run(confirmation, iterations=DEFAULT_ITERATIONS):
    """Run the bounded USB-only persistence smoke after exact confirmation."""

    if (
        type(confirmation) is not str
        or confirmation != SOFTWARE_ONLY_CONFIRMATION
    ):
        raise RuntimeError("exact Phase-6 USB-only confirmation is required")
    if type(iterations) is not int or iterations < 1 or iterations > MAX_ITERATIONS:
        raise ValueError("iterations must be an integer from 1 to 8")

    memory_before = _memory_free()
    core = _load_core()
    memory_after_import = _memory_free()
    platform_ticks_checked = _check_platform_ticks()

    warmup_fs = _MemoryFileSystem()
    _exercise(core, "/warm-config", "/warm-ledger", warmup_fs, True)
    memory_after_warmup = _memory_free()

    last = None
    for index in range(iterations):
        filesystem = _MemoryFileSystem()
        last = _exercise(
            core,
            "/memory-config-{}".format(index),
            "/memory-ledger-{}".format(index),
            filesystem,
            True,
        )
    flash = _run_flash_roundtrip(core)
    memory_after = _memory_free()
    memory_checked = _check_memory(
        memory_before,
        memory_after_import,
        memory_after_warmup,
        memory_after,
    )
    if memory_checked:
        _require(
            platform_ticks_checked is True,
            "MicroPython heap exists but platform ticks were not checked",
        )

    result = {
        "phase": 6,
        "scope": "usb_only_configuration_storage",
        "iterations": iterations,
        "passed": iterations,
        "configuration_generation": last["configuration_generation"],
        "ledger_generation": last["ledger_generation"],
        "flash_config_writes": flash["config_writes"],
        "flash_ledger_writes": flash["ledger_writes"],
        "platform_ticks_checked": platform_ticks_checked,
        "memory_checked": memory_checked,
        "memory_before": memory_before,
        "memory_after_import": memory_after_import,
        "memory_after_warmup": memory_after_warmup,
        "memory_after": memory_after,
    }
    print("PHASE 6 USB-ONLY CONFIG SMOKE PASS: {}/{}".format(
        iterations, iterations
    ))
    print("configuration_generation={}".format(
        result["configuration_generation"]
    ))
    print("ledger_generation={}".format(result["ledger_generation"]))
    print("flash_config_writes={}".format(result["flash_config_writes"]))
    print("flash_ledger_writes={}".format(result["flash_ledger_writes"]))
    print("memory_before={}".format(memory_before))
    print("memory_after_import={}".format(memory_after_import))
    print("memory_after_warmup={}".format(memory_after_warmup))
    print("memory_after={}".format(memory_after))
    print(PHASE6_PASS_TOKEN)
    return result
