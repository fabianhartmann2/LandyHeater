import inspect
import unittest

from tools import phase13_sensor_web_phone_smoke as smoke


class _Request:
    def __init__(self, method="GET", path="/api/v1/status"):
        self.method = method
        self.path = path


class _Response:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body


class _Application:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def handle(self, request, peer_ip, ingress, local_ip):
        self.calls += 1
        return self.response


def status_body(value=21.5, health="ok"):
    return {
        "temperatures": {
            role: {"value_c": value, "health": health}
            for role in ("roof_tent", "cabin", "outside")
        }
    }


class TestPhase13SensorWebPhoneSmoke(unittest.TestCase):
    def test_temperature_validation_requires_three_healthy_float_values(self):
        self.assertEqual(
            smoke._valid_temperatures(status_body()),
            {"roof_tent": 21.5, "cabin": 21.5, "outside": 21.5},
        )
        self.assertIsNone(smoke._valid_temperatures(status_body(21, "ok")))
        self.assertIsNone(smoke._valid_temperatures(status_body(21.5, "stale")))
        missing = status_body()
        del missing["temperatures"]["outside"]
        self.assertIsNone(smoke._valid_temperatures(missing))

    def test_observer_counts_only_valid_read_responses(self):
        application = _Application(_Response(body=status_body()))
        observer = smoke._ObservedWeb(application)
        response = observer.handle(
            _Request(), "192.168.4.2", "ap", "192.168.4.1"
        )
        self.assertIs(response, application.response)
        self.assertEqual(observer.valid_status_reads, 1)
        self.assertEqual(observer.values["outside"], 21.5)
        self.assertEqual(application.calls, 1)

    def test_observer_blocks_mutation_before_product_dispatch(self):
        application = _Application(_Response())
        observer = smoke._ObservedWeb(application)
        response = observer.handle(
            _Request("POST", "/api/v1/heater/start"),
            "192.168.4.2",
            "ap",
            "192.168.4.1",
        )
        self.assertEqual(response.status, 405)
        self.assertEqual(observer.mutations, 1)
        self.assertEqual(application.calls, 0)

    def test_observer_handles_read_only_portal_probe_without_dispatch(self):
        application = _Application(_Response())
        observer = smoke._ObservedWeb(application)
        for method in ("HEAD", "OPTIONS"):
            response = observer.handle(
                _Request(method, "/"),
                "192.168.4.2",
                "ap",
                "192.168.4.1",
            )
            self.assertEqual(response.status, 405)
        self.assertEqual(observer.read_only_probes, 2)
        self.assertEqual(observer.mutations, 0)
        self.assertEqual(application.calls, 0)

    def test_http_health_accepts_only_accounted_browser_cancelled_send(self):
        healthy = {
            "faulted": False,
            "parse_errors": 0,
            "socket_errors": 1,
            "accepted": 3,
            "completed": 2,
            "client_count": 0,
            "reentries": 0,
            "last_error": "client_send_failed",
        }
        self.assertTrue(smoke._http_transport_healthy(healthy))
        unhealthy = dict(healthy)
        unhealthy["last_error"] = "application_handle_failed"
        self.assertFalse(smoke._http_transport_healthy(unhealthy))

    def test_runner_is_bounded_read_only_and_has_ordered_cleanup(self):
        source = inspect.getsource(smoke.run)
        self.assertIn("WINDOW_SECONDS * 1000", source)
        self.assertIn("production_before", source)
        self.assertIn("_remove_test_files()", source)
        self.assertIn("observer.mutations == 0", source)
        self.assertNotIn("write_flash", source)
        self.assertNotIn("erase_flash", source)


if __name__ == "__main__":
    unittest.main()
