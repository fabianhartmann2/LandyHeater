import unittest

from app.discovery_composition import (
    ConfiguredDiscoveryRuntime,
    DiscoveryRuntimeError,
    build_discovery_runtime,
)


class FakeService:
    def __init__(self, steps=()):
        self.steps = list(steps)
        self.calls = []

    def start(self):
        self.calls.append("start")
        return True

    def step(self):
        self.calls.append("step")
        return self.steps.pop(0) if self.steps else False

    def deinit(self):
        self.calls.append("deinit")

    def snapshot(self):
        return {"calls": len(self.calls)}


class TestDiscoveryComposition(unittest.TestCase):
    def test_builder_creates_two_explicit_port_80_http_ingresses(self):
        class Security:
            def validate_read(self, headers, ingress=None, local_ip=None):
                return "heater.local"

        class Rest:
            security_policy = Security()

            def handle(self, request, peer_ip, ingress=None, local_ip=None):
                raise AssertionError("construction must not dispatch")

        calls = []

        def unopened(name):
            def factory():
                calls.append(name)
                raise AssertionError("construction must not open sockets")

            return factory

        runtime = build_discovery_runtime(
            Rest(),
            "192.168.4.1",
            station_address="192.168.1.17",
            ap_http_socket_factory=unopened("ap"),
            station_http_socket_factory=unopened("sta"),
            dns_socket_factory=unopened("dns"),
        )
        self.assertEqual(calls, [])
        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["ap_http"]["bind_address"], "192.168.4.1")
        self.assertEqual(snapshot["ap_http"]["request_ingress"], "ap")
        self.assertEqual(
            snapshot["station_http"]["bind_address"], "192.168.1.17"
        )
        self.assertEqual(snapshot["station_http"]["request_ingress"], "sta")
        self.assertEqual(runtime.ap_http_server.port, 80)
        self.assertEqual(runtime.station_http_server.port, 80)

    def test_builder_rejects_same_ap_and_station_address(self):
        class Security:
            def validate_read(self, headers, ingress=None, local_ip=None):
                return "192.168.4.1"

        class Rest:
            security_policy = Security()

            def handle(self, request, peer_ip, ingress=None, local_ip=None):
                return None

        with self.assertRaises(ValueError):
            build_discovery_runtime(
                Rest(), "192.168.4.1", station_address="192.168.4.1"
            )

    def test_construction_is_inert_and_steps_alternate_fairly(self):
        ap_http = FakeService((True, False))
        station_http = FakeService((False, True))
        dns = FakeService((False, True))
        runtime = ConfiguredDiscoveryRuntime(ap_http, dns, station_http)
        self.assertEqual(ap_http.calls, [])
        self.assertEqual(station_http.calls, [])
        self.assertEqual(dns.calls, [])
        self.assertTrue(runtime.start())
        self.assertTrue(runtime.step())
        self.assertFalse(runtime.step())
        self.assertFalse(runtime.step())
        self.assertFalse(runtime.step())
        self.assertTrue(runtime.step())
        self.assertTrue(runtime.step())
        self.assertEqual(ap_http.calls, ["start", "step", "step"])
        self.assertEqual(station_http.calls, ["start", "step", "step"])
        self.assertEqual(dns.calls, ["start", "step", "step"])
        snapshot = runtime.snapshot()
        self.assertIsNotNone(snapshot["station_http"])
        self.assertEqual(snapshot["next_service"], "ap_http")

    def test_ap_only_runtime_omits_station_listener(self):
        ap_http = FakeService()
        dns = FakeService()
        runtime = ConfiguredDiscoveryRuntime(ap_http, dns)
        self.assertTrue(runtime.start())
        self.assertFalse(runtime.step())
        self.assertFalse(runtime.step())
        self.assertIsNone(runtime.snapshot()["station_http"])

    def test_cleanup_closes_dns_before_http_and_is_terminal(self):
        order = []

        class Ordered(FakeService):
            def __init__(self, name):
                super().__init__()
                self.name = name

            def deinit(self):
                order.append(self.name)

        runtime = ConfiguredDiscoveryRuntime(
            Ordered("ap_http"), Ordered("dns"), Ordered("station_http")
        )
        runtime.start()
        self.assertIsNone(runtime.deinit())
        self.assertEqual(order, ["dns", "station_http", "ap_http"])
        self.assertFalse(runtime.step())
        with self.assertRaises(DiscoveryRuntimeError):
            runtime.start()


if __name__ == "__main__":
    unittest.main()
