import contextlib
import io
import runpy
import sys
import types
import unittest
from unittest import mock

import board_config
import tools.phase7_network_smoke as smoke


class _FakeInterface:
    _MISSING = object()

    def __init__(self, kind, network_module):
        self.kind = kind
        self.network_module = network_module
        self.enabled = False
        self.connected = False
        self.raw_status = network_module.STAT_IDLE
        self.reconnects = None

    def active(self, value=_MISSING):
        if value is self._MISSING:
            return self.enabled
        if type(value) is not bool:
            raise ValueError("active must be bool")
        self.network_module.log.append("{}.active.{}".format(
            self.kind, str(value).lower()
        ))
        self.enabled = value
        if not value and self.kind == "sta":
            self.connected = False
            self.raw_status = self.network_module.STAT_IDLE
        return self.enabled

    def config(self, **values):
        if self.kind == "ap":
            self.network_module.log.append("ap.config")
            self.network_module.ap_values = dict(values)
        else:
            self.network_module.log.append("sta.config")
            self.reconnects = values.get("reconnects")
        return None

    def status(self, selector=None):
        if self.kind == "ap":
            if selector != "stations":
                raise ValueError("AP selector differs")
            return []
        if selector == "rssi":
            return -50
        if selector is not None:
            raise ValueError("STA selector differs")
        return self.raw_status

    def ifconfig(self):
        if self.kind == "ap":
            return ("192.168.4.1", "255.255.255.0", "192.168.4.1", "0.0.0.0")
        return ("0.0.0.0", "255.255.255.0", "0.0.0.0", "0.0.0.0")

    def connect(self, ssid, password=None):
        self.network_module.log.append("sta.connect")
        if self.network_module.connect_error:
            # Deliberately hostile driver text verifies that neither wrapper
            # nor smoke leaks the key it supplied.
            raise OSError("driver rejected key {}".format(password))
        self.raw_status = self.network_module.STAT_CONNECTING
        return None

    def disconnect(self):
        self.network_module.log.append("sta.disconnect")
        self.connected = False
        self.raw_status = self.network_module.STAT_IDLE
        return None

    def isconnected(self):
        return self.connected


class _FakeWLAN:
    IF_AP = 1
    IF_STA = 0
    SEC_WPA2 = 3
    STAT_IDLE = 0
    STAT_CONNECTING = 1
    STAT_WRONG_PASSWORD = -3
    STAT_NO_AP_FOUND = -2
    STAT_CONNECT_FAIL = -1
    STAT_GOT_IP = 3

    def __init__(self, module):
        self.module = module

    def __call__(self, interface_id):
        return self.module.interfaces[interface_id]


def _fake_network(connect_error=False):
    module = types.ModuleType("network")
    for name in (
        "IF_AP",
        "IF_STA",
        "SEC_WPA2",
        "STAT_IDLE",
        "STAT_CONNECTING",
        "STAT_WRONG_PASSWORD",
        "STAT_NO_AP_FOUND",
        "STAT_CONNECT_FAIL",
        "STAT_GOT_IP",
    ):
        setattr(module, name, getattr(_FakeWLAN, name))
    module.log = []
    module.connect_error = connect_error
    module.ap_values = {}
    module.country_value = None
    module.hostname_value = None

    def country(value=_FakeInterface._MISSING):
        if value is _FakeInterface._MISSING:
            return module.country_value
        module.log.append("country.set")
        module.country_value = value
        return None

    module.country = country

    def hostname(value=_FakeInterface._MISSING):
        if value is _FakeInterface._MISSING:
            return module.hostname_value
        module.log.append("hostname.set")
        module.hostname_value = value
        return None

    module.hostname = hostname
    module.interfaces = {
        module.IF_AP: _FakeInterface("ap", module),
        module.IF_STA: _FakeInterface("sta", module),
    }
    module.WLAN = _FakeWLAN(module)
    return module


