import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from app.main_window import MainWindow
from features.registry import FEATURES


class FeatureNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.deleteLater()

    def test_every_registered_feature_has_the_same_top_navigation(self):
        expected = ["回首頁", "文件偵錯", "圖式標號", "段落圖式比對 (beta)"]
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
                "embodiment_figure_compare": "段落圖式比對 (beta)",
                "demo_tool": None,
            }[feature["id"]]
            self.assertEqual(
                active.text() if active is not None else None,
                expected_active,
            )

    def test_navigation_has_the_same_top_slot_on_every_feature_page(self):
        for feature in FEATURES:
            page = self.window.feature_pages[feature["id"]]
            margins = page.layout().contentsMargins()
            self.assertEqual((margins.left(), margins.top(), margins.right()), (10, 4, 10))
            self.assertEqual(page.layout().indexOf(page.feature_navigation), 0)
            self.assertEqual(page.feature_navigation.height(), 42)

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


if __name__ == "__main__":
    unittest.main()
