"""Bounded strict JSON for the Phase-8 HTTP boundary.

The request decoder intentionally accepts less than MicroPython's ``ujson``:
input must be UTF-8 bytes, object names must be unique, and every number must
fit in a signed 32-bit integer.  Floating-point and exponent input is rejected
because every current REST request field is integral.

The response encoder is separate from that request policy.  It accepts finite
``float`` values and tuples in addition to exact JSON primitive/container
types, emits sorted object keys without optional whitespace, and enforces an
exact UTF-8 byte limit while building the result.

This module imports no hardware, socket, filesystem, regular-expression,
typing or framework module.  ``MemoryError`` is deliberately never caught.
"""

import math as _math


DEFAULT_MAX_INPUT_BYTES = 4096
DEFAULT_MAX_OUTPUT_BYTES = 8192
DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_INPUT_NODES = 128
DEFAULT_MAX_OUTPUT_NODES = 512
DEFAULT_MAX_INPUT_STRING_CHARACTERS = 256
HARD_MAX_INPUT_BYTES = 4096
HARD_MAX_OUTPUT_BYTES = 16384
HARD_MAX_DEPTH = 8
HARD_MAX_INPUT_NODES = 128
HARD_MAX_OUTPUT_NODES = 512
HARD_MAX_INPUT_STRING_CHARACTERS = 256
_STRING_CHUNK_CHARACTERS = 64

MIN_REQUEST_INTEGER = -2147483648
MAX_REQUEST_INTEGER = 2147483647

_WHITESPACE = " \t\r\n"
_HEX_DIGITS = "0123456789abcdefABCDEF"
_DIGITS = "0123456789"


class StrictJSONError(ValueError):
    """Base class for bounded JSON contract failures."""


class StrictJSONDecodeError(StrictJSONError):
    """The request body is not accepted strict JSON."""


class StrictJSONEncodeError(StrictJSONError):
    """A response value is not in the supported JSON domain."""


class StrictJSONLimitError(StrictJSONError):
    """A configured or observed JSON bound was exceeded."""


def _require_limit(name, value, hard_maximum, allow_zero=False):
    minimum = 0 if allow_zero else 1
    if type(value) is not int or value < minimum or value > hard_maximum:
        raise ValueError(
            "{} must be an integer from {} to {}".format(
                name, minimum, hard_maximum
            )
        )
    return value


