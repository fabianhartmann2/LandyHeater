"""Cooperative, hardware-injected DS18B20 sampling state machine.

The adapter intentionally imports no board or hardware module.  Its injected
bus port provides ``scan()``, ``start_conversion()``, ``read_celsius(rom)``
and ``deinit()``.  ``hardware.micropython_ds18b20`` maps those calls to
``onewire.OneWire`` and ``ds18x20.DS18X20`` only after a board pin is
explicitly approved.

One call to :meth:`step` performs at most one synchronous bus operation.  The
mandatory DS18B20 conversion delay is represented by a wrap-safe deadline;
there is no sleep, timer callback, interrupt handler or catch-up loop here.
"""

import math
import time as _time


STATE_IDLE = "idle"
STATE_CONVERSION_READY = "conversion_ready"
STATE_WAIT_CONVERSION = "wait_conversion"
STATE_READING = "reading"
STATE_FAULTED = "faulted"
STATE_CLOSED = "closed"

DEFAULT_CONVERSION_WAIT_MS = 750
DEFAULT_POLL_INTERVAL_MS = 1000
DEFAULT_DISCOVERY_INTERVAL_MS = 30000
DEFAULT_MAX_SENSORS = 16

DS18B20_FAMILY_CODE = 0x28
DS18B20_POWER_ON_READING_C = 85.0
DS18B20_MINIMUM_C = -55.0
DS18B20_MAXIMUM_C = 125.0

ERROR_SCAN = "onewire_scan_failed"
ERROR_CONVERSION = "onewire_conversion_failed"
ERROR_READ = "onewire_read_failed"
ERROR_VALUE = "ds18b20_value_invalid"
ERROR_POWER_ON_85 = "ds18b20_power_on_85_untrusted"
ERROR_MANAGER = "temperature_manager_failed"
ERROR_BUS = "onewire_bus_contract_failed"

_HEX_DIGITS = "0123456789abcdef"
_BUS_METHODS = ("scan", "start_conversion", "read_celsius", "deinit")
_MANAGER_METHODS = (
    "record_discovery",
    "record_valid",
    "record_failure",
)


class DS18B20BusError(Exception):
    """Expected, recoverable 1-Wire/DS18B20 port error."""


def _plain_ticks_diff(newer, older):
    return newer - older


def _plain_ticks_add(ticks, delta):
    return ticks + delta


_platform_ticks_diff = getattr(_time, "ticks_diff", _plain_ticks_diff)
_platform_ticks_add = getattr(_time, "ticks_add", _plain_ticks_add)


def _require_positive_integer(name, value):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("{} must be a positive integer".format(name))


def _require_ticks(now_ms):
    if not isinstance(now_ms, int) or isinstance(now_ms, bool):
        raise ValueError("now_ms must be an integer")


