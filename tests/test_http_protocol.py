import ast
import inspect
import unittest

import services.http_protocol as protocol_module
from services.http_protocol import (
    MAX_BODY_BYTES,
    MAX_HEADER_BLOCK_BYTES,
    MAX_HEADER_COUNT,
    MAX_HEADER_LINE_BYTES,
    MAX_REQUEST_LINE_BYTES,
    MAX_RESPONSE_BODY_BYTES,
    MAX_STATIC_RESPONSE_BODY_BYTES,
    MAX_TARGET_BYTES,
    HttpParseError,
    HttpResponseEncodeError,
    encode_bytes_response,
    encode_json_response,
    parse_request,
)


def make_request(
    method=b"GET",
    target=b"/",
    headers=None,
    body=b"",
    version=b"HTTP/1.1",
):
    if headers is None:
        headers = ((b"Host", b"heater.local"),)
    lines = [method + b" " + target + b" " + version]
    for name, value in headers:
        lines.append(name + b": " + value)
    return b"\r\n".join(lines) + b"\r\n\r\n" + body


def assert_parse_error(testcase, raw, status=None, code=None):
    with testcase.assertRaises(HttpParseError) as caught:
        parse_request(raw)
    if status is not None:
        testcase.assertEqual(caught.exception.status, status)
    if code is not None:
        testcase.assertEqual(caught.exception.code, code)
    testcase.assertEqual(str(caught.exception), caught.exception.code)
    return caught.exception


def header_block_of_wire_size(size):
    """Build valid, distinct header lines whose wire block is ``size``."""

    lines = [b"Host: h"]
    remaining = size - (len(lines[0]) + 2) - 2
    index = 0
    while remaining:
        name = ("X-%02d" % index).encode("ascii")
        maximum_wire = MAX_HEADER_LINE_BYTES + 2
        wire_size = min(maximum_wire, remaining)
        if wire_size < len(name) + 3:
            raise AssertionError("unfillable header block")
        line_size = wire_size - 2
        lines.append(name + b":" + (b"a" * (line_size - len(name) - 1)))
        remaining -= wire_size
        index += 1
    return b"\r\n".join(lines) + b"\r\n\r\n"


class TestHttpRequestSuccess(unittest.TestCase):
    def test_get_projection_and_header_lookup(self):
        raw = make_request(
            target=b"/api/v1/status?detail=short",
            headers=((b"hOsT", b"192.168.4.1"), (b"X-Request-Id", b"abc")),
        )
        request = parse_request(raw)
        self.assertEqual(request.method, "GET")
        self.assertTrue(request.method_supported)
        self.assertEqual(request.target, "/api/v1/status?detail=short")
        self.assertEqual(request.path, "/api/v1/status")
        self.assertEqual(request.query, "detail=short")
        self.assertEqual(request.version, "HTTP/1.1")
        self.assertEqual(request.host, "192.168.4.1")
        self.assertEqual(request.header("HOST"), "192.168.4.1")
        self.assertEqual(request.header("missing", "fallback"), "fallback")
        self.assertEqual(request.body, b"")

        projected = request.to_dict()
        projected["headers"]["host"] = "changed"
        self.assertEqual(request.host, "192.168.4.1")

    def test_post_put_and_patch_require_and_preserve_exact_body(self):
        body = b'{"value":1}\n'
        headers = (
            (b"Host", b"heater.local"),
            (b"Content-Length", str(len(body)).encode("ascii")),
        )
        for method in (b"POST", b"PUT", b"PATCH"):
            with self.subTest(method=method):
                request = parse_request(make_request(method, b"/api", headers, body))
                self.assertEqual(request.body, body)

    def test_bytearray_input_and_origin_form_characters(self):
        raw = make_request(target=b"/a-b_c~d/e%20f?q=x/y?z&n=1")
        request = parse_request(bytearray(raw))
        self.assertEqual(request.path, "/a-b_c~d/e%20f")
        self.assertEqual(request.query, "q=x/y?z&n=1")

    def test_unknown_safe_method_is_retained_for_router_405(self):
        request = parse_request(make_request(method=b"OPTIONS"))
        self.assertEqual(request.method, "OPTIONS")
        self.assertFalse(request.method_supported)

        body = b"data"
        request = parse_request(make_request(
            method=b"PROPFIND",
            headers=((b"Host", b"h"), (b"Content-Length", b"4")),
            body=body,
        ))
        self.assertFalse(request.method_supported)
        self.assertEqual(request.body, body)

    def test_get_and_delete_allow_absent_or_zero_length_only(self):
        for method in (b"GET", b"DELETE"):
            for headers in (
                ((b"Host", b"h"),),
                ((b"Host", b"h"), (b"Content-Length", b"0")),
            ):
                with self.subTest(method=method, headers=headers):
                    self.assertEqual(
                        parse_request(make_request(method, headers=headers)).body,
                        b"",
                    )

    def test_target_exact_limit_is_accepted(self):
        target = b"/" + (b"a" * (MAX_TARGET_BYTES - 1))
        self.assertEqual(len(target), MAX_TARGET_BYTES)
        self.assertEqual(parse_request(make_request(target=target)).target, target.decode())

    def test_header_line_exact_limit_is_accepted(self):
        name = b"X-Fill"
        value = b"a" * (MAX_HEADER_LINE_BYTES - len(name) - 2)
        raw = make_request(headers=((b"Host", b"h"), (name, value)))
        self.assertEqual(
            len(name + b": " + value),
            MAX_HEADER_LINE_BYTES,
        )
        self.assertEqual(parse_request(raw).header("x-fill"), value.decode())

    def test_header_count_exact_limit_is_accepted(self):
        headers = [(b"Host", b"h")]
        for index in range(MAX_HEADER_COUNT - 1):
            headers.append((("X-%02d" % index).encode(), b"v"))
        self.assertEqual(len(parse_request(make_request(headers=headers)).headers), 24)

    def test_header_block_exact_limit_is_accepted(self):
        block = header_block_of_wire_size(MAX_HEADER_BLOCK_BYTES)
        self.assertEqual(len(block), MAX_HEADER_BLOCK_BYTES)
        raw = b"GET / HTTP/1.1\r\n" + block
        self.assertEqual(parse_request(raw).host, "h")

    def test_body_exact_limit_is_accepted(self):
        body = b"x" * MAX_BODY_BYTES
        raw = make_request(
            method=b"POST",
            headers=((b"Host", b"h"), (b"Content-Length", b"4096")),
            body=body,
        )
        self.assertEqual(len(parse_request(raw).body), MAX_BODY_BYTES)


