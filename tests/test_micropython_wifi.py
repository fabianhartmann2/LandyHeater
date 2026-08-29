import ast
import builtins
import inspect
import runpy
import sys
import types
import unittest
from unittest import mock

import board_config
import hardware.micropython_wifi as hardware_module
from app.network_manager import (
    NetworkManager,
    NetworkPortContractError,
    NetworkPortError,
    STA_CONNECTING,
    STA_CONNECT_FAIL,
    STA_GOT_IP,
    STA_IDLE,
    STA_NO_AP,
    STA_WRONG_PASSWORD,
)
from hardware.micropython_wifi import (
    ERROR_CLEANUP,
    ERROR_CLOSED,
    ERROR_CONNECT,
    ERROR_STATION_PREPARE,
    MicroPythonWiFiPort,
    open_wifi_from_board_config,
)


_UNSET = object()


class FakeInterface:
    def __init__(self, interface_id):
        self.interface_id = interface_id
        self.calls = []
        self.active_value = False
        self.config_values = {}
        self.raw_status = FakeWLAN.STAT_IDLE
        self.connected = False
        self.ifconfig_value = (
            "192.168.4.1" if interface_id == FakeWLAN.IF_AP else "0.0.0.0",
            "255.255.255.0",
            "0.0.0.0",
            "0.0.0.0",
        )
        self.stations = []
        self.rssi = -57
        self.plans = {}

    def plan(self, name, *items):
        self.plans[name] = list(items)

    def _take(self, name, default=_UNSET):
        plan = self.plans.get(name)
        if not plan:
            return False, default
        item = plan.pop(0)
        if isinstance(item, BaseException):
            raise item
        return True, item

    def active(self, *args):
        if args:
            if len(args) != 1 or type(args[0]) is not bool:
                raise TypeError("invalid active call")
            self.calls.append(("active_set", args[0]))
            planned, result = self._take("active_set", _UNSET)
            if not planned:
                self.active_value = args[0]
                return self.active_value
            if type(result) is bool:
                self.active_value = result
            return result
        self.calls.append(("active_get",))
        planned, result = self._take("active_get", self.active_value)
        return result if planned else self.active_value

    def config(self, *args, **kwargs):
        self.calls.append(("config", args, dict(kwargs)))
        planned, result = self._take("config", None)
        if not planned or result is None:
            self.config_values.update(kwargs)
        return result

    def status(self, param=None):
        self.calls.append(("status", param))
        planned, result = self._take(
            "status_{}".format(param), _UNSET
        )
        if planned:
            return result
        if param == "stations":
            return list(self.stations)
        if param == "rssi":
            return self.rssi
        if param is not None:
            raise ValueError("unknown status parameter")
        return self.raw_status

    def isconnected(self):
        self.calls.append(("isconnected",))
        planned, result = self._take("isconnected", self.connected)
        return result if planned else self.connected

    def ifconfig(self):
        self.calls.append(("ifconfig",))
        planned, result = self._take("ifconfig", self.ifconfig_value)
        return result if planned else self.ifconfig_value

    def connect(self, *args):
        self.calls.append(("connect", args))
        planned, result = self._take("connect", None)
        if not planned or result is None:
            self.raw_status = FakeWLAN.STAT_CONNECTING
        return result

    def disconnect(self):
        self.calls.append(("disconnect",))
        planned, result = self._take("disconnect", None)
        if not planned or result is None:
            self.connected = False
            self.raw_status = FakeWLAN.STAT_IDLE
        return result


class FakeWLAN:
    IF_STA = 0
    IF_AP = 1
    SEC_WPA2 = 3
    STAT_IDLE = 0
    STAT_CONNECTING = 1
    STAT_WRONG_PASSWORD = -3
    STAT_NO_AP_FOUND = -2
    STAT_CONNECT_FAIL = -1
    STAT_GOT_IP = 3
    instances = []
    constructor_plan = []

    def __new__(cls, interface_id):
        if cls.constructor_plan:
            item = cls.constructor_plan.pop(0)
            if isinstance(item, BaseException):
                raise item
        instance = FakeInterface(interface_id)
        cls.instances.append(instance)
        return instance


