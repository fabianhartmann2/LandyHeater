"""Lazy MicroPython-v1.28 binding for the cooperative DS18B20 adapter.

Importing this module does not import ``machine``, ``onewire`` or ``ds18x20``
and performs no hardware I/O.  The public board factory checks the dedicated
1-Wire lock before loading those modules or constructing a pin.
"""

from adapters.ds18b20_adapter import DS18B20Adapter, DS18B20BusError


ERROR_CLOSED = "micropython_ds18b20_bus_closed"
ERROR_SCAN = "micropython_ds18b20_scan_failed"
ERROR_CONVERSION = "micropython_ds18b20_conversion_failed"
ERROR_READ = "micropython_ds18b20_read_failed"
ERROR_DRIVER_CONTRACT = "micropython_ds18b20_driver_contract_failed"
ERROR_LINE_LOW = "micropython_ds18b20_line_not_released"
ERROR_PIN_CLEANUP = "micropython_ds18b20_pin_cleanup_failed"


class DS18B20HardwareError(RuntimeError):
    """Hardware binding or guarded factory failure."""


class DS18B20HardwareCleanupError(DS18B20HardwareError):
    """The factory failed and GPIO cleanup could not be confirmed."""

    def __init__(self, message, primary_error, cleanup_error):
        super().__init__(message)
        # Keep both original exceptions inspectable without constructing
        # unbounded driver-provided strings in this already-failing path.
        self.primary_error = primary_error
        self.cleanup_error = cleanup_error


def _require_positive_integer(name, value):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("{} must be a positive integer".format(name))


def _validate_manager_port(manager, max_sensors):
    for method_name in (
        "record_discovery",
        "record_valid",
        "record_failure",
    ):
        if not callable(getattr(manager, method_name, None)):
            raise ValueError(
                "temperature_manager must provide {}()".format(method_name)
            )
    if not isinstance(getattr(manager, "assignments", None), dict):
        raise ValueError(
            "temperature_manager must expose detached assignments"
        )
    capacity = getattr(manager, "max_discovered_sensors", None)
    _require_positive_integer(
        "temperature_manager.max_discovered_sensors", capacity
    )
    if max_sensors > capacity:
        raise ValueError(
            "configured sensor limit exceeds TemperatureManager capacity"
        )


