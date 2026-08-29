"""USB-only integration smoke test for the Phase-5 software path.

The test uses an in-memory DS3231 register bus and an in-memory heater
controller.  It imports no board configuration, hardware factory, protocol
module or ``machine`` module and never opens GPIO, I2C, UART or 1-Wire.
Importing this module is inert; :func:`run` must be armed explicitly.
"""

import gc as _gc


SOFTWARE_ONLY_CONFIRMATION = "USB_ONLY_NO_GPIO_CONNECTED"
PHASE5_PASS_TOKEN = "PHASE5_USB_SMOKE_PASS_V2"
DEFAULT_ITERATIONS = 4
MAX_ITERATIONS = 16
MINIMUM_FREE_HEAP_BYTES = 32 * 1024
MAXIMUM_HEAP_DRIFT_BYTES = 4096

_DS3231_ADDRESS = 0x68
_REGISTER_TIME = 0x00
_REGISTER_CONTROL = 0x0E
_REGISTER_STATUS = 0x0F
_EXPECTED_OCCURRENCE_KEY = "phase5-integration|2026-08-09|14:30"


def _require(condition, message):
    if not condition:
        raise RuntimeError(
            "phase5 integration smoke failed: {}".format(message)
        )


def _plain_ticks_diff(newer, older):
    return newer - older


def _plain_ticks_add(ticks, delta):
    return ticks + delta


def _memory_free():
    """Return free MicroPython heap bytes, or ``None`` on CPython."""

    _gc.collect()
    reader = getattr(_gc, "mem_free", None)
    if not callable(reader):
        return None
    value = reader()
    if type(value) is not int or value < 0:
        raise RuntimeError("gc.mem_free() returned an invalid value")
    return value


def _check_platform_ticks():
    """Exercise MicroPython's actual wrap-safe tick helpers when present."""

    try:
        from time import ticks_add, ticks_diff, ticks_ms
    except ImportError:
        return False
    now_ms = ticks_ms()
    future_ms = ticks_add(now_ms, 17)
    _require(
        ticks_diff(future_ms, now_ms) == 17,
        "MicroPython tick primitives are inconsistent",
    )
    return True


def _check_memory(
    memory_before,
    memory_after_import,
    memory_after_warmup,
    memory_after,
):
    values = (
        memory_before,
        memory_after_import,
        memory_after_warmup,
        memory_after,
    )
    available = tuple(value is not None for value in values)
    if not any(available):
        return False
    _require(all(available), "heap measurements are incomplete")
    _require(
        memory_after_import >= MINIMUM_FREE_HEAP_BYTES,
        "free heap after core import is below 32 KiB",
    )
    _require(
        memory_after_warmup >= MINIMUM_FREE_HEAP_BYTES,
        "free heap after warm-up is below 32 KiB",
    )
    _require(
        memory_after >= MINIMUM_FREE_HEAP_BYTES,
        "free heap after smoke test is below 32 KiB",
    )
    allowed_drift = max(
        MAXIMUM_HEAP_DRIFT_BYTES, memory_after_warmup // 50
    )
    _require(
        memory_after >= memory_after_warmup - allowed_drift,
        "free heap did not recover after bounded iterations",
    )
    return True


def _load_software_core():
    """Load only hardware-independent Phase-5 components."""

    from adapters.ds3231_adapter import DS3231Adapter
    from app.scheduler import Scheduler
    from app.scheduler_controller_gateway import SchedulerControllerGateway
    from services.rtc_time_bridge import RTCTimeBridge
    from services.time_service import (
        CLOCK_SOURCE_NTP,
        CLOCK_SOURCE_RTC,
        EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
        EUROPE_ZURICH_TIMEZONE_NAME,
        TIMEZONE_RULE_EUROPE_ZURICH,
        TimeService,
        local_civil_to_utc_occurrences,
    )

    return (
        DS3231Adapter,
        Scheduler,
        SchedulerControllerGateway,
        RTCTimeBridge,
        CLOCK_SOURCE_NTP,
        CLOCK_SOURCE_RTC,
        EUROPE_ZURICH_STANDARD_OFFSET_MINUTES,
        EUROPE_ZURICH_TIMEZONE_NAME,
        TIMEZONE_RULE_EUROPE_ZURICH,
        TimeService,
        local_civil_to_utc_occurrences,
    )


