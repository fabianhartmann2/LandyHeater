"""Locked MicroPython-v1.28 binding for the DS3231 UTC clock.

Importing this module performs no hardware import or I/O.  The public factory
checks the dedicated board lock before importing ``machine`` or constructing
GPIO/I2C objects.  The delivered configuration keeps that lock closed.
"""

from adapters.ds3231_adapter import DS3231Adapter, DS3231BusError


ERROR_CLOSED = "micropython_ds3231_closed"
ERROR_CLEANUP = "micropython_ds3231_cleanup_failed"
ERROR_DRIVER_CONTRACT = "micropython_ds3231_driver_contract_failed"

_I2C_LEASED = False
_I2C_LEASE_POISONED = False


class DS3231HardwareError(RuntimeError):
    """MicroPython binding or guarded factory failure."""


class DS3231HardwareCleanupError(DS3231HardwareError):
    """Setup failed and release of the I2C pins was not confirmed."""

    def __init__(self, message, primary_error, cleanup_error):
        super().__init__(message)
        self.primary_error = primary_error
        self.cleanup_error = cleanup_error


def _release_pin(pin, input_mode):
    result = pin.init(input_mode, pull=None, hold=False)
    if result is not None:
        raise DS3231HardwareError(ERROR_CLEANUP)


def _cleanup_pin_bounded(pin, input_mode):
    last_error = None
    for _ in range(2):
        try:
            _release_pin(pin, input_mode)
            return None
        except BaseException as error:
            last_error = error
    return last_error


def _cleanup_unreturned_pin(pin_class, number, input_mode):
    last_error = None
    for _ in range(2):
        try:
            pin_class(number, input_mode, pull=None, hold=False)
            return None
        except BaseException as error:
            last_error = error
    return last_error


class MicroPythonDS3231:
    """Closed-first DS3231 port with retryable pin cleanup."""

    __slots__ = (
        "__rtc",
        "__i2c_deinit",
        "__sda_init",
        "__scl_init",
        "__input_mode",
        "__closed",
        "__i2c_released",
        "__sda_released",
        "__scl_released",
        "__lease_owner",
    )

    def __init__(self, i2c, sda_pin, scl_pin, input_mode, address=0x68):
        if input_mode is None:
            raise ValueError("machine.Pin.IN is unavailable")
        sda_init = getattr(sda_pin, "init", None)
        scl_init = getattr(scl_pin, "init", None)
        if not callable(sda_init) or not callable(scl_init):
            raise ValueError("I2C pins must provide init()")
        self.__rtc = DS3231Adapter(i2c, address=address)
        i2c_deinit = getattr(i2c, "deinit", None)
        if i2c_deinit is not None and not callable(i2c_deinit):
            raise ValueError("i2c.deinit must be callable when present")
        self.__i2c_deinit = i2c_deinit
        self.__sda_init = sda_init
        self.__scl_init = scl_init
        self.__input_mode = input_mode
        self.__closed = False
        self.__i2c_released = i2c_deinit is None
        self.__sda_released = False
        self.__scl_released = False
        self.__lease_owner = False

    @property
    def closed(self):
        return self.__closed

    @property
    def cleanup_complete(self):
        return (
            self.__i2c_released
            and self.__sda_released
            and self.__scl_released
        )

    def _claim_lease(self):
        self.__lease_owner = True

    def _require_open(self):
        if self.__closed:
            raise DS3231BusError(ERROR_CLOSED)

    def status(self):
        self._require_open()
        return self.__rtc.status()

    def read_utc_datetime(self):
        self._require_open()
        return self.__rtc.read_utc_datetime()

    def write_utc_datetime(
        self, year, month, day, hour, minute, second
    ):
        self._require_open()
        return self.__rtc.write_utc_datetime(
            year, month, day, hour, minute, second
        )

    def stage_utc_datetime(
        self, year, month, day, hour, minute, second
    ):
        self._require_open()
        return self.__rtc.stage_utc_datetime(
            year, month, day, hour, minute, second
        )

    def commit_staged_write(self):
        self._require_open()
        return self.__rtc.commit_staged_write()

    def deinit(self):
        """Close immediately and retry only incomplete cleanup stages."""

        global _I2C_LEASED, _I2C_LEASE_POISONED
        self.__closed = True
        if self.cleanup_complete:
            if self.__lease_owner:
                _I2C_LEASED = False
                _I2C_LEASE_POISONED = False
                self.__lease_owner = False
            return None

        first_error = None
        if not self.__i2c_released:
            try:
                result = self.__i2c_deinit()
                if result is not None:
                    raise DS3231HardwareError(ERROR_DRIVER_CONTRACT)
                self.__i2c_released = True
            except BaseException as error:
                first_error = error
        if not self.__sda_released:
            try:
                result = self.__sda_init(
                    self.__input_mode, pull=None, hold=False
                )
                if result is not None:
                    raise DS3231HardwareError(ERROR_CLEANUP)
                self.__sda_released = True
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if not self.__scl_released:
            try:
                result = self.__scl_init(
                    self.__input_mode, pull=None, hold=False
                )
                if result is not None:
                    raise DS3231HardwareError(ERROR_CLEANUP)
                self.__scl_released = True
            except BaseException as error:
                if first_error is None:
                    first_error = error

        if self.cleanup_complete and self.__lease_owner:
            _I2C_LEASED = False
            _I2C_LEASE_POISONED = False
            self.__lease_owner = False
        elif self.__lease_owner:
            _I2C_LEASE_POISONED = True
        if first_error is not None:
            raise first_error
        return None