class TestHttpFramingFailures(unittest.TestCase):
    def test_only_strict_crlf_is_accepted(self):
        cases = (
            b"GET / HTTP/1.1\nHost: h\n\n",
            b"GET / HTTP/1.1\rHost: h\r\r",
            b"GET / HTTP/1.1\r\nHost: h\nX: y\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost: h\rX: y\r\n\r\n",
        )
        for raw in cases:
            with self.subTest(raw=raw):
                assert_parse_error(self, raw, 400, "invalid_line_ending")

    def test_body_may_contain_arbitrary_line_endings(self):
        body = b"one\ntwo\rthree\r\nfour"
        raw = make_request(
            method=b"POST",
            headers=((b"Host", b"h"), (b"Content-Length", str(len(body)).encode())),
            body=body,
        )
        self.assertEqual(parse_request(raw).body, body)

    def test_incomplete_headers_and_body_are_rejected(self):
        assert_parse_error(self, b"GET / HTTP/1.1\r\nHost: h\r\n", 400, "incomplete_headers")
        raw = make_request(
            method=b"POST",
            headers=((b"Host", b"h"), (b"Content-Length", b"4")),
            body=b"abc",
        )
        assert_parse_error(self, raw, 400, "incomplete_body")

    def test_every_proper_prefix_of_a_valid_request_is_rejected(self):
        raw = make_request(
            method=b"PATCH",
            target=b"/api/v1/value?q=1",
            headers=((b"Host", b"h"), (b"Content-Length", b"2")),
            body=b"{}",
        )
        for cut in range(len(raw)):
            with self.subTest(cut=cut):
                assert_parse_error(self, raw[:cut])
        self.assertEqual(parse_request(raw).body, b"{}")

    def test_extra_bytes_and_pipeline_are_rejected_before_routing(self):
        assert_parse_error(
            self,
            make_request() + b"x",
            400,
            "unexpected_data_after_body",
        )
        assert_parse_error(
            self,
            make_request() + make_request(target=b"/second"),
            400,
            "unexpected_data_after_body",
        )

    def test_request_line_and_target_limits_fail_closed(self):
        raw = (b"A" * (MAX_REQUEST_LINE_BYTES + 1)) + b"\r\n\r\n"
        assert_parse_error(self, raw, 414, "request_line_too_long")
        target = b"/" + (b"a" * MAX_TARGET_BYTES)
        assert_parse_error(self, make_request(target=target), 414, "target_too_long")

    def test_header_line_block_and_count_over_limit(self):
        name = b"X-Fill"
        value = b"a" * (MAX_HEADER_LINE_BYTES - len(name) - 1)
        assert_parse_error(
            self,
            make_request(headers=((b"Host", b"h"), (name, value))),
            431,
            "header_line_too_long",
        )

        block = header_block_of_wire_size(MAX_HEADER_BLOCK_BYTES)
        enlarged = block[:-4] + b"a" + block[-4:]
        self.assertEqual(len(enlarged), MAX_HEADER_BLOCK_BYTES + 1)
        assert_parse_error(
            self,
            b"GET / HTTP/1.1\r\n" + enlarged,
            431,
            "header_block_too_large",
        )

        headers = [(b"Host", b"h")]
        for index in range(MAX_HEADER_COUNT):
            headers.append((("X-%02d" % index).encode(), b"v"))
        assert_parse_error(self, make_request(headers=headers), 431, "too_many_headers")


