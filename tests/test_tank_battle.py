import os
from collections import deque
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.features.chat_room.store import ChatProfileStore, ChatRoomStore
from app.features.tank_battle.network import (
    TANK_MAP_CHOICES,
    TankBattleEngine,
    TankClient,
    TankHost,
    TankRoom,
    TankRoomDirectory,
    TankRoomRegistry,
)
from ui.tank_battle_page import TankBattlePage


class TankBattleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout=2.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def test_engine_has_original_arena_three_lives_and_elimination(self):
        engine = TankBattleEngine(random.Random(3))
        engine.reset(["甲", "乙"], "ffa")
        self.assertEqual(engine.players[0]["lives"], 3)
        self.assertTrue(any(1 in row for row in engine.map))
        self.assertTrue(any(2 in row for row in engine.map))
        self.assertTrue(any(3 in row for row in engine.map))
        target = engine.players[1]
        target["shield"] = 0
        engine._damage(target)
        engine._damage(target)
        engine._damage(target)
        engine._finish_if_needed()
        self.assertFalse(target["alive"])
        self.assertEqual(engine.status, "finished")
        self.assertEqual(engine.winner_name(), "甲")

    def test_team_mode_requires_exactly_four_players(self):
        engine = TankBattleEngine()
        engine.reset(["甲", "乙", "丙"], "teams")
        self.assertEqual(engine.team_mode, "ffa")
        engine.reset(["甲", "乙", "丙", "丁"], "teams")
        self.assertEqual(engine.team_mode, "teams")
        self.assertEqual(engine.players[0]["team"], engine.players[2]["team"])

    def test_tank_lobby_disables_match_actions_without_chat_nickname(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            page = TankBattlePage(
                lambda: None,
                room_directory=TankRoomDirectory(TankRoomRegistry(root / "registry")),
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

    def test_five_original_maps_are_distinct_and_all_spawns_are_connected(self):
        engine = TankBattleEngine(random.Random(17))
        fingerprints = set()
        self.assertEqual(len(TANK_MAP_CHOICES), 5)
        for map_id, _map_name in TANK_MAP_CHOICES:
            engine.reset(["甲", "乙", "丙", "丁"], "ffa", map_id)
            fingerprints.add(tuple(tuple(row) for row in engine.map))
            start = engine.SPAWNS[0]
            pending = deque([start])
            visited = {start}
            while pending:
                x, y = pending.popleft()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    wanted = (x + dx, y + dy)
                    if (
                        0 <= wanted[0] < engine.WIDTH
                        and 0 <= wanted[1] < engine.HEIGHT
                        and engine.map[wanted[1]][wanted[0]] == 0
                        and wanted not in visited
                    ):
                        visited.add(wanted)
                        pending.append(wanted)
            self.assertTrue(all(spawn in visited for spawn in engine.SPAWNS))
            self.assertEqual(engine.to_state()["map_id"], map_id)
        self.assertEqual(len(fingerprints), 5)

    def test_room_payload_and_host_keep_selected_map(self):
        room = TankRoom(
            "mapped", "127.0.0.1:1", "甲", "A", 1, 2, "ffa", "waiting",
            map_id="river_bend",
        )
        self.assertEqual(TankRoom.from_payload(room.to_payload()).map_id, "river_bend")
        host = TankHost()
        try:
            host.create_room("甲", max_players=2, port=0, map_id="steel_maze")
            self.assertEqual(host.map_id, "steel_maze")
            self.assertEqual(host.engine.to_state()["map_id"], "steel_maze")
        finally:
            host.close()
            host.deleteLater()

    def test_tank_lobby_exposes_map_and_cpu_difficulty_choices(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile = ChatProfileStore(root / "profile.json")
            profile.save_nickname("測試者")
            page = TankBattlePage(
                lambda: None,
                room_directory=TankRoomDirectory(TankRoomRegistry(root / "registry")),
                chat_store=ChatRoomStore(root / "chat"),
                profile_store=profile,
            )
            try:
                self.assertEqual(page.map_choice.count(), 5)
                self.assertEqual(
                    [page.ai_difficulty.itemData(i) for i in range(3)],
                    ["easy", "normal", "hard"],
                )
            finally:
                page.shutdown()
                page.deleteLater()

    def test_room_registry_and_full_room_ui(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            registry = TankRoomRegistry(root / "registry")
            open_room = TankRoom("open", "127.0.0.1:1", "甲", "A", 1, 3, "ffa", "waiting")
            full_room = TankRoom("full", "127.0.0.1:2", "乙", "B", 4, 4, "teams", "playing")
            registry.publish(open_room)
            registry.publish(full_room)
            self.assertEqual(len(registry.load_rooms()), 2)
            profile = ChatProfileStore(root / "profile.json")
            profile.save_nickname("測試者")
            page = TankBattlePage(
                lambda: None,
                room_directory=TankRoomDirectory(registry),
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

    def test_two_player_host_and_client_start_match(self):
        host = TankHost()
        client = TankClient()
        states = []
        client.state_changed.connect(states.append)
        try:
            host.create_room("房主", max_players=2, port=0)
            client.connect_to_room(
                f"127.0.0.1:{host.server.serverPort()}",
                "加入者",
            )
            self.assertTrue(
                self.wait_until(
                    lambda: any(state.get("status") == "playing" for state in states)
                )
            )
            self.assertEqual(len(host.engine.players), 2)
        finally:
            client.disconnect()
            host.close()
            client.deleteLater()
            host.deleteLater()


if __name__ == "__main__":
    unittest.main()
