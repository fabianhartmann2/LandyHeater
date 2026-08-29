import ast
import inspect
import unittest

import app.network_manager as network_module
from app.network_manager import (
    NETWORK_AP_SSID,
    NETWORK_HOSTNAME,
    NetworkManager,
    NetworkPortContractError,
    NetworkPortError,
    STA_CONNECTING,
    STA_GOT_IP,
    STA_NO_AP,
    default_network_configuration,
    validate_network_configuration,
)


def configured_network(profiles=None):
    if profiles is None:
        profiles = []
    return {
        "hostname": "heater",
        "access_point": {
            "ssid": "Landy Heater",
            "password": "individual-secret",
        },
        "known_networks": profiles,
    }


def profile(profile_id="home", ssid="Home", password="home-secret"):
    return {"id": profile_id, "ssid": ssid, "password": password}


def sta_status(
    state=STA_CONNECTING,
    raw_status=1,
    connected=False,
    profile_id="home",
    ssid="Home",
    ip=None,
    gateway=None,
    dns=None,
    rssi=None,
    mdns_ready=False,
):
    return {
        "state": state,
        "raw_status": raw_status,
        "connected": connected,
        "profile_id": profile_id,
        "ssid": ssid,
        "ip": ip,
        "gateway": gateway,
        "dns": dns,
        "rssi": rssi,
        "mdns_ready": mdns_ready,
    }


class FakePort:
    def __init__(self):
        self.calls = []
        self.ap_active = True
        self.ap_ip = "192.168.4.1"
        self.ap_clients = 0
        self.status_plan = []
        self.errors = {}
        self.on_call = None
        self.closed = False

    def _call(self, name, *args):
        self.calls.append((name,) + args)
        if self.on_call is not None:
            self.on_call(name)
        error = self.errors.get(name)
        if error is not None:
            raise error

    def configure_hostname(self, name):
        self._call("hostname", name)
        return None

    def ensure_access_point(self, ssid, password):
        self._call("ensure_ap", ssid, password)
        return {
            "active": self.ap_active,
            "ip": self.ap_ip if self.ap_active else None,
            "clients": self.ap_clients,
        }

    def access_point_status(self):
        self._call("ap_status")
        return {
            "active": self.ap_active,
            "ip": self.ap_ip if self.ap_active else None,
            "clients": self.ap_clients,
        }

    def prepare_station(self):
        self._call("prepare_station")
        return None

    def connect_station(self, profile_id, ssid, password):
        self._call("connect", profile_id, ssid, password)
        return None

    def station_status(self):
        self._call("station_status")
        if self.status_plan:
            value = self.status_plan.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        return sta_status()

    def disconnect_station(self):
        self._call("disconnect")
        return None

    def deinit(self):
        self._call("deinit")
        self.closed = True
        return None


class ReactivatingPort(FakePort):
    """Model a driver call that resumes after a nested cleanup."""

    def __init__(self):
        super().__init__()
        self.radio_active = False

    def ensure_access_point(self, ssid, password):
        self._call("ensure_ap", ssid, password)
        self.radio_active = True
        return {
            "active": True,
            "ip": self.ap_ip,
            "clients": self.ap_clients,
        }

    def deinit(self):
        self._call("deinit")
        self.radio_active = False
        self.closed = True
        return None


def run_to_connect(manager):
    manager.start(0)
    manager.step(0)
    manager.step(0)
    manager.step(0)
    manager.step(0)


class TestNetworkConfiguration(unittest.TestCase):
    def test_unprovisioned_default_is_valid_but_not_runnable(self):
        default = default_network_configuration()
        self.assertIsNone(default["access_point"]["password"])
        self.assertEqual(validate_network_configuration(default), default)
        with self.assertRaisesRegex(ValueError, "required"):
            validate_network_configuration(default, True)

    def test_exact_bounds_and_priorities_are_canonical(self):
        candidate = configured_network([
            profile("first", "One", None),
            profile("second", "Two", "abcdefgh"),
        ])
        result = validate_network_configuration(candidate, True)
        self.assertEqual([item["id"] for item in result["known_networks"]], [
            "first", "second"
        ])
        candidate["known_networks"][0]["ssid"] = "changed"
        self.assertEqual(result["known_networks"][0]["ssid"], "One")

    def test_invalid_shapes_duplicates_and_secrets_are_rejected(self):
        cases = []
        value = configured_network()
        value["unknown"] = 1
        cases.append(value)
        value = configured_network()
        value["hostname"] = "heater.local"
        cases.append(value)
        value = configured_network()
        value["access_point"]["ssid"] = "Other"
        cases.append(value)
        value = configured_network()
        value["access_point"]["password"] = "short"
        cases.append(value)
        value = configured_network([profile("a", "Same"), profile("b", "Same")])
        cases.append(value)
        value = configured_network([profile("same", "One"), profile("same", "Two")])
        cases.append(value)
        value = configured_network([profile("a", "One", "not\x00valid")])
        cases.append(value)
        for candidate in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    validate_network_configuration(candidate, True)

    def test_64_hex_station_psk_is_supported_but_non_hex_is_not(self):
        value = configured_network([profile("a", "One", "a" * 64)])
        self.assertEqual(
            len(validate_network_configuration(value, True)["known_networks"][0]["password"]),
            64,
        )
        value["known_networks"][0]["password"] = "z" * 64
        with self.assertRaises(ValueError):
            validate_network_configuration(value, True)


