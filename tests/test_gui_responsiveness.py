import os
from dataclasses import asdict
import time
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QResizeEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.background_tasks import BackgroundTaskRunner
from features.patent_review.custom_rules import CustomTextRuleStore, DocumentSimilarityWhitelistStore
from features.patent_review.docx_reader import parse_docx
from features.patent_review.rule_engine import review_document
from ui.patent_review_page import PatentReviewPage
from ui.recognition_page import RecognitionPage


class GuiResponsivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate):
        end = time.monotonic() + 5
        while not predicate() and time.monotonic() < end:
            QTest.qWait(5)
        self.assertTrue(predicate())

    def test_runner_is_bounded_cancellable_and_keeps_gui_running(self):
        runner = BackgroundTaskRunner()
        gate = Event()
        received = []
        ticks = []
        runner.succeeded.connect(received.append)
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start()
        try:
            self.assertTrue(runner.start(lambda cancel: gate.wait(2)))
            self.assertFalse(runner.start(lambda cancel: "duplicate"))
            QTest.qWait(40)
            self.assertGreater(len(ticks), 0)
            runner.cancel()
            gate.set()
            self.wait_for(lambda: not runner.busy)
            self.assertEqual(received, [])
            self.assertTrue(runner.start(lambda cancel: "new"))
            self.wait_for(lambda: not runner.busy)
            self.assertEqual(received, ["new"])
        finally:
            timer.stop()
            gate.set()
            runner.shutdown()

    def test_runner_shutdown_does_not_wait_for_io_or_deliver_late_results(self):
        runner = BackgroundTaskRunner()
        gate = Event()
        received = []
        runner.succeeded.connect(received.append)
        runner.start(lambda cancel: gate.wait(2))
        start = time.monotonic()
        runner.shutdown()
        self.assertLess(time.monotonic() - start, 0.1)
        gate.set()
        self.wait_for(lambda: not runner.busy)
        self.assertEqual(received, [])

    def test_document_page_does_not_read_network_rules_during_construction(self):
        store = Mock(spec=CustomTextRuleStore)
        store.path = Path("missing-shared-rules.json")
        page = PatentReviewPage(lambda: None, custom_rule_store=store)
        store.load.assert_not_called()
        page.shutdown()
        page.deleteLater()

    def test_background_document_review_preserves_exact_rule_results(self):
        source = next((Path(__file__).parent / "fixtures").glob("TW_*.docx"))
        with TemporaryDirectory() as directory:
            page = PatentReviewPage(
                lambda: None,
                custom_rule_store=CustomTextRuleStore(Path(directory) / "rules.json"),
                document_whitelist_store=DocumentSimilarityWhitelistStore(Path(directory) / "white.json"),
            )
            expected = review_document(parse_docx(source))
            self.assertTrue(page.load_document(source))
            self.wait_for(lambda: not page._review_tasks.busy)
            self.assertIsNotNone(page.review)
            self.assertEqual([asdict(i) for i in page.review.issues],
                             [asdict(i) for i in expected.issues])
            page.shutdown()
            page.deleteLater()

    def test_cancelled_document_never_replaces_existing_work(self):
        page = PatentReviewPage(lambda: None)
        gate = Event()
        with patch("ui.patent_review_page.parse_docx", side_effect=lambda path: gate.wait(2)):
            self.assertTrue(page.load_document("slow.docx"))
            page.abort_current_workflow()
            gate.set()
            self.wait_for(lambda: not page._review_tasks.busy)
        self.assertIsNone(page.document)
        page.shutdown()
        page.deleteLater()

    def test_cancelled_pdf_keeps_old_images_and_has_isolated_output(self):
        page = RecognitionPage(lambda: None)
        page.image_paths = ["previous.png"]
        page.source_image_paths = ["previous.png"]
        gate = Event()
        roots = []

        def convert(**kwargs):
            roots.append(kwargs["output_root"])
            gate.wait(2)
            return ["new.png"]

        with TemporaryDirectory() as directory, patch(
            "ui.recognition_page.convert_pdf_to_images", side_effect=convert
        ), patch("ui.recognition_page.get_output_base_dir", return_value=Path(directory)):
            source = Path(directory) / "same-name.pdf"
            source.write_bytes(b"PDF source for cancellation test")
            page.import_pdf(str(source))
            self.wait_for(lambda: len(roots) == 1)
            page.abort_current_workflow()
            gate.set()
            self.wait_for(lambda: not page._pdf_tasks.busy)
            gate.clear()
            page.import_pdf(str(source))
            self.wait_for(lambda: len(roots) == 2)
            page.abort_current_workflow()
            gate.set()
            self.wait_for(lambda: not page._pdf_tasks.busy)
        self.assertEqual(page.image_paths, ["previous.png"])
        self.assertEqual(len(roots), 2)
        self.assertNotEqual(roots[0], roots[1])
        page.shutdown()
        page.deleteLater()

    def test_batch_rotation_keeps_gui_running_and_cancel_preserves_sources(self):
        page = RecognitionPage(lambda: None)
        page.image_paths = ["first.png", "second.png"]
        page.source_image_paths = list(page.image_paths)
        entered, release = Event(), Event()
        ticks = []
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: ticks.append(1))

        def rotate(path):
            entered.set()
            release.wait(2)
            return f"rotated-{path}"

        with patch("ui.recognition_page.rotate_image_clockwise_90", side_effect=rotate):
            timer.start()
            try:
                self.assertTrue(page.rotate_all_images_clockwise())
                self.assertTrue(entered.wait(0.5))
                QTest.qWait(40)
                self.assertGreater(len(ticks), 0)
                page.abort_current_workflow()
                release.set()
                self.wait_for(lambda: not page._rotation_tasks.busy)
            finally:
                timer.stop()
                release.set()

        self.assertEqual(page.source_image_paths, ["first.png", "second.png"])
        page.shutdown()
        page.deleteLater()

    def test_repeated_preview_resize_events_are_coalesced(self):
        page = RecognitionPage(lambda: None)
        rendered = []
        page._preview_resize_timer.timeout.disconnect()
        page._preview_resize_timer.timeout.connect(lambda: rendered.append(1))
        event = QResizeEvent(QSize(1000, 700), QSize(900, 600))

        for _ in range(25):
            page.resizeEvent(event)

        self.assertTrue(page._preview_resize_timer.isActive())
        QTest.qWait(80)
        self.assertEqual(rendered, [1])
        page.shutdown()
        page.deleteLater()

    def test_pdf_output_permission_error_is_reported_from_worker(self):
        page = RecognitionPage(lambda: None)
        with patch("ui.recognition_page.get_output_base_dir", side_effect=OSError("read-only")), \
             patch.object(QMessageBox, "critical") as critical:
            self.assertTrue(page.import_pdf("sample.pdf"))
            self.wait_for(lambda: not page._pdf_tasks.busy)
            critical.assert_called_once()
        self.assertTrue(page.run_button.isEnabled())
        page.shutdown()
        page.deleteLater()

    def test_pending_ocr_completion_blocks_reentry_and_cancel_drops_result(self):
        page = RecognitionPage(lambda: None)
        page.worker = Mock()
        page.worker.isRunning.return_value = False
        page._ocr_job_active = True
        self.assertTrue(page._recognition_is_running())
        self.assertFalse(page.import_pdf("new.pdf"))
        page.abort_current_workflow()
        with patch.object(QMessageBox, "information") as info:
            page.on_batch_finished([{"image_path": "stale.png"}])
            info.assert_not_called()
        self.assertEqual(page.all_results, [])
        self.assertFalse(page._ocr_job_active)
        page.shutdown()
        page.deleteLater()

    def test_startup_and_ocr_page_imports_do_not_load_inference_or_pdf_libraries(self):
        code = (
            "import sys; import app.main_window; "
            "assert 'features.patent_review.rule_engine' not in sys.modules; "
            "import ui.recognition_page; "
            "assert not any(x in sys.modules for x in ('torch', 'cv2', 'pymupdf'))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reimporting_images_invalidates_preview_cache(self):
        page = RecognitionPage(lambda: None)
        page.current_pixmap = object()
        page._preview_image_key = (0, "same.png")
        page.reset_review_state()
        self.assertIsNone(page.current_pixmap)
        self.assertIsNone(page._preview_image_key)
        page.shutdown()
        page.deleteLater()

    def test_clean_window_shuts_down_formal_page_workers(self):
        from tools import build_company_update
        with patch.dict(sys.modules, {"build_company_update": build_company_update}):
            from tools.build_clean_company_update import CLEAN_MAIN_WINDOW
        namespace = {"__name__": "clean_window_test"}
        exec(compile(CLEAN_MAIN_WINDOW, "clean_main_window.py", "exec"), namespace)
        window = namespace["MainWindow"]()
        page = window.feature_pages["patent_review"]
        window.close()
        self.assertTrue(page._review_tasks._closed)
        self.assertTrue(page._rules_tasks._closed)
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
