import json
import math
import unittest
from unittest import mock

import services.strict_json as strict_json
from services.strict_json import (
    DEFAULT_MAX_INPUT_BYTES,
    StrictJSONDecodeError,
    StrictJSONEncodeError,
    StrictJSONLimitError,
    decode_json_bytes,
    encode_json_bytes,
)


class TestStrictJSONDecoder(unittest.TestCase):
    def test_decodes_exact_json_primitives_and_containers(self):
        value = decode_json_bytes(
            b' {"text":"ok","items":[null,true,false,-12,0,2147483647]} '\
        )
        self.assertEqual(
            value,
            {
                "text": "ok",
                "items": [None, True, False, -12, 0, 2147483647],
            },
        )
        self.assertEqual(decode_json_bytes(b"-2147483648"), -2147483648)

    def test_requires_exact_bytes_and_valid_bounds(self):
        for invalid in ('{}', bytearray(b'{}'), memoryview(b'{}'), None):
            with self.subTest(invalid=type(invalid).__name__):
                with self.assertRaises(TypeError):
                    decode_json_bytes(invalid)
        for invalid in (None, False, 0, -1, 4097, "4096"):
            with self.subTest(max_bytes=invalid):
                with self.assertRaises(ValueError):
                    decode_json_bytes(b"null", max_bytes=invalid)
        for invalid in (None, False, -1, 9, "8"):
            with self.subTest(max_depth=invalid):
                with self.assertRaises(ValueError):
                    decode_json_bytes(b"null", max_depth=invalid)

    def test_enforces_exact_input_byte_limit(self):
        accepted = b"0" + b" " * (DEFAULT_MAX_INPUT_BYTES - 1)
        self.assertEqual(decode_json_bytes(accepted), 0)
        with self.assertRaises(StrictJSONLimitError):
            decode_json_bytes(accepted + b" ")
        with self.assertRaises(StrictJSONLimitError):
            decode_json_bytes(b"null", max_bytes=3)

    def test_depth_counts_containers_and_has_a_hard_bound(self):
        accepted = (b"[" * 8) + b"0" + (b"]" * 8)
        rejected = (b"[" * 9) + b"0" + (b"]" * 9)
        self.assertEqual(decode_json_bytes(accepted), [[[[[[[[0]]]]]]]])
        with self.assertRaises(StrictJSONLimitError):
            decode_json_bytes(rejected)
        self.assertEqual(decode_json_bytes(b"0", max_depth=0), 0)
        with self.assertRaises(StrictJSONLimitError):
            decode_json_bytes(b"[]", max_depth=0)

    def test_aggregate_node_bound_rejects_many_tiny_containers(self):
        accepted = b"[" + b",".join([b"{}"] * 127) + b"]"
        rejected = b"[" + b",".join([b"{}"] * 128) + b"]"
        self.assertEqual(len(decode_json_bytes(accepted)), 127)
        with self.assertRaisesRegex(StrictJSONLimitError, "nodes"):
            decode_json_bytes(rejected)
        with self.assertRaises(StrictJSONLimitError):
            decode_json_bytes(b"[0,1]", max_nodes=2)

    def test_input_string_character_bound_counts_plain_and_escaped_values(self):
        self.assertEqual(
            decode_json_bytes(b'"' + (b"x" * 256) + b'"'),
            "x" * 256,
        )
        with self.assertRaisesRegex(StrictJSONLimitError, "string"):
            decode_json_bytes(b'"' + (b"x" * 257) + b'"')

        self.assertEqual(
            decode_json_bytes(b'"' + (b"\\n" * 256) + b'"'),
            "\n" * 256,
        )
        with self.assertRaisesRegex(StrictJSONLimitError, "string"):
            decode_json_bytes(b'"' + (b"\\n" * 257) + b'"')

        for invalid in (None, False, 0, -1, 257, "256"):
            with self.subTest(max_string_characters=invalid):
                with self.assertRaises(ValueError):
                    decode_json_bytes(
                        b'"ok"', max_string_characters=invalid
                    )

    def test_rejects_duplicate_names_after_escape_decoding(self):
        cases = (
            b'{"a":1,"a":2}',
            b'{"a":1,"\\u0061":2}',
            b'{"\\ud83d\\ude00":1,"\xf0\x9f\x98\x80":2}',
        )
        for body in cases:
            with self.subTest(body=body):
                with self.assertRaisesRegex(
                    StrictJSONDecodeError, "duplicate"
                ):
                    decode_json_bytes(body)

    def test_rejects_invalid_utf8_bom_and_trailing_content_without_echo(self):
        cases = (
            (b'"secret\xff"', "valid UTF-8"),
            (b"\xef\xbb\xbf{}", "BOM"),
            (b"{} []", "trailing"),
            (b"\xffsecret", "valid UTF-8"),
        )
        for body, message in cases:
            with self.subTest(body=body):
                with self.assertRaisesRegex(
                    StrictJSONDecodeError, message
                ) as caught:
                    decode_json_bytes(body)
                self.assertNotIn("secret", str(caught.exception))

    def test_accepts_json_string_escapes_and_a_valid_surrogate_pair(self):
        body = b'"quote=\\\" slash=\\/ controls=\\b\\f\\n\\r\\t \\u20ac \\ud83d\\ude00"'
        self.assertEqual(
            decode_json_bytes(body),
            'quote=" slash=/ controls=\b\f\n\r\t \u20ac \U0001f600',
        )

    def test_rejects_unpaired_or_malformed_surrogates(self):
        cases = (
            b'"\\ud800"',
            b'"\\ud800x"',
            b'"\\ud800\\u0041"',
            b'"\\udc00"',
            b'"\\u12xz"',
        )
        for body in cases:
            with self.subTest(body=body):
                with self.assertRaises(StrictJSONDecodeError):
                    decode_json_bytes(body)

    def test_rejects_floats_exponents_and_non_json_numbers(self):
        cases = (
            b"1.0",
            b"1e3",
            b"1E-3",
            b"-0.0",
            b"+1",
            b"01",
            b"-01",
            b"NaN",
            b"Infinity",
        )
        for body in cases:
            with self.subTest(body=body):
                with self.assertRaises(StrictJSONDecodeError):
                    decode_json_bytes(body)

    def test_rejects_integers_outside_signed_32_bit_without_bigint_parsing(self):
        cases = (
            b"2147483648",
            b"-2147483649",
            b"9999999999999999999999999999999999999999",
        )
        for body in cases:
            with self.subTest(body=body):
                with self.assertRaisesRegex(
                    StrictJSONDecodeError, "32-bit"
                ):
                    decode_json_bytes(body)

    def test_rejects_malformed_containers_strings_and_literals(self):
        cases = (
            b"",
            b" ",
            b"[1,]",
            b'{"a":1,}',
            b"[1 2]",
            b'{"a" 1}',
            b'{a:1}',
            b'"unterminated',
            b'"bad\\x"',
            b'"raw\x01control"',
            b"tru",
        )
        for body in cases:
            with self.subTest(body=body):
                with self.assertRaises(StrictJSONDecodeError):
                    decode_json_bytes(body)

    def test_decoder_does_not_swallow_memory_error(self):
        with mock.patch.object(strict_json, "_StrictParser") as parser:
            parser.return_value.parse.side_effect = MemoryError("decode oom")
            with self.assertRaises(MemoryError) as caught:
                decode_json_bytes(b"null")
        self.assertEqual(str(caught.exception), "decode oom")


