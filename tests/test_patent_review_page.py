import os
from pathlib import Path
import re
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from app.workflow_context import PatentWorkflowContext
from app.styles import APP_STYLE
from features.patent_review.models import (
    PatentDocument,
    PatentIssue,
    PatentParagraph,
    TextRunSpan,
)
from features.patent_review.rule_engine import review_document
from features.patent_review.custom_rules import (
    CustomTextRuleStore,
    DocumentSimilarityWhitelistStore,
)
from features.patent_review.section_parser import assign_sections
from features.patent_review.symbol_transfer import extract_document_symbols
from features.patent_review.text_normalizer import normalize_patent_text
from features.registry import FEATURES
from ui.patent_review_page import PatentReviewPage


def build_document():
    lines = [
        "【中文新型名稱】測試Ａ裝置",
        "【符號說明】",
        "1:底座",
        "2：上蓋",
        "【代表圖之符號簡單說明】",
        "1:底座",
    ]
    paragraphs = [
        PatentParagraph(
            index=index,
            text=text,
            normalized_text=normalize_patent_text(text),
            source_path=f"body/p[{index}]",
            run_spans=[TextRunSpan(0, 0, len(text), text)],
        )
        for index, text in enumerate(lines)
    ]
    sections, patent_type, title = assign_sections(paragraphs)
    return PatentDocument(
        source_path="sample.docx",
        file_name="sample.docx",
        file_size_bytes=0,
        sha256="d" * 64,
        patent_type=patent_type,
        patent_title=title,
        paragraphs=paragraphs,
        sections=sections,
    )


class PatentReviewPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        temporary_path = Path(self.temporary_directory.name)
        self.opened_features = []
        self.context = PatentWorkflowContext()
        self.page = PatentReviewPage(
            lambda: None,
            custom_rule_store=CustomTextRuleStore(
                temporary_path / "custom_text_rules.json"
            ),
            document_whitelist_store=DocumentSimilarityWhitelistStore(
                temporary_path / "document_similarity_whitelists.json"
            ),
        )
        self.page.set_workflow_context(self.context)
        self.page.set_open_feature_callback(self.opened_features.append)
        self.page.set_feature_navigation(FEATURES, self.opened_features.append)

    def tearDown(self):
        self.page.shutdown()
        self.page.deleteLater()
        self.temporary_directory.cleanup()

    def wait_for_review(self):
        deadline = time.monotonic() + 5
        while self.page._review_tasks.busy and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertFalse(self.page._review_tasks.busy)

    def test_home_registry_contains_document_review_page(self):
        self.assertIn("patent_review", [feature["id"] for feature in FEATURES])

    def test_shared_top_navigation_is_generated_from_feature_registry(self):
        labels = [
            button.text()
            for button in self.page.feature_navigation.findChildren(QPushButton)
        ]
        self.assertEqual(
            labels,
            [
                "回首頁",
                "文件偵錯",
                "圖式標號",
                "段落圖式比對",
                "台陸轉換",
            ],
        )
        self.assertNotIn("功能測試頁", labels)
        active = self.page.feature_navigation.findChild(
            QPushButton, "ActiveFeatureNavigationButton"
        )
        self.assertIsNotNone(active)
        self.assertEqual(active.text(), "文件偵錯")

    def test_compact_top_rows_and_consistent_symbol_tabs(self):
        margins = self.page.layout().contentsMargins()
        splitter_index = self.page.layout().indexOf(self.page.main_splitter)

        self.assertEqual(margins.top(), 4)
        self.assertEqual(self.page.layout().stretch(splitter_index), 1)
        self.assertLessEqual(self.page.status_label.maximumHeight(), 38)
        self.assertTrue(self.page.status_label.isHidden())
        self.assertTrue(self.page.symbol_tabs.tabBar().expanding())
        self.assertFalse(self.page.symbol_tabs.tabBar().usesScrollButtons())
        self.assertEqual(
            self.page.symbol_tabs.tabBar().elideMode(),
            Qt.TextElideMode.ElideNone,
        )
        self.assertEqual(self.page.symbol_tabs.objectName(), "SymbolSourceTabs")
        self.assertEqual(
            self.page.symbol_tabs.tabBar().objectName(),
            "SymbolSourceTabBar",
        )
        tab_style = self.page.symbol_tabs.styleSheet()
        self.assertIn("background: #f8fafc", tab_style)
        self.assertIn("color: #18324b", tab_style)
        self.assertIn("background: #0f4c81", tab_style)
        self.assertIn("color: #ffffff", self.page.symbol_tabs.styleSheet())
        self.assertIn("font-family: 'Microsoft JhengHei'", tab_style)
        self.assertIn("font-size: 12.5pt", tab_style)

    def test_symbol_tabs_remain_visible_at_minimum_supported_resolution(self):
        self.page.resize(960, 640)
        self.page.show()
        self.app.processEvents()

        tab_bar = self.page.symbol_tabs.tabBar()
        self.assertEqual(tab_bar.count(), 2)
        self.assertGreater(tab_bar.tabRect(0).width(), 0)
        self.assertGreater(tab_bar.tabRect(1).width(), 0)
        self.assertLessEqual(
            tab_bar.tabRect(1).right(),
            tab_bar.contentsRect().right(),
        )
        self.assertGreater(self.page.symbol_tabs.height(), tab_bar.height())

    def test_symbol_hint_is_removed_and_document_whitelist_gets_more_height(self):
        label_text = "".join(
            label.text() for label in self.page.findChildren(QLabel)
        )
        self.assertNotIn("兩套清單會分開傳送", label_text)

        self.page.resize(1400, 900)
        self.page.show()
        self.app.processEvents()
        upper_height, lower_height = self.page.right_splitter.sizes()

        self.assertGreater(lower_height, 0)
        self.assertGreaterEqual(
            lower_height / (upper_height + lower_height),
            0.40,
        )

    def test_document_whitelist_panel_uses_white_background_and_black_text(self):
        self.assertEqual(
            self.page.document_whitelist_title.text(),
            "實施方式段落出現之無標號元件",
        )
        self.assertEqual(
            self.page.document_whitelist_hint.text(),
            "在此處手動新增之元件不參與模糊比對以及標號偵測",
        )
        self.assertEqual(
            self.page.document_whitelist_table.horizontalHeaderItem(0).text(),
            "不參與模糊比對及標號偵測的元件或詞語",
        )
        self.assertEqual(
            self.page.reload_button.objectName(),
            "PrimaryButton",
        )
        self.assertEqual(
            self.page.add_document_whitelist_button.objectName(),
            "PrimaryButton",
        )
        self.assertEqual(
            self.page.delete_document_whitelist_button.objectName(),
            "PrimaryButton",
        )
        self.assertEqual(
            self.page.document_whitelist_table.objectName(),
            "DocumentWhitelistTable",
        )
        self.assertIn("#DocumentWhitelistPanel", APP_STYLE)
        self.assertIn("background: #ffffff", APP_STYLE)
        self.assertIn("color: #111111", APP_STYLE)

    def test_empty_document_windows_show_prompt_and_restored_reload(self):
        expected = "請拖選專利文件進入視窗或透過上方欄位瀏覽"
        self.assertEqual(self.page.issue_table.centered_placeholder, expected)
        self.assertEqual(
            self.page.original_paragraph_view.centered_placeholder,
            expected,
        )
        self.assertEqual(self.page.full_symbol_text.centered_placeholder, expected)
        self.assertEqual(
            self.page.representative_symbol_text.centered_placeholder,
            expected,
        )
        self.assertTrue(hasattr(self.page, "reload_button"))
        self.assertEqual(self.page.reload_button.text(), "重新檢核")
        self.assertFalse(self.page.reload_button.isEnabled())
        self.assertEqual(self.page.right_splitter.count(), 2)
        self.assertEqual(
            self.page.custom_rules_button.objectName(),
            "PrimaryButton",
        )

        document = build_document()
        review = review_document(document)
        self.page.show_review(
            document,
            review,
            extract_document_symbols(document, review),
        )
        self.assertEqual(self.page.issue_table.centered_placeholder, "")
        self.assertEqual(self.page.full_symbol_text.centered_placeholder, "")
        self.assertTrue(self.page.reload_button.isEnabled())

    def test_review_and_error_context_use_requested_font_sizes(self):
        context_size = (
            self.page.original_paragraph_view.document()
            .defaultFont()
            .pointSizeF()
        )

        self.assertAlmostEqual(context_size, 14.0)
        self.assertAlmostEqual(
            self.page.original_paragraph_view.font().pointSizeF(),
            14.0,
        )
        self.assertAlmostEqual(self.page.issue_table.font().pointSizeF(), 12.0)
        self.assertAlmostEqual(
            self.page.issue_table.horizontalHeader().font().pointSizeF(),
            12.0,
        )

    def test_reload_button_reopens_the_current_word_path(self):
        self.page.file_line.setText("C:/synthetic/current.docx")
        with patch.object(
            self.page,
            "load_document",
            return_value=True,
        ) as load_document:
            self.assertTrue(self.page.reload_document())

        load_document.assert_called_once_with("C:/synthetic/current.docx")

    def test_review_auto_publishes_two_lists_then_manual_send_opens_ocr(self):
        document = build_document()
        review = review_document(document)
        transfer = extract_document_symbols(document, review)

        self.page.show_review(document, review, transfer)

        self.assertIs(self.context.symbol_transfer, transfer)
        self.assertEqual(self.page.full_symbol_text.toPlainText(), "1:底座\n2:上蓋")
        self.assertEqual(self.page.representative_symbol_text.toPlainText(), "1:底座")
        self.assertTrue(self.page.send_button.isEnabled())

        self.page.full_symbol_text.append("3:人工新增")
        self.page.representative_symbol_text.append("3:人工新增")
        self.page.publish_to_ocr()

        self.assertEqual(self.opened_features, ["patent_ocr"])
        self.assertEqual(
            [entry.label for entry in self.context.symbol_transfer.full_entries],
            ["1", "2", "3"],
        )
        self.assertEqual(
            [entry.label for entry in self.context.symbol_transfer.representative_entries],
            ["1", "3"],
        )

    def test_document_whitelist_add_delete_and_manual_rerun(self):
        document = build_document()
        typo_text = "該底坐1可移動。"
        document.paragraphs.append(
            PatentParagraph(
                index=len(document.paragraphs),
                text=typo_text,
                normalized_text=normalize_patent_text(typo_text),
                content_text=normalize_patent_text(typo_text),
                source_path="body/p[6]",
                section_key="embodiments",
                section_title="實施方式",
                major_section_key="major_description",
                run_spans=[TextRunSpan(0, 0, len(typo_text), typo_text)],
            )
        )
        review = review_document(document)
        self.assertTrue(
            any(
                issue.rule_id == "REF005"
                and issue.details.get("candidate") == "底坐"
                for issue in review.issues
            )
        )
        self.page.show_review(
            document,
            review,
            extract_document_symbols(document, review),
        )

        self.page.document_whitelist_input.setText("底坐")
        self.assertTrue(self.page.add_document_whitelist_term())
        self.assertEqual(self.page.document_whitelist_table.rowCount(), 1)
        self.assertTrue(
            any(issue.rule_id == "REF005" for issue in self.page.review.issues)
        )

        self.assertTrue(self.page.rerun_review())
        self.wait_for_review()
        self.assertFalse(
            any(
                issue.rule_id == "REF005"
                and issue.details.get("candidate") == "底坐"
                for issue in self.page.review.issues
            )
        )
        self.page.document_whitelist_table.selectRow(0)
        self.assertEqual(
            self.page.delete_selected_document_whitelist_terms(),
            1,
        )
        self.assertTrue(self.page.rerun_review())
        self.wait_for_review()
        self.assertTrue(
            any(
                issue.rule_id == "REF005"
                and issue.details.get("candidate") == "底坐"
                for issue in self.page.review.issues
            )
        )

    def test_document_whitelist_suppresses_missing_label_inside_full_phrase(self):
        document = build_document()
        phrase_text = "該上蓋結構檢查報告已完成。"
        document.paragraphs.append(
            PatentParagraph(
                index=len(document.paragraphs),
                text=phrase_text,
                normalized_text=normalize_patent_text(phrase_text),
                content_text=normalize_patent_text(phrase_text),
                source_path="body/p[6]",
                section_key="embodiments",
                section_title="實施方式",
                major_section_key="major_description",
                run_spans=[TextRunSpan(0, 0, len(phrase_text), phrase_text)],
            )
        )
        review = review_document(document)
        self.assertTrue(
            any(
                issue.rule_id == "REF004"
                and issue.details.get("component_name") == "上蓋"
                for issue in review.issues
            )
        )
        self.page.show_review(
            document,
            review,
            extract_document_symbols(document, review),
        )

        self.page.document_whitelist_input.setText("上蓋結構檢查報告")
        self.assertTrue(self.page.add_document_whitelist_term())
        self.assertTrue(self.page.rerun_review())
        self.wait_for_review()

        self.assertFalse(
            any(
                issue.rule_id == "REF004"
                and issue.details.get("component_name") == "上蓋"
                for issue in self.page.review.issues
            )
        )

    def test_document_whitelist_does_not_cross_to_another_document(self):
        first = build_document()
        second = build_document()
        second.source_path = "C:/synthetic/another.docx"
        second.file_name = "another.docx"
        second.sha256 = "f" * 64
        self.page.show_review(
            first,
            review_document(first),
            extract_document_symbols(first, review_document(first)),
        )
        self.page.document_whitelist_input.setText("第一位置")
        self.assertTrue(self.page.add_document_whitelist_term())

        second_review = review_document(second)
        self.page.show_review(
            second,
            second_review,
            extract_document_symbols(second, second_review),
        )

        self.assertEqual(self.page.document_whitelist_terms, [])
        self.assertEqual(self.page.document_whitelist_table.rowCount(), 0)

    def test_document_is_published_for_the_separate_figure_comparison_page(self):
        lines = [
            "【新型說明書】",
            "【中文新型名稱】測試裝置",
            "【實施方式】",
            "參閱圖1，底座1連接上蓋2。",
            "【符號說明】",
            "1:底座",
            "2:上蓋",
        ]
        paragraphs = [
            PatentParagraph(
                index=index,
                text=text,
                normalized_text=normalize_patent_text(text),
                source_path=f"body/p[{index}]",
                run_spans=[TextRunSpan(0, 0, len(text), text)],
            )
            for index, text in enumerate(lines)
        ]
        sections, patent_type, title = assign_sections(paragraphs)
        paragraphs[3].numbering_id = 10
        paragraphs[3].numbering_value = 1
        paragraphs[3].numbering_text = "【0001】"
        document = PatentDocument(
            source_path="linked.docx",
            file_name="linked.docx",
            file_size_bytes=0,
            sha256="e" * 64,
            patent_type=patent_type,
            patent_title=title,
            paragraphs=paragraphs,
            sections=sections,
        )
        review = review_document(document)
        transfer = extract_document_symbols(document, review)
        self.page.show_review(document, review, transfer)
        published_transfer = self.context.symbol_transfer
        published_review = self.page.review

        self.context.publish_ocr_results(
            [{"image_name": "Pic_01", "numbers": ["1", "3"]}]
        )

        self.assertIs(self.context.document, document)
        self.assertIs(self.page.review, published_review)
        self.assertFalse(
            any(issue.rule_id == "OCR001" for issue in self.page.review.issues)
        )
        self.assertIs(self.context.symbol_transfer, published_transfer)

    def test_selecting_issue_shows_error_type_and_original_location_only(self):
        document = build_document()
        original_title = document.paragraphs[0].text
        review = review_document(document)
        transfer = extract_document_symbols(document, review)
        self.page.show_review(document, review, transfer)

        txt_row = next(
            row
            for row in range(self.page.issue_table.rowCount())
            if self.page._issue_for_row(row).rule_id == "TXT001"
        )
        self.page.issue_table.selectRow(txt_row)
        self.app.processEvents()
        self.assertEqual(
            self.page.original_paragraph_view.toPlainText(),
            original_title,
        )
        self.assertIn("【中文新型名稱】", self.page.selected_issue_title.text())
        self.assertFalse(hasattr(self.page, "error_type_label"))
        self.assertFalse(hasattr(self.page, "error_location_label"))
        visible_labels = [
            label.text() for label in self.page.findChildren(QLabel)
        ]
        self.assertFalse(any(text.startswith("錯誤種類：") for text in visible_labels))
        self.assertFalse(any(text.startswith("原文位置：") for text in visible_labels))
        self.assertFalse(any(text.startswith("原始 Word 段落") for text in visible_labels))
        self.assertGreaterEqual(self.page.original_paragraph_view.minimumHeight(), 190)
        self.assertEqual(self.page.issue_table.columnCount(), 4)
        headers = [
            self.page.issue_table.horizontalHeaderItem(column).text()
            for column in range(self.page.issue_table.columnCount())
        ]
        self.assertEqual(headers, ["等級", "章節", "位置", "錯誤種類"])
        self.assertNotIn("規則", headers)
        self.assertNotIn("建議", headers)
        self.assertEqual(document.paragraphs[0].text, original_title)
        self.assertFalse(hasattr(self.page, "correction_edit"))
        self.assertFalse(hasattr(self.page, "accept_suggestion_button"))

        structure_row = next(
            row
            for row in range(self.page.issue_table.rowCount())
            if self.page._issue_for_row(row).rule_id == "STR001"
        )
        self.page.issue_table.selectRow(structure_row)
        self.app.processEvents()
        self.assertTrue(
            self.page.selected_issue_title.text().startswith("錯誤原文定位 · ")
        )
        self.assertIn(
            "沒有單一原始段落",
            self.page.original_paragraph_view.toPlainText(),
        )

    def test_error_type_column_wraps_after_thirty_characters(self):
        document = build_document()
        review = review_document(document)
        long_message = "這是一段用來驗證錯誤種類欄位超過三十個字元後會自動換行的完整測試訊息"
        review.issues.append(
            PatentIssue(
                issue_id="long-error-type-test",
                rule_id="TEST001",
                severity="error",
                category="test",
                message=long_message,
                suggestion="測試",
                section_key=document.paragraphs[0].section_key or "",
                section_title=document.paragraphs[0].section_title,
                paragraph_index=0,
            )
        )
        transfer = extract_document_symbols(document, review)
        self.page.show_review(document, review, transfer)

        row = next(
            row
            for row in range(self.page.issue_table.rowCount())
            if self.page._issue_for_row(row).issue_id == "long-error-type-test"
        )
        issue = self.page._issue_for_row(row)
        displayed = self.page.issue_table.item(row, 3).text()
        displayed_lines = displayed.split("\n")

        self.assertEqual("".join(displayed_lines), issue.message)
        self.assertGreater(len(displayed_lines), 1)
        self.assertTrue(all(len(line) <= 30 for line in displayed_lines))
        self.assertEqual(self.page.issue_table.item(row, 3).toolTip(), issue.message)
        self.assertGreater(
            self.page.issue_table.rowHeight(row),
            self.page.issue_table.fontMetrics().height(),
        )

    def test_issue_location_prefers_numeric_paragraph_then_medium_section(self):
        document = build_document()
        numbered = document.paragraphs[2]
        target = document.paragraphs[3]
        numbered.numbering_text = "〖0007〗"
        numbered.numbering_value = 7
        issue = PatentIssue(
            issue_id="location-test",
            rule_id="TEST001",
            severity="error",
            category="test",
            message="測試",
            suggestion="測試",
            section_key=target.section_key or "",
            section_title=target.section_title,
            paragraph_index=target.index,
        )
        self.page.document = document

        self.assertEqual(self.page._logical_issue_location(issue), "【0007】")

        numbered.numbering_text = ""
        numbered.numbering_value = None
        self.assertEqual(self.page._logical_issue_location(issue), "【符號說明】")

    def test_table_issue_location_uses_table_row_and_column(self):
        document = build_document()
        target = document.paragraphs[3]
        target.source_kind = "table"
        target.table_index = 1
        target.row_index = 2
        target.cell_index = 3
        issue = PatentIssue(
            issue_id="table-location-test",
            rule_id="TEST001",
            severity="error",
            category="test",
            message="測試",
            suggestion="測試",
            section_key=target.section_key or "",
            section_title=target.section_title,
            paragraph_index=target.index,
        )
        self.page.document = document

        self.assertEqual(
            self.page._logical_issue_location(issue),
            "表格2／第3列／第4欄",
        )

    def test_selected_issue_shows_full_paragraph_and_highlights_only_term(self):
        text = "第一句沒有問題。第二句前段，這裡出現錯誤詞語需要修正，最後一句沒有問題。"
        issue = PatentIssue(
            issue_id="clause-test",
            rule_id="TEST001",
            severity="warning",
            category="test",
            message="偵測到「錯誤詞語」。",
            suggestion="請修正。",
            paragraph_index=0,
            char_start=0,
            char_end=len(text),
            details={"highlight_text": "錯誤詞語"},
        )
        start, end = self.page._resolved_issue_span(text, issue)

        self.assertEqual(text[start:end], "錯誤詞語")
        self.page.original_paragraph_view.setHtml(
            self.page._highlighted_paragraph(text, start, end)
        )
        shown = self.page.original_paragraph_view.toPlainText()
        self.assertEqual(shown, text)
        self.assertIn("第一句沒有問題", shown)
        self.assertIn("最後一句沒有問題", shown)
        self.assertIn("background-color:#fecaca", self.page.original_paragraph_view.toHtml())

    def test_claim_issue_shows_every_word_paragraph_until_next_claim(self):
        lines = [
            "【新型申請專利範圍】",
            "一種測試裝置，包含：",
            "一底座；",
            "以及一出現錯誤詞語的上蓋。",
            "如請求項1所述的測試裝置，其中該上蓋可移動。",
        ]
        paragraphs = [
            PatentParagraph(
                index=index,
                text=text,
                normalized_text=normalize_patent_text(text),
                source_path=f"body/p[{index}]",
                run_spans=[TextRunSpan(0, 0, len(text), text)],
            )
            for index, text in enumerate(lines)
        ]
        paragraphs[1].numbering_text = "請求項1"
        paragraphs[4].numbering_text = "請求項2"
        sections, patent_type, title = assign_sections(paragraphs)
        document = PatentDocument(
            source_path="claims.docx",
            file_name="claims.docx",
            file_size_bytes=0,
            sha256="f" * 64,
            patent_type=patent_type,
            patent_title=title,
            paragraphs=paragraphs,
            sections=sections,
        )
        issue = PatentIssue(
            issue_id="claim-context-test",
            rule_id="CLM016",
            severity="error",
            category="claims",
            message="請求項1偵測到錯誤詞語。",
            suggestion="請修正。",
            section_key="claims",
            section_title="申請專利範圍",
            paragraph_index=1,
            details={"highlight_text": "錯誤詞語"},
        )
        self.page.document = document

        self.page._show_issue(issue)

        shown = self.page.original_paragraph_view.toPlainText()
        self.assertIn("請求項1", shown)
        self.assertIn("一種測試裝置，包含：", shown)
        self.assertIn("一底座；", shown)
        self.assertIn("以及一出現錯誤詞語的上蓋。", shown)
        self.assertNotIn("如請求項1所述", shown)
        self.assertIn(
            "background-color:#fecaca",
            self.page.original_paragraph_view.toHtml(),
        )

    def test_duplicate_claim_reminder_highlights_complete_repeated_claim(self):
        lines = [
            "【新型申請專利範圍】",
            "一種測試裝置，包含一底座及一上蓋。",
            "一種測試裝置，包含",
            "一底座及一上蓋。",
        ]
        paragraphs = [
            PatentParagraph(
                index=index, text=text,
                normalized_text=normalize_patent_text(text),
                source_path=f"body/p[{index}]",
            )
            for index, text in enumerate(lines)
        ]
        paragraphs[1].numbering_text = "請求項1"
        paragraphs[2].numbering_text = "請求項2"
        sections, patent_type, title = assign_sections(paragraphs)
        document = PatentDocument(
            source_path="duplicates.docx", file_name="duplicates.docx",
            file_size_bytes=0, sha256="f" * 64, patent_type=patent_type,
            patent_title=title, paragraphs=paragraphs, sections=sections,
        )
        issues = [
            issue for issue in review_document(document).issues
            if issue.rule_id == "CLM018"
        ]
        self.assertEqual(len(issues), 1)
        self.page.document = document
        issue = issues[0]
        context = self.page._claim_context(issue)
        start, end = self.page._resolved_claim_issue_span(context, issue)
        self.assertEqual(
            re.sub(r"\s+", "", context["text"][start:end]), lines[1]
        )
        self.page._show_issue(issue)
        shown = self.page.original_paragraph_view.toPlainText()
        self.assertIn("請求項2", shown)
        self.assertNotIn("請求項1", shown)
        self.assertIn("一底座及一上蓋。", shown)
        self.assertIn("background-color:#fecaca", self.page.original_paragraph_view.toHtml())

    def test_coverage_issue_shows_full_claim_and_original_disclosure_evidence(self):
        from test_claim_coverage_integration import coverage_document

        document = coverage_document(
            ["一種測試裝置，該基座具有三開口。"], claims=[
                "【請求項1】一種測試裝置，",
                "該基座具有二開口。",
            ],
        )
        review = review_document(document)
        issue = next(
            issue for issue in review.issues
            if issue.rule_id == "CLM019" and issue.details["coverage_status"] == "conflict"
        )
        self.page.document = document
        context = self.page._claim_context(issue)
        start, end = self.page._resolved_claim_issue_span(context, issue)
        self.assertEqual(context["text"][start:end], "該基座具有二開口")
        self.page._show_issue(issue)
        text = self.page.original_paragraph_view.toPlainText()
        self.assertIn("請求項1", text)
        self.assertIn("一種測試裝置", text)
        self.assertIn("該基座具有二開口", text)
        self.assertIn("該基座具有三開口", text)
        self.assertIn("【0004】", text)
        self.assertIn("候選原文（不代表已涵蓋）", text)
        html = self.page.original_paragraph_view.toHtml()
        self.assertIn("background-color:#fecaca", html)
        self.assertIn("background-color:#dbeafe", html)

    def test_coverage_evidence_does_not_render_untrusted_markup_or_other_sections(self):
        from test_claim_coverage_integration import coverage_document

        document = coverage_document(["<b>該基座具有三開口。</b>"])
        self.page.document = document
        disclosure = next(p for p in document.paragraphs if p.text.startswith("<b>"))
        embodiment = next(p for p in document.paragraphs if p.section_key == "embodiments" and not p.is_heading)
        issue = PatentIssue(
            issue_id="coverage-html", rule_id="CLM019", severity="warning",
            category="claims", message="需確認", suggestion="請核對原文",
            details={"coverage_item": {
                "reason": "<script>不是網頁指令</script>",
                "evidence": [
                    {"paragraph_index": disclosure.index, "char_start": 0, "char_end": len(disclosure.text)},
                    {"paragraph_index": embodiment.index, "char_start": 0, "char_end": len(embodiment.text)},
                ],
            }},
        )
        html = self.page._coverage_evidence_html(issue)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;b&gt;", html)
        self.assertNotIn(embodiment.text, html)

    def test_grouped_coverage_info_highlights_every_unparsed_fragment(self):
        from test_claim_coverage_integration import coverage_document

        document = coverage_document(["一種測試裝置。"], claims=[
            "【請求項1】一種測試裝置，該外殼由未知材料製成，該基座滿足Ω準則。",
        ])
        self.page.document = document
        issue = next(i for i in review_document(document).issues if i.rule_id == "CLM019" and i.severity == "info")
        self.page._show_issue(issue)
        text = self.page.original_paragraph_view.toPlainText()
        self.assertIn("待確認 1：該外殼由未知材料製成", text)
        self.assertIn("待確認 2：該基座滿足Ω準則", text)
        html = self.page.original_paragraph_view.toHtml()
        self.assertEqual(html.count("background-color:#fecaca"), 2)

    def test_claim_issue_local_span_is_mapped_from_later_word_paragraph(self):
        lines = [
            "【新型申請專利範圍】",
            "一種測試裝置，包含：",
            "一底座；",
            "以及一出現錯誤詞語的上蓋。",
            "如請求項1所述的測試裝置，其中該上蓋可移動。",
        ]
        paragraphs = [
            PatentParagraph(
                index=index,
                text=text,
                normalized_text=normalize_patent_text(text),
                source_path=f"body/p[{index}]",
                run_spans=[TextRunSpan(0, 0, len(text), text)],
            )
            for index, text in enumerate(lines)
        ]
        paragraphs[1].numbering_text = "請求項1"
        paragraphs[4].numbering_text = "請求項2"
        sections, patent_type, title = assign_sections(paragraphs)
        document = PatentDocument(
            source_path="claims.docx",
            file_name="claims.docx",
            file_size_bytes=0,
            sha256="f" * 64,
            patent_type=patent_type,
            patent_title=title,
            paragraphs=paragraphs,
            sections=sections,
        )
        start = paragraphs[3].text.index("錯誤詞語")
        issue = PatentIssue(
            issue_id="claim-local-span-test",
            rule_id="CLM017",
            severity="error",
            category="claims",
            message="請求項1的後段標點錯誤。",
            suggestion="請修正。",
            section_key="claims",
            section_title="申請專利範圍",
            paragraph_index=3,
            char_start=start,
            char_end=start + len("錯誤詞語"),
        )
        self.page.document = document

        context = self.page._claim_context(issue)
        resolved_start, resolved_end = self.page._resolved_claim_issue_span(
            context,
            issue,
        )

        self.assertEqual(
            context["text"][resolved_start:resolved_end],
            "錯誤詞語",
        )
        self.assertGreater(resolved_start, len(paragraphs[1].text))

    def test_claim_full_stop_issue_highlights_internal_stop_in_complete_claim(self):
        lines = [
            "【發明申請專利範圍】",
            "一種測試裝置，包含：",
            "一底座。",
            "該底座連接一上蓋。",
            "如請求項1所述的測試裝置，其中該上蓋可移動。",
        ]
        paragraphs = [
            PatentParagraph(
                index=index, text=text, normalized_text=normalize_patent_text(text),
                source_path=f"body/p[{index}]",
                run_spans=[TextRunSpan(0, 0, len(text), text)],
            )
            for index, text in enumerate(lines)
        ]
        paragraphs[1].numbering_text = "請求項1"
        paragraphs[4].numbering_text = "請求項2"
        sections, patent_type, title = assign_sections(paragraphs)
        document = PatentDocument(
            source_path="claims.docx", file_name="claims.docx", file_size_bytes=0,
            sha256="f" * 64, patent_type=patent_type, patent_title=title,
            paragraphs=paragraphs, sections=sections,
        )
        issues = [issue for issue in review_document(document).issues if issue.rule_id == "CLM009"]
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue.details, {"claim_number": 1, "full_stop_count": 2})
        self.assertEqual(issue.paragraph_index, 2)
        self.page.document = document
        context = self.page._claim_context(issue)
        start, end = self.page._resolved_claim_issue_span(context, issue)
        self.assertEqual(context["text"][start:end], "。")
        self.assertTrue(context["text"][:start].endswith("一底座"))
        self.page._show_issue(issue)
        shown = self.page.original_paragraph_view.toPlainText()
        self.assertIn("一種測試裝置，包含：", shown)
        self.assertIn("一底座。", shown)
        self.assertIn("該底座連接一上蓋。", shown)
        self.assertNotIn("其中該上蓋可移動", shown)
        self.assertIn("background-color:#fecaca", self.page.original_paragraph_view.toHtml())

    def test_claim_body_offset_selects_correct_repeated_component(self):
        lines = [
            "【新型申請專利範圍】",
            "一種測試裝置，包含一底座；",
            "該底座連接該底座。",
            "如請求項1所述的測試裝置，其中該底座可移動。",
        ]
        paragraphs = [
            PatentParagraph(
                index=index,
                text=text,
                normalized_text=normalize_patent_text(text),
                source_path=f"body/p[{index}]",
                run_spans=[TextRunSpan(0, 0, len(text), text)],
            )
            for index, text in enumerate(lines)
        ]
        paragraphs[1].numbering_text = "請求項1"
        paragraphs[3].numbering_text = "請求項2"
        sections, patent_type, title = assign_sections(paragraphs)
        document = PatentDocument(
            source_path="repeated-claim.docx",
            file_name="repeated-claim.docx",
            file_size_bytes=0,
            sha256="e" * 64,
            patent_type=patent_type,
            patent_title=title,
            paragraphs=paragraphs,
            sections=sections,
        )
        normalized_body = re.sub(r"\s+", "", lines[1] + lines[2])
        issue = PatentIssue(
            issue_id="claim-body-offset-test",
            rule_id="CLM012",
            severity="error",
            category="claims",
            message="請求項1使用錯誤的元件指稱。",
            suggestion="請修正。",
            section_key="claims",
            section_title="申請專利範圍",
            paragraph_index=1,
            char_start=0,
            char_end=len(paragraphs[1].text),
            details={
                "highlight_text": "該底座",
                "component_name": "底座",
                "body_offset": normalized_body.rfind("底座"),
            },
        )
        self.page.document = document

        context = self.page._claim_context(issue)
        resolved_start, resolved_end = self.page._resolved_claim_issue_span(
            context,
            issue,
        )

        self.assertEqual(
            context["text"][resolved_start:resolved_end],
            "該底座",
        )
        self.assertEqual(resolved_start, context["text"].rfind("該底座"))


if __name__ == "__main__":
    unittest.main()
