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
        self.assertIn('root, "data", "ultralytics"', source)
        self.assertNotIn("SpecialFolder.LocalApplicationData", source)
        self.assertIn("RedirectStandardError = automatedTest", source)
        self.assertIn('WriteDiagnostic(diagnosticLog, "Child stderr="', source)
        self.assertIn('[assembly: AssemblyVersion("2.2.7.0")]', source)
        self.assertIn('[assembly: AssemblyFileVersion("2.2.7.0")]', source)
        self.assertIn(
            '[assembly: AssemblyInformationalVersion("2.2.07")]',
            source,
        )

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
        self.assertIn("features\\patent_ocr\\reference_reconciliation.py", installer)
        self.assertIn("features\\patent_review\\figure_ocr_checker.py", installer)
        self.assertIn("app\\workflow_context.py", installer)
        self.assertIn("app\\startup_splash.py", installer)
        self.assertIn("ui\\custom_text_rule_dialog.py", installer)
        self.assertIn("ui\\embodiment_figure_compare_page.py", installer)
        self.assertIn("ui\\taiwan_china_spec_page.py", installer)
        self.assertIn("features\\taiwan_china_spec\\converter.py", installer)
        self.assertIn("features\\taiwan_china_spec\\terminology_store.py", installer)
        self.assertIn("features\\taiwan_china_spec\\article_review.py", installer)
        self.assertIn("ui\\spec_article_review.py", installer)
        self.assertIn(
            "app\\resources\\taiwan_china_spec\\BeijingTaijiTemplate.docx",
            installer,
        )
        self.assertIn("app\\features\\chat_room\\store.py", installer)
        self.assertIn("app\\features\\bulls_and_cows\\engine.py", installer)
        self.assertIn("app\\features\\pong\\network.py", installer)
        self.assertIn("ui\\chat_room_page.py", installer)
        self.assertIn("ui\\arcade_navigation.py", installer)
        self.assertIn("ui\\snake_game_page.py", installer)
        self.assertIn("ui\\pong_game_page.py", installer)
        self.assertIn("app\\features\\snake\\score_store.py", installer)
        self.assertIn("app\\features\\tetris\\network.py", installer)
        self.assertIn("app\\features\\tetris\\themes.py", installer)
        self.assertIn("ui\\tetris_game_page.py", installer)
        self.assertIn("app\\features\\tank_battle\\network.py", installer)
        self.assertIn("ui\\tank_battle_page.py", installer)
        self.assertIn("ui\\bulls_and_cows_page.py", installer)
        self.assertIn("easyocr_models\\english_g2.pth", installer)
        self.assertNotIn('del /q "%TARGET%\\app\\ui\\chat_room_page.py"', installer)
        self.assertNotIn('del /q "%TARGET%\\app\\ui\\snake_game_page.py"', installer)
        self.assertIn("app\\resources\\app_icon.png", installer)
        self.assertIn("app\\resources\\app_icon.ico", installer)
        self.assertNotIn("del /q custom_text_rules.json", installer.lower())
        self.assertFalse(installer_path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_update_builder_requires_custom_rule_components(self):
        source = (PROJECT_ROOT / "tools" / "build_company_update.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('UPDATE_DESCRIPTION = "內部測試版本"', source)
        self.assertIn('"app/features/taiwan_china_spec/article_review.py"', source)
        self.assertIn('"app/ui/spec_article_review.py"', source)
        self.assertNotIn('UPDATE_DESCRIPTION = "有彩蛋完整功能版"', source)
        self.assertIn('"app/features/patent_review/custom_rules.py"', source)
        self.assertIn('"app/features/patent_ocr/reference_reconciliation.py"', source)
        self.assertIn('"app/features/patent_review/figure_ocr_checker.py"', source)
        self.assertIn('"app/ui/custom_text_rule_dialog.py"', source)
        self.assertIn('"app/ui/embodiment_figure_compare_page.py"', source)
        self.assertIn('"app/ui/taiwan_china_spec_page.py"', source)
        self.assertIn(
            '"app/features/taiwan_china_spec/converter.py"',
            source,
        )
        self.assertIn(
            '"app/features/taiwan_china_spec/terminology_store.py"',
            source,
        )
        self.assertIn(
            '"app/app/resources/taiwan_china_spec/BeijingTaijiTemplate.docx"',
            source,
        )
        self.assertIn('"app/app/features/chat_room/store.py"', source)
        self.assertIn('"app/app/features/bulls_and_cows/engine.py"', source)
        self.assertIn('"app/app/features/pong/network.py"', source)
        self.assertIn('"app/ui/chat_room_page.py"', source)
        self.assertIn('"app/ui/arcade_navigation.py"', source)
        self.assertIn('"app/ui/snake_game_page.py"', source)
        self.assertIn('"app/ui/pong_game_page.py"', source)
        self.assertIn('"app/app/features/snake/score_store.py"', source)
        self.assertIn('"app/app/features/tetris/network.py"', source)
        self.assertIn('"app/app/features/tetris/themes.py"', source)
        self.assertIn('"app/ui/tetris_game_page.py"', source)
        self.assertIn('"app/app/features/tank_battle/network.py"', source)
        self.assertIn('"app/ui/tank_battle_page.py"', source)
        self.assertIn('"app/ui/bulls_and_cows_page.py"', source)
        self.assertIn('"app/easyocr_models/english_g2.pth"', source)
        self.assertIn('EASYOCR_MODEL_FILES = ("english_g2.pth",)', source)
        self.assertIn('"clean_distribution": False', source)
        self.assertIn('"easter_eggs_included": True', source)
        self.assertIn('"app/app/resources/app_icon.png"', source)
        self.assertIn('"app/app/resources/app_icon.ico"', source)
        self.assertIn('"app/app/startup_splash.py"', source)
        self.assertIn('/win32icon:', source)
        self.assertIn("custom_text_rules.json", source)

    def test_clean_builder_is_the_only_package_that_switches_shared_rules_to_i_drive(self):
        shared_rules = (
            PROJECT_ROOT / "features" / "patent_review" / "custom_rules.py"
        ).read_text(encoding="utf-8")
        clean_builder = (
            PROJECT_ROOT / "tools" / "build_clean_company_update.py"
        ).read_text(encoding="utf-8")

        self.assertIn(r'C:\Documents', shared_rules)
        self.assertNotIn(r'I:\Saint-Island_Patent_MDS', shared_rules)
        self.assertIn(r"SOURCE_SHARED_RULES_PATH = r'C:\Documents'", clean_builder)
        self.assertIn(
            r"STANDARD_SHARED_RULES_PATH = r'I:\Saint-Island_Patent_MDS'",
            clean_builder,
        )
        self.assertIn('CLEAN_VERSION = "2.2.07c"', clean_builder)
        self.assertIn('AssemblyInformationalVersion("2.2.07c")', clean_builder)
        self.assertIn('and not getattr(page, "uses_isolated_file_drop", False)', clean_builder)
        for required in (
            '"app/app/resources/taiwan_china_spec/BeijingTaijiTemplate.docx"',
            '"app/app/resources/taiwan_china_spec/ShanghaiYiPin.docx"',
            '"app/app/resources/taiwan_china_spec/terminology.tsv"',
            '"app/features/taiwan_china_spec/__init__.py"',
            '"app/features/taiwan_china_spec/converter.py"',
            '"app/features/taiwan_china_spec/terminology_store.py"',
            '"app/features/taiwan_china_spec/article_review.py"',
            '"app/ui/spec_article_review.py"',
            '"app/ui/taiwan_china_spec_page.py"',
        ):
            self.assertIn(required, clean_builder)
        self.assertIn('app_dir / "app" / "features" / "tetris"', clean_builder)
        self.assertIn('app_dir / "ui" / "tetris_game_page.py"', clean_builder)
        self.assertIn('app_dir / "ui" / "bulls_and_cows_page.py"', clean_builder)
        self.assertIn("validate_standard_package_language(package_dir)", clean_builder)

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
            self.assertIn(
                "--include-package=features.taiwan_china_spec",
                source,
            )
            self.assertIn(
                "--include-module=ui.taiwan_china_spec_page",
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
