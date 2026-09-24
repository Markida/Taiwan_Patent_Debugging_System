import os
import random
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.features.chat_room.store import ChatProfileStore, ChatRoomStore
from app.features.tetris.network import (
    TetrisBattleEngine,
    TetrisClient,
    TetrisHost,
    TetrisRoom,
    TetrisRoomDirectory,
    TetrisRoomRegistry,
    best_ai_placement,
)
from app.features.tetris.themes import TETRIS_SKINS, unlocked_tetris_skins
from app.main_window import MainWindow
from ui.tetris_game_page import TetrisBattleBoard, TetrisGamePage


class TetrisGameTests(unittest.TestCase):
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

    def test_engine_moves_rotates_clears_lines_and_accepts_garbage(self):
        engine = TetrisBattleEngine(random.Random(4))
        engine.board[-1] = [1] * 9 + [0]
        engine.piece_id = 1
        engine.matrix = ((1,),)
        engine.x = 9
        engine.y = 0
        cleared = engine.hard_drop()
        self.assertEqual(cleared, 1)
        self.assertEqual(engine.lines, 1)
        self.assertGreaterEqual(engine.score, 100)

        before = [row[:] for row in engine.board]
        engine.add_garbage(2)
        self.assertNotEqual(engine.board, before)
        for row in engine.board[-2:]:
            self.assertNotIn(0, row)
            self.assertEqual(row.count(10), 1)
            self.assertEqual(row.count(8), engine.WIDTH - 1)

    def test_engine_renders_ghost_and_cpu_placement_is_valid(self):
        engine = TetrisBattleEngine(random.Random(19))
        visible = engine.visible_board()
        self.assertTrue(any(value == 9 for row in visible for value in row))
        rotations, target_x = best_ai_placement(engine)
        self.assertIn(rotations, range(4))
        self.assertGreaterEqual(target_x, 0)
        self.assertLess(target_x, engine.WIDTH)

    def test_battle_engine_uses_seven_bag_hold_and_tetris_attack(self):
        engine = TetrisBattleEngine(random.Random(23))
        first_bag = [engine.piece_id, *engine.next_piece_ids(6)]
        self.assertEqual(len(set(first_bag)), 7)

        first_piece = engine.piece_id
        engine.hold()
        self.assertEqual(engine.hold_piece_id, first_piece)
        held_replacement = engine.piece_id
        engine.hold()
        self.assertEqual(engine.piece_id, held_replacement)

        engine.board = [[0] * engine.WIDTH for _ in range(engine.HEIGHT)]
        engine.board[-5][0] = 2
        for row in range(engine.HEIGHT - 4, engine.HEIGHT):
            engine.board[row] = [1] * 9 + [0]
        engine.piece_id = 1
        engine.matrix = ((1,), (1,), (1,), (1,))
        engine.x = 9
        engine.y = 0
        self.assertEqual(engine.hard_drop(), 4)
        self.assertEqual(engine.last_attack, 4)
        self.assertEqual(engine.sent_lines, 4)
        self.assertTrue(engine.back_to_back)

    def test_piece_landing_on_bomb_removes_only_that_obstacle_row(self):
        engine = TetrisBattleEngine(random.Random(29))
        engine.board = [[0] * engine.WIDTH for _ in range(engine.HEIGHT)]
        engine.board[-1] = [8] * engine.WIDTH
        engine.board[-1][2] = 10
        engine.board[-2] = [8] * engine.WIDTH
        engine.board[-2][4] = 10
        engine.piece_id = 2
        engine.matrix = ((1,),)
        engine.x = 4
        engine.y = 0

        engine.hard_drop()

        remaining_obstacles = [
            value
            for row in engine.board
            for value in row
            if value in {8, 10}
        ]
        self.assertEqual(len(remaining_obstacles), engine.WIDTH)
        self.assertEqual(remaining_obstacles.count(10), 1)
        self.assertEqual(remaining_obstacles.count(8), engine.WIDTH - 1)

    def test_consecutive_line_or_bomb_clears_send_matching_combo_rows(self):
        host = TetrisHost(random_source=random.Random(31))
        try:
            host.start_match()
            engine = host.left

            engine.board = [[0] * engine.WIDTH for _ in range(engine.HEIGHT)]
            engine.board[-2][0] = 2
            engine.board[-1] = [1] * 9 + [0]
            engine.piece_id = 1
            engine.matrix = ((1,),)
            engine.x = 9
            engine.y = 0
            host.apply_action("left", "hard")
            self.assertEqual(engine.combo, 1)
            self.assertEqual(engine.last_attack, 0)

            engine.board = [[0] * engine.WIDTH for _ in range(engine.HEIGHT)]
            engine.board[-1] = [8] * engine.WIDTH
            engine.board[-1][4] = 10
            engine.piece_id = 2
            engine.matrix = ((1,),)
            engine.x = 4
            engine.y = 0
            host.apply_action("left", "hard")

            self.assertEqual(engine.combo, 2)
            self.assertEqual(engine.last_attack, 2)
            self.assertEqual(engine.sent_lines, 2)
            self.assertEqual(
                host.right.pending_garbage, 2,

            )

            engine.board = [[0] * engine.WIDTH for _ in range(engine.HEIGHT)]
            engine.piece_id = 2
            engine.matrix = ((1,),)
            engine.x = 4
            engine.y = 0
            host.apply_action("left", "hard")
            self.assertEqual(engine.combo, 0)
        finally:
            host.close()
            host.deleteLater()

    def test_combo_animation_starts_at_two(self):
        board = TetrisBattleBoard()
        board.show()
        board.set_state({"status": "playing", "left_combo": 1})
        self.assertNotIn("left", board._combo_animations)

        board.set_state({"status": "playing", "left_combo": 2})
        self.assertEqual(board._combo_animations["left"][0], 2)
        self.assertTrue(board.combo_animation_timer.isActive())
        board.combo_animation_timer.stop()
        board.deleteLater()

    def test_hold_piece_is_drawn_as_a_shape(self):
        board = TetrisBattleBoard()
        board.resize(560, 400)
        empty = [[0] * 10 for _ in range(20)]
        board.set_state(
            {
                "status": "playing",
                "left_name": "甲",
                "right_name": "乙",
                "left_board": empty,
                "right_board": empty,
                "left_hold": 1,
                "right_hold": 0,
                "left_next": [],
                "right_next": [],
            }
        )
        image = QImage(board.size(), QImage.Format_ARGB32)
        image.fill(0)
        board.render(image)
        hold_color = board.COLORS[1].rgb()
        matching_pixels = sum(
            image.pixel(x, y) == hold_color
            for y in range(image.height())
            for x in range(image.width())
        )
        board.deleteLater()

        self.assertGreater(matching_pixels, 0)

    def test_rank_unlocks_three_six_or_all_nine_bonus_skins(self):
        self.assertEqual(len(TETRIS_SKINS), 10)
        self.assertEqual(len({skin.skin_id for skin in TETRIS_SKINS}), 10)
        self.assertEqual(len(unlocked_tetris_skins(None)), 1)
        self.assertEqual(len(unlocked_tetris_skins(3)), 4)
        self.assertEqual(len(unlocked_tetris_skins(2)), 7)
        self.assertEqual(len(unlocked_tetris_skins(1)), 10)

    def test_lobby_skin_selector_uses_ranking_access(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            page = TetrisGamePage(
                lambda: None,
                room_directory=TetrisRoomDirectory(TetrisRoomRegistry(root / "registry")),
                chat_store=ChatRoomStore(root / "chat"),
                profile_store=ChatProfileStore(root / "profile.json"),
            )
            try:
                page._apply_skin_access({"rank": 3})
                self.assertEqual(page.skin_selector.count(), 4)
                page._apply_skin_access({"rank": 2})
                self.assertEqual(page.skin_selector.count(), 7)
                page._apply_skin_access({"rank": 1})
                self.assertEqual(page.skin_selector.count(), 10)
                for index in range(page.skin_selector.count()):
                    page.skin_selector.setCurrentIndex(index)
                    image = QImage(page.skin_preview.size(), QImage.Format_ARGB32)
                    image.fill(0)
                    page.skin_preview.render(image)
                    self.assertFalse(image.isNull())
            finally:
                page.shutdown()
                page.deleteLater()

    def test_two_minute_battle_uses_ko_as_first_tiebreak(self):
        host = TetrisHost(random_source=random.Random(31))
        try:
            host.start_match()
            host.left.ko_count = 2
            host.right.ko_count = 1
            host.remaining_ms = 0
            host._finish_if_needed()
            self.assertEqual(host.status, "finished")
            self.assertEqual(host.winner, "left")
            self.assertEqual(host.winner_reason, "KO")
        finally:
            host.close()
            host.deleteLater()

    def test_room_registry_lists_full_room_but_ui_disables_it(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            registry = TetrisRoomRegistry(root / "registry")
            open_room = TetrisRoom("open", "127.0.0.1:1", "甲", "A", 1, "waiting")
            full_room = TetrisRoom("full", "127.0.0.1:2", "乙", "B", 2, "playing")
            registry.publish(open_room)
            registry.publish(full_room)
            self.assertEqual(len(registry.load_rooms()), 2)

            profile = ChatProfileStore(root / "profile.json")
            profile.save_nickname("測試者")
            page = TetrisGamePage(
                lambda: None,
                room_directory=TetrisRoomDirectory(registry),
                chat_store=ChatRoomStore(root / "chat"),
                profile_store=profile,
            )
            try:
                page._update_room_list([open_room, full_room])
                self.assertTrue(page.room_list.item(0).flags() & Qt.ItemIsEnabled)
                self.assertFalse(page.room_list.item(1).flags() & Qt.ItemIsEnabled)
            finally:
                page.shutdown()
                page.deleteLater()

    def test_tetris_cpu_difficulty_changes_decision_interval(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile = ChatProfileStore(root / "profile.json")
            profile.save_nickname("測試者")
            page = TetrisGamePage(
                lambda: None,
                room_directory=TetrisRoomDirectory(TetrisRoomRegistry(root / "registry")),
                chat_store=ChatRoomStore(root / "chat"),
                profile_store=profile,
            )
            try:
                page.player_name.setText("測試者")
                page.ai_difficulty.setCurrentIndex(0)
                page.start_cpu_match()
                page.cpu_timer.stop()
                easy_interval = page.cpu_timer.interval()
                page.shutdown_match()
                page.ai_difficulty.setCurrentIndex(2)
                page.start_cpu_match()
                page.cpu_timer.stop()
                hard_interval = page.cpu_timer.interval()
                self.assertGreater(easy_interval, hard_interval)
            finally:
                page.shutdown()
                page.deleteLater()

    def test_tetris_lobby_disables_match_actions_without_chat_nickname(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            page = TetrisGamePage(
                lambda: None,
                room_directory=TetrisRoomDirectory(TetrisRoomRegistry(root / "registry")),
                chat_store=ChatRoomStore(root / "chat"),
                profile_store=ChatProfileStore(root / "profile.json"),
            )
            try:
                page.prepare_lobby()
                self.assertFalse(page.create_button.isEnabled())
                self.assertFalse(page.cpu_button.isEnabled())
                self.assertFalse(page.room_list.isEnabled())
            finally:
                page.shutdown()
                page.deleteLater()

    def test_host_and_client_join_and_sync_board_state(self):
        host = TetrisHost(random_source=random.Random(7))
        client = TetrisClient()
        guests = []
        states = []
        host.guest_joined.connect(guests.append)
        client.state_changed.connect(states.append)
        try:
            host.create_room("房主", port=0, skin_id="galaxy")
            client.connect_to_room(
                f"127.0.0.1:{host.server.serverPort()}",
                "加入者",
                skin_id="sakura",
            )
            self.assertTrue(self.wait_until(lambda: guests == ["加入者"]))
            self.assertTrue(self.wait_until(lambda: states and states[-1]["status"] == "ready"))
            self.assertFalse(host.timer.isActive())
            self.assertTrue(host.start_countdown())
            self.assertTrue(
                self.wait_until(
                    lambda: any(state.get("status") == "playing" for state in states),
                    timeout=4.0,
                )
            )
            countdown = [state["countdown"] for state in states if state["status"] == "countdown"]
            self.assertEqual(countdown, [3, 2, 1])
            original_x = host.right.x
            client.send_action("left")
            self.assertTrue(self.wait_until(lambda: host.right.x != original_x))
            self.assertEqual(states[-1]["left_skin"], "galaxy")
            self.assertEqual(states[-1]["right_skin"], "sakura")
        finally:
            client.disconnect()
            host.close()
            client.deleteLater()
            host.deleteLater()

    def test_hidden_main_window_can_open_tetris_and_return_home(self):
        window = MainWindow()
        try:
            window.open_tetris_game()
            self.assertIs(window.currentWidget(), window.tetris_page)
            window.go_home()
            self.assertIs(window.currentWidget(), window.home_page)
        finally:
            window.close()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
