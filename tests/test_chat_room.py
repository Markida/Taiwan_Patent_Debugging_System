import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QDropEvent, QImage, QKeyEvent
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRect, QSize, Qt, QUrl
from PySide6.QtWidgets import QApplication, QSizePolicy, QStackedWidget

from app.features.chat_room.store import (
    ChatProfileStore,
    ChatRoomError,
    ChatRoomStore,
    default_chat_room_root,
    default_chat_profile_path,
    publish_game_announcement,
    publish_game_invitation,
)
from ui.chat_room_page import (
    ChatInputEdit,
    ChatMediaLoader,
    ChatPoller,
    ChatRoomPage,
)


class ChatRoomStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_default_company_chat_root_uses_documents_share(self):
        with patch.dict(os.environ, {"SAINT_ISLAND_CHAT_ROOM_ROOT": ""}):
            self.assertEqual(
                str(default_chat_room_root()),
                r"\\CPC2856\Documents\features\chat_room",
            )

    def test_rapid_sends_keep_increasing_timestamps_if_wall_clock_moves_back(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "chat"
            later = datetime.now(timezone.utc)
            earlier = later - timedelta(microseconds=100)
            with (
                patch("app.features.chat_room.store._last_message_created_at", None),
                patch("app.features.chat_room.store.datetime") as mocked_datetime,
            ):
                mocked_datetime.now.side_effect = [later, earlier]
                first = ChatRoomStore(root).send_message("甲", "第一則")
                second = ChatRoomStore(root).send_message("甲", "第二則")

            self.assertGreater(second.created_at_utc, first.created_at_utc)
            self.assertEqual(
                [message.text for message in ChatRoomStore(root).load_recent_messages()],
                ["第一則", "第二則"],
            )

    def test_game_announcement_is_published_asynchronously(self):
        with TemporaryDirectory() as temporary_directory:
            store = ChatRoomStore(Path(temporary_directory) / "chat")
            thread = publish_game_announcement(
                '"甲"剛剛在雙人彈球比賽中屌虐了"乙"',
                store=store,
            )
            thread.join(timeout=2)
            messages = store.load_recent_messages()
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].sender, "遊戲戰報")
            self.assertIn("屌虐了", messages[0].text)

    def test_reply_reaction_and_game_invitation_are_durable(self):
        with TemporaryDirectory() as temporary_directory:
            store = ChatRoomStore(Path(temporary_directory) / "chat")
            original = store.send_message(
                "甲", "原始訊息", user_id="user_00000001"
            )
            reply = store.send_message(
                "乙",
                "收到",
                user_id="user_00000002",
                reply_to=original,
            )
            reaction = store.set_reaction(
                original.message_id,
                "user_00000002",
                "乙",
                "👍",
            )
            invitation_thread = publish_game_invitation(
                "pong",
                "雙人彈球",
                {"room_id": "room_1", "room_code": "127.0.0.1:1"},
                "甲",
                store=store,
            )
            invitation_thread.join(timeout=2)
            messages = store.load_recent_messages()
            restored_reply = next(
                message for message in messages
                if message.message_id == reply.message_id
            )
            invitation = next(
                message for message in messages
                if message.message_type == "game_invitation"
            )
            self.assertEqual(restored_reply.reply_to_message_id, original.message_id)
            self.assertEqual(restored_reply.reply_sender, "甲")
            self.assertEqual(store.load_reactions()[0], reaction)
            self.assertEqual(invitation.metadata["game_id"], "pong")

    def test_profile_remembers_last_nickname_in_nested_feature_path(self):
        with TemporaryDirectory() as temporary_directory:
            path = (
                Path(temporary_directory)
                / "app/features/chat_room/data/settings/chat_profile.json"
            )
            store = ChatProfileStore(path)

            self.assertEqual(store.load_nickname(), "")
            self.assertEqual(store.save_nickname(" 小明 "), "小明")
            avatar_source = Path(temporary_directory) / "avatar.png"
            avatar = QImage(32, 32, QImage.Format_RGB32)
            avatar.fill(QColor("#2563eb"))
            self.assertTrue(avatar.save(str(avatar_source)))
            saved_avatar = store.save_avatar(avatar_source)
            self.assertEqual(ChatProfileStore(path).load_nickname(), "小明")
            profile = ChatProfileStore(path).load_profile()
            self.assertEqual(profile.avatar_path, saved_avatar)
            self.assertTrue(Path(profile.avatar_path).is_file())
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["nickname"],
                "小明",
            )
            self.assertFalse(store.save_notifications_enabled(False))
            self.assertEqual(store.save_last_read_message_id("message-001"), "message-001")
            reopened_profile = ChatProfileStore(path).load_profile()
            self.assertFalse(reopened_profile.notifications_enabled)
            self.assertEqual(reopened_profile.last_read_message_id, "message-001")

        default_parts = [part.casefold() for part in default_chat_profile_path().parts]
        self.assertIn("features", default_parts)
        self.assertIn("chat_room", default_parts)
        self.assertTrue(default_parts[-1].endswith("chat_profile.json"))

    def test_legacy_copied_profile_gets_a_stable_per_computer_identity(self):
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "chat_profile.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "user_id": "shared_user_0001",
                        "nickname": "測試者",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            store = ChatProfileStore(path)
            with patch(
                "app.features.chat_room.store.socket.gethostname",
                return_value="COMPANY-PC-A",
            ):
                first = store.load_profile().user_id
                self.assertEqual(store.load_profile().user_id, first)
                store.save_nickname("測試者")
            with patch(
                "app.features.chat_room.store.socket.gethostname",
                return_value="COMPANY-PC-B",
            ):
                second = store.load_profile().user_id

            self.assertNotEqual(first, "shared_user_0001")
            self.assertNotEqual(first, second)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["identity_computer_name"], "COMPANY-PC-A")

    def test_composer_emoji_button_inserts_at_the_current_cursor(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            page = ChatRoomPage(
                lambda: None,
                lambda: None,
                chat_store=ChatRoomStore(root / "chat"),
                profile_store=ChatProfileStore(root / "profile.json"),
            )
            try:
                page.message_input.setPlainText("甲乙")
                cursor = page.message_input.textCursor()
                cursor.setPosition(1)
                page.message_input.setTextCursor(cursor)
                page.message_emoji_buttons[0].click()
                self.assertEqual(page.message_input.toPlainText(), "甲😀乙")
            finally:
                page.shutdown()
                page.deleteLater()

    def test_composer_and_emoji_row_fit_the_960_by_640_window(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            page = ChatRoomPage(
                lambda: None,
                lambda: None,
                chat_store=ChatRoomStore(root / "chat"),
                profile_store=ChatProfileStore(root / "profile.json"),
            )
            host = QStackedWidget()
            try:
                page.setMinimumSize(0, 0)
                page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
                host.addWidget(page)
                host.setFixedSize(960, 640)
                host.show()
                self.app.processEvents()

                self.assertEqual((page.width(), page.height()), (960, 640))
                for widget in [page.message_input, *page.message_emoji_buttons]:
                    top_left = widget.mapTo(page, QPoint(0, 0))
                    bounds = QRect(top_left, widget.size())
                    self.assertTrue(page.rect().contains(bounds), widget.objectName())
                self.assertLessEqual(
                    page.message_input.geometry().bottom(),
                    page.height(),
                )
            finally:
                page.shutdown()
                host.close()
                page.deleteLater()
                host.deleteLater()

    def test_two_clients_share_text_and_image_without_overwriting(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "features/chat_room"
            first_client = ChatRoomStore(root)
            second_client = ChatRoomStore(root)
            image_path = Path(temporary_directory) / "樣本.png"
            image = QImage(24, 16, QImage.Format_RGB32)
            image.fill(QColor("#ffffff"))
            self.assertTrue(image.save(str(image_path)))

            first_message = first_client.send_message("甲", "第一則訊息")
            avatar_path = Path(temporary_directory) / "頭貼.png"
            avatar = QImage(32, 32, QImage.Format_RGB32)
            avatar.fill(QColor("#dc2626"))
            self.assertTrue(avatar.save(str(avatar_path)))
            second_message = second_client.send_message(
                "乙",
                "附圖",
                image_path,
                user_id="user_00000002",
                avatar_path=avatar_path,
            )
            messages = ChatRoomStore(root).load_recent_messages()

            self.assertEqual(
                [message.sender for message in messages],
                ["甲", "乙"],
            )
            self.assertNotEqual(first_message.message_id, second_message.message_id)
            attachment = second_client.resolve_attachment(second_message)
            self.assertIsNotNone(attachment)
            self.assertTrue(attachment.is_file())
            self.assertEqual(attachment.read_bytes(), image_path.read_bytes())
            shared_avatar = second_client.resolve_avatar(second_message)
            self.assertIsNotNone(shared_avatar)
            self.assertTrue(shared_avatar.is_file())
            self.assertIn("avatars", shared_avatar.parts)
            message_files = list((root / "data/messages").rglob("*.json"))
            self.assertEqual(len(message_files), 2)
            self.assertTrue(
                all("features" in path.parts and "chat_room" in path.parts for path in message_files)
            )

    def test_invalid_message_and_image_are_rejected(self):
        with TemporaryDirectory() as temporary_directory:
            store = ChatRoomStore(Path(temporary_directory) / "features/chat_room")
            with self.assertRaises(ChatRoomError):
                store.send_message("測試者", "")
            bad_image = Path(temporary_directory) / "bad.exe"
            bad_image.write_bytes(b"not an image")
            with self.assertRaises(ChatRoomError):
                store.send_message("測試者", "", bad_image)

    def test_dropping_image_on_message_input_selects_attachment_not_path_text(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            image_path = root / "dragged.png"
            image = QImage(24, 16, QImage.Format_RGB32)
            image.fill(QColor("#ffffff"))
            self.assertTrue(image.save(str(image_path)))
            page = ChatRoomPage(
                lambda: None,
                lambda: None,
                chat_store=ChatRoomStore(root / "features/chat_room"),
                profile_store=ChatProfileStore(
                    root / "app/features/chat_room/data/settings/chat_profile.json"
                ),
            )
            try:
                mime_data = QMimeData()
                mime_data.setUrls([QUrl.fromLocalFile(str(image_path))])
                event = QDropEvent(
                    QPointF(5, 5),
                    Qt.CopyAction,
                    mime_data,
                    Qt.LeftButton,
                    Qt.NoModifier,
                )

                page.message_input.dropEvent(event)

                self.assertTrue(event.isAccepted())
                self.assertEqual(page._selected_image_path, str(image_path))
                self.assertEqual(page.message_input.toPlainText(), "")
                self.assertIn(image_path.name, page.attachment_label.text())
            finally:
                page.deactivate()
                page.deleteLater()

    def test_enter_sends_and_shift_enter_inserts_a_new_line(self):
        editor = ChatInputEdit()
        requests = []
        editor.send_requested.connect(lambda: requests.append(True))
        try:
            editor.setPlainText("測試")
            editor.keyPressEvent(
                QKeyEvent(
                    QEvent.KeyPress,
                    Qt.Key_Return,
                    Qt.NoModifier,
                    "\r",
                )
            )
            self.assertEqual(requests, [True])
            self.assertEqual(editor.toPlainText(), "測試")

            editor.moveCursor(editor.textCursor().MoveOperation.End)
            editor.keyPressEvent(
                QKeyEvent(
                    QEvent.KeyPress,
                    Qt.Key_Return,
                    Qt.ShiftModifier,
                    "\r",
                )
            )
            self.assertEqual(requests, [True])
            self.assertTrue(editor.toPlainText().endswith("\n"))
        finally:
            editor.deleteLater()

    def test_ctrl_v_stages_clipboard_screenshot_without_inserting_path_text(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            page = ChatRoomPage(
                lambda: None,
                lambda: None,
                chat_store=ChatRoomStore(root / "features/chat_room"),
                profile_store=ChatProfileStore(
                    root / "app/features/chat_room/data/settings/chat_profile.json"
                ),
            )
            screenshot = QImage(48, 32, QImage.Format_RGB32)
            screenshot.fill(QColor("#2563eb"))
            QApplication.clipboard().setImage(screenshot)
            try:
                page.message_input.setPlainText("附上截圖")
                page.message_input.keyPressEvent(
                    QKeyEvent(
                        QEvent.KeyPress,
                        Qt.Key_V,
                        Qt.ControlModifier,
                        "v",
                    )
                )

                staged = Path(page._selected_image_path)
                self.assertTrue(staged.is_file())
                self.assertEqual(staged.suffix.lower(), ".png")
                self.assertEqual(page.message_input.toPlainText(), "附上截圖")
                self.assertIn("螢幕截圖", page.attachment_label.text())

                page.nickname_line.setText("測試者")
                self.assertTrue(page.save_nickname(show_confirmation=False))
                sent = page.chat_store.send_message(
                    "測試者",
                    page.message_input.toPlainText(),
                    staged,
                )
                self.assertEqual(sent.text, "附上截圖")
                self.assertTrue(page.chat_store.resolve_attachment(sent).is_file())
            finally:
                staged_path = Path(page._selected_image_path) if page._selected_image_path else None
                page.shutdown()
                if staged_path is not None:
                    self.assertFalse(staged_path.exists())
                page.deleteLater()

    def test_chat_page_changes_and_persists_nickname_at_any_time(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile = ChatProfileStore(
                root / "app/features/chat_room/data/settings/chat_profile.json"
            )
            page = ChatRoomPage(
                lambda: None,
                lambda: None,
                chat_store=ChatRoomStore(root / "features/chat_room"),
                profile_store=profile,
                poll_interval=0.05,
            )
            try:
                page.nickname_line.setText("第一個暱稱")
                self.assertTrue(page.save_nickname(show_confirmation=False))
                page.nickname_line.setText("第二個暱稱")
                self.assertTrue(page.save_nickname(show_confirmation=False))

                self.assertEqual(
                    ChatProfileStore(profile.path).load_nickname(),
                    "第二個暱稱",
                )
            finally:
                page.deactivate()
                page.deleteLater()

    def test_background_poller_finds_messages_and_can_restart_quickly(self):
        with TemporaryDirectory() as temporary_directory:
            store = ChatRoomStore(
                Path(temporary_directory) / "features/chat_room"
            )
            first = store.send_message("甲", "一秒同步測試")
            poller = ChatPoller(store, interval=0.05)
            received_ids = set()
            poller.messages_received.connect(
                lambda messages: received_ids.update(
                    message.message_id for message in messages
                )
            )

            def wait_for(message_id):
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    self.app.processEvents()
                    if message_id in received_ids:
                        return True
                    time.sleep(0.01)
                return False

            poller.start()
            self.assertTrue(wait_for(first.message_id))
            poller.stop()
            poller.start()
            second = store.send_message("乙", "重新進入後仍會同步")
            self.assertTrue(wait_for(second.message_id))
            poller.stop()

    def test_poller_captures_widget_identity_only_on_calling_thread(self):
        with TemporaryDirectory() as temporary_directory:
            store = ChatRoomStore(
                Path(temporary_directory) / "features/chat_room"
            )
            main_thread_id = threading.get_ident()
            provider_thread_ids = []

            def identity_provider():
                provider_thread_ids.append(threading.get_ident())
                return {
                    "nickname": "測試者",
                    "user_id": "user_00000001",
                    "avatar_path": "",
                }

            poller = ChatPoller(
                store,
                interval=0.05,
                identity_provider=identity_provider,
            )
            poller.start()
            deadline = time.monotonic() + 0.3
            while time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            poller.stop()

            self.assertEqual(provider_thread_ids, [main_thread_id])

    def test_media_loader_reads_and_resizes_image_in_background(self):
        with TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "large.png"
            image = QImage(800, 400, QImage.Format_RGB32)
            image.fill(QColor("#2563eb"))
            self.assertTrue(image.save(str(image_path)))
            loader = ChatMediaLoader()
            received = {}
            callback_thread_ids = []
            loader.image_ready.connect(
                lambda key, thumbnail: (
                    received.update({key: thumbnail}),
                    callback_thread_ids.append(threading.get_ident()),
                )
            )

            key = loader.request(image_path, QSize(200, 200))
            deadline = time.monotonic() + 2.0
            while key not in received and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)

            self.assertIn(key, received)
            self.assertLessEqual(received[key].width(), 200)
            self.assertLessEqual(received[key].height(), 200)
            self.assertEqual(callback_thread_ids, [threading.get_ident()])

    def test_slow_shared_image_does_not_block_message_rendering(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = ChatRoomStore(root / "features/chat_room")
            profile = ChatProfileStore(
                root / "app/features/chat_room/data/settings/chat_profile.json"
            )
            page = ChatRoomPage(
                lambda: None,
                lambda: None,
                chat_store=store,
                profile_store=profile,
            )
            slow_read_started = threading.Event()

            def slow_thumbnail(_path, _maximum_size):
                slow_read_started.set()
                time.sleep(0.3)
                return QImage()

            page.media_loader._read_thumbnail = slow_thumbnail
            message = store.send_message("甲", "附圖")
            message = type(message)(
                **{
                    **message.__dict__,
                    "attachment_relative_path": "data/images/slow.png",
                    "attachment_name": "slow.png",
                }
            )
            try:
                started = time.perf_counter()
                page._append_messages([message])
                elapsed = time.perf_counter() - started

                self.assertLess(elapsed, 0.1)
                self.assertTrue(slow_read_started.wait(0.5))
                self.assertIn("附圖", page.message_view.toPlainText())
            finally:
                page.deactivate()
                page.deleteLater()

    def test_presence_lists_active_people_and_removes_only_matching_session(self):
        with TemporaryDirectory() as temporary_directory:
            store = ChatRoomStore(
                Path(temporary_directory) / "features/chat_room"
            )
            first = store.update_presence(
                "甲",
                "user_00000001",
                "session_one",
            )
            second = store.update_presence(
                "乙",
                "user_00000002",
                "session_two",
            )

            self.assertEqual(
                [person.nickname for person in store.load_active_presence()],
                ["乙", "甲"],
            )
            store.remove_presence(first.user_id, "wrong_session")
            self.assertEqual(len(store.load_active_presence()), 2)
            store.remove_presence(first.user_id, first.session_id)
            self.assertEqual(
                [person.user_id for person in store.load_active_presence()],
                [second.user_id],
            )

    def test_presence_keeps_and_displays_five_online_computers(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = ChatRoomStore(root / "features/chat_room")
            for index in range(5):
                store.update_presence(
                    f"使用者{index + 1}",
                    f"user_0000000{index}",
                    f"session_{index}",
                )

            people = store.load_active_presence()
            self.assertEqual(len(people), 5)

            page = ChatRoomPage(
                lambda: None,
                lambda: None,
                chat_store=store,
                profile_store=ChatProfileStore(root / "profile.json"),
            )
            try:
                page._update_presence_list(people)
                self.assertEqual(page.presence_list.count(), 5)
                self.assertEqual(page.presence_title.text(), "目前在聊天室（5）")
                self.assertTrue(
                    all(
                        "\n" not in page.presence_list.item(row).text()
                        for row in range(page.presence_list.count())
                    )
                )
                self.assertEqual(
                    {
                        page.presence_list.item(row).text()
                        for row in range(page.presence_list.count())
                    },
                    {
                        f"{person.nickname}（{person.computer_name}）"
                        for person in people
                    },
                )
                self.assertEqual(
                    page.presence_list.verticalScrollBarPolicy(),
                    Qt.ScrollBarAlwaysOn,
                )
            finally:
                page.deactivate()
                page.deleteLater()

    def test_presence_collapses_duplicate_sessions_from_the_same_computer(self):
        with TemporaryDirectory() as temporary_directory:
            store = ChatRoomStore(
                Path(temporary_directory) / "features/chat_room"
            )
            with patch(
                "app.features.chat_room.store.socket.gethostname",
                return_value="COMPANY-PC-A",
            ):
                for index in range(10):
                    store.update_presence(
                        "甲",
                        "user_00000001",
                        f"session_{index}",
                    )

            people = store.load_active_presence()
            self.assertEqual(len(people), 1)
            self.assertEqual(people[0].nickname, "甲")
            self.assertEqual(people[0].computer_name, "COMPANY-PC-A")

    def test_read_receipts_are_per_computer_and_render_for_sender(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = ChatRoomStore(root / "features/chat_room")
            message = store.send_message(
                "甲",
                "需要確認的訊息",
                user_id="sender_00000001",
            )
            receipt = store.update_read_receipt(
                "乙",
                "reader_00000002",
                message,
            )

            self.assertEqual(receipt.last_read_message_id, message.message_id)
            self.assertEqual(len(store.load_read_receipts()), 1)

            profile = ChatProfileStore(root / "profile.json")
            profile._save_profile(
                profile.load_profile().__class__(
                    user_id="sender_00000001",
                    nickname="甲",
                )
            )
            page = ChatRoomPage(
                lambda: None,
                lambda: None,
                chat_store=store,
                profile_store=profile,
            )
            try:
                page._user_id = "sender_00000001"
                page.nickname_line.setText("甲")
                page._append_messages([message])
                page._update_read_receipts([receipt])
                self.assertIn("已讀 1 人", page.message_view.toPlainText())
            finally:
                page.shutdown()
                page.deleteLater()

    def test_inactive_chat_flashes_and_keeps_unread_marker_until_next_deactivation(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = ChatRoomStore(root / "features/chat_room")
            first = store.send_message("甲", "已讀", user_id="sender_00000001")
            second = store.send_message("甲", "新訊息", user_id="sender_00000001")
            profile = ChatProfileStore(root / "profile.json")
            profile.save_nickname("乙")
            profile.save_last_read_message_id(first.message_id)
            page = ChatRoomPage(
                lambda: None,
                lambda: None,
                chat_store=store,
                profile_store=profile,
            )
            unread_counts = []
            page.unread_count_changed.connect(unread_counts.append)
            try:
                loaded = profile.load_profile()
                page._user_id = loaded.user_id
                page._last_read_message_id = first.message_id
                page.nickname_line.setText("乙")
                page._append_messages([first])
                with patch.object(QApplication, "alert") as alert:
                    page._append_messages([second])
                    alert.assert_called_once()
                self.assertIn("從這裡開始為未讀訊息", page.message_view.toPlainText())
                self.assertEqual(unread_counts[-1], 1)

                page._mark_all_messages_read()
                self.assertEqual(unread_counts[-1], 0)
                self.assertIn("從這裡開始為未讀訊息", page.message_view.toPlainText())
                page.deactivate()
                self.assertNotIn("從這裡開始為未讀訊息", page.message_view.toPlainText())
            finally:
                page.shutdown()
                page.deleteLater()

    def test_copied_profile_keeps_two_computers_visible(self):
        with TemporaryDirectory() as temporary_directory:
            store = ChatRoomStore(
                Path(temporary_directory) / "features/chat_room"
            )
            with patch(
                "app.features.chat_room.store.socket.gethostname",
                return_value="COMPANY-PC-A",
            ):
                first = store.update_presence(
                    "甲",
                    "shared_user_0001",
                    "computer_one_session",
                )
            with patch(
                "app.features.chat_room.store.socket.gethostname",
                return_value="COMPANY-PC-B",
            ):
                second = store.update_presence(
                    "乙",
                    "shared_user_0001",
                    "computer_two_session",
                )

            people = store.load_active_presence()
            self.assertEqual([person.nickname for person in people], ["乙", "甲"])
            self.assertEqual(len(list(store.presence_root.glob("*.json"))), 2)

            store.remove_presence(first.user_id, first.session_id)
            remaining = store.load_active_presence()
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0].session_id, second.session_id)

    def test_copied_user_id_from_another_computer_still_counts_as_unread(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = ChatRoomStore(root / "features/chat_room")
            shared_user_id = "copied_user_0001"
            first = store.send_message(
                "自己",
                "已讀基準",
                user_id=shared_user_id,
            )
            second = store.send_message(
                "同事",
                "另一台電腦的新訊息",
                user_id=shared_user_id,
            )
            second = type(second)(
                **{
                    **second.__dict__,
                    "computer_name": "OTHER-COMPANY-PC",
                }
            )
            profile = ChatProfileStore(root / "profile.json")
            page = ChatRoomPage(
                lambda: None,
                lambda: None,
                chat_store=store,
                profile_store=profile,
            )
            unread_counts = []
            page.unread_count_changed.connect(unread_counts.append)
            try:
                page._user_id = shared_user_id
                page._last_read_message_id = first.message_id
                page._chat_visible = False
                page._append_messages([first])
                page._append_messages([second])
                self.assertEqual(unread_counts[-1], 1)
            finally:
                page.shutdown()
                page.deleteLater()

    def test_presence_uses_shared_file_time_instead_of_client_clock(self):
        with TemporaryDirectory() as temporary_directory:
            store = ChatRoomStore(
                Path(temporary_directory) / "features/chat_room"
            )
            first = store.update_presence("clock-a", "user_a", "session_a")
            store.update_presence("clock-b", "user_b", "session_b")
            first_path = store.presence_root / (
                f"{first.user_id}_{first.session_id}.json"
            )
            payload = json.loads(first_path.read_text(encoding="utf-8"))
            payload["last_seen_utc"] = "2000-01-01T00:00:00+00:00"
            first_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            self.assertEqual(len(store.load_active_presence()), 2)

    def test_presence_expires_using_shared_file_modification_time(self):
        with TemporaryDirectory() as temporary_directory:
            store = ChatRoomStore(
                Path(temporary_directory) / "features/chat_room"
            )
            stale = store.update_presence("stale", "user_a", "session_a")
            stale_path = store.presence_root / (
                f"{stale.user_id}_{stale.session_id}.json"
            )
            old_time = time.time() - 90
            os.utime(stale_path, (old_time, old_time))
            store.update_presence("fresh", "user_b", "session_b")

            self.assertEqual(
                [person.nickname for person in store.load_active_presence()],
                ["fresh"],
            )

    def test_chat_view_keeps_visible_scrollbar_and_shows_full_timestamp(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = ChatRoomStore(root / "features/chat_room")
            profile = ChatProfileStore(
                root / "app/features/chat_room/data/settings/chat_profile.json"
            )
            page = ChatRoomPage(
                lambda: None,
                lambda: None,
                chat_store=store,
                profile_store=profile,
            )
            try:
                message = store.send_message("甲", "時間顯示測試")
                page._append_messages([message])
                self.assertEqual(
                    page.message_view.verticalScrollBarPolicy(),
                    Qt.ScrollBarAlwaysOn,
                )
                self.assertRegex(
                    page.message_view.toPlainText(),
                    r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}",
                )
            finally:
                page.deactivate()
                page.deleteLater()



if __name__ == "__main__":
    unittest.main()
