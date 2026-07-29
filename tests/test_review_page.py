import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from features.patent_review.symbol_transfer import (
    DocumentSymbolTransfer,
    PatentSymbolEntry,
)
from ui.recognition_page import (
    LABEL_COLUMN,
    STATUS_COLUMN,
    RecognitionPage,
)


class RecognitionReviewPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.page = RecognitionPage(lambda: None)
        self.page.all_results = [{
            "image_name": "Pic_01",
            "source_image_name": "original.png",
            "image_path": "original.png",
            "original_image_path": "original.png",
            "rotation_degrees": 0,
            "numbers": ["B", "2"],
            "detections": [
                {
                    "label": "B",
                    "original_label": "B",
                    "confidence": 0.52,
                    "x1": 10,
                    "y1": 10,
                    "x2": 30,
                    "y2": 30,
                },
                {
                    "label": "2",
                    "original_label": "2",
                    "confidence": 0.94,
                    "x1": 40,
                    "y1": 10,
                    "x2": 60,
                    "y2": 30,
                },
            ],
        }]
        self.page.populate_review_table()

    def tearDown(self):
        self.page.deleteLater()

    def test_low_confidence_row_is_red_and_editable(self):
        label_item = self.page.review_table.item(0, LABEL_COLUMN)
        status_item = self.page.review_table.item(0, STATUS_COLUMN)

        self.assertEqual(status_item.text(), "低信心，請確認")
        self.assertEqual(label_item.background().color().name(), "#fee2e2")
        self.assertTrue(label_item.flags() & label_item.flags().ItemIsEditable)

        label_item.setText("8")
        reviewed = self.page.collect_reviewed_results()
        self.assertEqual(reviewed[0]["numbers"], ["8", "2"])
        self.assertTrue(reviewed[0]["detections"][0]["manual_edited"])

    def test_default_confidence_threshold_is_point_six(self):
        self.assertAlmostEqual(self.page.confidence_threshold.value(), 0.60)
        self.assertFalse(hasattr(self.page, "save_button"))

    def test_document_review_button_returns_directly_to_review_page(self):
        opened_features = []
        self.page.set_open_feature_callback(opened_features.append)

        self.page.document_review_button.click()

        self.assertEqual(opened_features, ["patent_review"])
        self.assertEqual(self.page.document_review_button.text(), "← 回文件偵錯")

    def test_result_sorting_is_enabled_by_default(self):
        self.assertTrue(self.page.result_sort_button.isChecked())
        self.assertTrue(self.page._sort_result_numbers)

    def test_review_table_is_above_smaller_reference_input(self):
        right_layout = self.page.review_table.parentWidget().layout()
        self.assertLess(
            right_layout.indexOf(self.page.review_table),
            right_layout.indexOf(self.page.reference_text),
        )
        self.assertEqual(self.page.reference_text.height(), 78)

    def test_document_symbol_button_switches_two_independent_drafts(self):
        transfer = DocumentSymbolTransfer(
            source_path="sample.docx",
            file_name="sample.docx",
            source_sha256="c" * 64,
            patent_title="測試裝置",
            full_entries=[
                PatentSymbolEntry("1", "底座", "1", "full", 1, "p[1]", 0, 1),
                PatentSymbolEntry("2", "上蓋", "2", "full", 2, "p[2]", 0, 1),
            ],
            representative_entries=[
                PatentSymbolEntry(
                    "1", "底座", "1", "representative", 4, "p[4]", 0, 1
                ),
            ],
        )

        self.page.load_document_symbol_transfer(transfer)
        self.assertEqual(self.page.reference_text.toPlainText(), "1:底座\n2:上蓋")
        self.assertIn("完整符號說明", self.page.reference_source_button.text())

        self.page.reference_text.append("3:人工補入")
        self.page.reference_source_button.setChecked(True)
        self.assertEqual(self.page.reference_text.toPlainText(), "1:底座")
        self.assertIn("代表圖符號說明", self.page.reference_source_button.text())

        self.page.reference_text.append("9:代表圖人工補入")
        self.page.reference_source_button.setChecked(False)
        self.assertIn("3:人工補入", self.page.reference_text.toPlainText())
        self.assertNotIn("9:代表圖人工補入", self.page.reference_text.toPlainText())

    def test_all_detection_boxes_are_enabled_by_default(self):
        self.assertTrue(self.page.show_all_boxes_button.isChecked())
        self.assertEqual(
            self.page.show_all_boxes_button.text(),
            "隱藏一般辨識框",
        )

    def test_low_confidence_rows_are_sorted_to_the_top(self):
        result = self.page.all_results[0]
        result["detections"] = [
            {
                "label": "9",
                "original_label": "9",
                "confidence": 0.96,
            },
            {
                "label": "A",
                "original_label": "A",
                "confidence": 0.31,
            },
        ]
        self.page.populate_review_table()

        self.assertEqual(
            self.page.review_table.item(0, LABEL_COLUMN).text(),
            "A",
        )

    def test_result_button_switches_between_all_and_current_picture(self):
        self.page.all_results.append({
            "image_name": "Pic_02",
            "source_image_name": "second.png",
            "image_path": "second.png",
            "original_image_path": "second.png",
            "rotation_degrees": 0,
            "numbers": ["5"],
            "detections": [{
                "label": "5",
                "original_label": "5",
                "confidence": 0.91,
            }],
        })
        self.page.populate_review_table()
        self.page.refresh_result_display()
        self.assertIn("Pic_01", self.page.result_text.toPlainText())
        self.assertIn("Pic_02", self.page.result_text.toPlainText())

        self.page.current_preview_index = 1
        self.page.result_view_button.setChecked(True)

        current_text = self.page.result_text.toPlainText()
        self.assertNotIn("Pic_01", current_text)
        self.assertIn("Pic_02", current_text)
        self.assertEqual(
            self.page.result_view_button.text(),
            "顯示全部結果",
        )

    def test_result_sort_button_uses_left_to_right_character_order(self):
        labels = ["34", "3", "121", "21", "1", "33", "131", "2"]
        self.page.all_results[0]["detections"] = [
            {
                "label": label,
                "original_label": label,
                "confidence": 0.95,
            }
            for label in labels
        ]
        self.page.populate_review_table()
        self.page.refresh_result_display()

        self.assertIn(
            "1, 121, 131, 2, 21, 3, 33, 34",
            self.page.result_text.toPlainText(),
        )

        self.page.result_sort_button.setChecked(False)

        self.assertIn(
            "34, 3, 121, 21, 1, 33, 131, 2",
            self.page.result_text.toPlainText(),
        )
        self.assertEqual(
            self.page.result_sort_button.text(),
            "數字由小到大",
        )

    def test_manual_rotation_replaces_source_without_auto_orientation_state(self):
        self.page.image_paths = ["input.png"]
        self.page.source_image_paths = ["input.png"]
        rotated_path = "manually_rotated.png"

        with patch(
            "ui.recognition_page.rotate_image_clockwise_90",
            return_value=rotated_path,
        ):
            self.page.rotate_current_image_clockwise()

        self.assertEqual(self.page.source_image_paths, [rotated_path])
        self.assertFalse(hasattr(self.page, "manual_orientation_paths"))

    def test_second_stage_uses_all_pictures_and_pic_name(self):
        self.page.reference_text.setPlainText("1:part one\n2:part two")

        self.page.compare_with_reference_list()

        plain_text = self.page.result_text.toPlainText()
        self.assertTrue(plain_text.startswith("All Pictures"))
        self.assertIn("Pic_01", plain_text)
        self.assertIn("標號清單有，但全部圖片都沒有出現：1", plain_text)

    def test_toggle_draws_all_boxes_and_selection_highlights_blue(self):
        self.page.review_table.clearSelection()
        self.page.show_all_boxes_button.setChecked(False)
        pixmap = QPixmap(100, 100)
        pixmap.fill(QColor("#ffffff"))

        low_only = self.page.draw_low_confidence_boxes(pixmap.copy(), 0)
        self.assertEqual(low_only.toImage().pixelColor(10, 10).name(), "#dc2626")
        self.assertEqual(low_only.toImage().pixelColor(40, 10).name(), "#ffffff")

        self.page.show_all_boxes_button.setChecked(True)
        self.page.review_table.selectRow(1)
        highlighted = self.page.draw_low_confidence_boxes(pixmap.copy(), 0)

        self.assertEqual(self.page.show_all_boxes_button.text(), "隱藏一般辨識框")
        self.assertEqual(highlighted.toImage().pixelColor(40, 10).name(), "#2563eb")


if __name__ == "__main__":
    unittest.main()