class _StrictParser:
    __slots__ = (
        "text",
        "length",
        "index",
        "max_depth",
        "max_nodes",
        "nodes",
        "max_string_characters",
    )

    def __init__(
        self,
        text,
        max_depth,
        max_nodes=DEFAULT_MAX_INPUT_NODES,
        max_string_characters=DEFAULT_MAX_INPUT_STRING_CHARACTERS,
    ):
        self.text = text
        self.length = len(text)
        self.index = 0
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.nodes = 0
        self.max_string_characters = max_string_characters

    def _fail(self, message):
        raise StrictJSONDecodeError(
            "{} at character {}".format(message, self.index)
        )

    def _skip_whitespace(self):
        text = self.text
        length = self.length
        index = self.index
        while index < length and text[index] in _WHITESPACE:
            index += 1
        self.index = index

    def parse(self):
        self._skip_whitespace()
        value = self._parse_value(0)
        self._skip_whitespace()
        if self.index != self.length:
            self._fail("trailing JSON content")
        return value

    def _parse_value(self, depth):
        if self.index >= self.length:
            self._fail("JSON value is missing")
        self.nodes += 1
        if self.nodes > self.max_nodes:
            raise StrictJSONLimitError(
                "JSON input exceeds {} nodes".format(self.max_nodes)
            )
        character = self.text[self.index]
        if character == '"':
            return self._parse_string()
        if character == "{":
            return self._parse_object(depth + 1)
        if character == "[":
            return self._parse_array(depth + 1)
        if character == "t":
            return self._parse_literal("true", True)
        if character == "f":
            return self._parse_literal("false", False)
        if character == "n":
            return self._parse_literal("null", None)
        if character == "-" or character in _DIGITS:
            return self._parse_integer()
        self._fail("JSON value is invalid")

    def _parse_literal(self, spelling, value):
        end = self.index + len(spelling)
        if self.text[self.index:end] != spelling:
            self._fail("JSON literal is invalid")
        self.index = end
        return value

    def _parse_hex_quad(self):
        end = self.index + 4
        if end > self.length:
            self._fail("Unicode escape is incomplete")
        value = 0
        while self.index < end:
            character = self.text[self.index]
            if character not in _HEX_DIGITS:
                self._fail("Unicode escape is invalid")
            if "0" <= character <= "9":
                digit = ord(character) - ord("0")
            elif "a" <= character <= "f":
                digit = ord(character) - ord("a") + 10
            else:
                digit = ord(character) - ord("A") + 10
            value = value * 16 + digit
            self.index += 1
        return value

    def _parse_string(self):
        self.index += 1
        pieces = []
        decoded_characters = 0
        while self.index < self.length:
            segment_start = self.index
            while self.index < self.length:
                character = self.text[self.index]
                codepoint = ord(character)
                if (
                    character == '"'
                    or character == "\\"
                    or codepoint < 0x20
                    or 0xD800 <= codepoint <= 0xDFFF
                ):
                    break
                self.index += 1
            if self.index > segment_start:
                decoded_characters += self.index - segment_start
                if decoded_characters > self.max_string_characters:
                    raise StrictJSONLimitError(
                        "JSON string exceeds {} characters".format(
                            self.max_string_characters
                        )
                    )
                pieces.append(self.text[segment_start:self.index])
            if self.index >= self.length:
                break

            character = self.text[self.index]
            codepoint = ord(character)
            if character == '"':
                self.index += 1
                return "".join(pieces)
            if codepoint < 0x20:
                self._fail("unescaped control character in JSON string")
            if 0xD800 <= codepoint <= 0xDFFF:
                self._fail("unpaired surrogate in JSON string")

            self.index += 1
            if self.index >= self.length:
                self._fail("JSON string escape is incomplete")
            escape = self.text[self.index]
            self.index += 1
            if escape == '"' or escape == "\\" or escape == "/":
                decoded_characters += 1
                pieces.append(escape)
            elif escape == "b":
                decoded_characters += 1
                pieces.append("\b")
            elif escape == "f":
                decoded_characters += 1
                pieces.append("\f")
            elif escape == "n":
                decoded_characters += 1
                pieces.append("\n")
            elif escape == "r":
                decoded_characters += 1
                pieces.append("\r")
            elif escape == "t":
                decoded_characters += 1
                pieces.append("\t")
            elif escape != "u":
                self._fail("JSON string escape is invalid")
            else:
                decoded_characters += 1
                first = self._parse_hex_quad()
                if 0xD800 <= first <= 0xDBFF:
                    if (
                        self.index + 2 > self.length
                        or self.text[self.index] != "\\"
                        or self.text[self.index + 1] != "u"
                    ):
                        self._fail("high surrogate has no low surrogate")
                    self.index += 2
                    second = self._parse_hex_quad()
                    if not 0xDC00 <= second <= 0xDFFF:
                        self._fail("high surrogate has an invalid pair")
                    scalar = (
                        0x10000
                        + ((first - 0xD800) << 10)
                        + (second - 0xDC00)
                    )
                    pieces.append(chr(scalar))
                elif 0xDC00 <= first <= 0xDFFF:
                    self._fail("low surrogate has no high surrogate")
                else:
                    pieces.append(chr(first))
            if decoded_characters > self.max_string_characters:
                raise StrictJSONLimitError(
                    "JSON string exceeds {} characters".format(
                        self.max_string_characters
                    )
                )
        self._fail("JSON string is unterminated")

    def _parse_integer(self):
        negative = False
        if self.text[self.index] == "-":
            negative = True
            self.index += 1
            if self.index >= self.length:
                self._fail("JSON integer is incomplete")

        if self.text[self.index] == "0":
            self.index += 1
            if (
                self.index < self.length
                and self.text[self.index] in _DIGITS
            ):
                self._fail("JSON integer has a leading zero")
            value = 0
        else:
            if self.text[self.index] not in "123456789":
                self._fail("JSON integer is invalid")
            value = 0
            limit = 2147483648 if negative else MAX_REQUEST_INTEGER
            while (
                self.index < self.length
                and self.text[self.index] in _DIGITS
            ):
                digit = ord(self.text[self.index]) - ord("0")
                if value > (limit - digit) // 10:
                    self._fail("JSON integer exceeds signed 32-bit range")
                value = value * 10 + digit
                self.index += 1

        if (
            self.index < self.length
            and self.text[self.index] in ".eE"
        ):
            self._fail("floating-point JSON numbers are not accepted")
        return -value if negative else value

    def _require_depth(self, depth):
        if depth > self.max_depth:
            raise StrictJSONLimitError(
                "JSON nesting exceeds depth {}".format(self.max_depth)
            )

    def _parse_array(self, depth):
        self._require_depth(depth)
        self.index += 1
        result = []
        self._skip_whitespace()
        if self.index < self.length and self.text[self.index] == "]":
            self.index += 1
            return result
        while True:
            result.append(self._parse_value(depth))
            self._skip_whitespace()
            if self.index >= self.length:
                self._fail("JSON array is unterminated")
            delimiter = self.text[self.index]
            self.index += 1
            if delimiter == "]":
                return result
            if delimiter != ",":
                self._fail("JSON array delimiter is invalid")
            self._skip_whitespace()
            if self.index < self.length and self.text[self.index] == "]":
                self._fail("JSON array has a trailing comma")

    def _parse_object(self, depth):
        self._require_depth(depth)
        self.index += 1
        result = {}
        self._skip_whitespace()
        if self.index < self.length and self.text[self.index] == "}":
            self.index += 1
            return result
        while True:
            if self.index >= self.length or self.text[self.index] != '"':
                self._fail("JSON object name must be a string")
            key = self._parse_string()
            if key in result:
                self._fail("duplicate JSON object name")
            self._skip_whitespace()
            if self.index >= self.length or self.text[self.index] != ":":
                self._fail("JSON object colon is missing")
            self.index += 1
            self._skip_whitespace()
            result[key] = self._parse_value(depth)
            self._skip_whitespace()
            if self.index >= self.length:
                self._fail("JSON object is unterminated")
            delimiter = self.text[self.index]
            self.index += 1
            if delimiter == "}":
                return result
            if delimiter != ",":
                self._fail("JSON object delimiter is invalid")
            self._skip_whitespace()
            if self.index < self.length and self.text[self.index] == "}":
                self._fail("JSON object has a trailing comma")


