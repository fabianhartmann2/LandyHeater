import ast
import unittest

from services.rest_rate_limiter import (
    RestRateLimiter,
    RestRateLimitExceeded,
    RestRateLimitUnavailable,
)


class TestRestRateLimiter(unittest.TestCase):
    def setUp(self):
        self.limiter = RestRateLimiter(lambda newer, older: newer - older)

    def test_ten_requests_per_ten_seconds_and_window_reset(self):
        for value in range(10):
            ticket = self.limiter.authorize(
                "192.168.4.2", "GET", "/api/v1/status", value
            )
            self.limiter.complete(ticket, False)
        with self.assertRaises(RestRateLimitExceeded) as caught:
            self.limiter.authorize(
                "192.168.4.2", "GET", "/api/v1/status", 9
            )
        self.assertEqual(caught.exception.retry_after_seconds, 10)
        self.assertIsNotNone(
            self.limiter.authorize(
                "192.168.4.2", "GET", "/api/v1/status", 10000
            )
        )

    def test_two_mutations_per_second_but_stop_is_never_limited(self):
        for value in (0, 1):
            self.assertIsNotNone(
                self.limiter.authorize(
                    "192.168.4.2", "POST", "/api/v1/heater/start", value
                )
            )
        with self.assertRaises(RestRateLimitExceeded):
            self.limiter.authorize(
                "192.168.4.2", "POST", "/api/v1/heater/start", 2
            )
        for _ in range(20):
            self.assertIsNone(
                self.limiter.authorize(
                    None, "POST", "/api/v1/heater/stop", None
                )
            )
        self.assertEqual(self.limiter.snapshot()["stop_bypasses"], 20)

    def test_config_cooldown_begins_only_after_confirmed_change(self):
        ticket = self.limiter.authorize(
            "192.168.4.2", "PATCH", "/api/v1/settings", 0
        )
        self.limiter.complete(ticket, False)
        ticket = self.limiter.authorize(
            "192.168.4.2", "PATCH", "/api/v1/settings", 1000
        )
        self.limiter.complete(ticket, True, 1500)
        with self.assertRaises(RestRateLimitExceeded) as caught:
            self.limiter.authorize(
                "192.168.4.2", "PATCH", "/api/v1/settings", 2000
            )
        self.assertEqual(caught.exception.retry_after_seconds, 5)
        self.assertIsNotNone(
            self.limiter.authorize(
                "192.168.4.2", "PATCH", "/api/v1/settings", 6500
            )
        )

    def test_table_is_fixed_and_stale_peer_can_be_replaced(self):
        for suffix in range(2, 6):
            self.limiter.authorize(
                "192.168.4.{}".format(suffix),
                "GET",
                "/api/v1/status",
                0,
            )
        with self.assertRaises(RestRateLimitExceeded):
            self.limiter.authorize(
                "192.168.4.6", "GET", "/api/v1/status", 1
            )
        self.assertIsNotNone(
            self.limiter.authorize(
                "192.168.4.6", "GET", "/api/v1/status", 60000
            )
        )
        self.assertEqual(self.limiter.snapshot()["peer_count"], 4)

    def test_invalid_peer_clock_and_reentrancy_fail_closed(self):
        for peer in (None, "", "192.168.004.2", "999.1.1.1", "host"):
            with self.subTest(peer=peer):
                with self.assertRaises(RestRateLimitUnavailable):
                    RestRateLimiter(lambda a, b: a - b).authorize(
                        peer, "GET", "/api/v1/status", 0
                    )

        limiter = None

        def reenter(newer, older):
            limiter.authorize(
                "192.168.4.2", "GET", "/api/v1/status", newer
            )
            return newer - older

        limiter = RestRateLimiter(reenter)
        with self.assertRaises(RestRateLimitUnavailable):
            limiter.authorize("192.168.4.2", "GET", "/api/v1/status", 0)
        self.assertTrue(limiter.faulted)

    def test_module_is_hardware_socket_and_secret_free(self):
        with open(
            "services/rest_rate_limiter.py", "r", encoding="utf-8"
        ) as handle:
            tree = ast.parse(handle.read())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertTrue(
            imports.isdisjoint(
                {"machine", "network", "socket", "board_config", "hardware", "protocol"}
            )
        )


if __name__ == "__main__":
    unittest.main()
