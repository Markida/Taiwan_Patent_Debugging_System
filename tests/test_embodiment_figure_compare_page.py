import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from app.workflow_context import PatentWorkflowContext
from features.patent_review.models import (
    PatentDocument,
    PatentParagraph,
    TextRunSpan,
)
from features.patent_review.section_parser import assign_sections
from features.patent_review.text_normalizer import normalize_patent_text
from ui.embodiment_figure_compare_page import EmbodimentFigureComparePage


def build_document():
    lines = [
        "【新型說明書】",
        "【中文新型名稱】測試裝置",
        "【實施方式】",
        "參閱圖1，底座1連接上蓋2及感測器A。",
        "該底座1支撐上蓋2及感測器A。",
        "【符號說明】",
        "1:底座",
        "2:上蓋",
        "A:感測器",
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
    for value, index in enumerate((3, 4), start=1):
        paragraphs[index].numbering_id = 10
        paragraphs[index].numbering_value = value
        paragraphs[index].numbering_text = f"【{value:04d}】"
    return PatentDocument(
        source_path="comparison.docx",
        file_name="comparison.docx",
        file_size_bytes=0,
        sha256="f" * 64,
        patent_type=patent_type,
        patent_title=title,
        paragraphs=paragraphs,
        sections=sections,
    )


class EmbodimentFigureComparePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.context = PatentWorkflowContext()
        self.opened = []
        self.page = EmbodimentFigureComparePage(lambda: None)
        self.page.set_open_feature_callback(self.opened.append)
        self.page.set_workflow_context(self.context)

    def tearDown(self):
        self.page.deleteLater()

    def test_two_windows_allow_independent_paragraph_and_figure_selection(self):
        with TemporaryDirectory() as temporary_directory:
            first_path = Path(temporary_directory) / "圖1.png"
            second_path = Path(temporary_directory) / "圖2.png"
            for path, color in (
                (first_path, QColor("white")),
                (second_path, QColor("lightgray")),
            ):
                image = QImage(240, 160, QImage.Format_RGB32)
                image.fill(color)
                self.assertTrue(image.save(str(path)))

            self.context.publish_document(build_document())
            self.context.publish_ocr_results(
                [
                    {
                        "image_path": str(first_path),
                        "source_image_name": first_path.name,
                        "figure_numbers": [1],
                        "numbers": ["1", "2", "A"],
                    },
                    {
                        "image_path": str(second_path),
                        "source_image_name": second_path.name,
                        "figure_numbers": [2],
                        "numbers": ["9"],
                    },
                ]
            )

            self.assertEqual(self.page.paragraph_combo.count(), 2)
            self.assertEqual(self.page.paragraph_table.rowCount(), 2)
            self.assertEqual(self.page.figure_combo.count(), 2)
            self.assertIn("【0001】", self.page.paragraph_combo.itemText(0))
            self.assertIn("圖2", self.page.figure_combo.itemText(1))
            self.assertEqual(
                self.page.comparisons[0].paragraph_labels,
                ["1", "2", "A"],
            )

            self.page.paragraph_combo.setCurrentIndex(1)
            self.page.figure_combo.setCurrentIndex(1)
            self.assertEqual(self.page.paragraph_combo.currentIndex(), 1)
            self.assertEqual(self.page.figure_combo.currentIndex(), 1)
            self.assertIn("人工切換的其他圖式頁", self.page.comparison_result.text())
            self.assertNotIn("圖式有、段落沒有", self.page.comparison_result.text())

            self.assertTrue(self.page.select_referenced_figure_page())
            self.assertEqual(self.page.figure_combo.currentIndex(), 0)
            self.assertIn("圖式缺失標號：無", self.page.comparison_result.text())

    def test_selecting_paragraph_jumps_to_its_first_referenced_figure(self):
        with TemporaryDirectory() as temporary_directory:
            paths = []
            for number in (1, 2, 3):
                path = Path(temporary_directory) / f"圖{number}.png"
                image = QImage(200, 120, QImage.Format_RGB32)
                image.fill(QColor("white"))
                self.assertTrue(image.save(str(path)))
                paths.append(path)

            document = build_document()
            second = document.paragraphs[4]
            second.text = "參閱圖2及圖3，該底座1支撐上蓋2。"
            second.normalized_text = second.text
            second.content_text = second.text
            self.context.publish_document(document)
            self.context.publish_ocr_results(
                [
                    {
                        "image_path": str(path),
                        "source_image_name": path.name,
                        "figure_numbers": [number],
                        "numbers": ["1", "2"],
                    }
                    for number, path in enumerate(paths, start=1)
                ]
            )

            self.page.paragraph_table.selectRow(1)

            self.assertEqual(self.page.paragraph_combo.currentIndex(), 1)
            self.assertEqual(self.page.figure_combo.currentIndex(), 1)
            self.assertIn("圖2", self.page.figure_meta.text())

    def test_paragraph_labels_are_boxed_and_only_missing_drawing_labels_reported(self):
        with TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "圖1.png"
            image = QImage(240, 160, QImage.Format_RGB32)
            image.fill(QColor("white"))
            self.assertTrue(image.save(str(image_path)))

            self.context.publish_document(build_document())
            self.context.publish_ocr_results(
                [
                    {
                        "image_path": str(image_path),
                        "source_image_name": image_path.name,
                        "figure_numbers": [1],
                        "numbers": ["1", "2", "9"],
                    }
                ]
            )

            html = self.page.paragraph_text.toHtml()
            self.assertIn("border", html)
            self.assertIn("圖式缺失標號：A", self.page.comparison_result.text())
            self.assertNotIn("圖式缺失標號：9", self.page.comparison_result.text())

    def test_navigation_buttons_return_to_source_features(self):
        buttons = self.page.findChildren(type(self.page.auto_match_button))
        next(button for button in buttons if button.text() == "前往文件偵錯").click()
        next(button for button in buttons if button.text() == "前往圖式標號").click()

        self.assertEqual(self.opened, ["patent_review", "patent_ocr"])


if __name__ == "__main__":
    unittest.main()