class _FakeRegisterI2C:
    """Minimal DS3231 register file; it owns no external resource."""

    __slots__ = ("registers", "writes")

    def __init__(self):
        self.registers = bytearray(0x13)
        # 2026-08-09 14:29:59 UTC; DS3231 weekday Sunday is register value 7.
        self.registers[0:7] = bytes((
            0x59,
            0x29,
            0x14,
            0x07,
            0x09,
            0x08,
            0x26,
        ))
        self.registers[_REGISTER_CONTROL] = 0x04
        self.registers[_REGISTER_STATUS] = 0x00
        self.writes = []

    @staticmethod
    def _validate(address, register, length, addrsize):
        if address != _DS3231_ADDRESS or addrsize != 8:
            raise OSError("unexpected synthetic I2C addressing")
        if (
            type(register) is not int
            or type(length) is not int
            or length < 0
            or register < 0
            or register + length > 0x13
        ):
            raise OSError("synthetic I2C range error")

    def readfrom_mem(self, address, register, length, addrsize=8):
        self._validate(address, register, length, addrsize)
        return bytes(self.registers[register:register + length])

    def writeto_mem(self, address, register, data, addrsize=8):
        try:
            payload = bytes(data)
        except Exception:
            raise OSError("synthetic I2C write contract error")
        self._validate(address, register, len(payload), addrsize)
        self.writes.append((register, payload))
        if register == _REGISTER_STATUS and len(payload) == 1:
            # DS3231 OSF/A2F/A1F are cleared by writing zero and retained by
            # writing one.  BSY is read-only; EN32kHz follows the write.
            previous = self.registers[_REGISTER_STATUS]
            retained_flags = previous & payload[0] & 0x83
            self.registers[_REGISTER_STATUS] = (
                retained_flags | (previous & 0x04) | (payload[0] & 0x08)
            )
        else:
            self.registers[register:register + len(payload)] = payload
        return None


class _TickPlan:
    __slots__ = ("_values", "_index")

    def __init__(self, *values):
        self._values = values
        self._index = 0

    def __call__(self):
        if self._index >= len(self._values):
            raise RuntimeError("synthetic tick plan exhausted")
        value = self._values[self._index]
        self._index += 1
        return value


class _FakeController:
    """Requested-state-only controller used by the synchronous gateway."""

    __slots__ = (
        "_on",
        "_mode",
        "_target",
        "_power",
        "_runtime",
        "_source",
        "_not_after_ms",
        "starts",
        "stops",
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
        if type(now_ms) is not int:
            return False
        if self._on:
            return False
        if request is None:
            return True
        return now_ms <= request.not_after_ms

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
        "id": "phase5-integration",
        "name": "Phase 5 integration smoke",
        "enabled": True,
        "weekdays": [6],
        "start": "14:30",
        "mode": "power",
        "target_temperature": None,
        "power_level": 5,
        "runtime_minutes": 30,
    }