class TestPhase7NetworkSmoke(unittest.TestCase):
    def setUp(self):
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

    def tearDown(self):
        board_config.WIFI_RADIO_APPROVED = False
        # A deliberately failed test must not poison another unit test.  The
        # assertions still verify that ordinary smoke cleanup releases it.
        import hardware.micropython_wifi as wifi

        wifi._WIFI_LEASED = False
        wifi._WIFI_LEASE_POISONED = False

    def _run_fake(self, fake, iterations=1, memory=(100000, 90000, 85000)):
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {"network": fake}):
            with mock.patch.object(smoke, "_verify_platform", return_value=True):
                with mock.patch.object(smoke, "_memory_free", side_effect=memory):
                    with contextlib.redirect_stdout(output):
                        result = smoke.run(
                            smoke.RADIO_SMOKE_CONFIRMATION,
                            iterations=iterations,
                        )
        return result, output.getvalue()

    def test_production_factory_manager_and_fake_network_pass(self):
        fake = _fake_network()
        result, output = self._run_fake(fake, iterations=2)

        self.assertEqual(result["phase"], 7)
        self.assertEqual(result["scope"], "explicit_wifi_radio")
        self.assertEqual(result["passed"], 2)
        self.assertEqual(result["station_attempts"], 2)
        self.assertEqual(result["ap_ssid"], "Landy Heater")
        self.assertEqual(result["ap_ip"], "192.168.4.1")
        self.assertIs(result["ap_available_during_station_attempt"], True)
        self.assertIs(result["ap_only_mdns_ready"], False)
        self.assertIs(result["radio_cleanup_confirmed"], True)
        self.assertIs(result["approval_restored"], True)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
        self.assertEqual(fake.ap_values["ssid"], "Landy Heater")
        self.assertEqual(fake.ap_values["security"], fake.SEC_WPA2)
        self.assertEqual(fake.interfaces[fake.IF_STA].reconnects, 0)
        self.assertLess(
            fake.log.index("ap.active.true"),
            fake.log.index("sta.active.true"),
        )
        self.assertLess(
            fake.log.index("sta.active.true"),
            fake.log.index("sta.connect"),
        )
        self.assertEqual(output.splitlines()[-1], smoke.PHASE7_PASS_TOKEN)
        self.assertNotIn(smoke._SMOKE_AP_PASSWORD, repr(result) + output)
        self.assertNotIn(smoke._SMOKE_STA_PASSWORD, repr(result) + output)

    def test_confirmation_is_exact_and_type_strict_before_any_loading(self):
        class EqualitySpoof:
            def __eq__(self, other):
                return True

        with mock.patch.object(
            smoke, "_memory_free", side_effect=AssertionError("heap read")
        ) as heap:
            with mock.patch.object(
                smoke, "_load_runtime", side_effect=AssertionError("loaded")
            ) as loader:
                for value in (None, True, "yes", EqualitySpoof()):
                    with self.subTest(value=value):
                        with self.assertRaises(RuntimeError):
                            smoke.run(value, 1)
        heap.assert_not_called()
        loader.assert_not_called()

    def test_iteration_count_is_strictly_bounded_before_hardware(self):
        with mock.patch.object(
            smoke, "_memory_free", side_effect=AssertionError("heap read")
        ) as heap:
            for value in (None, False, True, 0, -1, 5, "1"):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        smoke.run(smoke.RADIO_SMOKE_CONFIRMATION, value)
        heap.assert_not_called()

    def test_import_is_inert_and_never_imports_hardware(self):
        real_import = __import__
        forbidden = (
            "board_config",
            "network",
            "machine",
            "onewire",
            "ds18x20",
            "hardware",
            "app.network_manager",
        )

        def guarded_import(name, *args, **kwargs):
            for blocked in forbidden:
                if name == blocked or name.startswith(blocked + "."):
                    raise AssertionError("forbidden import: {}".format(name))
            return real_import(name, *args, **kwargs)

        output = io.StringIO()
        with mock.patch("builtins.__import__", side_effect=guarded_import):
            with contextlib.redirect_stdout(output):
                namespace = runpy.run_path(
                    "tools/phase7_network_smoke.py",
                    run_name="phase7_network_smoke_import_test",
                )
        self.assertIn("run", namespace)
        self.assertEqual(output.getvalue(), "")

    def test_connect_failure_is_sanitized_and_cleanup_is_still_confirmed(self):
        fake = _fake_network(connect_error=True)
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {"network": fake}):
            with mock.patch.object(smoke, "_verify_platform", return_value=True):
                with mock.patch.object(
                    smoke, "_memory_free", side_effect=(100000, 90000)
                ):
                    with contextlib.redirect_stdout(output):
                        with self.assertRaises(RuntimeError) as caught:
                            smoke.run(smoke.RADIO_SMOKE_CONFIRMATION, 1)

        rendered = str(caught.exception) + output.getvalue()
        self.assertNotIn(smoke.PHASE7_PASS_TOKEN, rendered)
        self.assertNotIn(smoke._SMOKE_AP_PASSWORD, rendered)
        self.assertNotIn(smoke._SMOKE_STA_PASSWORD, rendered)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)

    def test_cleanup_or_heap_failure_can_never_print_the_pass_token(self):
        fake = _fake_network()
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {"network": fake}):
            with mock.patch.object(smoke, "_verify_platform", return_value=True):
                with mock.patch.object(
                    smoke,
                    "_interfaces_inactive",
                    return_value=False,
                ):
                    with mock.patch.object(
                        smoke, "_memory_free", side_effect=(100000, 90000)
                    ):
                        with contextlib.redirect_stdout(output):
                            with self.assertRaises(RuntimeError):
                                smoke.run(smoke.RADIO_SMOKE_CONFIRMATION, 1)
        self.assertNotIn(smoke.PHASE7_PASS_TOKEN, output.getvalue())
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

        fake = _fake_network()
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {"network": fake}):
            with mock.patch.object(smoke, "_verify_platform", return_value=True):
                with mock.patch.object(
                    smoke, "_memory_free", side_effect=(100000, 90000, 1)
                ):
                    with contextlib.redirect_stdout(output):
                        with self.assertRaises(RuntimeError):
                            smoke.run(smoke.RADIO_SMOKE_CONFIRMATION, 1)
        self.assertNotIn(smoke.PHASE7_PASS_TOKEN, output.getvalue())
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)

    def test_unrelated_hardware_imports_are_absent_during_fake_run(self):
        fake = _fake_network()
        real_import = __import__
        forbidden = ("machine", "onewire", "ds18x20")

        def guarded_import(name, *args, **kwargs):
            for blocked in forbidden:
                if name == blocked or name.startswith(blocked + "."):
                    raise AssertionError("unrelated hardware import: {}".format(name))
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            result, _ = self._run_fake(fake)
        self.assertEqual(result["passed"], 1)


if __name__ == "__main__":
    unittest.main()
