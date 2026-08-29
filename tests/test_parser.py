import unittest

from protocol.autoterm_protocol import (
    CMD_INIT,
    CMD_STATUS,
    DEVICE_HEATER,
    FrameStreamParser,
    build_frame,
    build_init_request,
    build_status_request,
    parse_frame,
)
from protocol.autoterm_frames import RawFrameStreamParser


REAL_HEATER_OFF_STATUS_2026_08_09 = bytes((
    0xAA, 0x04, 0x13, 0x00, 0x0F, 0x00, 0x01, 0x00,
    0x1E, 0x7F, 0x00, 0x80, 0x01, 0x2F, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x60,
    0x6D, 0xA0,
))

REAL_HEATER_INIT_RESPONSE_2026_08_09 = bytes((
    0xAA, 0x04, 0x05, 0x00, 0x04,
    0x12, 0x8A, 0x00, 0x3D, 0xD6,
    0xCB, 0xA6,
))


class TestFrameParser(unittest.TestCase):
    def test_parse_init(self):
        parsed = parse_frame(build_init_request())
        self.assertEqual(parsed["device"], 0x03)
        self.assertEqual(parsed["command"], CMD_INIT)
        self.assertEqual(parsed["command_name"], "init")
        self.assertEqual(parsed["payload_length"], 0)
        self.assertEqual(parsed["payload"], b"")
        self.assertTrue(parsed["crc_valid"])

    def test_bad_crc_is_reported_not_discarded(self):
        raw = bytearray(build_init_request())
        raw[-1] ^= 0x01
        parsed = parse_frame(raw)
        self.assertFalse(parsed["crc_valid"])
        self.assertNotEqual(parsed["crc_received"], parsed["crc_calculated"])

    def test_invalid_start_and_lengths_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_frame(bytes.fromhex("AB 03 00 00 04 9F 3D"))
        with self.assertRaises(ValueError):
            parse_frame(bytes.fromhex("AA 03 00 00 04"))
        with self.assertRaises(ValueError):
            parse_frame(bytes.fromhex("AA 03 01 00 04 9F 3D"))

    def test_unknown_command_is_preserved(self):
        parsed = parse_frame(build_frame(0x7E, b"\x12"))
        self.assertEqual(parsed["command"], 0x7E)
        self.assertEqual(parsed["command_name"], "unknown")
        self.assertEqual(parsed["payload"], b"\x12")

    def test_nonzero_reserved_byte_is_preserved(self):
        parsed = parse_frame(build_frame(0x7E, reserved=0xA5))
        self.assertEqual(parsed["reserved"], 0xA5)

    def test_known_status_offsets(self):
        # The documented fields are at absolute indexes 11, 13, 14 and 19.
        payload = bytearray(15)
        payload[6] = 126
        payload[8] = 7
        payload[9] = 4
        payload[14] = 55
        raw = build_frame(CMD_STATUS, payload, device=DEVICE_HEATER)

        status = parse_frame(raw)["status"]
        self.assertEqual(status["voltage"], 12.6)
        self.assertEqual(status["glow_plug_raw"], 7)
        self.assertEqual(status["heater_state"], 4)
        self.assertEqual(status["heater_state_name"], "running")
        self.assertEqual(status["fan_raw"], 55)

    def test_unknown_heater_state_is_not_guessed(self):
        payload = bytearray(10)
        payload[9] = 2
        raw = build_frame(CMD_STATUS, payload, device=DEVICE_HEATER)
        status = parse_frame(raw)["status"]
        self.assertEqual(status["heater_state"], 2)
        self.assertEqual(status["heater_state_name"], "unknown")

    def test_controller_status_request_is_not_decoded_as_response(self):
        self.assertNotIn("status", parse_frame(build_status_request()))

    def test_short_status_does_not_decode_crc_as_telemetry(self):
        raw = build_frame(CMD_STATUS, b"\x01\x02\x03\x04\x05", device=DEVICE_HEATER)
        status = parse_frame(raw)["status"]
        self.assertEqual(
            status,
            {
                "voltage": None,
                "glow_plug_raw": None,
                "heater_state": None,
                "heater_state_name": "unknown",
                "fan_raw": None,
            },
        )

    def test_real_heater_off_status_capture_2026_08_09(self):
        """Regression vector copied from the working Node-RED Heater RX."""

        parsed = parse_frame(REAL_HEATER_OFF_STATUS_2026_08_09)

        self.assertEqual(len(parsed["raw"]), 26)
        self.assertEqual(parsed["device"], DEVICE_HEATER)
        self.assertEqual(parsed["payload_length"], 19)
        self.assertEqual(parsed["reserved"], 0)
        self.assertEqual(parsed["command"], CMD_STATUS)
        self.assertEqual(parsed["crc_received"], 0x6DA0)
        self.assertEqual(parsed["crc_calculated"], 0x6DA0)
        self.assertTrue(parsed["crc_valid"])
        self.assertEqual(
            parsed["status"],
            {
                "voltage": 12.8,
                "glow_plug_raw": 47,
                "heater_state": 0,
                "heater_state_name": "off",
                "fan_raw": 0,
            },
        )

        strict_parser = FrameStreamParser(require_valid_crc_for_framing=True)
        self.assertEqual(
            strict_parser.feed(REAL_HEATER_OFF_STATUS_2026_08_09)[0]["raw"],
            REAL_HEATER_OFF_STATUS_2026_08_09,
        )

    def test_real_heater_init_response_capture_2026_08_09(self):
        """Regression vector copied from the working Node-RED Heater RX."""

        parsed = parse_frame(REAL_HEATER_INIT_RESPONSE_2026_08_09)

        self.assertEqual(len(parsed["raw"]), 12)
        self.assertEqual(parsed["device"], DEVICE_HEATER)
        self.assertEqual(parsed["payload_length"], 5)
        self.assertEqual(parsed["reserved"], 0)
        self.assertEqual(parsed["command"], CMD_INIT)
        self.assertEqual(parsed["command_name"], "init")
        self.assertEqual(parsed["payload"], bytes.fromhex("12 8A 00 3D D6"))
        self.assertEqual(parsed["crc_received"], 0xCBA6)
        self.assertEqual(parsed["crc_calculated"], 0xCBA6)
        self.assertTrue(parsed["crc_valid"])
        self.assertNotIn("status", parsed)

        strict_parser = FrameStreamParser(require_valid_crc_for_framing=True)
        self.assertEqual(
            strict_parser.feed(REAL_HEATER_INIT_RESPONSE_2026_08_09)[0]["raw"],
            REAL_HEATER_INIT_RESPONSE_2026_08_09,
        )


