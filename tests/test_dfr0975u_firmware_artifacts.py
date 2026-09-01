import hashlib
import json
from pathlib import Path
import unittest


FIRMWARE_ROOT = (
    Path(__file__).resolve().parents[1] / "firmware" / "dfr0975u_n16r8"
)
ARTIFACT_ROOT = FIRMWARE_ROOT / "artifacts"


class TestDFR0975UFirmwareArtifacts(unittest.TestCase):
    def test_retained_artifact_ledger_is_complete_and_exact(self):
        ledger = (ARTIFACT_ROOT / "SHA256SUMS").read_text(
            encoding="ascii"
        ).splitlines()
        self.assertGreater(len(ledger), 0)
        declared = set()
        for line in ledger:
            digest, relative = line.split("  ", 1)
            self.assertEqual(len(digest), 64)
            path = ARTIFACT_ROOT / relative
            self.assertTrue(path.is_file(), relative)
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                digest,
                relative,
            )
            declared.add(relative)

        retained = {
            path.relative_to(ARTIFACT_ROOT).as_posix()
            for path in ARTIFACT_ROOT.rglob("*")
            if path.is_file() and path.name != "SHA256SUMS"
        }
        self.assertEqual(retained, declared)

    def test_full_flash_metadata_paths_exist_and_app_only_is_not_retained(self):
        lines = (ARTIFACT_ROOT / "flash_args").read_text(
            encoding="ascii"
        ).splitlines()
        self.assertEqual(
            lines[0], "--flash_mode dio --flash_freq 80m --flash_size 16MB"
        )
        pairs = {}
        for line in lines[1:]:
            offset, relative = line.split(" ", 1)
            pairs[offset] = relative
            self.assertTrue((ARTIFACT_ROOT / relative).is_file(), relative)
        self.assertEqual(
            pairs,
            {
                "0x0": "bootloader/bootloader.bin",
                "0x10000": "micropython.bin",
                "0x8000": "partition_table/partition-table.bin",
            },
        )

        metadata = json.loads(
            (ARTIFACT_ROOT / "flasher_args.json").read_text(encoding="ascii")
        )
        self.assertEqual(metadata["flash_files"], pairs)
        for relative in metadata["flash_files"].values():
            self.assertTrue((ARTIFACT_ROOT / relative).is_file(), relative)
        for name in (
            "app-flash_args",
            "bootloader-flash_args",
            "partition-table-flash_args",
        ):
            self.assertFalse((ARTIFACT_ROOT / name).exists(), name)

    def test_final_config_and_custom_layout_are_n16r8_fail_closed(self):
        sdkconfig = (ARTIFACT_ROOT / "sdkconfig").read_text(encoding="utf-8")
        for setting in (
            'CONFIG_IDF_TARGET="esp32s3"',
            "CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y",
            "CONFIG_SPIRAM=y",
            "CONFIG_SPIRAM_MODE_OCT=y",
            "CONFIG_SPIRAM_BOOT_INIT=y",
            "CONFIG_SPIRAM_MEMTEST=y",
            "CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=32768",
        ):
            self.assertIn(setting, sdkconfig)
        self.assertIn("# CONFIG_SPIRAM_IGNORE_NOTFOUND is not set", sdkconfig)
        self.assertIn("# CONFIG_ESPTOOLPY_FLASHSIZE_4MB is not set", sdkconfig)

        partitions = (
            FIRMWARE_ROOT
            / "boards"
            / "DFR0975U_N16R8"
            / "partitions-16MiB.csv"
        ).read_text(encoding="ascii")
        self.assertIn("factory,  app,  factory, 0x10000,  0x300000", partitions)
        self.assertIn("vfs,      data, fat,     0x310000, 0xCF0000", partitions)
        self.assertNotIn("ota_", partitions)


if __name__ == "__main__":
    unittest.main()
