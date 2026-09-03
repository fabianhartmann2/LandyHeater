import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "firmware" / "phase10_frozen"


class TestPhase10FrozenSources(unittest.TestCase):
    def test_candidate_is_exact_bounded_source_closure(self):
        files = (CANDIDATE / "FROZEN_MODULES.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(len(files), 42)
        self.assertEqual(len(files), len(set(files)))
        self.assertIn("app/web_application.py", files)
        self.assertIn("app/web_assets.py", files)
        for excluded in ("boot.py", "main.py", "board_config.py"):
            self.assertNotIn(excluded, files)
        for relative in files:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_historical_source_ledger_is_complete_and_well_formed(self):
        """The Phase-10 ledger pins its flashed bytes, not later phases."""

        entries = {}
        for line in (CANDIDATE / "CURRENT_FROZEN_SOURCES.sha256").read_text(
            encoding="utf-8"
        ).splitlines():
            digest, relative = line.split("  ", 1)
            entries[relative] = digest
        files = (CANDIDATE / "FROZEN_MODULES.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(list(entries), files)
        for relative, digest in entries.items():
            self.assertEqual(len(digest), 64, relative)
            self.assertTrue(all(value in "0123456789abcdef" for value in digest))
        build_info = (CANDIDATE / "BUILD_INFO.md").read_text(encoding="utf-8")
        ledger_digest = hashlib.sha256(
            (CANDIDATE / "CURRENT_FROZEN_SOURCES.sha256").read_bytes()
        ).hexdigest()
        self.assertIn(ledger_digest, build_info)

    def test_build_record_pins_phase10_input_files(self):
        build_info = (CANDIDATE / "BUILD_INFO.md").read_text(encoding="utf-8")
        for name in (
            "manifest.py",
            "FROZEN_MODULES.txt",
            "CURRENT_FROZEN_SOURCES.sha256",
        ):
            digest = hashlib.sha256((CANDIDATE / name).read_bytes()).hexdigest()
            self.assertIn(digest, build_info, name)


if __name__ == "__main__":
    unittest.main()