class TestHttpRequestLineAndHeaderValidation(unittest.TestCase):
    def test_invalid_request_line_shapes_and_version(self):
        cases = (
            b"GET  / HTTP/1.1\r\nHost: h\r\n\r\n",
            b"GET / HTTP/1.1 extra\r\nHost: h\r\n\r\n",
            b"GET / HTTP/1.0\r\nHost: h\r\n\r\n",
            b"get / HTTP/1.1\r\nHost: h\r\n\r\n",
            b"BAD-METHOD / HTTP/1.1\r\nHost: h\r\n\r\n",
        )
        for raw in cases:
            with self.subTest(raw=raw):
                assert_parse_error(self, raw, 400)

    def test_origin_form_and_uri_syntax_are_strict(self):
        targets = (
            b"http://heater.local/",
            b"*",
            b"",
            b"/fragment#part",
            b"/back\\slash",
            b"/space here",
            b"/non-ascii-\xff",
            b"/bad%",
            b"/bad%0",
            b"/bad%xx",
        )
        for target in targets:
            with self.subTest(target=target):
                assert_parse_error(self, make_request(target=target), 400)

    def test_missing_empty_and_case_insensitive_duplicate_host(self):
        assert_parse_error(self, make_request(headers=()), 400, "missing_host")
        assert_parse_error(self, make_request(headers=((b"Host", b""),)), 400, "missing_host")
        assert_parse_error(
            self,
            make_request(headers=((b"Host", b"a"), (b"hOsT", b"b"))),
            400,
            "duplicate_header",
        )

    def test_every_duplicate_header_is_rejected_case_insensitively(self):
        raw = make_request(headers=(
            (b"Host", b"h"),
            (b"X-Trace", b"one"),
            (b"x-tRACE", b"two"),
        ))
        assert_parse_error(self, raw, 400, "duplicate_header")

    def test_obsolete_fold_non_ascii_controls_and_bad_names_are_rejected(self):
        raw_cases = (
            b"GET / HTTP/1.1\r\nHost: h\r\n folded\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost: h\r\n\tfolded\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost: h\r\nX: \xff\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost: h\r\nX: a\tb\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost: h\r\nBad_Name: v\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost : h\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost h\r\n\r\n",
        )
        for raw in raw_cases:
            with self.subTest(raw=raw):
                assert_parse_error(self, raw, 400)

    def test_forbidden_framing_and_override_headers_are_rejected(self):
        names = (
            b"TE",
            b"Transfer-Encoding",
            b"Expect",
            b"Content-Encoding",
            b"Trailer",
            b"Upgrade",
            b"Method-Override",
            b"X-HTTP-Method",
            b"X-Method-Override",
            b"X-HTTP-Method-Override",
        )
        for name in names:
            with self.subTest(name=name):
                raw = make_request(headers=((b"Host", b"h"), (name, b"value")))
                assert_parse_error(self, raw, 400, "forbidden_header")


class TestContentLengthRules(unittest.TestCase):
    def test_post_put_patch_require_content_length_even_for_empty_body(self):
        for method in (b"POST", b"PUT", b"PATCH"):
            with self.subTest(method=method):
                assert_parse_error(
                    self,
                    make_request(method=method),
                    411,
                    "content_length_required",
                )
                request = parse_request(make_request(
                    method=method,
                    headers=((b"Host", b"h"), (b"Content-Length", b"0")),
                ))
                self.assertEqual(request.body, b"")

    def test_content_length_is_canonical_and_bounded(self):
        invalid_values = (b"", b"00", b"01", b"+1", b"-1", b"1,1", b"1.0", b"4097")
        for value in invalid_values:
            with self.subTest(value=value):
                raw = make_request(
                    method=b"POST",
                    headers=((b"Host", b"h"), (b"Content-Length", value)),
                )
                assert_parse_error(self, raw)

    def test_ordinary_space_ows_around_length_is_normalized(self):
        raw = (
            b"POST / HTTP/1.1\r\n"
            b"Host: h\r\n"
            b"Content-Length:   1   \r\n\r\nx"
        )
        self.assertEqual(parse_request(raw).body, b"x")

    def test_content_length_duplicate_and_body_on_get_delete_are_rejected(self):
        raw = make_request(
            method=b"POST",
            headers=(
                (b"Host", b"h"),
                (b"Content-Length", b"0"),
                (b"content-length", b"0"),
            ),
        )
        assert_parse_error(self, raw, 400, "duplicate_header")

        for method in (b"GET", b"DELETE"):
            with self.subTest(method=method):
                raw = make_request(
                    method=method,
                    headers=((b"Host", b"h"), (b"Content-Length", b"1")),
                    body=b"x",
                )
                assert_parse_error(self, raw, 400, "body_not_allowed")


