import pathlib
import unittest
from unittest import mock

from adapters.micropython_bonjour import (
    BONJOUR_INSTANCE,
    BONJOUR_PATH,
    BONJOUR_PORT,
    BONJOUR_SERVICE,
    BonjourHTTPError,
    MicroPythonBonjourService,
)


class FakeNative:
    def __init__(self, advertise_results=None, remove_results=None):
        self.advertise_results = list(advertise_results or [True])
        self.remove_results = list(remove_results or [True])
        self.calls = []

    @staticmethod
    def _result(results):
        result = results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def advertise_http(self):
        self.calls.append("advertise")
        return self._result(self.advertise_results)

    def remove_http(self):
        self.calls.append("remove")
        return self._result(self.remove_results)


class TestMicroPythonBonjourService(unittest.TestCase):
    def test_construction_is_inert_and_start_only_validates_contract(self):
        native = FakeNative()
        service = MicroPythonBonjourService(native)
        self.assertEqual(native.calls, [])
        self.assertFalse(service.snapshot()["started"])
        self.assertTrue(service.start())
        self.assertFalse(service.start())
        self.assertEqual(native.calls, [])

    def test_lazy_native_import_occurs_only_on_explicit_start(self):
        native = FakeNative()
        service = MicroPythonBonjourService()
        with mock.patch(
            "adapters.micropython_bonjour._load_native_module",
            return_value=native,
        ) as loader:
            self.assertEqual(loader.call_count, 0)
            self.assertTrue(service.start())
            self.assertEqual(loader.call_count, 1)

    def test_ready_transition_is_idempotent_and_reconnects_cleanly(self):
        native = FakeNative(
            advertise_results=[True, True], remove_results=[True, True]
        )
        service = MicroPythonBonjourService(native)
        service.start()
        self.assertFalse(service.update(False))
        self.assertTrue(service.update(True))
        self.assertFalse(service.update(True))
        self.assertTrue(service.update(False))
        self.assertFalse(service.update(False))
        self.assertTrue(service.update(True))
        self.assertEqual(native.calls, ["advertise", "remove", "advertise"])
        snapshot = service.snapshot()
        self.assertTrue(snapshot["advertised"])
        self.assertEqual(snapshot["registrations"], 2)
        self.assertEqual(snapshot["removals"], 1)
        self.assertIsNone(service.deinit())
        self.assertEqual(native.calls[-1], "remove")
        self.assertEqual(service.snapshot()["removals"], 2)

    def test_native_already_present_or_absent_is_a_successful_state_change(self):
        native = FakeNative(advertise_results=[False], remove_results=[False])
        service = MicroPythonBonjourService(native)
        service.start()
        self.assertTrue(service.update(True))
        self.assertTrue(service.advertised)
        self.assertTrue(service.update(False))
        self.assertFalse(service.advertised)

    def test_advertise_failure_is_faulted_without_claiming_publication(self):
        native = FakeNative(advertise_results=[OSError(0x101)])
        service = MicroPythonBonjourService(native)
        service.start()
        with self.assertRaisesRegex(BonjourHTTPError, "advertise_failed"):
            service.update(True)
        snapshot = service.snapshot()
        self.assertTrue(snapshot["faulted"])
        self.assertFalse(snapshot["advertised"])
        self.assertEqual(snapshot["last_error"], "bonjour_advertise_failed")
        with self.assertRaisesRegex(BonjourHTTPError, "faulted"):
            service.update(True)

    def test_cleanup_failure_preserves_advertised_truth_for_retry(self):
        native = FakeNative(
            advertise_results=[True],
            remove_results=[OSError(0x101), True],
        )
        service = MicroPythonBonjourService(native)
        service.start()
        service.update(True)
        with self.assertRaisesRegex(BonjourHTTPError, "cleanup_failed"):
            service.deinit()
        snapshot = service.snapshot()
        self.assertTrue(snapshot["advertised"])
        self.assertFalse(snapshot["closed"])
        self.assertIsNone(service.deinit())
        self.assertFalse(service.snapshot()["advertised"])

    def test_invalid_native_contract_and_readiness_type_are_rejected(self):
        with self.assertRaisesRegex(BonjourHTTPError, "start_failed"):
            MicroPythonBonjourService(object()).start()
        service = MicroPythonBonjourService(FakeNative())
        service.start()
        for value in (1, None, "yes"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.update(value)

    def test_snapshot_exposes_only_fixed_service_metadata(self):
        snapshot = MicroPythonBonjourService(FakeNative()).snapshot()
        self.assertEqual(snapshot["instance"], BONJOUR_INSTANCE)
        self.assertEqual(snapshot["service"], BONJOUR_SERVICE)
        self.assertEqual(snapshot["port"], BONJOUR_PORT)
        self.assertEqual(snapshot["path"], BONJOUR_PATH)

    def test_native_source_exposes_no_caller_controlled_registration(self):
        source = pathlib.Path(
            "native/landy_mdns/modlandy_mdns.c"
        ).read_text(encoding="utf-8")
        self.assertIn('LANDY_MDNS_INSTANCE "Landy Heater"', source)
        self.assertIn('LANDY_MDNS_SERVICE "_http"', source)
        self.assertIn('LANDY_MDNS_PROTO "_tcp"', source)
        self.assertIn('LANDY_MDNS_PORT (80)', source)
        self.assertIn('{"path", "/"}', source)
        self.assertIn("mdns_service_add(", source)
        self.assertIn("mdns_service_exists_with_instance(", source)
        self.assertIn("mdns_service_remove_for_host(", source)
        self.assertNotIn("MP_DEFINE_CONST_FUN_OBJ_VAR", source)


if __name__ == "__main__":
    unittest.main()
