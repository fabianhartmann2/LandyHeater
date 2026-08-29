import ast
import contextlib
import io
import runpy
import sys
import unittest
from unittest import mock

import board_config
from app.network_manager import NetworkManager
from hardware.micropython_wifi import open_wifi_from_board_config
from tests.test_phase7_network_smoke import _fake_network
import tools.phase7_network_smoke as radio_support
import tools.phase7_phone_ap_smoke as smoke


_TEST_PASSWORD = "PhoneOnlyTest!42"


class _Clock:
    def __init__(self):
        self.now_ms = 0
        self.sleeps = []
        self.interrupt_after = None
        self.error_type = None
        self.on_sleep = None

    def ticks_ms(self):
        return self.now_ms

    @staticmethod
    def ticks_add(value, delta):
        return value + delta

    @staticmethod
    def ticks_diff(newer, older):
        return newer - older

    def sleep_ms(self, milliseconds):
        self.sleeps.append(milliseconds)
        self.now_ms += milliseconds
        if self.on_sleep is not None:
            self.on_sleep(self)
        if (
            self.interrupt_after is not None
            and self.now_ms >= self.interrupt_after
        ):
            raise KeyboardInterrupt("do not leak {}".format(_TEST_PASSWORD))
        if self.error_type is not None and self.now_ms >= 1000:
            raise self.error_type("do not leak {}".format(_TEST_PASSWORD))
        return None


class _WrappingClock(_Clock):
    PERIOD = 1 << 30
    HALF_PERIOD = PERIOD >> 1

    def __init__(self):
        super().__init__()
        self.now_ms = self.PERIOD - 5000

    @classmethod
    def ticks_add(cls, value, delta):
        return (value + delta) % cls.PERIOD

    @classmethod
    def ticks_diff(cls, newer, older):
        return (
            (newer - older + cls.HALF_PERIOD) % cls.PERIOD
        ) - cls.HALF_PERIOD

    def sleep_ms(self, milliseconds):
        self.sleeps.append(milliseconds)
        self.now_ms = (self.now_ms + milliseconds) % self.PERIOD
        if self.on_sleep is not None:
            self.on_sleep(self)
        return None


def _script_ap_clients(fake, counts):
    access_point = fake.interfaces[fake.IF_AP]
    original = access_point.status
    values = list(counts)
    calls = {"count": 0}

    def status(selector=None):
        if selector != "stations":
            return original(selector)
        index = calls["count"]
        calls["count"] += 1
        if index >= len(values):
            value = values[-1]
        else:
            value = values[index]
        return [(b"phone",)] * value

    access_point.status = status
    return calls


