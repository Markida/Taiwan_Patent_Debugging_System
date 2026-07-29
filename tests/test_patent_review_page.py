import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.workflow_context import PatentWorkflowContext
from features.patent_review.models import PatentDocument, PatentParagraph, TextRunSpan
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

    def tearDown(self):
        self.page.deleteLater()

    def test_home_registry_contains_document_review_page(self):
        self.assertIn("patent_review", [feature["id"] for feature in FEATURES])

    def test_compact_top_rows_and_prominent_dark_symbol_tabs(self):
        margins = self.page.layout().contentsMargins()
        splitter_index = self.page.layout().indexOf(self.page.main_splitter)

        self.assertEqual(margins.top(), 4)
        self.assertEqual(self.page.layout().stretch(splitter_index), 1)
        self.assertLessEqual(self.page.status_label.maximumHeight(), 38)
        self.assertTrue(self.page.symbol_tabs.tabBar().expanding())
        self.assertIn("background: #334155", self.page.symbol_tabs.styleSheet())
        self.assertIn("color: #ffffff", self.page.symbol_tabs.styleSheet())

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

    def test_selecting_issue_shows_error_type_and_original_location_only(self):
        document = build_document()
        original_title = document.paragraphs[0].text
        review = review_document(document)
        transfer = extract_document_symbols(document, review)
        self.page.show_review(document, review, transfer)

        txt_row = next(
            row
            for row in range(self.page.issue_table.rowCount())
            if self.page.issue_table.item(row, 1).text() == "TXT001"
        )
        self.page.issue_table.selectRow(txt_row)
        self.app.processEvents()
        self.assertEqual(
            self.page.original_paragraph_view.toPlainText(),
            original_title,
        )
        self.assertIn("TXT001", self.page.error_type_label.text())
        self.assertIn("全形英數字", self.page.error_type_label.text())
        self.assertIn("段落：0", self.page.error_location_label.text())
        self.assertIn("字元：", self.page.error_location_label.text())
        self.assertEqual(self.page.issue_table.columnCount(), 6)
        self.assertEqual(document.paragraphs[0].text, original_title)
        self.assertFalse(hasattr(self.page, "correction_edit"))
        self.assertFalse(hasattr(self.page, "accept_suggestion_button"))

        structure_row = next(
            row
            for row in range(self.page.issue_table.rowCount())
            if self.page.issue_table.item(row, 1).text() == "STR001"
        )
        self.page.issue_table.selectRow(structure_row)
        self.app.processEvents()
        self.assertIn("STR001", self.page.error_type_label.text())
        self.assertIn("文件層級", self.page.error_location_label.text())


if __name__ == "__main__":
    unittest.main()
