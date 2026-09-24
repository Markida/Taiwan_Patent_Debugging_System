import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QPushButton, QSizePolicy, QStackedWidget

from app.features.snake.score_store import SnakeScoreStore, default_snake_score_path
from app.features.snake.network import SnakeBattleEngine, SnakeClient, SnakeHost
from app.features.chat_room.store import ChatRoomStore
from app.main_window import MainWindow
from ui.snake_game_page import SnakeGamePage


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SnakeEasterEggTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def test_default_score_path_uses_company_share(self):
        with patch.dict(os.environ, {"SAINT_ISLAND_SNAKE_ROOT": ""}):
            self.assertEqual(
                str(default_snake_score_path()),
                r"\\CPC2856\Documents\app\features\snake\snake_scores.json",
            )

    def test_five_home_title_clicks_open_chat_first_without_navigation_entry(self):
        window = MainWindow()
        try:
            window.chat_room_page.prepare_chat = lambda: None
            self.assertNotIn("chat_room", window.feature_pages)
            self.assertNotIn("snake", window.feature_pages)
            self.assertNotIn("pong", window.feature_pages)
            navigation_labels = {
                button.text()
                for page in window.feature_pages.values()
                for button in page.feature_navigation.findChildren(QPushButton)
            }
            self.assertNotIn("CHAT", navigation_labels)
            self.assertNotIn("SNAKE", navigation_labels)
            self.assertNotIn(
                "更多功能即將加入",
                {
                    button.text()
                    for button in window.home_page.findChildren(QPushButton)
                },
            )

            for _ in range(4):
                window.home_page.secret_p_button.click()
                self.assertIs(window.currentWidget(), window.home_page)
            window.home_page.secret_p_button.click()

            self.assertIs(window.currentWidget(), window.chat_room_page)
            window.open_snake_game()
            self.assertIs(window.currentWidget(), window.snake_page)
            window.open_pong_game()
            self.assertIs(window.currentWidget(), window.pong_page)
            window.open_tetris_game()
            self.assertIs(window.currentWidget(), window.tetris_page)
        finally:
            window.snake_page.board.timer.stop()
            window.chat_room_page.deactivate()
            window.pong_page.shutdown()
            window.tetris_page.shutdown()
            window.deleteLater()

    def test_easter_egg_pages_and_styles_are_present(self):
        main_source = (PROJECT_ROOT / "app/main_window.py").read_text(
            encoding="utf-8"
        )
        styles = (PROJECT_ROOT / "app/styles.py").read_text(encoding="utf-8")
        self.assertIn("ChatRoomPage", main_source)
        self.assertIn("SnakeGamePage", main_source)
        self.assertIn("PongGamePage", main_source)
        self.assertIn("TetrisGamePage", main_source)
        self.assertIn("#Chat", styles)
        self.assertIn("#Snake", styles)

    def test_score_store_keeps_a_persistent_top_ten(self):
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app/features/snake/snake_scores.json"
            store = SnakeScoreStore(path)
            for index in range(12):
                store.record(f"player-{index}", index * 10)

            top = SnakeScoreStore(path).top(10)

            self.assertEqual(len(top), 10)
            self.assertEqual(top[0].score, 110)
            self.assertEqual(top[-1].score, 20)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)

    def test_new_top_ten_score_posts_chat_announcement_with_rank(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            page = SnakeGamePage(
                lambda: None,
                score_store=SnakeScoreStore(root / "snake_scores.json"),
                chat_store=ChatRoomStore(root / "chat"),
            )
            try:
                with patch(
                    "ui.snake_game_page.local_computer_name",
                    return_value="CP2856",
                ), patch(
                    "ui.snake_game_page.publish_game_announcement"
                ) as announce:
                    page._on_game_finished(999)
                announce.assert_called_once()
                self.assertEqual(
                    announce.call_args.args[0],
                    '"CP2856"在貪食蛇中破了第1名的紀錄，太神啦',
                )
            finally:
                page.board.timer.stop()
                page.deleteLater()

    def test_snake_opens_on_the_same_lobby_flow_as_the_other_games(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            page = SnakeGamePage(
                lambda: None,
                score_store=SnakeScoreStore(root / "snake_scores.json"),
                chat_store=ChatRoomStore(root / "chat"),
            )
            try:
                self.assertEqual(page.mode, "")
                self.assertIs(page.pages.currentWidget(), page.lobby_page)
                buttons = {
                    button.text()
                    for button in page.lobby_page.findChildren(QPushButton)
                }
                self.assertIn("建立雙人房間", buttons)
                self.assertIn("▶  單人排行榜模式", buttons)
                page.show_local_mode()
                self.assertIs(page.pages.currentWidget(), page.local_page)
            finally:
                page.shutdown()
                page.deleteLater()

    def test_snake_lobby_controls_fit_the_960_by_640_window(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            page = SnakeGamePage(
                lambda: None,
                score_store=SnakeScoreStore(root / "snake_scores.json"),
                chat_store=ChatRoomStore(root / "chat"),
            )
            host = QStackedWidget()
            try:
                page.setMinimumSize(0, 0)
                page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
                host.addWidget(page)
                host.setFixedSize(960, 640)
                host.show()
                self.app.processEvents()
                targets = [
                    *page.lobby_page.findChildren(QPushButton),
                    page.lan_room_list,
                ]
                for widget in targets:
                    top_left = widget.mapTo(page, QPoint(0, 0))
                    self.assertTrue(
                        page.rect().contains(QRect(top_left, widget.size())),
                        widget.text() if hasattr(widget, "text") else widget.objectName(),
                    )
            finally:
                page.shutdown()
                host.close()
                page.deleteLater()
                host.deleteLater()

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
            window.chat_room_page.deactivate()
            window.deleteLater()

    def test_lan_snake_engine_and_transport_share_one_battle_state(self):
        engine = SnakeBattleEngine()
        engine.snakes["left"] = [(0, 0), (1, 0), (2, 0)]
        engine.directions["left"] = "left"
        engine.pending["left"] = "left"
        engine.tick()
        self.assertEqual(engine.status, "finished")
        self.assertEqual(engine.winner, "right")

        host = SnakeHost()
        client = SnakeClient()
        states = []
        client.state_changed.connect(states.append)
        try:
            host.create_room("甲", port=0)
            client.connect_to_room(
                f"127.0.0.1:{host.transport.server.serverPort()}",
                "乙",
            )
            self.assertTrue(
                self.wait_until(
                    lambda: any(state.get("status") == "playing" for state in states)
                )
            )
            host.timer.stop()
            client.action("up")
            self.assertTrue(self.wait_until(lambda: host.engine.pending["right"] == "up"))
            host._tick()
            self.assertTrue(self.wait_until(lambda: len(states) >= 2))
        finally:
            client.disconnect()
            host.close()
            client.deleteLater()
            host.deleteLater()


if __name__ == "__main__":
    unittest.main()
