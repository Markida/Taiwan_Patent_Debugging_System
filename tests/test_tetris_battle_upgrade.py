import json
import os
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.features.chat_room.store import ChatProfileStore, ChatRoomStore
from app.features.tetris.network import (
    MAX_PACKET_BYTES, TETRIS_PROTOCOL_VERSION, TetrisBattleEngine, TetrisHost,
    TetrisClient, TetrisRoomDirectory, TetrisRoomRegistry,
)
from ui.tetris_game_page import TetrisBattleBoard, TetrisGamePage


class TetrisBattleUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            QTest.qWait(5)
        return False

    def make_pair(self):
        host, client = TetrisHost(), TetrisClient()
        self.addCleanup(host.deleteLater)
        self.addCleanup(client.deleteLater)
        self.addCleanup(host.close)
        self.addCleanup(client.disconnect)
        states = []
        client.state_changed.connect(states.append)
        host.create_room("房主", port=0)
        client.connect_to_room(f"127.0.0.1:{host.server.serverPort()}", "訪客")
        self.assertTrue(self.wait_until(lambda: states and states[-1]["status"] == "ready"))
        return host, client, states

    def test_incoming_rows_accumulate_and_wait_for_lock_not_move_or_hold(self):
        engine = TetrisBattleEngine(random.Random(9))
        engine.queue_garbage(2)
        engine.queue_garbage(3)
        empty = [row[:] for row in engine.board]
        engine.action("left")
        engine.action("rotate")
        engine.action("hold")
        engine.tick()
        self.assertEqual(engine.pending_garbage, 5)
        self.assertEqual(engine.board, empty)
        engine.action("hard")
        self.assertEqual(engine.pending_garbage, 0)
        for row in engine.board[-5:]:
            self.assertEqual(row.count(8), 9)
            self.assertEqual(row.count(10), 1)
        self.assertFalse(engine.game_over)
        self.assertTrue(engine._valid(engine.matrix, engine.x, engine.y))

    def test_pending_overflow_causes_ko_and_reset_removes_pending_rows(self):
        host = TetrisHost()
        self.addCleanup(host.deleteLater)
        self.addCleanup(host.close)
        host.start_match()
        host.left.queue_garbage(21)
        host.apply_action("left", "hard")
        self.assertEqual(host.right.ko_count, 1)
        self.assertEqual(host.left.pending_garbage, 0)
        self.assertFalse(any(any(row) for row in host.left.board))

    def test_either_player_top_out_awards_only_opponent_and_only_once(self):
        host = TetrisHost()
        self.addCleanup(host.deleteLater)
        self.addCleanup(host.close)
        for defeated_side, scoring_side in (("left", "right"), ("right", "left")):
            with self.subTest(defeated_side=defeated_side):
                host.start_match()
                defeated = getattr(host, defeated_side)
                opponent = getattr(host, scoring_side)
                defeated.queue_garbage(21)
                host.apply_action(defeated_side, "hard")
                self.assertEqual(defeated.ko_count, 0)
                self.assertEqual(opponent.ko_count, 1)
                self.assertEqual(defeated.effects[-1]["kind"], "knocked_out")
                self.assertEqual(opponent.effects[-1]["kind"], "ko")
                host._finish_if_needed()
                self.assertEqual(opponent.ko_count, 1)
                state = host.to_state()
                self.assertEqual(state[f"{defeated_side}_kos"], 0)
                self.assertEqual(state[f"{scoring_side}_kos"], 1)

    def test_simultaneous_top_out_awards_each_opponent_once(self):
        host = TetrisHost()
        self.addCleanup(host.deleteLater)
        self.addCleanup(host.close)
        host.start_match()
        host.left.game_over = host.right.game_over = True
        host._finish_if_needed()
        self.assertEqual((host.left.ko_count, host.right.ko_count), (1, 1))
        self.assertFalse(host.left.game_over or host.right.game_over)
        host._finish_if_needed()
        self.assertEqual((host.left.ko_count, host.right.ko_count), (1, 1))

    def test_five_losses_award_match_to_the_opponent(self):
        host = TetrisHost()
        self.addCleanup(host.deleteLater)
        self.addCleanup(host.close)
        for losing_side, winner in (("left", "right"), ("right", "left")):
            with self.subTest(losing_side=losing_side):
                host.start_match()
                for _ in range(5):
                    getattr(host, losing_side).game_over = True
                    host._finish_if_needed()
                self.assertEqual(host.status, "finished")
                self.assertEqual(host.winner, winner)
                self.assertEqual(getattr(host, losing_side).ko_count, 0)
                self.assertEqual(getattr(host, winner).ko_count, 5)

    def test_ko_attribution_and_labels_match_on_host_and_guest(self):
        host, client, states = self.make_pair()
        board = TetrisBattleBoard()
        self.addCleanup(board.deleteLater)
        board.local_side = "right"
        client.state_changed.connect(board.set_state)
        self.assertTrue(host.start_countdown())
        host._countdown_deadline = time.monotonic() - 0.01
        host._advance_countdown()
        host.timer.stop()
        host.right.queue_garbage(21)
        client.send_action("hard")
        self.assertTrue(self.wait_until(lambda: states[-1]["left_kos"] == 1))
        self.assertEqual(states[-1]["right_kos"], 0)
        self.assertIn("KO 對手 0", board.player_score_text("right"))
        self.assertIn("KO 對手 1", board.player_score_text("left"))
        host.left.queue_garbage(21)
        host.apply_action("left", "hard")
        self.assertTrue(self.wait_until(lambda: states[-1]["right_kos"] == 1))
        self.assertEqual(states[-1]["left_kos"], 1)
        self.assertEqual(states[-1]["right_effects"][-1]["kind"], "ko")

    def test_hold_only_emits_effect_on_success_and_queue_stays_correct(self):
        engine = TetrisBattleEngine(random.Random(6))
        original = engine.piece_id
        following = engine.next_piece_ids(6)
        engine.action("hold")
        self.assertEqual(engine.hold_piece_id, original)
        self.assertEqual(engine.piece_id, following[0])
        self.assertEqual(engine.next_piece_ids(), following[1:6])
        self.assertEqual(len(engine.effects), 1)
        self.assertEqual(engine.effects[0]["kind"], "hold")
        engine.action("hold")
        self.assertEqual(len(engine.effects), 1)

    def test_normal_and_bomb_clears_record_distinct_rows_and_real_attacks(self):
        engine = TetrisBattleEngine(random.Random(10))
        engine.board[-2][0] = 2
        engine.board[-1] = [1] * 9 + [0]
        engine.piece_id, engine.matrix, engine.x, engine.y = 1, ((1,),), 9, 0
        engine.action("hard")
        self.assertEqual(engine.effects[-1]["kind"], "clear")
        self.assertEqual(engine.effects[-1]["rows"], [19])
        engine.board = [[0] * 10 for _ in range(20)]
        engine.board[-1] = [8] * 10
        engine.board[-1][4] = 10
        engine.piece_id, engine.matrix, engine.x, engine.y = 2, ((1,),), 4, 0
        engine.action("hard")
        obstacle, attack = engine.effects[-2:]
        self.assertEqual(obstacle["kind"], "obstacle")
        self.assertEqual(obstacle["rows"], [19])
        self.assertEqual(obstacle["bombs"], [[4, 19]])
        self.assertEqual(attack["kind"], "attack")
        self.assertEqual(attack["amount"], 2)
        engine.action("left")
        self.assertEqual(engine.effects[-1], attack)

    def test_guest_cannot_start_or_move_before_host_and_repeat_join_cannot_reset(self):
        host, client, states = self.make_pair()
        before = host.to_state()
        client.send_action("hard")
        client.socket.write((json.dumps({"type": "start"}) + "\n").encode())
        client.socket.write((json.dumps({"type": "join", "protocol": TETRIS_PROTOCOL_VERSION, "name": "重複"}) + "\n").encode())
        QTest.qWait(30)
        self.assertEqual(host.status, "ready")
        self.assertEqual(host.to_state()["right_board"], before["right_board"])
        self.assertEqual(host.guest_name, "訪客")
        self.assertTrue(host.start_countdown())
        self.assertFalse(host.start_countdown())
        for action in ("hard", "hold", "left"):
            host.apply_action("left", action)
            client.send_action(action)
        QTest.qWait(30)
        self.assertEqual(host.left.score, 0)
        self.assertEqual(host.right.score, 0)
        self.assertEqual(host.remaining_ms, 120000)
        self.assertEqual(host.left.hold_piece_id, 0)

    def test_countdown_disconnect_cancels_and_new_guest_requires_start(self):
        host, client, states = self.make_pair()
        self.assertTrue(host.start_countdown())
        client.disconnect()
        self.assertTrue(self.wait_until(lambda: host.status == "waiting"))
        self.assertFalse(host.countdown_timer.isActive())
        self.assertFalse(host.timer.isActive())
        self.assertEqual(host.countdown, 0)
        host._advance_countdown()
        self.assertEqual(host.status, "waiting")
        client.connect_to_room(f"127.0.0.1:{host.server.serverPort()}", "新訪客")
        self.assertTrue(self.wait_until(lambda: host.status == "ready"))
        self.assertFalse(host.timer.isActive())

    def test_host_close_cancels_countdown(self):
        host, client, states = self.make_pair()
        self.assertTrue(host.start_countdown())
        host.close()
        self.assertFalse(host.countdown_timer.isActive())
        self.assertEqual(host.countdown, 0)
        host._advance_countdown()
        self.assertNotEqual(host.status, "playing")

    def test_guest_hold_and_incoming_attacks_sync_to_both_screens(self):
        host, client, states = self.make_pair()
        self.assertTrue(host.start_countdown())
        host._countdown_deadline = time.monotonic() - 0.01
        host._advance_countdown()
        host.timer.stop()
        self.assertTrue(self.wait_until(lambda: states[-1]["status"] == "playing"))
        client.send_action("hold")
        self.assertTrue(self.wait_until(lambda: bool(states[-1]["right_effects"])))
        self.assertEqual(states[-1]["right_effects"][-1]["kind"], "hold")
        self.assertEqual(states[-1]["right_hold"], host.right.hold_piece_id)
        engine = host.left
        engine.board[-5][0] = 2
        for row in range(16, 20):
            engine.board[row] = [1] * 9 + [0]
        engine.piece_id, engine.matrix, engine.x, engine.y = 1, ((1,),) * 4, 9, 0
        host.apply_action("left", "hard")
        self.assertTrue(self.wait_until(lambda: states[-1]["right_incoming"] == 4))
        self.assertEqual(states[-1]["left_effects"][-1]["kind"], "attack")
        self.assertEqual(states[-1]["left_effects"][-1]["amount"], 4)
        self.assertFalse(any(value in {8, 10} for row in host.right.board for value in row))
        client.send_action("hard")
        self.assertTrue(self.wait_until(lambda: states[-1]["right_incoming"] == 0))
        self.assertEqual(sum(value in {8, 10} for row in host.right.board for value in row), 40)

    def test_protocol_mismatch_is_rejected_with_message(self):
        host, client = TetrisHost(), TetrisClient()
        self.addCleanup(host.deleteLater)
        self.addCleanup(client.deleteLater)
        self.addCleanup(host.close)
        self.addCleanup(client.disconnect)
        errors = []
        client.error_occurred.connect(errors.append)
        host.create_room("房主", port=0)
        client.socket.connected.disconnect(client._connected)
        client.socket.connected.connect(lambda: client.socket.write(
            (json.dumps({"type": "join", "protocol": 3}) + "\n").encode()
        ))
        client.socket.connectToHost("127.0.0.1", host.server.serverPort())
        self.assertTrue(self.wait_until(lambda: any("版本不同" in error for error in errors)))
        self.assertFalse(host._guest_joined)
        self.assertEqual(host.status, "waiting")

    def test_effect_snapshots_do_not_replay_and_new_match_resets_ids(self):
        board = TetrisBattleBoard()
        self.addCleanup(board.deleteLater)
        self.addCleanup(board.close)
        board.show()
        state = {
            "match_id": "one", "status": "playing",
            "left_effects": [{"id": 1, "kind": "clear", "rows": [19]}],
        }
        with patch("ui.tetris_game_page.time.monotonic", return_value=100.0):
            board.set_state(state)
            board.set_state(state)
            self.assertEqual(len(board._effects), 1)
            self.assertTrue(board.combo_animation_timer.isActive())
        with patch("ui.tetris_game_page.time.monotonic", return_value=102.0):
            board._advance_combo_animation()
            board.set_state(state)
            self.assertEqual(board._effects, [])
            board.set_state({**state, "match_id": "two"})
            self.assertEqual(len(board._effects), 1)
        board.hide()
        self.assertFalse(board.combo_animation_timer.isActive())

    def test_shift_auto_repeat_is_ignored_and_input_is_blocked_during_countdown(self):
        board = TetrisBattleBoard()
        self.addCleanup(board.deleteLater)
        requested = []
        board.action_requested.connect(requested.append)
        key = QKeyEvent(QEvent.KeyPress, Qt.Key_Shift, Qt.ShiftModifier)
        board.set_state({"status": "countdown", "countdown": 3})
        board.keyPressEvent(key)
        self.assertEqual(requested, [])
        board.set_state({"status": "playing"})
        board.keyPressEvent(key)
        repeat = QKeyEvent(QEvent.KeyPress, Qt.Key_Shift, Qt.ShiftModifier, "", True)
        board.keyPressEvent(repeat)
        self.assertEqual(requested, ["hold"])

    def test_five_next_shapes_and_incoming_meter_fit_small_and_large_boards(self):
        board = TetrisBattleBoard()
        self.addCleanup(board.deleteLater)
        board.set_state({
            "status": "playing", "left_next": [1, 2, 3, 4, 5],
            "right_next": [7, 6, 5, 4, 3], "left_incoming": 7, "right_incoming": 24,
        })
        for width, height in ((560, 400), (900, 500), (1200, 660)):
            board.resize(width, height)
            image = QImage(board.size(), QImage.Format_ARGB32)
            image.fill(0)
            board.render(image)
            layouts = board.battle_layout()
            for side in ("left", "right"):
                layout = layouts[side]
                self.assertLess(layout["well"].right(), layout["meter"].left())
                self.assertLess(layout["meter"].right(), layout["next"].left())
                self.assertLessEqual(layout["next"].right(), width)
                self.assertLessEqual(layout["well"].bottom(), height)
                panel = layout["next"]
                slot_height = (panel.height() - 22) / 5
                for index, piece_id in enumerate(board.state[f"{side}_next"]):
                    top = int(panel.y() + 22 + index * slot_height)
                    pixels = sum(
                        image.pixel(x, y) == board.COLORS[piece_id].rgb()
                        for y in range(top, int(top + slot_height))
                        for x in range(int(panel.left()), int(panel.right()))
                    )
                    self.assertGreater(pixels, 0, (width, side, index))

    def test_all_effects_render_and_stay_bounded_in_network_packets(self):
        host, board = TetrisHost(), TetrisBattleBoard()
        self.addCleanup(host.deleteLater)
        self.addCleanup(host.close)
        self.addCleanup(board.deleteLater)
        host.start_match()
        for engine in (host.left, host.right):
            for _ in range(10):
                engine._record_effect("hold", piece=3, origin=[5, 2])
                engine._record_effect("clear", rows=[16, 17, 18, 19])
                engine._record_effect("obstacle", rows=[19], bombs=[[4, 19]])
                engine._record_effect("attack", rows=[19], amount=8)
            self.assertEqual(len(engine.effects), 16)
        state = host.to_state()
        self.assertLess(len(json.dumps(state).encode()), MAX_PACKET_BYTES)
        with patch("ui.tetris_game_page.time.monotonic", return_value=100):
            board.set_state(state)
        board.resize(1000, 600)
        for now in (100.05, 100.25, 100.45, 100.75):
            with patch("ui.tetris_game_page.time.monotonic", return_value=now):
                image = QImage(board.size(), QImage.Format_ARGB32)
                image.fill(0)
                board.render(image)
                self.assertFalse(image.isNull())

    def test_start_button_is_only_enabled_for_ready_host(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            page = TetrisGamePage(
                lambda: None, chat_store=ChatRoomStore(root / "chat"),
                profile_store=ChatProfileStore(root / "profile.json"),
                room_directory=TetrisRoomDirectory(TetrisRoomRegistry(root / "rooms")),
            )
            try:
                for mode, status, enabled in (
                    ("host", "waiting", False), ("host", "ready", True),
                    ("host", "countdown", False), ("client", "ready", False),
                    ("cpu", "playing", False),
                ):
                    page.mode = mode
                    page._apply_battle_state({"status": status})
                    self.assertEqual(page.start_button.isEnabled(), enabled)
                    if mode != "host":
                        self.assertTrue(page.start_button.isHidden())
            finally:
                page.shutdown()
                page.deleteLater()


if __name__ == "__main__":
    unittest.main()
