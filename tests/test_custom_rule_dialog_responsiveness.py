import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from features.patent_review.custom_rules import (
    CUSTOM_RULE_WHITELIST, CustomRuleStorageError, CustomTextRule,
)
from ui.custom_text_rule_dialog import CustomTextRuleDialog


class CustomRuleDialogResponsivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.store = Mock()
        self.store.path = Path("shared-rules.json")
        self.store.load.return_value = []
        self.dialog = None

    def tearDown(self):
        if self.dialog is not None:
            self.dialog.shutdown()
            self.dialog.close()
            self.dialog.deleteLater()
            QApplication.processEvents()

    def pump_until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if predicate():
                return
            time.sleep(0.002)
        self.fail("共用規則背景作業未在時限內完成")

    def open_dialog(self):
        self.dialog = CustomTextRuleDialog(self.store)
        self.dialog.show()
        self.pump_until(lambda: not self.dialog._tasks.busy)

    def test_slow_initial_load_keeps_gui_running(self):
        release, entered = threading.Event(), threading.Event()
        threads = []
        rule = CustomTextRule.from_text("的的")

        def load():
            threads.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return [rule]

        self.store.load.side_effect = load
        self.dialog = CustomTextRuleDialog(self.store)
        try:
            self.assertTrue(entered.wait(0.5))
            ticked = []
            QTimer.singleShot(0, lambda: ticked.append(True))
            self.pump_until(lambda: bool(ticked))
            self.assertNotEqual(threads, [threading.get_ident()])
            self.assertTrue(self.dialog._tasks.busy)
            self.assertFalse(self.dialog.close_button.isEnabled())
            self.assertFalse(self.dialog.add_current_rule())
        finally:
            release.set()
            self.pump_until(lambda: not self.dialog._tasks.busy)
        self.assertEqual(self.dialog.rule_table.rowCount(), 1)
        self.assertTrue(self.dialog.close_button.isEnabled())

    def test_save_and_delete_are_asynchronous_and_preserve_rule_types(self):
        self.open_dialog()
        release, entered = threading.Event(), threading.Event()
        rule = CustomTextRule.from_text("第一端", CUSTOM_RULE_WHITELIST)

        def add(*_args):
            entered.set()
            release.wait(2)
            self.store.load.return_value = [rule]
            return rule, True

        self.store.add.side_effect = add
        self.dialog._inputs[CUSTOM_RULE_WHITELIST].setText(rule.text)
        self.assertTrue(self.dialog.add_current_rule(CUSTOM_RULE_WHITELIST))
        try:
            self.assertTrue(entered.wait(0.5))
            self.assertFalse(self.dialog.rules_changed)
            self.assertFalse(self.dialog.add_current_rule(CUSTOM_RULE_WHITELIST))
        finally:
            release.set()
            self.pump_until(lambda: not self.dialog._tasks.busy)
        self.assertTrue(self.dialog.rules_changed)
        self.assertEqual(self.dialog.rule_table.rowCount(), 0)
        white_table = self.dialog._tables[CUSTOM_RULE_WHITELIST]
        self.assertEqual(white_table.rowCount(), 1)
        white_table.selectRow(0)
        self.store.remove.return_value = 1
        self.store.load.return_value = []
        self.assertEqual(self.dialog.delete_selected_rules(CUSTOM_RULE_WHITELIST), 1)
        self.pump_until(lambda: not self.dialog._tasks.busy)
        self.store.remove.assert_called_once_with((rule.rule_id,))
        self.assertEqual(white_table.rowCount(), 0)

    def test_failed_write_keeps_input_and_does_not_claim_saved(self):
        self.open_dialog()
        self.store.add.side_effect = CustomRuleStorageError("共用路徑離線")
        self.dialog.rule_input.setText("的的")
        self.assertTrue(self.dialog.add_current_rule())
        self.pump_until(lambda: not self.dialog._tasks.busy)
        self.assertFalse(self.dialog.rules_changed)
        self.assertEqual(self.dialog.rule_input.text(), "的的")
        self.assertIn("離線", self.dialog.status_label.text())
        self.assertTrue(self.dialog.close_button.isEnabled())

    def test_committed_write_followed_by_failed_read_still_reports_saved(self):
        self.open_dialog()
        rule = CustomTextRule.from_text("的的")
        self.store.add.return_value = (rule, True)
        self.store.load.side_effect = CustomRuleStorageError("重新讀取失敗")
        self.dialog.rule_input.setText(rule.text)
        self.dialog.add_current_rule()
        self.pump_until(lambda: not self.dialog._tasks.busy)
        self.assertTrue(self.dialog.rules_changed)
        self.assertIn("已將", self.dialog.status_label.text())
        self.assertIn("重新讀取失敗", self.dialog.status_label.text())

    def test_escape_closes_immediately_during_slow_save_and_suppresses_late_result(self):
        self.open_dialog()
        release, entered = threading.Event(), threading.Event()
        rule = CustomTextRule.from_text("的的")

        def save(*_args):
            entered.set()
            release.wait(2)
            return rule, True

        self.store.add.side_effect = save
        received = []
        runner = self.dialog._tasks
        runner.succeeded.connect(received.append)
        self.dialog.rule_input.setText(rule.text)
        self.dialog.add_current_rule()
        try:
            self.assertTrue(entered.wait(0.5))
            QTest.keyClick(self.dialog, Qt.Key_Escape)
            self.assertFalse(self.dialog.isVisible())
            self.assertTrue(runner._closed)
        finally:
            release.set()
            self.pump_until(lambda: not runner.busy)
        self.assertEqual(received, [])
        self.assertFalse(self.dialog.rules_changed)

    def test_destroyed_dialog_is_not_accessed_after_slow_save_returns(self):
        self.open_dialog()
        release, entered, completed = threading.Event(), threading.Event(), threading.Event()
        rule = CustomTextRule.from_text("的的")

        def save(*_args):
            entered.set()
            release.wait(2)
            completed.set()
            return rule, True

        self.store.add.side_effect = save
        self.dialog.rule_input.setText(rule.text)
        self.dialog.add_current_rule()
        try:
            self.assertTrue(entered.wait(0.5))
            self.dialog.close()
            self.dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.dialog = None
        finally:
            release.set()
            self.assertTrue(completed.wait(0.5))
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
