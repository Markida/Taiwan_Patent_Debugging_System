"""Release contracts for the internal and company-standard v2.2.07 updates."""

from pathlib import Path
from dataclasses import asdict
import runpy
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import patch

from tools import build_company_update as internal

with patch.dict(sys.modules, {"build_company_update": internal}):
    from tools import build_clean_company_update as standard


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class V2207PackagingTests(unittest.TestCase):
    def test_release_versions_identify_both_editions(self):
        self.assertEqual(internal.read_version(), "2.2.07")
        self.assertEqual(standard.CLEAN_VERSION, "2.2.07c")

    def test_cloud_dictionary_seed_contains_all_198_approved_rows(self):
        source = (
            PROJECT_ROOT
            / "app"
            / "resources"
            / "taiwan_china_spec"
            / "terminology.tsv"
        )
        approved = [
            line
            for line in source.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(len(approved), 198)
        self.assertEqual(approved[-1], "兩個合一個\t二合一")
        with TemporaryDirectory() as directory:
            destination = Path(directory) / internal.CLOUD_DICTIONARY_SEED_FILENAME
            internal.write_cloud_dictionary_seed(destination)
            self.assertEqual(destination.read_text(encoding="utf-8").splitlines(), approved)

    def test_release_notes_cover_the_latest_cross_task_updates(self):
        for notes in (internal.release_notes("2.2.07"), standard.release_notes()):
            with self.subTest(title=notes.splitlines()[0]):
                for expected in (
                    "REF007",
                    "最多參閱四張",
                    "%LOCALAPPDATA%",
                    "於是",
                    "第 198 筆",
                    "兩個合一個 → 二合一",
                    "權利要求1重複的標的",
                    "發明／實用新型內容",
                    "插入原檔內文與有益效果之間",
                    "漩渦眼睛",
                    "圖8A-圖8E",
                    "可逐段雙擊修改",
                    "不啟用尚未完成標註",
                    "移除人工檢查區",
                ):
                    self.assertIn(expected, notes)

    def test_payload_contracts_keep_correspondence_check_in_internal_edition_only(self):
        required = {
            "app/app/background_tasks.py",
            "app/features/patent_review/rule_engine.py",
            "app/ui/workflow_pet.py",
            "app/ui/pixel_cat.py",
            "app/features/workflow_pet/__init__.py",
            "app/features/workflow_pet/guidance.py",
        }
        for edition, files in (
            ("internal", internal.REQUIRED_PACKAGE_FILES),
            ("standard", standard.CLEAN_REQUIRED_FILES),
        ):
            with self.subTest(edition=edition):
                self.assertTrue(required.issubset(files), required.difference(files))
                for relative_path in required:
                    self.assertTrue((PROJECT_ROOT / relative_path.removeprefix("app/")).is_file())
                original = (PROJECT_ROOT / "packaging/Install_Update.bat").read_text(encoding="ascii")
                installer = standard.clean_installer(original) if edition == "standard" else original
                for relative_path in required:
                    self.assertIn(relative_path.removeprefix("app/").replace("/", "\\"), installer)
        checker = "app/features/patent_review/claim_coverage.py"
        self.assertIn(checker, internal.REQUIRED_PACKAGE_FILES)
        self.assertNotIn(checker, standard.CLEAN_REQUIRED_FILES)
        for preview in ("app/features/patent_review/syntax_lab.py", "app/ui/syntax_lab_dialog.py"):
            self.assertIn(preview, internal.REQUIRED_PACKAGE_FILES)
            self.assertNotIn(preview, standard.CLEAN_REQUIRED_FILES)
            original = (PROJECT_ROOT / "packaging/Install_Update.bat").read_text(encoding="ascii")
            self.assertIn(preview.removeprefix("app/").replace("/", "\\"), original)
            self.assertNotIn("syntax_lab", standard.clean_installer(original))

    def test_internal_tetris_theme_dependency_is_required_and_clean_installer_omits_it(self):
        relative = "app/app/features/tetris/themes.py"
        self.assertIn(relative, internal.REQUIRED_PACKAGE_FILES)
        self.assertTrue((PROJECT_ROOT / relative.removeprefix("app/")).is_file())
        installer = (PROJECT_ROOT / "packaging" / "Install_Update.bat").read_text(
            encoding="ascii"
        )
        self.assertIn('"app\\features\\tetris\\themes.py"', installer)
        self.assertNotIn(
            '"app\\features\\tetris\\themes.py"',
            standard.clean_installer(installer),
        )

    def test_arcade_cosmetic_dependencies_are_required_only_in_internal_distribution(self):
        files = (
            "app/app/features/arcade_cosmetics.py",
            "app/ui/arcade_particles.py",
            "app/ui/arcade_skin_art.py",
            "app/ui/arcade_skin_picker.py",
        )
        original = (PROJECT_ROOT / "packaging/Install_Update.bat").read_text(encoding="ascii")
        clean = standard.clean_installer(original)
        for path in files:
            with self.subTest(path=path):
                self.assertIn(path, internal.REQUIRED_PACKAGE_FILES)
                self.assertNotIn(path, standard.CLEAN_REQUIRED_FILES)
                marker = path.removeprefix("app/").replace("/", "\\")
                self.assertIn(marker, original)
                self.assertNotIn(marker, clean)
                self.assertIn(Path(path).stem, standard.STANDARD_FORBIDDEN_TERMS)

    def test_both_builders_gate_optional_figure_heading_models(self):
        self.assertEqual(internal.approved_figure_heading_release_files(), ())
        source = (PROJECT_ROOT / "tools/build_clean_company_update.py").read_text(encoding="utf-8")
        self.assertEqual(internal.figure_heading_package_files(), ())
        self.assertIn("common.figure_heading_package_files(include_figure_heading_trial)", source)
        self.assertIn('--include-figure-heading-trial', source)
        for name in ("figure_heading.py", "figure_orientation_batch.py", "figure_heading_classes.py", "figure_identifiers.py"):
            self.assertIn("app/features/patent_ocr/"+name, standard.CLEAN_REQUIRED_FILES)

    def test_internal_arcade_navigation_dependency_is_required_and_clean_edition_omits_it(self):
        relative = "app/ui/arcade_navigation.py"
        self.assertIn(relative, internal.REQUIRED_PACKAGE_FILES)
        self.assertTrue((PROJECT_ROOT / relative.removeprefix("app/")).is_file())
        installer = (PROJECT_ROOT / "packaging" / "Install_Update.bat").read_text(
            encoding="ascii"
        )
        self.assertIn('"ui\\arcade_navigation.py"', installer)
        self.assertNotIn('"ui\\arcade_navigation.py"', standard.clean_installer(installer))
        cleaned_styles = standard.clean_styles(
            (PROJECT_ROOT / "app" / "styles.py").read_text(encoding="utf-8")
        )
        self.assertNotIn("ArcadeNavigation", cleaned_styles)
        self.assertNotIn("ArcadeStatus", cleaned_styles)

    def test_standard_review_payload_removes_checker_without_changing_shared_source(self):
        relative_paths = (
            "features/patent_review/rule_engine.py",
            "features/patent_review/claim_coverage.py",
            "ui/patent_review_page.py",
            "features/patent_review/syntax_lab.py",
            "ui/syntax_lab_dialog.py",
        )
        originals = {path: (PROJECT_ROOT / path).read_bytes() for path in relative_paths}
        with TemporaryDirectory() as directory:
            app_dir = Path(directory) / "app"
            internal.copy_source_tree(PROJECT_ROOT / "features", app_dir / "features")
            for path in relative_paths:
                target = app_dir / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PROJECT_ROOT / path, target)
            (app_dir / "app").mkdir()
            for name in ("__init__.py", "paths.py"):
                shutil.copy2(PROJECT_ROOT / "app" / name, app_dir / "app" / name)
            standard.configure_standard_review(app_dir)
            self.assertFalse((app_dir / relative_paths[1]).exists())
            self.assertFalse((app_dir / "features/patent_review/syntax_lab.py").exists())
            self.assertFalse((app_dir / "ui/syntax_lab_dialog.py").exists())
            for path in (relative_paths[0], relative_paths[2]):
                source = (app_dir / path).read_text(encoding="utf-8")
                self.assertNotIn("CLM019", source)
                self.assertNotIn("_claim_disclosure_issues", source)
                self.assertNotIn("_coverage_evidence_html", source)
                self.assertNotIn("syntax_lab", source)
                self.assertNotIn("語法對照", source)
                compile(source, path, "exec")
            fixture = next((PROJECT_ROOT / "tests" / "fixtures").glob("TW_*.docx"))
            probe = (
                "import importlib.util, pathlib, sys; "
                "sys.path.insert(0, sys.argv[1]); "
                "from features.patent_review import RULE_CATALOG, parse_docx, review_document; "
                "from features.patent_review import rule_engine; "
                "assert pathlib.Path(rule_engine.__file__).is_relative_to(pathlib.Path(sys.argv[1])); "
                "assert importlib.util.find_spec('features.patent_review.claim_coverage') is None; "
                "assert importlib.util.find_spec('features.patent_review.syntax_lab') is None; "
                "catalog = {r.rule_id for r in RULE_CATALOG}; "
                "assert 'CLM019' not in catalog and {'ABS001', 'CLM018'} <= catalog; "
                "review = review_document(parse_docx(sys.argv[2])); "
                "assert not any(i.rule_id == 'CLM019' for i in review.issues); "
                "assert review.claim_disclosure_coverage == {}; "
                "print('clean package import and review passed')"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-c", probe, str(app_dir), str(fixture)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path, original in originals.items():
            self.assertEqual((PROJECT_ROOT / path).read_bytes(), original)

    def test_standard_review_runs_without_checker_and_keeps_other_findings(self):
        from features.patent_review import rule_engine as shared

        reminder_document = runpy.run_path(
            str(PROJECT_ROOT / "tests" / "test_document_reminders.py")
        )["reminder_document"]
        document = reminder_document(
            abstract=["摘" * 251],
            claims=[
                "【請求項1】一種測試裝置，包含一基座。",
                "【請求項2】一種測試裝置，包含一基座。",
            ],
        )
        expected = shared.review_document(document)
        self.assertTrue({"ABS001", "CLM018", "CLM019"}.issubset(
            {issue.rule_id for issue in expected.issues}
        ))
        source = standard.clean_patent_rule_engine(
            (PROJECT_ROOT / "features" / "patent_review" / "rule_engine.py").read_text(encoding="utf-8")
        )
        module = ModuleType("features.patent_review._standard_rule_engine_test")
        module.__package__ = "features.patent_review"
        with patch.dict(sys.modules, {
            module.__name__: module,
            "features.patent_review.claim_coverage": None,
        }):
            exec(compile(source, "standard_rule_engine.py", "exec"), module.__dict__)
            actual = module.review_document(document)
        self.assertEqual(
            [asdict(issue) for issue in actual.issues],
            [asdict(issue) for issue in expected.issues if issue.rule_id != "CLM019"],
        )
        self.assertEqual(actual.claim_disclosure_coverage, {})
        self.assertEqual(
            [asdict(rule) for rule in actual.rule_catalog],
            [asdict(rule) for rule in expected.rule_catalog if rule.rule_id != "CLM019"],
        )
        self.assertIn("CLM019", {rule.rule_id for rule in shared.RULE_CATALOG})
        self.assertTrue(callable(shared._claim_disclosure_issues))

    def test_standard_page_has_no_preview_even_with_legacy_environment_flag(self):
        with TemporaryDirectory() as directory:
            app_dir = Path(directory) / "app"
            for folder in ("app", "features", "ui"):
                internal.copy_source_tree(PROJECT_ROOT / folder, app_dir / folder)
            standard.configure_standard_review(app_dir)
            probe = """
import importlib.util, os, pathlib, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['PATENT_MDS_SYNTAX_LAB'] = '1'
sys.path.insert(0, sys.argv[1])
from PySide6.QtWidgets import QApplication
from features.patent_review.custom_rules import CustomTextRuleStore, DocumentSimilarityWhitelistStore
from ui.patent_review_page import PatentReviewPage
assert importlib.util.find_spec('features.patent_review.syntax_lab') is None
assert importlib.util.find_spec('ui.syntax_lab_dialog') is None
root = pathlib.Path(sys.argv[1])
app = QApplication([])
page = PatentReviewPage(lambda: None,
    custom_rule_store=CustomTextRuleStore(root / 'test_rules.json'),
    document_whitelist_store=DocumentSimilarityWhitelistStore(root / 'test_whitelist.json'))
assert not hasattr(page, 'syntax_lab_button')
assert not hasattr(page, 'open_syntax_lab')
page._set_review_busy(True)
page._set_review_busy(False)
page.shutdown()
page.deleteLater()
print('clean GUI has no preview')
"""
            result = subprocess.run([sys.executable, "-I", "-c", probe, str(app_dir)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_standard_preview_exclusion_and_scan_fail_closed(self):
        original = (PROJECT_ROOT / "ui/patent_review_page.py").read_text(encoding="utf-8")
        for broken in (original.replace("open_syntax_lab", "open_preview"),
                       original.replace('self.syntax_lab_button.setObjectName("ToolButton")', "pass")):
            with self.assertRaisesRegex(RuntimeError, "markers"):
                standard.clean_patent_review_page(broken)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "syntax_lab.py"
            path.write_text("# unexpected optional module", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "language isolation"):
                standard.validate_standard_package_language(root)
    def test_standard_review_exclusion_fails_closed_when_source_shape_changes(self):
        rule_source = (PROJECT_ROOT / "features" / "patent_review" / "rule_engine.py").read_text(encoding="utf-8")
        for source in (
            rule_source.replace('RuleDefinition("CLM019",', 'RuleDefinition("CLM099",', 1),
            rule_source.replace("def _claim_disclosure_issues(", "def _unexpected_review_rule(", 1),
            rule_source + "\n\ndef _claim_disclosure_issues():\n    pass\n",
        ):
            with self.subTest(source_length=len(source)):
                with self.assertRaisesRegex(RuntimeError, "missing or ambiguous"):
                    standard.clean_patent_rule_engine(source)
        page_source = (PROJECT_ROOT / "ui" / "patent_review_page.py").read_text(encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "missing or ambiguous"):
            standard.clean_patent_review_page(page_source.replace('== "CLM019"', '== "CLM099"', 1))

    def test_both_payload_contracts_require_all_model_files(self):
        required = {
            *(f"app/models/{name}" for name in internal.MODEL_FILES),
            *(f"app/easyocr_models/{name}" for name in internal.EASYOCR_MODEL_FILES),
        }
        for edition, files in (
            ("internal", internal.REQUIRED_PACKAGE_FILES),
            ("standard", standard.CLEAN_REQUIRED_FILES),
        ):
            with self.subTest(edition=edition):
                self.assertTrue(required.issubset(files), required.difference(files))

    def test_both_installers_require_new_runtime_dependencies(self):
        original = (PROJECT_ROOT / "packaging" / "Install_Update.bat").read_text(
            encoding="ascii"
        )
        for edition, installer in (
            ("internal", original),
            ("standard", standard.clean_installer(original)),
        ):
            with self.subTest(edition=edition):
                checker = '"features\\patent_review\\claim_coverage.py"'
                if edition == "internal":
                    self.assertIn(checker, installer)
                else:
                    self.assertNotIn("claim_coverage", installer)
                self.assertIn('"app\\background_tasks.py"', installer)
                for module in (
                    "compact_ocr_reader.py",
                    "onnx_detector.py",
                    "figure_heading.py",
                    "figure_orientation_batch.py",
                    "figure_heading_classes.py",
                    "figure_identifiers.py",
                    "image_io.py",
                    "image_tools.py",
                ):
                    self.assertIn(f'"features\\patent_ocr\\{module}"', installer)
                for model in internal.MODEL_FILES:
                    self.assertIn(f'"models\\{model}"', installer)
                for model in internal.EASYOCR_MODEL_FILES:
                    self.assertIn(f'"easyocr_models\\{model}"', installer)
                self.assertIn("--offline-self-test", installer)
                self.assertIn("--gui-smoke-test", installer)
                self.assertTrue(installer.isascii())

    def test_launcher_drains_both_diagnostic_streams_before_waiting(self):
        source = (PROJECT_ROOT / "packaging" / "Saint-IslandPatentOCR.Launcher.cs").read_text(
            encoding="utf-8"
        )
        wait_position = source.index("child.WaitForExit();")
        self.assertLess(source.index("child.BeginOutputReadLine();"), wait_position)
        self.assertLess(source.index("child.BeginErrorReadLine();"), wait_position)
        self.assertIn("child.OutputDataReceived +=", source)
        self.assertIn("child.ErrorDataReceived +=", source)
        self.assertNotIn(".ReadToEnd()", source)

    def test_both_installers_report_the_correct_release(self):
        original = (PROJECT_ROOT / "packaging" / "Install_Update.bat").read_text(
            encoding="ascii"
        )
        for version, report_version, installer in (
            ("2.2.07", "2207", original),
            ("2.2.07c", "2207c", standard.clean_installer(original)),
        ):
            with self.subTest(version=version):
                self.assertIn(f"v{version} launcher", installer)
                self.assertIn(f"[PASS] v{version} update installed", installer)
                self.assertIn(f"SaintIsland_v{report_version}_update_check.json", installer)
                self.assertNotIn("2.1.10", installer)
                self.assertNotIn("v2110", installer)

    def test_release_notes_describe_only_the_rules_enabled_in_each_edition(self):
        for version, notes in (
            ("2.2.07", internal.release_notes("2.2.07")),
            ("2.2.07c", standard.release_notes()),
        ):
            with self.subTest(version=version):
                self.assertIn(f"v{version}", notes.splitlines()[0])
                for rule_id in ("ABS001", "CLM018"):
                    self.assertIn(rule_id, notes)
                if version.endswith("c"):
                    self.assertNotIn("CLM019", notes)
                    self.assertNotIn("請求項1與發明／新型內容對應", notes)
                else:
                    self.assertIn("CLM019", notes)
                    self.assertIn("語法對照（內測）", notes)
                self.assertIn("250", notes)
                self.assertIn("摘要", notes)
                self.assertIn("重複請求項", notes)
                self.assertIn("custom_text_rules.json", notes)
                self.assertIn("刪除所選項目", notes)
                self.assertIn("Ctrl+Y", notes)
                for obsolete_button in ("排除所選項目", "刪除剩餘項目", "返回上一步"):
                    self.assertNotIn(obsolete_button, notes)

    def test_standard_notes_and_installer_do_not_name_optional_modules(self):
        installer = standard.clean_installer(
            (PROJECT_ROOT / "packaging" / "Install_Update.bat").read_text(encoding="ascii")
        )
        for name, text in (("notes", standard.release_notes()), ("installer", installer)):
            with self.subTest(file=name):
                for term in standard.STANDARD_FORBIDDEN_TERMS:
                    self.assertNotIn(term.casefold(), text.casefold())

    def test_standard_installer_checks_existing_data_before_copy_or_cleanup(self):
        installer = standard.clean_installer(
            (PROJECT_ROOT / "packaging" / "Install_Update.bat").read_text(encoding="ascii")
        )
        before_copy, after_copy = installer.split("robocopy ", 1)
        self.assertIn('dir /b /s /a-d "%TARGET%\\app\\app\\features"', before_copy)
        self.assertIn('dir /b /ad "%TARGET%\\app\\features"', before_copy)
        self.assertIn('if not exist "%SOURCE%\\features\\%%D\\"', before_copy)
        self.assertIn('dir /b /s /a-d "%TARGET%\\app\\features\\%%D"', before_copy)
        self.assertEqual(before_copy.count("exit /b 7"), 2)
        for suffix in (".py", ".pyc", ".pyo"):
            self.assertEqual(before_copy.count(f'if /I not "%%~xF"=="{suffix}"'), 2)
        self.assertIn("No files have been changed by this installer.", before_copy)
        self.assertNotIn("rmdir /s /q", before_copy)
        self.assertNotIn("copy /Y", before_copy)
        self.assertIn('rmdir /s /q "%TARGET%\\app\\app\\features"', after_copy)
        self.assertIn('if not exist "%SOURCE%\\features\\patent_review\\%%~nF.py" del /q "%%~fF"', after_copy)
        self.assertNotIn('rmdir /s /q "%TARGET%\\app\\features\\patent_review\\__pycache__"', after_copy)

    def test_standard_installer_fails_closed_without_unique_copy_marker(self):
        source = (PROJECT_ROOT / "packaging" / "Install_Update.bat").read_text(
            encoding="ascii"
        )
        marker = 'robocopy "%SOURCE%" "%TARGET%\\app" /E'
        for invalid_source in (source.replace(marker, ""), source + "\n" + marker):
            with self.subTest(marker_count=invalid_source.count(marker)):
                with self.assertRaisesRegex(RuntimeError, "missing or ambiguous"):
                    standard.clean_installer(invalid_source)

    def test_source_copy_excludes_company_rules_at_every_depth(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "payload"
            nested = source / "features" / "patent_review"
            nested.mkdir(parents=True)
            original_rules = b'{"rules": [{"text": "company-maintained"}]}\n'
            for folder in (source, nested):
                (folder / "custom_text_rules.json").write_bytes(original_rules)
                (folder / "custom_text_rules.json.tmp").write_bytes(original_rules)
            (nested / "custom_rules.py").write_text("# rule loader\n", encoding="utf-8")

            internal.copy_source_tree(source, destination)

            self.assertTrue((destination / "features" / "patent_review" / "custom_rules.py").is_file())
            self.assertFalse(list(destination.rglob("custom_text_rules.json*")))
            for folder in (source, nested):
                self.assertEqual((folder / "custom_text_rules.json").read_bytes(), original_rules)
                self.assertEqual((folder / "custom_text_rules.json.tmp").read_bytes(), original_rules)

    def test_installers_do_not_mirror_or_purge_company_data(self):
        original = (PROJECT_ROOT / "packaging" / "Install_Update.bat").read_text(
            encoding="ascii"
        )
        for edition, installer in (
            ("internal", original),
            ("standard", standard.clean_installer(original)),
        ):
            with self.subTest(edition=edition):
                copy_lines = [
                    line.upper() for line in installer.splitlines()
                    if line.strip().lower().startswith("robocopy ")
                ]
                self.assertTrue(copy_lines)
                for line in copy_lines:
                    self.assertNotIn("/MIR", line)
                    self.assertNotIn("/PURGE", line)
                    self.assertIn("/XF CUSTOM_TEXT_RULES.JSON CUSTOM_TEXT_RULES.JSON.TMP", line)
                destructive_rule_lines = [
                    line for line in installer.splitlines()
                    if line.strip().lower().startswith(("del ", "erase ", "rmdir ", "rd "))
                    and "custom_text_rules" in line.lower()
                ]
                self.assertEqual(destructive_rule_lines, [])


if __name__ == "__main__":
    unittest.main()
