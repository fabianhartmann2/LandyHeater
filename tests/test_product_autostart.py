import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "firmware" / "phase13_heater_frozen"


class TestProductAutostart(unittest.TestCase):
    def test_candidate_manifest_freezes_private_main_and_product_owner(self):
        manifest = (CANDIDATE / "manifest.py").read_text(encoding="utf-8")
        modules = (CANDIDATE / "FROZEN_MODULES.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertIn('module("main.py", base_path=CANDIDATE_ROOT, opt=0)', manifest)
        self.assertIn('"product_runtime.py"', manifest)
        self.assertIn("firmware/phase13_heater_frozen/main.py", modules)
        self.assertIn("app/product_runtime.py", modules)

    def test_candidate_main_prefers_frozen_modules_before_product_import(self):
        source = (CANDIDATE / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn('sys.path.insert(0, ".frozen")', source)
        self.assertLess(
            source.index("_prefer_frozen_modules()"),
            source.index("from app.product_runtime import build_product_runtime"),
        )
        self.assertIn("LANDY_HEATER_PRODUCT_AUTOSTART_V1", source)
        self.assertIn("runtime.shutdown_step()", source)
        self.assertIsInstance(tree, ast.Module)

    def test_repository_default_main_remains_safe_and_passive(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("safe boot", source)
        self.assertNotIn("build_product_runtime", source)


if __name__ == "__main__":
    unittest.main()