class TestFrameStreamParser(unittest.TestCase):
    def test_partial_frame(self):
        parser = FrameStreamParser()
        raw = build_status_request()
        self.assertEqual(parser.feed(raw[:3]), [])
        frames = parser.feed(raw[3:])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["command"], CMD_STATUS)

    def test_partial_frame_at_every_split_position(self):
        raw = build_frame(0x7E, bytes(range(20)))
        for split in range(1, len(raw)):
            with self.subTest(split=split):
                parser = FrameStreamParser()
                self.assertEqual(parser.feed(raw[:split]), [])
                self.assertEqual(parser.feed(raw[split:])[0]["raw"], raw)

    def test_multiple_frames(self):
        parser = FrameStreamParser()
        frames = parser.feed(build_init_request() + build_status_request())
        self.assertEqual([item["command"] for item in frames], [CMD_INIT, CMD_STATUS])

    def test_junk_before_frame_is_discarded(self):
        parser = FrameStreamParser()
        frames = parser.feed(b"\x00\x55noise" + build_init_request())
        self.assertEqual(len(frames), 1)
        self.assertEqual(parser.discarded_bytes, 7)

    def test_impossible_length_resynchronizes(self):
        parser = FrameStreamParser(max_frame_length=32)
        frames = parser.feed(bytes((0xAA, 0x03, 0xFF)) + build_init_request())
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["command"], CMD_INIT)
        self.assertEqual(parser.discarded_bytes, 3)

    def test_plausible_bad_length_recovers_after_timeout(self):
        parser = FrameStreamParser()
        stalled_prefix = bytes((0xAA, 0x03, 0x20))
        self.assertEqual(parser.feed(stalled_prefix + build_init_request()), [])

        frames = parser.recover_after_timeout()
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["command"], CMD_INIT)
        self.assertEqual(parser.discarded_bytes, 3)

    def test_strict_crc_mode_recovers_embedded_valid_frame(self):
        parser = FrameStreamParser(require_valid_crc_for_framing=True)
        bad_candidate_prefix = bytes.fromhex("AA 03 01 00 7E")
        frames = parser.feed(bad_candidate_prefix + build_init_request())

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["raw"], build_init_request())
        self.assertEqual(parser.rejected_candidates, 1)
        self.assertEqual(parser.discarded_bytes, 5)

    def test_empty_feed_can_drain_nothing(self):
        parser = FrameStreamParser()
        self.assertEqual(parser.feed(b""), [])

    def test_reset_clears_state(self):
        parser = FrameStreamParser()
        parser.feed(b"junk\xAA")
        self.assertTrue(parser.buffer)
        self.assertGreater(parser.discarded_bytes, 0)
        parser.reset()
        self.assertEqual(parser.buffer, bytearray())
        self.assertEqual(parser.discarded_bytes, 0)


class TestRawFrameStreamParser(unittest.TestCase):
    def test_raw_framer_does_not_interpret_protocol(self):
        parser = RawFrameStreamParser()
        raw = build_init_request()
        self.assertEqual(parser.feed(raw), [raw])

    def test_default_accepts_maximum_protocol_frame(self):
        parser = RawFrameStreamParser()
        raw = build_frame(0x7E, bytes(255))
        self.assertEqual(parser.feed(raw), [raw])


if __name__ == "__main__":
    unittest.main()
