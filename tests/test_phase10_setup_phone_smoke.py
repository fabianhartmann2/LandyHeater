import unittest

from app.rest_application import RestResponse
from services.http_protocol import parse_request
from services.rest_security import INGRESS_ACCESS_POINT, RestSecurityPolicy
from tools import phase10_setup_phone_smoke as smoke


class _Controller:
    requested_on = False
    request_revision = 0


class _Protocol:
    calls = 0


class _Manager:
    def __init__(self):
        self.complete = False

    def snapshot(self):
        return {"configuration": {
            "system": {"setup_complete": self.complete},
            "network": {
                "access_point": {"password": "replacement-password"},
                "known_networks": [{
                    "id": "test",
                    "ssid": "Test WLAN",
                    "password": "station-password",
                }] if self.complete else [],
            },
        }}


class _Runtime:
    def __init__(self, manager):
        self.manager = manager
        self.security_policy = RestSecurityPolicy(
            lambda count: b"r" * count,
            (smoke.AP_IP,),
            INGRESS_ACCESS_POINT,
        )

    def handle(self, request, peer_ip):
        target = request.target
        if request.method == "PUT":
            self.manager.complete = True
            return RestResponse(200, {
                "api_version": 1,
                "changed": True,
                "system": {"setup_complete": True},
            })
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
        if target.startswith("/api/v1/timers"):
            return RestResponse(200, {
                "api_version": 1,
                "items": [],
                "offset": 0,
                "limit": 8,
            })
        return RestResponse(200, {
            "api_version": 1,
            "network": {},
            "checks": {
                "sensors": {"active_probe_performed": False},
                "autoterm": {"active_test_performed": False},
            },
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
        self.sent = bytearray()

    def send(self, payload):
        self.sent.extend(payload)
        return len(payload)

    def close(self):
        return None


class TestPhase10SetupPhoneSmoke(unittest.TestCase):
    def test_required_surface_is_exact_and_bounded(self):
        self.assertEqual(len(smoke._STATIC_TARGETS), 11)
        self.assertEqual(len(smoke._API_TARGETS), 5)
        self.assertEqual(len(smoke._READ_TARGETS), 16)
        self.assertEqual(len(set(smoke._READ_TARGETS)), 16)
        self.assertIn("/assets/setup.js", smoke._STATIC_TARGETS)
        self.assertIn("/api/v1/setup", smoke._API_TARGETS)

    def test_gateway_allows_one_setup_commit_with_replaced_credentials(self):
        manager = _Manager()
        gateway = smoke._SetupWebGateway(
            _Runtime(manager),
            _Controller(),
            _Protocol(),
            manager,
            "live-password",
            "replacement-password",
            "Test WLAN",
            "station-password",
        )
        for target in smoke._READ_TARGETS:
            response = gateway.handle(_request(target), "192.168.4.2")
            self.assertIn(response.status, (200, 204), target)
            self.assertEqual(gateway.validated[target], 1, target)
        committed = gateway.handle(
            _request("/api/v1/setup", method="PUT"), "192.168.4.2"
        )
        self.assertEqual(committed.status, 200)
        self.assertEqual(gateway.mutation_attempts, 1)
        self.assertEqual(gateway.successful_mutations, 1)
        rejected = gateway.handle(
            _request("/api/v1/setup", method="PUT"), "192.168.4.2"
        )
        self.assertEqual(rejected.status, 404)
        self.assertEqual(gateway.mutation_attempts, 2)

    def test_transport_observer_identifies_setup_put(self):
        observer = smoke._TransportObserver()
        client = observer.claim_client(_Port())
        client._observe_request(
            b"PUT /api/v1/setup HTTP/1.1\r\nHost: x\r\n\r\n"
        )
        client.send(b"HTTP/1.1 200 OK\r\n\r\nbody")
        client.close()
        self.assertEqual(observer.completed, {smoke._SETUP_MUTATION: 1})

    def test_missing_target_diagnostics_separate_application_and_wire(self):
        class Gateway:
            validated = {target: 1 for target in smoke._READ_TARGETS}

        class Observer:
            completed = {target: 1 for target in smoke._READ_TARGETS}

        missing = smoke._READ_TARGETS[-1]
        del Gateway.validated[smoke._READ_TARGETS[0]]
        del Observer.completed[missing]
        self.assertEqual(
            smoke._missing_targets(Gateway(), Observer()),
            ((smoke._READ_TARGETS[0],), (missing,)),
        )

    def test_commit_counter_requires_one_config_write_only(self):
        self.assertTrue(smoke._committed_once((2, 2, 2, 2), (3, 2, 3, 2)))
        self.assertFalse(smoke._committed_once((2, 2, 2, 2), (4, 2, 4, 2)))
        self.assertFalse(smoke._committed_once((2, 2, 2, 2), (3, 3, 3, 3)))

    def test_exact_confirmation_and_window_are_fail_closed(self):
        self.assertEqual(
            smoke.PHASE10_SETUP_PHONE_CONFIRMATION,
            "PHASE10_SETUP_PHONE_CONFIRM_V1",
        )
        with self.assertRaises(ValueError):
            smoke._phase9._validate_window_seconds(59)


if __name__ == "__main__":
    unittest.main()
