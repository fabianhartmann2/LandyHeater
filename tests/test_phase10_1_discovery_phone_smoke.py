import unittest

from app.web_application import WebResponse
from tools import phase10_1_discovery_phone_smoke as smoke


class _Controller:
    requested_on = False


class _Protocol:
    calls = 0


class _Application:
    def __init__(self, response):
        self.response = response

    def handle(self, request, peer_ip, ingress, local_ip):
        return self.response


class _Request:
    def __init__(self, method, path, host="heater.local"):
        self.method = method
        self.path = path
        self.headers = {"host": host}


class TestPhase101DiscoveryPhoneSmoke(unittest.TestCase):
    def test_test_configuration_keeps_stable_ap_and_one_station(self):
        configuration = {
            "system": {"setup_complete": True},
            "network": {
                "access_point": {"password": smoke.AP_PASSWORD},
                "known_networks": [{"ssid": "home", "password": "private123"}],
            },
        }
        self.assertTrue(smoke._configured_for_test(configuration))
        configuration["network"]["access_point"]["password"] = "changed123"
        self.assertFalse(smoke._configured_for_test(configuration))

    def test_observer_separates_station_read_and_denials(self):
        response = WebResponse(200, b"ok", "text/html; charset=utf-8")
        observer = smoke._ObservedWeb(
            _Application(response), _Controller(), _Protocol(), "192.168.1.20"
        )
        observer.handle(
            _Request("GET", "/"), "192.168.1.4", "sta", "192.168.1.20"
        )
        self.assertEqual(observer.sta_root, 1)
        response.status = 503
        observer.handle(
            _Request("GET", "/api/v1/security-context"),
            "192.168.1.4",
            "sta",
            "192.168.1.20",
        )
        observer.handle(
            _Request("POST", "/api/v1/heater/stop"),
            "192.168.1.4",
            "sta",
            "192.168.1.20",
        )
        self.assertEqual(observer.sta_security_denied, 1)
        self.assertEqual(observer.sta_mutation_denied, 1)

    def test_window_is_bounded(self):
        self.assertEqual(smoke._window(180), 180)
        self.assertEqual(smoke._window(900), 900)
        with self.assertRaises(ValueError):
            smoke._window(179)
        with self.assertRaises(ValueError):
            smoke._window(901)


if __name__ == "__main__":
    unittest.main()
