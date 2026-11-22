import hashlib
import json
import struct
import unittest
import zlib
from pathlib import Path

from app.rest_application import RestResponse
from app.web_application import (
    IMMUTABLE_CACHE_CONTROL,
    Phase9WebApplication,
    _content_encoding,
)
from app.web_assets import (
    ASSETS,
    ASSET_VERSION,
    GZIP_ASSETS,
    VERSIONED_ASSET_PREFIX,
    asset_for_path,
    canonical_asset_path,
    etag_for_path,
    gzip_asset_for_path,
    versioned_asset_path,
)
from services.http_protocol import encode_bytes_response, parse_request
from services.rest_security import INGRESS_ACCESS_POINT, RestSecurityPolicy
from tools.build_web_assets import (
    EXPECTED_VERSION_REFERENCES,
    MAXIMUM_ASSET_BYTES,
    MAXIMUM_OPENAPI_BYTES,
    _asset_version,
    _source_rows,
)


HOST = "192.168.4.1"
PEER = "192.168.4.2"


def request(target="/", extra_headers=(), host=HOST):
    headers = [("Host", host)]
    headers.extend(extra_headers)
    block = "".join("{}: {}\r\n".format(*item) for item in headers)
    return parse_request(
        ("GET {} HTTP/1.1\r\n{}\r\n".format(target, block)).encode(
            "ascii"
        )
    )


class FakeRestRuntime:
    def __init__(self):
        self.security_policy = RestSecurityPolicy(
            lambda count: b"r" * count,
            (HOST,),
            INGRESS_ACCESS_POINT,
        )

    def handle(self, value, peer_ip):
        return RestResponse(200, {"delegated": True})


