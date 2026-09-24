import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QPushButton, QToolButton

from app.main_window import MainWindow
from features.registry import FEATURES
from ui.feature_navigation import FEATURE_NAVIGATION_HEIGHT


class FeatureNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.startup_messages = []
        self.window = MainWindow(
            startup_progress_callback=self.startup_messages.append
        )

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def test_formal_pages_and_games_are_created_only_on_first_use(self):
        self.assertEqual(list(self.window.feature_pages.keys()), [])
        self.assertIsNone(self.window._snake_page)
        self.assertIsNone(self.window._pong_page)

        self.window.open_feature("patent_review")

        self.assertEqual(list(self.window.feature_pages.keys()), ["patent_review"])
        self.assertIsNotNone(self.window.currentWidget())
        self.assertIsNone(self.window._snake_page)
        self.assertIsNone(self.window._pong_page)

    def test_every_registered_feature_has_the_same_top_navigation(self):
        expected = [
            "回首頁",
            "文件偵錯",
            "圖式標號",
            "段落圖式比對",
            "台陸轉換",
        ]
        for feature in FEATURES:
            page = self.window.feature_pages[feature["id"]]
            labels = [
                button.text()
                for button in page.feature_navigation.findChildren(QPushButton)
            ]
            self.assertEqual(labels, expected)
            self.assertNotIn("功能測試頁", labels)
            active = page.feature_navigation.findChild(
                QPushButton, "ActiveFeatureNavigationButton"
            )
            expected_active = {
                "patent_review": "文件偵錯",
                "patent_ocr": "圖式標號",
                "embodiment_figure_compare": "段落圖式比對",
                "taiwan_china_spec": "台陸轉換",
                "demo_tool": None,
            }[feature["id"]]
            self.assertEqual(
                active.text() if active is not None else None,
                expected_active,
            )

    def test_main_window_reports_incremental_startup_progress(self):
        self.assertTrue(self.startup_messages)
        self.assertEqual(self.startup_messages[-1], "主程式準備完成")
        for feature in FEATURES:
            self.assertIn(
                f"正在載入{feature['title']}",
                self.startup_messages,
            )

    def test_navigation_has_the_same_top_slot_on_every_feature_page(self):
        for feature in FEATURES:
            page = self.window.feature_pages[feature["id"]]
            margins = page.layout().contentsMargins()
            self.assertEqual((margins.left(), margins.top(), margins.right()), (10, 4, 10))
            self.assertEqual(page.layout().indexOf(page.feature_navigation), 0)
            self.assertEqual(
                page.feature_navigation.height(),
                FEATURE_NAVIGATION_HEIGHT,
            )

    def test_switching_pages_preserves_visible_window_geometry(self):
        self.window.show()
        QApplication.processEvents()
        available = self.window.screen().availableGeometry()
        # The offscreen platform reports an 800px-wide synthetic screen; use a
        # smaller test-only minimum so the decorated window can retain margin.
        self.window.setMinimumSize(640, 480)
        self.window.resize(
            min(1000, max(640, available.width() - 40)),
            min(680, max(480, available.height() - 40)),
        )
        QApplication.processEvents()
        expected = self.window.geometry()

        for feature_id in (
            "patent_review",
            "patent_ocr",
            "embodiment_figure_compare",
            "taiwan_china_spec",
        ):
            self.window.open_feature(feature_id)
            QApplication.processEvents()
            actual = self.window.geometry()
            self.assertEqual(actual.size(), expected.size())
            # Offscreen Qt can re-round the decorated origin by one physical
            # pixel while constraining it to the simulated screen edge.
            self.assertLessEqual(abs(actual.x() - expected.x()), 1)
            self.assertLessEqual(abs(actual.y() - expected.y()), 1)

    def test_navigation_switches_pages_without_recreating_them(self):
        source = self.window.feature_pages["patent_review"]
        target = self.window.feature_pages["patent_ocr"]
        button = next(
            button
            for button in source.feature_navigation.findChildren(QPushButton)
            if button.text() == "圖式標號"
        )

        button.click()

        self.assertIs(self.window.currentWidget(), target)
        self.assertIs(self.window.feature_pages["patent_review"], source)

    def test_every_easter_egg_page_uses_the_same_compact_navigation(self):
        expected = [
            "首頁",
            "工作進度",
            "聊天室",
            "彈球",
            "方塊",
            "坦克",
            "1A2B",
            "貪食蛇",
        ]
        pages = {
            "chat": self.window.chat_room_page,
            "pong": self.window.pong_page,
            "tetris": self.window.tetris_page,
            "tank": self.window.tank_page,
            "bulls_and_cows": self.window.bulls_cows_page,
            "snake": self.window.snake_page,
        }
        for active_id, page in pages.items():
            bar = page.arcade_navigation
            self.assertEqual(
                [button.text() for button in bar.buttons.values()], expected
            )
            self.assertEqual(bar.height(), 50)
            self.assertEqual(
                [
                    destination_id
                    for destination_id, button in bar.buttons.items()
                    if button.objectName() == "ActiveFeatureNavigationButton"
                ],
                [active_id],
            )

        self.assertEqual(self.window.chat_room_page.chat_side_tabs.count(), 2)

    def test_unread_badge_opens_chat_and_returns_to_unchanged_work_page(self):
        work_page = self.window.feature_pages["patent_review"]
        self.window.open_feature("patent_review")
        self.window.chat_room_page.prepare_chat = lambda: None

        self.window.chat_room_page.unread_count_changed.emit(3)
        badge = work_page.feature_navigation.findChild(
            QToolButton, "ChatUnreadBadge"
        )
        self.assertIsNotNone(badge)
        self.assertFalse(badge.isHidden())
        self.assertEqual(badge.text(), "3")

        badge.click()
        self.assertIs(self.window.currentWidget(), self.window.chat_room_page)
        self.assertTrue(self.window.chat_room_page.return_work_button.isEnabled())
        self.window.chat_room_page.return_work_button.click()
        self.assertIs(self.window.currentWidget(), work_page)

        self.window.chat_room_page.unread_count_changed.emit(0)
        self.assertTrue(badge.isHidden())

    def test_escape_from_a_focused_field_always_returns_home_first(self):
        page = self.window.feature_pages["patent_ocr"]
        self.window.open_feature("patent_ocr")
        page.model_line.setFocus()
        aborted = []
        original_abort = page.abort_current_workflow
        page.abort_current_workflow = lambda: (
            aborted.append(True),
            original_abort(),
        )

        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
        QApplication.sendEvent(page.model_line, event)

        self.assertIs(self.window.currentWidget(), self.window.home_page)
        self.assertEqual(aborted, [True])
        self.assertTrue(event.isAccepted())


if __name__ == "__main__":
    unittest.main()