def _check_europe_zurich_dst(core):
    """Exercise the embedded CET/CEST rule without any hardware access."""

    (
        _,
        Scheduler,
        _,
        _,
        _,
        clock_source_rtc,
        standard_offset,
        timezone_name,
        timezone_rule,
        TimeService,
        local_to_utc,
    ) = core

    def clock_at(year, month, day, hour, minute, second=0):
        clock = TimeService(
            ticks_diff=_plain_ticks_diff,
            timezone_name=timezone_name,
            utc_offset_minutes=standard_offset,
            timezone_rule=timezone_rule,
        )
        _require(
            clock.set_utc_datetime(
                year,
                month,
                day,
                hour,
                minute,
                second,
                clock_source_rtc,
                0,
            )
            is True,
            "Europe/Zurich clock sample was not accepted",
        )
        return clock

    spring_before = clock_at(2026, 3, 29, 0, 59, 59).snapshot(0)
    spring_after = clock_at(2026, 3, 29, 1, 0, 0).snapshot(0)
    _require(
        spring_before["local"]
        == {
            "year": 2026,
            "month": 3,
            "day": 29,
            "weekday": 6,
            "hour": 1,
            "minute": 59,
            "second": 59,
            "local_minute_id": 13801079,
            "fold": 0,
        },
        "Europe/Zurich spring boundary before transition differs",
    )
    _require(
        spring_before["utc_offset_minutes"] == 60
        and spring_before["is_dst"] is False,
        "Europe/Zurich winter projection differs",
    )
    _require(
        spring_after["local"]
        == {
            "year": 2026,
            "month": 3,
            "day": 29,
            "weekday": 6,
            "hour": 3,
            "minute": 0,
            "second": 0,
            "local_minute_id": 13801140,
            "fold": 0,
        },
        "Europe/Zurich spring boundary after transition differs",
    )
    _require(
        spring_after["utc_offset_minutes"] == 120
        and spring_after["is_dst"] is True,
        "Europe/Zurich summer projection differs",
    )
    _require(
        local_to_utc(
            2026,
            3,
            29,
            2,
            30,
            0,
            timezone_rule,
            standard_offset,
        )
        == (),
        "non-existent Europe/Zurich spring time was accepted",
    )

    fall_first = clock_at(2026, 10, 25, 0, 30, 0).snapshot(0)
    fall_second = clock_at(2026, 10, 25, 1, 30, 0).snapshot(0)
    _require(
        fall_first["local"]["hour"] == 2
        and fall_first["local"]["minute"] == 30
        and fall_first["local"]["fold"] == 0
        and fall_first["utc_offset_minutes"] == 120,
        "first Europe/Zurich repeated hour differs",
    )
    _require(
        fall_second["local"]["hour"] == 2
        and fall_second["local"]["minute"] == 30
        and fall_second["local"]["fold"] == 1
        and fall_second["utc_offset_minutes"] == 60,
        "second Europe/Zurich repeated hour differs",
    )
    occurrences = local_to_utc(
        2026,
        10,
        25,
        2,
        30,
        0,
        timezone_rule,
        standard_offset,
    )
    _require(
        len(occurrences) == 2
        and occurrences[0]["fold"] == 0
        and occurrences[1]["fold"] == 1
        and occurrences[1]["utc_seconds"]
        - occurrences[0]["utc_seconds"]
        == 3600,
        "Europe/Zurich repeated-hour mapping differs",
    )

    # A fresh scheduler booting in the second 02:xx hour must never create a
    # timer request, even though no earlier occurrence ledger exists in RAM.
    fold_clock = clock_at(2026, 10, 25, 1, 29, 59)
    fold_scheduler = Scheduler(
        fold_clock,
        maximum_runtime_minutes=120,
        ticks_diff=_plain_ticks_diff,
        ticks_add=_plain_ticks_add,
    )
    fold_timer = {
        "id": "phase5-fold",
        "name": "Repeated-hour safety smoke",
        "enabled": True,
        "weekdays": [6],
        "start": "02:30",
        "mode": "power",
        "target_temperature": None,
        "power_level": 5,
        "runtime_minutes": 30,
    }
    _require(
        fold_scheduler.replace_timers((fold_timer,)) is True,
        "Europe/Zurich fold timer was not accepted",
    )
    _require(fold_scheduler.arm() is True, "fold scheduler did not arm")
    _require(
        fold_scheduler.step(0, True) is None,
        "fold scheduler baseline produced an intent",
    )
    _require(
        fold_scheduler.step(1000, True) is None,
        "second repeated hour produced a timer intent",
    )
    fold_state = fold_scheduler.snapshot()
    _require(
        fold_state["occurrences"] == {},
        "second repeated hour consumed a timer occurrence",
    )
    upcoming = fold_scheduler.next_occurrence(1000)
    _require(
        upcoming is not None
        and upcoming["occurrence_key"]
        == "phase5-fold|2026-11-01|02:30"
        and upcoming["minutes_from_now"] == 10080,
        "next start after the repeated hour differs",
    )