class FakeNetwork(types.ModuleType):
    def __init__(self):
        super().__init__("network")
        self.WLAN = FakeWLAN
        self.country_value = "XX"
        self.hostname_value = "micropython"
        self.country_calls = []
        self.hostname_calls = []
        self.country_plan = []
        self.hostname_plan = []

    @staticmethod
    def _planned(plan):
        if not plan:
            return False, None
        item = plan.pop(0)
        if isinstance(item, BaseException):
            raise item
        return True, item

    def country(self, *args):
        self.country_calls.append(args)
        planned, result = self._planned(self.country_plan)
        if args and (not planned or result is None):
            self.country_value = args[0]
        if planned:
            return result
        return None if args else self.country_value

    def hostname(self, *args):
        self.hostname_calls.append(args)
        planned, result = self._planned(self.hostname_plan)
        if args and (not planned or result is None):
            self.hostname_value = args[0]
        if planned:
            return result
        return None if args else self.hostname_value


def fake_network_module():
    FakeWLAN.instances = []
    FakeWLAN.constructor_plan = []
    return FakeNetwork()


def direct_port(max_clients=4):
    network = fake_network_module()
    ap = FakeInterface(FakeWLAN.IF_AP)
    sta = FakeInterface(FakeWLAN.IF_STA)
    port = MicroPythonWiFiPort(
        network,
        ap,
        sta,
        max_clients,
        FakeWLAN.SEC_WPA2,
        (
            FakeWLAN.STAT_IDLE,
            FakeWLAN.STAT_CONNECTING,
            FakeWLAN.STAT_WRONG_PASSWORD,
            FakeWLAN.STAT_NO_AP_FOUND,
            FakeWLAN.STAT_CONNECT_FAIL,
            FakeWLAN.STAT_GOT_IP,
        ),
    )
    return port, network, ap, sta


def approved_board():
    return mock.patch.multiple(
        board_config,
        WIFI_RADIO_APPROVED=True,
        WIFI_COUNTRY_CODE="CH",
        WIFI_AP_MAX_CLIENTS=4,
        WIFI_STA_RECONNECTS=0,
    )


