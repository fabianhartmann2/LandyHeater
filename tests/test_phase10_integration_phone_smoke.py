import unittest

from app.rest_application import RestResponse
from tools import phase10_integration_phone_smoke as smoke


class _Controller:
    requested_on = False
    request_revision = 0


class _Protocol:
    calls = 0


class _Manager:
    def __init__(self, configuration):
        self.configuration = configuration

    def snapshot(self):
        return {"configuration": self.configuration}


class _Application:
    def __init__(self, response):
        self.response = response

    def handle(self, request, peer_ip):
        return self.response


class _Request:
    def __init__(self, method, path):
        self.method = method
        self.path = path


class TestPhase10IntegrationPhoneSmoke(unittest.TestCase):
    def test_private_configuration_requires_one_real_profile_and_new_ap_key(self):
        configuration = {
            "system": {"setup_complete": True},
            "network": {
                "access_point": {"password": "NewDeviceKey!92"},
                "known_networks": [{
                    "id": "home",
                    "ssid": "Home WLAN",
                    "password": "HomeNetwork!92",
                }],
            },
        }
        self.assertTrue(
            smoke._valid_private_configuration(
                configuration, "Phase7RadioOnly!92"
            )
        )
        configuration["network"]["access_point"]["password"] = (
            "Phase7RadioOnly!92"
        )
        self.assertFalse(
            smoke._valid_private_configuration(
                configuration, "Phase7RadioOnly!92"
            )
        )

    def test_timer_shapes_are_exact_and_inactive(self):
        created = dict(smoke._EXPECTED_TIMER_CREATED)
        created["id"] = "timer-1"
        self.assertTrue(
            smoke._timer_matches(
                created, smoke._EXPECTED_TIMER_CREATED, "timer-1"
            )
        )
        created["enabled"] = True
        self.assertFalse(
            smoke._timer_matches(
                created, smoke._EXPECTED_TIMER_CREATED, "timer-1"
            )
        )
        partial = dict(smoke._EXPECTED_TIMER_PARTIAL_EDIT)
        partial["id"] = "timer-1"
        self.assertTrue(
            smoke._timer_matches(
                partial, smoke._EXPECTED_TIMER_PARTIAL_EDIT, "timer-1"
            )
        )

    def test_timer_gateway_rejects_non_timer_mutation(self):
        manager = _Manager({"timers": []})
        gateway = smoke._TimerGateway(
            _Application(RestResponse(200, {"api_version": 1})),
            _Controller(),
            _Protocol(),
            manager,
        )
        response = gateway.handle(
            _Request("PATCH", "/api/v1/settings"), "192.168.4.2"
        )
        self.assertEqual(response.status, 404)
        self.assertEqual(gateway.rejected, 1)

    def test_timer_gateway_resume_requires_a_timer_id(self):
        manager = _Manager({"timers": []})
        with self.assertRaises(ValueError):
            smoke._TimerGateway(
                _Application(RestResponse(200, {"api_version": 1})),
                _Controller(),
                _Protocol(),
                manager,
                initial_stage=1,
            )
        with self.assertRaises(ValueError):
            smoke._TimerGateway(
                _Application(RestResponse(200, {"api_version": 1})),
                _Controller(),
                _Protocol(),
                manager,
                initial_stage=2,
            )

    def test_expected_failed_delete_is_retryable_and_does_not_advance(self):
        manager = _Manager({"timers": []})
        gateway = smoke._TimerGateway(
            _Application(RestResponse(429, {
                "api_version": 1,
                "error": {"code": "rate_limited"},
            })),
            _Controller(),
            _Protocol(),
            manager,
            initial_stage=2,
            timer_id="timer-1",
        )
        response = gateway.handle(
            _Request("DELETE", "/api/v1/timers/~id/74696d65722d31"),
            "192.168.4.2",
        )
        self.assertEqual(response.status, 429)
        self.assertEqual(gateway.stage, 2)
        self.assertEqual(gateway.transient, 1)
        self.assertEqual(gateway.rejected, 0)
        self.assertEqual(gateway.last_error_code, "rate_limited")

    def test_window_and_confirmation_tokens_are_bounded(self):
        self.assertEqual(
            smoke.INTEGRATION_PROVISION_CONFIRMATION,
            "PHASE10_INTEGRATION_PROVISION_CONFIRM_V1",
        )
        self.assertEqual(
            smoke.INTEGRATION_EXERCISE_CONFIRMATION,
            "PHASE10_INTEGRATION_EXERCISE_CONFIRM_V1",
        )
        self.assertEqual(smoke._validate_window_seconds(180), 180)
        self.assertEqual(smoke._validate_window_seconds(900), 900)
        with self.assertRaises(ValueError):
            smoke._validate_window_seconds(179)
        with self.assertRaises(ValueError):
            smoke._validate_window_seconds(901)

    def test_isolated_paths_are_separate_from_product_paths(self):
        self.assertTrue(
            set(smoke._storage_paths()).isdisjoint(smoke._production_paths())
        )
        self.assertEqual(len(smoke._storage_paths()), 6)
        self.assertEqual(len(smoke._production_paths()), 6)


if __name__ == "__main__":
    unittest.main()
