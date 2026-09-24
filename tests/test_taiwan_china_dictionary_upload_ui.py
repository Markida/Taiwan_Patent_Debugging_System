import os
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from ui.taiwan_china_spec_page import TaiwanChinaSpecPage


def snapshot(pairs, source_kind="內建"):
    return SimpleNamespace(
        pairs=tuple(pairs),
        source_kind=source_kind,
        source_path=Path("company_dictionary.txt"),
        warning="",
    )


class TaiwanChinaSharedDictionaryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        initial = snapshot(
            (
                ("先", "第一"),
                ("刪", "移除"),
                ("後", "最後"),
            )
        )
        self.store = Mock()
        self.store.load_bundled.return_value = initial
        self.store.load_preferred.return_value = snapshot(
            (("同步台灣", "同步大陸"),),
            source_kind="雲端",
        )
        self.store.upload_pairs.return_value = snapshot(
            initial.pairs,
            source_kind="雲端",
        )
        self.page = TaiwanChinaSpecPage(
            lambda: None,
            dictionary_store=self.store,
            auto_load_dictionary=False,
        )
        self.page.resize(960, 640)
        self.page.show()
        QApplication.processEvents()

    def tearDown(self):
        self._wait_for_dictionary_jobs()
        self.page.close()
        self.page.deleteLater()
        QApplication.processEvents()

    def _wait_for_dictionary_jobs(self):
        for _attempt in range(200):
            QApplication.processEvents()
            if (
                self.page._dictionary_thread is None
                and self.page._dictionary_upload_thread is None
            ):
                return
            time.sleep(0.005)
        self.fail("辭典背景作業未在時限內完成")

    def test_sync_and_upload_buttons_are_both_visible_and_distinct(self):
        self.page.dictionary_dialog.show()
        QApplication.processEvents()
        buttons = {
            button.text(): button
            for button in self.page.dictionary_dialog.findChildren(QPushButton)
        }

        self.assertIn("同步", buttons)
        self.assertIn("上傳", buttons)
        self.assertIs(buttons["同步"], self.page.reload_dictionary_button)
        self.assertIs(buttons["上傳"], self.page.upload_dictionary_button)
        self.assertFalse(buttons["同步"].icon().isNull())
        self.assertFalse(buttons["上傳"].icon().isNull())
        self.assertLess(
            buttons["同步"].mapTo(
                self.page.dictionary_dialog,
                buttons["同步"].rect().topLeft(),
            ).x(),
            buttons["上傳"].mapTo(
                self.page.dictionary_dialog,
                buttons["上傳"].rect().topLeft(),
            ).x(),
        )
        self.assertLessEqual(self.page.minimumSizeHint().width(), 960)
        self.assertLessEqual(self.page.minimumSizeHint().height(), 640)

    def test_sync_only_loads_the_shared_dictionary(self):
        QTest.mouseClick(self.page.reload_dictionary_button, Qt.LeftButton)
        self._wait_for_dictionary_jobs()

        self.store.load_preferred.assert_called_once_with()
        self.store.upload_pairs.assert_not_called()
        self.assertEqual(self.page.dictionary_status.text(), "● 雲端")
        self.assertEqual(self.page.dictionary_table.rowCount(), 1)

    def test_upload_sends_the_full_edited_table_in_display_order(self):
        self.page.dictionary_table.selectRow(1)
        self.page.delete_selected_dictionary_rows()
        self.page.add_dictionary_row()
        last_row = self.page.dictionary_table.rowCount() - 1
        self.page.dictionary_table.item(last_row, 0).setText("新增")
        self.page.dictionary_table.item(last_row, 1).setText("追加")
        expected = (("先", "第一"), ("後", "最後"), ("新增", "追加"))
        self.store.upload_pairs.return_value = snapshot(
            expected,
            source_kind="雲端",
        )

        QTest.mouseClick(self.page.upload_dictionary_button, Qt.LeftButton)
        self._wait_for_dictionary_jobs()

        self.store.upload_pairs.assert_called_once_with(expected)
        self.store.load_preferred.assert_not_called()
        self.assertEqual(self.page.dictionary_status.text(), "● 雲端")
        self.assertIn("已上傳公司辭典：3 組", self.page.message_output.toPlainText())
        self.assertTrue(self.page.reload_dictionary_button.isEnabled())
        self.assertTrue(self.page.upload_dictionary_button.isEnabled())

    def test_upload_failure_keeps_edits_and_shows_a_clear_error(self):
        self.store.upload_pairs.side_effect = OSError("共用文字檔目前無法寫入")
        original_pairs = self.page._dictionary_pairs_from_table()

        QTest.mouseClick(self.page.upload_dictionary_button, Qt.LeftButton)
        self._wait_for_dictionary_jobs()

        self.assertEqual(
            self.page._dictionary_pairs_from_table(),
            original_pairs,
        )
        self.assertEqual(self.page.dictionary_status.text(), "● 已修訂")
        self.assertIn("辭典上傳失敗", self.page.message_output.toPlainText())
        self.assertIn("共用文字檔", self.page.message_output.toPlainText())
        self.assertTrue(self.page.reload_dictionary_button.isEnabled())
        self.assertTrue(self.page.upload_dictionary_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
