import unittest

from app.product_runtime import ProductRuntime, ProductRuntimeError


class Scheduler:
    def __init__(self):
        self.armed = False
        self.calls = []

    def arm(self):
        self.calls.append("arm")
        if self.armed:
            return False
        self.armed = True
        return True

    def disarm(self):
        self.calls.append("disarm")
        changed = self.armed
        self.armed = False
        return changed

    def snapshot(self):
        return {"armed": self.armed}


class Configured:
    def __init__(self):
        self.scheduler = Scheduler()
        self.restart = False

    def restart_required(self, manager):
        return self.restart


class Owner:
    def __init__(self, name, start_result=True):
        self.name = name
        self.start_result = start_result
        self.calls = []

    def start(self):
        self.calls.append("start")
        return self.start_result

    def step(self, *args):
        self.calls.append("step")
        return False

    def deinit(self):
        self.calls.append("deinit")

    def snapshot(self):
        return {"faulted": False}


class Controller:
    requested_on = False

    def request_stop(self):
        self.requested_on = False
        return False


class Protocol:
    def validate_inbound_frame(self, frame):
        return frame


class Heater(Owner):
    def __init__(self):
        super().__init__("heater")
        self.controller = Controller()
        self.protocol_port = Protocol()


class Network(Owner):
    def __init__(self):
        super().__init__("network")
        self.station_connected = False
        self.station_ip = "192.168.1.44"
        self.raise_on_step = False

    def start(self, now_ms):
        self.calls.append("start")
        return True

    def snapshot(self):
        return {
            "faulted": False,
            "access_point": {"active": True, "ip": "192.168.4.1"},
            "station": {
                "connected": self.station_connected,
                "ip": self.station_ip if self.station_connected else None,
            },
            "mdns": {"ready": self.station_connected},
        }

    def step(self, *args):
        self.calls.append("step")
        if self.raise_on_step:
            raise OSError("radio failed")
        return False


class NetworkRuntime:
    def __init__(self, manager):
        self.manager = manager

    def restart_required(self, manager):
        return False


class Rest(Owner):
    def __init__(self, start_result=True):
        super().__init__("rest", start_result=start_result)

    def step_diagnostics(self, now_ms):
        self.calls.append("diagnostics")
        return 0


class Discovery(Owner):
    def __init__(self):
        super().__init__("discovery")
        self.station = None
        self.raise_on_step = False

    def step(self):
        self.calls.append("step")
        if self.raise_on_step:
            raise OSError("listener failed")
        return False

    def attach_station_http(self, server):
        self.calls.append("attach")
        if self.station is not None:
            return False
        self.station = server
        server.start()
        return True

    def detach_station_http(self):
        self.calls.append("detach")
        if self.station is None:
            return False
        self.station.deinit()
        self.station = None
        return True


class Gateway:
    def __init__(self):
        self.calls = []

    def step(self):
        self.calls.append("step")

    def snapshot(self):
        return {"steps": len(self.calls)}


class Fixture:
    def __init__(self, rest_start=True):
        self.manager = type("Manager", (), {"status": lambda self: {}})()
        self.configured = Configured()
        self.sensor = Owner("sensor")
        self.heater = Heater()
        self.network = Network()
        self.network_runtime = NetworkRuntime(self.network)
        self.rest = Rest(start_result=rest_start)
        self.discovery = Discovery()
        self.gateway = Gateway()
        self.station_servers = []
        self.radio_releases = 0
        self.now = 0

        def station_factory(rest, station_ip, ap_ip):
            server = Owner("station")
            server.station_ip = station_ip
            server.ap_ip = ap_ip
            self.station_servers.append(server)
            return server

        def release_radio():
            self.radio_releases += 1

        self.runtime = ProductRuntime(
            self.manager,
            self.configured,
            self.sensor,
            self.heater,
            self.network_runtime,
            lambda controller, gateway, protocol: self.rest,
            lambda rest, ap: self.discovery,
            station_factory,
            lambda scheduler, controller, persistence: self.gateway,
            self.ticks_ms,
            lambda newer, older: newer - older,
            lambda value, delta: value + delta,
            self.sleep_ms,
            radio_release=release_radio,
        )

    def ticks_ms(self):
        self.now += 1
        return self.now

    def sleep_ms(self, delay):
        self.now += delay


