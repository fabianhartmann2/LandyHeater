"""Bounded A/B JSON record storage for persistent application state.

The adapter performs no I/O at import time or construction time.  Each
logical store owns two generation slots and one temporary publication file.
The currently newest valid slot is never modified while a new generation is
being staged.  A truncated write can therefore invalidate only the inactive
slot; the previous generation remains available for fail-closed recovery.

This module knows nothing about heater state or configuration semantics.  It
only validates the storage envelope, canonical JSON encoding, length, CRC32
and repeated commit footer.  :mod:`services.config_manager` owns the schema.
"""

import json as _json
import os as _os
import gc as _gc
import hashlib as _hashlib


STORAGE_FORMAT_VERSION = 1
DEFAULT_MAX_RECORD_BYTES = 24 * 1024
MAX_RECORD_BYTES = 32 * 1024
MAX_GENERATION = 0x7FFFFFFF

_MAGIC = "LANDY_CONFIG_SLOT_V1"
_FOOTER_MAGIC = "LANDY_CONFIG_END_V1"
_SLOT_NAMES = ("a", "b")
_MAX_JSON_DEPTH = 16
_RECORD_CHUNK_BYTES = 256
_HEX_DIGITS = b"0123456789abcdef"
_JSON_NULL = b"null"
_JSON_TRUE = b"true"
_JSON_FALSE = b"false"
_ESCAPE_SUFFIX = {
    0x22: 0x22,  # "
    0x5C: 0x5C,  # \\
    0x08: 0x62,  # b
    0x0C: 0x66,  # f
    0x0A: 0x6E,  # n
    0x0D: 0x72,  # r
    0x09: 0x74,  # t
}


class _SegmentedRecord:
    """Exact-length record storage without one payload-sized heap run."""

    __slots__ = ("_chunks", "_length")

    def __init__(self, length):
        self._length = length
        self._chunks = []
        remaining = length
        while remaining:
            size = min(remaining, _RECORD_CHUNK_BYTES)
            self._chunks.append(bytearray(size))
            remaining -= size

    def __len__(self):
        return self._length

    def __setitem__(self, index, value):
        if type(index) is not int or index < 0 or index >= self._length:
            raise IndexError("segmented record index is invalid")
        chunk_index = index // _RECORD_CHUNK_BYTES
        chunk_offset = index % _RECORD_CHUNK_BYTES
        self._chunks[chunk_index][chunk_offset] = value

    def __getitem__(self, index):
        if type(index) is not int or index < 0 or index >= self._length:
            raise IndexError("segmented record index is invalid")
        chunk_index = index // _RECORD_CHUNK_BYTES
        chunk_offset = index % _RECORD_CHUNK_BYTES
        return self._chunks[chunk_index][chunk_offset]

    def iter_chunks(self):
        for chunk in self._chunks:
            yield chunk

    def iter_bytes(self, start, end):
        position = start
        while position < end:
            chunk_index = position // _RECORD_CHUNK_BYTES
            chunk_offset = position % _RECORD_CHUNK_BYTES
            chunk = self._chunks[chunk_index]
            stop = min(len(chunk), chunk_offset + end - position)
            while chunk_offset < stop:
                yield chunk[chunk_offset]
                chunk_offset += 1
                position += 1

    def iter_views(self, start, end):
        position = start
        while position < end:
            chunk_index = position // _RECORD_CHUNK_BYTES
            chunk_offset = position % _RECORD_CHUNK_BYTES
            chunk = self._chunks[chunk_index]
            count = min(len(chunk) - chunk_offset, end - position)
            yield memoryview(chunk)[chunk_offset:chunk_offset + count]
            position += count

    def find_byte(self, value, start=0):
        position = start
        while position < self._length:
            if self[position] == value:
                return position
            position += 1
        return -1

    def slice_bytes(self, start, end):
        result = bytearray(end - start)
        offset = 0
        for byte in self.iter_bytes(start, end):
            result[offset] = byte
            offset += 1
        return bytes(result)

    def to_bytes(self):
        return b"".join(bytes(chunk) for chunk in self._chunks)


class _CanonicalComparison:
    """Writer target that compares canonical bytes without storing them."""

    __slots__ = ("_record", "_start", "_length")

    def __init__(self, record, start, length):
        self._record = record
        self._start = start
        self._length = length

    def __setitem__(self, index, value):
        if type(index) is not int or index < 0 or index >= self._length:
            raise ConfigStoreFormatError("record JSON is not canonical")
        if self._record[self._start + index] != value:
            raise ConfigStoreFormatError("record JSON is not canonical")


class ConfigStoreError(RuntimeError):
    pass


class ConfigStoreFormatError(ConfigStoreError):
    pass


class ConfigStoreConflictError(ConfigStoreError):
    pass


class ConfigStoreDurabilityError(ConfigStoreError):
    pass


def _sha256_token(parts):
    hasher = _hashlib.sha256()
    for part in parts:
        hasher.update(part)
    digest = hasher.digest()
    encoded = bytearray(len(digest) * 2)
    offset = 0
    for byte in digest:
        encoded[offset] = _HEX_DIGITS[byte >> 4]
        encoded[offset + 1] = _HEX_DIGITS[byte & 0x0F]
        offset += 2
    return "sha256:" + bytes(encoded).decode("ascii")


