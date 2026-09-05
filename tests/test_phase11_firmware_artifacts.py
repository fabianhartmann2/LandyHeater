import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "firmware" / "phase11_frozen"
ARTIFACTS = CANDIDATE / "artifacts"


class TestPhase11FirmwareArtifacts(unittest.TestCase):
    def test_retained_artifact_ledger_is_complete_and_exact(self):
        declared = set()
        for line in (ARTIFACTS / "SHA256SUMS").read_text(
            encoding="ascii"
        ).splitlines():
            digest, relative = line.split("  ", 1)
            path = ARTIFACTS / relative
            self.assertTrue(path.is_file(), relative)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            declared.add(relative)
        retained = {
            path.relative_to(ARTIFACTS).as_posix()
            for path in ARTIFACTS.rglob("*")
            if path.is_file() and path.name != "SHA256SUMS"
        }
        self.assertEqual(retained, declared)

    def test_app_candidate_hash_size_and_layout_are_exact(self):
        application = (ARTIFACTS / "micropython.bin").read_bytes()
        combined = (ARTIFACTS / "firmware.bin").read_bytes()
        bootloader = (ARTIFACTS / "bootloader/bootloader.bin").read_bytes()
        partition = (
            ARTIFACTS / "partition_table/partition-table.bin"
        ).read_bytes()
        self.assertEqual(len(application), 2_086_960)
        self.assertEqual(
            hashlib.sha256(application).hexdigest(),
            "274234961f43551526b843ca7b27b3ead594cb5e93bf079b39f4ea838ab2c566",
        )
        self.assertEqual(combined[: len(bootloader)], bootloader)
        self.assertEqual(combined[0x8000:0x8000 + len(partition)], partition)
        self.assertEqual(combined[0x10000:], application)
        self.assertEqual(len(combined), 0x10000 + len(application))
        self.assertLess(len(combined), 0x310000)

    def test_flash_metadata_is_exact_and_resolvable(self):
        lines = (ARTIFACTS / "flash_args").read_text(
            encoding="ascii"
        ).splitlines()
        self.assertEqual(
            lines[0], "--flash_mode dio --flash_freq 80m --flash_size 16MB"
        )
        pairs = dict(line.split(" ", 1) for line in lines[1:])
        self.assertEqual(
            pairs,
            {
                "0x0": "bootloader/bootloader.bin",
                "0x10000": "micropython.bin",
                "0x8000": "partition_table/partition-table.bin",
            },
        )
        metadata = json.loads(
            (ARTIFACTS / "flasher_args.json").read_text(encoding="ascii")
        )
        self.assertEqual(metadata["flash_files"], pairs)

    def test_final_config_is_n16r8_fail_closed(self):
        sdkconfig = (ARTIFACTS / "sdkconfig").read_text(encoding="utf-8")
        for setting in (
            'CONFIG_IDF_TARGET="esp32s3"',
            "CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y",
            "CONFIG_SPIRAM=y",
            "CONFIG_SPIRAM_MODE_OCT=y",
            "CONFIG_SPIRAM_SPEED_80M=y",
            "CONFIG_SPIRAM_BOOT_INIT=y",
            "CONFIG_SPIRAM_MEMTEST=y",
            "CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=8192",
            "CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=32768",
        ):
            self.assertIn(setting, sdkconfig)
        self.assertIn("# CONFIG_SPIRAM_IGNORE_NOTFOUND is not set", sdkconfig)

    def test_bootloader_and_partition_remain_unchanged(self):
        accepted = (
            ROOT / "firmware" / "phase10_1_portal_fixed_frozen" / "artifacts"
        )
        self.assertEqual(
            (accepted / "bootloader/bootloader.bin").read_bytes(),
            (ARTIFACTS / "bootloader/bootloader.bin").read_bytes(),
        )
        self.assertEqual(
            (accepted / "partition_table/partition-table.bin").read_bytes(),
            (ARTIFACTS / "partition_table/partition-table.bin").read_bytes(),
        )

    def test_build_record_pins_artifact_ledger(self):
        build_info = (CANDIDATE / "BUILD_INFO.md").read_text(encoding="utf-8")
        digest = hashlib.sha256(
            (ARTIFACTS / "SHA256SUMS").read_bytes()
        ).hexdigest()
        self.assertIn(digest, build_info)


if __name__ == "__main__":
    unittest.main()