def _run_iteration(core):
    (
        DS3231Adapter,
        Scheduler,
        SchedulerControllerGateway,
        RTCTimeBridge,
        clock_source_ntp,
        _,
        _,
        _,
        _,
        TimeService,
        _,
    ) = core

    _check_europe_zurich_dst(core)

    bus = _FakeRegisterI2C()
    rtc = DS3231Adapter(bus)
    initial_rtc = rtc.read_utc_datetime()
    _require(
        initial_rtc
        == {
            "year": 2026,
            "month": 8,
            "day": 9,
            "weekday": 6,
            "hour": 14,
            "minute": 29,
            "second": 59,
        },
        "synthetic DS3231 read differs",
    )

    clock = TimeService(ticks_diff=_plain_ticks_diff)
    bridge = RTCTimeBridge(
        rtc,
        clock,
        ticks_diff=_plain_ticks_diff,
        ticks_add=_plain_ticks_add,
        refresh_interval_ms=60000,
        retry_interval_ms=5000,
    )
    _require(bridge.step(0) == "rtc_read", "RTC cold read was not applied")
    cold_clock = clock.snapshot(0)
    _require(cold_clock["valid"] is True, "RTC did not establish time")
    _require(cold_clock["source"] == "rtc", "cold clock source differs")
    _require(
        cold_clock["rtc_write_pending"] is False,
        "cold RTC read unexpectedly needs persistence",
    )

    scheduler = Scheduler(
        clock,
        maximum_runtime_minutes=120,
        ticks_diff=_plain_ticks_diff,
        ticks_add=_plain_ticks_add,
        intent_valid_ms=5000,
    )
    _require(
        scheduler.replace_timers((_timer_definition(),)) is True,
        "timer configuration was not accepted",
    )
    _require(scheduler.arm() is True, "scheduler did not arm")
    controller = _FakeController()
    gateway = SchedulerControllerGateway(
        scheduler,
        controller,
        ticks_ms=_TickPlan(0, 1000, 1001, 1001, 1001, 2000, 2000),
    )
    _require(gateway.step() is None, "scheduler baseline was not fenced")
    _require(gateway.step() is True, "timer intent was not applied")
    _require(controller.starts == 1, "timer start count differs")
    _require(controller.requested_on is True, "Requested State is not ON")
    _require(
        scheduler.active_occurrence_key == _EXPECTED_OCCURRENCE_KEY,
        "active timer association differs",
    )
    _require(
        gateway.request_manual_stop() is True,
        "manual timer stop was not accepted",
    )
    _require(controller.requested_on is False, "manual stop stayed ON")
    _require(controller.stops == 1, "manual stop count differs")
    _require(
        scheduler.active_occurrence_key is None,
        "manual override left an active association",
    )
    occurrence = scheduler.snapshot()["occurrences"].get(
        "phase5-integration"
    )
    _require(occurrence is not None, "timer occurrence is missing")
    _require(occurrence["status"] == "overridden", "override status differs")
    _require(occurrence["overridden"] is True, "override flag differs")
    _require(
        gateway.step() is None,
        "overridden occurrence produced another timer request",
    )
    _require(controller.starts == 1, "overridden occurrence restarted")
    _require(
        controller.requested_on is False,
        "overridden occurrence restored Requested ON",
    )
    gateway_state = gateway.snapshot()
    _require(gateway_state["faulted"] is False, "gateway faulted")
    _require(gateway_state["applied"] == 1, "gateway applied count differs")
    _require(
        gateway_state["manual_stops"] == 1,
        "gateway manual stop count differs",
    )

    _require(
        clock.set_utc_datetime(
            2026,
            8,
            9,
            15,
            0,
            0,
            clock_source_ntp,
            2000,
        )
        is True,
        "synthetic NTP correction was not accepted",
    )
    pending = clock.snapshot(2000)
    _require(pending["rtc_write_pending"] is True, "RTC write is not pending")
    _require(bridge.step(59999) is None, "RTC bridge ran before its deadline")
    _require(
        bridge.step(60000) == "rtc_write",
        "staged RTC write was not committed",
    )
    persisted = rtc.read_utc_datetime()
    _require(
        persisted
        == {
            "year": 2026,
            "month": 8,
            "day": 9,
            "weekday": 6,
            "hour": 15,
            "minute": 0,
            "second": 58,
        },
        "persisted UTC correction differs",
    )
    _require(
        bus.writes
        == [
            (_REGISTER_CONTROL, bytes((0x84,))),
            (
                _REGISTER_TIME,
                bytes((0x58, 0x00, 0x15, 0x07, 0x09, 0x08, 0x26)),
            ),
            (_REGISTER_CONTROL, bytes((0x04,))),
        ],
        "DS3231 staged write order differs",
    )
    final_clock = clock.snapshot(60000)
    _require(
        final_clock["rtc_write_pending"] is False,
        "committed RTC revision stayed pending",
    )
    _require(final_clock["rtc_health"] == "ok", "RTC health did not recover")
    _require(
        final_clock["rtc_commit_revision"] is None,
        "RTC commit lock was not released",
    )
    bridge_state = bridge.snapshot()
    _require(bridge_state["reads"] == 1, "bridge read count differs")
    _require(bridge_state["writes"] == 1, "bridge write count differs")
    _require(bridge_state["commit_pending"] is False, "bridge commit stayed pending")
    _require(bridge_state["faulted"] is False, "RTC bridge faulted")