class _CanonicalJSONParser:
    """Parse canonical UTF-8 JSON directly from segmented storage."""

    __slots__ = ("_record", "_position", "_end")

    def __init__(self, record, start, end):
        self._record = record
        self._position = start
        self._end = end

    def _fail(self):
        raise ConfigStoreFormatError("record JSON is not canonical")

    def _peek(self):
        if self._position >= self._end:
            return -1
        return self._record[self._position]

    def _take(self):
        value = self._peek()
        if value < 0:
            self._fail()
        self._position += 1
        return value

    def _literal(self, suffix, value):
        for expected in suffix:
            if self._take() != expected:
                self._fail()
        return value

    def _string(self):
        if self._take() != 0x22:
            self._fail()
        result = bytearray()
        while True:
            byte = self._take()
            if byte == 0x22:
                break
            if byte == 0x5C:
                escaped = self._take()
                if escaped == 0x22 or escaped == 0x5C:
                    result.append(escaped)
                elif escaped == 0x62:
                    result.append(0x08)
                elif escaped == 0x66:
                    result.append(0x0C)
                elif escaped == 0x6E:
                    result.append(0x0A)
                elif escaped == 0x72:
                    result.append(0x0D)
                elif escaped == 0x74:
                    result.append(0x09)
                elif escaped == 0x75:
                    codepoint = 0
                    for _ in range(4):
                        digit = self._take()
                        if 0x30 <= digit <= 0x39:
                            value = digit - 0x30
                        elif 0x61 <= digit <= 0x66:
                            value = digit - 0x61 + 10
                        else:
                            self._fail()
                        codepoint = (codepoint << 4) | value
                    if (
                        codepoint >= 0x20
                        or codepoint in _ESCAPE_SUFFIX
                    ):
                        self._fail()
                    result.append(codepoint)
                else:
                    self._fail()
            else:
                if byte < 0x20:
                    self._fail()
                result.append(byte)
        try:
            return bytes(result).decode("utf-8")
        except (UnicodeError, ValueError) as exc:
            raise ConfigStoreFormatError(
                "record JSON is not canonical"
            ) from exc

    def _integer(self):
        negative = False
        if self._peek() == 0x2D:
            negative = True
            self._position += 1
        first = self._take()
        if first == 0x30:
            if negative or 0x30 <= self._peek() <= 0x39:
                self._fail()
            return 0
        if first < 0x31 or first > 0x39:
            self._fail()
        value = first - 0x30
        while 0x30 <= self._peek() <= 0x39:
            value = (value * 10) + (self._take() - 0x30)
        return -value if negative else value

    def _value(self, depth):
        if depth > _MAX_JSON_DEPTH:
            raise ConfigStoreFormatError(
                "configuration JSON is too deeply nested"
            )
        byte = self._peek()
        if byte == 0x6E:
            self._position += 1
            return self._literal(b"ull", None)
        if byte == 0x74:
            self._position += 1
            return self._literal(b"rue", True)
        if byte == 0x66:
            self._position += 1
            return self._literal(b"alse", False)
        if byte == 0x22:
            return self._string()
        if byte == 0x5B:
            self._position += 1
            result = []
            if self._peek() == 0x5D:
                self._position += 1
                return result
            while True:
                result.append(self._value(depth + 1))
                separator = self._take()
                if separator == 0x5D:
                    return result
                if separator != 0x2C:
                    self._fail()
        if byte == 0x7B:
            self._position += 1
            result = {}
            previous_key = None
            if self._peek() == 0x7D:
                self._position += 1
                return result
            while True:
                if self._peek() != 0x22:
                    self._fail()
                key = self._string()
                if previous_key is not None and key <= previous_key:
                    self._fail()
                previous_key = key
                if self._take() != 0x3A:
                    self._fail()
                result[key] = self._value(depth + 1)
                separator = self._take()
                if separator == 0x7D:
                    return result
                if separator != 0x2C:
                    self._fail()
        if byte == 0x2D or 0x30 <= byte <= 0x39:
            return self._integer()
        self._fail()

    def parse(self):
        value = self._value(0)
        if self._position != self._end:
            self._fail()
        return value


def _require_integer(name, value, minimum=None, maximum=None):
    if type(value) is not int:
        raise ValueError("{} must be an integer".format(name))
    if minimum is not None and value < minimum:
        raise ValueError("{} is below its minimum".format(name))
    if maximum is not None and value > maximum:
        raise ValueError("{} exceeds its maximum".format(name))


def _bounded_path(path):
    if type(path) is not str:
        raise ValueError("base_path must be a string")
    if not path or len(path) > 192 or "\x00" in path or path.endswith("/"):
        raise ValueError("base_path must be a bounded file path")
    return path


def _crc32(data):
    """Return an unsigned IEEE CRC32 without importing protocol code."""

    value = 0xFFFFFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            if value & 1:
                value = (value >> 1) ^ 0xEDB88320
            else:
                value >>= 1
    return (value ^ 0xFFFFFFFF) & 0xFFFFFFFF


def _checked_size(total, increment, maximum):
    total += increment
    if maximum is not None and total > maximum:
        raise ConfigStoreFormatError(
            "configuration record exceeds size limit"
        )
    return total


def _utf8_width(codepoint):
    if codepoint < 0x80:
        return 1
    if codepoint < 0x800:
        return 2
    if 0xD800 <= codepoint <= 0xDFFF:
        raise ConfigStoreFormatError("configuration is not valid UTF-8")
    if codepoint < 0x10000:
        return 3
    if codepoint <= 0x10FFFF:
        return 4
    raise ConfigStoreFormatError("configuration is not valid UTF-8")


def _canonical_string_size(value, maximum=None):
    total = _checked_size(0, 2, maximum)
    for character in value:
        codepoint = ord(character)
        if codepoint in _ESCAPE_SUFFIX:
            width = 2
        elif codepoint < 0x20:
            width = 6
        else:
            width = _utf8_width(codepoint)
        total = _checked_size(total, width, maximum)
    return total