class TestStrictJSONEncoder(unittest.TestCase):
    def test_emits_sorted_compact_deterministic_utf8(self):
        value = {
            "z": (True, None, 4),
            "a": "Gr\u00fcezi \U0001f600\n",
        }
        expected = (
            '{"a":"Gr\u00fcezi \U0001f600\\n","z":[true,null,4]}'
        ).encode("utf-8")
        self.assertEqual(encode_json_bytes(value), expected)
        self.assertEqual(encode_json_bytes(value), expected)
        self.assertEqual(json.loads(expected.decode("utf-8")), {
            "a": "Gr\u00fcezi \U0001f600\n",
            "z": [True, None, 4],
        })

    def test_escapes_controls_quotes_and_backslashes(self):
        value = '"\\\b\f\n\r\t\x00\x1f/'
        self.assertEqual(
            encode_json_bytes(value),
            b'"\\\"\\\\\\b\\f\\n\\r\\t\\u0000\\u001f/"',
        )

    def test_accepts_response_integers_tuples_and_finite_floats(self):
        value = {
            "large": 4294967295,
            "values": (1.5, -0.0, 1e20),
        }
        encoded = encode_json_bytes(value)
        decoded = json.loads(encoded.decode("ascii"))
        self.assertEqual(decoded["large"], 4294967295)
        self.assertEqual(decoded["values"][0], 1.5)
        self.assertEqual(math.copysign(1, decoded["values"][1]), -1)
        self.assertEqual(decoded["values"][2], 1e20)

    def test_rejects_non_finite_floats_and_non_json_types(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    StrictJSONEncodeError, "finite"
                ):
                    encode_json_bytes(value)
        for value in ({1: "bad"}, set((1,)), object(), b"bytes"):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(StrictJSONEncodeError):
                    encode_json_bytes(value)

    def test_supports_a_python_surrogate_pair_but_rejects_unpaired_values(self):
        pair = "\ud83d\ude00"
        self.assertEqual(
            encode_json_bytes(pair),
            '"\U0001f600"'.encode("utf-8"),
        )
        for value in ("\ud800", "\ud800A", "\udc00"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(StrictJSONEncodeError):
                    encode_json_bytes(value)

    def test_enforces_output_byte_limit_before_returning_partial_data(self):
        self.assertEqual(encode_json_bytes("abc", max_bytes=5), b'"abc"')
        with self.assertRaises(StrictJSONLimitError):
            encode_json_bytes("abc", max_bytes=4)
        with self.assertRaises(StrictJSONLimitError):
            encode_json_bytes("\u20ac", max_bytes=4)

    def test_huge_string_is_rejected_before_utf8_temporary_allocation(self):
        with mock.patch.object(
            strict_json._BoundedWriter,
            "append_utf8",
            side_effect=AssertionError("UTF-8 encoding was reached"),
        ):
            with self.assertRaises(StrictJSONLimitError):
                encode_json_bytes("x" * 20000)

    def test_encoder_node_bound_is_checked_before_key_list_allocation(self):
        with self.assertRaisesRegex(StrictJSONLimitError, "nodes"):
            encode_json_bytes({"a": 1, "b": 2}, max_nodes=4)
        self.assertEqual(
            encode_json_bytes({"a": 1, "b": 2}, max_nodes=5),
            b'{"a":1,"b":2}',
        )

    def test_encoder_depth_and_configuration_bounds_are_strict(self):
        accepted = [[[[[[[[0]]]]]]]]
        rejected = [accepted]
        self.assertEqual(json.loads(encode_json_bytes(accepted)), accepted)
        with self.assertRaises(StrictJSONLimitError):
            encode_json_bytes(rejected)
        self.assertEqual(encode_json_bytes(0, max_depth=0), b"0")
        with self.assertRaises(StrictJSONLimitError):
            encode_json_bytes([], max_depth=0)
        for invalid in (None, False, 0, -1, 16385, "8"):
            with self.subTest(max_bytes=invalid):
                with self.assertRaises(ValueError):
                    encode_json_bytes(None, max_bytes=invalid)
        for invalid in (None, False, -1, 9, "8"):
            with self.subTest(max_depth=invalid):
                with self.assertRaises(ValueError):
                    encode_json_bytes(None, max_depth=invalid)

    def test_cyclic_containers_fail_at_the_depth_bound(self):
        value = []
        value.append(value)
        with self.assertRaises(StrictJSONLimitError):
            encode_json_bytes(value)

    def test_encoder_does_not_swallow_memory_error(self):
        with mock.patch.object(
            strict_json, "_encode_value", side_effect=MemoryError("encode oom")
        ):
            with self.assertRaises(MemoryError) as caught:
                encode_json_bytes(None)
        self.assertEqual(str(caught.exception), "encode oom")


if __name__ == "__main__":
    unittest.main()
