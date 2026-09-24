import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from features.taiwan_china_spec import ConversionPreview, ConversionPreviewParagraph
from features.taiwan_china_spec.terminology_store import TerminologySnapshot
from ui.taiwan_china_spec_page import TaiwanChinaSpecPage


class TaiwanChinaResponsivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.store = Mock()
        self.store.load_bundled.return_value = TerminologySnapshot(
            pairs=(("模組", "模塊"),), source_kind="內建", source_path=Path("local.txt")
        )
        self.page = TaiwanChinaSpecPage(
            lambda: None, dictionary_store=self.store, auto_load_dictionary=False
        )

    def tearDown(self):
        self.page.shutdown()
        self.page.close()
        self.page.deleteLater()
        QApplication.processEvents()

    def pump_until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if predicate():
                return
            time.sleep(0.002)
        self.fail("背景作業未在時限內完成")

    def wait_preview(self):
        self.pump_until(lambda: self.page._preview_thread is None and self.page._pending_preview is None)

    @staticmethod
    def preview(source):
        return ConversionPreview(
            source=str(source), specification_kind="發明",
            paragraphs=[ConversionPreviewParagraph(str(source), str(source))],
        )

    def test_slow_word_load_and_diff_do_not_block_gui_timer(self):
        release, entered = threading.Event(), threading.Event()
        threads = []
        gui_thread = threading.get_ident()

        def slow_load(source, *_args, **_kwargs):
            threads.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return self.preview(source)

        ticked = []
        with patch("ui.taiwan_china_spec_page.build_conversion_preview", side_effect=slow_load):
            try:
                self.assertTrue(self.page.load_source("slow.docx"))
                self.assertTrue(entered.wait(0.5))
                QTimer.singleShot(0, lambda: ticked.append(True))
                self.pump_until(lambda: bool(ticked))
                self.assertFalse(self.page.preview_output.isEnabled())
                self.assertFalse(self.page.convert_button.isEnabled())
                self.assertNotEqual(threads, [gui_thread])
                self.assertTrue(self.page._preview_thread.daemon)
            finally:
                release.set()
                self.wait_preview()
        self.assertTrue(self.page.preview_output.isEnabled())
        self.assertIn("slow.docx", self.page.preview_output.toPlainText())

    def test_rapid_source_changes_only_build_first_and_latest_and_drop_stale_result(self):
        release, entered = threading.Event(), threading.Event()
        built = []

        def slow_load(source, *_args, **_kwargs):
            built.append(source)
            if source == "first.docx":
                entered.set()
                release.wait(2)
            return self.preview(source)

        with patch("ui.taiwan_china_spec_page.build_conversion_preview", side_effect=slow_load):
            try:
                self.page.load_source("first.docx")
                self.assertTrue(entered.wait(0.5))
                self.page.load_source("skipped.docx")
                self.page.load_source("latest.docx")
                self.assertEqual(built, ["first.docx"])
            finally:
                release.set()
                self.wait_preview()
        self.assertEqual(built, ["first.docx", "latest.docx"])
        self.assertEqual(self.page.preview_output.toPlainText(), "latest.docx")

    def test_preview_failure_recovers_controls_and_cannot_convert_old_source(self):
        self.page._render_preview(self.preview("old.docx"))
        with patch("ui.taiwan_china_spec_page.build_conversion_preview", side_effect=OSError("離線")):
            self.page.load_source("offline.docx")
            self.wait_preview()
        self.assertEqual(self.page.preview_output.toPlainText(), "")
        self.assertEqual(self.page._preview_source, "")
        self.assertFalse(self.page.convert_button.isEnabled())
        self.assertTrue(self.page.source_button.isEnabled())
        self.assertIn("離線", self.page.message_output.toPlainText())

    def test_returning_to_edited_source_cancels_other_file_without_losing_edits(self):
        self.page._render_preview(self.preview("edited.docx"))
        self.page.preview_output.append("保留人工修訂")
        edited = self.page.preview_output.toPlainText()
        release, entered = threading.Event(), threading.Event()

        def slow_load(source, *_args, **_kwargs):
            entered.set()
            release.wait(2)
            return self.preview(source)

        with patch("ui.taiwan_china_spec_page.build_conversion_preview", side_effect=slow_load):
            try:
                self.page.load_source("other.docx")
                self.assertTrue(entered.wait(0.5))
                self.page.load_source("edited.docx")
            finally:
                release.set()
                self.wait_preview()
        self.assertEqual(self.page.preview_output.toPlainText(), edited)
        self.assertTrue(self.page._preview_dirty)
        self.assertIn("人工修訂", self.page.preview_status.text())

    def test_shutdown_does_not_wait_for_shared_dictionary_or_apply_late_result(self):
        release, entered = threading.Event(), threading.Event()

        def slow_dictionary():
            entered.set()
            release.wait(2)
            return TerminologySnapshot((("新", "字"),), "雲端", Path("shared.txt"))

        self.store.load_preferred.side_effect = slow_dictionary
        self.page.reload_dictionary()
        try:
            self.assertTrue(entered.wait(0.5))
            self.assertTrue(self.page._dictionary_thread.daemon)
            before = self.page._dictionary_pairs_from_table()
            self.page.shutdown()
            ticked = []
            QTimer.singleShot(0, lambda: ticked.append(True))
            self.pump_until(lambda: bool(ticked))
        finally:
            release.set()
            self.pump_until(lambda: self.page._dictionary_thread is None)
        self.assertEqual(self.page._dictionary_pairs_from_table(), before)

    def test_identical_repetitive_redline_uses_exact_render_without_quadratic_diff(self):
        text = "一個基座具有一個外殼。<>&\n" * 250
        with patch("ui.taiwan_china_spec_page.SequenceMatcher") as matcher:
            rendered = TaiwanChinaSpecPage._converted_text_html(text, text)
        matcher.assert_not_called()
        self.assertEqual(rendered, ("一個基座具有一個外殼。&lt;&gt;&amp;<br>" * 250))

    def test_preview_html_reset_scans_article_list_only_once(self):
        with patch.object(self.page.article_review, "_rebuild_list", wraps=self.page.article_review._rebuild_list) as rebuild:
            self.page._render_preview(self.preview("一個元件.docx"))
        self.assertEqual(rebuild.call_count, 1)


if __name__ == "__main__":
    unittest.main()
