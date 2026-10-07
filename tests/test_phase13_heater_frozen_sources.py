import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "firmware" / "phase13_heater_frozen"


class TestPhase13HeaterFrozenSources(unittest.TestCase):
    def test_candidate_is_exact_bounded_source_closure(self):
        files = (CANDIDATE / "FROZEN_MODULES.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(len(files), 50)
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual(
            files[0],
            "firmware/phase13_heater_frozen/board_config.py",
        )
        self.assertEqual(
            files[1],
            "firmware/phase13_heater_frozen/main.py",
        )
        self.assertIn("app/heater_composition.py", files)
        self.assertIn("app/product_runtime.py", files)
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

    def test_private_profile_opens_only_two_runtime_locks(self):
        safe = (ROOT / "board_config.py").read_text(encoding="utf-8")
        active = (CANDIDATE / "board_config.py").read_text(encoding="utf-8")
        normalized = active.replace(
            "UART_PINS_APPROVED = True",
            "UART_PINS_APPROVED = False",
            1,
        ).replace(
            "UART_PROTOCOL_TX_ENABLED = True",
            "UART_PROTOCOL_TX_ENABLED = False",
            1,
        )
        self.assertEqual(normalized, safe)
        self.assertIn('UART_TX_INTERFACE = "direct_level_shifter"', active)
        self.assertIn("UART_DIRECT_TX_APPROVED = True", active)
        self.assertIn("UART_TX_GATE_APPROVED = False", active)
        self.assertIn("I2C_PINS_APPROVED = False", active)
        self.assertIn("WIFI_RADIO_APPROVED = False", active)
        self.assertIn("ONEWIRE_PIN_APPROVED = True", active)

    def test_manifest_declares_every_frozen_module(self):
        manifest = (CANDIDATE / "manifest.py").read_text(encoding="utf-8")
        self.assertIn('module("board_config.py", base_path=CANDIDATE_ROOT', manifest)
        self.assertIn('module("main.py", base_path=CANDIDATE_ROOT', manifest)
        for relative in (CANDIDATE / "FROZEN_MODULES.txt").read_text(
            encoding="utf-8"
        ).splitlines()[2:]:
            package, name = relative.split("/", 1)
            self.assertIn('package(\n    "{}",'.format(package), manifest)
            self.assertIn('        "{}",'.format(name), manifest)


if __name__ == "__main__":
    unittest.main()