class TestProductRuntime(unittest.TestCase):
    def test_construction_is_inert_and_start_arms_last(self):
        fixture = Fixture()
        self.assertEqual(fixture.sensor.calls, [])
        self.assertEqual(fixture.heater.calls, [])
        self.assertEqual(fixture.network.calls, [])

        self.assertTrue(fixture.runtime.start())
        self.assertEqual(fixture.sensor.calls, ["start"])
        self.assertEqual(fixture.heater.calls, ["start"])
        self.assertEqual(fixture.network.calls, ["start", "step"])
        self.assertEqual(fixture.rest.calls, ["start"])
        self.assertEqual(fixture.discovery.calls, ["start"])
        self.assertEqual(fixture.configured.scheduler.calls, ["arm"])

    def test_step_advances_every_owner_and_attaches_station_listener(self):
        fixture = Fixture()
        fixture.runtime.start()
        fixture.network.station_connected = True

        self.assertTrue(fixture.runtime.step())
        self.assertEqual(fixture.sensor.calls[-1], "step")
        self.assertEqual(fixture.network.calls[-1], "step")
        self.assertIn("attach", fixture.discovery.calls)
        self.assertEqual(fixture.gateway.calls, ["step"])
        self.assertEqual(fixture.heater.calls[-1], "step")
        self.assertEqual(fixture.rest.calls[-1], "diagnostics")
        self.assertEqual(fixture.runtime.snapshot()["station_ip"], "192.168.1.44")

        fixture.network.station_connected = False
        fixture.runtime.step()
        self.assertIn("detach", fixture.discovery.calls)
        self.assertIsNone(fixture.runtime.snapshot()["station_ip"])

    def test_configuration_change_disarms_and_keeps_web_for_safe_restart(self):
        fixture = Fixture()
        fixture.runtime.start()
        fixture.configured.restart = True
        sensor_steps = fixture.sensor.calls.count("step")

        fixture.runtime.step()
        self.assertEqual(fixture.sensor.calls.count("step"), sensor_steps)
        self.assertEqual(fixture.gateway.calls, [])
        self.assertEqual(fixture.heater.calls[-1], "step")
        self.assertEqual(fixture.discovery.calls[-1], "step")
        self.assertTrue(fixture.runtime.snapshot()["restart_required"])
        self.assertFalse(fixture.configured.scheduler.armed)

    def test_network_failure_does_not_stop_heater_supervision(self):
        fixture = Fixture()
        fixture.runtime.start()
        fixture.network.raise_on_step = True

        self.assertTrue(fixture.runtime.step())
        self.assertEqual(fixture.heater.calls[-1], "step")
        state = fixture.runtime.snapshot()
        self.assertFalse(state["network_available"])
        self.assertTrue(state["discovery_available"])
        self.assertFalse(state["faulted"])

    def test_web_failure_does_not_stop_heater_supervision(self):
        fixture = Fixture()
        fixture.runtime.start()
        fixture.discovery.raise_on_step = True

        self.assertTrue(fixture.runtime.step())
        self.assertEqual(fixture.heater.calls[-1], "step")
        state = fixture.runtime.snapshot()
        self.assertTrue(state["network_available"])
        self.assertFalse(state["discovery_available"])
        self.assertFalse(state["faulted"])

    def test_failed_start_closes_every_started_owner_and_radio_gate(self):
        fixture = Fixture(rest_start=False)
        with self.assertRaises(ProductRuntimeError):
            fixture.runtime.start()
        self.assertTrue(fixture.runtime.closed)
        self.assertTrue(fixture.runtime.faulted)
        self.assertIn("deinit", fixture.sensor.calls)
        self.assertIn("deinit", fixture.heater.calls)
        self.assertIn("deinit", fixture.network.calls)
        self.assertIn("deinit", fixture.rest.calls)
        self.assertEqual(fixture.radio_releases, 1)

    def test_normal_cleanup_is_idempotent(self):
        fixture = Fixture()
        fixture.runtime.start()
        self.assertIsNone(fixture.runtime.deinit())
        self.assertIsNone(fixture.runtime.deinit())
        self.assertTrue(fixture.runtime.closed)
        self.assertEqual(fixture.radio_releases, 1)

    def test_shutdown_step_disarms_and_cleans_when_heater_is_safe(self):
        fixture = Fixture()
        fixture.runtime.start()
        self.assertTrue(fixture.runtime.shutdown_step())
        self.assertTrue(fixture.runtime.closed)
        self.assertFalse(fixture.configured.scheduler.armed)


if __name__ == "__main__":
    unittest.main()