class TestMicroPythonWiFi(unittest.TestCase):
    def setUp(self):
        hardware_module._WIFI_LEASED = False
        hardware_module._WIFI_LEASE_POISONED = False
        FakeWLAN.instances = []
        FakeWLAN.constructor_plan = []

    def test_module_import_is_network_hardware_free(self):
        tree = ast.parse(inspect.getsource(hardware_module))
        top_imports = []
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                top_imports.extend(alias.name for alias in statement.names)
            elif isinstance(statement, ast.ImportFrom):
                top_imports.append(statement.module)
        self.assertNotIn("network", top_imports)

        real_import = builtins.__import__

        def guard(name, *args, **kwargs):
            if name in ("network", "machine"):
                raise AssertionError("radio or machine import attempted")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guard):
            namespace = runpy.run_path(hardware_module.__file__)
        self.assertIn("MicroPythonWiFiPort", namespace)

    def test_factory_guard_runs_before_lazy_network_import(self):
        real_import = builtins.__import__
        attempted = []

        def guard(name, *args, **kwargs):
            if name == "network":
                attempted.append(name)
                raise AssertionError("network import attempted")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(
            board_config,
            "require_wifi_configuration",
            side_effect=RuntimeError("locked"),
        ), mock.patch("builtins.__import__", side_effect=guard):
            with self.assertRaisesRegex(RuntimeError, "locked"):
                open_wifi_from_board_config()
        self.assertEqual(attempted, [])

    def test_public_factory_has_no_arguments(self):
        self.assertEqual(
            tuple(inspect.signature(open_wifi_from_board_config).parameters),
            (),
        )

    def test_factory_sets_country_then_leases_without_radio_calls(self):
        network = fake_network_module()
        with approved_board(), mock.patch.dict(
            sys.modules, {"network": network}
        ):
            port = open_wifi_from_board_config()
        self.assertEqual(network.country_calls, [("CH",), ()])
        self.assertEqual(
            [item.interface_id for item in FakeWLAN.instances],
            [FakeWLAN.IF_AP, FakeWLAN.IF_STA],
        )
        for interface in FakeWLAN.instances:
            self.assertEqual(interface.calls, [])
        self.assertFalse(port.closed)
        port.deinit()

    def test_fresh_factory_deinit_skips_disconnect_and_confirms_radio_off(self):
        network = fake_network_module()
        with approved_board(), mock.patch.dict(
            sys.modules, {"network": network}
        ):
            port = open_wifi_from_board_config()
        ap, sta = FakeWLAN.instances

        self.assertIsNone(port.deinit())
        self.assertTrue(port.closed)
        self.assertTrue(port.cleanup_complete)
        self.assertNotIn(("disconnect",), sta.calls)
        self.assertEqual(
            [call for call in sta.calls if call[0] == "active_set"],
            [("active_set", False)],
        )
        self.assertEqual(
            [call for call in ap.calls if call[0] == "active_set"],
            [("active_set", False)],
        )
        self.assertIs(sta.active_value, False)
        self.assertIs(ap.active_value, False)
        self.assertGreaterEqual(
            sta.calls.count(("active_get",)), 2
        )
        self.assertGreaterEqual(
            ap.calls.count(("active_get",)), 1
        )

    def test_double_factory_open_waits_for_confirmed_cleanup(self):
        network = fake_network_module()
        with approved_board(), mock.patch.dict(
            sys.modules, {"network": network}
        ):
            first = open_wifi_from_board_config()
            with self.assertRaises(NetworkPortError):
                open_wifi_from_board_config()
            first.deinit()
            second = open_wifi_from_board_config()
            second.deinit()
        self.assertEqual(len(FakeWLAN.instances), 4)

    def test_hostname_is_set_and_exactly_read_back(self):
        port, network, _, _ = direct_port()
        self.assertIsNone(port.configure_hostname("heater"))
        self.assertEqual(network.hostname_calls, [("heater",), ()])
        port.deinit()

    def test_access_point_uses_wpa2_and_bounded_clients(self):
        port, _, ap, _ = direct_port()
        port.configure_hostname("heater")
        ap.stations = [(b"one",), (b"two",)]
        password = "safe-passphrase"
        status = port.ensure_access_point("Landy Heater", password)
        self.assertEqual(
            ap.config_values,
            {
                "ssid": "Landy Heater",
                "security": FakeWLAN.SEC_WPA2,
                "key": password,
                "max_clients": 4,
            },
        )
        self.assertEqual(
            status,
            {"active": True, "ip": "192.168.4.1", "clients": 2},
        )
        self.assertNotIn(password, repr(status))
        port.deinit()

    def test_ap_starts_with_direct_ip_after_hostname_failure(self):
        port, network, ap, _ = direct_port()
        network.hostname_plan = [OSError("hostname unavailable")]
        with self.assertRaises(NetworkPortError):
            port.configure_hostname("heater")

        status = port.ensure_access_point("Landy Heater", "safe-ap-pass")
        self.assertEqual(
            status,
            {"active": True, "ip": "192.168.4.1", "clients": 0},
        )
        self.assertFalse(port.station_status()["mdns_ready"])
        self.assertEqual(ap.calls.count(("active_set", True)), 1)
        port.deinit()

    def test_core_keeps_ap_and_sta_usable_when_hostname_driver_fails(self):
        port, network, ap, sta = direct_port()
        network.hostname_plan = [OSError("hostname unavailable")]
        manager = NetworkManager(
            port,
            {
                "hostname": "heater",
                "access_point": {
                    "ssid": "Landy Heater",
                    "password": "safe-ap-pass",
                },
                "known_networks": [{
                    "id": "home",
                    "ssid": "Home",
                    "password": "safe-sta-pass",
                }],
            },
        )
        manager.start(0)
        self.assertEqual(manager.step(0), "hostname_degraded")
        self.assertEqual(manager.step(0), "ap_available")
        self.assertEqual(manager.step(0), "station_ready")
        self.assertEqual(manager.step(0), "station_connecting")

        sta.connected = True
        sta.raw_status = FakeWLAN.STAT_GOT_IP
        sta.ifconfig_value = (
            "192.168.1.25",
            "255.255.255.0",
            "192.168.1.1",
            "192.168.1.1",
        )
        self.assertEqual(manager.step(1000), "station_connected")
        snapshot = manager.snapshot()
        self.assertFalse(snapshot["faulted"])
        self.assertTrue(snapshot["access_point"]["active"])
        self.assertEqual(snapshot["access_point"]["ip"], "192.168.4.1")
        self.assertTrue(snapshot["station"]["connected"])
        self.assertFalse(snapshot["mdns"]["ready"])
        self.assertTrue(ap.active_value)
        manager.deinit()

    def test_reentrant_manager_deinit_cannot_reactivate_access_point(self):
        port, _, ap, sta = direct_port()
        port._claim_lease()
        hardware_module._WIFI_LEASED = True
        manager = NetworkManager(
            port,
            {
                "hostname": "heater",
                "access_point": {
                    "ssid": "Landy Heater",
                    "password": "safe-ap-pass",
                },
                "known_networks": [],
            },
        )
        original_config = ap.config
        nested_factory = []

        def reentrant_config(*args, **kwargs):
            manager.deinit()
            nested_factory.append(hardware_module._WIFI_LEASED)
            network = fake_network_module()
            with approved_board(), mock.patch.dict(
                sys.modules, {"network": network}
            ):
                try:
                    open_wifi_from_board_config()
                except NetworkPortError as error:
                    nested_factory.append(str(error))
            return original_config(*args, **kwargs)

        ap.config = reentrant_config
        self.assertTrue(manager.start(0))
        self.assertEqual(manager.step(0), "hostname_configured")
        with self.assertRaisesRegex(RuntimeError, "re-entered"):
            manager.step(0)
        self.assertTrue(manager.closed)
        self.assertTrue(port.closed)
        self.assertTrue(port.cleanup_complete)
        self.assertFalse(ap.active_value)
        self.assertFalse(sta.active_value)
        self.assertNotIn(("active_set", True), ap.calls)
        self.assertEqual(nested_factory, [True, "micropython_wifi_already_owned"])
        self.assertFalse(hardware_module._WIFI_LEASED)
        self.assertIsNone(manager.deinit())

    def test_outer_cleanup_rechecks_station_after_partial_nested_cleanup(self):
        port, _, ap, sta = direct_port()
        manager = NetworkManager(
            port,
            {
                "hostname": "heater",
                "access_point": {
                    "ssid": "Landy Heater",
                    "password": "safe-ap-pass",
                },
                "known_networks": [{
                    "id": "absent",
                    "ssid": "Absent",
                    "password": "safe-sta-pass",
                }],
            },
        )
        self.assertTrue(manager.start(0))
        self.assertEqual(manager.step(0), "hostname_configured")
        self.assertEqual(manager.step(0), "ap_available")

        ap.plan("active_set", OSError("nested AP cleanup failed"))
        original_active = sta.active

        def reentrant_station_active(*args):
            if args == (True,):
                try:
                    manager.deinit()
                except BaseException:
                    pass
                # Model the suspended outer vendor operation completing after
                # the nested cleanup and then failing under heap pressure.
                sta.active_value = True
                raise MemoryError("must-not-survive")
            return original_active(*args)

        sta.active = reentrant_station_active
        with self.assertRaises(MemoryError) as caught:
            manager.step(0)
        self.assertEqual(str(caught.exception), "")
        self.assertIsNone(caught.exception.__context__)
        self.assertTrue(manager.closed)
        self.assertTrue(port.cleanup_complete)
        self.assertFalse(ap.active_value)
        self.assertFalse(sta.active_value)

    def test_active_setter_requires_exact_bool_and_confirmed_state(self):
        port, _, ap, _ = direct_port()
        for returned in (None, 1, False):
            with self.subTest(returned=returned):
                ap.plan("active_set", returned)
                with self.assertRaises(NetworkPortContractError):
                    port.ensure_access_point(
                        "Landy Heater", "safe-ap-pass"
                    )
                ap.active_value = False

        ap.plan("active_set", True)
        ap.plan("active_get", False)
        with self.assertRaises(NetworkPortContractError):
            port.ensure_access_point("Landy Heater", "safe-ap-pass")
        port.deinit()

    def test_access_point_driver_error_has_no_secret_context(self):
        port, _, ap, _ = direct_port()
        port.configure_hostname("heater")
        secret = "private-ap-key"
        ap.plan("config", OSError(secret))
        with self.assertRaises(NetworkPortError) as context:
            port.ensure_access_point("Landy Heater", secret)
        self.assertNotIn(secret, str(context.exception))
        self.assertIsNone(context.exception.__context__)
        self.assertNotIn(("active_set", True), ap.calls)
        port.deinit()

    def test_access_point_memory_error_preserves_type_without_secret(self):
        port, _, ap, _ = direct_port()
        port.configure_hostname("heater")
        secret = "private-ap-key"
        ap.plan("config", MemoryError(secret))
        with self.assertRaises(MemoryError) as context:
            port.ensure_access_point("Landy Heater", secret)
        self.assertEqual(str(context.exception), "")
        self.assertIsNone(context.exception.__context__)
        self.assertNotIn(secret, repr(context.exception))
        self.assertNotIn(("active_set", True), ap.calls)
        port.deinit()

    def test_all_driver_memory_errors_are_text_and_context_free(self):
        secret = "driver-must-not-echo-private-ap-key"

        port, _, ap, _ = direct_port()
        port.configure_hostname("heater")
        ap.plan("active_set", MemoryError(secret))
        with self.assertRaises(MemoryError) as active_error:
            port.ensure_access_point("Landy Heater", "safe-ap-pass")
        self.assertEqual(str(active_error.exception), "")
        self.assertIsNone(active_error.exception.__context__)
        port.deinit()

        port, _, ap, _ = direct_port()
        ap.active_value = True
        ap.plan("active_get", MemoryError(secret))
        with self.assertRaises(MemoryError) as status_error:
            port.access_point_status()
        self.assertEqual(str(status_error.exception), "")
        self.assertIsNone(status_error.exception.__context__)
        port.deinit()

        network = fake_network_module()
        network.country_plan = [MemoryError(secret)]
        with approved_board(), mock.patch.dict(
            sys.modules, {"network": network}
        ):
            with self.assertRaises(MemoryError) as country_error:
                open_wifi_from_board_config()
        self.assertEqual(str(country_error.exception), "")
        self.assertIsNone(country_error.exception.__context__)
        self.assertFalse(hardware_module._WIFI_LEASED)

    def test_inactive_access_point_status_does_not_use_client_truth(self):
        port, _, ap, _ = direct_port()
        ap.stations = [(b"stale",)]
        self.assertEqual(
            port.access_point_status(),
            {"active": False, "ip": None, "clients": 0},
        )
        self.assertNotIn(("status", "stations"), ap.calls)
        port.deinit()

    def test_station_prepare_requires_ap_and_disables_driver_reconnect(self):
        port, _, ap, sta = direct_port()
        port.configure_hostname("heater")
        with self.assertRaises(NetworkPortError):
            port.prepare_station()
        self.assertEqual(sta.calls, [])
        ap.active_value = True
        self.assertIsNone(port.prepare_station())
        self.assertEqual(sta.config_values, {"reconnects": 0})
        self.assertTrue(sta.active_value)
        self.assertEqual(
            sta.calls,
            [
                ("active_set", True),
                ("active_get",),
                ("config", (), {"reconnects": 0}),
            ],
        )
        port.deinit()

    def test_station_config_failure_rolls_activation_back_before_reporting(self):
        for planned, error_type in (
            (OSError("driver detail"), NetworkPortError),
            (MemoryError("vendor detail"), MemoryError),
            (False, NetworkPortContractError),
        ):
            with self.subTest(planned=type(planned).__name__):
                port, _, ap, sta = direct_port()
                ap.active_value = True
                sta.plan("config", planned)
                with self.assertRaises(error_type) as caught:
                    port.prepare_station()
                if isinstance(planned, MemoryError):
                    self.assertEqual(str(caught.exception), "")
                    self.assertIsNone(caught.exception.__context__)
                self.assertEqual(
                    [call[0] for call in sta.calls[:3]],
                    ["active_set", "active_get", "config"],
                )
                self.assertIn(("active_set", False), sta.calls)
                self.assertIs(sta.active_value, False)
                with self.assertRaises(NetworkPortContractError):
                    port.connect_station("home", "Home", "safe-pass")
                port.deinit()

    def test_station_config_primary_failure_survives_failed_rollback(self):
        port, _, ap, sta = direct_port()
        ap.active_value = True
        sta.plan(
            "active_set",
            True,
            OSError("rollback one"),
            OSError("rollback two"),
        )
        sta.plan("config", OSError("primary driver detail"))

        with self.assertRaises(NetworkPortError) as caught:
            port.prepare_station()
        self.assertEqual(str(caught.exception), ERROR_STATION_PREPARE)
        self.assertTrue(sta.active_value)
        with self.assertRaises(NetworkPortContractError):
            port.connect_station("home", "Home", "safe-pass")

        sta.plan("active_set", False)
        self.assertIsNone(port.deinit())
        self.assertFalse(sta.active_value)

    def test_secure_and_open_station_connect_do_not_retain_password(self):
        port, _, ap, sta = direct_port()
        port.configure_hostname("heater")
        ap.active_value = True
        port.prepare_station()
        secret = "never-report-this"
        port.connect_station("home", "Home", secret)
        self.assertEqual(sta.calls[-1], ("connect", ("Home", secret)))
        status = port.station_status()
        self.assertNotIn(secret, repr(status))
        port.disconnect_station()
        port.connect_station("guest", "Guest", None)
        self.assertEqual(sta.calls[-1], ("connect", ("Guest",)))
        port.deinit()

    def test_driver_exception_text_cannot_leak_station_password(self):
        port, _, ap, sta = direct_port()
        port.configure_hostname("heater")
        ap.active_value = True
        port.prepare_station()
        secret = "driver-secret"
        sta.plan("connect", OSError(secret))
        with self.assertRaises(NetworkPortError) as context:
            port.connect_station("home", "Home", secret)
        self.assertEqual(str(context.exception), ERROR_CONNECT)
        self.assertNotIn(secret, repr(context.exception))
        self.assertIsNone(context.exception.__cause__)
        self.assertIsNone(context.exception.__context__)
        port.deinit()

    def test_connect_memory_error_propagates_without_disabling_access_point(self):
        port, _, ap, sta = direct_port()
        port.configure_hostname("heater")
        ap.active_value = True
        port.prepare_station()
        secret = "secret-pass"
        sta.plan("connect", MemoryError(secret))
        with self.assertRaises(MemoryError) as context:
            port.connect_station("home", "Home", secret)
        self.assertEqual(str(context.exception), "")
        self.assertIsNone(context.exception.__context__)
        self.assertNotIn(secret, repr(context.exception))
        self.assertTrue(ap.active_value)
        self.assertNotIn(("active_set", False), ap.calls)
        port.deinit()

    def test_station_status_maps_all_documented_and_unknown_codes(self):
        port, _, ap, sta = direct_port()
        port.configure_hostname("heater")
        ap.active_value = True
        port.prepare_station()
        expected = (
            (FakeWLAN.STAT_IDLE, STA_IDLE),
            (FakeWLAN.STAT_CONNECTING, STA_CONNECTING),
            (FakeWLAN.STAT_WRONG_PASSWORD, STA_WRONG_PASSWORD),
            (FakeWLAN.STAT_NO_AP_FOUND, STA_NO_AP),
            (FakeWLAN.STAT_CONNECT_FAIL, STA_CONNECT_FAIL),
            (9999, STA_CONNECT_FAIL),
        )
        for raw, normalized in expected:
            sta.raw_status = raw
            sta.connected = False
            status = port.station_status()
            self.assertEqual(status["raw_status"], raw)
            self.assertEqual(status["state"], normalized)
            self.assertFalse(status["connected"])
        port.deinit()

    def test_connected_station_has_complete_truth_and_mdns_shape(self):
        port, _, ap, sta = direct_port()
        port.configure_hostname("heater")
        ap.active_value = True
        port.prepare_station()
        port.connect_station("home", "Home", "correct-pass")
        sta.connected = True
        sta.raw_status = FakeWLAN.STAT_GOT_IP
        sta.ifconfig_value = (
            "192.168.1.25",
            "255.255.255.0",
            "192.168.1.1",
            "192.168.1.1",
        )
        sta.rssi = -61
        self.assertEqual(
            port.station_status(),
            {
                "state": STA_GOT_IP,
                "raw_status": FakeWLAN.STAT_GOT_IP,
                "connected": True,
                "profile_id": "home",
                "ssid": "Home",
                "ip": "192.168.1.25",
                "gateway": "192.168.1.1",
                "dns": "192.168.1.1",
                "rssi": -61,
                "mdns_ready": True,
            },
        )
        port.deinit()

    def test_real_core_and_fake_micropython_port_interoperate(self):
        port, _, ap, sta = direct_port()
        manager = NetworkManager(
            port,
            {
                "hostname": "heater",
                "access_point": {
                    "ssid": "Landy Heater",
                    "password": "safe-ap-pass",
                },
                "known_networks": [
                    {
                        "id": "home",
                        "ssid": "Home",
                        "password": "safe-sta-pass",
                    }
                ],
            },
        )
        self.assertTrue(manager.start(0))
        self.assertEqual(manager.step(0), "hostname_configured")
        self.assertEqual(manager.step(0), "ap_available")
        self.assertEqual(manager.step(0), "station_ready")
        self.assertEqual(manager.step(0), "station_connecting")
        self.assertTrue(ap.active_value)
        self.assertEqual(sta.config_values, {"reconnects": 0})
        sta.connected = True
        sta.raw_status = FakeWLAN.STAT_GOT_IP
        sta.ifconfig_value = (
            "192.168.1.25",
            "255.255.255.0",
            "192.168.1.1",
            "192.168.1.1",
        )
        self.assertEqual(manager.step(1000), "station_connected")
        snapshot = manager.snapshot()
        self.assertTrue(snapshot["access_point"]["active"])
        self.assertTrue(snapshot["station"]["connected"])
        self.assertTrue(snapshot["mdns"]["ready"])
        self.assertNotIn("safe-ap-pass", repr(snapshot))
        self.assertNotIn("safe-sta-pass", repr(snapshot))
        manager.deinit()

    def test_driver_status_race_is_normalized_to_consistent_truth(self):
        port, _, ap, sta = direct_port()
        port.configure_hostname("heater")
        ap.active_value = True
        port.prepare_station()
        sta.raw_status = FakeWLAN.STAT_IDLE
        sta.connected = True
        sta.ifconfig_value = (
            "10.0.0.2", "255.255.255.0", "10.0.0.1", "10.0.0.1"
        )
        first = port.station_status()
        self.assertEqual(first["state"], STA_GOT_IP)
        self.assertFalse(first["mdns_ready"])
        sta.connected = False
        sta.raw_status = FakeWLAN.STAT_GOT_IP
        status = port.station_status()
        self.assertEqual(status["state"], STA_CONNECT_FAIL)
        self.assertFalse(status["connected"])
        port.deinit()

    def test_malformed_driver_values_fail_as_contract_errors(self):
        port, _, ap, sta = direct_port()
        ap.plan("active_get", 1)
        with self.assertRaises(NetworkPortContractError):
            port.access_point_status()
        ap.active_value = True
        ap.plans.clear()
        port.configure_hostname("heater")
        port.prepare_station()
        sta.raw_status = True
        with self.assertRaises(NetworkPortContractError):
            port.station_status()
        port.deinit()

    def test_malformed_ipv4_and_excess_clients_are_rejected(self):
        port, _, ap, _ = direct_port()
        ap.active_value = True
        ap.ifconfig_value = (
            "999.1.1.1", "255.255.255.0", "0.0.0.0", "0.0.0.0"
        )
        with self.assertRaises(NetworkPortContractError):
            port.access_point_status()
        ap.ifconfig_value = (
            "192.168.4.1", "255.255.255.0", "0.0.0.0", "0.0.0.0"
        )
        ap.stations = [object()] * 5
        with self.assertRaises(NetworkPortContractError):
            port.access_point_status()
        port.deinit()

    def test_all_methods_reject_after_close_with_fixed_error(self):
        port, _, _, _ = direct_port()
        port.deinit()
        self.assertTrue(port.closed)
        self.assertTrue(port.cleanup_complete)
        for operation in (
            lambda: port.configure_hostname("heater"),
            lambda: port.access_point_status(),
            lambda: port.prepare_station(),
            lambda: port.disconnect_station(),
        ):
            with self.assertRaises(NetworkPortError) as context:
                operation()
            self.assertEqual(str(context.exception), ERROR_CLOSED)

    def test_deinit_is_closed_first_attempts_both_interfaces_and_retries(self):
        port, _, ap, sta = direct_port()
        sta.active_value = True
        ap.active_value = True
        sta.plan("active_set", OSError("station busy"), False)
        ap.plan("active_set", OSError("ap busy"), False)
        with self.assertRaises(NetworkPortError) as context:
            port.deinit()
        self.assertEqual(str(context.exception), ERROR_CLEANUP)
        self.assertTrue(port.closed)
        self.assertFalse(port.cleanup_complete)
        self.assertEqual(
            [call for call in sta.calls if call[0] == "active_set"],
            [("active_set", False)],
        )
        self.assertEqual(
            [call for call in ap.calls if call[0] == "active_set"],
            [("active_set", False)],
        )
        self.assertIsNone(port.deinit())
        self.assertTrue(port.cleanup_complete)
        self.assertEqual(
            [call for call in sta.calls if call[0] == "disconnect"],
            [("disconnect",), ("disconnect",)],
        )

    def test_deinit_memory_error_still_turns_both_interfaces_off(self):
        port, _, ap, sta = direct_port()
        ap.active_value = True
        sta.active_value = True
        sta.connected = True
        secret = "cleanup-must-not-echo-this-secret"
        sta.plan("disconnect", MemoryError(secret))
        with self.assertRaises(MemoryError) as caught:
            port.deinit()
        self.assertEqual(str(caught.exception), "")
        self.assertIsNone(caught.exception.__context__)
        self.assertTrue(port.closed)
        self.assertTrue(port.cleanup_complete)
        self.assertFalse(ap.active_value)
        self.assertFalse(sta.active_value)
        self.assertIsNone(port.deinit())

    def test_finalized_deinit_is_idempotent_and_driver_inert(self):
        port, _, ap, sta = direct_port()
        self.assertIsNone(port.deinit())
        self.assertTrue(port.cleanup_complete)
        ap_calls = list(ap.calls)
        sta_calls = list(sta.calls)
        self.assertIsNone(port.deinit())
        self.assertFalse(ap.active_value)
        self.assertFalse(sta.active_value)
        self.assertTrue(port.cleanup_complete)
        self.assertEqual(ap.calls, ap_calls)
        self.assertEqual(sta.calls, sta_calls)

    def test_released_old_port_cannot_disable_a_new_singleton_owner(self):
        first, network, ap, sta = direct_port()
        first._claim_lease()
        hardware_module._WIFI_LEASED = True
        first.configure_hostname("heater")
        first.ensure_access_point("Landy Heater", "first-safe-pass")
        self.assertIsNone(first.deinit())
        self.assertFalse(hardware_module._WIFI_LEASED)

        second = MicroPythonWiFiPort(
            network,
            ap,
            sta,
            4,
            FakeWLAN.SEC_WPA2,
            (
                FakeWLAN.STAT_IDLE,
                FakeWLAN.STAT_CONNECTING,
                FakeWLAN.STAT_WRONG_PASSWORD,
                FakeWLAN.STAT_NO_AP_FOUND,
                FakeWLAN.STAT_CONNECT_FAIL,
                FakeWLAN.STAT_GOT_IP,
            ),
        )
        second._claim_lease()
        hardware_module._WIFI_LEASED = True
        second.configure_hostname("heater")
        second.ensure_access_point("Landy Heater", "second-safe-pass")
        self.assertTrue(ap.active_value)

        self.assertIsNone(first.deinit())
        self.assertTrue(ap.active_value)
        self.assertTrue(hardware_module._WIFI_LEASED)
        self.assertTrue(second.cleanup_complete is False)

        self.assertIsNone(second.deinit())
        self.assertFalse(ap.active_value)
        self.assertFalse(sta.active_value)
        self.assertFalse(hardware_module._WIFI_LEASED)

    def test_factory_constructor_failure_turns_returned_interface_off(self):
        network = fake_network_module()
        FakeWLAN.constructor_plan = [None, RuntimeError("STA constructor")]
        with approved_board(), mock.patch.dict(
            sys.modules, {"network": network}
        ):
            with self.assertRaisesRegex(RuntimeError, "STA constructor"):
                open_wifi_from_board_config()
        self.assertEqual(len(FakeWLAN.instances), 1)
        self.assertIn(("active_set", False), FakeWLAN.instances[0].calls)
        self.assertFalse(hardware_module._WIFI_LEASED)
        self.assertFalse(hardware_module._WIFI_LEASE_POISONED)

    def test_factory_constructor_oom_keeps_primary_and_cleans_fresh_radios(self):
        network = fake_network_module()
        secret = "vendor-oom-secret"
        with approved_board(), mock.patch.dict(
            sys.modules, {"network": network}
        ), mock.patch.object(
            hardware_module,
            "MicroPythonWiFiPort",
            side_effect=MemoryError(secret),
        ):
            with self.assertRaises(MemoryError) as caught:
                open_wifi_from_board_config()

        self.assertEqual(str(caught.exception), "")
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(len(FakeWLAN.instances), 2)
        ap, sta = FakeWLAN.instances
        self.assertNotIn(("disconnect",), sta.calls)
        self.assertIs(ap.active_value, False)
        self.assertIs(sta.active_value, False)
        self.assertIn(("active_set", False), ap.calls)
        self.assertIn(("active_set", False), sta.calls)
        self.assertFalse(hardware_module._WIFI_LEASED)
        self.assertFalse(hardware_module._WIFI_LEASE_POISONED)

    def test_raw_cleanup_deactivates_both_after_disconnect_error(self):
        ap = FakeInterface(FakeWLAN.IF_AP)
        sta = FakeInterface(FakeWLAN.IF_STA)
        ap.active_value = True
        sta.active_value = True
        sta.plan("disconnect", OSError("disconnect failed"))

        self.assertTrue(hardware_module._raw_interface_cleanup(ap, sta))
        self.assertIn(("disconnect",), sta.calls)
        self.assertIn(("active_set", False), sta.calls)
        self.assertIn(("active_set", False), ap.calls)
        self.assertIs(sta.active_value, False)
        self.assertIs(ap.active_value, False)

    def test_factory_cleanup_failure_poison_blocks_reopen(self):
        network = fake_network_module()
        FakeWLAN.constructor_plan = [None, RuntimeError("STA constructor")]

        original_new = FakeWLAN.__new__

        def constructed_with_failed_cleanup(cls, interface_id):
            result = original_new(cls, interface_id)
            result.plan(
                "active_set",
                OSError("radio stuck"),
                OSError("radio still stuck"),
            )
            return result

        with approved_board(), mock.patch.dict(
            sys.modules, {"network": network}
        ), mock.patch.object(FakeWLAN, "__new__", constructed_with_failed_cleanup):
            with self.assertRaises(NetworkPortError):
                open_wifi_from_board_config()
        self.assertTrue(hardware_module._WIFI_LEASE_POISONED)


if __name__ == "__main__":
    unittest.main()
