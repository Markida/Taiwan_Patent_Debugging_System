import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CompanyPackagingContractTests(unittest.TestCase):
    def test_launcher_avoids_case_duplicate_environment_dictionary(self):
        source = (
            PROJECT_ROOT / "packaging" / "Saint-IslandPatentOCR.Launcher.cs"
        ).read_text(encoding="utf-8")

        self.assertNotIn("startInfo.EnvironmentVariables", source)
        self.assertIn('Environment.SetEnvironmentVariable("PATH"', source)
        self.assertIn('[assembly: AssemblyFileVersion("1.9.0.0")]', source)

    def test_update_installer_uses_current_executable_with_legacy_fallback(self):
        installer_path = PROJECT_ROOT / "packaging" / "Install_Update.bat"
        installer = installer_path.read_text(encoding="ascii")

        self.assertIn("Saint-Island_Patent_MDS.exe", installer)
        self.assertIn("Saint-IslandPatentOCR.exe", installer)
        self.assertIn("--offline-self-test", installer)
        self.assertIn("--gui-smoke-test", installer)
        self.assertFalse(installer_path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_diagnostic_and_offline_check_are_ascii_only(self):
        for relative_path in (
            "Offline_Check.bat",
            "packaging/Install_Update.bat",
            "packaging/Startup_Diagnostic.bat",
        ):
            data = (PROJECT_ROOT / relative_path).read_bytes()
            self.assertTrue(all(byte < 128 for byte in data), relative_path)


if __name__ == "__main__":
    unittest.main()
