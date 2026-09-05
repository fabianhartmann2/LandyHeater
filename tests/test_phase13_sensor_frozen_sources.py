import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "firmware" / "phase13_sensor_frozen"


class TestPhase13SensorFrozenSources(unittest.TestCase):
    def test_candidate_is_exact_bounded_source_closure(self):
        files = (CANDIDATE / "FROZEN_MODULES.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(len(files), 47)
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual(files[0], "board_config.py")
        self.assertIn("app/sensor_composition.py", files)
        for excluded in ("boot.py", "main.py"):
            self.assertNotIn(excluded, files)

        entries = {}
        for line in (CANDIDATE / "CURRENT_FROZEN_SOURCES.sha256").read_text(
            encoding="utf-8"
        ).splitlines():
            digest, relative = line.split("  ", 1)
            entries[relative] = digest
        self.assertEqual(list(entries), files)
        for relative in files:
            source = ROOT / relative
            self.assertTrue(source.is_file(), relative)
            self.assertEqual(
                hashlib.sha256(source.read_bytes()).hexdigest(),
                entries[relative],
                relative,
            )

    def test_manifest_declares_every_frozen_module(self):
        manifest = (CANDIDATE / "manifest.py").read_text(encoding="utf-8")
        self.assertIn('module("board_config.py",', manifest)
        for relative in (CANDIDATE / "FROZEN_MODULES.txt").read_text(
            encoding="utf-8"
        ).splitlines()[1:]:
            package, name = relative.split("/", 1)
            self.assertIn('package(\n    "{}",'.format(package), manifest)
            self.assertIn('        "{}",'.format(name), manifest)


if __name__ == "__main__":
    unittest.main()