def decode_json_bytes(
    data,
    max_bytes=DEFAULT_MAX_INPUT_BYTES,
    max_depth=DEFAULT_MAX_DEPTH,
    max_nodes=DEFAULT_MAX_INPUT_NODES,
    max_string_characters=DEFAULT_MAX_INPUT_STRING_CHARACTERS,
):
    """Decode one strict, bounded HTTP request JSON value from exact bytes."""

    max_bytes = _require_limit(
        "max_bytes", max_bytes, HARD_MAX_INPUT_BYTES
    )
    max_depth = _require_limit(
        "max_depth",
        max_depth,
        HARD_MAX_DEPTH,
        allow_zero=True,
    )
    max_nodes = _require_limit(
        "max_nodes", max_nodes, HARD_MAX_INPUT_NODES
    )
    max_string_characters = _require_limit(
        "max_string_characters",
        max_string_characters,
        HARD_MAX_INPUT_STRING_CHARACTERS,
    )
    if type(data) is not bytes:
        raise TypeError("strict JSON input must be bytes")
    if len(data) > max_bytes:
        raise StrictJSONLimitError(
            "JSON input exceeds {} bytes".format(max_bytes)
        )
    if data.startswith(b"\xef\xbb\xbf"):
        raise StrictJSONDecodeError("UTF-8 BOM is not accepted")

    invalid_utf8 = False
    try:
        text = data.decode("utf-8")
    except (UnicodeError, ValueError):
        invalid_utf8 = True
        text = None
    if invalid_utf8:
        # Raised outside the decoder exception handler so no body bytes are
        # retained as an implicit exception context.
        raise StrictJSONDecodeError("JSON input is not valid UTF-8")
    return _StrictParser(
        text, max_depth, max_nodes, max_string_characters
    ).parse()


class _BoundedWriter:
    __slots__ = ("buffer", "max_bytes", "max_nodes", "nodes")

    def __init__(self, max_bytes, max_nodes=DEFAULT_MAX_OUTPUT_NODES):
        self.buffer = bytearray()
        self.max_bytes = max_bytes
        self.max_nodes = max_nodes
        self.nodes = 0

    @property
    def remaining_bytes(self):
        return self.max_bytes - len(self.buffer)

    @property
    def remaining_nodes(self):
        return self.max_nodes - self.nodes

    def consume_node(self):
        self.nodes += 1
        if self.nodes > self.max_nodes:
            raise StrictJSONLimitError(
                "JSON output exceeds {} nodes".format(self.max_nodes)
            )

    def append(self, value):
        if type(value) is not bytes:
            raise StrictJSONEncodeError("encoder produced non-byte output")
        if len(self.buffer) + len(value) > self.max_bytes:
            raise StrictJSONLimitError(
                "JSON output exceeds {} bytes".format(self.max_bytes)
            )
        self.buffer.extend(value)

    def append_ascii(self, value):
        self.append(value.encode("ascii"))

    def append_utf8(self, value):
        self.append(value.encode("utf-8"))


