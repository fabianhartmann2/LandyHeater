import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import tools.phase7_config_capacity_smoke as smoke


class TestPhase7ConfigCapacitySmoke(unittest.TestCase):
    def test_maximum_useful_roundtrip_is_secret_free_and_cleans_files(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = io.StringIO()
        with mock.patch.object(
            smoke, "CONFIG_BASE_PATH", os.path.join(directory.name, "config")
        ), mock.patch.object(
            smoke, "LEDGER_BASE_PATH", os.path.join(directory.name, "ledger")
        ), contextlib.redirect_stdout(output):
            result = smoke.run(smoke.SOFTWARE_ONLY_CONFIRMATION)
        self.assertEqual(result["timers"], 32)
        self.assertEqual(result["networks"], 8)
        self.assertGreaterEqual(result["canonical_bytes"], 7 * 1024)
        self.assertLessEqual(result["canonical_bytes"], 8 * 1024)
        self.assertEqual(
            output.getvalue().splitlines()[-1],
            smoke.PHASE7_CONFIG_CAPACITY_PASS_TOKEN,
        )
        self.assertEqual(os.listdir(directory.name), [])

    def test_confirmation_is_type_strict_and_import_is_inert(self):
        for value in (None, True, 1, "wrong"):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    smoke.run(value)

    def test_low_heap_can_never_emit_the_pass_token(self):
        output = io.StringIO()
        with mock.patch.object(
            smoke, "_heap", return_value=1
        ), contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, "below 32 KiB"):
                smoke.run(smoke.SOFTWARE_ONLY_CONFIRMATION)
        self.assertNotIn(smoke.PHASE7_CONFIG_CAPACITY_PASS_TOKEN, output.getvalue())


if __name__ == "__main__":
    unittest.main()
