import os
import random
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtNetwork import QNetworkProxy
from PySide6.QtWidgets import QApplication

from app.features.chat_room.store import ChatProfileStore, ChatRoomStore
from app.features.pong.network import (
    PongClient,
    PongEngine,
    PongHost,
    PongRoom,
    PongRoomDirectory,
    PongRoomRegistry,
)
from app.main_window import MainWindow
from ui.pong_game_page import PongGamePage


class PongGameTests(unittest.TestCase):
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

    def test_engine_bounces_scores_and_declares_first_to_seven_winner(self):
        engine = PongEngine(random_source=random.Random(7))
        engine.reset_match("甲", "乙")
        engine.ball_x = engine.LEFT_X + engine.PADDLE_WIDTH - 2
        engine.ball_y = engine.left_y + 20
        engine.ball_vx = -400
        engine.ball_vy = 0
        engine.step(0.01)
        self.assertGreater(engine.ball_vx, 0)

        engine.left_score = 6
        engine.ball_x = engine.WIDTH + 1
        engine.ball_vx = 400
        engine.step(0.01)
        self.assertEqual(engine.left_score, 7)
        self.assertEqual(engine.status, "finished")
        self.assertEqual(engine.winner, "left")

    def test_engine_spawns_and_applies_all_three_powerups(self):
        engine = PongEngine(random_source=random.Random(11))
        engine.reset_match("甲", "乙")

        engine.powerup_spawn_remaining = 0
        engine.step(0)
        self.assertIn(engine.powerup_type, engine.POWERUP_TYPES)
        self.assertAlmostEqual(
            engine.powerup_x,
            (engine.WIDTH - engine.POWERUP_SIZE) / 2,
        )

        engine.powerup_type = "speed"
        engine.powerup_x = engine.ball_x
        engine.powerup_y = engine.ball_y
        old_speed = (engine.ball_vx ** 2 + engine.ball_vy ** 2) ** 0.5
        engine.step(0)
        new_speed = (engine.ball_vx ** 2 + engine.ball_vy ** 2) ** 0.5
        self.assertGreater(new_speed, old_speed)

        engine.reset_match("甲", "乙")
        engine.powerup_type = "giant"
        engine.powerup_x = engine.ball_x
        engine.powerup_y = engine.ball_y
        engine.step(0)
        self.assertEqual(engine.ball_scale, 5.0)
        for _ in range(81):
            engine.step(0.05)
        self.assertEqual(engine.ball_scale, 1.0)

        engine.reset_match("甲", "乙")
        engine.left_score = 1
        engine.right_score = 2
        engine.powerup_type = "swap"
        engine.powerup_x = engine.ball_x
        engine.powerup_y = engine.ball_y
        engine.step(0)
        self.assertGreater(engine.swap_countdown, 0)
        for _ in range(11):
            engine.step(0.05)
        self.assertTrue(engine.controls_swapped)
        self.assertEqual((engine.left_name, engine.right_name), ("乙", "甲"))
        self.assertEqual((engine.left_score, engine.right_score), (2, 1))

    def test_engine_keeps_two_powerups_and_applies_ghost_ball(self):
        engine = PongEngine(random_source=random.Random(23))
        engine.reset_match("甲", "乙")
        engine._spawn_powerup()
        engine._spawn_powerup()
        engine._spawn_powerup()
        self.assertEqual(len(engine.powerups), 2)
        engine.powerups = [{
            "type": "ghost", "x": engine.ball_x, "y": engine.ball_y,
        }]
        engine.step(0)
        self.assertGreater(engine.ghost_remaining, 0)
        self.assertTrue(engine.to_state()["ghost_remaining"] > 0)

    def test_spin_powerup_orbits_while_forward_trajectory_continues(self):
        engine = PongEngine(random_source=random.Random(31))
        engine.reset_match("甲", "乙")
        engine.ball_x = 410.0
        engine.ball_y = 210.0
        engine.ball_vx = 300.0
        engine.ball_vy = 0.0
        engine.powerups = [{
            "type": "spin",
            "x": engine.ball_x,
            "y": engine.ball_y,
        }]
        engine.step(0)
        start_x, start_y = engine.ball_x, engine.ball_y
        engine.step(0.05)
        self.assertGreater(engine.ball_x, start_x)
        self.assertNotEqual(engine.ball_y, start_y)
        state = engine.to_state()
        self.assertGreater(state["spin_remaining"], 0)
        self.assertIn("spin", engine.POWERUP_TYPES)

    def test_pong_lobby_exposes_three_cpu_difficulties(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile = ChatProfileStore(root / "profile.json")
            profile.save_nickname("測試玩家")
            page = PongGamePage(
                lambda: None,
                room_directory=PongRoomDirectory(PongRoomRegistry(root / "rooms")),
                chat_store=ChatRoomStore(root / "chat"),
                profile_store=profile,
            )
            try:
                self.assertEqual(
                    [page.ai_difficulty.itemData(i) for i in range(3)],
                    ["easy", "normal", "hard"],
                )
            finally:
                page.shutdown()
                page.deleteLater()

    def test_shared_room_registry_lists_and_removes_room(self):
        with TemporaryDirectory() as temporary_directory:
            registry = PongRoomRegistry(Path(temporary_directory))
            room = PongRoom(
                room_id="room_one",
                room_code="127.0.0.1:28570",
                host_name="房主甲",
                computer_name="C1972",
                player_count=1,
                status="waiting",
            )
            registry.publish(room)
            rooms = registry.load_rooms()
            self.assertEqual(len(rooms), 1)
            self.assertEqual(rooms[0].host_name, "房主甲")
            registry.publish(
                PongRoom(**{**room.__dict__, "player_count": 2, "status": "playing"})
            )
            self.assertEqual(registry.load_rooms()[0].player_count, 2)
            registry.remove(room.room_id)
            self.assertEqual(registry.load_rooms(), [])

    def test_host_and_client_complete_room_join_and_state_sync_on_loopback(self):
        host = PongHost()
        client = PongClient()
        states = []
        guests = []
        host.state_changed.connect(states.append)
        host.guest_joined.connect(guests.append)
        client_states = []
        client.state_changed.connect(client_states.append)
        try:
            host.create_room("房主", port=0)
            port = host.server.serverPort()
            client.connect_to_room(f"127.0.0.1:{port}", "加入者")
            self.assertTrue(self.wait_until(lambda: guests == ["加入者"]))
            self.assertTrue(
                self.wait_until(
                    lambda: any(state.get("status") == "playing" for state in client_states)
                )
            )
            client.set_direction(-1)
            self.assertTrue(self.wait_until(lambda: host.engine.right_input == -1))
            self.assertTrue(host.timer.isActive())
        finally:
            client.disconnect()
            host.close()
            client.deleteLater()
            host.deleteLater()

    def test_room_advertises_creator_selected_winning_score(self):
        room = PongRoom(
            "custom", "127.0.0.1:28570", "甲", "PC-A", 1, "waiting",
            winning_score=12,
        )
        restored = PongRoom.from_payload(room.to_payload())
        self.assertEqual(restored.winning_score, 12)

        host = PongHost()
        try:
            host.create_room("甲", port=0, winning_score=12)
            self.assertEqual(host.engine.winning_score, 12)
            self.assertEqual(host.engine.to_state()["winning_score"], 12)
        finally:
            host.close()
            host.deleteLater()

    def test_cpu_match_uses_lobby_winning_score_and_delayed_imperfect_ai(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile_store = ChatProfileStore(root / "profile.json")
            profile_store.save_nickname("測試玩家")
            page = PongGamePage(
                lambda: None,
                room_directory=PongRoomDirectory(PongRoomRegistry(root / "rooms")),
                chat_store=ChatRoomStore(root / "chat"),
                profile_store=profile_store,
            )
            try:
                page._refresh_chat_nickname()
                page.winning_score.setValue(11)
                page.start_cpu_match()
                page.local_timer.stop()
                self.assertEqual(page.local_engine.winning_score, 11)
                self.assertIn("11 分", page.game_status.text())

                page.local_engine.ball_y = 0
                page.local_engine.ball_vx = 400
                page._ai_reaction_remaining = 0
                page._advance_cpu_match()
                first_target = page._ai_target_y
                page.local_engine.ball_y = page.local_engine.HEIGHT - 20
                page._advance_cpu_match()
                self.assertEqual(page._ai_target_y, first_target)
                self.assertGreater(page._ai_reaction_remaining, 0)
            finally:
                page.shutdown()
                page.deleteLater()

    def test_lan_game_ignores_an_invalid_global_web_proxy(self):
        previous_proxy = QNetworkProxy.applicationProxy()
        QNetworkProxy.setApplicationProxy(
            QNetworkProxy(
                QNetworkProxy.ProxyType.HttpProxy,
                "127.0.0.1",
                9,
            )
        )
        host = PongHost()
        client = PongClient()
        guests = []
        host.guest_joined.connect(guests.append)
        try:
            self.assertEqual(
                host.server.proxy().type(),
                QNetworkProxy.ProxyType.NoProxy,
            )
            self.assertEqual(
                client.socket.proxy().type(),
                QNetworkProxy.ProxyType.NoProxy,
            )
            host.create_room("房主", port=0)
            client.connect_to_room(
                f"127.0.0.1:{host.server.serverPort()}",
                "加入者",
            )
            self.assertTrue(self.wait_until(lambda: guests == ["加入者"]))
        finally:
            client.disconnect()
            host.close()
            client.deleteLater()
            host.deleteLater()
            QNetworkProxy.setApplicationProxy(previous_proxy)

    def test_host_rejects_a_third_player_when_room_is_full(self):
        host = PongHost()
        first = PongClient()
        third = PongClient()
        guests = []
        errors = []
        host.guest_joined.connect(guests.append)
        third.error_occurred.connect(errors.append)
        try:
            host.create_room("房主", port=0)
            code = f"127.0.0.1:{host.server.serverPort()}"
            first.connect_to_room(code, "第二位")
            self.assertTrue(self.wait_until(lambda: guests == ["第二位"]))
            third.connect_to_room(code, "第三位")
            self.assertTrue(
                self.wait_until(lambda: any("房間已滿" in error for error in errors))
            )
            self.assertEqual(guests, ["第二位"])
        finally:
            third.disconnect()
            first.disconnect()
            host.close()
            third.deleteLater()
            first.deleteLater()
            host.deleteLater()

    def test_room_list_disables_full_rooms_and_uses_chat_nickname(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile_store = ChatProfileStore(root / "profile.json")
            profile_store.save_nickname("聊天室小明")
            directory = PongRoomDirectory(PongRoomRegistry(root / "rooms"))
            page = PongGamePage(
                lambda: None,
                room_directory=directory,
                chat_store=ChatRoomStore(root / "chat"),
                profile_store=profile_store,
            )
            try:
                page._refresh_chat_nickname()
                self.assertEqual(page.player_name.text(), "聊天室小明")
                page._update_room_list(
                    [
                        PongRoom("open", "127.0.0.1:1", "甲", "A", 1, "waiting"),
                        PongRoom("full", "127.0.0.1:2", "乙", "B", 2, "playing"),
                    ]
                )
                self.assertTrue(page.room_list.item(0).flags() & Qt.ItemIsEnabled)
                self.assertFalse(page.room_list.item(1).flags() & Qt.ItemIsEnabled)
            finally:
                page.shutdown()
                page.deleteLater()

    def test_host_publishes_one_chat_notice_with_both_chat_nicknames(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile_store = ChatProfileStore(root / "profile.json")
            profile_store.save_nickname("小明")
            page = PongGamePage(
                lambda: None,
                room_directory=PongRoomDirectory(PongRoomRegistry(root / "rooms")),
                chat_store=ChatRoomStore(root / "chat"),
                profile_store=profile_store,
            )
            try:
                page.mode = "host"
                page.host.engine.reset_match("小明", "小華")
                page.host.engine.status = "finished"
                page.host.engine.winner = "left"
                with patch("ui.pong_game_page.publish_game_announcement") as announce, patch(
                    "ui.pong_game_page.award_multiplayer_victory"
                ):
                    page._match_finished("小明")
                    page._match_finished("小明")
                announce.assert_called_once()
                self.assertEqual(
                    announce.call_args.args[0],
                    '"小明"剛剛在雙人彈球比賽中屌虐了"小華"',
                )
            finally:
                page.shutdown()
                page.deleteLater()

    def test_lobby_modes_and_global_escape_return_home_immediately(self):
        window = MainWindow()
        try:
            window.open_pong_game()
            self.assertIs(window.currentWidget(), window.pong_page)
            window.pong_page.player_name.setText("測試玩家")
            window.pong_page.start_cpu_match()
            self.assertTrue(window.pong_page.local_timer.isActive())

            event = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
            QApplication.sendEvent(window.pong_page.board, event)

            self.assertIs(window.currentWidget(), window.home_page)
            self.assertFalse(window.pong_page.local_timer.isActive())
        finally:
            window.close()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
