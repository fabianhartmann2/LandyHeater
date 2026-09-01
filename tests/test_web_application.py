import unittest

from app.rest_application import RestResponse
from app.web_application import Phase9WebApplication
from services.http_protocol import parse_request
from services.rest_security import INGRESS_ACCESS_POINT, RestSecurityPolicy
from tools.build_web_assets import OUTPUT, render


HOST = "192.168.4.1"
PEER = "192.168.4.2"


def request(method="GET", target="/", host=HOST):
    framing = "Content-Length: 0\r\n" if method in ("POST", "PUT", "PATCH") else ""
    return parse_request(
        ("{} {} HTTP/1.1\r\nHost: {}\r\n{}\r\n".format(
            method, target, host, framing
        )).encode("ascii")
    )


class FakeRestRuntime:
    def __init__(self):
        self.security_policy = RestSecurityPolicy(
            lambda count: b"r" * count,
            (HOST,),
            INGRESS_ACCESS_POINT,
        )
        self.calls = []

    def handle(self, value, peer_ip):
        self.calls.append((value, peer_ip))
        return RestResponse(200, {"delegated": True})


class TestPhase9WebApplication(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRestRuntime()
        self.app = Phase9WebApplication(self.runtime)

    def test_root_and_every_allowlisted_asset_are_frozen_and_hardened(self):
        root = self.app.handle(request(), PEER)
        self.assertEqual(root.status, 200)
        self.assertEqual(root.content_type, "text/html; charset=utf-8")
        self.assertIn(b"Landy Heater", root.body)
        self.assertIn("default-src 'self'", root.headers["Content-Security-Policy"])
        self.assertEqual(root.headers["X-Frame-Options"], "DENY")
        for path in (
            "/assets/base.css",
            "/assets/components.css",
            "/assets/session.css",
            "/assets/setup.css",
            "/assets/i18n.js",
            "/assets/app.js",
            "/assets/home.js",
            "/assets/timers.js",
            "/assets/settings.js",
            "/assets/setup.js",
        ):
            with self.subTest(path=path):
                response = self.app.handle(request(target=path), PEER)
                self.assertEqual(response.status, 200)
                self.assertIs(type(response.body), bytes)
                self.assertLessEqual(len(response.body), 16 * 1024)
                self.assertIn("Content-Security-Policy", response.headers)

    def test_api_requests_are_delegated_without_rewriting(self):
        value = request(target="/api/v1/status")
        response = self.app.handle(value, PEER)
        self.assertEqual(response.body, {"delegated": True})
        self.assertEqual(self.runtime.calls, [(value, PEER)])

    def test_static_surface_is_closed_and_host_checked_first(self):
        cases = (
            (request(target="/missing"), 404),
            (request(method="POST"), 405),
            (request(target="/?x=1"), 400),
            (request(target="/?x=1", host="attacker.invalid"), 403),
        )
        for value, status in cases:
            with self.subTest(target=value.target, host=value.host):
                response = self.app.handle(value, PEER)
                self.assertEqual(response.status, status)
                self.assertIn("Content-Security-Policy", response.headers)

    def test_frontend_has_no_external_dependency_or_inline_executable_code(self):
        index = self.app.handle(request(), PEER).body
        self.assertNotIn(b"http://", index)
        self.assertNotIn(b"https://", index)
        self.assertNotIn(b"<style", index.lower())
        self.assertNotIn(b"<script>", index.lower())
        self.assertIn(b'data-i18n="home"', index)
        self.assertIn(b'prefers-color-scheme', self.app.handle(
            request(target="/assets/base.css"), PEER
        ).body)

    def test_generated_frozen_assets_match_the_readable_sources_exactly(self):
        self.assertEqual(OUTPUT.read_bytes(), render())

    def test_setup_assistant_is_local_bounded_and_uses_the_setup_api(self):
        index = self.app.handle(request(), PEER).body
        setup = self.app.handle(
            request(target="/assets/setup.js"), PEER
        ).body
        self.assertIn(b'id="setup-dialog"', index)
        self.assertEqual(index.count(b"data-setup-step="), 9)
        self.assertIn(b'"/api/v1/setup"', setup)
        self.assertIn(b'password_action', setup)
        self.assertIn(b'id="setup-ap-action"', index)
        self.assertIn(b'setup-network-action', setup)
        self.assertIn(b'networksError', setup)
        self.assertIn(b'apError', setup)
        self.assertIn(b'quickError', setup)
        self.assertIn(b'validateAll', setup)
        self.assertIn(b'"deferred"', setup)
        self.assertNotIn(b"localStorage", setup)
        self.assertNotIn(b"http://", setup)
        self.assertNotIn(b"https://", setup)


if __name__ == "__main__":
    unittest.main()