def _canonical_json_size(value, depth=0, maximum=None):
    """Measure the exact canonical UTF-8 output without building fragments."""

    if depth > _MAX_JSON_DEPTH:
        raise ConfigStoreFormatError("configuration JSON is too deeply nested")
    if value is None:
        return _checked_size(0, 4, maximum)
    if type(value) is bool:
        return _checked_size(0, 4 if value else 5, maximum)
    if type(value) is int:
        return _checked_size(0, len(str(value)), maximum)
    if type(value) is str:
        return _canonical_string_size(value, maximum)
    if type(value) is list:
        # One byte per value plus separators/brackets is the smallest possible
        # JSON list.  Reject obviously oversized containers before recursion.
        if value:
            minimum = (2 * len(value)) + 1
            _checked_size(0, minimum, maximum)
        total = _checked_size(0, 2, maximum)
        for index, item in enumerate(value):
            if index:
                total = _checked_size(total, 1, maximum)
            remaining = None if maximum is None else maximum - total
            total = _checked_size(
                total,
                _canonical_json_size(item, depth + 1, remaining),
                maximum,
            )
        return total
    if type(value) is dict:
        # At minimum each entry is "":0 plus one separator.  This prevents a
        # huge temporary key list for a document that cannot fit regardless.
        if value:
            minimum = (5 * len(value)) + 1
            _checked_size(0, minimum, maximum)
        keys = []
        for key in value:
            if type(key) is not str:
                raise ConfigStoreFormatError(
                    "configuration object keys must be strings"
                )
            keys.append(key)
        keys.sort()
        total = _checked_size(0, 2, maximum)
        for index, key in enumerate(keys):
            if index:
                total = _checked_size(total, 1, maximum)
            remaining = None if maximum is None else maximum - total
            total = _checked_size(
                total, _canonical_string_size(key, remaining), maximum
            )
            total = _checked_size(total, 1, maximum)
            remaining = None if maximum is None else maximum - total
            total = _checked_size(
                total,
                _canonical_json_size(value[key], depth + 1, remaining),
                maximum,
            )
        return total
    raise ConfigStoreFormatError(
        "configuration contains a non-JSON primitive"
    )


def _write_bytes(output, offset, value):
    for byte in value:
        output[offset] = byte
        offset += 1
    return offset


def _write_canonical_string(value, output, offset):
    output[offset] = 0x22
    offset += 1
    for character in value:
        codepoint = ord(character)
        escape_suffix = _ESCAPE_SUFFIX.get(codepoint)
        if escape_suffix is not None:
            output[offset] = 0x5C
            output[offset + 1] = escape_suffix
            offset += 2
        elif codepoint < 0x20:
            output[offset] = 0x5C
            output[offset + 1] = 0x75
            output[offset + 2] = _HEX_DIGITS[(codepoint >> 12) & 0x0F]
            output[offset + 3] = _HEX_DIGITS[(codepoint >> 8) & 0x0F]
            output[offset + 4] = _HEX_DIGITS[(codepoint >> 4) & 0x0F]
            output[offset + 5] = _HEX_DIGITS[codepoint & 0x0F]
            offset += 6
        elif codepoint < 0x80:
            output[offset] = codepoint
            offset += 1
        elif codepoint < 0x800:
            output[offset] = 0xC0 | (codepoint >> 6)
            output[offset + 1] = 0x80 | (codepoint & 0x3F)
            offset += 2
        elif codepoint < 0x10000:
            if 0xD800 <= codepoint <= 0xDFFF:
                raise ConfigStoreFormatError(
                    "configuration is not valid UTF-8"
                )
            output[offset] = 0xE0 | (codepoint >> 12)
            output[offset + 1] = 0x80 | ((codepoint >> 6) & 0x3F)
            output[offset + 2] = 0x80 | (codepoint & 0x3F)
            offset += 3
        elif codepoint <= 0x10FFFF:
            output[offset] = 0xF0 | (codepoint >> 18)
            output[offset + 1] = 0x80 | ((codepoint >> 12) & 0x3F)
            output[offset + 2] = 0x80 | ((codepoint >> 6) & 0x3F)
            output[offset + 3] = 0x80 | (codepoint & 0x3F)
            offset += 4
        else:
            raise ConfigStoreFormatError("configuration is not valid UTF-8")
    output[offset] = 0x22
    return offset + 1


def _write_canonical_json(value, output, offset, depth=0):
    if depth > _MAX_JSON_DEPTH:
        raise ConfigStoreFormatError("configuration JSON is too deeply nested")
    if value is None:
        return _write_bytes(output, offset, _JSON_NULL)
    if type(value) is bool:
        return _write_bytes(output, offset, _JSON_TRUE if value else _JSON_FALSE)
    if type(value) is int:
        return _write_bytes(output, offset, str(value).encode("ascii"))
    if type(value) is str:
        return _write_canonical_string(value, output, offset)
    if type(value) is list:
        output[offset] = 0x5B
        offset += 1
        for index, item in enumerate(value):
            if index:
                output[offset] = 0x2C
                offset += 1
            offset = _write_canonical_json(
                item, output, offset, depth + 1
            )
        output[offset] = 0x5D
        return offset + 1
    if type(value) is dict:
        keys = []
        for key in value:
            if type(key) is not str:
                raise ConfigStoreFormatError(
                    "configuration object keys must be strings"
                )
            keys.append(key)
        keys.sort()
        output[offset] = 0x7B
        offset += 1
        for index, key in enumerate(keys):
            if index:
                output[offset] = 0x2C
                offset += 1
            offset = _write_canonical_string(key, output, offset)
            output[offset] = 0x3A
            offset += 1
            offset = _write_canonical_json(
                value[key], output, offset, depth + 1
            )
        output[offset] = 0x7D
        return offset + 1
    raise ConfigStoreFormatError(
        "configuration contains a non-JSON primitive"
    )


def _canonical_json_buffer(payload, depth=0, maximum=None):
    size = _canonical_json_size(payload, depth, maximum)
    output = bytearray(size)
    end = _write_canonical_json(payload, output, 0, depth)
    if end != size:
        raise ConfigStoreFormatError(
            "configuration changed during canonical encoding"
        )
    return output


def _canonical_json_string(value):
    if type(value) is not str:
        raise ConfigStoreFormatError("configuration string is invalid")
    return bytes(_canonical_json_buffer(value)).decode("utf-8")


