"""Bounded, hardware-independent HTTP/1.1 request and response primitives.

The module deliberately parses one complete request at a time.  Socket
ownership, timeouts, JSON decoding, routing and application state belong to
other layers.  Keeping those concerns out of this module makes the parser
usable in CPython tests and on MicroPython without importing any hardware.
"""


MAX_REQUEST_LINE_BYTES = 256
MAX_METHOD_BYTES = 16
MAX_TARGET_BYTES = 192
MAX_HEADER_LINE_BYTES = 256
MAX_HEADER_BLOCK_BYTES = 2048
MAX_HEADER_COUNT = 24
MAX_BODY_BYTES = 4096

MAX_RESPONSE_BODY_BYTES = 8192
MAX_RESPONSE_HEADER_BLOCK_BYTES = 2048
MAX_RESPONSE_HEADER_COUNT = 24

SUPPORTED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

_CRLF = b"\r\n"
_HEADER_END = b"\r\n\r\n"
_URI_DIRECT_BYTES = (
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    b"-._~!$&'()*+,;=:@/?"
)
_HEX_BYTES = b"0123456789ABCDEFabcdef"
_FORBIDDEN_REQUEST_HEADERS = (
    "te",
    "transfer-encoding",
    "expect",
    "content-encoding",
    "trailer",
    "upgrade",
    "method-override",
    "x-http-method",
    "x-method-override",
    "x-http-method-override",
)
_RESPONSE_CORE_HEADER_NAMES = (
    "content-type",
    "content-length",
    "connection",
    "cache-control",
    "x-content-type-options",
)
_FORBIDDEN_RESPONSE_HEADERS = (
    "te",
    "transfer-encoding",
    "content-encoding",
    "trailer",
    "upgrade",
)
_BODYLESS_RESPONSE_STATUSES = (204, 304)
_STATUS_REASONS = {
    200: "OK",
    201: "Created",
    202: "Accepted",
    204: "No Content",
    304: "Not Modified",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    408: "Request Timeout",
    409: "Conflict",
    411: "Length Required",
    412: "Precondition Failed",
    413: "Content Too Large",
    414: "URI Too Long",
    415: "Unsupported Media Type",
    422: "Unprocessable Content",
    428: "Precondition Required",
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",
    500: "Internal Server Error",
    501: "Not Implemented",
    503: "Service Unavailable",
}


class HttpParseError(ValueError):
    """A request error containing only a fixed, response-safe code."""

    def __init__(self, status, code):
        self.status = status
        self.code = code

    def __str__(self):
        return self.code


class HttpResponseEncodeError(ValueError):
    """A response construction error that never includes caller data."""

    def __init__(self, code):
        self.code = code

    def __str__(self):
        return self.code


class HttpRequest:
    """The immutable-by-convention result of :func:`parse_request`."""

    __slots__ = (
        "method",
        "method_supported",
        "target",
        "path",
        "query",
        "version",
        "headers",
        "host",
        "body",
    )

    def __init__(
        self,
        method,
        method_supported,
        target,
        path,
        query,
        headers,
        body,
    ):
        self.method = method
        self.method_supported = method_supported
        self.target = target
        self.path = path
        self.query = query
        self.version = "HTTP/1.1"
        self.headers = headers
        self.host = headers["host"]
        self.body = body

    def header(self, name, default=None):
        """Return a normalized request-header value."""

        if type(name) is not str:
            return default
        return self.headers.get(name.lower(), default)

    def to_dict(self):
        """Return a detached dictionary projection for simple routers."""

        return {
            "method": self.method,
            "method_supported": self.method_supported,
            "target": self.target,
            "path": self.path,
            "query": self.query,
            "version": self.version,
            "headers": self.headers.copy(),
            "host": self.host,
            "body": self.body,
        }


def _parse_error(status, code):
    raise HttpParseError(status, code)


def _encode_error(code):
    raise HttpResponseEncodeError(code)


def _validate_crlf(data, end):
    index = 0
    while index < end:
        value = data[index]
        if value == 13:
            if index + 1 >= end or data[index + 1] != 10:
                _parse_error(400, "invalid_line_ending")
            index += 2
            continue
        if value == 10:
            _parse_error(400, "invalid_line_ending")
        index += 1


def _is_header_name_byte(value):
    return (
        65 <= value <= 90
        or 97 <= value <= 122
        or 48 <= value <= 57
        or value == 45
    )


def _decode_ascii(value, error_code):
    try:
        return value.decode("ascii")
    except UnicodeError:
        _parse_error(400, error_code)


def _parse_method(value):
    if not value or len(value) > MAX_METHOD_BYTES:
        _parse_error(400, "invalid_method")
    for item in value:
        if item < 65 or item > 90:
            _parse_error(400, "invalid_method")
    method = _decode_ascii(value, "invalid_method")
    return method, method in SUPPORTED_METHODS


