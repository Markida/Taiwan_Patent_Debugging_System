import os
import random
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QPushButton, QSizePolicy, QStackedWidget

from app.features.bulls_and_cows.engine import (
    BullsAndCowsError,
    LocalTwoPlayerGame,
    SinglePlayerGame,
    generate_secret,
    score_guess,
    validate_number,
)
from app.main_window import MainWindow
from app.features.bulls_and_cows.network import BullsAndCowsClient, BullsAndCowsHost
from ui.bulls_and_cows_page import BullsAndCowsPage


class BullsAndCowsTests(unittest.TestCase):
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

    def test_validation_requires_four_unique_digits_and_allows_leading_zero(self):
        self.assertEqual(validate_number(" 1234 "), "1234")
        self.assertEqual(validate_number("0123"), "0123")
        for invalid in ("1123", "123", "12345", "12A4", "１２３４"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(BullsAndCowsError):
                    validate_number(invalid)

    def test_classic_score_counts_correct_positions_and_misplaced_digits(self):
        self.assertEqual(score_guess("1234", "1234").notation, "4A0B")
        self.assertEqual(score_guess("1234", "4321").notation, "0A4B")
        self.assertEqual(score_guess("1234", "1357").notation, "1A1B")
        self.assertEqual(score_guess("1234", "5678").notation, "0A0B")

    def test_generated_secret_is_always_valid_and_reproducible(self):
        first = generate_secret(random.Random(9))
        second = generate_secret(random.Random(9))
        self.assertEqual(first, second)
        self.assertEqual(validate_number(first), first)

    def test_single_player_tracks_attempts_and_stops_after_win(self):
        game = SinglePlayerGame("4271")
        first = game.submit_guess("1234")
        self.assertEqual(first.turn, 1)
        self.assertFalse(game.finished)
        winner = game.submit_guess("4271")
        self.assertTrue(winner.result.won)
        self.assertTrue(game.finished)
        with self.assertRaises(BullsAndCowsError):
            game.submit_guess("4271")

    def test_two_players_set_private_secrets_and_alternate_until_win(self):
        game = LocalTwoPlayerGame(("甲", "乙"))
        game.set_secret(0, "1234")
        game.set_secret(1, "5678")
        self.assertTrue(game.ready)
        first = game.submit_guess("5670")
        self.assertEqual(first.player_index, 0)
        self.assertEqual(game.active_player, 1)
        second = game.submit_guess("1234")
        self.assertEqual(second.player_index, 1)
        self.assertEqual(game.winner_index, 1)
        self.assertTrue(game.finished)

    def test_page_can_switch_between_modes_without_network(self):
        page = BullsAndCowsPage(
            lambda: None,
            single_game_factory=lambda: SinglePlayerGame("1234"),
        )
        try:
            page.start_single_player()
            self.assertEqual(page.mode, "single")
            self.assertIs(page.pages.currentWidget(), page.game_page)
            page.start_two_player()
            self.assertEqual(page.mode, "two")
            self.assertIs(page.pages.currentWidget(), page.setup_page)
        finally:
            page.deleteLater()

    def test_home_matches_the_shared_arcade_lobby_pattern(self):
        page = BullsAndCowsPage(lambda: None)
        try:
            self.assertIs(page.pages.currentWidget(), page.mode_page)
            self.assertIs(page.lan_lobby_page, page.mode_page)
            self.assertEqual(page.pages.count(), 3)
            labels = {label.text() for label in page.mode_page.findChildren(QPushButton)}
            self.assertIn("👤  單人挑戰", labels)
            self.assertIn("👥  雙人輪流", labels)
            self.assertIn("🌐  建立區網房間", labels)
            self.assertGreaterEqual(page.lan_room_list.minimumHeight(), 180)
        finally:
            page.shutdown()
            page.deleteLater()

    def test_arcade_lobby_controls_fit_the_960_by_640_window(self):
        page = BullsAndCowsPage(lambda: None)
        host = QStackedWidget()
        try:
            page.setMinimumSize(0, 0)
            page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
            host.addWidget(page)
            host.setFixedSize(960, 640)
            host.show()
            self.app.processEvents()
            targets = [
                *page.mode_page.findChildren(QPushButton),
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

    def test_lan_players_set_private_secrets_and_share_turn_state(self):
        host = BullsAndCowsHost()
        client = BullsAndCowsClient()
        states = []
        client.state_changed.connect(states.append)
        try:
            host.create_room("甲", port=0)
            client.connect_to_room(
                f"127.0.0.1:{host.transport.server.serverPort()}",
                "乙",
            )
            self.assertTrue(self.wait_until(lambda: host.guest_name == "乙"))
            host.set_secret("left", "1234")
            client.set_secret("5678")
            self.assertTrue(self.wait_until(lambda: host.status == "playing"))
            host.submit_guess("left", "5678")
            self.assertTrue(
                self.wait_until(
                    lambda: any(state.get("status") == "finished" for state in states)
                )
            )
            self.assertEqual(host.winner, "left")
            self.assertNotIn("secret", host.state())
        finally:
            client.disconnect()
            host.close()
            client.deleteLater()
            host.deleteLater()

    def test_main_window_loads_1a2b_lazily_and_keeps_it_hidden(self):
        window = MainWindow()
        try:
            self.assertIsNone(window._bulls_cows_page)
            window.open_bulls_and_cows()
            self.assertIs(window.currentWidget(), window.bulls_cows_page)
            self.assertIsNotNone(window._bulls_cows_page)
            home_labels = [
                button.text()
                for button in window.home_page.findChildren(QPushButton)
            ]
            self.assertNotIn("1A2B", home_labels)
        finally:
            window.close()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
