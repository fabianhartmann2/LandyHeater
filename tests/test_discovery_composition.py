import unittest

from app.discovery_composition import (
    ConfiguredDiscoveryRuntime,
    DiscoveryRuntimeError,
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
    def test_construction_is_inert_and_steps_alternate_fairly(self):
        http = FakeService((True, False))
        dns = FakeService((False, True))
        runtime = ConfiguredDiscoveryRuntime(http, dns)
        self.assertEqual(http.calls, [])
        self.assertEqual(dns.calls, [])
        self.assertTrue(runtime.start())
        self.assertTrue(runtime.step())
        self.assertFalse(runtime.step())
        self.assertFalse(runtime.step())
        self.assertTrue(runtime.step())
        self.assertEqual(http.calls, ["start", "step", "step"])
        self.assertEqual(dns.calls, ["start", "step", "step"])

    def test_cleanup_closes_dns_before_http_and_is_terminal(self):
        order = []

        class Ordered(FakeService):
            def __init__(self, name):
                super().__init__()
                self.name = name

            def deinit(self):
                order.append(self.name)

        runtime = ConfiguredDiscoveryRuntime(Ordered("http"), Ordered("dns"))
        runtime.start()
        self.assertIsNone(runtime.deinit())
        self.assertEqual(order, ["dns", "http"])
        self.assertFalse(runtime.step())
        with self.assertRaises(DiscoveryRuntimeError):
            runtime.start()


if __name__ == "__main__":
    unittest.main()