def _canonical_json_text(value, depth=0):
    return bytes(_canonical_json_buffer(value, depth)).decode("utf-8")


def _canonical_json_bytes(payload):
    return bytes(_canonical_json_buffer(payload))


def _record_layout(payload, generation, max_record_bytes):
    _require_integer("generation", generation, 1, MAX_GENERATION)
    payload_length = _canonical_json_size(
        payload, maximum=max_record_bytes
    )
    # Metadata widths are independent from the checksum value (always eight
    # hexadecimal digits), so the final immutable record can be allocated once.
    generation_text = str(generation)
    payload_length_text = str(payload_length)
    header_length = (
        len(_MAGIC) + len(generation_text) + len(payload_length_text) + 12
    )
    footer_length = (
        len(_FOOTER_MAGIC)
        + len(generation_text)
        + len(payload_length_text)
        + 13
    )
    record_length = header_length + payload_length + footer_length
    if record_length > max_record_bytes:
        raise ConfigStoreFormatError("configuration record exceeds size limit")
    return payload_length, header_length, footer_length, record_length


def _encode_record_buffer(payload, generation, max_record_bytes):
    payload_length, header_length, footer_length, record_length = (
        _record_layout(payload, generation, max_record_bytes)
    )
    # The sizing walk creates short-lived sorted-key lists on MicroPython.
    # Reclaim those objects before reserving the exact record as small blocks;
    # a fragmented ESP32 heap cannot promise one contiguous 8-KiB run.
    _gc.collect()
    record_buffer = _SegmentedRecord(record_length)
    payload_start = header_length
    payload_end = _write_canonical_json(
        payload, record_buffer, payload_start
    )
    if payload_end != payload_start + payload_length:
        raise ConfigStoreFormatError(
            "configuration changed during canonical encoding"
        )
    checksum = _crc32(record_buffer.iter_bytes(payload_start, payload_end))
    header = "{}|{}|{}|{:08x}\n".format(
        _MAGIC, generation, payload_length, checksum
    ).encode("ascii")
    footer = "\n{}|{}|{}|{:08x}\n".format(
        _FOOTER_MAGIC, generation, payload_length, checksum
    ).encode("ascii")
    if len(header) != header_length or len(footer) != footer_length:
        raise ConfigStoreFormatError("configuration metadata size differs")
    _write_bytes(record_buffer, 0, header)
    _write_bytes(record_buffer, payload_end, footer)
    return record_buffer


def _encode_record(payload, generation, max_record_bytes):
    """Return the immutable helper form used by format tests and fixtures."""

    return _encode_record_buffer(
        payload, generation, max_record_bytes
    ).to_bytes()


def _parse_record_parts(line, expected_magic):
    try:
        text = line.decode("ascii")
    except (UnicodeError, ValueError) as exc:
        raise ConfigStoreFormatError("record metadata is not ASCII") from exc
    parts = text.split("|")
    if len(parts) != 4 or parts[0] != expected_magic:
        raise ConfigStoreFormatError("record metadata magic differs")
    try:
        generation = int(parts[1])
        payload_length = int(parts[2])
        checksum = int(parts[3], 16)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigStoreFormatError("record metadata is malformed") from exc
    _require_integer("generation", generation, 1, MAX_GENERATION)
    _require_integer("payload_length", payload_length, 1)
    _require_integer("checksum", checksum, 0, 0xFFFFFFFF)
    if parts[1] != str(generation) or parts[2] != str(payload_length):
        raise ConfigStoreFormatError("record numeric metadata is not canonical")
    if len(parts[3]) != 8 or parts[3] != "{:08x}".format(checksum):
        raise ConfigStoreFormatError("record checksum text is not canonical")
    return generation, payload_length, checksum


def _decode_record(record, max_record_bytes):
    if type(record) is not bytes:
        raise ConfigStoreFormatError("record must be immutable bytes")
    if not record or len(record) > max_record_bytes:
        raise ConfigStoreFormatError("record size is invalid")
    header_end = record.find(b"\n")
    if header_end <= 0:
        raise ConfigStoreFormatError("record header is incomplete")
    generation, payload_length, checksum = _parse_record_parts(
        record[:header_end], _MAGIC
    )
    payload_start = header_end + 1
    payload_end = payload_start + payload_length
    if payload_end >= len(record) or record[payload_end:payload_end + 1] != b"\n":
        raise ConfigStoreFormatError("record payload length differs")
    footer_end = record.find(b"\n", payload_end + 1)
    if footer_end != len(record) - 1:
        raise ConfigStoreFormatError("record has missing or trailing data")
    footer = _parse_record_parts(
        record[payload_end + 1:footer_end], _FOOTER_MAGIC
    )
    if footer != (generation, payload_length, checksum):
        raise ConfigStoreFormatError("record footer differs from header")
    payload_view = memoryview(record)[payload_start:payload_end]
    if _crc32(payload_view) != checksum:
        raise ConfigStoreFormatError("record CRC32 differs")
    del payload_view
    try:
        # json.loads() and the returned recovery contract require text on both
        # CPython and MicroPython.  Keep the unavoidable byte slice only for
        # the decode itself, then release it before allocating the parsed tree
        # and canonical comparison buffer.
        payload_bytes = record[payload_start:payload_end]
        payload_text = payload_bytes.decode("utf-8")
        del payload_bytes
        payload = _json.loads(payload_text)
    except (UnicodeError, TypeError, ValueError, OverflowError) as exc:
        raise ConfigStoreFormatError("record JSON is invalid") from exc
    canonical_length = _canonical_json_size(
        payload, maximum=payload_length
    )
    if canonical_length != payload_length:
        raise ConfigStoreFormatError("record JSON is not canonical")
    comparison = _CanonicalComparison(
        record, payload_start, payload_length
    )
    canonical_end = _write_canonical_json(payload, comparison, 0)
    if canonical_end != payload_length:
        raise ConfigStoreFormatError("record JSON is not canonical")
    del comparison
    token_view = memoryview(record)[payload_start:payload_end]
    canonical_token = _sha256_token((token_view,))
    del token_view
    del payload_text
    return {
        "generation": generation,
        "payload": payload,
        "fingerprint": checksum,
        "canonical_payload": canonical_token,
    }


