import os
from concurrent.futures import Future
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox

from app.workflow_context import PatentWorkflowContext
from features.patent_review.symbol_transfer import (
    FULL_SYMBOL_SOURCE,
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
        # Model selection is tested with a placeholder, never private weights.
        models = tempfile.TemporaryDirectory()
        self.addCleanup(models.cleanup)
        (Path(models.name) / "patent_label_group_v2_gold_ft.onnx").touch()
        model_directory = patch("ui.recognition_page.get_models_dir", return_value=Path(models.name))
        model_directory.start()
        self.addCleanup(model_directory.stop)
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

    def test_default_model_is_gold_fine_tuned_v2(self):
        self.assertEqual(
            Path(self.page.model_path).name,
            "patent_label_group_v2_gold_ft.onnx",
        )
        self.assertTrue(self.page.model_line.isHidden())
        self.assertTrue(self.page.model_button.isHidden())

    def test_delete_key_removes_row_but_not_selected_cell_text(self):
        original_rows = self.page.review_table.rowCount()
        self.page.review_table.selectRow(0)
        QTest.keyClick(self.page.review_table, Qt.Key_Delete)
        self.assertEqual(self.page.review_table.rowCount(), original_rows - 1)

        label_item = self.page.review_table.item(0, LABEL_COLUMN)
        label_item.setText("ABC")
        self.page.show()
        self.page.review_table.editItem(label_item)
        self.app.processEvents()
        editor = self.app.focusWidget()
        self.assertIsInstance(editor, QLineEdit)
        editor.setSelection(1, 1)
        QTest.keyClick(editor, Qt.Key_Delete)
        self.assertEqual(editor.text(), "AC")
        self.assertEqual(self.page.review_table.rowCount(), original_rows - 1)

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

    def test_trusted_reference_list_corrects_audited_one_glyph_confusion(self):
        self.page.all_results[0]["detections"] = [{
            "label": "Ll",
            "original_label": "Ll",
            "confidence": 0.91,
        }]
        self.page.populate_review_table()

        count = self.page.apply_reference_reconciliation([
            {"number": "L1", "name": "定位標記"},
        ])

        self.assertEqual(count, 1)
        self.assertEqual(
            self.page.review_table.item(0, LABEL_COLUMN).text(),
            "L1",
        )
        self.assertEqual(
            self.page.review_table.item(0, STATUS_COLUMN).text(),
            "依清單自動修正",
        )
        reviewed = self.page.collect_reviewed_results()[0]["detections"][0]
        self.assertEqual(reviewed["label"], "L1")
        self.assertEqual(reviewed["original_label"], "Ll")
        self.assertTrue(reviewed["auto_reference_corrected"])
        self.assertFalse(reviewed["manual_edited"])

    def test_reference_switch_restarts_from_raw_ocr_without_stacking(self):
        self.page.all_results[0]["detections"] = [{
            "label": "Ll",
            "original_label": "Ll",
            "confidence": 0.91,
        }]
        self.page.populate_review_table()
        self.page.apply_reference_reconciliation([{"number": "L1"}])

        count = self.page.apply_reference_reconciliation([{"number": "M1"}])

        self.assertEqual(count, 0)
        self.assertEqual(
            self.page.review_table.item(0, LABEL_COLUMN).text(),
            "Ll",
        )

    def test_reference_reconciliation_never_overwrites_manual_edit(self):
        self.page.all_results[0]["detections"] = [{
            "label": "Ll",
            "original_label": "Ll",
            "confidence": 0.91,
        }]
        self.page.populate_review_table()
        self.page.apply_reference_reconciliation([{"number": "L1"}])
        self.page.review_table.item(0, LABEL_COLUMN).setText("L2")

        self.page.apply_reference_reconciliation([{"number": "L1"}])

        self.assertEqual(
            self.page.review_table.item(0, LABEL_COLUMN).text(),
            "L2",
        )
        self.assertTrue(
            self.page.collect_reviewed_results()[0]["detections"][0][
                "manual_edited"
            ]
        )

    def test_delete_selects_the_next_label_for_continuous_review(self):
        self.page.review_table.selectRow(0)
        deleted_label = self.page.review_table.item(0, LABEL_COLUMN).text()

        self.page.delete_selected_labels()

        self.assertEqual(deleted_label, "B")
        self.assertEqual(self.page.review_table.rowCount(), 1)
        self.assertEqual(
            self.page.review_table.currentItem().row(),
            0,
        )
        self.assertEqual(
            self.page.review_table.item(0, LABEL_COLUMN).text(),
            "2",
        )
        self.assertTrue(self.page.review_table.item(0, LABEL_COLUMN).isSelected())

    def test_delete_last_label_falls_back_to_the_new_last_row(self):
        last_row = self.page.review_table.rowCount() - 1
        self.page.review_table.selectRow(last_row)

        self.page.delete_selected_labels()

        self.assertEqual(self.page.review_table.rowCount(), 1)
        self.assertEqual(self.page.review_table.currentRow(), 0)
        self.assertTrue(self.page.review_table.item(0, LABEL_COLUMN).isSelected())

    def test_default_confidence_threshold_is_point_six(self):
        self.assertAlmostEqual(self.page.confidence_threshold.value(), 0.60)
        self.assertFalse(hasattr(self.page, "save_button"))

    def test_duplicate_document_navigation_button_was_removed(self):
        opened_features = []
        self.page.set_open_feature_callback(opened_features.append)

        self.page.open_patent_review()

        self.assertEqual(opened_features, ["patent_review"])
        self.assertFalse(hasattr(self.page, "document_review_button"))

    def test_result_sorting_is_enabled_by_default(self):
        self.assertTrue(self.page._sort_result_numbers)
        self.assertFalse(hasattr(self.page, "result_sort_button"))

    def test_manual_reference_input_is_hidden_and_review_area_is_larger(self):
        right_layout = self.page.review_table.parentWidget().layout()
        self.assertTrue(self.page.reference_text.isHidden())
        self.assertEqual(right_layout.indexOf(self.page.reference_text), -1)
        labels = {label.text() for label in self.page.findChildren(QLabel)}
        self.assertNotIn("標號清單輸入（第二階段使用）", labels)
        review_index = right_layout.indexOf(self.page.review_table)
        result_index = right_layout.indexOf(self.page.result_text)
        self.assertEqual(right_layout.stretch(review_index), 3)
        self.assertEqual(right_layout.stretch(result_index), 2)
        self.assertLess(
            self.page.result_header_layout.indexOf(
                self.page.reference_source_button
            ),
            self.page.result_header_layout.indexOf(self.page.result_view_button),
        )

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
        self.assertTrue(self.page.compare_button.isEnabled())
        self.assertEqual(self.page.reference_text.toPlainText(), "1:底座\n2:上蓋")
        self.assertEqual(
            self.page.reference_source_button.text(),
            "切換至代表圖",
        )
        self.assertIn("目前使用：完整符號說明", self.page.reference_source_button.toolTip())

        self.page.reference_text.append("3:人工補入")
        self.page.reference_source_button.setChecked(True)
        self.assertEqual(self.page.reference_text.toPlainText(), "1:底座")
        self.assertEqual(
            self.page.reference_source_button.text(),
            "切換至完整圖",
        )
        self.assertIn("目前使用：代表圖符號說明", self.page.reference_source_button.toolTip())

        self.page.reference_text.append("9:代表圖人工補入")
        self.page.reference_source_button.setChecked(False)
        self.assertEqual(
            self.page.reference_source_button.text(),
            "切換至代表圖",
        )
        self.assertIn("3:人工補入", self.page.reference_text.toPlainText())
        self.assertNotIn("9:代表圖人工補入", self.page.reference_text.toPlainText())

    def test_representative_switch_jumps_image_and_filters_comparison_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = []
            for number in range(1, 4):
                path = Path(temp_dir) / f"figure_{number}.png"
                pixmap = QPixmap(160, 120)
                pixmap.fill(QColor("white"))
                self.assertTrue(pixmap.save(str(path)))
                paths.append(str(path))

            self.page.image_paths = paths
            self.page.source_image_paths = list(paths)
            self.page._initialize_figure_number_mappings(paths)
            self.page.all_results = [
                {
                    "image_name": f"Pic_{number:02d}",
                    "source_image_name": Path(path).name,
                    "image_path": path,
                    "original_image_path": path,
                    "detections": [{
                        "label": str(number),
                        "original_label": str(number),
                        "confidence": 0.95,
                    }],
                }
                for number, path in enumerate(paths, start=1)
            ]
            self.page.populate_review_table()
            transfer = DocumentSymbolTransfer(
                source_path="sample.docx",
                file_name="sample.docx",
                source_sha256="c" * 64,
                patent_title="測試裝置",
                representative_figure_number="2",
                full_entries=[
                    PatentSymbolEntry("1", "底座", "1", "full", 1, "p[1]", 0, 1),
                    PatentSymbolEntry("2", "上蓋", "2", "full", 2, "p[2]", 0, 1),
                ],
                representative_entries=[
                    PatentSymbolEntry(
                        "2", "上蓋", "2", "representative", 4, "p[4]", 0, 1
                    ),
                ],
            )
            self.page.load_document_symbol_transfer(transfer)

            self.page.reference_source_button.setChecked(True)

            self.assertEqual(self.page.current_preview_index, 1)
            self.assertTrue(self.page._result_view_current_only)
            self.assertTrue(self.page.result_view_button.isChecked())
            self.assertEqual(self.page.result_view_button.text(), "顯示完整結果")
            self.assertIn("圖2", self.page.result_title.text())
            result_text = self.page.result_text.toPlainText()
            self.assertIn("Pic_02", result_text)
            self.assertNotIn("Pic_01", result_text)
            self.assertNotIn("Pic_03", result_text)
            self.assertEqual(
                self.page.reference_items,
                [{"number": "2", "name": "上蓋"}],
            )

    def test_representative_switch_without_matching_page_keeps_current_image(self):
        self.page.image_paths = ["figure_1.png", "figure_2.png"]
        self.page.source_image_paths = list(self.page.image_paths)
        self.page._initialize_figure_number_mappings(self.page.image_paths)
        self.page.current_preview_index = 1
        transfer = DocumentSymbolTransfer(
            source_path="sample.docx",
            file_name="sample.docx",
            source_sha256="c" * 64,
            patent_title="測試裝置",
            representative_figure_number="9",
            full_entries=[
                PatentSymbolEntry("1", "底座", "1", "full", 1, "p[1]", 0, 1),
            ],
            representative_entries=[
                PatentSymbolEntry(
                    "9", "上蓋", "9", "representative", 4, "p[4]", 0, 1
                ),
            ],
        )
        self.page.load_document_symbol_transfer(transfer)

        self.page.reference_source_button.setChecked(True)

        self.assertEqual(self.page.current_preview_index, 1)
        self.assertFalse(self.page._result_view_current_only)
        self.assertFalse(self.page.result_view_button.isChecked())

    def test_representative_jump_reads_figure_numbers_from_recognition_results(self):
        self.page.image_paths = ["first.png", "second.png"]
        self.page.source_image_paths = list(self.page.image_paths)
        self.page.figure_number_mappings = {}
        self.page.all_results = [
            {
                "image_name": "Pic_01",
                "image_path": "first.png",
                "figure_numbers": [4],
                "detections": [],
            },
            {
                "image_name": "Pic_02",
                "image_path": "second.png",
                "figure_numbers": ["7A"],
                "detections": [],
            },
        ]
        self.page.populate_review_table()
        transfer = DocumentSymbolTransfer(
            source_path="sample.docx",
            file_name="sample.docx",
            source_sha256="c" * 64,
            patent_title="測試裝置",
            representative_figure_number="7A",
            full_entries=[
                PatentSymbolEntry("1", "底座", "1", "full", 1, "p[1]", 0, 1),
            ],
            representative_entries=[
                PatentSymbolEntry(
                    "7", "上蓋", "7", "representative", 4, "p[4]", 0, 1
                ),
            ],
        )
        self.page.load_document_symbol_transfer(transfer)

        self.page.reference_source_button.setChecked(True)

        self.assertEqual(self.page.current_preview_index, 1)
        self.assertTrue(self.page._result_view_current_only)

    def test_file_picker_shares_title_row_and_panels_are_three_to_two(self):
        self.page.resize(1100, 700)
        self.page.show()
        self.app.processEvents()
        title = next(
            label
            for label in self.page.findChildren(QLabel)
            if label.text() == "圖片標號識別"
        )

        self.assertLessEqual(abs(title.geometry().center().y() - self.page.image_line.geometry().center().y()), 2)
        table_height = self.page.review_table.height()
        result_height = self.page.result_text.height()
        self.assertGreater(table_height, result_height)
        self.assertGreater(table_height / result_height, 1.35)
        self.assertLess(table_height / result_height, 1.75)

    def test_search_navigation_arrows_are_high_contrast_icon_buttons(self):
        self.assertTrue(self.page.review_search_previous_button.text() == "")
        self.assertTrue(self.page.review_search_next_button.text() == "")
        self.assertFalse(self.page.review_search_previous_button.icon().isNull())
        self.assertFalse(self.page.review_search_next_button.icon().isNull())
        self.assertEqual(self.page.review_search_previous_button.height(), 30)
        self.assertEqual(self.page.review_search_next_button.height(), 30)
        self.assertEqual(
            self.page.review_search_previous_button.iconSize(),
            QSize(16, 16),
        )
        self.assertEqual(
            self.page.review_search_previous_button.objectName(),
            "SearchArrowButton",
        )
        self.assertEqual(
            self.page.review_search_next_button.objectName(),
            "SearchArrowButton",
        )
        for button in (
            self.page.review_search_previous_button,
            self.page.review_search_next_button,
        ):
            self.assertFalse(
                button.icon().pixmap(
                    QSize(16, 16),
                    QIcon.Mode.Disabled,
                ).isNull()
            )
            self.assertFalse(
                button.icon().pixmap(
                    QSize(16, 16),
                    QIcon.Mode.Normal,
                ).isNull()
            )

    def test_unready_document_transfer_clears_hidden_reference_state(self):
        ready_transfer = DocumentSymbolTransfer(
            source_path="ready.docx",
            file_name="ready.docx",
            source_sha256="d" * 64,
            patent_title="測試裝置",
            full_entries=[
                PatentSymbolEntry("1", "底座", "1", "full", 1, "p[1]", 0, 1),
            ],
        )
        self.page.load_document_symbol_transfer(ready_transfer)
        self.assertTrue(self.page.reference_text.toPlainText())
        self.assertTrue(self.page.compare_button.isEnabled())

        blocked_transfer = DocumentSymbolTransfer(
            source_path="blocked.docx",
            file_name="blocked.docx",
            source_sha256="e" * 64,
            patent_title="另一案件",
            full_entries=[
                PatentSymbolEntry("9", "外殼", "9", "full", 1, "p[1]", 0, 1),
            ],
            source_review_issue_ids={FULL_SYMBOL_SOURCE: ["issue-1"]},
        )
        self.page.load_document_symbol_transfer(blocked_transfer)

        self.assertEqual(self.page.reference_text.toPlainText(), "")
        self.assertEqual(self.page.reference_items, [])
        self.assertFalse(self.page.compare_button.isEnabled())

    def test_first_recognition_step_uses_emphasized_red_button_style(self):
        self.assertEqual(
            self.page.auto_rotate_button.objectName(),
            "RecognitionStartButton",
        )

    def test_three_step_actions_follow_manual_rotation_and_hide_save_status(self):
        layout = self.page.preview_action_layout
        self.assertEqual(self.page.auto_rotate_button.text(), "第一步.自動旋轉")
        self.assertEqual(self.page.run_button.text(), "第二步.圖片標號辨識")
        self.assertEqual(self.page.compare_button.text(), "第三步.辨識結果與標號清單比對")
        self.assertLess(layout.indexOf(self.page.rotate_all_right_button), layout.indexOf(self.page.auto_rotate_button))
        self.assertLess(layout.indexOf(self.page.auto_rotate_button), layout.indexOf(self.page.run_button))
        self.assertLess(layout.indexOf(self.page.run_button), layout.indexOf(self.page.compare_button))
        self.assertEqual(self.page.layout().indexOf(self.page.result_save_status), -1)
        self.assertTrue(self.page.result_save_status.isHidden())
        labels = [widget.text() for widget in self.page.findChildren(QLabel)]
        self.assertNotIn("圖片預覽：", labels)

    def test_auto_orientation_commits_all_paths_before_number_recognition(self):
        self.page.source_image_paths = ["original.png"]
        self.page.image_paths = ["original.png"]
        pages = [{
            "original_image_path": "original.png",
            "image_path": "oriented.png",
            "rotation_degrees": 90,
            "figure_heading": {"status": "ready", "orientation": {"status": "needs_rotation"}},
        }]
        with patch.object(self.page, "show_original_image"):
            self.page._on_auto_orientation_ready(pages)
        self.assertEqual(self.page.source_image_paths, ["oriented.png"])
        self.assertEqual(self.page.all_results, [])
        self.assertTrue(self.page._heading_stage_enabled)
        self.assertEqual(self.page._orientation_stage_pages[0]["rotation_degrees"], 90)
        self.assertIn("第二步.圖片標號辨識", self.page.result_text.toPlainText())

    def test_repeating_first_step_on_upright_result_preserves_review(self):
        self.page.source_image_paths = ["oriented.png"]
        self.page.image_paths = ["oriented.png"]
        self.page._orientation_stage_pages = [{
            "original_image_path": "original.png", "image_path": "oriented.png",
            "rotation_degrees": 90,
            "figure_heading": {"orientation": {"status": "needs_rotation", "correction_degrees": 90}},
        }]
        original_results = self.page.all_results
        self.page._on_auto_orientation_ready([{
            "original_image_path": "oriented.png", "image_path": "oriented.png",
            "rotation_degrees": 0,
            "figure_heading": {"orientation": {"status": "upright", "correction_degrees": 0}},
        }])
        self.assertIs(self.page.all_results, original_results)
        self.assertEqual(self.page._orientation_stage_pages[0]["original_image_path"], "original.png")
        self.assertEqual(self.page._orientation_stage_pages[0]["rotation_degrees"], 90)

    def test_second_step_result_keeps_first_step_rotation_history(self):
        self.page.source_image_paths = ["oriented.png"]
        self.page.image_paths = ["oriented.png"]
        self.page._orientation_stage_pages = [{
            "original_image_path": "original.png", "image_path": "oriented.png",
            "rotation_degrees": 90,
            "figure_heading": {"orientation": {"status": "needs_rotation", "correction_degrees": 90}},
        }]
        result = {
            "image_path": "oriented.png", "original_image_path": "oriented.png",
            "rotation_degrees": 0, "numbers": [], "detections": [],
            "figure_heading": {"status": "ready", "mapping": {"status": "rejected"}},
            "orientation": {"status": "upright", "correction_degrees": 0},
        }
        with patch("ui.recognition_page.QMessageBox.information"), patch.object(
            self.page, "show_original_image"
        ), patch.object(self.page, "_publish_reviewed_ocr_results"):
            self.page.on_batch_finished([result])
        self.assertEqual(result["original_image_path"], "original.png")
        self.assertEqual(result["rotation_degrees"], 90)
        self.assertEqual(result["orientation"]["status"], "needs_rotation")

    def test_second_step_reads_corrected_paths_without_rotating_again(self):
        self.page.source_image_paths = ["oriented.png"]
        self.page.image_paths = ["oriented.png"]
        self.page._heading_stage_enabled = True
        with patch("ui.recognition_page.BatchRecognitionWorker") as worker_class, patch.object(
            self.page, "apply_current_figure_mapping", return_value=True
        ):
            self.page.run_batch_recognition()
        self.assertEqual(worker_class.call_args.kwargs["image_paths"], ["oriented.png"])
        self.assertFalse(worker_class.call_args.kwargs["auto_orient"])

    def test_failed_prior_save_cancels_first_step_without_discarding_edits(self):
        self.page.source_image_paths = ["original.png"]
        self.page.image_paths = ["original.png"]
        self.page.review_table.item(0, LABEL_COLUMN).setText("88")
        failed_save = Future()
        failed_save.set_result("disk full")
        with (
            patch("ui.recognition_page.resolve_figure_heading_model_path", return_value=Path("heading.onnx")),
            patch("ui.recognition_page.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
            patch("ui.recognition_page.QMessageBox.critical"),
            patch.object(self.page, "_queue_result_save", return_value=failed_save),
            patch("ui.recognition_page.auto_orient_figure_images") as orient,
        ):
            self.assertTrue(self.page.auto_orient_all_images())
            deadline = time.monotonic() + 2
            while self.page._orientation_tasks.busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.002)
            self.app.processEvents()
            orient.assert_not_called()
        self.assertIn("88", self.page.collect_reviewed_results()[0]["numbers"])
        self.assertEqual(self.page.source_image_paths, ["original.png"])

    def test_requested_action_button_colors_and_removed_export_button(self):
        for button in (
            self.page.prev_button,
            self.page.next_button,
            self.page.rotate_button,
            self.page.rotate_left_button,
            self.page.rotate_all_right_button,
        ):
            self.assertEqual(button.objectName(), "GreenActionButton")
        for button in (
            self.page.add_label_button,
            self.page.delete_label_button,
        ):
            self.assertEqual(button.objectName(), "OrangeActionButton")
        for button in (
            self.page.reference_source_button,
            self.page.result_view_button,
        ):
            self.assertEqual(button.objectName(), "LightBlueActionButton")
        self.assertFalse(hasattr(self.page, "export_review_button"))

    def test_preview_navigation_sits_between_figure_mapping_and_rotation(self):
        layout = self.page.preview_action_layout
        mapping_index = layout.indexOf(self.page.figure_mapping_line)
        previous_index = layout.indexOf(self.page.prev_button)
        next_index = layout.indexOf(self.page.next_button)
        rotate_index = layout.indexOf(self.page.rotate_button)

        self.assertLess(mapping_index, previous_index)
        self.assertLess(previous_index, next_index)
        self.assertLess(next_index, rotate_index)
        self.assertEqual(self.page.rotate_left_button.text(), "左旋90°")
        self.assertEqual(self.page.rotate_all_right_button.text(), "全部右旋90°")

    def test_clicking_detection_box_selects_matching_row_without_reordering(self):
        pixmap = QPixmap(100, 100)
        pixmap.fill(QColor("white"))
        self.page.current_pixmap = pixmap
        self.page.current_preview_index = 0
        self.page.image_preview.resize(100, 100)

        handled = self.page.on_preview_detection_clicked(QPoint(50, 20))

        self.assertTrue(handled)
        self.assertEqual(
            self.page.review_table.item(0, LABEL_COLUMN).text(),
            "B",
        )
        self.assertEqual(
            self.page.review_table.item(1, LABEL_COLUMN).text(),
            "2",
        )
        self.assertTrue(
            self.page.review_table.item(1, LABEL_COLUMN).isSelected()
        )
        self.assertEqual(self.page.review_table.currentRow(), 1)
        self.assertEqual(
            [index.row() for index in self.page.review_table.selectionModel().selectedRows()],
            [1],
        )
        self.assertEqual(
            self.page.compare_button.objectName(),
            "RecognitionStartButton",
        )

    def test_clicking_scaled_detection_box_selects_matching_row(self):
        pixmap = QPixmap(100, 100)
        pixmap.fill(QColor("white"))
        self.page.current_pixmap = pixmap
        self.page.current_preview_index = 0
        self.page.image_preview.resize(200, 200)

        handled = self.page.on_preview_detection_clicked(QPoint(100, 40))

        self.assertTrue(handled)
        self.assertEqual(self.page.review_table.currentRow(), 1)
        self.assertEqual(
            [index.row() for index in self.page.review_table.selectionModel().selectedRows()],
            [1],
        )
        self.assertEqual(
            [
                self.page.review_table.item(row, LABEL_COLUMN).text()
                for row in range(self.page.review_table.rowCount())
            ],
            ["B", "2"],
        )

    def test_review_search_navigates_exact_matches_and_wraps(self):
        self.page.all_results.append({
            "image_name": "Pic_02",
            "source_image_name": "second.png",
            "image_path": "second.png",
            "original_image_path": "second.png",
            "rotation_degrees": 0,
            "numbers": ["B", "B'"],
            "detections": [
                {"label": "B", "original_label": "B", "confidence": 0.91},
                {"label": "B'", "original_label": "B'", "confidence": 0.92},
            ],
        })
        self.page.populate_review_table()
        matches = self.page._matching_review_rows("B")
        self.assertEqual(len(matches), 2)

        self.page.review_table.setCurrentCell(-1, -1)
        self.page.review_search_line.setText("B")
        self.assertTrue(self.page.review_search_next_button.isEnabled())
        self.assertTrue(self.page.navigate_review_search(1))
        self.assertEqual(self.page.review_table.currentRow(), matches[0])
        self.assertTrue(self.page.navigate_review_search(1))
        self.assertEqual(self.page.review_table.currentRow(), matches[1])
        self.assertTrue(self.page.navigate_review_search(1))
        self.assertEqual(self.page.review_table.currentRow(), matches[0])

        self.page.review_search_line.setText("B'")
        self.assertEqual(len(self.page._matching_review_rows("B'")), 1)
        self.page.review_search_line.setText("b")
        self.assertFalse(self.page.review_search_next_button.isEnabled())

    def test_clicking_same_review_row_recenters_zoomed_preview(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "focus.png"
            pixmap = QPixmap(1000, 1000)
            pixmap.fill(QColor("white"))
            self.assertTrue(pixmap.save(str(image_path)))
            self.page.image_paths = [str(image_path)]
            self.page.source_image_paths = [str(image_path)]
            self.page.all_results[0]["detections"][1].update({
                "x1": 780,
                "y1": 780,
                "x2": 840,
                "y2": 840,
            })
            self.page.populate_review_table()
            target_row = next(
                row
                for row in range(self.page.review_table.rowCount())
                if self.page.review_table.item(row, LABEL_COLUMN).text() == "2"
            )
            self.page.resize(1100, 760)
            self.page.show()
            self.app.processEvents()
            self.page.show_original_image(0)
            self.page.preview_zoom_factor = 3.0
            self.page.update_scaled_preview()
            self.app.processEvents()
            self.page.review_table.selectRow(target_row)
            self.app.processEvents()

            horizontal = self.page.scroll_area.horizontalScrollBar()
            vertical = self.page.scroll_area.verticalScrollBar()
            self.assertGreater(horizontal.value(), 0)
            self.assertGreater(vertical.value(), 0)

            horizontal.setValue(0)
            vertical.setValue(0)
            self.page.on_review_cell_clicked(target_row, LABEL_COLUMN)
            self.app.processEvents()
            self.assertGreater(horizontal.value(), 0)
            self.assertGreater(vertical.value(), 0)

    def test_all_detection_boxes_are_enabled_by_default(self):
        self.assertTrue(self.page._show_all_boxes)
        self.assertFalse(hasattr(self.page, "show_all_boxes_button"))

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
            "顯示完整結果",
        )

    def test_results_keep_left_to_right_character_order_without_toggle_button(self):
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
        self.assertFalse(hasattr(self.page, "result_sort_button"))

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

    def test_manual_left_rotation_replaces_current_source(self):
        self.page.image_paths = ["first.png", "second.png"]
        self.page.source_image_paths = ["first.png", "second.png"]
        self.page.current_preview_index = 1

        with patch(
            "ui.recognition_page.rotate_image_counterclockwise_90",
            return_value="second_left.png",
        ) as rotate:
            self.page.rotate_current_image_counterclockwise()

        rotate.assert_called_once_with("second.png")
        self.assertEqual(
            self.page.source_image_paths,
            ["first.png", "second_left.png"],
        )

    def test_rotate_all_images_clockwise_replaces_every_source(self):
        self.page.image_paths = ["first.png", "second.png"]
        self.page.source_image_paths = ["first.png", "second.png"]

        with patch(
            "ui.recognition_page.rotate_image_clockwise_90",
            side_effect=["first_right.png", "second_right.png"],
        ) as rotate:
            self.assertTrue(self.page.rotate_all_images_clockwise())
            deadline = time.monotonic() + 2
            while self.page._rotation_tasks.busy and time.monotonic() < deadline:
                QTest.qWait(5)

        self.assertFalse(self.page._rotation_tasks.busy)
        self.assertEqual(
            [call.args[0] for call in rotate.call_args_list],
            ["first.png", "second.png"],
        )
        self.assertEqual(
            self.page.source_image_paths,
            ["first_right.png", "second_right.png"],
        )
        self.assertIn("全部 2 張圖片", self.page.result_text.toPlainText())

    def test_second_stage_uses_all_pictures_and_pic_name(self):
        self.page.reference_text.setPlainText("1:part one\n2:part two")

        self.page.compare_with_reference_list()

        plain_text = self.page.result_text.toPlainText()
        self.assertTrue(plain_text.startswith("All Pictures"))
        self.assertIn("Pic_01", plain_text)
        self.assertIn("標號清單有，但全部圖片都沒有出現：1", plain_text)

    def test_default_draws_all_boxes_and_selection_highlights_blue(self):
        self.page.review_table.clearSelection()
        self.page._show_all_boxes = False
        pixmap = QPixmap(100, 100)
        pixmap.fill(QColor("#ffffff"))

        low_only = self.page.draw_low_confidence_boxes(pixmap.copy(), 0)
        self.assertEqual(low_only.toImage().pixelColor(10, 10).name(), "#dc2626")
        self.assertEqual(low_only.toImage().pixelColor(40, 10).name(), "#ffffff")

        self.page._show_all_boxes = True
        self.page.review_table.selectRow(1)
        highlighted = self.page.draw_low_confidence_boxes(pixmap.copy(), 0)

        self.assertEqual(highlighted.toImage().pixelColor(40, 10).name(), "#2563eb")

    def test_preview_zoom_rerenders_from_full_resolution_pixmap(self):
        source = QPixmap(1200, 900)
        source.fill(QColor("#ffffff"))
        self.page.current_pixmap = source
        self.page.current_preview_index = 0
        self.page.resize(1100, 760)
        self.page.show()
        self.app.processEvents()
        self.page.update_scaled_preview()

        initial_width = self.page.image_preview.pixmap().width()
        viewport_center = self.page.scroll_area.viewport().rect().center()
        self.page.zoom_preview_at(1, viewport_center)

        zoomed_pixmap = self.page.image_preview.pixmap()
        self.assertGreater(self.page.preview_zoom_factor, 1.0)
        self.assertGreater(zoomed_pixmap.width(), initial_width)
        self.assertEqual(self.page.image_preview.size(), zoomed_pixmap.size())
        self.assertIn("%", self.page.preview_zoom_status.text())

    def test_preview_zoom_buttons_zoom_and_reset_to_fit(self):
        source = QPixmap(1200, 900)
        source.fill(QColor("#ffffff"))
        self.page.current_pixmap = source
        self.page.resize(1100, 760)
        self.page.show()
        self.app.processEvents()
        self.page._set_preview_zoom_controls_enabled(True)
        self.page.update_scaled_preview()

        self.assertEqual(self.page.preview_zoom_out_button.text(), "－")
        self.assertEqual(self.page.preview_zoom_reset_button.text(), "適合視窗")
        self.assertEqual(self.page.preview_zoom_in_button.text(), "＋")
        self.page.preview_zoom_in_button.click()
        self.assertGreater(self.page.preview_zoom_factor, 1.0)
        self.assertIn("%", self.page.preview_zoom_status.text())

        self.page.preview_zoom_reset_button.click()
        self.assertEqual(self.page.preview_zoom_factor, 1.0)
        self.assertEqual(self.page.preview_zoom_status.text(), "滾輪縮放：適合視窗")
        self.assertEqual(self.page.scroll_area.horizontalScrollBar().value(), 0)
        self.assertEqual(self.page.scroll_area.verticalScrollBar().value(), 0)

    def test_scaled_preview_keeps_detection_box_coordinates_aligned(self):
        pixmap = QPixmap(200, 200)
        pixmap.fill(QColor("#ffffff"))

        scaled_boxes = self.page.draw_low_confidence_boxes(
            pixmap,
            0,
            coordinate_scale_x=2.0,
            coordinate_scale_y=2.0,
        )

        self.assertEqual(
            scaled_boxes.toImage().pixelColor(QPoint(20, 20)).name(),
            "#dc2626",
        )

    def test_reviewed_ocr_labels_are_published_after_manual_edit(self):
        context = PatentWorkflowContext()
        self.page.set_workflow_context(context)
        self.page._publish_reviewed_ocr_results()
        self.assertEqual(context.ocr_results[0]["numbers"], ["B", "2"])

        label_item = self.page.review_table.item(0, LABEL_COLUMN)
        label_item.setText("8")
        self.app.processEvents()

        self.assertEqual(context.ocr_results[0]["numbers"], ["8", "2"])

    def test_current_page_figure_mapping_accepts_multi_figure_ranges(self):
        context = PatentWorkflowContext()
        self.page.set_workflow_context(context)
        self.page.image_paths = ["page_001.png"]
        self.page.source_image_paths = ["page_001.png"]
        self.page._initialize_figure_number_mappings(self.page.image_paths)
        self.page.figure_mapping_line.setText("1-2")

        self.assertTrue(self.page.apply_current_figure_mapping())

        self.assertEqual(self.page.figure_number_mappings[0], [1, 2])
        self.assertEqual(self.page.all_results[0]["figure_numbers"], [1, 2])
        self.assertNotIn("figure_number", self.page.all_results[0])
        self.assertEqual(context.ocr_results[0]["figure_numbers"], [1, 2])

    def test_explicit_figure_name_overrides_default_page_number(self):
        self.page.image_paths = ["專利圖6.png", "page_002.png"]

        self.page._initialize_figure_number_mappings(self.page.image_paths)

        self.assertEqual(self.page.figure_number_mappings, {0: [6], 1: [2]})

    def test_high_confidence_caption_mapping_replaces_only_fallback(self):
        self.page.image_paths = ["page_001.png"]
        self.page.source_image_paths = list(self.page.image_paths)
        self.page.all_results = []
        self.page._initialize_figure_number_mappings(self.page.image_paths)
        result = {
            "auto_figure_numbers": ["3A", "3B"],
            "figure_heading": {
                "mapping": {
                    "status": "accepted",
                    "confidence": 0.91,
                    "complete": True,
                }
            },
        }

        self.assertTrue(self.page._adopt_automatic_figure_mapping(result, 0))
        self.page._set_result_figure_mapping(result, 0)

        self.assertEqual(self.page.figure_number_mappings[0], ["3A", "3B"])
        self.assertEqual(self.page.figure_mapping_sources[0], "auto")
        self.assertEqual(result["figure_numbers"], ["3A", "3B"])
        self.assertEqual(result["figure_number_source"], "auto")

    def test_caption_mapping_never_overwrites_manual_page_mapping(self):
        self.page.image_paths = ["page_001.png"]
        self.page.source_image_paths = list(self.page.image_paths)
        self.page.all_results = []
        self.page._initialize_figure_number_mappings(self.page.image_paths)
        self.page._mark_figure_mapping_as_manually_edited("7'")
        self.page.figure_mapping_line.setText("7'")
        self.assertTrue(self.page.apply_current_figure_mapping())
        result = {
            "auto_figure_numbers": [2],
            "figure_heading": {
                "mapping": {
                    "status": "accepted",
                    "confidence": 0.95,
                    "complete": True,
                }
            },
        }

        self.assertFalse(self.page._adopt_automatic_figure_mapping(result, 0))
        self.page._set_result_figure_mapping(result, 0)

        self.assertEqual(self.page.figure_number_mappings[0], ["7'"])
        self.assertEqual(self.page.figure_mapping_sources[0], "manual")
        self.assertEqual(result["figure_numbers"], ["7'"])
        self.assertEqual(result["figure_number_source"], "manual")


if __name__ == "__main__":
    unittest.main()