def _cleanup_factory_resources(i2c, sda, scl, pin_class, numbers, input_mode):
    cleanup_error = None
    deinit = getattr(i2c, "deinit", None) if i2c is not None else None
    if callable(deinit):
        deinit_complete = False
        for _ in range(2):
            try:
                result = deinit()
                if result is not None:
                    raise DS3231HardwareError(ERROR_DRIVER_CONTRACT)
                deinit_complete = True
                break
            except BaseException as error:
                cleanup_error = error
        if deinit_complete:
            cleanup_error = None
    for pin, number in ((sda, numbers[0]), (scl, numbers[1])):
        if pin is not None:
            error = _cleanup_pin_bounded(pin, input_mode)
        else:
            error = _cleanup_unreturned_pin(pin_class, number, input_mode)
        if error is not None:
            cleanup_error = error
    return cleanup_error


def open_ds3231_from_board_config():
    """Open the approved DS3231 I2C stack without probing the bus."""

    global _I2C_LEASED, _I2C_LEASE_POISONED
    import board_config

    board_config.require_i2c_configuration()
    if _I2C_LEASED or _I2C_LEASE_POISONED:
        raise DS3231HardwareError("DS3231 I2C bus is already owned")

    i2c_id = board_config.I2C_ID
    sda_number = board_config.I2C_SDA_PIN
    scl_number = board_config.I2C_SCL_PIN
    frequency = board_config.I2C_FREQUENCY_HZ
    timeout_us = board_config.I2C_TIMEOUT_US
    address = board_config.DS3231_I2C_ADDRESS

    try:
        from machine import I2C, Pin
    except ImportError:
        raise DS3231HardwareError("MicroPython machine.I2C is unavailable")
    if not callable(I2C) or not callable(Pin):
        raise DS3231HardwareError("MicroPython I2C/Pin constructors unavailable")
    input_mode = getattr(Pin, "IN", None)
    if input_mode is None:
        raise DS3231HardwareError("machine.Pin.IN is unavailable")

    sda = None
    scl = None
    i2c = None
    try:
        sda = Pin(sda_number)
        scl = Pin(scl_number)
        i2c = I2C(
            i2c_id,
            sda=sda,
            scl=scl,
            freq=frequency,
            timeout=timeout_us,
        )
        port = MicroPythonDS3231(
            i2c, sda, scl, input_mode, address=address
        )
        port._claim_lease()
        _I2C_LEASED = True
        return port
    except BaseException as primary_error:
        # The public factory did not return an owner.  Clear a possibly
        # committed lease even when a BaseException landed between the final
        # assignment and return; cleanup failure below poisons it separately.
        _I2C_LEASED = False
        cleanup_error = _cleanup_factory_resources(
            i2c,
            sda,
            scl,
            Pin,
            (sda_number, scl_number),
            input_mode,
        )
        if cleanup_error is not None:
            _I2C_LEASE_POISONED = True
            raise DS3231HardwareCleanupError(
                ERROR_CLEANUP, primary_error, cleanup_error
            )
        raise