class TestWebStaticCache(unittest.TestCase):
    def setUp(self):
        self.app = Phase9WebApplication(FakeRestRuntime())

    def test_generated_gzip_is_fixed_bounded_and_exact(self):
        self.assertEqual(len(ASSETS), len(GZIP_ASSETS))
        for identity, compressed in zip(ASSETS, GZIP_ASSETS):
            route, content_type, payload = identity
            gzip_route, gzip_type, gzip_payload = compressed
            with self.subTest(route=route):
                self.assertEqual((gzip_route, gzip_type), (route, content_type))
                self.assertEqual(
                    gzip_payload[:10],
                    b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff",
                )
                self.assertLess(len(gzip_payload), len(payload))
                self.assertLessEqual(len(gzip_payload), 16 * 1024)
                self.assertEqual(
                    zlib.decompress(gzip_payload, 16 + zlib.MAX_WBITS),
                    payload,
                )
                crc, size = struct.unpack("<II", gzip_payload[-8:])
                self.assertEqual(crc, zlib.crc32(payload) & 0xFFFFFFFF)
                self.assertEqual(size, len(payload) & 0xFFFFFFFF)
                self.assertEqual(
                    etag_for_path(route),
                    '"{}"'.format(hashlib.sha256(payload).hexdigest()),
                )
                self.assertEqual(
                    etag_for_path(route, "gzip"),
                    '"{}"'.format(
                        hashlib.sha256(gzip_payload).hexdigest()
                    ),
                )
                self.assertNotEqual(
                    etag_for_path(route), etag_for_path(route, "gzip")
                )

    def test_bundle_version_changes_for_every_logical_asset(self):
        rows = _source_rows()
        self.assertEqual(_asset_version(rows), ASSET_VERSION)
        for index, row in enumerate(rows):
            changed = list(rows)
            changed[index] = (row[0], row[1], row[2] + b"\n")
            with self.subTest(route=row[0]):
                self.assertNotEqual(_asset_version(changed), ASSET_VERSION)

    def test_versioned_paths_are_exact_and_placeholders_are_absent(self):
        self.assertEqual(len(ASSET_VERSION), 64)
        self.assertEqual(
            VERSIONED_ASSET_PREFIX,
            "/assets/v-{}/".format(ASSET_VERSION),
        )
        references = 0
        for route, content_type, payload in ASSETS:
            self.assertNotIn(b"__LANDY_ASSET_VERSION__", payload)
            if route.startswith("/assets/"):
                versioned = versioned_asset_path(route)
                self.assertTrue(versioned.startswith(VERSIONED_ASSET_PREFIX))
                self.assertEqual(canonical_asset_path(versioned), route)
            offset = 0
            while True:
                offset = payload.find(b"/assets/", offset)
                if offset < 0:
                    break
                references += 1
                self.assertTrue(
                    payload.startswith(
                        VERSIONED_ASSET_PREFIX.encode("ascii"), offset
                    )
                )
                offset += len(b"/assets/")
        self.assertEqual(references, EXPECTED_VERSION_REFERENCES)

    def test_documentation_assets_have_explicit_frozen_size_budgets(self):
        assets = {route: payload for route, _, payload in ASSETS}
        self.assertTrue({
            "/openapi.json",
            "/assets/api-docs.js",
            "/assets/api-console.js",
            "/assets/api-docs.css",
        }.issubset(assets))
        self.assertLessEqual(MAXIMUM_OPENAPI_BYTES, 2 * MAXIMUM_ASSET_BYTES)
        self.assertLessEqual(
            len(assets["/openapi.json"]), MAXIMUM_OPENAPI_BYTES
        )
        self.assertLessEqual(
            len(assets["/assets/api-docs.js"]), MAXIMUM_ASSET_BYTES
        )
        self.assertLessEqual(
            len(assets["/assets/api-console.js"]), MAXIMUM_ASSET_BYTES
        )
        self.assertLessEqual(
            len(assets["/assets/api-docs.css"]), MAXIMUM_ASSET_BYTES
        )

    def test_openapi_is_static_revalidatable_and_station_readable(self):
        content_type, payload = asset_for_path("/openapi.json")
        self.assertEqual(content_type, "application/json; charset=utf-8")
        self.assertEqual(json.loads(payload), json.loads(
            (Path(__file__).resolve().parent.parent / "web" / "openapi.json")
            .read_text(encoding="utf-8")
        ))
        self.assertIsNone(versioned_asset_path("/openapi.json"))

        identity = self.app.handle(request("/openapi.json"), PEER)
        self.assertEqual(identity.status, 200)
        self.assertEqual(identity.body, payload)
        self.assertEqual(identity.cache_control, "no-cache")
        self.assertEqual(identity.headers["Vary"], "Accept-Encoding")
        self.assertEqual(identity.headers["ETag"], etag_for_path(
            "/openapi.json"
        ))
        wire = encode_bytes_response(
            identity.status,
            identity.body,
            identity.content_type,
            identity.headers,
            identity.cache_control,
            identity.content_encoding,
        )
        self.assertTrue(wire.endswith(payload))
        self.assertIn(
            b"Content-Type: application/json; charset=utf-8\r\n", wire
        )

        station = self.app.handle(
            request("/openapi.json", host="10.0.0.17"),
            "10.0.0.2",
            "sta",
            "10.0.0.17",
        )
        self.assertEqual(station.status, 200)
        self.assertEqual(station.body, payload)

        revalidated = self.app.handle(
            request(
                "/openapi.json",
                (("If-None-Match", identity.headers["ETag"]),),
            ),
            PEER,
        )
        self.assertEqual(revalidated.status, 304)
        self.assertEqual(revalidated.body, b"")
        self.assertEqual(revalidated.cache_control, "no-cache")

    def test_accept_encoding_quality_selection_is_bounded(self):
        cases = (
            (None, None),
            ("", None),
            ("br", None),
            ("gzip, deflate, br", "gzip"),
            ("GZip", "gzip"),
            ("gzip;q=0", None),
            ("gzip;q=0.5", None),
            ("identity;q=0, gzip;q=0.5", "gzip"),
            ("gzip;q=0, *;q=1", None),
            ("identity;q=0, *;q=0.5", "gzip"),
            ("*", "gzip"),
            ("*;q=0", False),
            ("identity;q=0, gzip;q=0", False),
            ("gzip;q=2", None),
            ("gzip;q=0.5;q=1", None),
        )
        for header, expected in cases:
            with self.subTest(header=header):
                self.assertEqual(_content_encoding(header), expected)

    def test_identity_and_gzip_have_separate_validators(self):
        path = "/assets/app.js"
        content_type, payload = asset_for_path(path)
        gzip_type, gzip_payload = gzip_asset_for_path(path)
        identity_etag = etag_for_path(path)
        gzip_etag = etag_for_path(path, "gzip")

        identity = self.app.handle(request(path), PEER)
        self.assertEqual(identity.status, 200)
        self.assertEqual(identity.body, payload)
        self.assertEqual(identity.content_type, content_type)
        self.assertIsNone(identity.content_encoding)
        self.assertEqual(identity.headers["ETag"], identity_etag)
        self.assertEqual(identity.headers["Vary"], "Accept-Encoding")
        self.assertEqual(identity.cache_control, "no-cache")

        gzip = self.app.handle(
            request(path, (("Accept-Encoding", "gzip, deflate, br"),)),
            PEER,
        )
        self.assertEqual(gzip.status, 200)
        self.assertEqual(gzip.body, gzip_payload)
        self.assertEqual(gzip.content_type, gzip_type)
        self.assertEqual(gzip.content_encoding, "gzip")
        self.assertEqual(gzip.headers["ETag"], gzip_etag)
        self.assertEqual(gzip.headers["Vary"], "Accept-Encoding")

        stale_representation = self.app.handle(
            request(
                path,
                (
                    ("Accept-Encoding", "gzip"),
                    ("If-None-Match", identity_etag),
                ),
            ),
            PEER,
        )
        self.assertEqual(stale_representation.status, 200)
        self.assertEqual(stale_representation.body, gzip_payload)

        revalidated = self.app.handle(
            request(
                path,
                (
                    ("Accept-Encoding", "gzip"),
                    ("If-None-Match", gzip_etag),
                ),
            ),
            PEER,
        )
        self.assertEqual(revalidated.status, 304)
        self.assertEqual(revalidated.body, b"")
        self.assertEqual(revalidated.content_encoding, "gzip")
        wire = encode_bytes_response(
            revalidated.status,
            revalidated.body,
            revalidated.content_type,
            revalidated.headers,
            revalidated.cache_control,
            revalidated.content_encoding,
        )
        self.assertIn(b"HTTP/1.1 304 Not Modified\r\n", wire)
        self.assertIn(b"Content-Encoding: gzip\r\n", wire)
        self.assertIn(b"Vary: Accept-Encoding\r\n", wire)
        self.assertNotIn(b"Content-Length:", wire)
        self.assertNotIn(b"Content-Type:", wire)

    def test_current_version_is_immutable_but_unversioned_is_not(self):
        canonical = "/assets/home.js"
        versioned = versioned_asset_path(canonical)
        current = self.app.handle(request(versioned), PEER)
        self.assertEqual(current.status, 200)
        self.assertEqual(current.body, asset_for_path(canonical)[1])
        self.assertEqual(current.cache_control, IMMUTABLE_CACHE_CONTROL)

        cached = self.app.handle(
            request(versioned, (("If-None-Match", current.headers["ETag"]),)),
            PEER,
        )
        self.assertEqual(cached.status, 304)
        self.assertEqual(cached.cache_control, IMMUTABLE_CACHE_CONTROL)

        compatibility = self.app.handle(request(canonical), PEER)
        self.assertEqual(compatibility.status, 200)
        self.assertEqual(compatibility.cache_control, "no-cache")

    def test_only_the_exact_selected_validator_matches(self):
        path = versioned_asset_path("/assets/app.js")
        selected = etag_for_path("/assets/app.js", "gzip")
        identity = etag_for_path("/assets/app.js")
        for value in (
            "*",
            "W/" + selected,
            selected + ", " + selected,
            selected[1:-1],
            identity,
            '"different"',
        ):
            with self.subTest(value=value):
                response = self.app.handle(
                    request(
                        path,
                        (
                            ("Accept-Encoding", "gzip"),
                            ("If-None-Match", value),
                        ),
                    ),
                    PEER,
                )
                self.assertEqual(response.status, 200)
                self.assertTrue(response.body)

    def test_stale_or_malformed_version_never_resolves_to_current_bytes(self):
        stale_prefix = "/assets/v-{}/".format("0" * 64)
        cases = (
            stale_prefix + "app.js",
            VERSIONED_ASSET_PREFIX + "../app.js",
            VERSIONED_ASSET_PREFIX + "%2fapp.js",
            VERSIONED_ASSET_PREFIX + "nested/app.js",
            VERSIONED_ASSET_PREFIX + "not-present.js",
        )
        for target in cases:
            with self.subTest(target=target):
                response = self.app.handle(request(target), PEER)
                self.assertEqual(response.status, 404)
                self.assertEqual(response.cache_control, "no-store")
                self.assertNotIn("ETag", response.headers)
                self.assertNotEqual(
                    response.body, asset_for_path("/assets/app.js")[1]
                )
        query = self.app.handle(
            request(versioned_asset_path("/assets/app.js") + "?x=1"), PEER
        )
        self.assertEqual(query.status, 400)
        self.assertEqual(query.cache_control, "no-store")

    def test_index_revalidates_and_errors_and_api_remain_no_store(self):
        root = self.app.handle(request("/"), PEER)
        index = self.app.handle(request("/index.html"), PEER)
        self.assertEqual(root.status, 200)
        self.assertEqual(root.body, index.body)
        self.assertEqual(root.headers["ETag"], index.headers["ETag"])
        self.assertEqual(root.cache_control, "no-cache")
        self.assertEqual(root.headers["Vary"], "Accept-Encoding")

        revalidated = self.app.handle(
            request("/", (("If-None-Match", root.headers["ETag"]),)), PEER
        )
        self.assertEqual(revalidated.status, 304)
        self.assertEqual(revalidated.cache_control, "no-cache")

        stale = self.app.handle(
            request("/", (("If-None-Match", '"old-index"'),)), PEER
        )
        self.assertEqual(stale.status, 200)
        self.assertEqual(stale.body, root.body)

        missing = self.app.handle(request("/missing"), PEER)
        self.assertEqual(missing.status, 404)
        self.assertEqual(missing.cache_control, "no-store")
        self.assertNotIn("ETag", missing.headers)

        delegated = self.app.handle(request("/api/v1/status"), PEER)
        self.assertEqual(delegated.status, 200)
        self.assertFalse(hasattr(delegated, "content_encoding"))

    def test_unacceptable_static_encoding_is_406_and_varies(self):
        response = self.app.handle(
            request(
                versioned_asset_path("/assets/app.js"),
                (("Accept-Encoding", "identity;q=0, gzip;q=0"),),
            ),
            PEER,
        )
        self.assertEqual(response.status, 406)
        self.assertEqual(response.cache_control, "no-store")
        self.assertEqual(response.headers["Vary"], "Accept-Encoding")

    def test_asset_lookup_keeps_compatible_two_value_api(self):
        value = asset_for_path("/assets/home.js")
        compressed = gzip_asset_for_path("/assets/home.js")
        self.assertEqual(len(value), 2)
        self.assertEqual(len(compressed), 2)
        self.assertIs(type(value[0]), str)
        self.assertIs(type(value[1]), bytes)
        self.assertIs(type(compressed[1]), bytes)
        self.assertIs(type(etag_for_path("/index.html")), str)
        self.assertIsNone(asset_for_path("/assets/not-present"))
        self.assertIsNone(gzip_asset_for_path("/assets/not-present"))
        self.assertIsNone(etag_for_path("/assets/not-present"))
        self.assertIsNone(etag_for_path("/assets/app.js", "br"))
        self.assertIsNone(versioned_asset_path("/index.html"))
        self.assertIsNone(canonical_asset_path("/assets/app.js"))


if __name__ == "__main__":
    unittest.main()
