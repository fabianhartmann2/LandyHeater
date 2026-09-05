import unittest

from tools import phase11_diagnostics_phone_smoke as smoke


class _Gateway:
    def __init__(self):
        self.validated = {target: 1 for target in smoke._STATIC_TARGETS}
        self.validated[smoke._DIAGNOSTICS_PREFIX] = 1
        self.validated[smoke._CAPTURE_EXPORT_PREFIX] = 1
        self.successful_starts = 1
        self.successful_stops = 1
        self.exported = 1


class _Observer:
    def __init__(self):
        self.completed = {target: 1 for target in smoke._STATIC_TARGETS}
        self.completed[
            smoke._DIAGNOSTICS_PREFIX + "event_after=0&protocol_after=0"
        ] = 1
        self.completed[smoke._CAPTURE_EXPORT_PREFIX + "offset=0&limit=4"] = 1


class TestPhase11DiagnosticsPhoneSmoke(unittest.TestCase):
    def test_required_fragment_uses_the_asset_route(self):
        self.assertIn("/assets/diagnostics.html", smoke._STATIC_TARGETS)
        self.assertNotIn("/diagnostics.html", smoke._STATIC_TARGETS)

    def test_completion_uses_gateway_truth_for_non_get_mutations(self):
        gateway = _Gateway()
        observer = _Observer()
        self.assertTrue(smoke._all_complete(gateway, observer))

        gateway.successful_stops = 0
        self.assertFalse(smoke._all_complete(gateway, observer))

    def test_get_wire_prefixes_remain_required(self):
        gateway = _Gateway()
        observer = _Observer()
        del observer.completed[
            smoke._CAPTURE_EXPORT_PREFIX + "offset=0&limit=4"
        ]
        self.assertFalse(smoke._all_complete(gateway, observer))


if __name__ == "__main__":
    unittest.main()
