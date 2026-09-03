import unittest

from app.rest_application import RestResponse
from app.web_application import Phase9WebApplication
from services.http_protocol import encode_bytes_response, parse_request
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

    def test_known_ap_captive_probes_redirect_but_station_does_not(self):
        for target in (
            "/generate_204",
            "/hotspot-detect.html",
            "/connecttest.txt",
            "/canonical.html",
        ):
            with self.subTest(target=target):
                response = self.app.handle(
                    request(target=target, host="probe.invalid"),
                    PEER,
                    "ap",
                    HOST,
                )
                self.assertEqual(response.status, 302)
                self.assertEqual(response.headers["Location"], "http://192.168.4.1/")
                encoded = encode_bytes_response(
                    response.status,
                    response.body,
                    response.content_type,
                    response.headers,
                )
                self.assertEqual(encoded.count(b"Cache-Control: no-store\r\n"), 1)
                station = self.app.handle(
                    request(target=target, host="probe.invalid"),
                    "10.0.0.2",
                    "sta",
                    "10.0.0.17",
                )
                self.assertEqual(station.status, 403)

    def test_station_reads_accept_destination_ip_and_api_context(self):
        class IngressRuntime(FakeRestRuntime):
            def handle(self, value, peer_ip, ingress=None, local_ip=None):
                self.calls.append((value, peer_ip, ingress, local_ip))
                return RestResponse(200, {"delegated": True})

        runtime = IngressRuntime()
        app = Phase9WebApplication(runtime)
        static = app.handle(
            request(host="10.0.0.17"), "10.0.0.2", "sta", "10.0.0.17"
        )
        self.assertEqual(static.status, 200)
        api_request = request(target="/api/v1/status", host="10.0.0.17")
        api = app.handle(api_request, "10.0.0.2", "sta", "10.0.0.17")
        self.assertEqual(api.status, 200)
        self.assertEqual(runtime.calls[-1][2:], ("sta", "10.0.0.17"))

    def test_frontend_boots_reads_without_requesting_mutation_token(self):
        app = self.app.handle(request(target="/assets/app.js"), PEER).body
        boot = app.split(b"async function boot()", 1)[1]
        self.assertIn(b"await L.refresh()", boot)
        self.assertNotIn(b"await L.security();await L.refresh()", boot)

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

    def test_bodyless_mutations_omit_the_fetch_body_member(self):
        app = self.app.handle(
            request(target="/assets/app.js"), PEER
        ).body
        self.assertIn(b"options={method,headers}", app)
        self.assertIn(b"options.body=JSON.stringify(payload)", app)
        self.assertIn(b"L.request(path,options)", app)
        self.assertNotIn(b'let body=""', app)
        self.assertNotIn(b"{method,headers,body}", app)


if __name__ == "__main__":
    unittest.main()