class MicroPythonDS18B20Bus:
    """Normalize MicroPython's DS18X20 object to the adapter BusPort."""

    __slots__ = (
        "__scan",
        "__convert_temp",
        "__read_temp",
        "__pin_init",
        "__pin_value",
        "__pin_input_mode",
        "__onewire_error_type",
        "__max_sensors",
        "__closed",
        "__cleanup_complete",
    )

    def __init__(
        self,
        driver,
        pin,
        pin_input_mode,
        max_sensors=16,
        onewire_error_type=None,
    ):
        scan = getattr(driver, "scan", None)
        convert_temp = getattr(driver, "convert_temp", None)
        read_temp = getattr(driver, "read_temp", None)
        pin_init = getattr(pin, "init", None)
        pin_value = getattr(pin, "value", None)
        if not callable(scan):
            raise ValueError("driver must provide scan()")
        if not callable(convert_temp):
            raise ValueError("driver must provide convert_temp()")
        if not callable(read_temp):
            raise ValueError("driver must provide read_temp()")
        if not callable(pin_init):
            raise ValueError("pin must provide init()")
        if not callable(pin_value):
            raise ValueError("pin must provide value()")
        if pin_input_mode is None:
            raise ValueError("machine.Pin.IN is unavailable")
        if onewire_error_type is not None:
            try:
                valid_error_type = (
                    isinstance(onewire_error_type, type)
                    and issubclass(onewire_error_type, Exception)
                )
            except TypeError:
                valid_error_type = False
            if not valid_error_type:
                raise ValueError(
                    "onewire_error_type must be an exception class"
                )
        _require_positive_integer("max_sensors", max_sensors)

        self.__scan = scan
        self.__convert_temp = convert_temp
        self.__read_temp = read_temp
        self.__pin_init = pin_init
        self.__pin_value = pin_value
        self.__pin_input_mode = pin_input_mode
        self.__onewire_error_type = onewire_error_type
        self.__max_sensors = max_sensors
        self.__closed = False
        self.__cleanup_complete = False

    @property
    def closed(self):
        return self.__closed

    @property
    def cleanup_complete(self):
        return self.__cleanup_complete

    @property
    def max_sensors(self):
        return self.__max_sensors

    def _require_open(self):
        if self.__closed:
            raise DS18B20BusError(ERROR_CLOSED)

    def _is_expected_driver_error(self, error, crc_error_allowed=False):
        if isinstance(error, OSError):
            return True
        if (
            self.__onewire_error_type is not None
            and isinstance(error, self.__onewire_error_type)
        ):
            return True
        return (
            crc_error_allowed
            and type(error) is Exception
            and error.args == ("CRC error",)
        )

    def _check_line_released(self):
        """Reject a stuck-low bus before it can masquerade as 0.0 C."""

        try:
            level = self.__pin_value()
        except Exception as error:
            if self._is_expected_driver_error(error):
                raise DS18B20BusError(ERROR_LINE_LOW)
            raise
        if type(level) is not int or level != 1:
            raise DS18B20BusError(ERROR_LINE_LOW)

    def scan(self):
        self._require_open()
        self._check_line_released()
        try:
            raw_roms = self.__scan()
        except Exception as error:
            if self._is_expected_driver_error(error):
                raise DS18B20BusError(ERROR_SCAN)
            raise
        self._check_line_released()
        if type(raw_roms) not in (list, tuple):
            raise DS18B20BusError(ERROR_DRIVER_CONTRACT)
        if len(raw_roms) > self.__max_sensors:
            raise DS18B20BusError(ERROR_DRIVER_CONTRACT)
        try:
            copied = []
            for raw_rom in raw_roms:
                if not isinstance(raw_rom, (bytes, bytearray, memoryview)):
                    raise DS18B20BusError(ERROR_DRIVER_CONTRACT)
                # Preserve the official driver's raw result.  The pure core
                # remains the single authority for family, ROM CRC and
                # duplicate validation and rejects a mixed scan atomically.
                copied.append(bytes(raw_rom))
            return tuple(copied)
        except MemoryError:
            raise
        except DS18B20BusError:
            raise
        except Exception:
            raise DS18B20BusError(ERROR_DRIVER_CONTRACT)

    def start_conversion(self):
        self._require_open()
        self._check_line_released()
        try:
            result = self.__convert_temp()
        except Exception as error:
            if self._is_expected_driver_error(error):
                raise DS18B20BusError(ERROR_CONVERSION)
            raise
        self._check_line_released()
        if result is not None:
            raise DS18B20BusError(ERROR_DRIVER_CONTRACT)
        return None

    def read_celsius(self, raw_rom):
        self._require_open()
        if not isinstance(raw_rom, (bytes, bytearray, memoryview)):
            raise DS18B20BusError(ERROR_DRIVER_CONTRACT)
        try:
            raw_rom = bytes(raw_rom)
        except MemoryError:
            raise
        except Exception:
            raise DS18B20BusError(ERROR_DRIVER_CONTRACT)
        self._check_line_released()
        try:
            result = self.__read_temp(raw_rom)
        except Exception as error:
            # MicroPython v1.28 raises exactly Exception("CRC error") for a
            # scratchpad mismatch.  Other unexpected exceptions must reach
            # the adapter so it can latch FAULTED instead of hiding a bug.
            if self._is_expected_driver_error(
                error, crc_error_allowed=True
            ):
                raise DS18B20BusError(ERROR_READ)
            raise
        self._check_line_released()
        return result

    def deinit(self):
        """Close immediately; retry Pin.IN cleanup until it succeeds."""

        self.__closed = True
        if self.__cleanup_complete:
            return None
        try:
            result = self.__pin_init(
                self.__pin_input_mode, pull=None, hold=False
            )
        except MemoryError:
            raise
        except Exception:
            raise DS18B20HardwareError(ERROR_PIN_CLEANUP)
        if result is not None:
            raise DS18B20HardwareError(ERROR_PIN_CLEANUP)
        self.__cleanup_complete = True
        return None


