import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.features.chat_room.game_ranking import GameRankingStore


class GameRankingTests(unittest.TestCase):
    def test_points_idempotence_and_persistent_top_three(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = GameRankingStore(root)
            store.record_win("甲", "tetris", match_id="t1", user_id="user-a")
            store.record_win("甲", "tetris", match_id="t1", user_id="user-a")
            store.record_win("乙", "pong", match_id="p1", user_id="user-b")
            store.record_win("丙", "snake", match_id="s1", user_id="user-c")
            store.record_win("丁", "bulls_and_cows", match_id="b1", user_id="user-d")

            entries = store.leaderboard(3)
            self.assertEqual([entry.nickname for entry in entries[:2]], ["甲", "乙"])
            self.assertEqual([entry.points for entry in entries], [3, 2, 1])
            self.assertEqual(entries[0].wins, 1)

    def test_latest_shared_profile_changes_name_and_avatar_without_resetting_points(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = GameRankingStore(root)
            store.record_win("舊暱稱", "tetris", match_id="match", user_id="stable-user")
            profiles = root / "data" / "profiles"
            profiles.mkdir(parents=True)
            (profiles / "stable-user.json").write_text(
                json.dumps(
                    {
                        "user_id": "stable-user",
                        "identity_computer_name": "PC-001",
                        "nickname": "新暱稱",
                        "avatar_relative_path": "data/avatars/stable-user/new.jpg",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            entry = store.leaderboard(1)[0]
            self.assertEqual(entry.nickname, "新暱稱")
            self.assertEqual(entry.points, 3)
            self.assertEqual(entry.avatar_relative_path, "data/avatars/stable-user/new.jpg")

    def test_legacy_shared_profile_cannot_make_ranking_name_flicker(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = GameRankingStore(root)
            store.record_win("穩定名稱", "pong", match_id="older", user_id="copied-user")

            profiles = root / "data" / "profiles"
            profiles.mkdir(parents=True)
            profile_path = profiles / "copied-user.json"
            for overwritten_name in ("另一台電腦", "第三台電腦"):
                profile_path.write_text(
                    json.dumps(
                        {
                            "user_id": "copied-user",
                            "nickname": overwritten_name,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(store.leaderboard(1)[0].nickname, "穩定名稱")

    def test_latest_event_name_is_independent_of_directory_iteration_order(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            events = root / "data" / "game_ranking" / "events"
            events.mkdir(parents=True)
            common = {
                "schema_version": 1,
                "user_id": "stable-user",
                "player_key": "stable-user",
                "game_id": "pong",
                "points": 2,
            }
            # Deliberately create the newer event first.  Reading whichever
            # file the SMB share returns last must not decide the displayed name.
            (events / "newer.json").write_text(
                json.dumps(
                    {
                        **common,
                        "event_id": "newer",
                        "nickname": "新名稱",
                        "created_at_utc": "2026-01-02T00:00:00+00:00",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (events / "older.json").write_text(
                json.dumps(
                    {
                        **common,
                        "event_id": "older",
                        "nickname": "舊名稱",
                        "created_at_utc": "2026-01-01T00:00:00+00:00",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            entry = GameRankingStore(root).leaderboard(1)[0]
            self.assertEqual(entry.nickname, "新名稱")
            self.assertEqual(entry.points, 4)


if __name__ == "__main__":
    unittest.main()