def _parse_target(value):
    if not value or len(value) > MAX_TARGET_BYTES:
        _parse_error(414, "target_too_long")
    if value[0] != 47:
        _parse_error(400, "invalid_target")

    index = 0
    while index < len(value):
        item = value[index]
        if item == 35 or item == 92:
            _parse_error(400, "invalid_target")
        if item == 37:
            if (
                index + 2 >= len(value)
                or value[index + 1] not in _HEX_BYTES
                or value[index + 2] not in _HEX_BYTES
            ):
                _parse_error(400, "invalid_target")
            index += 3
            continue
        if item not in _URI_DIRECT_BYTES:
            _parse_error(400, "invalid_target")
        index += 1

    target = _decode_ascii(value, "invalid_target")
    separator = target.find("?")
    if separator < 0:
        return target, target, None
    return target, target[:separator], target[separator + 1:]


def _parse_request_line(line):
    if len(line) > MAX_REQUEST_LINE_BYTES:
        _parse_error(414, "request_line_too_long")
    parts = line.split(b" ")
    if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2]:
        _parse_error(400, "invalid_request_line")
    method, method_supported = _parse_method(parts[0])
    target, path, query = _parse_target(parts[1])
    if parts[2] != b"HTTP/1.1":
        _parse_error(400, "unsupported_http_version")
    return method, method_supported, target, path, query


def _parse_header_line(line):
    if not line or len(line) > MAX_HEADER_LINE_BYTES:
        _parse_error(431, "header_line_too_long")
    if line[0] == 32 or line[0] == 9:
        _parse_error(400, "obsolete_header_fold")
    separator = line.find(b":")
    if separator <= 0:
        _parse_error(400, "invalid_header")

    name_bytes = line[:separator]
    for item in name_bytes:
        if not _is_header_name_byte(item):
            _parse_error(400, "invalid_header_name")

    value_bytes = line[separator + 1:]
    for item in value_bytes:
        if item < 32 or item > 126:
            _parse_error(400, "invalid_header_value")

    name = _decode_ascii(name_bytes, "invalid_header_name").lower()
    value = _decode_ascii(value_bytes.strip(b" "), "invalid_header_value")
    return name, value


def _parse_content_length(value):
    if not value or (len(value) > 1 and value[0] == "0"):
        _parse_error(400, "invalid_content_length")
    for item in value:
        if item < "0" or item > "9":
            _parse_error(400, "invalid_content_length")
    # Avoid converting an arbitrarily large integer even if a future header
    # line limit changes.
    if len(value) > 4:
        _parse_error(413, "body_too_large")
    length = int(value)
    if length > MAX_BODY_BYTES:
        _parse_error(413, "body_too_large")
    return length


def parse_request(data):
    """Parse exactly one complete, bounded HTTP/1.1 request.

    ``data`` may be ``bytes`` or ``bytearray``.  Incomplete input and bytes
    after the declared body are errors; the socket adapter must therefore
    invoke this function only when it wants a complete-request decision.
    """

    if type(data) is not bytes and type(data) is not bytearray:
        _parse_error(400, "invalid_request_type")

    line_search_end = MAX_REQUEST_LINE_BYTES + len(_CRLF)
    line_end = data.find(_CRLF, 0, line_search_end)
    if line_end < 0:
        if len(data) > MAX_REQUEST_LINE_BYTES:
            _parse_error(414, "request_line_too_long")
        inspect_end = min(len(data), line_search_end)
        for item in data[:inspect_end]:
            if item == 10 or item == 13:
                _parse_error(400, "invalid_line_ending")
        _parse_error(400, "incomplete_headers")

    if line_end > MAX_REQUEST_LINE_BYTES:
        _parse_error(414, "request_line_too_long")

    header_start = line_end + len(_CRLF)
    header_search_end = header_start + MAX_HEADER_BLOCK_BYTES
    marker_start = data.find(
        _HEADER_END,
        line_end,
        header_search_end,
    )
    if marker_start < 0:
        if len(data) - header_start >= MAX_HEADER_BLOCK_BYTES:
            _parse_error(431, "header_block_too_large")
        inspect_end = min(len(data), header_search_end)
        _validate_crlf(data, inspect_end)
        _parse_error(400, "incomplete_headers")

    head_end = marker_start + len(_HEADER_END)
    header_block_length = head_end - header_start
    if header_block_length > MAX_HEADER_BLOCK_BYTES:
        _parse_error(431, "header_block_too_large")
    _validate_crlf(data, head_end)

    request_line = bytes(data[:line_end])
    method, method_supported, target, path, query = _parse_request_line(
        request_line
    )

    headers = {}
    if marker_start != line_end:
        header_count = 0
        header_line_start = header_start
        while True:
            if header_count >= MAX_HEADER_COUNT:
                _parse_error(431, "too_many_headers")
            header_line_end = data.find(
                _CRLF,
                header_line_start,
                marker_start + len(_CRLF),
            )
            if header_line_end < 0:
                # The framing scan above makes this unreachable, but retaining
                # a fixed failure keeps the parser fail-closed if it changes.
                _parse_error(400, "invalid_line_ending")
            if header_line_end - header_line_start > MAX_HEADER_LINE_BYTES:
                _parse_error(431, "header_line_too_long")
            line = bytes(data[header_line_start:header_line_end])
            name, value = _parse_header_line(line)
            if name in headers:
                _parse_error(400, "duplicate_header")
            if name in _FORBIDDEN_REQUEST_HEADERS:
                _parse_error(400, "forbidden_header")
            headers[name] = value
            header_count += 1
            if header_line_end == marker_start:
                break
            header_line_start = header_line_end + len(_CRLF)

    if "host" not in headers or not headers["host"]:
        _parse_error(400, "missing_host")

    content_length_value = headers.get("content-length")
    if content_length_value is None:
        if method == "POST" or method == "PUT" or method == "PATCH":
            _parse_error(411, "content_length_required")
        body_length = 0
    else:
        body_length = _parse_content_length(content_length_value)

    if (method == "GET" or method == "DELETE") and body_length != 0:
        _parse_error(400, "body_not_allowed")

    available_body_length = len(data) - head_end
    if available_body_length < body_length:
        _parse_error(400, "incomplete_body")
    if available_body_length > body_length:
        _parse_error(400, "unexpected_data_after_body")

    body = bytes(data[head_end:])
    return HttpRequest(
        method,
        method_supported,
        target,
        path,
        query,
        headers,
        body,
    )