def _cleanup_pin(pin, pin_input_mode):
    cleanup_error = None
    for _ in range(2):
        try:
            result = pin.init(pin_input_mode, pull=None, hold=False)
            if result is not None:
                raise DS18B20HardwareError(ERROR_PIN_CLEANUP)
            return None
        except BaseException as error:
            cleanup_error = error
    return cleanup_error


def _cleanup_unreturned_pin(pin_class, pin_number, pin_input_mode):
    """Best-effort cleanup when Pin() raised before returning a handle."""

    cleanup_error = None
    for _ in range(2):
        try:
            pin_class(
                pin_number,
                pin_input_mode,
                pull=None,
                hold=False,
            )
            return None
        except BaseException as error:
            cleanup_error = error
    return cleanup_error


def open_ds18b20_adapter_from_board_config(temperature_manager):
    """Open the approved ESP32 1-Wire stack and return a core adapter.

    With the delivered configuration this function fails at the board guard,
    before any MicroPython hardware module is imported.
    """

    import board_config

    board_config.require_onewire_configuration()
    max_sensors = board_config.ONEWIRE_MAX_SENSORS
    conversion_wait_ms = board_config.ONEWIRE_CONVERSION_WAIT_MS
    poll_interval_ms = board_config.ONEWIRE_POLL_INTERVAL_MS
    discovery_interval_ms = board_config.ONEWIRE_DISCOVERY_INTERVAL_MS
    pin_number = board_config.ONEWIRE_PIN
    _validate_manager_port(temperature_manager, max_sensors)

    try:
        from machine import Pin
        from onewire import OneWire, OneWireError
        from ds18x20 import DS18X20
    except ImportError:
        raise DS18B20HardwareError(
            "MicroPython machine/onewire/ds18x20 modules are unavailable"
        )

    pin_input_mode = getattr(Pin, "IN", None)
    pin_open_drain_mode = getattr(Pin, "OPEN_DRAIN", None)
    pin_pull_up = getattr(Pin, "PULL_UP", None)
    if (
        pin_input_mode is None
        or pin_open_drain_mode is None
        or pin_pull_up is None
    ):
        raise DS18B20HardwareError(
            "required machine.Pin modes are unavailable"
        )
    if not callable(OneWire) or not callable(DS18X20):
        raise DS18B20HardwareError(
            "MicroPython OneWire/DS18X20 constructors are unavailable"
        )

    pin = None
    bus_port = None
    try:
        # Pin(number) resolves the pad without intentionally selecting an
        # output mode.  Set the open-drain output latch high before OneWire's
        # own init so an old low latch cannot hold the bus down.
        pin = Pin(pin_number)
        result = pin.init(
            pin_open_drain_mode,
            pull=pin_pull_up,
            value=1,
            hold=False,
        )
        if result is not None:
            raise DS18B20HardwareError(ERROR_DRIVER_CONTRACT)
        one_wire = OneWire(pin)
        driver = DS18X20(one_wire)
        bus_port = MicroPythonDS18B20Bus(
            driver,
            pin,
            pin_input_mode,
            max_sensors=max_sensors,
            onewire_error_type=OneWireError,
        )
        return DS18B20Adapter(
            bus_port,
            temperature_manager,
            conversion_wait_ms=conversion_wait_ms,
            poll_interval_ms=poll_interval_ms,
            discovery_interval_ms=discovery_interval_ms,
            max_sensors=max_sensors,
        )
    except BaseException as primary_error:
        cleanup_error = None
        if bus_port is not None:
            for _ in range(2):
                try:
                    bus_port.deinit()
                    cleanup_error = None
                    break
                except BaseException as error:
                    cleanup_error = error
        elif pin is not None:
            cleanup_error = _cleanup_pin(pin, pin_input_mode)
        else:
            cleanup_error = _cleanup_unreturned_pin(
                Pin, pin_number, pin_input_mode
            )
        if cleanup_error is not None:
            raise DS18B20HardwareCleanupError(
                "DS18B20 setup failed and GPIO cleanup could not be "
                "confirmed",
                primary_error,
                cleanup_error,
            )
        raise
