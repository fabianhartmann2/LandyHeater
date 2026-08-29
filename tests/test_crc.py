import unittest

from protocol.crc16 import append_crc, crc16, crc_bytes


class TestAutotermCRC(unittest.TestCase):
    VECTORS = (
        ("AA 03 00 00 04", 0x9F3D),
        ("AA 03 00 00 0F", 0x587C),
        ("AA 03 00 00 03", 0x5D7C),
        ("AA 03 01 00 11 14", 0xB251),
        ("AA 03 06 00 01 FF FF 04 FF 01 01", 0x1A1F),
        ("AA 03 06 00 01 FF FF 02 14 01 03", 0x67EE),
    )

    def test_reference_vectors(self):
        for hex_data, expected in self.VECTORS:
            with self.subTest(hex_data=hex_data):
                self.assertEqual(crc16(bytes.fromhex(hex_data)), expected)

    def test_canonical_algorithm_vector(self):
        self.assertEqual(crc16(b"123456789"), 0x4B37)

    def test_crc_bytes_are_high_byte_first(self):
        data = bytes.fromhex("AA 03 00 00 04")
        self.assertEqual(crc_bytes(data), bytes.fromhex("9F 3D"))

    def test_crc_bytes_preserve_leading_zero(self):
        # The Node-RED string conversion could lose this leading zero.
        data = bytes.fromhex("AA 03 00 00 D1")
        self.assertEqual(crc16(data), 0x00FC)
        self.assertEqual(crc_bytes(data), bytes.fromhex("00 FC"))

    def test_append_crc_does_not_mutate_bytearray(self):
        data = bytearray.fromhex("AA 03 00 00 04")
        result = append_crc(data)
        self.assertEqual(result, bytes.fromhex("AA 03 00 00 04 9F 3D"))
        self.assertEqual(data, bytearray.fromhex("AA 03 00 00 04"))

    def test_empty_input_has_initial_crc(self):
        self.assertEqual(crc16(b""), 0xFFFF)

    def test_invalid_input_byte_is_rejected(self):
        with self.assertRaises(ValueError):
            crc16((0xAA, 0x100))


if __name__ == "__main__":
    unittest.main()