def _validate_response_header(name, value):
    if type(name) is not str or type(value) is not str or not name:
        _encode_error("invalid_response_header")
    if len(name) + 2 + len(value) > MAX_HEADER_LINE_BYTES:
        _encode_error("response_header_line_too_long")
    try:
        name_bytes = name.encode("ascii")
        value_bytes = value.encode("ascii")
    except UnicodeError:
        _encode_error("invalid_response_header")

    for item in name_bytes:
        if not _is_header_name_byte(item):
            _encode_error("invalid_response_header")
    for item in value_bytes:
        if item < 32 or item > 126:
            _encode_error("invalid_response_header")

    line = name_bytes + b": " + value_bytes + _CRLF
    if len(line) - len(_CRLF) > MAX_HEADER_LINE_BYTES:
        _encode_error("response_header_line_too_long")
    return name.lower(), line


def encode_json_response(status, body, extra_headers=None):
    """Encode a bounded HTTP/1.1 response around already-encoded JSON bytes.

    The caller cannot replace the framing or security headers.  ``body`` must
    be immutable bytes so this layer never performs implicit JSON or text
    serialization.
    """

    if type(status) is not int or status not in _STATUS_REASONS:
        _encode_error("unsupported_response_status")
    if type(body) is not bytes:
        _encode_error("response_body_must_be_bytes")
    if len(body) > MAX_RESPONSE_BODY_BYTES:
        _encode_error("response_body_too_large")
    if status in _BODYLESS_RESPONSE_STATUSES and body:
        _encode_error("response_body_not_allowed")

    if extra_headers is None:
        extra_items = ()
        extra_count = 0
    elif type(extra_headers) is dict:
        extra_items = extra_headers.items()
        extra_count = len(extra_headers)
    elif type(extra_headers) is tuple or type(extra_headers) is list:
        extra_items = extra_headers
        extra_count = len(extra_headers)
    else:
        _encode_error("invalid_response_headers")

    if extra_count + len(_RESPONSE_CORE_HEADER_NAMES) > MAX_RESPONSE_HEADER_COUNT:
        _encode_error("too_many_response_headers")

    if status in _BODYLESS_RESPONSE_STATUSES:
        # RFC 9110 forbids a message body on both statuses.  A 204 cannot
        # carry Content-Length, while a 304 Content-Length would have to
        # describe the selected 200 representation, which this encoder does
        # not know.  Omitting both representation framing fields is therefore
        # the only safe generic encoding.
        header_lines = [
            b"Connection: close\r\n",
            b"Cache-Control: no-store\r\n",
            b"X-Content-Type-Options: nosniff\r\n",
        ]
    else:
        body_length = str(len(body)).encode("ascii")
        header_lines = [
            b"Content-Type: application/json; charset=utf-8\r\n",
            b"Content-Length: " + body_length + b"\r\n",
            b"Connection: close\r\n",
            b"Cache-Control: no-store\r\n",
            b"X-Content-Type-Options: nosniff\r\n",
        ]
    names = {}
    for name in _RESPONSE_CORE_HEADER_NAMES:
        names[name] = True

    for item in extra_items:
        if type(item) is not tuple and type(item) is not list:
            _encode_error("invalid_response_headers")
        if len(item) != 2:
            _encode_error("invalid_response_headers")
        normalized_name, line = _validate_response_header(item[0], item[1])
        if normalized_name in names:
            _encode_error("duplicate_response_header")
        if normalized_name in _FORBIDDEN_RESPONSE_HEADERS:
            _encode_error("forbidden_response_header")
        names[normalized_name] = True
        header_lines.append(line)

    header_block_length = 2
    for line in header_lines:
        header_block_length += len(line)
    if header_block_length > MAX_RESPONSE_HEADER_BLOCK_BYTES:
        _encode_error("response_header_block_too_large")

    reason = _STATUS_REASONS[status]
    status_line = "HTTP/1.1 {} {}\r\n".format(status, reason).encode("ascii")
    return status_line + b"".join(header_lines) + _CRLF + body
