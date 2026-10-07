import inspect
import unittest

from tools import browser_time_phone_smoke as smoke


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


class _Controller:
    requested_on = False
    request_revision = 0


class _Protocol:
    calls = 0


def trusted_body():
    return {
        "time": {
            "valid": True,
            "source": "browser",
            "volatile_browser_time": True,
            "timer_trusted": True,
            "rtc_write_pending": False,
            "rtc_commit_pending": False,
            "utc_seconds": 846000000,
            "local": {"year": 2026},
        }
    }


class TestBrowserTimePhoneSmoke(unittest.TestCase):
    def observer(self, response):
        application = _Application(response)
        return (
            smoke._ObservedWeb(application, _Controller(), _Protocol()),
            application,
        )

    def test_trusted_browser_time_requires_all_safety_fields(self):
        body = trusted_body()
        self.assertTrue(smoke._trusted_browser_time(body))
        body["time"]["rtc_write_pending"] = True
        self.assertFalse(smoke._trusted_browser_time(body))

    def test_observer_accepts_only_browser_time_mutation(self):
        observer, application = self.observer(_Response(body=trusted_body()))
        response = observer.handle(
            _Request("PUT", "/api/v1/time/browser-sync"),
            "192.168.4.2",
            "ap",
            "192.168.4.1",
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(observer.sync_attempts, 1)
        self.assertEqual(observer.sync_successes, 1)
        self.assertEqual(application.calls, 1)

        response = observer.handle(
            _Request("POST", "/api/v1/heater/start"),
            "192.168.4.2",
            "ap",
            "192.168.4.1",
        )
        self.assertEqual(response.status, 405)
        self.assertEqual(observer.blocked_mutations, 1)
        self.assertEqual(application.calls, 1)

    def test_observer_records_invalid_then_trusted_status(self):
        observer, application = self.observer(
            _Response(body={"time": {"valid": False}})
        )
        request = _Request()
        observer.handle(request, "192.168.4.2", "ap", "192.168.4.1")
        self.assertEqual(observer.invalid_status_reads, 1)
        application.response = _Response(body=trusted_body())
        observer.handle(request, "192.168.4.2", "ap", "192.168.4.1")
        self.assertEqual(observer.trusted_status_reads, 1)

    def test_runner_is_bounded_and_preserves_production_storage(self):
        source = inspect.getsource(smoke.run)
        self.assertIn("WINDOW_SECONDS * 1000", source)
        self.assertIn("production_before", source)
        self.assertIn("_remove_test_files()", source)
        self.assertIn('_sys.path.insert(0, ".frozen")', source)
        self.assertIn("_sys.path[:] = original_sys_path", source)
        self.assertIn("observer.blocked_mutations == 0", source)
        self.assertNotIn("ConfiguredHeaterRuntime", source)
        self.assertNotIn("machine.UART", source)
        self.assertNotIn("write_flash", source)
        self.assertNotIn("erase_flash", source)


if __name__ == "__main__":
    unittest.main()
