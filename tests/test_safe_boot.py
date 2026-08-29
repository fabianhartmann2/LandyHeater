import contextlib
import io
import runpy
import unittest
from unittest import mock


class TestSafeBoot(unittest.TestCase):
    def test_importing_boot_and_main_exposes_no_uart(self):
        boot_namespace = runpy.run_path("boot.py", run_name="safe_boot_test")
        main_namespace = runpy.run_path("main.py", run_name="safe_main_test")
        self.assertNotIn("uart", boot_namespace)
        self.assertNotIn("uart", main_namespace)

    def test_main_does_not_import_protocol_or_machine(self):
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name == "machine" or name.startswith("protocol"):
                raise AssertionError("safe main attempted hardware/protocol import")
            return real_import(name, *args, **kwargs)

        output = io.StringIO()
        with mock.patch("builtins.__import__", side_effect=guarded_import):
            with contextlib.redirect_stdout(output):
                namespace = runpy.run_path(
                    "main.py", run_name="safe_main_import_test"
                )
                namespace["main"]()

        self.assertIn("UART inactive", output.getvalue())
        self.assertIn("protocol TX disabled", output.getvalue())


if __name__ == "__main__":
    unittest.main()
