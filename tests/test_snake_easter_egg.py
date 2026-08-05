import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from app.features.snake.score_store import SnakeScoreStore
from app.main_window import MainWindow


class SnakeEasterEggTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_home_title_p_requires_five_clicks_and_is_not_registered(self):
        window = MainWindow()
        try:
            self.assertNotIn("snake", window.feature_pages)
            navigation_labels = {
                button.text()
                for page in window.feature_pages.values()
                for button in page.feature_navigation.findChildren(QPushButton)
            }
            self.assertNotIn("SNAKE", navigation_labels)

            for _ in range(4):
                window.home_page.secret_p_button.click()
                self.assertIs(window.currentWidget(), window.home_page)
            window.home_page.secret_p_button.click()

            self.assertIs(window.currentWidget(), window.snake_page)
        finally:
            window.snake_page.board.timer.stop()
            window.deleteLater()

    def test_score_store_keeps_a_persistent_top_three(self):
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app/features/snake/snake_scores.json"
            store = SnakeScoreStore(path)
            for name, score in (
                ("甲", 20),
                ("乙", 70),
                ("丙", 40),
                ("丁", 100),
            ):
                store.record(name, score)

            top = SnakeScoreStore(path).top(3)

            self.assertEqual(
                [(entry.name, entry.score) for entry in top],
                [("丁", 100), ("乙", 70), ("丙", 40)],
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)

    def test_every_third_normal_food_spawns_a_timed_bonus(self):
        window = MainWindow()
        try:
            board = window.snake_page.board
            board.timer.stop()
            board.snake = [(5, 5), (4, 5), (3, 5)]
            board.direction = (1, 0)
            board.pending_direction = (1, 0)
            board.food = (6, 5)
            board.bonus_food = None
            board.normal_food_count = 2
            board.score = 0
            board.running = True

            board.advance()

            self.assertEqual(board.normal_food_count, 3)
            self.assertEqual(board.score, board.NORMAL_SCORE)
            self.assertIsNotNone(board.bonus_food)
            self.assertGreater(board.bonus_ticks, 0)
        finally:
            window.snake_page.board.timer.stop()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
