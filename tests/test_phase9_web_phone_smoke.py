import unittest

from app.rest_application import RestResponse
from services.http_protocol import parse_request
from services.rest_security import INGRESS_ACCESS_POINT, RestSecurityPolicy
from tools import phase9_web_phone_smoke as smoke


class _Controller:
    requested_on = False
    request_revision = 0


class _Protocol:
    calls = 0


class _Runtime:
    def __init__(self):
        self.security_policy = RestSecurityPolicy(
            lambda count: b"r" * count,
            (smoke.AP_IP,),
            INGRESS_ACCESS_POINT,
        )

    def handle(self, request, peer_ip):
        target = request.target
        if target == "/api/v1/security-context":
            return RestResponse(200, {
                "api_version": 1,
                "csrf_token": "a" * 64,
                "mutation_api_available": True,
            })
        if target == "/api/v1/status":
            return RestResponse(200, {
                "api_version": 1,
                "heater": {},
                "network": {},
            })
        if target == "/api/v1/settings":
            return RestResponse(200, {
                "api_version": 1,
                "heater": {},
                "network": {},
            })
        return RestResponse(200, {
            "api_version": 1,
            "items": [],
            "offset": 0,
            "limit": 8,
        })


def _request(target, method="GET"):
    framing = "Content-Length: 0\r\n" if method != "GET" else ""
    return parse_request(
        ("{} {} HTTP/1.1\r\nHost: {}\r\n{}\r\n".format(
            method, target, smoke.AP_IP, framing
        )).encode("ascii")
    )


class _Port:
    def __init__(self):
        self.closed = False
        self.sent = bytearray()

    def recv(self, maximum):
        return b""

    def send(self, payload):
        self.sent.extend(payload)
        return len(payload)

    def close(self):
        self.closed = True
        return None

    def setblocking(self, value):
        return None


class TestPhase9WebPhoneSmoke(unittest.TestCase):
    def test_required_surface_is_exact_and_bounded(self):
        self.assertEqual(len(smoke._STATIC_TARGETS), 9)
        self.assertEqual(len(smoke._API_TARGETS), 4)
        self.assertEqual(len(smoke._REQUIRED_TARGETS), 13)
        self.assertEqual(len(set(smoke._REQUIRED_TARGETS)), 13)
        self.assertIn("/api/v1/status", smoke._API_TARGETS)
        self.assertNotIn("/api/v1/diagnostics", smoke._API_TARGETS)

    def test_gateway_validates_every_expected_read_and_blocks_mutations(self):
        gateway = smoke._ReadOnlyWebGateway(
            _Runtime(), _Controller(), _Protocol(), "test-password"
        )
        for target in smoke._REQUIRED_TARGETS:
            response = gateway.handle(_request(target), "192.168.4.2")
            self.assertIn(response.status, (200, 204), target)
            self.assertEqual(gateway.validated[target], 1, target)
        rejected = gateway.handle(
            _request("/api/v1/heater/stop", method="POST"),
            "192.168.4.2",
        )
        self.assertEqual(rejected.status, 404)
        self.assertEqual(gateway.mutation_attempts, 1)

    def test_transport_observer_binds_completion_to_request_target(self):
        observer = smoke._TransportObserver()
        port = _Port()
        client = observer.claim_client(port)
        client._observe_request(b"GET /assets/app.js HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertEqual(client.send(b"HTTP/1.1 200 OK\r\n\r\nbody"), 23)
        self.assertIsNone(client.close())
        self.assertEqual(observer.completed, {"/assets/app.js": 1})
        self.assertEqual(observer.accepted, 1)
        self.assertEqual(observer.closed_count, 1)
        self.assertEqual(observer.open_clients(), 0)

    def test_exact_confirmation_and_window_are_fail_closed(self):
        with self.assertRaises(ValueError):
            smoke._validate_window_seconds(59)
        with self.assertRaises(ValueError):
            smoke._validate_window_seconds(301)
        self.assertEqual(smoke._validate_window_seconds(300), 300)
        self.assertEqual(
            smoke.PHASE9_WEB_PHONE_CONFIRMATION,
            "PHASE9_WEB_PHONE_CONFIRM_V1",
        )

    def test_failure_diagnostics_contain_only_bounded_counters(self):
        class _Snapshot:
            failure_stage = "phase9_http_step"
            gateway = None
            socket_observer = None

        class _Server:
            @staticmethod
            def snapshot():
                return {
                    "accepted": 1,
                    "completed": 0,
                    "client_count": 1,
                    "parse_errors": 0,
                    "timeouts": 0,
                    "socket_errors": 0,
                    "faulted": False,
                }

        class _State:
            context = _Snapshot()
            server = _Server()

        import contextlib
        import io

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            smoke._emit_failure(_State(), RuntimeError("private detail"))
        rendered = output.getvalue()
        self.assertIn("stage=phase9_http_step", rendered)
        self.assertIn("error_type=RuntimeError", rendered)
        self.assertNotIn("private detail", rendered)


if __name__ == "__main__":
    unittest.main()
