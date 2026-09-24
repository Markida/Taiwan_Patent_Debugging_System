import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from app.workflow_context import PatentWorkflowContext
from features.patent_ocr.result_store import FigureResultStore
from ui.recognition_page import RecognitionPage, LABEL_COLUMN


class FigureResultPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = FigureResultStore(self.root / "local")
        self.pdf = self.root / "圖式.pdf"
        self.pdf.write_bytes(b"source PDF version 1")
        self.image = self.root / "page.png"
        image = QImage(100, 140, QImage.Format_RGB32)
        image.fill(QColor("white"))
        self.assertTrue(image.save(str(self.image)))
        self.pages = []
        self.dialogs = patch.object(QMessageBox, "information")
        self.dialogs.start()

    def tearDown(self):
        for page in self.pages:
            page.shutdown()
            page._result_io.shutdown(wait=True)
            page.deleteLater()
        self.app.processEvents()
        self.dialogs.stop()
        self.temporary.cleanup()

    def new_page(self):
        page = RecognitionPage(lambda: None, result_store=self.store)
        # Recognition workers are mocked here; do not depend on private weights.
        page.model_line.setText(str(self.root / "mock-locator.onnx"))
        self.pages.append(page)
        return page

    def wait(self, condition):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            time.sleep(0.005)
        self.fail("Background persistence did not complete")

    def finish_save(self, page):
        future = page._queue_result_save()
        self.assertIsNotNone(future)
        self.assertEqual(future.result(timeout=5), "")
        self.app.processEvents()

    def recognized_page(self):
        page = self.new_page()
        with patch("ui.recognition_page.convert_pdf_to_images", return_value=[str(self.image)]), patch(
            "ui.recognition_page.get_output_base_dir", return_value=self.root / "output"
        ):
            self.assertTrue(page.import_pdf(str(self.pdf)))
            self.wait(lambda: not page._pdf_tasks.busy)
        page.on_batch_finished([{
            "image_path": str(self.image), "original_image_path": str(self.image),
            "rotation_degrees": 90, "orientation": {"status": "needs_rotation", "correction_degrees": 90},
            "figure_heading": {"status": "ready", "mapping": {"status": "accepted"}},
            "auto_figure_numbers": ["3A"], "detected_figure_numbers": ["3A"],
            "numbers": ["B", "2"],
            "detections": [
                {"label": "B", "confidence": 0.52, "x1": 10, "y1": 10, "x2": 30, "y2": 30},
                {"label": "2", "confidence": 0.94, "x1": 40, "y1": 10, "x2": 60, "y2": 30},
            ],
        }])
        return page

    def test_pet_comparison_advice_tracks_edits_restore_and_view_refresh(self):
        from features.workflow_pet.guidance import guidance_for
        page = self.recognized_page()
        page.reference_text.setPlainText("2:基座\nB:外殼")
        self.assertEqual(guidance_for("patent_ocr", page).key, "ocr-compare-ready")
        page.compare_with_reference_list()
        self.assertEqual(guidance_for("patent_ocr", page).key, "ocr-compared")
        state = page._capture_result_state()
        page.reference_text.setPlainText("3:基座")
        self.assertEqual(guidance_for("patent_ocr", page).key, "ocr-compare-ready")
        page.refresh_result_display()  # Changing the view must not approve an edited list.
        self.assertEqual(guidance_for("patent_ocr", page).key, "ocr-compare-ready")
        page._restore_result_state(state)
        self.assertEqual(guidance_for("patent_ocr", page).key, "ocr-compared")
        page.review_table.item(0, LABEL_COLUMN).setText("9")
        self.assertEqual(guidance_for("patent_ocr", page).key, "ocr-compare-ready")
        page.reset_review_state()
        self.assertIsNone(page._guidance_comparison_snapshot)

    def test_same_name_after_restart_restores_edits_deletions_and_copied_images(self):
        page = self.recognized_page()
        page.review_table.item(0, LABEL_COLUMN).setText("8")
        page.review_table.selectRow(1)
        page.delete_selected_labels()
        row = page.insert_review_row(0, {"label": "A'", "manual_added": True}, manual_added=True)
        page.review_table.item(row, LABEL_COLUMN).setText("B'")
        page.figure_mapping_line.setText("4A,5'")
        page._mark_figure_mapping_as_manually_edited("4A,5'")
        page.apply_current_figure_mapping()
        self.finish_save(page)
        before = page.collect_reviewed_results()[0]
        page.shutdown()
        page._result_io.shutdown(wait=True)
        self.image.unlink()

        moved = self.root / "moved" / self.pdf.name
        moved.parent.mkdir()
        moved.write_bytes(self.pdf.read_bytes())
        restored = self.new_page()
        context = PatentWorkflowContext()
        restored.set_workflow_context(context)
        with patch("ui.recognition_page.convert_pdf_to_images") as convert:
            restored.import_pdf(str(moved))
            self.wait(lambda: not restored._pdf_tasks.busy)
            convert.assert_not_called()
        result = restored.collect_reviewed_results()[0]
        self.assertEqual(result["detections"], before["detections"])
        self.assertEqual(result["review_deleted"], before["review_deleted"])
        self.assertEqual(result["figure_numbers"], ["4A", "5'"])
        self.assertEqual(result["figure_number_source"], "manual")
        self.assertEqual(result["rotation_degrees"], 90)
        self.assertTrue(Path(result["image_path"]).is_file())
        self.assertEqual(context.ocr_results[0]["numbers"], result["numbers"])
        restored.apply_reference_reconciliation([{"number": "B"}])
        self.assertIn("8", restored.collect_reviewed_results()[0]["numbers"])

    def test_immediate_reimport_waits_for_last_manual_edit(self):
        page = self.recognized_page()
        page.review_table.item(0, LABEL_COLUMN).setText("99")
        page.import_pdf(str(self.pdf))
        self.wait(lambda: not page._pdf_tasks.busy)
        self.assertIn("99", page.collect_reviewed_results()[0]["numbers"])

    def test_shutdown_commits_active_cell_editor(self):
        page = self.recognized_page()
        page.show()
        page.review_table.setCurrentCell(0, LABEL_COLUMN)
        page.review_table.editItem(page.review_table.item(0, LABEL_COLUMN))
        self.app.processEvents()
        editor = QApplication.focusWidget()
        self.assertIsInstance(editor, QLineEdit)
        editor.setText("123B")
        page.shutdown()
        page._result_io.shutdown(wait=True)
        saved = self.store.load(page._result_identity)
        self.assertIn("123B", saved["reviewed_results"][0]["numbers"])

    def test_finished_signal_saves_even_before_worker_thread_returns(self):
        page = self.recognized_page()
        self.finish_save(page)
        page.worker = Mock()
        page.worker.isRunning.return_value = True
        page._ocr_job_active = False
        page.review_table.item(0, LABEL_COLUMN).setText("456")
        self.assertEqual(page._result_save_future.result(timeout=5), "")
        saved = self.store.load(page._result_identity)
        self.assertIn("456", saved["reviewed_results"][0]["numbers"])
        page.worker.isRunning.return_value = False

    def test_same_name_changed_content_loads_old_results_then_reruns_current_pdf(self):
        page = self.recognized_page()
        self.finish_save(page)
        self.pdf.write_bytes(b"source PDF version 2")
        restored = self.new_page()
        with patch("ui.recognition_page.convert_pdf_to_images") as convert:
            restored.import_pdf(str(self.pdf))
            self.wait(lambda: not restored._pdf_tasks.busy)
            convert.assert_not_called()
        self.assertTrue(restored._result_source_changed)
        self.assertIn("內容已更新", restored.result_save_status.text())
        worker = Mock()
        worker.isRunning.return_value = False
        with patch("ui.recognition_page.convert_pdf_to_images", return_value=[str(self.image)]) as convert, patch(
            "ui.recognition_page.BatchRecognitionWorker", return_value=worker
        ), patch("ui.recognition_page.get_output_base_dir", return_value=self.root / "output"):
            restored.run_batch_recognition()
            self.wait(lambda: not restored._pdf_tasks.busy)
            convert.assert_called_once()
            worker.start.assert_called_once()
        self.assertFalse(restored._result_source_changed)
        restored._ocr_job_active = False

    def test_cancelled_rerun_keeps_manual_table_and_saved_result(self):
        page = self.recognized_page()
        page.review_table.item(0, LABEL_COLUMN).setText("88")
        self.finish_save(page)
        worker = Mock()
        worker.isRunning.return_value = False
        with patch("ui.recognition_page.BatchRecognitionWorker", return_value=worker):
            page.run_batch_recognition()
        self.assertEqual(page.review_table.rowCount(), 0)
        page.on_recognition_cancelled()
        self.assertIn("88", page.collect_reviewed_results()[0]["numbers"])

    def test_corrupt_cache_falls_back_to_import_with_visible_notice(self):
        page = self.new_page()
        with patch.object(self.store, "load", side_effect=ValueError("broken JSON")), patch(
            "ui.recognition_page.convert_pdf_to_images", return_value=[str(self.image)]
        ) as convert, patch("ui.recognition_page.get_output_base_dir", return_value=self.root / "output"):
            page.import_pdf(str(self.pdf))
            self.wait(lambda: not page._pdf_tasks.busy)
        convert.assert_called_once()
        self.assertIn("無法還原", page.result_save_status.text())
        self.assertEqual(page.all_results, [])

    def test_background_save_error_is_visible_and_preserves_current_table(self):
        page = self.recognized_page()
        self.finish_save(page)
        with patch.object(self.store, "save", side_effect=OSError("disk full")):
            page.review_table.item(0, LABEL_COLUMN).setText("77")
            future = page._result_save_future
            self.assertIn("disk full", future.result(timeout=5))
            self.wait(lambda: "尚未保存" in page.result_save_status.text())
        self.assertIn("77", page.collect_reviewed_results()[0]["numbers"])

    def test_multiple_image_import_restores_saved_result(self):
        page = self.new_page()
        page.import_images([str(self.image)])
        self.wait(lambda: not page._pdf_tasks.busy)
        page.on_batch_finished([{"image_path": str(self.image), "original_image_path": str(self.image),
                                 "detections": [{"label": "12", "confidence": 0.9}], "numbers": ["12"]}])
        self.finish_save(page)
        restored = self.new_page()
        restored.import_images([str(self.image)])
        self.wait(lambda: not restored._pdf_tasks.busy)
        self.assertEqual(restored.collect_reviewed_results()[0]["numbers"], ["12"])

    def test_first_step_rotation_history_survives_local_restore(self):
        page = self.recognized_page()
        page._heading_stage_enabled = True
        page._orientation_stage_pages = [{
            "original_image_path": str(self.image),
            "image_path": str(self.image),
            "rotation_degrees": 90,
            "figure_heading": {"orientation": {"status": "needs_rotation", "correction_degrees": 90}},
        }]
        self.finish_save(page)
        restored = self.new_page()
        restored.import_pdf(str(self.pdf))
        self.wait(lambda: not restored._pdf_tasks.busy)
        self.assertTrue(restored._heading_stage_enabled)
        self.assertEqual(restored._orientation_stage_pages[0]["rotation_degrees"], 90)
        self.assertTrue(Path(restored._orientation_stage_pages[0]["image_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
