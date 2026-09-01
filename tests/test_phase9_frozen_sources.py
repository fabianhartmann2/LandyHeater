import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "firmware" / "phase9_frozen"


class TestPhase9FrozenSources(unittest.TestCase):
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

    def test_source_ledger_matches_every_candidate_byte(self):
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
        for relative, expected in entries.items():
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, relative)

    def test_phase8_retained_manifest_is_not_relabelled(self):
        old = (ROOT / "firmware" / "phase8_frozen" / "manifest.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("web_application.py", old)
        self.assertNotIn("web_assets.py", old)


if __name__ == "__main__":
    unittest.main()