def run(confirmation, iterations=DEFAULT_ITERATIONS):
    """Run a bounded deterministic integration test and return its result."""

    if (
        type(confirmation) is not str
        or confirmation != SOFTWARE_ONLY_CONFIRMATION
    ):
        raise RuntimeError(
            "integration smoke not armed; disconnect every GPIO and use "
            "the exact USB-only confirmation"
        )
    if type(iterations) is not int or not 1 <= iterations <= MAX_ITERATIONS:
        raise ValueError("iterations must be between 1 and 16")

    memory_before = _memory_free()
    core = _load_software_core()
    memory_after_import = _memory_free()
    platform_ticks_checked = _check_platform_ticks()
    # Stabilize import caches and one-time allocations before drift checking.
    _run_iteration(core)
    memory_after_warmup = _memory_free()
    passed = 0
    for _ in range(iterations):
        _run_iteration(core)
        passed += 1
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
            "MicroPython wrap-safe tick primitives are unavailable",
        )

    result = {
        "phase": 5,
        "scope": "usb_only_integration",
        "iterations": iterations,
        "passed": passed,
        "rtc_reads": passed,
        "rtc_writes": passed,
        "timer_starts": passed,
        "manual_stops": passed,
        "dst_checks": passed,
        "platform_ticks_checked": platform_ticks_checked,
        "memory_checked": memory_checked,
        "memory_free_before": memory_before,
        "memory_free_after_import": memory_after_import,
        "memory_free_after_warmup": memory_after_warmup,
        "memory_free_after": memory_after,
    }
    print(
        "PHASE 5 USB-ONLY INTEGRATION SMOKE PASS: {}/{} iterations; "
        "no hardware opened".format(passed, iterations)
    )
    if memory_checked:
        print(
            "MicroPython heap free: before={} after_import={} "
            "after_warmup={} after={} bytes".format(
                memory_before,
                memory_after_import,
                memory_after_warmup,
                memory_after,
            )
        )
    print(PHASE5_PASS_TOKEN)
    return result
