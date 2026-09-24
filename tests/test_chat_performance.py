"""Regression tests for bounded chat I/O and nonblocking worker teardown."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.features.chat_room.game_ranking import GameRankingStore
from app.features.chat_room.store import ChatProfileStore, ChatRoomStore
from ui.chat_room_page import ChatMediaLoader, ChatPoller, ChatRoomPage


class ChatPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_unchanged_ranking_events_are_not_reopened_and_changes_are_detected(self):
        with TemporaryDirectory() as temporary:
            store = GameRankingStore(Path(temporary))
            # Ranking identity is the computer, never the display nickname.
            store.record_win("甲", "pong", match_id="one", computer_name="CP2856")
            self.assertEqual(store.leaderboard()[0].points, 2)
            with patch.object(Path, "read_text", side_effect=AssertionError("Unexpected file read")):
                self.assertEqual(store.leaderboard()[0].points, 2)

            store.record_win("甲", "tetris", match_id="two", computer_name="CP2856")
            self.assertEqual(store.leaderboard()[0].points, 5)
            event = next(store.events_root.glob("*.json"))
            payload = json.loads(event.read_text(encoding="utf-8"))
            payload["points"] = 100
            event.write_text(json.dumps(payload), encoding="utf-8")
            self.assertGreaterEqual(store.leaderboard()[0].points, 100)
            event.unlink()
            self.assertEqual(store.leaderboard()[0].wins, 1)

    def test_active_message_poll_uses_cache_but_full_reload_rereads(self):
        with TemporaryDirectory() as temporary:
            store = ChatRoomStore(Path(temporary))
            original = store.send_message("甲", "原始內容")
            store.load_recent_messages()
            with patch.object(store, "_read_message", wraps=store._read_message) as read:
                self.assertEqual(store.load_active_messages(), [original])
                self.assertEqual(store.load_active_messages(), [original])
                read.assert_not_called()
                store.load_recent_messages()
                self.assertEqual(read.call_count, 1)

    def test_profile_is_not_rewritten_on_every_heartbeat_but_changes_publish_immediately(self):
        with TemporaryDirectory() as temporary:
            store = ChatRoomStore(Path(temporary))
            store.update_presence("甲", "user_123", "session")
            reads = []
            write_text = Path.write_text

            def track_write(path, *args, **kwargs):
                reads.append(path)
                return write_text(path, *args, **kwargs)

            with patch.object(Path, "write_text", track_write):
                store.update_presence("甲", "user_123", "session")
                self.assertTrue(any(path.parent == store.presence_root for path in reads))
                self.assertFalse(any(path.parent == store.shared_profiles_root for path in reads))
                reads.clear()
                store.update_presence("新的暱稱", "user_123", "session")
                self.assertTrue(any(path.parent == store.shared_profiles_root for path in reads))

    def test_empty_visible_message_list_does_not_scan_historical_reactions(self):
        with TemporaryDirectory() as temporary:
            store = ChatRoomStore(Path(temporary))
            store.ensure_available()
            with patch.object(Path, "iterdir", side_effect=AssertionError("Historical scan")):
                self.assertEqual(store.load_reactions([]), [])

    def test_background_monitor_still_loads_messages_without_auxiliary_share_reads(self):
        with TemporaryDirectory() as temporary:
            store = ChatRoomStore(Path(temporary))
            poller = ChatPoller(store, interval=0.1, ranking_store=Mock())
            poller.set_details_enabled(False)
            loaded = threading.Event()
            store.load_recent_messages = Mock(side_effect=lambda **_kwargs: loaded.set() or [])
            store.load_active_messages = Mock(return_value=[])
            store.load_active_presence = Mock()
            store.load_read_receipts = Mock()
            store.load_reactions = Mock()
            try:
                poller.start()
                self.assertTrue(loaded.wait(1))
            finally:
                poller.stop()
            store.load_active_presence.assert_not_called()
            store.load_read_receipts.assert_not_called()
            store.load_reactions.assert_not_called()
            poller.ranking_store.leaderboard.assert_not_called()

    def test_stalled_read_does_not_spawn_duplicate_worker_on_restart(self):
        with TemporaryDirectory() as temporary:
            store = ChatRoomStore(Path(temporary))
            entered, release = threading.Event(), threading.Event()

            def slow_read(**_kwargs):
                entered.set()
                release.wait(2)
                return []

            store.load_recent_messages = Mock(side_effect=slow_read)
            store.load_active_presence = Mock()
            poller = ChatPoller(store)
            poller.set_details_enabled(False)
            try:
                poller.start()
                self.assertTrue(entered.wait(1))
                first_worker = poller._thread
                started = time.perf_counter()
                poller.stop(wait_timeout=0)
                poller.start()
                self.assertLess(time.perf_counter() - started, 0.1)
                self.assertIs(poller._thread, first_worker)
                self.assertEqual(store.load_recent_messages.call_count, 1)
                release.set()
                first_worker.join(1)
                deadline = time.monotonic() + 1
                while store.load_recent_messages.call_count < 2 and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertIsNot(poller._thread, first_worker)
                self.assertEqual(store.load_recent_messages.call_count, 2)
            finally:
                release.set()
                poller.stop()
            store.load_active_presence.assert_not_called()

    def test_media_loader_is_lazy_and_stops_without_waiting_for_stalled_read(self):
        loader = ChatMediaLoader()
        self.assertIsNone(loader._thread)
        entered, release = threading.Event(), threading.Event()
        received = []

        def slow_image(*_args):
            entered.set()
            release.wait(2)
            return QImage(8, 8, QImage.Format_RGB32)

        loader._read_thumbnail = slow_image
        loader.image_ready.connect(lambda *_args: received.append(True))
        try:
            loader.request(Path("slow-share.png"), QSize(20, 20))
            self.assertTrue(entered.wait(1))
            loader.request(Path("queued-share.png"), QSize(20, 20))
            started = time.perf_counter()
            loader.stop()
            self.assertLess(time.perf_counter() - started, 0.1)
            self.assertTrue(loader._queue.empty())
            self.assertEqual(loader.request(Path("late.png"), QSize(20, 20)), "")
        finally:
            release.set()
            loader._thread.join(1)
            self.app.processEvents()
        self.assertFalse(loader._thread.is_alive())
        self.assertEqual(received, [])

    def test_many_media_completions_coalesce_into_one_history_render(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ChatRoomStore(root / "chat")
            page = ChatRoomPage(
                lambda: None,
                lambda: None,
                chat_store=store,
                profile_store=ChatProfileStore(root / "profile.json"),
            )
            page._messages_by_id["test"] = object()
            image = QImage(8, 8, QImage.Format_RGB32)
            try:
                with patch.object(page, "_render_messages") as render:
                    for index in range(50):
                        page._on_media_ready(f"test:{index}", image)
                    render.assert_not_called()
                    deadline = time.monotonic() + 1
                    while not render.called and time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(0.005)
                    render.assert_called_once()
            finally:
                page.shutdown()
                page.deleteLater()


if __name__ == "__main__":
    unittest.main()