class TestPhase7PhoneAPSmoke(unittest.TestCase):
    def setUp(self):
        board_config.WIFI_RADIO_APPROVED = False
        import hardware.micropython_wifi as wifi

        wifi._WIFI_LEASED = False
        wifi._WIFI_LEASE_POISONED = False

    def tearDown(self):
        board_config.WIFI_RADIO_APPROVED = False
        import hardware.micropython_wifi as wifi

        wifi._WIFI_LEASED = False
        wifi._WIFI_LEASE_POISONED = False

    def _runtime(self, clock):
        return (
            radio_support,
            board_config,
            NetworkManager,
            open_wifi_from_board_config,
            clock.ticks_ms,
            clock.ticks_add,
            clock.ticks_diff,
            clock.sleep_ms,
        )

    def _run_fake(self, fake, clock=None, cleanup=None):
        if clock is None:
            clock = _Clock()
        output = io.StringIO()
        patches = [
            mock.patch.dict(sys.modules, {"network": fake}),
            mock.patch.object(
                smoke, "_load_runtime", return_value=self._runtime(clock)
            ),
            mock.patch.object(
                radio_support, "_verify_platform", return_value=True
            ),
            mock.patch.object(
                smoke, "_memory_free", side_effect=(100000, 90000, 85000)
            ),
        ]
        if cleanup is not None:
            patches.append(mock.patch.object(
                radio_support, "_cleanup_radio", side_effect=cleanup
            ))
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with contextlib.redirect_stdout(output):
                result = smoke.run(
                    smoke.PHONE_AP_CONFIRMATION,
                    _TEST_PASSWORD,
                    window_seconds=60,
                )
        return result, output.getvalue(), clock

    def test_real_factory_manager_path_confirms_stable_client(self):
        fake = _fake_network()
        calls = _script_ap_clients(fake, (0, 0, 1, 1))

        result, output, clock = self._run_fake(fake)

        self.assertEqual(result["scope"], "manual_phone_ap_association")
        self.assertEqual(result["ssid"], "Landy Heater")
        self.assertEqual(result["ap_ip"], "192.168.4.1")
        self.assertEqual(result["clients_confirmed"], 1)
        self.assertIs(result["radio_cleanup_confirmed"], True)
        self.assertIs(result["approval_restored"], True)
        self.assertGreaterEqual(calls["count"], 4)
        self.assertTrue(clock.sleeps)
        self.assertTrue(all(value == 250 for value in clock.sleeps))
        self.assertIn(smoke.PHONE_AP_READY_TOKEN, output)
        self.assertIn(smoke.PHONE_AP_CLIENT_TOKEN, output)
        self.assertIn("clients=1", output)
        self.assertIn("PHONE_CLIENT_CONFIRMED clients=1", output)
        self.assertEqual(output.splitlines()[-1], smoke.PHONE_AP_PASS_TOKEN)
        self.assertNotIn(_TEST_PASSWORD, output + repr(result))
        self.assertNotIn("sta.active.true", fake.log)
        self.assertNotIn("sta.connect", fake.log)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

    def test_ap_loss_during_observation_or_hold_can_never_pass(self):
        cases = (
            (2500, "observation"),
            (33250, "hold"),
        )
        for failure_at, label in cases:
            with self.subTest(label=label):
                fake = _fake_network()
                _script_ap_clients(fake, (0, 0, 1, 1, 1))
                clock = _Clock()
                switched = {"done": False}

                def drop_access_point(current):
                    if not switched["done"] and current.now_ms >= failure_at:
                        fake.interfaces[fake.IF_AP].enabled = False
                        switched["done"] = True

                clock.on_sleep = drop_access_point
                output = io.StringIO()
                with mock.patch.dict(sys.modules, {"network": fake}):
                    with mock.patch.object(
                        smoke,
                        "_load_runtime",
                        return_value=self._runtime(clock),
                    ):
                        with mock.patch.object(
                            radio_support, "_verify_platform", return_value=True
                        ):
                            with mock.patch.object(
                                smoke,
                                "_memory_free",
                                side_effect=(100000, 90000),
                            ):
                                with contextlib.redirect_stdout(output):
                                    with self.assertRaises(RuntimeError):
                                        smoke.run(
                                            smoke.PHONE_AP_CONFIRMATION,
                                            _TEST_PASSWORD,
                                            60,
                                        )
                self.assertTrue(switched["done"])
                self.assertIn(smoke.PHONE_AP_FAIL_TOKEN, output.getvalue())
                self.assertNotIn(smoke.PHONE_AP_PASS_TOKEN, output.getvalue())
                self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
                self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)

    def test_tick_wrap_preserves_window_and_stability_deadlines(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 0, 1, 1, 1))
        clock = _WrappingClock()

        result, output, clock = self._run_fake(fake, clock=clock)

        self.assertEqual(result["clients_confirmed"], 1)
        self.assertEqual(result["stable_seconds"], 30)
        self.assertIn(smoke.PHONE_AP_PASS_TOKEN, output)
        self.assertLess(clock.now_ms, 60000)

    def test_timeout_never_passes_and_still_closes_both_interfaces(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0,))
        clock = _Clock()
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {"network": fake}):
            with mock.patch.object(
                smoke, "_load_runtime", return_value=self._runtime(clock)
            ):
                with mock.patch.object(
                    radio_support, "_verify_platform", return_value=True
                ):
                    with mock.patch.object(
                        smoke, "_memory_free", side_effect=(100000, 90000)
                    ):
                        with contextlib.redirect_stdout(output):
                            with self.assertRaises(RuntimeError):
                                smoke.run(
                                    smoke.PHONE_AP_CONFIRMATION,
                                    _TEST_PASSWORD,
                                    window_seconds=60,
                                )

        self.assertNotIn(smoke.PHONE_AP_PASS_TOKEN, output.getvalue())
        self.assertIn(smoke.PHONE_AP_FAIL_TOKEN, output.getvalue())
        self.assertNotIn(_TEST_PASSWORD, output.getvalue())
        self.assertGreaterEqual(clock.now_ms, 60000)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)
        self.assertIs(board_config.WIFI_RADIO_APPROVED, False)

    def test_confirmation_password_and_window_fail_before_loading(self):
        class EqualitySpoof:
            def __eq__(self, other):
                return True

        cases = (
            (None, _TEST_PASSWORD, 60, RuntimeError),
            (True, _TEST_PASSWORD, 60, RuntimeError),
            (EqualitySpoof(), _TEST_PASSWORD, 60, RuntimeError),
            (smoke.PHONE_AP_CONFIRMATION, None, 60, ValueError),
            (smoke.PHONE_AP_CONFIRMATION, "short", 60, ValueError),
            (smoke.PHONE_AP_CONFIRMATION, "A" * 64, 60, ValueError),
            (smoke.PHONE_AP_CONFIRMATION, "TwelveChars\n!", 60, ValueError),
            (smoke.PHONE_AP_CONFIRMATION, _TEST_PASSWORD, True, ValueError),
            (smoke.PHONE_AP_CONFIRMATION, _TEST_PASSWORD, 59, ValueError),
            (smoke.PHONE_AP_CONFIRMATION, _TEST_PASSWORD, 301, ValueError),
        )
        with mock.patch.object(
            smoke, "_memory_free", side_effect=AssertionError("heap read")
        ) as heap:
            with mock.patch.object(
                smoke, "_load_runtime", side_effect=AssertionError("loaded")
            ) as loader:
                for confirmation, password, window, error_type in cases:
                    with self.subTest(
                        confirmation=confirmation,
                        password=password,
                        window=window,
                    ):
                        with self.assertRaises(error_type):
                            smoke.run(confirmation, password, window)
        heap.assert_not_called()
        loader.assert_not_called()

    def test_driver_error_cannot_leak_temporary_password(self):
        fake = _fake_network()
        access_point = fake.interfaces[fake.IF_AP]

        def exploding_config(**values):
            raise OSError("vendor repeated {}".format(values.get("key")))

        access_point.config = exploding_config
        clock = _Clock()
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {"network": fake}):
            with mock.patch.object(
                smoke, "_load_runtime", return_value=self._runtime(clock)
            ):
                with mock.patch.object(
                    radio_support, "_verify_platform", return_value=True
                ):
                    with mock.patch.object(
                        smoke, "_memory_free", side_effect=(100000, 90000)
                    ):
                        with contextlib.redirect_stdout(output):
                            with self.assertRaises(RuntimeError) as caught:
                                smoke.run(
                                    smoke.PHONE_AP_CONFIRMATION,
                                    _TEST_PASSWORD,
                                    60,
                                )
        rendered = repr(caught.exception) + output.getvalue()
        self.assertNotIn(_TEST_PASSWORD, rendered)
        self.assertNotIn(smoke.PHONE_AP_PASS_TOKEN, rendered)
        self.assertIn(smoke.PHONE_AP_FAIL_TOKEN, rendered)
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)

    def test_keyboard_interrupt_is_blank_and_cleanup_still_runs(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0,))
        clock = _Clock()
        clock.interrupt_after = 1000
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {"network": fake}):
            with mock.patch.object(
                smoke, "_load_runtime", return_value=self._runtime(clock)
            ):
                with mock.patch.object(
                    radio_support, "_verify_platform", return_value=True
                ):
                    with mock.patch.object(
                        smoke, "_memory_free", side_effect=(100000, 90000)
                    ):
                        with contextlib.redirect_stdout(output):
                            with self.assertRaises(KeyboardInterrupt) as caught:
                                smoke.run(
                                    smoke.PHONE_AP_CONFIRMATION,
                                    _TEST_PASSWORD,
                                    60,
                                )
        self.assertEqual(caught.exception.args, ())
        self.assertNotIn(_TEST_PASSWORD, output.getvalue())
        self.assertNotIn(smoke.PHONE_AP_PASS_TOKEN, output.getvalue())
        self.assertIn(smoke.PHONE_AP_FAIL_TOKEN, output.getvalue())
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)

    def test_memory_error_and_system_exit_are_blank_and_cleanup(self):
        for error_type in (MemoryError, SystemExit):
            with self.subTest(error_type=error_type):
                fake = _fake_network()
                _script_ap_clients(fake, (0,))
                clock = _Clock()
                clock.error_type = error_type
                output = io.StringIO()
                with mock.patch.dict(sys.modules, {"network": fake}):
                    with mock.patch.object(
                        smoke,
                        "_load_runtime",
                        return_value=self._runtime(clock),
                    ):
                        with mock.patch.object(
                            radio_support, "_verify_platform", return_value=True
                        ):
                            with mock.patch.object(
                                smoke,
                                "_memory_free",
                                side_effect=(100000, 90000),
                            ):
                                with contextlib.redirect_stdout(output):
                                    with self.assertRaises(error_type) as caught:
                                        smoke.run(
                                            smoke.PHONE_AP_CONFIRMATION,
                                            _TEST_PASSWORD,
                                            60,
                                        )
                self.assertEqual(caught.exception.args, ())
                self.assertNotIn(_TEST_PASSWORD, output.getvalue())
                self.assertIn(smoke.PHONE_AP_FAIL_TOKEN, output.getvalue())
                self.assertNotIn(smoke.PHONE_AP_PASS_TOKEN, output.getvalue())
                self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
                self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)

    def test_cleanup_failure_or_low_heap_can_never_emit_pass(self):
        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1))
        original_cleanup = radio_support._cleanup_radio

        def clean_but_report_failure(manager, port, network_module):
            original_cleanup(manager, port, network_module)
            return False

        clock = _Clock()
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {"network": fake}):
            with mock.patch.object(
                smoke, "_load_runtime", return_value=self._runtime(clock)
            ):
                with mock.patch.object(
                    radio_support, "_verify_platform", return_value=True
                ):
                    with mock.patch.object(
                        smoke, "_memory_free", side_effect=(100000, 90000)
                    ):
                        with mock.patch.object(
                            radio_support,
                            "_cleanup_radio",
                            side_effect=clean_but_report_failure,
                        ):
                            with contextlib.redirect_stdout(output):
                                with self.assertRaises(RuntimeError):
                                    smoke.run(
                                        smoke.PHONE_AP_CONFIRMATION,
                                        _TEST_PASSWORD,
                                        60,
                                    )
        self.assertNotIn(smoke.PHONE_AP_PASS_TOKEN, output.getvalue())
        self.assertIn(smoke.PHONE_AP_FAIL_TOKEN, output.getvalue())
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)

        fake = _fake_network()
        _script_ap_clients(fake, (0, 1, 1))
        clock = _Clock()
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {"network": fake}):
            with mock.patch.object(
                smoke, "_load_runtime", return_value=self._runtime(clock)
            ):
                with mock.patch.object(
                    radio_support, "_verify_platform", return_value=True
                ):
                    with mock.patch.object(
                        smoke, "_memory_free", side_effect=(100000, 90000, 1)
                    ):
                        with contextlib.redirect_stdout(output):
                            with self.assertRaises(RuntimeError):
                                smoke.run(
                                    smoke.PHONE_AP_CONFIRMATION,
                                    _TEST_PASSWORD,
                                    60,
                                )
        self.assertNotIn(smoke.PHONE_AP_PASS_TOKEN, output.getvalue())
        self.assertIs(fake.interfaces[fake.IF_AP].enabled, False)
        self.assertIs(fake.interfaces[fake.IF_STA].enabled, False)

    def test_import_is_inert_and_source_has_no_http_or_socket_path(self):
        real_import = __import__
        forbidden = (
            "board_config",
            "network",
            "machine",
            "socket",
            "hardware",
            "app.network_manager",
            "tools.phase7_network_smoke",
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
                    "tools/phase7_phone_ap_smoke.py",
                    run_name="phase7_phone_ap_smoke_import_test",
                )
        self.assertIn("run", namespace)
        self.assertEqual(output.getvalue(), "")

        with open("tools/phase7_phone_ap_smoke.py", "r", encoding="utf-8") as file:
            tree = ast.parse(file.read())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        self.assertNotIn("socket", imports)
        self.assertNotIn("machine", imports)


if __name__ == "__main__":
    unittest.main()