def _decode_segmented_record(record, max_record_bytes):
    if type(record) is not _SegmentedRecord:
        raise ConfigStoreFormatError("segmented record is invalid")
    if not len(record) or len(record) > max_record_bytes:
        raise ConfigStoreFormatError("record size is invalid")
    header_end = record.find_byte(0x0A)
    if header_end <= 0:
        raise ConfigStoreFormatError("record header is incomplete")
    generation, payload_length, checksum = _parse_record_parts(
        record.slice_bytes(0, header_end), _MAGIC
    )
    payload_start = header_end + 1
    payload_end = payload_start + payload_length
    if payload_end >= len(record) or record[payload_end] != 0x0A:
        raise ConfigStoreFormatError("record payload length differs")
    footer_end = record.find_byte(0x0A, payload_end + 1)
    if footer_end != len(record) - 1:
        raise ConfigStoreFormatError("record has missing or trailing data")
    footer = _parse_record_parts(
        record.slice_bytes(payload_end + 1, footer_end), _FOOTER_MAGIC
    )
    if footer != (generation, payload_length, checksum):
        raise ConfigStoreFormatError("record footer differs from header")
    if _crc32(record.iter_bytes(payload_start, payload_end)) != checksum:
        raise ConfigStoreFormatError("record CRC32 differs")
    parser = _CanonicalJSONParser(record, payload_start, payload_end)
    payload = parser.parse()
    del parser
    canonical_token = _sha256_token(
        record.iter_views(payload_start, payload_end)
    )
    del record
    _gc.collect()
    return {
        "generation": generation,
        "payload": payload,
        "fingerprint": checksum,
        "canonical_payload": canonical_token,
    }


class _DefaultFileSystem:
    __slots__ = ("_sync",)

    def __init__(self):
        self._sync = getattr(_os, "sync", None)
        if not callable(self._sync):
            raise ConfigStoreDurabilityError(
                "filesystem does not provide durable os.sync()"
            )

    @staticmethod
    def open(path, mode):
        return open(path, mode)

    @staticmethod
    def stat(path):
        return _os.stat(path)

    @staticmethod
    def remove(path):
        return _os.remove(path)

    @staticmethod
    def rename(source, target):
        return _os.rename(source, target)

    def sync(self):
        return self._sync()


def _is_missing_error(exc):
    code = getattr(exc, "errno", None)
    if code is None and getattr(exc, "args", None):
        code = exc.args[0]
    return code == 2


def _stream_can_seek(stream):
    seek = getattr(stream, "seek", None)
    if not callable(seek):
        return False
    result = seek(0)
    if result is not None and (type(result) is not int or result != 0):
        raise ConfigStoreError("file seek returned an invalid position")
    return True


