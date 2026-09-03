import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "firmware" / "phase10_1_frozen"


class TestPhase101FrozenSources(unittest.TestCase):
    def test_candidate_is_exact_bounded_source_closure(self):
        files = (CANDIDATE / "FROZEN_MODULES.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(len(files), 44)
        self.assertEqual(len(files), len(set(files)))
        self.assertIn("adapters/micropython_captive_dns.py", files)
        self.assertIn("app/discovery_composition.py", files)
        for excluded in ("boot.py", "main.py", "board_config.py"):
            self.assertNotIn(excluded, files)
        for relative in files:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_source_ledger_matches_current_closure(self):
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
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, digest, relative)


if __name__ == "__main__":
    unittest.main()