class TestNetworkManager(unittest.TestCase):
    def test_import_and_constructor_are_hardware_free(self):
        tree = ast.parse(inspect.getsource(network_module))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        for forbidden in (
            "network", "machine", "board_config", "protocol",
            "app.heater_controller",
        ):
            self.assertNotIn(forbidden, imports)
        port = FakePort()
        NetworkManager(port, configured_network())
        self.assertEqual(port.calls, [])

    def test_ap_is_started_before_any_station_action(self):
        port = FakePort()
        manager = NetworkManager(port, configured_network([profile()]))
        run_to_connect(manager)
        self.assertEqual(
            [call[0] for call in port.calls],
            ["hostname", "ensure_ap", "prepare_station", "connect"],
        )
        self.assertTrue(manager.access_point_available)

    def test_hostname_failure_never_blocks_direct_ap_ip(self):
        port = FakePort()
        port.errors["hostname"] = NetworkPortError("driver unavailable")
        manager = NetworkManager(port, configured_network())
        manager.start(0)

        self.assertEqual(manager.step(0), "hostname_degraded")
        self.assertEqual(manager.step(0), "ap_available")

        snapshot = manager.snapshot()
        self.assertTrue(snapshot["access_point"]["active"])
        self.assertEqual(snapshot["access_point"]["ip"], "192.168.4.1")
        self.assertFalse(snapshot["mdns"]["ready"])
        self.assertEqual(snapshot["last_error"], "network_hostname_failed")
        self.assertEqual(
            [call[0] for call in port.calls], ["hostname", "ensure_ap"]
        )

    def test_no_profiles_keeps_access_point_up_and_station_disabled(self):
        port = FakePort()
        manager = NetworkManager(port, configured_network())
        manager.start(0)
        self.assertEqual(manager.step(0), "hostname_configured")
        self.assertEqual(manager.step(0), "ap_available")
        manager.step(0)
        snapshot = manager.snapshot()
        self.assertTrue(snapshot["access_point"]["active"])
        self.assertFalse(snapshot["station"]["connected"])
        self.assertEqual(snapshot["state"], "offline")

    def test_no_profiles_keep_the_full_access_point_check_cadence(self):
        port = FakePort()
        manager = NetworkManager(
            port,
            configured_network(),
            ap_check_interval_ms=5000,
        )
        self.assertTrue(manager.start(0))
        self.assertEqual(manager.step(0), "hostname_configured")
        self.assertEqual(manager.step(5000), "ap_available")
        for now_ms in (10000, 15000, 20000, 25000, 30000):
            self.assertEqual(manager.step(now_ms), "ap_checked")
        self.assertEqual(
            [call[0] for call in port.calls].count("ap_status"), 5
        )
        self.assertNotIn("prepare_station", [call[0] for call in port.calls])
        self.assertNotIn("prepare_station", [call[0] for call in port.calls])

    def test_station_success_exposes_direct_ip_and_likely_internet(self):
        port = FakePort()
        port.status_plan = [sta_status(
            state=STA_GOT_IP,
            raw_status=3,
            connected=True,
            ip="192.168.1.50",
            gateway="192.168.1.1",
            dns="192.168.1.1",
            rssi=-55,
            mdns_ready=True,
        )]
        manager = NetworkManager(port, configured_network([profile()]))
        run_to_connect(manager)
        self.assertEqual(manager.step(1000), "station_connected")
        snapshot = manager.snapshot()
        self.assertEqual(snapshot["state"], "online")
        self.assertEqual(snapshot["station"]["ip"], "192.168.1.50")
        self.assertTrue(snapshot["mdns"]["ready"])
        self.assertTrue(snapshot["internet_likely_available"])

    def test_station_link_loss_immediately_clears_online_truth(self):
        port = FakePort()
        port.status_plan = [
            sta_status(
                state=STA_GOT_IP,
                raw_status=3,
                connected=True,
                ip="10.0.0.2",
                gateway="10.0.0.1",
                dns="10.0.0.1",
                rssi=-60,
                mdns_ready=True,
            ),
            sta_status(state=STA_NO_AP, raw_status=201),
        ]
        manager = NetworkManager(port, configured_network([profile()]))
        run_to_connect(manager)
        self.assertEqual(manager.step(1000), "station_connected")
        self.assertEqual(manager.step(2000), "station_failed")
        snapshot = manager.snapshot()
        self.assertEqual(snapshot["state"], "offline")
        self.assertFalse(snapshot["station"]["connected"])
        self.assertIsNone(snapshot["station"]["ip"])
        self.assertFalse(snapshot["mdns"]["ready"])

    def test_station_status_error_immediately_revokes_online_truth(self):
        port = FakePort()
        port.status_plan = [
            sta_status(
                state=STA_GOT_IP,
                raw_status=3,
                connected=True,
                ip="10.0.0.2",
                gateway="10.0.0.1",
                dns="10.0.0.1",
                rssi=-60,
                mdns_ready=True,
            ),
            NetworkPortError("status unavailable"),
        ]
        manager = NetworkManager(port, configured_network([profile()]))
        run_to_connect(manager)
        self.assertEqual(manager.step(1000), "station_connected")
        self.assertEqual(manager.step(2000), "station_retry")

        snapshot = manager.snapshot()
        self.assertEqual(snapshot["state"], "degraded")
        self.assertFalse(snapshot["station"]["connected"])
        self.assertIsNone(snapshot["station"]["ip"])
        self.assertFalse(snapshot["mdns"]["ready"])
        self.assertFalse(snapshot["internet_likely_available"])

    def test_fatal_station_status_errors_revoke_truth_before_fault(self):
        for failure in (
            MemoryError("status oom"),
            sta_status(
                state=STA_GOT_IP,
                raw_status=3,
                connected=True,
                ip=None,
            ),
        ):
            with self.subTest(failure=type(failure).__name__):
                port = FakePort()
                port.status_plan = [
                    sta_status(
                        state=STA_GOT_IP,
                        raw_status=3,
                        connected=True,
                        ip="10.0.0.2",
                        gateway="10.0.0.1",
                        dns="10.0.0.1",
                        rssi=-60,
                        mdns_ready=True,
                    ),
                    failure,
                ]
                manager = NetworkManager(
                    port, configured_network([profile()])
                )
                run_to_connect(manager)
                self.assertEqual(manager.step(1000), "station_connected")
                with self.assertRaises((MemoryError, NetworkPortContractError)):
                    manager.step(2000)
                snapshot = manager.snapshot()
                self.assertTrue(snapshot["faulted"])
                self.assertFalse(snapshot["station"]["connected"])
                self.assertIsNone(snapshot["station"]["ip"])
                self.assertFalse(snapshot["mdns"]["ready"])
                self.assertFalse(snapshot["internet_likely_available"])

    def test_ap_repair_preserves_independent_live_station_truth(self):
        port = FakePort()
        port.status_plan = [sta_status(
            state=STA_GOT_IP,
            raw_status=3,
            connected=True,
            ip="10.0.0.2",
            gateway="10.0.0.1",
            dns="10.0.0.1",
            rssi=-60,
            mdns_ready=True,
        )]
        manager = NetworkManager(port, configured_network([profile()]))
        run_to_connect(manager)
        self.assertEqual(manager.step(1000), "station_connected")
        initial_connects = len([
            call for call in port.calls if call[0] == "connect"
        ])
        port.ap_active = False
        self.assertEqual(manager.step(5000), "ap_lost")
        degraded = manager.snapshot()
        self.assertEqual(degraded["state"], "degraded")
        self.assertTrue(degraded["station"]["connected"])
        self.assertTrue(degraded["internet_likely_available"])
        port.ap_active = True
        self.assertEqual(manager.step(5000), "ap_available")
        self.assertEqual(manager.snapshot()["state"], "online")
        self.assertEqual(len([
            call for call in port.calls if call[0] == "connect"
        ]), initial_connects)

    def test_wrong_network_rotates_to_next_profile(self):
        port = FakePort()
        port.status_plan = [
            sta_status(state=STA_NO_AP, raw_status=201),
            sta_status(
                state=STA_GOT_IP,
                raw_status=3,
                connected=True,
                profile_id="second",
                ssid="Two",
                ip="10.0.0.2",
                gateway="10.0.0.1",
                dns="10.0.0.1",
                mdns_ready=True,
            ),
        ]
        manager = NetworkManager(
            port,
            configured_network([
                profile("first", "One"),
                profile("second", "Two"),
            ]),
        )
        run_to_connect(manager)
        self.assertEqual(manager.step(1000), "station_failed")
        self.assertEqual(manager.step(1000), "station_disconnected")
        self.assertIsNone(manager.step(1500))
        self.assertEqual(manager.step(2000), "station_ready")
        self.assertEqual(manager.step(2000), "station_connecting")
        self.assertEqual(manager.step(3000), "station_connected")
        connects = [call for call in port.calls if call[0] == "connect"]
        self.assertEqual([call[1] for call in connects], ["first", "second"])

    def test_connecting_timeout_is_bounded_and_disconnects(self):
        port = FakePort()
        port.status_plan = [sta_status(), sta_status(), sta_status()]
        manager = NetworkManager(
            port,
            configured_network([profile()]),
            connection_timeout_ms=2000,
        )
        run_to_connect(manager)
        self.assertEqual(manager.step(1000), "station_connecting")
        self.assertEqual(manager.step(2000), "station_timeout")
        self.assertEqual(manager.step(2000), "station_disconnected")
        self.assertIn("disconnect", [call[0] for call in port.calls])

    def test_ap_loss_preempts_station_poll_and_is_repaired_first(self):
        port = FakePort()
        manager = NetworkManager(port, configured_network([profile()]))
        run_to_connect(manager)
        port.ap_active = False
        self.assertEqual(manager.step(5000), "ap_lost")
        names = [call[0] for call in port.calls]
        self.assertEqual(names[-1], "ap_status")
        port.ap_active = True
        self.assertEqual(manager.step(5000), "ap_available")
        self.assertNotIn(("disconnect",), port.calls)

    def test_ap_repair_resumes_pending_disconnect_and_profile_rotation(self):
        port = FakePort()
        port.status_plan = [sta_status(state=STA_NO_AP, raw_status=201)]
        manager = NetworkManager(
            port,
            configured_network([
                profile("first", "One"),
                profile("second", "Two"),
            ]),
        )
        run_to_connect(manager)
        self.assertEqual(manager.step(1000), "station_failed")
        port.ap_active = False
        self.assertEqual(manager.step(5000), "ap_lost")
        port.ap_active = True
        self.assertEqual(manager.step(5000), "ap_available")
        self.assertEqual(manager.step(5000), "station_disconnected")
        self.assertIsNone(manager.step(5500))
        self.assertEqual(manager.step(6000), "station_ready")
        self.assertEqual(manager.step(6000), "station_connecting")
        self.assertEqual(
            [call[1] for call in port.calls if call[0] == "connect"],
            ["first", "second"],
        )
        self.assertEqual(
            [call[0] for call in port.calls].count("disconnect"), 1
        )

    def test_ap_repair_preserves_existing_round_backoff_deadline(self):
        port = FakePort()
        port.status_plan = [sta_status(state=STA_NO_AP, raw_status=201)]
        manager = NetworkManager(port, configured_network([profile()]))
        run_to_connect(manager)
        self.assertEqual(manager.step(1000), "station_failed")
        self.assertEqual(manager.step(1000), "station_disconnected")
        port.ap_active = False
        self.assertEqual(manager.step(5000), "ap_lost")
        port.ap_active = True
        self.assertEqual(manager.step(5000), "ap_available")
        self.assertIsNone(manager.step(5000))
        self.assertIsNone(manager.step(5999))
        self.assertEqual(manager.step(6000), "station_ready")
        self.assertEqual(manager.step(6000), "station_connecting")
        self.assertEqual(
            [call[0] for call in port.calls].count("connect"), 2
        )

    def test_long_station_backoff_never_postpones_ap_supervision(self):
        port = FakePort()
        port.status_plan = [sta_status(state=STA_NO_AP, raw_status=201)]
        manager = NetworkManager(port, configured_network([profile()]))
        run_to_connect(manager)
        manager.step(1000)
        manager.step(1000)
        # One-profile rotation enters a 5-second round backoff, while the AP
        # health deadline remains at 5 seconds from initial activation.
        port.ap_active = False
        self.assertEqual(manager.step(5000), "ap_lost")
        self.assertEqual(port.calls[-1][0], "ap_status")

    def test_ap_check_does_not_shorten_station_backoff(self):
        port = FakePort()
        port.status_plan = [sta_status(state=STA_NO_AP, raw_status=201)]
        manager = NetworkManager(port, configured_network([profile()]))
        run_to_connect(manager)
        self.assertEqual(manager.step(1000), "station_failed")
        self.assertEqual(manager.step(1000), "station_disconnected")
        self.assertEqual(manager.step(5000), "ap_checked")
        self.assertIsNone(manager.step(5001))
        self.assertEqual(manager.step(6000), "station_ready")
        self.assertEqual(manager.step(6000), "station_connecting")

    def test_coarse_mainloop_alternates_ap_check_and_station_progress(self):
        port = FakePort()
        manager = NetworkManager(port, configured_network([profile()]))
        manager.start(0)
        results = [manager.step(value) for value in (
            0, 5000, 10000, 15000, 20000, 25000
        )]
        self.assertEqual(results[:2], ["hostname_configured", "ap_available"])
        self.assertIn("station_ready", results)
        self.assertIn("station_connecting", results)
        self.assertEqual(
            [call[0] for call in port.calls].count("connect"), 1
        )

    def test_ap_supervision_continues_during_disconnect_retries(self):
        port = FakePort()
        port.status_plan = [sta_status(state=STA_NO_AP, raw_status=201)]
        manager = NetworkManager(port, configured_network([profile()]))
        run_to_connect(manager)
        self.assertEqual(manager.step(1000), "station_failed")
        port.errors["disconnect"] = NetworkPortError("driver failure")
        self.assertEqual(manager.step(1000), "disconnect_retry")
        self.assertEqual(manager.step(2000), "disconnect_retry")
        self.assertEqual(manager.step(3000), "disconnect_retry")
        self.assertEqual(manager.step(4000), "disconnect_retry")
        port.ap_active = False
        self.assertEqual(manager.step(5000), "ap_lost")
        self.assertEqual(port.calls[-1][0], "ap_status")

    def test_recoverable_ap_error_never_calls_station_or_deactivates_ap(self):
        port = FakePort()
        port.errors["ensure_ap"] = NetworkPortError("secret-driver-text")
        manager = NetworkManager(port, configured_network([profile()]))
        manager.start(0)
        manager.step(0)
        self.assertEqual(manager.step(0), "ap_retry")
        snapshot = manager.snapshot()
        self.assertEqual(snapshot["last_error"], "network_ap_operation_failed")
        self.assertEqual(snapshot["access_point"], {
            "ssid": "Landy Heater",
            "active": False,
            "ip": None,
            "clients": 0,
            "password_configured": True,
        })
        serialized = repr(snapshot) + repr(manager.drain_events())
        self.assertNotIn("secret-driver-text", serialized)
        self.assertNotIn("individual-secret", serialized)
        self.assertNotIn("prepare_station", [call[0] for call in port.calls])

    def test_malformed_station_truth_faults_instead_of_claiming_online(self):
        port = FakePort()
        port.status_plan = [sta_status(
            state=STA_GOT_IP,
            raw_status=3,
            connected=False,
            ip="192.168.1.2",
        )]
        manager = NetworkManager(port, configured_network([profile()]))
        run_to_connect(manager)
        with self.assertRaises(NetworkPortContractError):
            manager.step(1000)
        self.assertTrue(manager.faulted)
        self.assertFalse(manager.snapshot()["station"]["connected"])

    def test_fatal_ap_status_error_revokes_stale_direct_ip(self):
        for failure in (MemoryError("status oom"), {"active": True}):
            with self.subTest(failure=type(failure).__name__):
                port = FakePort()
                manager = NetworkManager(port, configured_network())
                manager.start(0)
                manager.step(0)
                manager.step(0)
                port.errors["ap_status"] = (
                    failure if isinstance(failure, BaseException) else None
                )
                if type(failure) is dict:
                    original = port.access_point_status

                    def malformed_status():
                        port._call("ap_status")
                        return failure

                    port.access_point_status = malformed_status
                with self.assertRaises((MemoryError, ValueError)):
                    manager.step(5000)
                snapshot = manager.snapshot()
                self.assertTrue(snapshot["faulted"])
                self.assertFalse(snapshot["access_point"]["active"])
                self.assertIsNone(snapshot["access_point"]["ip"])
                if type(failure) is dict:
                    port.access_point_status = original

    def test_disconnected_station_cannot_claim_live_network_fields(self):
        port = FakePort()
        port.status_plan = [sta_status(
            state=STA_CONNECTING,
            raw_status=1,
            connected=False,
            ip="192.168.1.2",
            mdns_ready=True,
        )]
        manager = NetworkManager(port, configured_network([profile()]))
        run_to_connect(manager)
        with self.assertRaises(NetworkPortContractError):
            manager.step(1000)
        self.assertTrue(manager.faulted)

    def test_memory_error_faults_without_deinitializing_access_point(self):
        port = FakePort()
        manager = NetworkManager(port, configured_network())
        manager.start(0)
        manager.step(0)
        port.errors["ensure_ap"] = MemoryError("oom")
        with self.assertRaises(MemoryError):
            manager.step(0)
        self.assertTrue(manager.faulted)
        self.assertNotIn("deinit", [call[0] for call in port.calls])

    def test_credential_boundary_scrubs_port_memory_error_text(self):
        port = FakePort()
        port.errors["ensure_ap"] = MemoryError("individual-secret")
        manager = NetworkManager(port, configured_network())
        manager.start(0)
        manager.step(0)
        with self.assertRaises(MemoryError) as caught:
            manager.step(0)
        self.assertNotIn("individual-secret", repr(caught.exception))
        self.assertIsNone(caught.exception.__context__)
        self.assertTrue(manager.faulted)

        port = FakePort()
        port.errors["connect"] = MemoryError("home-secret")
        manager = NetworkManager(port, configured_network([profile()]))
        manager.start(0)
        manager.step(0)
        manager.step(0)
        manager.step(0)
        with self.assertRaises(MemoryError) as caught:
            manager.step(0)
        self.assertNotIn("home-secret", repr(caught.exception))
        self.assertIsNone(caught.exception.__context__)
        self.assertTrue(manager.faulted)

    def test_reentrant_step_faults_even_when_port_swallows_nested_error(self):
        port = FakePort()
        manager = NetworkManager(port, configured_network())
        manager.start(0)

        def callback(name):
            if name == "hostname":
                port.on_call = None
                try:
                    manager.step(0)
                except RuntimeError:
                    pass

        port.on_call = callback
        with self.assertRaisesRegex(RuntimeError, "re-entered"):
            manager.step(0)
        self.assertTrue(manager.faulted)

    def test_reentrant_deinit_preserves_closed_radio_truth(self):
        port = ReactivatingPort()
        manager = NetworkManager(port, configured_network())
        manager.start(0)
        manager.step(0)

        def callback(name):
            if name == "ensure_ap":
                port.on_call = None
                manager.deinit()

        port.on_call = callback
        with self.assertRaisesRegex(RuntimeError, "re-entered"):
            manager.step(0)
        snapshot = manager.snapshot()
        self.assertTrue(snapshot["closed"])
        self.assertTrue(snapshot["faulted"])
        self.assertFalse(snapshot["running"])
        self.assertEqual(snapshot["state"], "closed")
        self.assertFalse(snapshot["access_point"]["active"])
        self.assertTrue(port.closed)
        self.assertFalse(port.radio_active)
        self.assertEqual(
            [call[0] for call in port.calls].count("deinit"), 2
        )

    def test_wrap_safe_connection_timeout(self):
        period = 8192

        def diff(newer, older):
            return ((newer - older + period // 2) % period) - period // 2

        def add(value, delta):
            return (value + delta) % period

        port = FakePort()
        manager = NetworkManager(
            port,
            configured_network([profile()]),
            ticks_diff=diff,
            ticks_add=add,
            station_poll_interval_ms=100,
            connection_timeout_ms=300,
            ap_check_interval_ms=1000,
        )
        manager.start(8100)
        manager.step(8100)
        manager.step(8100)
        manager.step(8100)
        manager.step(8100)
        self.assertEqual(manager.step(8), "station_connecting")
        self.assertEqual(manager.step(208), "station_timeout")

    def test_deinit_is_explicit_and_closes_manager(self):
        port = FakePort()
        manager = NetworkManager(port, configured_network())
        manager.start(0)
        manager.step(0)
        manager.step(0)
        self.assertIsNone(manager.deinit())
        self.assertTrue(manager.closed)
        self.assertTrue(port.closed)
        self.assertFalse(manager.snapshot()["access_point"]["active"])


if __name__ == "__main__":
    unittest.main()
