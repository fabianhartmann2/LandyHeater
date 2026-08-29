"""Hardware-independent DS3231 register codec and I2C port adapter.

The DS3231 is treated as a UTC clock.  Local time and the fixed UTC offset
remain the responsibility of :mod:`services.time_service`.  This module never
imports ``machine`` and performs I2C activity only when one of its public
methods is called.
"""


DS3231_DEFAULT_ADDRESS = 0x68

_REGISTER_TIME = 0x00
_REGISTER_CONTROL = 0x0E
_REGISTER_STATUS = 0x0F
_CONTROL_EOSC = 0x80
_STATUS_OSF = 0x80


class DS3231Error(RuntimeError):
    """Base class for bounded DS3231 failures."""


class DS3231BusError(DS3231Error):
    """An expected I2C operation failed."""


class DS3231DataError(DS3231Error):
    """The RTC returned malformed or incoherent register data."""


class DS3231ClockInvalidError(DS3231DataError):
    """The oscillator-stop flag says that the stored time is untrusted."""


def _require_integer(name, value, minimum, maximum):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("{} must be an integer".format(name))
    if value < minimum or value > maximum:
        raise ValueError(
            "{} must be between {} and {}".format(name, minimum, maximum)
        )


def _is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year, month):
    if month == 2:
        return 29 if _is_leap_year(year) else 28
    if month in (4, 6, 9, 11):
        return 30
    return 31


def _validate_datetime(year, month, day, hour, minute, second):
    _require_integer("year", year, 2000, 2099)
    _require_integer("month", month, 1, 12)
    _require_integer("day", day, 1, _days_in_month(year, month))
    _require_integer("hour", hour, 0, 23)
    _require_integer("minute", minute, 0, 59)
    _require_integer("second", second, 0, 59)


def _days_since_2000(year, month, day):
    days = 0
    current = 2000
    while current < year:
        days += 366 if _is_leap_year(current) else 365
        current += 1
    current_month = 1
    while current_month < month:
        days += _days_in_month(year, current_month)
        current_month += 1
    return days + day - 1


def _iso_weekday(year, month, day):
    # 2000-01-01 was Saturday (ISO weekday 6).
    return (_days_since_2000(year, month, day) + 5) % 7 + 1


def _datetime_seconds(year, month, day, hour, minute, second):
    return (
        _days_since_2000(year, month, day) * 86400
        + hour * 3600
        + minute * 60
        + second
    )


