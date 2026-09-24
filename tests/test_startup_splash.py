import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from app.startup_splash import StartupSplash


class StartupSplashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_status_animation_and_finish_lifecycle(self):
        splash = StartupSplash()
        splash.show()
        self.app.processEvents()

        self.assertTrue(splash.isVisible())
        self.assertTrue(splash.animation_timer.isActive())
        before = splash.animation_label.text()
        splash._advance_animation()
        self.assertNotEqual(splash.animation_label.text(), before)
        splash.set_status("正在載入圖片標號識別")
        self.assertEqual(splash.status_label.text(), "正在載入圖片標號識別")
        self.assertIn("#e0f2fe", splash.styleSheet())
        self.assertIn("#bfdbfe", splash.styleSheet())

        main_window = QWidget()
        main_window.show()
        splash.finish(main_window)
        self.assertFalse(splash.animation_timer.isActive())
        self.assertFalse(splash.isVisible())
        main_window.close()
        main_window.deleteLater()


if __name__ == "__main__":
    unittest.main()
