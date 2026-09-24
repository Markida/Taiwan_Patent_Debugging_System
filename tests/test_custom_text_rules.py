import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from app.paths import get_app_install_dir
from features.patent_review.custom_rules import (
    CUSTOM_RULE_BLACKLIST,
    CUSTOM_RULE_WHITELIST,
    CustomRuleStorageError,
    CustomRuleValidationError,
    CustomTextRule,
    CustomTextRuleStore,
    DocumentSimilarityWhitelistStore,
    SHARED_CUSTOM_TEXT_RULES_DIRECTORY,
)
from features.patent_review.models import (
    PatentDocument,
    PatentParagraph,
    TextRunSpan,
)
from features.patent_review.rule_engine import review_document
from features.patent_review.text_normalizer import normalize_patent_text
from ui.patent_review_page import PatentReviewPage
from ui.custom_text_rule_dialog import CustomTextRuleDialog


def build_document(text="這是一個的的錯誤。"):
    paragraph = PatentParagraph(
        index=0,
        text=text,
        normalized_text=normalize_patent_text(text),
        source_path="body/p[0]",
        run_spans=[TextRunSpan(0, 0, len(text), text)],
    )
    return PatentDocument(
        source_path="sample.docx",
        file_name="sample.docx",
        file_size_bytes=0,
        sha256="e" * 64,
        patent_type="invention",
        patent_title="測試文件",
        paragraphs=[paragraph],
        sections={},
    )


class CustomTextRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_rule_is_persisted_and_loaded_by_a_new_store(self):
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "custom_text_rules.json"
            store = CustomTextRuleStore(path)

            rule, created = store.add("的的")
            duplicate, duplicate_created = store.add("的的")
            reopened_rules = CustomTextRuleStore(path).load()

            self.assertTrue(created)
            self.assertFalse(duplicate_created)
            self.assertEqual(rule, duplicate)
            self.assertEqual([item.text for item in reopened_rules], ["的的"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["rules"][0]["text"], "的的")

    def test_easter_egg_default_shared_rule_store_uses_legacy_documents_path(self):
        store = CustomTextRuleStore()

        self.assertEqual(store.path.parent, SHARED_CUSTOM_TEXT_RULES_DIRECTORY)
        self.assertEqual(store.path.parent, Path(r"C:\Documents"))
        self.assertEqual(store.path.name, "custom_text_rules.json")

    def test_document_whitelist_is_persistent_and_isolated_by_document(self):
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "document_similarity_whitelists.json"
            store = DocumentSimilarityWhitelistStore(path)
            first = build_document()
            second = build_document()
            second.source_path = "another.docx"
            second.file_name = "another.docx"
            second.sha256 = "f" * 64

            term, created = store.add(first, "第一位置")
            duplicate, duplicate_created = store.add(first, "第一位置")

            self.assertEqual(term, "第一位置")
            self.assertEqual(duplicate, term)
            self.assertTrue(created)
            self.assertFalse(duplicate_created)
            self.assertEqual(store.load(first), ["第一位置"])
            self.assertEqual(store.load(second), [])

            # Editing and reparsing the same source path keeps its document-
            # specific list even though the file hash changes.
            first.sha256 = "1" * 64
            self.assertEqual(store.load(first), ["第一位置"])
            self.assertEqual(store.remove(first, ["第一位置"]), 1)
            self.assertEqual(store.load(first), [])

    def test_default_document_whitelist_is_local_to_each_windows_user(self):
        document = build_document()
        with TemporaryDirectory() as first_profile, TemporaryDirectory() as second_profile:
            with patch.dict(os.environ, {"LOCALAPPDATA": first_profile}):
                first_store = DocumentSimilarityWhitelistStore()
                first_store.add(document, "第一位置")
                self.assertEqual(
                    first_store.path,
                    Path(first_profile)
                    / "Saint-Island_Patent_MDS"
                    / "document_similarity_whitelists.json",
                )
                self.assertEqual(
                    DocumentSimilarityWhitelistStore().load(document),
                    ["第一位置"],
                )

            with patch.dict(os.environ, {"LOCALAPPDATA": second_profile}):
                second_store = DocumentSimilarityWhitelistStore()
                self.assertNotEqual(second_store.path, first_store.path)
                self.assertEqual(second_store.load(document), [])

    def test_version_one_file_is_loaded_as_blacklist(self):
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "custom_text_rules.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "rules": [{"rule_id": "legacy", "text": "個個"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            rules = CustomTextRuleStore(path).load()

            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0].rule_type, CUSTOM_RULE_BLACKLIST)

    def test_blacklist_and_whitelist_are_persisted_separately(self):
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "custom_text_rules.json"
            store = CustomTextRuleStore(path)

            blacklist, black_created = store.add("第一尺輪")
            whitelist, white_created = store.add(
                "第一尺輪",
                CUSTOM_RULE_WHITELIST,
            )

            self.assertTrue(black_created)
            self.assertTrue(white_created)
            self.assertNotEqual(blacklist.rule_id, whitelist.rule_id)
            self.assertEqual(
                {rule.rule_type for rule in store.load()},
                {CUSTOM_RULE_BLACKLIST, CUSTOM_RULE_WHITELIST},
            )

    def test_portable_install_uses_launcher_root_for_rule_storage(self):
        with TemporaryDirectory() as temporary_directory:
            install_root = Path(temporary_directory)
            app_directory = install_root / "app"
            app_directory.mkdir()
            (install_root / "runtime").mkdir()

            with patch("app.paths.get_app_base_dir", return_value=app_directory):
                self.assertEqual(get_app_install_dir(), install_root)

    def test_empty_multiline_and_oversized_rules_are_rejected(self):
        for value in ("", "   ", "第一行\n第二行", "字" * 81):
            with self.subTest(value=value):
                with self.assertRaises(CustomRuleValidationError):
                    CustomTextRule.from_text(value)

    def test_malformed_file_does_not_crash_the_review_page(self):
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "custom_text_rules.json"
            path.write_text("{broken", encoding="utf-8")
            store = CustomTextRuleStore(path)
            with self.assertRaises(CustomRuleStorageError):
                store.load()

            page = PatentReviewPage(lambda: None, custom_rule_store=store)
            deadline = time.monotonic() + 5
            while not page.custom_rule_load_error and time.monotonic() < deadline:
                QTest.qWait(5)
            self.assertEqual(page.custom_rules, [])
            self.assertTrue(page.custom_rule_load_error)
            page.deleteLater()

    def test_literal_rule_produces_localized_error_and_catalog_entry(self):
        rule = CustomTextRule.from_text("的的")
        review = review_document(build_document(), custom_rules=[rule])
        issue = next(item for item in review.issues if item.rule_id == rule.rule_id)

        self.assertEqual(issue.severity, "error")
        self.assertEqual(issue.matched_text, "的的")
        self.assertEqual(issue.paragraph_index, 0)
        self.assertIsNotNone(issue.char_start)
        self.assertEqual(issue.char_end - issue.char_start, 2)
        self.assertEqual(issue.details["custom_text"], "的的")
        definition = next(
            item for item in review.rule_catalog if item.rule_id == rule.rule_id
        )
        self.assertIn("的的", definition.title)

    def test_whitelist_rule_does_not_create_custom_text_error(self):
        rule = CustomTextRule.from_text("的的", CUSTOM_RULE_WHITELIST)
        review = review_document(build_document(), custom_rules=[rule])

        self.assertFalse(any(item.rule_id == rule.rule_id for item in review.issues))
        self.assertFalse(
            any(item.rule_id == rule.rule_id for item in review.rule_catalog)
        )

    def test_page_loads_saved_rules_at_startup(self):
        with TemporaryDirectory() as temporary_directory:
            store = CustomTextRuleStore(
                Path(temporary_directory) / "custom_text_rules.json"
            )
            store.add("個個")

            page = PatentReviewPage(lambda: None, custom_rule_store=store)
            deadline = time.monotonic() + 5
            while not page.custom_rules and time.monotonic() < deadline:
                QTest.qWait(5)
            self.assertEqual([rule.text for rule in page.custom_rules], ["個個"])
            self.assertIn("（1）", page.custom_rules_button.text())
            page.deleteLater()

    def test_dialog_adds_and_removes_rules_without_restart(self):
        with TemporaryDirectory() as temporary_directory:
            store = CustomTextRuleStore(
                Path(temporary_directory) / "custom_text_rules.json"
            )
            dialog = CustomTextRuleDialog(store)
            self.wait_for_dialog(dialog)
            dialog.rule_input.setText("的的")

            self.assertTrue(dialog.add_current_rule())
            self.wait_for_dialog(dialog)
            self.assertTrue(dialog.rules_changed)
            self.assertEqual(dialog.rule_table.rowCount(), 1)

            dialog.rule_table.selectRow(0)
            self.assertEqual(dialog.delete_selected_rules(), 1)
            self.wait_for_dialog(dialog)
            self.assertEqual(store.load(), [])
            dialog.deleteLater()

    def test_dialog_manages_whitelist_in_a_separate_tab(self):
        with TemporaryDirectory() as temporary_directory:
            store = CustomTextRuleStore(
                Path(temporary_directory) / "custom_text_rules.json"
            )
            dialog = CustomTextRuleDialog(store)
            self.wait_for_dialog(dialog)
            white_input = dialog._inputs[CUSTOM_RULE_WHITELIST]
            white_table = dialog._tables[CUSTOM_RULE_WHITELIST]
            white_input.setText("第一尺輪")

            self.assertTrue(dialog.add_current_rule(CUSTOM_RULE_WHITELIST))
            self.wait_for_dialog(dialog)
            self.assertEqual(dialog.rule_table.rowCount(), 0)
            self.assertEqual(white_table.rowCount(), 1)
            self.assertEqual(store.load()[0].rule_type, CUSTOM_RULE_WHITELIST)

            white_table.selectRow(0)
            self.assertEqual(
                dialog.delete_selected_rules(CUSTOM_RULE_WHITELIST),
                1,
            )
            self.wait_for_dialog(dialog)
            dialog.deleteLater()

    def wait_for_dialog(self, dialog):
        deadline = time.monotonic() + 5
        while dialog._tasks.busy and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertFalse(dialog._tasks.busy)


if __name__ == "__main__":
    unittest.main()
