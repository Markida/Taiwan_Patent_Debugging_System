import unittest
from pathlib import Path

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CompanyPackagingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_launcher_avoids_case_duplicate_environment_dictionary(self):
        source = (
            PROJECT_ROOT / "packaging" / "Saint-IslandPatentOCR.Launcher.cs"
        ).read_text(encoding="utf-8")

        self.assertNotIn("startInfo.EnvironmentVariables", source)
        self.assertIn('Environment.SetEnvironmentVariable("PATH"', source)
        self.assertIn('[assembly: AssemblyFileVersion("2.0.4.0")]', source)

    def test_application_icon_assets_and_window_icon_are_available(self):
        png = PROJECT_ROOT / "app" / "resources" / "app_icon.png"
        ico = PROJECT_ROOT / "app" / "resources" / "app_icon.ico"

        self.assertTrue(png.is_file())
        self.assertTrue(ico.is_file())
        self.assertEqual(png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(ico.read_bytes()[:4], b"\x00\x00\x01\x00")
        window = MainWindow()
        try:
            self.assertFalse(window.windowIcon().isNull())
        finally:
            window.deleteLater()

    def test_update_installer_uses_current_executable_with_legacy_fallback(self):
        installer_path = PROJECT_ROOT / "packaging" / "Install_Update.bat"
        installer = installer_path.read_text(encoding="ascii")

        self.assertIn("Saint-Island_Patent_MDS.exe", installer)
        self.assertIn("Saint-IslandPatentOCR.exe", installer)
        self.assertIn("--offline-self-test", installer)
        self.assertIn("--gui-smoke-test", installer)
        self.assertIn("features\\patent_review\\custom_rules.py", installer)
        self.assertIn("features\\patent_review\\figure_ocr_checker.py", installer)
        self.assertIn("app\\workflow_context.py", installer)
        self.assertIn("ui\\custom_text_rule_dialog.py", installer)
        self.assertIn("ui\\embodiment_figure_compare_page.py", installer)
        self.assertIn("ui\\snake_game_page.py", installer)
        self.assertIn("app\\features\\snake\\score_store.py", installer)
        self.assertIn("app\\resources\\app_icon.png", installer)
        self.assertIn("app\\resources\\app_icon.ico", installer)
        self.assertNotIn("del /q custom_text_rules.json", installer.lower())
        self.assertFalse(installer_path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_update_builder_requires_custom_rule_components(self):
        source = (PROJECT_ROOT / "tools" / "build_company_update.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"app/features/patent_review/custom_rules.py"', source)
        self.assertIn('"app/features/patent_review/figure_ocr_checker.py"', source)
        self.assertIn('"app/ui/custom_text_rule_dialog.py"', source)
        self.assertIn('"app/ui/embodiment_figure_compare_page.py"', source)
        self.assertIn('"app/ui/snake_game_page.py"', source)
        self.assertIn('"app/app/features/snake/score_store.py"', source)
        self.assertIn('"app/app/resources/app_icon.png"', source)
        self.assertIn('"app/app/resources/app_icon.ico"', source)
        self.assertIn('/win32icon:', source)
        self.assertIn("custom_text_rules.json", source)

    def test_nuitka_builds_embed_and_copy_the_application_icon(self):
        for relative_path in ("build_cpu.bat", "build_cpu.ps1"):
            source = (PROJECT_ROOT / relative_path).read_text(
                encoding="utf-8", errors="replace"
            )
            self.assertIn(
                "--windows-icon-from-ico=app\\resources\\app_icon.ico",
                source,
            )
            self.assertIn(
                "--include-data-dir=app\\resources=app\\resources",
                source,
            )

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