def _encode_string(value, writer):
    # Every Unicode codepoint requires at least one output byte.  This cheap
    # lower-bound check rejects huge internal strings before slicing or UTF-8
    # encoding can allocate a correspondingly huge temporary object.
    if len(value) + 2 > writer.remaining_bytes:
        raise StrictJSONLimitError(
            "JSON output exceeds {} bytes".format(writer.max_bytes)
        )
    writer.append(b'"')
    index = 0
    while index < len(value):
        segment_start = index
        while index < len(value):
            character = value[index]
            codepoint = ord(character)
            if (
                character == '"'
                or character == "\\"
                or codepoint < 0x20
                or 0xD800 <= codepoint <= 0xDFFF
            ):
                break
            index += 1
            if index - segment_start >= _STRING_CHUNK_CHARACTERS:
                break
        if index > segment_start:
            writer.append_utf8(value[segment_start:index])
        if index >= len(value):
            break

        character = value[index]
        codepoint = ord(character)
        if character == '"':
            writer.append(b'\\"')
        elif character == "\\":
            writer.append(b"\\\\")
        elif character == "\b":
            writer.append(b"\\b")
        elif character == "\f":
            writer.append(b"\\f")
        elif character == "\n":
            writer.append(b"\\n")
        elif character == "\r":
            writer.append(b"\\r")
        elif character == "\t":
            writer.append(b"\\t")
        elif codepoint < 0x20:
            writer.append_ascii("\\u{:04x}".format(codepoint))
        elif 0xD800 <= codepoint <= 0xDBFF:
            if index + 1 >= len(value):
                raise StrictJSONEncodeError(
                    "response string contains an unpaired high surrogate"
                )
            second = ord(value[index + 1])
            if not 0xDC00 <= second <= 0xDFFF:
                raise StrictJSONEncodeError(
                    "response string contains an invalid surrogate pair"
                )
            scalar = (
                0x10000
                + ((codepoint - 0xD800) << 10)
                + (second - 0xDC00)
            )
            writer.append_utf8(chr(scalar))
            index += 1
        elif 0xDC00 <= codepoint <= 0xDFFF:
            raise StrictJSONEncodeError(
                "response string contains an unpaired low surrogate"
            )
        else:
            writer.append_utf8(character)
        index += 1
    writer.append(b'"')


def _encode_value(value, writer, depth, max_depth):
    writer.consume_node()
    if value is None:
        writer.append(b"null")
        return
    if type(value) is bool:
        writer.append(b"true" if value else b"false")
        return
    if type(value) is int:
        writer.append_ascii(str(value))
        return
    if type(value) is float:
        if not _math.isfinite(value):
            raise StrictJSONEncodeError(
                "response float must be finite"
            )
        rendered = repr(value)
        writer.append_ascii(rendered)
        return
    if type(value) is str:
        _encode_string(value, writer)
        return

    if type(value) in (list, tuple):
        next_depth = depth + 1
        if next_depth > max_depth:
            raise StrictJSONLimitError(
                "JSON nesting exceeds depth {}".format(max_depth)
            )
        if len(value) > writer.remaining_nodes:
            raise StrictJSONLimitError(
                "JSON output exceeds {} nodes".format(writer.max_nodes)
            )
        writer.append(b"[")
        for index, item in enumerate(value):
            if index:
                writer.append(b",")
            _encode_value(item, writer, next_depth, max_depth)
        writer.append(b"]")
        return

    if type(value) is dict:
        next_depth = depth + 1
        if next_depth > max_depth:
            raise StrictJSONLimitError(
                "JSON nesting exceeds depth {}".format(max_depth)
            )
        if len(value) * 2 > writer.remaining_nodes:
            raise StrictJSONLimitError(
                "JSON output exceeds {} nodes".format(writer.max_nodes)
            )
        keys = list(value.keys())
        for key in keys:
            if type(key) is not str:
                raise StrictJSONEncodeError(
                    "response object names must be strings"
                )
        keys.sort()
        writer.append(b"{")
        for index, key in enumerate(keys):
            if index:
                writer.append(b",")
            writer.consume_node()
            _encode_string(key, writer)
            writer.append(b":")
            _encode_value(value[key], writer, next_depth, max_depth)
        writer.append(b"}")
        return

    raise StrictJSONEncodeError(
        "response contains a non-JSON value"
    )


def encode_json_bytes(
    value,
    max_bytes=DEFAULT_MAX_OUTPUT_BYTES,
    max_depth=DEFAULT_MAX_DEPTH,
    max_nodes=DEFAULT_MAX_OUTPUT_NODES,
):
    """Encode one deterministic, bounded response value as UTF-8 bytes."""

    max_bytes = _require_limit(
        "max_bytes",
        max_bytes,
        HARD_MAX_OUTPUT_BYTES,
    )
    max_depth = _require_limit(
        "max_depth",
        max_depth,
        HARD_MAX_DEPTH,
        allow_zero=True,
    )
    max_nodes = _require_limit(
        "max_nodes", max_nodes, HARD_MAX_OUTPUT_NODES
    )
    writer = _BoundedWriter(max_bytes, max_nodes)
    _encode_value(value, writer, 0, max_depth)
    return bytes(writer.buffer)


# Familiar names for the REST parser/encoder while keeping the byte contract
# explicit in the primary function names.
loads = decode_json_bytes
dumps = encode_json_bytes
