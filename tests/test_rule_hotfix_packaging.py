import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RuleHotfixPackagingTests(unittest.TestCase):
    def test_installer_is_ascii_only_and_runs_required_checks(self):
        path = PROJECT_ROOT / "packaging" / "Install_Rule_Hotfix.bat"
        data = path.read_bytes()
        source = data.decode("ascii")

        self.assertTrue(all(byte < 128 for byte in data))
        self.assertIn("rule_engine.py", source)
        self.assertIn("claim_subjects", source)
        self.assertIn("-m py_compile", source)
        self.assertIn("--offline-self-test", source)
        self.assertIn("--gui-smoke-test", source)
        self.assertIn("v2.0.3", source)

    def test_builder_is_code_only_and_targets_v203(self):
        source = (
            PROJECT_ROOT / "tools" / "build_rule_hotfix.py"
        ).read_text(encoding="utf-8")

        self.assertIn('VERSION = "2.0.3"', source)
        self.assertIn('"code_only_rule_hotfix"', source)
        self.assertIn('"models_included": False', source)
        self.assertNotIn("MODEL_FILES", source)


if __name__ == "__main__":
    unittest.main()
