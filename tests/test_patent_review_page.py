import os
import re
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from app.workflow_context import PatentWorkflowContext
from features.patent_review.models import (
    PatentDocument,
    PatentIssue,
    PatentParagraph,
    TextRunSpan,
)
from features.patent_review.rule_engine import review_document
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
        self.opened_features = []
        self.context = PatentWorkflowContext()
        self.page = PatentReviewPage(lambda: None)
        self.page.set_workflow_context(self.context)
        self.page.set_open_feature_callback(self.opened_features.append)
        self.page.set_feature_navigation(FEATURES, self.opened_features.append)

    def tearDown(self):
        self.page.deleteLater()

    def test_home_registry_contains_document_review_page(self):
        self.assertIn("patent_review", [feature["id"] for feature in FEATURES])

    def test_shared_top_navigation_is_generated_from_feature_registry(self):
        labels = [
            button.text()
            for button in self.page.feature_navigation.findChildren(QPushButton)
        ]
        self.assertEqual(
            labels,
            ["回首頁", "文件偵錯", "圖式標號", "段落圖式比對 (beta)"],
        )
        self.assertNotIn("功能測試頁", labels)
        active = self.page.feature_navigation.findChild(
            QPushButton, "ActiveFeatureNavigationButton"
        )
        self.assertIsNotNone(active)
        self.assertEqual(active.text(), "文件偵錯")

    def test_compact_top_rows_and_prominent_dark_symbol_tabs(self):
        margins = self.page.layout().contentsMargins()
        splitter_index = self.page.layout().indexOf(self.page.main_splitter)

        self.assertEqual(margins.top(), 4)
        self.assertEqual(self.page.layout().stretch(splitter_index), 1)
        self.assertLessEqual(self.page.status_label.maximumHeight(), 38)
        self.assertTrue(self.page.symbol_tabs.tabBar().expanding())
        self.assertIn("background: #334155", self.page.symbol_tabs.styleSheet())
        self.assertIn("color: #ffffff", self.page.symbol_tabs.styleSheet())

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