def _encode_bcd(value):
    return ((value // 10) << 4) | (value % 10)


def _encode_datetime_payload(year, month, day, hour, minute, second):
    return bytes((
        _encode_bcd(second),
        _encode_bcd(minute),
        _encode_bcd(hour),
        _encode_bcd(_iso_weekday(year, month, day)),
        _encode_bcd(day),
        _encode_bcd(month),
        _encode_bcd(year - 2000),
    ))


def _decode_bcd(value, name, minimum, maximum):
    tens = (value >> 4) & 0x0F
    units = value & 0x0F
    if tens > 9 or units > 9:
        raise DS3231DataError("ds3231_invalid_{}_bcd".format(name))
    decoded = tens * 10 + units
    if decoded < minimum or decoded > maximum:
        raise DS3231DataError("ds3231_invalid_{}".format(name))
    return decoded


def _control_matches(actual, expected):
    # CONV (bit 5) is self-clearing.  All other control bits must match;
    # an unexpected 0->1 CONV transition is still rejected.
    stable_mask = 0xDF
    return (
        (actual & stable_mask) == (expected & stable_mask)
        and ((expected & 0x20) or not (actual & 0x20))
    )


class DS3231Adapter:
    """Translate DS3231 registers over an injected MicroPython-like I2C port."""

    __slots__ = (
        "__readfrom_mem",
        "__writeto_mem",
        "__address",
        "__staged_control",
        "__staged_status",
        "__staged_seconds",
    )

    def __init__(self, i2c, address=DS3231_DEFAULT_ADDRESS):
        readfrom_mem = getattr(i2c, "readfrom_mem", None)
        writeto_mem = getattr(i2c, "writeto_mem", None)
        if not callable(readfrom_mem):
            raise ValueError("i2c must provide readfrom_mem()")
        if not callable(writeto_mem):
            raise ValueError("i2c must provide writeto_mem()")
        _require_integer("address", address, 0x08, 0x77)
        if address != DS3231_DEFAULT_ADDRESS:
            raise ValueError("DS3231 address must be 0x68")
        self.__readfrom_mem = readfrom_mem
        self.__writeto_mem = writeto_mem
        self.__address = address
        self.__staged_control = None
        self.__staged_status = None
        self.__staged_seconds = None

    @property
    def address(self):
        return self.__address

    def _read(self, register, length, error_code):
        try:
            data = self.__readfrom_mem(
                self.__address, register, length, addrsize=8
            )
        except MemoryError:
            raise
        except OSError:
            raise DS3231BusError(error_code)
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise DS3231BusError("ds3231_i2c_read_contract_failed")
        try:
            data = bytes(data)
        except MemoryError:
            raise
        except Exception:
            raise DS3231BusError("ds3231_i2c_read_contract_failed")
        if len(data) != length:
            raise DS3231BusError("ds3231_i2c_read_contract_failed")
        return data

    def _write(self, register, data, error_code):
        try:
            result = self.__writeto_mem(
                self.__address, register, data, addrsize=8
            )
        except MemoryError:
            raise
        except OSError:
            raise DS3231BusError(error_code)
        if result is not None:
            raise DS3231BusError("ds3231_i2c_write_contract_failed")

    def status(self):
        raw = self._read(
            _REGISTER_CONTROL, 2, "ds3231_status_read_failed"
        )
        control = raw[0]
        status = raw[1]
        if status & 0x70:
            raise DS3231DataError("ds3231_reserved_status_bits_set")
        return {
            "oscillator_disabled": bool(control & _CONTROL_EOSC),
            "oscillator_stopped": bool(status & _STATUS_OSF),
            "busy": bool(status & 0x04),
            "alarm_2": bool(status & 0x02),
            "alarm_1": bool(status & 0x01),
            "enable_32khz": bool(status & 0x08),
            "control_raw": control,
            "status_raw": status,
        }

    @staticmethod
    def _decode_time(raw):
        if len(raw) != 7:
            raise DS3231DataError("ds3231_time_length_mismatch")
        if raw[0] & 0x80 or raw[1] & 0x80:
            raise DS3231DataError("ds3231_reserved_time_bits_set")
        second = _decode_bcd(raw[0] & 0x7F, "second", 0, 59)
        minute = _decode_bcd(raw[1] & 0x7F, "minute", 0, 59)

        hour_raw = raw[2]
        if hour_raw & 0x80:
            raise DS3231DataError("ds3231_reserved_hour_bit_set")
        if hour_raw & 0x40:
            hour_12 = _decode_bcd(hour_raw & 0x1F, "hour", 1, 12)
            hour = hour_12 % 12
            if hour_raw & 0x20:
                hour += 12
        else:
            hour = _decode_bcd(hour_raw & 0x3F, "hour", 0, 23)

        weekday = raw[3]
        if weekday < 1 or weekday > 7:
            raise DS3231DataError("ds3231_invalid_weekday")
        if raw[4] & 0xC0:
            raise DS3231DataError("ds3231_reserved_date_bits_set")
        day = _decode_bcd(raw[4] & 0x3F, "day", 1, 31)
        if raw[5] & 0x60:
            raise DS3231DataError("ds3231_reserved_month_bits_set")
        if raw[5] & 0x80:
            raise DS3231DataError("ds3231_century_outside_supported_range")
        month = _decode_bcd(raw[5] & 0x1F, "month", 1, 12)
        year = 2000 + _decode_bcd(raw[6], "year", 0, 99)
        try:
            _validate_datetime(year, month, day, hour, minute, second)
        except ValueError:
            raise DS3231DataError("ds3231_invalid_calendar_date")
        # The DS3231 day-of-week register is user-defined.  Validate its
        # range, but derive scheduler weekdays from the calendar date later.
        return {
            "year": year,
            "month": month,
            "day": day,
            "weekday": _iso_weekday(year, month, day) - 1,
            "hour": hour,
            "minute": minute,
            "second": second,
        }

    def read_utc_datetime(self):
        """Return one validated UTC datetime snapshot as a detached dict."""

        raw = self._read(_REGISTER_TIME, 7, "ds3231_time_read_failed")
        decoded = self._decode_time(raw)
        status = self.status()
        if status["oscillator_disabled"]:
            raise DS3231ClockInvalidError("ds3231_oscillator_disabled")
        if status["oscillator_stopped"]:
            raise DS3231ClockInvalidError("ds3231_oscillator_stop_flag")
        return decoded

    def stage_utc_datetime(
        self, year, month, day, hour, minute, second
    ):
        """Write and verify UTC while leaving the RTC trust marker locked.

        The caller must invoke :meth:`commit_staged_write` only after its own
        correction generation has been accepted.  Until then EOSC remains set,
        so a processor reset cannot make an obsolete or half-written value
        appear trustworthy.
        """

        _validate_datetime(year, month, day, hour, minute, second)
        payload = _encode_datetime_payload(
            year, month, day, hour, minute, second
        )
        # Never let a failed replacement stage leave an older in-memory
        # commit token usable for the newly touched RTC registers.
        self.__staged_control = None
        self.__staged_status = None
        self.__staged_seconds = None
        trust = self._read(
            _REGISTER_CONTROL, 2, "ds3231_status_read_failed"
        )
        control = trust[0]
        status = trust[1]
        if status & 0x70:
            raise DS3231DataError("ds3231_reserved_status_bits_set")
        # EOSC is the durable write-in-progress marker.  It is committed and
        # verified before the first time-register write, so any interruption
        # leaves subsequent reads fail-closed even across a processor reset.
        transaction_control = control | _CONTROL_EOSC
        if control != transaction_control:
            self._write(
                _REGISTER_CONTROL,
                bytes((transaction_control,)),
                "ds3231_control_write_failed",
            )
            confirmed = self._read(
                _REGISTER_CONTROL, 1, "ds3231_control_read_failed"
            )[0]
            if not _control_matches(confirmed, transaction_control):
                raise DS3231BusError(
                    "ds3231_transaction_marker_verify_failed"
                )

        self._write(_REGISTER_TIME, payload, "ds3231_time_write_failed")
        verified_raw = self._read(
            _REGISTER_TIME, 7, "ds3231_time_verify_failed"
        )
        verified = self._decode_time(verified_raw)
        requested_seconds = _datetime_seconds(
            year, month, day, hour, minute, second
        )
        verified_seconds = _datetime_seconds(
            verified["year"],
            verified["month"],
            verified["day"],
            verified["hour"],
            verified["minute"],
            verified["second"],
        )
        if verified_seconds not in (requested_seconds, requested_seconds + 1):
            raise DS3231DataError("ds3231_time_verify_mismatch")
        canonical_verified = _encode_datetime_payload(
            verified["year"],
            verified["month"],
            verified["day"],
            verified["hour"],
            verified["minute"],
            verified["second"],
        )
        if verified_raw != canonical_verified:
            raise DS3231DataError("ds3231_time_verify_not_canonical")

        # Commit metadata is installed only after the complete canonical time
        # block has been read back successfully.  EOSC is deliberately still
        # set in hardware at this point.
        self.__staged_control = control & ~_CONTROL_EOSC
        self.__staged_status = status
        self.__staged_seconds = verified_seconds
        return verified

    def commit_staged_write(self):
        """Release one verified staged write and restore RTC trust."""

        expected_control = self.__staged_control
        initial_status = self.__staged_status
        staged_seconds = self.__staged_seconds
        if (
            type(expected_control) is not int
            or type(initial_status) is not int
            or type(staged_seconds) is not int
        ):
            raise DS3231DataError("ds3231_no_staged_write")

        current_raw = self._read(
            _REGISTER_TIME, 7, "ds3231_time_verify_failed"
        )
        current = self._decode_time(current_raw)
        current_seconds = _datetime_seconds(
            current["year"],
            current["month"],
            current["day"],
            current["hour"],
            current["minute"],
            current["second"],
        )
        if current_seconds not in (staged_seconds, staged_seconds + 1):
            raise DS3231DataError("ds3231_staged_time_changed")
        if current_raw != _encode_datetime_payload(
            current["year"],
            current["month"],
            current["day"],
            current["hour"],
            current["minute"],
            current["second"],
        ):
            raise DS3231DataError("ds3231_staged_time_not_canonical")

        staged_trust = self._read(
            _REGISTER_CONTROL, 2, "ds3231_status_read_failed"
        )
        marker_is_set = bool(staged_trust[0] & _CONTROL_EOSC)
        expected_at_stage = (
            expected_control | _CONTROL_EOSC
            if marker_is_set
            else expected_control
        )
        if (
            not _control_matches(staged_trust[0], expected_at_stage)
            or staged_trust[1] & 0x70
        ):
            raise DS3231BusError("ds3231_staged_write_verify_failed")

        if marker_is_set:
            self._write(
                _REGISTER_CONTROL,
                bytes((expected_control,)),
                "ds3231_control_write_failed",
            )
            confirmed_control = self._read(
                _REGISTER_CONTROL, 1, "ds3231_control_read_failed"
            )[0]
            if not _control_matches(confirmed_control, expected_control):
                raise DS3231BusError("ds3231_control_verify_failed")

        status = self._read(
            _REGISTER_STATUS, 1, "ds3231_status_read_failed"
        )[0]
        if status & 0x70:
            raise DS3231DataError("ds3231_reserved_status_bits_set")
        if status & _STATUS_OSF:
            # A1F/A2F are cleared by writing zero and unchanged by writing
            # one.  Write them as ones so an alarm asserted between the read
            # and write is never erased.  Skip the write entirely when OSF is
            # already clear, so a newly asserted OSF remains observable.
            preserved_status = (status & 0x08) | 0x03
            self._write(
                _REGISTER_STATUS,
                bytes((preserved_status,)),
                "ds3231_status_write_failed",
            )
        confirmed = self._read(
            _REGISTER_CONTROL, 2, "ds3231_status_verify_failed"
        )
        confirmed_status = confirmed[1]
        preserved_alarm_bits = status & 0x03
        if (
            not _control_matches(confirmed[0], expected_control)
            or confirmed_status & 0x70
            or confirmed_status & _STATUS_OSF
            or (confirmed_status & 0x08) != (status & 0x08)
            or (confirmed_status & preserved_alarm_bits)
            != preserved_alarm_bits
        ):
            raise DS3231BusError("ds3231_trust_verify_failed")
        self.__staged_control = None
        self.__staged_status = None
        self.__staged_seconds = None
        return None

    def write_utc_datetime(
        self, year, month, day, hour, minute, second
    ):
        """Write, verify and immediately commit one UTC datetime.

        This convenience method is suitable when the caller has no separate
        generation handshake.  RTCTimeBridge uses the staged methods instead.
        """

        verified = self.stage_utc_datetime(
            year, month, day, hour, minute, second
        )
        self.commit_staged_write()
        return verified
