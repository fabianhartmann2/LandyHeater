import unittest

from protocol.autoterm_protocol import (
    CMD_SETTINGS,
    CONTROL_MODE_CABIN_TEMPERATURE,
    CONTROL_MODE_POWER,
    CONTROL_MODE_ROOF_TENT_TEMPERATURE,
    build_external_temperature,
    build_frame,
    build_init_request,
    build_set_power_settings,
    build_settings_request,
    build_shutdown_request,
    build_start_for_mode,
    build_start_power,
    build_start_temperature,
    build_status_request,
    encode_external_temperature,
    parse_frame,
    protocol_mode_for_control_mode,
)


class TestAutotermFrames(unittest.TestCase):
    def test_reference_frames(self):
        vectors = (
            (build_init_request, (), "AA 03 00 00 04 9F 3D"),
            (build_status_request, (), "AA 03 00 00 0F 58 7C"),
            (build_settings_request, (), "AA 03 00 00 02 9D BD"),
            (build_shutdown_request, (), "AA 03 00 00 03 5D 7C"),
            (
                build_external_temperature,
                (20,),
                "AA 03 01 00 11 14 B2 51",
            ),
            (
                build_start_power,
                (1,),
                "AA 03 06 00 01 FF FF 04 FF 01 01 1A 1F",
            ),
            (
                build_start_temperature,
                (20,),
                "AA 03 06 00 01 FF FF 02 14 01 03 67 EE",
            ),
        )

        for builder, arguments, expected in vectors:
            with self.subTest(builder=builder.__name__):
                self.assertEqual(builder(*arguments), bytes.fromhex(expected))

    def test_power_level_range(self):
        for value in (0, 10, 1.5, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    build_start_power(value)

        build_start_power(1)
        build_start_power(9)

    def test_power_settings_preserve_all_other_fields_from_readback(self):
        payload = bytearray.fromhex("FF FF 04 10 01 09")
        raw = build_set_power_settings(payload, 4)
        self.assertEqual(
            raw, bytes.fromhex("AA 03 06 00 02 FF FF 04 10 01 04 EC DD")
        )
        self.assertEqual(bytes(payload), bytes.fromhex("FF FF 04 10 01 09"))
        # Uninterpreted settings must not be replaced by START defaults.
        other_payload = bytes.fromhex("01 00 04 14 02 00")
        frame = parse_frame(build_set_power_settings(other_payload, 9))
        self.assertEqual(frame["command"], CMD_SETTINGS)
        self.assertEqual(frame["payload"], bytes.fromhex("01 00 04 14 02 09"))

    def test_power_settings_reject_invalid_payload_and_levels(self):
        payload = bytes.fromhex("FF FF 04 10 01 09")
        for level in (0, 10, 3.5, True):
            with self.subTest(level=level), self.assertRaises(ValueError):
                build_set_power_settings(payload, level)
        for baseline in (
            None, False, 6, [0xFF, 0xFF, 4, 0x10, 1, 9],
            payload[:5], payload + b"\x00",
            bytes.fromhex("FF FF 02 10 01 09"),
            bytes.fromhex("FF FF 04 10 01 0A"),
        ):
            with self.subTest(baseline=baseline), self.assertRaises(ValueError):
                build_set_power_settings(baseline, 4)

    def test_published_planar_settings_exchange_has_valid_crc(self):
        tx, rx = (
            parse_frame(bytes.fromhex(frame))
            for frame in (
                "AA 03 06 00 02 FF FF 01 10 01 03 E2 9C",
                "AA 04 06 00 02 FF FF 01 10 01 03 38 2D",
            )
        )
        self.assertTrue(tx["crc_valid"])
        self.assertTrue(rx["crc_valid"])
        self.assertNotIn("settings", tx)
        self.assertEqual(rx["settings"], {"mode": 1, "power_level": 3})
        self.assertEqual(tx["payload"], rx["payload"])

    def test_target_temperature_range(self):
        for value in (4, 31, 20.5, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    build_start_temperature(value)

        build_start_temperature(5)
        build_start_temperature(30)

    def test_external_temperature_preserves_node_red_ceiling(self):
        self.assertEqual(encode_external_temperature(19.1), 20)
        self.assertEqual(encode_external_temperature(19.9), 20)
        self.assertEqual(encode_external_temperature(20.01), 21)
        self.assertEqual(encode_external_temperature(-1.2), 0xFF)
        self.assertEqual(encode_external_temperature(-55), 0xC9)
        self.assertEqual(
            build_external_temperature(19.1),
            bytes.fromhex("AA 03 01 00 11 14 B2 51"),
        )
        self.assertEqual(
            build_external_temperature(-1.2),
            bytes.fromhex("AA 03 01 00 11 FF FD 11"),
        )

    def test_external_temperature_rejects_values_outside_signed_byte(self):
        for value in (-129, 128, 256, True, "20", float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    build_external_temperature(value)

    def test_named_application_modes_map_centrally(self):
        self.assertEqual(protocol_mode_for_control_mode(CONTROL_MODE_POWER), 0x04)
        self.assertEqual(
            protocol_mode_for_control_mode(CONTROL_MODE_ROOF_TENT_TEMPERATURE),
            0x02,
        )
        self.assertEqual(
            protocol_mode_for_control_mode(CONTROL_MODE_CABIN_TEMPERATURE),
            0x02,
        )

        self.assertEqual(
            build_start_for_mode(CONTROL_MODE_POWER, power_level=1),
            bytes.fromhex("AA 03 06 00 01 FF FF 04 FF 01 01 1A 1F"),
        )
        self.assertEqual(
            build_start_for_mode(
                CONTROL_MODE_ROOF_TENT_TEMPERATURE,
                target_temperature=20,
            ),
            bytes.fromhex("AA 03 06 00 01 FF FF 02 14 01 03 67 EE"),
        )
        self.assertEqual(
            build_start_for_mode(
                CONTROL_MODE_CABIN_TEMPERATURE,
                target_temperature=20,
            ),
            bytes.fromhex("AA 03 06 00 01 FF FF 02 14 01 03 67 EE"),
        )

        with self.assertRaises(ValueError):
            protocol_mode_for_control_mode("21")

    def test_generic_frame_validates_header_fields(self):
        for value in (-1, 256, "1", True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    build_frame(value)

        for keyword in ("device", "reserved"):
            with self.subTest(keyword=keyword):
                with self.assertRaises(ValueError):
                    build_frame(0x01, **{keyword: True})

    def test_integer_is_not_treated_as_payload_length(self):
        with self.assertRaises(ValueError):
            build_frame(0x01, 5)

    def test_payload_limit(self):
        with self.assertRaises(ValueError):
            build_frame(0x01, bytes(256))

    def test_maximum_payload_builds_a_262_byte_frame(self):
        frame = build_frame(0x7E, bytes(255))
        self.assertEqual(len(frame), 262)
        self.assertEqual(frame[2], 255)


if __name__ == "__main__":
    unittest.main()