def dallas_crc8(data):
    """Return the Dallas/Maxim CRC-8 used by 1-Wire ROM codes."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("CRC input must be bytes-like")
    crc = 0
    for byte in data:
        value = byte
        for _ in range(8):
            mix = (crc ^ value) & 0x01
            crc >>= 1
            if mix:
                crc ^= 0x8C
            value >>= 1
    return crc


def canonical_rom_id(raw_rom):
    """Validate one DS18B20 ROM and return immutable raw/id forms."""

    if not isinstance(raw_rom, (bytes, bytearray, memoryview)):
        raise ValueError("DS18B20 ROM must be bytes-like")
    raw_rom = bytes(raw_rom)
    if len(raw_rom) != 8:
        raise ValueError("DS18B20 ROM must contain exactly 8 bytes")
    if raw_rom[0] != DS18B20_FAMILY_CODE:
        raise ValueError("only DS18B20 family 0x28 is supported")
    if dallas_crc8(raw_rom) != 0:
        raise ValueError("DS18B20 ROM CRC is invalid")
    rom_id = "".join(
        _HEX_DIGITS[byte >> 4] + _HEX_DIGITS[byte & 0x0F]
        for byte in raw_rom
    )
    return raw_rom, rom_id


def _normalize_temperature(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if (
        not math.isfinite(value)
        or value < DS18B20_MINIMUM_C
        or value > DS18B20_MAXIMUM_C
    ):
        return None
    return value


class DS18B20Adapter:
    """Drive a bounded DS18B20 bus without blocking for conversion time."""

    def __init__(
        self,
        bus,
        temperature_manager,
        ticks_diff=None,
        ticks_add=None,
        conversion_wait_ms=DEFAULT_CONVERSION_WAIT_MS,
        poll_interval_ms=DEFAULT_POLL_INTERVAL_MS,
        discovery_interval_ms=DEFAULT_DISCOVERY_INTERVAL_MS,
        max_sensors=DEFAULT_MAX_SENSORS,
    ):
        for method_name in _BUS_METHODS:
            if not callable(getattr(bus, method_name, None)):
                raise ValueError(
                    "bus must provide {}()".format(method_name)
                )
        for method_name in _MANAGER_METHODS:
            if not callable(
                getattr(temperature_manager, method_name, None)
            ):
                raise ValueError(
                    "temperature_manager must provide {}()".format(
                        method_name
                    )
                )
        if not isinstance(
            getattr(temperature_manager, "assignments", None), dict
        ):
            raise ValueError(
                "temperature_manager must expose detached assignments"
            )

        _require_positive_integer("conversion_wait_ms", conversion_wait_ms)
        _require_positive_integer("poll_interval_ms", poll_interval_ms)
        _require_positive_integer(
            "discovery_interval_ms", discovery_interval_ms
        )
        _require_positive_integer("max_sensors", max_sensors)
        if conversion_wait_ms < DEFAULT_CONVERSION_WAIT_MS:
            raise ValueError(
                "conversion_wait_ms must be at least 750 milliseconds"
            )
        if poll_interval_ms < conversion_wait_ms:
            raise ValueError(
                "poll_interval_ms must cover the conversion wait"
            )
        if discovery_interval_ms < poll_interval_ms:
            raise ValueError(
                "discovery_interval_ms must not be shorter than polling"
            )
        manager_capacity = getattr(
            temperature_manager, "max_discovered_sensors", max_sensors
        )
        _require_positive_integer(
            "temperature_manager.max_discovered_sensors", manager_capacity
        )
        if max_sensors > manager_capacity:
            raise ValueError(
                "max_sensors exceeds TemperatureManager capacity"
            )

        if (ticks_diff is None) != (ticks_add is None):
            raise ValueError(
                "ticks_diff and ticks_add must be provided together"
            )
        if ticks_diff is None:
            ticks_diff = _platform_ticks_diff
            ticks_add = _platform_ticks_add
        if not callable(ticks_diff) or not callable(ticks_add):
            raise ValueError("tick helpers must be callable")

        self._bus = bus
        self._manager = temperature_manager
        self._ticks_diff = ticks_diff
        self._ticks_add = ticks_add
        self._conversion_wait_ms = conversion_wait_ms
        self._poll_interval_ms = poll_interval_ms
        self._discovery_interval_ms = discovery_interval_ms
        self._max_sensors = max_sensors

        self._state = STATE_IDLE
        self._roms = ()
        self._cycle_roms = ()
        self._read_index = 0
        self._devices = {}
        self._trusted_85_rom_ids = set()
        self._discovery_requested = True
        self._next_discovery_due_ms = None
        self._next_cycle_due_ms = None
        self._conversion_due_ms = None
        self._cycle_had_error = False
        self._last_poll_ms = None
        self._last_action_ms = None
        self._closed = False
        self._cleanup_complete = False

        self.scans = 0
        self.scan_errors = 0
        self.conversions = 0
        self.conversion_errors = 0
        self.read_attempts = 0
        self.valid_readings = 0
        self.invalid_readings = 0
        self.read_errors = 0
        self.manager_rejections = 0
        self.manager_errors = 0
        self.bus_contract_errors = 0
        self.trust_cleanup_errors = 0
        self.completed_cycles = 0
        self.last_completed_cycle_ms = None
        self.last_error = None

    @property
    def state(self):
        return self._state

    @property
    def closed(self):
        return self._closed

    @property
    def conversion_wait_ms(self):
        return self._conversion_wait_ms

    @property
    def poll_interval_ms(self):
        return self._poll_interval_ms

    @property
    def discovery_interval_ms(self):
        return self._discovery_interval_ms

    @property
    def max_sensors(self):
        return self._max_sensors

    def _due(self, now_ms, deadline_ms):
        return deadline_ms is not None and (
            self._ticks_diff(now_ms, deadline_ms) >= 0
        )

    def _clear_all_trust(self):
        self._trusted_85_rom_ids.clear()
        for device in self._devices.values():
            device["trusted"] = False

    def _clear_rom_trust(self, rom_id):
        self._trusted_85_rom_ids.discard(rom_id)
        device = self._devices.get(rom_id)
        if device is not None:
            device["trusted"] = False

    def _update_all_device_failures(self, error_code, count_invalid):
        for device in self._devices.values():
            if count_invalid:
                device["invalid_readings"] += 1
            device["last_error"] = error_code
            device["trusted"] = False

    def _set_fault(self, code, manager_error=False):
        # Commit the fail-closed latch before any best-effort diagnostic
        # cleanup.  Even an allocation failure while walking the device map
        # must never leave the adapter active after an unexpected exception.
        self._state = STATE_FAULTED
        self.last_error = code
        if manager_error:
            self.manager_errors += 1
        else:
            self.bus_contract_errors += 1
        try:
            self._clear_all_trust()
        except BaseException:
            try:
                self.trust_cleanup_errors += 1
            except BaseException:
                pass

    def _manager_call(self, method_name, *args):
        try:
            return getattr(self._manager, method_name)(*args)
        except BaseException:
            self._set_fault(ERROR_MANAGER, manager_error=True)
            raise

    def _assigned_rom_ids(self):
        try:
            assignments = self._manager.assignments
            if not isinstance(assignments, dict):
                raise ValueError("manager assignments are not a dictionary")
            rom_ids = []
            for rom_id in assignments.values():
                if rom_id is None:
                    continue
                if not isinstance(rom_id, str) or not rom_id:
                    raise ValueError("manager assignment is invalid")
                if rom_id not in rom_ids:
                    rom_ids.append(rom_id)
            return tuple(rom_ids)
        except BaseException:
            self._set_fault(ERROR_MANAGER, manager_error=True)
            raise

    def _record_global_failure(self, now_ms, reason):
        for rom_id in self._assigned_rom_ids():
            self._manager_call("record_failure", rom_id, now_ms, reason)

    def _validate_scan(self, raw_roms):
        if type(raw_roms) not in (list, tuple):
            raise ValueError("scan result must be a bounded sequence")
        if len(raw_roms) > self._max_sensors:
            raise ValueError("scan result exceeds configured sensor limit")
        normalized = []
        seen = set()
        for raw_rom in raw_roms:
            raw_rom, rom_id = canonical_rom_id(raw_rom)
            if rom_id in seen:
                raise ValueError("scan contains a duplicate ROM")
            seen.add(rom_id)
            normalized.append((raw_rom, rom_id))
        normalized.sort(key=lambda item: item[1])
        return tuple(normalized)

    @staticmethod
    def _new_device(rom_id):
        return {
            "rom_id": rom_id,
            "value_c": None,
            "last_sample_ms": None,
            "last_error": None,
            "trusted": False,
            "invalid_readings": 0,
        }

    def _perform_discovery(self, now_ms):
        try:
            raw_roms = self._bus.scan()
            normalized = self._validate_scan(raw_roms)
        except DS18B20BusError:
            self.scan_errors += 1
            self.last_error = ERROR_SCAN
            self._clear_all_trust()
            self._update_all_device_failures(ERROR_SCAN, False)
            self._record_global_failure(now_ms, ERROR_SCAN)
            self._discovery_requested = False
            self._next_discovery_due_ms = self._ticks_add(
                now_ms, self._discovery_interval_ms
            )
            self._next_cycle_due_ms = self._ticks_add(
                now_ms, self._poll_interval_ms
            )
            return
        except (ValueError, TypeError):
            self.scan_errors += 1
            self.last_error = ERROR_SCAN
            self._clear_all_trust()
            self._update_all_device_failures(ERROR_SCAN, False)
            self._record_global_failure(now_ms, ERROR_SCAN)
            self._discovery_requested = False
            self._next_discovery_due_ms = self._ticks_add(
                now_ms, self._discovery_interval_ms
            )
            self._next_cycle_due_ms = self._ticks_add(
                now_ms, self._poll_interval_ms
            )
            return
        except BaseException:
            self._set_fault(ERROR_BUS)
            raise

        rom_ids = tuple(item[1] for item in normalized)
        previous_devices = self._devices
        devices = {}
        for rom_id in rom_ids:
            device = previous_devices.get(rom_id)
            if device is None:
                device = self._new_device(rom_id)
            devices[rom_id] = device
        trusted_rom_ids = set(self._trusted_85_rom_ids)
        trusted_rom_ids.intersection_update(rom_ids)
        next_discovery_due_ms = self._ticks_add(
            now_ms, self._discovery_interval_ms
        )
        next_cycle_due_ms = self._next_cycle_due_ms
        if not normalized:
            next_cycle_due_ms = self._ticks_add(
                now_ms, self._poll_interval_ms
            )

        # Allocate and calculate the whole adapter-side commit before the
        # manager is changed.  A MemoryError can therefore never leave a
        # successful Manager discovery paired with a half-built adapter view.
        self._manager_call("record_discovery", rom_ids, now_ms)
        self._devices = devices
        self._trusted_85_rom_ids = trusted_rom_ids
        self._roms = normalized
        self._discovery_requested = False
        self._next_discovery_due_ms = next_discovery_due_ms
        self.scans += 1
        if normalized:
            self._state = STATE_CONVERSION_READY
        else:
            self._state = STATE_IDLE
            self._next_cycle_due_ms = next_cycle_due_ms

    def _start_conversion(self, now_ms):
        self._cycle_roms = self._roms
        if not self._cycle_roms:
            self._state = STATE_IDLE
            self._next_cycle_due_ms = self._ticks_add(
                now_ms, self._poll_interval_ms
            )
            return False
        try:
            result = self._bus.start_conversion()
            if result is not None:
                raise DS18B20BusError(
                    "start_conversion() must return None"
                )
        except DS18B20BusError:
            self.conversion_errors += 1
            self.last_error = ERROR_CONVERSION
            self._clear_all_trust()
            self._update_all_device_failures(ERROR_CONVERSION, True)
            self._record_global_failure(now_ms, ERROR_CONVERSION)
            self._cycle_roms = ()
            self._state = STATE_IDLE
            self._next_cycle_due_ms = self._ticks_add(
                now_ms, self._poll_interval_ms
            )
            return True
        except BaseException:
            self._set_fault(ERROR_BUS)
            raise

        self.conversions += 1
        self._cycle_had_error = False
        self._read_index = 0
        self._conversion_due_ms = self._ticks_add(
            now_ms, self._conversion_wait_ms
        )
        self._next_cycle_due_ms = self._ticks_add(
            now_ms, self._poll_interval_ms
        )
        self._state = STATE_WAIT_CONVERSION
        return True

    def _finish_cycle(self, now_ms):
        self._cycle_roms = ()
        self._read_index = 0
        self._conversion_due_ms = None
        self._state = STATE_IDLE
        self.completed_cycles += 1
        self.last_completed_cycle_ms = now_ms
        if not self._cycle_had_error:
            self.last_error = None
        if self._due(now_ms, self._next_cycle_due_ms):
            self._next_cycle_due_ms = self._ticks_add(
                now_ms, self._poll_interval_ms
            )

    def _update_device_failure(self, rom_id, error_code):
        device = self._devices.get(rom_id)
        if device is None:
            return
        device["invalid_readings"] += 1
        device["last_error"] = error_code
        device["trusted"] = False

    def _read_one(self, now_ms):
        raw_rom, rom_id = self._cycle_roms[self._read_index]
        sample_ms = self._conversion_due_ms
        if sample_ms is None:
            raise RuntimeError("conversion timestamp is unavailable")
        self.read_attempts += 1
        try:
            raw_value = self._bus.read_celsius(raw_rom)
        except DS18B20BusError:
            self._cycle_had_error = True
            self.read_errors += 1
            self.last_error = ERROR_READ
            self._clear_rom_trust(rom_id)
            self._manager_call(
                "record_failure", rom_id, sample_ms, ERROR_READ
            )
            self._update_device_failure(rom_id, ERROR_READ)
        except BaseException:
            self._set_fault(ERROR_BUS)
            raise
        else:
            value_c = _normalize_temperature(raw_value)
            if value_c is None:
                self._cycle_had_error = True
                self.invalid_readings += 1
                self.last_error = ERROR_VALUE
                self._clear_rom_trust(rom_id)
                self._manager_call(
                    "record_failure", rom_id, sample_ms, ERROR_VALUE
                )
                self._update_device_failure(rom_id, ERROR_VALUE)
            elif (
                value_c == DS18B20_POWER_ON_READING_C
                and rom_id not in self._trusted_85_rom_ids
            ):
                self._cycle_had_error = True
                self.invalid_readings += 1
                self.last_error = ERROR_POWER_ON_85
                self._manager_call(
                    "record_failure", rom_id, sample_ms, ERROR_POWER_ON_85
                )
                self._update_device_failure(rom_id, ERROR_POWER_ON_85)
            else:
                trusted_rom_ids = set(self._trusted_85_rom_ids)
                trusted_rom_ids.add(rom_id)
                device = self._devices.get(rom_id)
                updated_device = None
                if device is not None:
                    updated_device = dict(device)
                    updated_device["value_c"] = value_c
                    updated_device["last_sample_ms"] = sample_ms
                    updated_device["last_error"] = None
                    updated_device["trusted"] = True
                accepted = self._manager_call(
                    "record_valid", rom_id, value_c, sample_ms
                )
                self._trusted_85_rom_ids = trusted_rom_ids
                if updated_device is not None:
                    self._devices[rom_id] = updated_device
                if accepted is not True:
                    self.manager_rejections += 1
                self.valid_readings += 1

        self._read_index += 1
        if self._read_index >= len(self._cycle_roms):
            self._finish_cycle(now_ms)

    def request_discovery(self):
        """Coalesce a discovery request; the next safe idle step performs it."""

        if self._closed:
            raise RuntimeError("DS18B20 adapter is closed")
        already_requested = self._discovery_requested
        self._discovery_requested = True
        return not already_requested

    def reset_fault(self):
        """Explicitly rearm a faulted adapter for a fresh discovery cycle."""

        if self._closed:
            raise RuntimeError("DS18B20 adapter is closed")
        if self._state != STATE_FAULTED:
            return False
        # A cleanup failure leaves the fault latch intact and can be retried.
        self._clear_all_trust()
        self._cycle_roms = ()
        self._read_index = 0
        self._conversion_due_ms = None
        self._cycle_had_error = False
        self._next_cycle_due_ms = None
        self._discovery_requested = True
        self._state = STATE_IDLE
        return True

    def step(self, now_ms):
        """Perform at most one bounded bus action and return 1 or 0."""

        _require_ticks(now_ms)
        if self._closed or self._state == STATE_FAULTED:
            return 0
        if (
            self._last_poll_ms is not None
            and self._ticks_diff(now_ms, self._last_poll_ms) < 0
        ):
            raise ValueError("now_ms precedes the previous adapter step")
        if self._last_poll_ms == now_ms:
            return 0
        self._last_poll_ms = now_ms

        try:
            return self._step_once(now_ms)
        except BaseException:
            if self._state != STATE_FAULTED:
                self._set_fault(ERROR_BUS)
            raise

    def _step_once(self, now_ms):
        """Run one already time-validated state-machine transition."""

        if self._state == STATE_WAIT_CONVERSION:
            if not self._due(now_ms, self._conversion_due_ms):
                return 0
            self._state = STATE_READING

        if self._state == STATE_READING:
            self._read_one(now_ms)
            self._last_action_ms = now_ms
            return 1

        if self._state == STATE_CONVERSION_READY:
            if self._discovery_requested:
                self._state = STATE_IDLE
            else:
                worked = self._start_conversion(now_ms)
                if worked:
                    self._last_action_ms = now_ms
                    return 1

        discovery_due = (
            self._discovery_requested
            or self._next_discovery_due_ms is None
            or self._due(now_ms, self._next_discovery_due_ms)
        )
        if discovery_due:
            self._perform_discovery(now_ms)
            self._last_action_ms = now_ms
            return 1

        cycle_due = (
            self._next_cycle_due_ms is None
            or self._due(now_ms, self._next_cycle_due_ms)
        )
        if cycle_due and self._roms:
            worked = self._start_conversion(now_ms)
            if worked:
                self._last_action_ms = now_ms
                return 1
        elif cycle_due:
            self._next_cycle_due_ms = self._ticks_add(
                now_ms, self._poll_interval_ms
            )
        return 0

    def status(self):
        devices = tuple(
            dict(self._devices[rom_id]) for rom_id in sorted(self._devices)
        )
        return {
            "state": self._state,
            "closed": self._closed,
            "cleanup_complete": self._cleanup_complete,
            "discovery_requested": self._discovery_requested,
            "discovered_rom_ids": tuple(item[1] for item in self._roms),
            "cycle_rom_ids": tuple(item[1] for item in self._cycle_roms),
            "read_index": self._read_index,
            "next_discovery_due_ms": self._next_discovery_due_ms,
            "next_cycle_due_ms": self._next_cycle_due_ms,
            "conversion_due_ms": self._conversion_due_ms,
            "last_poll_ms": self._last_poll_ms,
            "last_action_ms": self._last_action_ms,
            "scans": self.scans,
            "scan_errors": self.scan_errors,
            "conversions": self.conversions,
            "conversion_errors": self.conversion_errors,
            "read_attempts": self.read_attempts,
            "valid_readings": self.valid_readings,
            "invalid_readings": self.invalid_readings,
            "read_errors": self.read_errors,
            "manager_rejections": self.manager_rejections,
            "manager_errors": self.manager_errors,
            "bus_contract_errors": self.bus_contract_errors,
            "trust_cleanup_errors": self.trust_cleanup_errors,
            "completed_cycles": self.completed_cycles,
            "last_completed_cycle_ms": self.last_completed_cycle_ms,
            "last_error": self.last_error,
            "devices": devices,
        }

    def deinit(self):
        """Close immediately; retry cleanup on later calls until confirmed."""

        self._closed = True
        self._state = STATE_CLOSED
        if self._cleanup_complete:
            return True
        try:
            result = self._bus.deinit()
            if result is not None:
                raise DS18B20BusError("deinit() must return None")
        except BaseException:
            self.last_error = ERROR_BUS
            self.bus_contract_errors += 1
            raise
        self._cleanup_complete = True
        return True
