import os
import tempfile
import unittest
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QUrl
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from ui.patent_review_page import PatentReviewPage
from ui.recognition_page import RecognitionPage
from ui.taiwan_china_spec_page import TaiwanChinaSpecPage
from app.main_window import MainWindow


class FakeMimeData:
    def __init__(self, paths):
        self._urls = [QUrl.fromLocalFile(str(path)) for path in paths]

    def hasUrls(self):
        return True

    def urls(self):
        return self._urls


class FakeDropEvent:
    def __init__(self, paths, event_type=QEvent.Type.Drop):
        self._mime_data = FakeMimeData(paths)
        self._event_type = event_type
        self.accepted = False

    def type(self):
        return self._event_type

    def mimeData(self):
        return self._mime_data

    def acceptProposedAction(self):
        self.accepted = True


class FileDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, ready):
        deadline = time.monotonic() + 5
        while not ready() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(ready())

    def test_document_page_accepts_one_docx_from_any_drop_target(self):
        page = PatentReviewPage(lambda: None)
        dropped = []
        page.file_drop_controller.on_file = dropped.append
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "公司文件.DOCX"
            path.write_bytes(b"test")
            event = FakeDropEvent([path])

            handled = page.file_drop_controller.eventFilter(
                page.issue_table.viewport(), event
            )

            self.assertTrue(handled)
            self.assertTrue(event.accepted)
            self.assertEqual(dropped, [str(path)])
            self.assertTrue(page.issue_table.viewport().acceptDrops())
        page.deleteLater()

    def test_wrong_extension_and_multiple_files_show_clear_warning(self):
        page = PatentReviewPage(lambda: None)
        dropped = []
        page.file_drop_controller.on_file = dropped.append
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            QMessageBox, "warning"
        ) as warning:
            pdf_path = Path(temp_dir) / "wrong.pdf"
            second_path = Path(temp_dir) / "second.docx"
            pdf_path.write_bytes(b"pdf")
            second_path.write_bytes(b"docx")

            page.file_drop_controller.eventFilter(page, FakeDropEvent([pdf_path]))
            self.assertIn("僅接受 Word 文件（.docx）", warning.call_args.args[2])

            page.file_drop_controller.eventFilter(
                page, FakeDropEvent([pdf_path, second_path])
            )
            self.assertIn("一次只能拖入一個檔案", warning.call_args.args[2])
            self.assertEqual(dropped, [])
        page.deleteLater()

    def test_ocr_pdf_drop_runs_the_existing_pdf_import_pipeline(self):
        page = RecognitionPage(lambda: None)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            pdf_path = temp_path / "圖式資料.PDF"
            png_path = temp_path / "page_001.png"
            pdf_path.write_bytes(b"synthetic pdf")
            image = QImage(20, 20, QImage.Format.Format_RGB32)
            image.fill(0xFFFFFF)
            self.assertTrue(image.save(str(png_path)))

            with patch(
                "ui.recognition_page.convert_pdf_to_images",
                return_value=[str(png_path)],
            ) as convert, patch.object(QMessageBox, "information"):
                event = FakeDropEvent([pdf_path])
                handled = page.file_drop_controller.eventFilter(
                    page.image_preview, event
                )
                self.wait_until(lambda: not page._pdf_tasks.busy)

            self.assertTrue(handled)
            self.assertTrue(event.accepted)
            convert.assert_called_once()
            self.assertEqual(page.source_image_paths, [str(png_path)])
            self.assertIn("圖式資料.PDF", page.image_line.text())
            self.assertTrue(page.rotate_button.isEnabled())
            self.assertTrue(page.rotate_left_button.isEnabled())
            self.assertTrue(page.rotate_all_right_button.isEnabled())
        page.deleteLater()

    def test_ocr_page_rejects_non_pdf_drop(self):
        page = RecognitionPage(lambda: None)
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            QMessageBox, "warning"
        ) as warning:
            docx_path = Path(temp_dir) / "說明書.docx"
            docx_path.write_bytes(b"docx")

            page.file_drop_controller.eventFilter(page, FakeDropEvent([docx_path]))

            self.assertIn("僅接受 PDF 文件（.pdf）", warning.call_args.args[2])
            self.assertEqual(page.source_image_paths, [])
        page.deleteLater()

    def test_taiwan_china_page_owns_its_word_drop_without_cross_routing(self):
        page = TaiwanChinaSpecPage(lambda: None, auto_load_dictionary=False)
        dropped = []
        page.file_drop_controller.on_file = dropped.append
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "台陸轉換來源.DOCX"
            path.write_bytes(b"test")
            event = FakeDropEvent([path])

            handled = page.file_drop_controller.eventFilter(
                page.preview_output.viewport(),
                event,
            )

            self.assertTrue(handled)
            self.assertTrue(event.accepted)
            self.assertEqual(dropped, [str(path)])
            self.assertTrue(page.preview_output.viewport().acceptDrops())
        page.close()
        page.deleteLater()

    def test_taiwan_china_drop_immediately_reads_and_previews_the_word_file(self):
        page = TaiwanChinaSpecPage(lambda: None, auto_load_dictionary=False)
        source = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "TW_方向校正專利圖式標號檢核系統_發明說明書.docx"
        )
        event = FakeDropEvent([source])

        handled = page.file_drop_controller.eventFilter(
            page.preview_output.viewport(),
            event,
        )
        self.wait_until(lambda: page._preview_thread is None and page._pending_preview is None)

        self.assertTrue(handled)
        self.assertTrue(event.accepted)
        self.assertEqual(page.source_line.text(), str(source))
        self.assertIn("說明書摘要", page.preview_output.toPlainText())
        self.assertIn("#c62828", page.preview_output.toHtml())
        page.close()
        page.deleteLater()

    def test_global_drop_routes_docx_and_pdf_to_their_feature_pages(self):
        window = MainWindow()
        document_page = window.feature_pages["patent_review"]
        ocr_page = window.feature_pages["patent_ocr"]
        comparison_page = window.feature_pages["embodiment_figure_compare"]
        converter_page = window.feature_pages["taiwan_china_spec"]
        installed = window.routed_file_drop_controller._installed_widgets
        self.assertIn(document_page, installed)
        self.assertIn(ocr_page, installed)
        self.assertIn(comparison_page, installed)
        self.assertNotIn(converter_page, installed)
        self.assertNotIn(converter_page.preview_output.viewport(), installed)
        with tempfile.TemporaryDirectory() as temp_dir:
            docx_path = Path(temp_dir) / "sample.DOCX"
            pdf_path = Path(temp_dir) / "sample.PDF"
            docx_path.write_bytes(b"docx")
            pdf_path.write_bytes(b"pdf")

            with patch.object(
                document_page,
                "load_document",
                return_value=True,
            ) as load_document:
                handled = window.routed_file_drop_controller.eventFilter(
                    ocr_page.image_preview,
                    FakeDropEvent([docx_path]),
                )
            self.assertTrue(handled)
            load_document.assert_called_once_with(str(docx_path))
            self.assertIs(window.currentWidget(), document_page)

            with patch.object(
                ocr_page,
                "import_pdf",
                return_value=True,
            ) as import_pdf:
                handled = window.routed_file_drop_controller.eventFilter(
                    document_page.issue_table.viewport(),
                    FakeDropEvent([pdf_path]),
                )
            self.assertTrue(handled)
            import_pdf.assert_called_once_with(str(pdf_path))
            self.assertIs(window.currentWidget(), ocr_page)
        window.snake_page.board.timer.stop()
        window.chat_room_page.deactivate()
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