class TestSafeErrorsAndIsolation(unittest.TestCase):
    def test_errors_expose_only_fixed_status_and_code(self):
        secret = b"do-not-reflect-this-secret"
        error = assert_parse_error(
            self,
            make_request(headers=((b"Host", b"h"),)) + secret,
            400,
            "unexpected_data_after_body",
        )
        self.assertNotIn(secret.decode(), str(error))
        self.assertEqual(set(error.__dict__), {"status", "code"})

    def test_wrong_input_types_fail_safely(self):
        for value in (None, "GET / HTTP/1.1", [], {}, memoryview(b"")):
            with self.subTest(value=type(value)):
                assert_parse_error(self, value, 400, "invalid_request_type")

    def test_protocol_module_has_no_imports_or_hardware_side_effects(self):
        tree = ast.parse(inspect.getsource(protocol_module))
        imports = (
            ast.Import,
            ast.ImportFrom,
        )
        self.assertFalse(any(isinstance(node, imports) for node in ast.walk(tree)))


class TestHttpJsonResponseEncoder(unittest.TestCase):
    def test_static_encoder_preserves_exact_content_type_and_larger_bound(self):
        body = b"x" * MAX_STATIC_RESPONSE_BODY_BYTES
        encoded = encode_bytes_response(
            200,
            body,
            "text/css; charset=utf-8",
            {"Content-Security-Policy": "default-src 'self'"},
        )
        self.assertIn(b"Content-Type: text/css; charset=utf-8\r\n", encoded)
        self.assertIn(
            b"Content-Security-Policy: default-src 'self'\r\n", encoded
        )
        self.assertTrue(encoded.endswith(body))

        for content_type, payload, code in (
            ("text/css; charset=utf-8", body + b"x", "response_body_too_large"),
            ("application/octet-stream", b"x", "unsupported_response_content_type"),
            (None, b"x", "unsupported_response_content_type"),
        ):
            with self.subTest(content_type=content_type, code=code):
                with self.assertRaises(HttpResponseEncodeError) as caught:
                    encode_bytes_response(200, payload, content_type)
                self.assertEqual(caught.exception.code, code)

    def test_exact_fixed_headers_content_length_and_body(self):
        body = b'{"ok":true}'
        encoded = encode_json_response(200, body)
        head, returned_body = encoded.split(b"\r\n\r\n", 1)
        self.assertEqual(returned_body, body)
        self.assertTrue(head.startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertIn(b"Content-Type: application/json; charset=utf-8\r\n", head + b"\r\n")
        self.assertIn(b"Content-Length: 11\r\n", head + b"\r\n")
        self.assertIn(b"Connection: close\r\n", head + b"\r\n")
        self.assertIn(b"Cache-Control: no-store\r\n", head + b"\r\n")
        self.assertIn(b"X-Content-Type-Options: nosniff", head)
        self.assertNotIn(b"Transfer-Encoding", head)

    def test_allow_header_for_405_is_bounded_and_ascii(self):
        encoded = encode_json_response(
            405,
            b'{"error":"method_not_allowed"}',
            (("Allow", "GET, POST"),),
        )
        self.assertIn(b"HTTP/1.1 405 Method Not Allowed\r\n", encoded)
        self.assertIn(b"Allow: GET, POST\r\n", encoded)

    def test_required_status_reasons_are_encoded(self):
        cases = (
            (406, b"Not Acceptable"),
            (408, b"Request Timeout"),
            (412, b"Precondition Failed"),
            (428, b"Precondition Required"),
            (501, b"Not Implemented"),
        )
        for status, reason in cases:
            with self.subTest(status=status):
                encoded = encode_json_response(status, b"{}")
                self.assertTrue(encoded.startswith(
                    b"HTTP/1.1 " + str(status).encode("ascii") + b" " + reason + b"\r\n"
                ))
                self.assertIn(b"Content-Length: 2\r\n", encoded)

    def test_204_and_304_are_strictly_bodyless(self):
        for status, reason in ((204, b"No Content"), (304, b"Not Modified")):
            with self.subTest(status=status):
                encoded = encode_json_response(status, b"")
                self.assertTrue(encoded.startswith(
                    b"HTTP/1.1 " + str(status).encode("ascii") + b" " + reason + b"\r\n"
                ))
                self.assertTrue(encoded.endswith(b"\r\n\r\n"))
                self.assertNotIn(b"\r\nContent-Length:", encoded)
                self.assertNotIn(b"\r\nContent-Type:", encoded)
                with self.assertRaises(HttpResponseEncodeError) as caught:
                    encode_json_response(status, b"{}")
                self.assertEqual(caught.exception.code, "response_body_not_allowed")

    def test_encoder_accepts_only_whitelisted_status_and_fixed_bytes(self):
        cases = (
            (418, b"{}", "unsupported_response_status"),
            (True, b"{}", "unsupported_response_status"),
            (200, "{}", "response_body_must_be_bytes"),
            (200, bytearray(b"{}"), "response_body_must_be_bytes"),
            (200, b"x" * (MAX_RESPONSE_BODY_BYTES + 1), "response_body_too_large"),
        )
        for status, body, code in cases:
            with self.subTest(status=status, body_type=type(body)):
                with self.assertRaises(HttpResponseEncodeError) as caught:
                    encode_json_response(status, body)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(str(caught.exception), code)

    def test_header_injection_non_ascii_bad_shape_and_override_are_rejected(self):
        cases = (
            (("X-Test", "ok\r\nInjected: yes"),),
            (("X-Test", "snowman-\u2603"),),
            (("Bad_Name", "value"),),
            (("Content-Length", "99"),),
            (("connection", "keep-alive"),),
            (("X-Test",),),
            ("not-a-header-pair",),
        )
        for headers in cases:
            with self.subTest(headers=headers):
                with self.assertRaises(HttpResponseEncodeError):
                    encode_json_response(200, b"{}", headers)

    def test_response_framing_headers_cannot_be_added(self):
        for name in (
            "TE",
            "Transfer-Encoding",
            "Content-Encoding",
            "Trailer",
            "Upgrade",
        ):
            with self.subTest(name=name):
                with self.assertRaises(HttpResponseEncodeError) as caught:
                    encode_json_response(200, b"{}", ((name, "chunked"),))
                self.assertEqual(caught.exception.code, "forbidden_response_header")

    def test_huge_response_header_fails_before_ascii_encoding(self):
        huge = "a" * 100000
        with self.assertRaises(HttpResponseEncodeError) as caught:
            encode_json_response(200, b"{}", (("X-Test", huge),))
        self.assertEqual(caught.exception.code, "response_header_line_too_long")

    def test_extra_header_duplicates_are_case_insensitive(self):
        with self.assertRaises(HttpResponseEncodeError) as caught:
            encode_json_response(
                200,
                b"{}",
                (("X-Test", "one"), ("x-tEST", "two")),
            )
        self.assertEqual(caught.exception.code, "duplicate_response_header")

    def test_response_header_line_count_and_block_are_bounded(self):
        long_value = "a" * MAX_HEADER_LINE_BYTES
        with self.assertRaises(HttpResponseEncodeError) as caught:
            encode_json_response(200, b"{}", (("X", long_value),))
        self.assertEqual(caught.exception.code, "response_header_line_too_long")

        many = []
        for index in range(MAX_HEADER_COUNT):
            many.append(("X-%02d" % index, "v"))
        with self.assertRaises(HttpResponseEncodeError) as caught:
            encode_json_response(200, b"{}", many)
        self.assertEqual(caught.exception.code, "too_many_response_headers")

        large_block = []
        for index in range(9):
            name = "X-%02d" % index
            large_block.append((name, "a" * (MAX_HEADER_LINE_BYTES - len(name) - 2)))
        with self.assertRaises(HttpResponseEncodeError) as caught:
            encode_json_response(200, b"{}", large_block)
        self.assertEqual(caught.exception.code, "response_header_block_too_large")

    def test_extra_headers_must_be_a_bounded_collection_of_text_pairs(self):
        invalid = ("X-Test", 1)
        for headers in (42, invalid, {"X-Test": 1}):
            with self.subTest(headers=headers):
                with self.assertRaises(HttpResponseEncodeError):
                    encode_json_response(200, b"{}", headers)


if __name__ == "__main__":
    unittest.main()