class AtomicJSONConfigStore:
    """Persist canonical JSON records in two validated generation slots."""

    def __init__(
        self,
        base_path="/landy_heater_config",
        max_record_bytes=DEFAULT_MAX_RECORD_BYTES,
        filesystem=None,
    ):
        base_path = _bounded_path(base_path)
        _require_integer(
            "max_record_bytes", max_record_bytes, 256, MAX_RECORD_BYTES
        )
        if filesystem is None:
            filesystem = _DefaultFileSystem()
        for method in ("open", "stat", "remove", "rename", "sync"):
            if not callable(getattr(filesystem, method, None)):
                raise ValueError("filesystem must provide {}()".format(method))

        self._filesystem = filesystem
        self._base_path = base_path
        self._slot_paths = {
            "a": base_path + ".a",
            "b": base_path + ".b",
        }
        self._temp_path = base_path + ".tmp"
        self._max_record_bytes = max_record_bytes
        self._last_error = None
        self._last_target_slot = None
        self._durability_unknown = False
        self._reads = 0
        self._writes = 0
        self._invalid_slots = 0
        self._invalid_slot_names = []
        self._slot_files_present = 0
        self._temp_present = False
        self._bootstrap_receipt = None

    @property
    def base_path(self):
        return self._base_path

    @property
    def max_record_bytes(self):
        return self._max_record_bytes

    def _exists(self, path):
        try:
            self._filesystem.stat(path)
        except OSError as exc:
            if _is_missing_error(exc):
                return False
            raise
        return True

    def _read_bytes(self, path):
        stat_result = self._filesystem.stat(path)
        try:
            file_size = stat_result[6]
        except (TypeError, IndexError, KeyError) as exc:
            raise ConfigStoreError(
                "filesystem.stat returned an invalid result"
            ) from exc
        if type(file_size) is not int or file_size < 0:
            raise ConfigStoreError(
                "filesystem.stat returned an invalid file size"
            )
        if file_size > self._max_record_bytes:
            raise ConfigStoreFormatError("configuration file is oversized")
        stream = self._filesystem.open(path, "rb")
        primary = None
        try:
            # Allocate only for the actual bounded record.  Reading one extra
            # byte still detects growth between stat() and read() without an
            # 8/24-KiB peak allocation on the ESP32 heap.
            data = stream.read(file_size + 1)
            if type(data) is not bytes:
                raise ConfigStoreFormatError("file read returned non-bytes")
            if len(data) != file_size:
                raise ConfigStoreFormatError(
                    "configuration file size changed during read"
                )
            return data
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                result = stream.close()
                if result is not None:
                    raise ConfigStoreError("file close returned non-None")
            except BaseException:
                if primary is None:
                    raise

    def _read_segmented_record(self, path):
        stat_result = self._filesystem.stat(path)
        try:
            file_size = stat_result[6]
        except (TypeError, IndexError, KeyError) as exc:
            raise ConfigStoreError(
                "filesystem.stat returned an invalid result"
            ) from exc
        if type(file_size) is not int or file_size < 0:
            raise ConfigStoreError(
                "filesystem.stat returned an invalid file size"
            )
        if file_size > self._max_record_bytes:
            raise ConfigStoreFormatError("configuration file is oversized")
        record = _SegmentedRecord(file_size)
        stream = self._filesystem.open(path, "rb")
        primary = None
        try:
            if not _stream_can_seek(stream):
                # Compatibility for legacy synthetic filesystem ports whose
                # read() returns a fresh prefix on every call.  Real files are
                # seekable and always use the fragmented-heap-safe path.
                data = stream.read(file_size + 1)
                if type(data) is not bytes:
                    raise ConfigStoreFormatError(
                        "file read returned non-bytes"
                    )
                if len(data) != file_size:
                    raise ConfigStoreFormatError(
                        "configuration file size changed during read"
                    )
                offset = 0
                for chunk in record.iter_chunks():
                    chunk[:] = data[offset:offset + len(chunk)]
                    offset += len(chunk)
                del data
            else:
                for chunk in record.iter_chunks():
                    data = stream.read(len(chunk))
                    if type(data) is not bytes:
                        raise ConfigStoreFormatError(
                            "file read returned non-bytes"
                        )
                    if len(data) != len(chunk):
                        raise ConfigStoreFormatError(
                            "configuration file size changed during read"
                        )
                    chunk[:] = data
                extra = stream.read(1)
                if type(extra) is not bytes:
                    raise ConfigStoreFormatError(
                        "file read returned non-bytes"
                    )
                if extra:
                    raise ConfigStoreFormatError(
                        "configuration file grew during read"
                    )
            return record
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                result = stream.close()
                if result is not None:
                    raise ConfigStoreError("file close returned non-None")
            except BaseException:
                if primary is None:
                    raise

    def _verify_segmented_record(self, path, expected):
        """Read back one candidate in small blocks and compare every byte."""

        stat_result = self._filesystem.stat(path)
        try:
            file_size = stat_result[6]
        except (TypeError, IndexError, KeyError) as exc:
            raise ConfigStoreError(
                "filesystem.stat returned an invalid result"
            ) from exc
        if type(file_size) is not int or file_size < 0:
            raise ConfigStoreError(
                "filesystem.stat returned an invalid file size"
            )
        if file_size != len(expected):
            raise ConfigStoreFormatError(
                "configuration file size changed during verification"
            )
        stream = self._filesystem.open(path, "rb")
        primary = None
        try:
            if not _stream_can_seek(stream):
                actual = stream.read(file_size + 1)
                if type(actual) is not bytes:
                    raise ConfigStoreFormatError(
                        "file read returned non-bytes"
                    )
                if len(actual) != file_size:
                    raise ConfigStoreFormatError(
                        "configuration file size changed during verification"
                    )
                position = 0
                for expected_chunk in expected.iter_chunks():
                    for byte in expected_chunk:
                        if byte != actual[position]:
                            raise ConfigStoreFormatError(
                                "stored record verification differs"
                            )
                        position += 1
            else:
                for expected_chunk in expected.iter_chunks():
                    actual = stream.read(len(expected_chunk))
                    if type(actual) is not bytes:
                        raise ConfigStoreFormatError(
                            "file read returned non-bytes"
                        )
                    if len(actual) != len(expected_chunk):
                        raise ConfigStoreFormatError(
                            "configuration file size changed during verification"
                        )
                    for index, byte in enumerate(actual):
                        if byte != expected_chunk[index]:
                            raise ConfigStoreFormatError(
                                "stored record verification differs"
                            )
                extra = stream.read(1)
                if type(extra) is not bytes:
                    raise ConfigStoreFormatError(
                        "file read returned non-bytes"
                    )
                if extra:
                    raise ConfigStoreFormatError(
                        "configuration file grew during verification"
                    )
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                result = stream.close()
                if result is not None:
                    raise ConfigStoreError("file close returned non-None")
            except BaseException:
                if primary is None:
                    raise

    def _read_header_generation(self, path):
        stat_result = self._filesystem.stat(path)
        try:
            file_size = stat_result[6]
        except (TypeError, IndexError, KeyError) as exc:
            raise ConfigStoreError(
                "filesystem.stat returned an invalid result"
            ) from exc
        if (
            type(file_size) is not int
            or file_size <= 0
            or file_size > self._max_record_bytes
        ):
            raise ConfigStoreFormatError("record size is invalid")
        stream = self._filesystem.open(path, "rb")
        primary = None
        try:
            size = min(file_size, _RECORD_CHUNK_BYTES)
            prefix = stream.read(size)
            if type(prefix) is not bytes:
                raise ConfigStoreFormatError("file read returned non-bytes")
            if len(prefix) != size:
                raise ConfigStoreFormatError(
                    "configuration file size changed during read"
                )
            header_end = prefix.find(b"\n")
            if header_end <= 0:
                raise ConfigStoreFormatError("record header is incomplete")
            return _parse_record_parts(prefix[:header_end], _MAGIC)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                result = stream.close()
                if result is not None:
                    raise ConfigStoreError("file close returned non-None")
            except BaseException:
                if primary is None:
                    raise

    def _read_slot_reusing_payload(self, slot, reference):
        """Reuse an identical first payload without a second full-size read."""

        path = self._slot_paths[slot]
        if not self._exists(path):
            return None
        self._slot_files_present += 1
        expected = None
        try:
            generation, payload_length, checksum = (
                self._read_header_generation(path)
            )
            if checksum != reference["fingerprint"]:
                raise ConfigStoreFormatError(
                    "record payload fingerprint differs"
                )
            expected = _encode_record_buffer(
                reference["payload"], generation, self._max_record_bytes
            )
            if payload_length + 1 >= len(expected):
                raise ConfigStoreFormatError(
                    "record payload length differs"
                )
            self._verify_segmented_record(path, expected)
        except (ConfigStoreError, ValueError):
            if expected is not None:
                del expected
                _gc.collect()
            try:
                record = _decode_segmented_record(
                    self._read_segmented_record(path),
                    self._max_record_bytes,
                )
            except (ConfigStoreError, ValueError):
                self._invalid_slots += 1
                self._invalid_slot_names.append(slot)
                return None
            self._reads += 1
            record["slot"] = slot
            return record
        del expected
        _gc.collect()
        self._reads += 1
        return {
            "generation": generation,
            "payload": reference["payload"],
            "fingerprint": reference["fingerprint"],
            "canonical_payload": reference["canonical_payload"],
            "slot": slot,
        }

    def _read_slot(self, slot):
        path = self._slot_paths[slot]
        if not self._exists(path):
            return None
        self._slot_files_present += 1
        try:
            record = _decode_segmented_record(
                self._read_segmented_record(path),
                self._max_record_bytes,
            )
        except (ConfigStoreError, ValueError):
            self._invalid_slots += 1
            self._invalid_slot_names.append(slot)
            return None
        self._reads += 1
        record["slot"] = slot
        return record

    def load_records(self):
        """Return structurally valid A/B records; the temp is never loaded."""

        self._invalid_slots = 0
        self._invalid_slot_names = []
        self._slot_files_present = 0
        self._temp_present = self._exists(self._temp_path)
        records = []
        for slot in _SLOT_NAMES:
            if records:
                record = self._read_slot_reusing_payload(slot, records[0])
            else:
                record = self._read_slot(slot)
            if record is not None:
                records.append(record)
        self._last_error = None if self._invalid_slots == 0 else "invalid_slot"
        return tuple(records)

    def _recovery_signature(self, records):
        record_tokens = []
        for record in records:
            record_tokens.append((
                record["slot"],
                record["generation"],
                record["fingerprint"],
                record["canonical_payload"],
            ))
        return (
            tuple(record_tokens),
            tuple(self._invalid_slot_names),
        )

    def inspect_recovery(self):
        """Return one bounded record view and an opaque reseal signature."""

        records = self.load_records()
        return records, self._recovery_signature(records)

    def _remove_if_present(self, path):
        if not self._exists(path):
            return False
        result = self._filesystem.remove(path)
        if result is not None:
            raise ConfigStoreError("filesystem.remove returned non-None")
        return True

    def _sync(self):
        result = self._filesystem.sync()
        if result is not None:
            raise ConfigStoreError("filesystem.sync returned non-None")

    def _write_all(self, path, data):
        stream = self._filesystem.open(path, "wb")
        primary = None
        try:
            parts = (
                data.iter_chunks()
                if type(data) is _SegmentedRecord
                else (data,)
            )
            for part in parts:
                offset = 0
                view = memoryview(part)
                while offset < len(part):
                    written = stream.write(view[offset:])
                    if (
                        type(written) is not int
                        or written <= 0
                        or written > len(part) - offset
                    ):
                        raise ConfigStoreError(
                            "file write returned an invalid count"
                        )
                    offset += written
                del view
            result = stream.flush()
            if result is not None:
                raise ConfigStoreError("file flush returned non-None")
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                result = stream.close()
                if result is not None:
                    raise ConfigStoreError("file close returned non-None")
            except BaseException:
                if primary is None:
                    raise

    @staticmethod
    def _latest_structural(records):
        if not records:
            return None
        if len(records) == 1:
            return records[0]
        first, second = records
        if first["generation"] == second["generation"]:
            if (
                first["fingerprint"] != second["fingerprint"]
                or first["canonical_payload"] != second["canonical_payload"]
            ):
                raise ConfigStoreConflictError(
                    "equal generation slots contain different payloads"
                )
            return first
        if first["generation"] > second["generation"]:
            return first
        return second

    @staticmethod
    def _target_slot(records):
        if not records:
            return "a"
        if len(records) == 1:
            return "b" if records[0]["slot"] == "a" else "a"
        first, second = records
        if first["generation"] < second["generation"]:
            return first["slot"]
        if second["generation"] < first["generation"]:
            return second["slot"]
        return "b"

    def _bootstrap_receipt_target(self, expected_generation):
        """Verify the just-provisioned first slot without a full-size read."""

        receipt = self._bootstrap_receipt
        if receipt is None or receipt[1] != expected_generation:
            return None
        slot, _, expected_record = receipt
        other_slot = "b" if slot == "a" else "a"
        if not self._exists(self._slot_paths[slot]):
            self._bootstrap_receipt = None
            raise ConfigStoreConflictError("persistent generation changed")
        if self._exists(self._slot_paths[other_slot]):
            # The cached one-slot topology is no longer complete; use the
            # ordinary recovery scan so no external generation is ignored.
            self._bootstrap_receipt = None
            return None
        try:
            self._verify_segmented_record(
                self._slot_paths[slot], expected_record
            )
        except (ConfigStoreError, ValueError) as exc:
            self._bootstrap_receipt = None
            raise ConfigStoreConflictError(
                "persistent generation changed"
            ) from exc
        self._invalid_slots = 0
        self._invalid_slot_names = []
        self._slot_files_present = 1
        self._temp_present = self._exists(self._temp_path)
        self._reads += 1
        self._last_error = None
        return other_slot

    def _publish_record(
        self,
        payload,
        generation,
        target_slot,
        record_holder=None,
        retain_bootstrap_receipt=False,
    ):
        if record_holder is None:
            record_buffer = _encode_record_buffer(
                payload, generation, self._max_record_bytes
            )
        else:
            # The one-item holder transfers the only caller-owned reference.
            # Clearing it lets the completed buffer be reclaimed immediately
            # after the write, while the caller's frame remains suspended.
            record_buffer = record_holder[0]
            record_holder[0] = None
        target_path = self._slot_paths[target_slot]
        publish_attempted = False
        try:
            self._remove_if_present(self._temp_path)
            self._write_all(self._temp_path, record_buffer)
            self._sync()
            self._verify_segmented_record(self._temp_path, record_buffer)

            publish_attempted = True
            result = self._filesystem.rename(self._temp_path, target_path)
            if result is not None:
                raise ConfigStoreError("filesystem.rename returned non-None")
            self._sync()
            self._verify_segmented_record(target_path, record_buffer)
        except BaseException:
            self._last_error = (
                "durability_unknown" if publish_attempted else "commit_failed"
            )
            if publish_attempted:
                self._durability_unknown = True
            raise

        if retain_bootstrap_receipt:
            self._bootstrap_receipt = (
                target_slot,
                generation,
                record_buffer,
            )
        else:
            self._bootstrap_receipt = None
            del record_buffer
            _gc.collect()

        self._last_error = None
        self._last_target_slot = target_slot
        self._durability_unknown = False
        self._writes += 1
        return True

    def commit(self, payload, generation, expected_generation):
        """Publish one generation and verify it after the final sync."""

        _require_integer("generation", generation, 1, MAX_GENERATION)
        _require_integer(
            "expected_generation", expected_generation, 0, MAX_GENERATION
        )
        if generation != expected_generation + 1:
            raise ConfigStoreConflictError("generation must advance by one")
        # Encode before any read or write so malformed/oversized candidates
        # cannot disturb the current slots.
        record_holder = [
            _encode_record_buffer(
                payload, generation, self._max_record_bytes
            )
        ]

        target_slot = self._bootstrap_receipt_target(expected_generation)
        retain_bootstrap_receipt = False
        if target_slot is None:
            records = self.load_records()
            latest = self._latest_structural(records)
            actual_generation = 0 if latest is None else latest["generation"]
            if actual_generation != expected_generation:
                raise ConfigStoreConflictError("persistent generation changed")
            target_slot = self._target_slot(records)
            retain_bootstrap_receipt = (
                actual_generation == 0
                and self._slot_files_present == 0
            )
            # The target slot is detached from the parsed recovery records.
            # Drop those potentially large trees before publication.
            del latest
            del records
            _gc.collect()
        return self._publish_record(
            payload,
            generation,
            target_slot,
            record_holder,
            retain_bootstrap_receipt,
        )

    def reseal(self, payload, expected_recovery_signature=None):
        """Explicitly replace an untrusted topology with two safe generations.

        The first publish always targets an inactive/older slot, preserving at
        least one structural survivor until the new candidate is durable.  A
        second verified publish then replaces the remaining slot.  Any failure
        leaves ConfigManager start authority closed; this method is intended
        only for an explicit user-confirmed recovery flow.
        """

        # Validate the payload before observing or touching persistent state.
        _record_layout(payload, 1, self._max_record_bytes)
        records = self.load_records()
        current_recovery_signature = self._recovery_signature(records)
        if (
            expected_recovery_signature is not None
            and current_recovery_signature != expected_recovery_signature
        ):
            raise ConfigStoreConflictError(
                "persistent recovery view changed before reseal"
            )
        invalid_slot_names = tuple(self._invalid_slot_names)
        maximum_generation = 0
        for record in records:
            if record["generation"] > maximum_generation:
                maximum_generation = record["generation"]
        if maximum_generation > MAX_GENERATION - 2:
            raise ConfigStoreConflictError(
                "generation space cannot be safely resealed"
            )
        first_generation = maximum_generation + 1
        second_generation = maximum_generation + 2
        # Build both exact generation records before the first publish.  The
        # extra digit at a decimal generation boundary can change the encoded
        # size; an allocation/size failure for generation N+2 must not strand
        # a successfully published N+1 record.
        first_record_holder = [
            _encode_record_buffer(
                payload, first_generation, self._max_record_bytes
            )
        ]
        second_record_holder = [
            _encode_record_buffer(
                payload, second_generation, self._max_record_bytes
            )
        ]
        if len(invalid_slot_names) == 2:
            # With two unreadable slots neither generation is known.  Writing
            # a low recovery generation beside either survivor could make an
            # old payload look newer after a partial reseal.  Explicit
            # recovery may therefore discard both untrusted slots, but each
            # deletion is synced before any generation-one record is
            # published.  A reset at every intermediate point sees at most a
            # lone/untrusted survivor and remains start-fenced.
            try:
                for invalid_slot in invalid_slot_names:
                    self._remove_if_present(
                        self._slot_paths[invalid_slot]
                    )
                    self._sync()
            except BaseException:
                self._last_error = "durability_unknown"
                self._durability_unknown = True
                raise
        if invalid_slot_names:
            # A stable format-corrupt slot has unknown generation metadata.
            # Replace it first, so no unknown higher generation can survive
            # beside the new recovery record after a partial reseal.
            first_slot = invalid_slot_names[0]
        elif not records:
            first_slot = "a"
        elif len(records) == 1:
            first_slot = "b" if records[0]["slot"] == "a" else "a"
        else:
            first, second = records
            if first["generation"] < second["generation"]:
                first_slot = first["slot"]
            elif second["generation"] < first["generation"]:
                first_slot = second["slot"]
            else:
                first_slot = "a"
        second_slot = "b" if first_slot == "a" else "a"
        del records
        _gc.collect()
        self._publish_record(
            payload, first_generation, first_slot, first_record_holder
        )
        self._publish_record(
            payload, second_generation, second_slot, second_record_holder
        )
        return second_generation

    def status(self):
        return {
            "base_path": self._base_path,
            "max_record_bytes": self._max_record_bytes,
            "last_error": self._last_error,
            "last_target_slot": self._last_target_slot,
            "durability_unknown": self._durability_unknown,
            "reads": self._reads,
            "writes": self._writes,
            "invalid_slots": self._invalid_slots,
            "invalid_slot_names": list(self._invalid_slot_names),
            "slot_files_present": self._slot_files_present,
            "temp_present": self._temp_present,
        }
