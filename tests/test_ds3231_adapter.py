import unittest

from adapters.ds3231_adapter import (
    DS3231Adapter,
    DS3231BusError,
    DS3231ClockInvalidError,
    DS3231DataError,
)


def bcd(value):
    return ((value // 10) << 4) | value % 10


def time_registers(
    year=2026,
    month=8,
    day=9,
    weekday=7,
    hour=14,
    minute=30,
    second=0,
):
    return bytes((
        bcd(second),
        bcd(minute),
        bcd(hour),
        bcd(weekday),
        bcd(day),
        bcd(month),
        bcd(year - 2000),
    ))


class FakeI2C:
    def __init__(self):
        self.registers = bytearray(0x13)
        self.registers[0:7] = time_registers()
        self.calls = []
        self.read_plan = []
        self.write_plan = []
        self.after_time_write = None
        self.after_read = None
        self.after_write = None

    def readfrom_mem(self, address, register, length, **kwargs):
        self.calls.append(("read", address, register, length, dict(kwargs)))
        if self.read_plan:
            result = self.read_plan.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        result = bytes(self.registers[register : register + length])
        if self.after_read is not None:
            self.after_read(register, length)
        return result

    def writeto_mem(self, address, register, data, **kwargs):
        data = bytes(data)
        self.calls.append(("write", address, register, data, dict(kwargs)))
        if self.write_plan:
            result = self.write_plan.pop(0)
            if isinstance(result, BaseException):
                raise result
            if result is not None:
                return result
        self.registers[register : register + len(data)] = data
        if register == 0 and self.after_time_write is not None:
            self.registers[0:7] = self.after_time_write
        if self.after_write is not None:
            self.after_write(register, data)
        return None


class TestDS3231Adapter(unittest.TestCase):
    def test_constructor_is_inert_and_address_is_fixed(self):
        bus = FakeI2C()
        rtc = DS3231Adapter(bus)
        self.assertEqual(rtc.address, 0x68)
        self.assertEqual(bus.calls, [])
        with self.assertRaises(ValueError):
            DS3231Adapter(bus, address=0x69)
        with self.assertRaises(ValueError):
            DS3231Adapter(object())

    def test_reads_valid_24_hour_utc_and_derives_weekday(self):
        bus = FakeI2C()
        sample = DS3231Adapter(bus).read_utc_datetime()
        self.assertEqual(
            sample,
            {
                "year": 2026,
                "month": 8,
                "day": 9,
                "weekday": 6,
                "hour": 14,
                "minute": 30,
                "second": 0,
            },
        )
        self.assertEqual(
            [(call[0], call[2], call[3]) for call in bus.calls],
            [("read", 0x00, 7), ("read", 0x0E, 2)],
        )

    def test_accepts_all_12_hour_boundaries(self):
        cases = ((0x52, 0), (0x41, 1), (0x72, 12), (0x71, 23))
        for raw_hour, expected in cases:
            with self.subTest(raw_hour=raw_hour):
                bus = FakeI2C()
                raw = bytearray(time_registers())
                raw[2] = raw_hour
                bus.registers[0:7] = raw
                self.assertEqual(
                    DS3231Adapter(bus).read_utc_datetime()["hour"], expected
                )

    def test_osf_eosc_and_reserved_status_bits_are_untrusted(self):
        for control, status, expected in (
            (0, 0x80, "oscillator_stop"),
            (0x80, 0, "oscillator_disabled"),
        ):
            bus = FakeI2C()
            bus.registers[0x0E] = control
            bus.registers[0x0F] = status
            with self.subTest(control=control, status=status):
                with self.assertRaisesRegex(DS3231ClockInvalidError, expected):
                    DS3231Adapter(bus).read_utc_datetime()

        bus = FakeI2C()
        bus.registers[0x0F] = 0x10
        with self.assertRaises(DS3231DataError):
            DS3231Adapter(bus).read_utc_datetime()

    def test_bad_time_registers_are_rejected(self):
        mutations = (
            (0, 0x6A),
            (0, 0x80),
            (1, 0x60),
            (2, 0x24),
            (2, 0x40),
            (3, 0),
            (3, 8),
            (4, 0x32),
            (5, 0x13),
            (5, 0x88),
            (6, 0xAA),
        )
        for index, value in mutations:
            bus = FakeI2C()
            raw = bytearray(time_registers())
            raw[index] = value
            bus.registers[0:7] = raw
            with self.subTest(index=index, value=value):
                with self.assertRaises(DS3231DataError):
                    DS3231Adapter(bus).read_utc_datetime()

    def test_weekday_register_mapping_is_user_defined_but_range_checked(self):
        bus = FakeI2C()
        raw = bytearray(time_registers())
        raw[3] = 1
        bus.registers[0:7] = raw
        sample = DS3231Adapter(bus).read_utc_datetime()
        self.assertEqual(sample["weekday"], 6)

    def test_read_contract_and_expected_i2c_error_fail_closed(self):
        for result in (b"short", None, "1234567"):
            bus = FakeI2C()
            bus.read_plan = [result]
            with self.subTest(result=result):
                with self.assertRaises(DS3231BusError):
                    DS3231Adapter(bus).read_utc_datetime()
        bus = FakeI2C()
        bus.read_plan = [OSError("nack")]
        with self.assertRaises(DS3231BusError):
            DS3231Adapter(bus).read_utc_datetime()

    def test_memory_and_unexpected_errors_are_not_normalized(self):
        for error in (MemoryError("oom"), AssertionError("bug")):
            bus = FakeI2C()
            bus.read_plan = [error]
            with self.subTest(error=type(error)):
                with self.assertRaises(type(error)):
                    DS3231Adapter(bus).read_utc_datetime()

    def test_write_is_verified_before_osf_clear_and_preserves_status(self):
        bus = FakeI2C()
        bus.registers[0x0E] = 0x04
        bus.registers[0x0F] = 0x8B
        verified = DS3231Adapter(bus).write_utc_datetime(
            2026, 8, 9, 14, 30, 0
        )
        self.assertEqual(verified["hour"], 14)
        writes = [call for call in bus.calls if call[0] == "write"]
        self.assertEqual(writes[0][2:4], (0x0E, b"\x84"))
        self.assertIn((0, time_registers()), [call[2:4] for call in writes])
        self.assertIn((0x0E, b"\x04"), [call[2:4] for call in writes])
        self.assertEqual(writes[-1][2:4], (0x0F, b"\x0b"))
        verify_read_index = next(
            index
            for index, call in enumerate(bus.calls)
            if call[0] == "read" and call[2] == 0 and index > 0
        )
        status_write_index = next(
            index
            for index, call in enumerate(bus.calls)
            if call[0] == "write" and call[2] == 0x0F
        )
        self.assertLess(verify_read_index, status_write_index)
        self.assertEqual(bus.registers[0x0F], 0x0B)

    def test_staged_write_remains_untrusted_until_single_use_commit(self):
        bus = FakeI2C()
        bus.registers[0x0E] = 0x04
        bus.registers[0x0F] = 0x80
        rtc = DS3231Adapter(bus)
        verified = rtc.stage_utc_datetime(2026, 8, 9, 14, 30, 0)
        self.assertEqual(verified["minute"], 30)
        self.assertEqual(bus.registers[0x0E] & 0x80, 0x80)
        with self.assertRaisesRegex(
            DS3231ClockInvalidError, "oscillator_disabled"
        ):
            DS3231Adapter(bus).read_utc_datetime()

        self.assertIsNone(rtc.commit_staged_write())
        self.assertEqual(bus.registers[0x0E] & 0x80, 0)
        self.assertEqual(bus.registers[0x0F] & 0x80, 0)
        self.assertEqual(rtc.read_utc_datetime()["minute"], 30)
        with self.assertRaisesRegex(DS3231DataError, "no_staged"):
            rtc.commit_staged_write()

    def test_failed_restage_invalidates_the_previous_commit_token(self):
        bus = FakeI2C()
        rtc = DS3231Adapter(bus)
        rtc.stage_utc_datetime(2026, 8, 9, 14, 30, 0)
        bus.after_time_write = time_registers(minute=32)
        with self.assertRaisesRegex(DS3231DataError, "verify_mismatch"):
            rtc.stage_utc_datetime(2026, 8, 9, 14, 31, 0)
        with self.assertRaisesRegex(DS3231DataError, "no_staged"):
            rtc.commit_staged_write()
        self.assertEqual(bus.registers[0x0E] & 0x80, 0x80)

    def test_staged_commit_retries_after_marker_was_already_cleared(self):
        bus = FakeI2C()
        bus.registers[0x0E] = 0x04
        bus.registers[0x0F] = 0x80
        rtc = DS3231Adapter(bus)
        rtc.stage_utc_datetime(2026, 8, 9, 14, 30, 0)
        status_reads = [0]

        def fail_final_trust_read(register, length):
            if register == 0x0E and length == 2:
                status_reads[0] += 1
                if status_reads[0] == 2:
                    raise OSError("final verify interrupted")

        # Stage already performed the first 0x0E/2 read.  Within commit the
        # second such read is the final trust verification.
        bus.after_read = fail_final_trust_read
        with self.assertRaises(DS3231BusError):
            rtc.commit_staged_write()
        self.assertEqual(bus.registers[0x0E] & 0x80, 0)

        bus.after_read = None
        self.assertIsNone(rtc.commit_staged_write())
        self.assertEqual(rtc.read_utc_datetime()["minute"], 30)

    def test_write_enables_oscillator_and_accepts_one_second_rollover(self):
        bus = FakeI2C()
        bus.registers[0x0E] = 0x84
        bus.registers[0x0F] = 0x80
        bus.after_time_write = time_registers(second=1)
        verified = DS3231Adapter(bus).write_utc_datetime(
            2026, 8, 9, 14, 30, 0
        )
        self.assertEqual(verified["second"], 1)
        self.assertIn(
            ("write", 0x68, 0x0E, b"\x04", {"addrsize": 8}),
            bus.calls,
        )

    def test_new_osf_after_clear_status_read_is_never_erased(self):
        bus = FakeI2C()

        def assert_osf_after_status_read(register, length):
            if register == 0x0F and length == 1:
                bus.registers[0x0F] |= 0x80

        bus.after_read = assert_osf_after_status_read
        with self.assertRaisesRegex(DS3231BusError, "trust_verify"):
            DS3231Adapter(bus).write_utc_datetime(
                2026, 8, 9, 14, 30, 0
            )
        self.assertEqual(bus.registers[0x0F] & 0x80, 0x80)
        self.assertFalse(
            any(call[0] == "write" and call[2] == 0x0F for call in bus.calls)
        )

    def test_status_and_stable_control_corruption_fail_verification(self):
        bus = FakeI2C()
        bus.registers[0x0E] = 0x04
        bus.registers[0x0F] = 0x8B

        def corrupt_status(register, data):
            if register == 0x0F:
                bus.registers[0x0F] = 0

        bus.after_write = corrupt_status
        with self.assertRaisesRegex(DS3231BusError, "trust_verify"):
            DS3231Adapter(bus).write_utc_datetime(
                2026, 8, 9, 14, 30, 0
            )

        bus = FakeI2C()
        bus.registers[0x0E] = 0x24  # CONV may self-clear; INTCN must not.
        bus.registers[0x0F] = 0x80

        def clear_convert_only(register, data):
            if register == 0x0F:
                bus.registers[0x0E] &= ~0x20

        bus.after_write = clear_convert_only
        self.assertEqual(
            DS3231Adapter(bus).write_utc_datetime(
                2026, 8, 9, 14, 30, 0
            )["minute"],
            30,
        )

    def test_verify_mismatch_never_clears_osf(self):
        bus = FakeI2C()
        bus.registers[0x0F] = 0x80
        bus.after_time_write = time_registers(minute=31)
        with self.assertRaisesRegex(DS3231DataError, "verify_mismatch"):
            DS3231Adapter(bus).write_utc_datetime(
                2026, 8, 9, 14, 30, 0
            )
        self.assertEqual(bus.registers[0x0F], 0x80)
        self.assertEqual(bus.registers[0x0E] & 0x80, 0x80)
        self.assertFalse(
            any(call[0] == "write" and call[2] == 0x0F for call in bus.calls)
        )

    def test_failed_write_leaves_durable_eosc_transaction_marker(self):
        bus = FakeI2C()
        bus.registers[0x0E] = 0x04
        bus.registers[0x0F] = 0
        bus.after_time_write = time_registers(minute=31)
        with self.assertRaisesRegex(DS3231DataError, "verify_mismatch"):
            DS3231Adapter(bus).write_utc_datetime(
                2026, 8, 9, 14, 30, 0
            )
        self.assertEqual(bus.registers[0x0E] & 0x80, 0x80)
        with self.assertRaisesRegex(
            DS3231ClockInvalidError, "oscillator_disabled"
        ):
            DS3231Adapter(bus).read_utc_datetime()

    def test_verify_requires_canonical_weekday_and_24_hour_registers(self):
        for index, value in ((3, 1), (2, 0x62)):
            bus = FakeI2C()
            bus.registers[0x0F] = 0x80
            raw = bytearray(time_registers())
            raw[index] = value
            bus.after_time_write = bytes(raw)
            with self.subTest(index=index, value=value):
                with self.assertRaisesRegex(
                    DS3231DataError, "not_canonical"
                ):
                    DS3231Adapter(bus).write_utc_datetime(
                        2026, 8, 9, 14, 30, 0
                    )
                self.assertEqual(bus.registers[0x0F] & 0x80, 0x80)

    def test_invalid_write_arguments_perform_no_io(self):
        bus = FakeI2C()
        rtc = DS3231Adapter(bus)
        for args in (
            (2026, 2, 29, 0, 0, 0),
            (True, 1, 1, 0, 0, 0),
            (2100, 1, 1, 0, 0, 0),
        ):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    rtc.write_utc_datetime(*args)
        self.assertEqual(bus.calls, [])

    def test_non_none_write_result_is_contract_failure(self):
        bus = FakeI2C()
        bus.write_plan = [False]
        with self.assertRaises(DS3231BusError):
            DS3231Adapter(bus).write_utc_datetime(
                2026, 8, 9, 14, 30, 0
            )


if __name__ == "__main__":
    unittest.main()
