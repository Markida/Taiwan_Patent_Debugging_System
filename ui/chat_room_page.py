"""Hidden LAN chat room with image attachments."""

from __future__ import annotations

from datetime import datetime
from html import escape
import hashlib
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread, current_thread
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QIcon,
    QImage,
    QImageReader,
    QPixmap,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.features.chat_room.store import (
    ALLOWED_IMAGE_SUFFIXES,
    ALLOWED_REACTION_EMOJIS,
    CHAT_POLL_INTERVAL_SECONDS,
    ChatMessage,
    ChatProfileStore,
    ChatRoomError,
    ChatRoomStore,
)
from app.features.chat_room.game_ranking import GameRankingStore
from ui.arcade_navigation import ArcadeNavigationBar


MESSAGE_COMPOSER_EMOJIS = (
    "😀", "😊", "😂", "👍", "👏", "🎉", "❤️", "🔥", "🙏", "💪", "🤔", "✅",
)


class ChatPoller(QObject):
    """Poll the SMB share off the GUI thread so an outage cannot freeze Qt."""

    messages_received = Signal(object)
    presence_received = Signal(object)
    receipts_received = Signal(object)
    reactions_received = Signal(object)
    rankings_received = Signal(object)
    connection_changed = Signal(bool, str)

    def __init__(
        self,
        store,
        interval=CHAT_POLL_INTERVAL_SECONDS,
        identity_provider=None,
        ranking_store=None,
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self.interval = max(0.1, float(interval))
        self.identity_provider = identity_provider
        self.ranking_store = ranking_store or GameRankingStore(self.store.root)
        self._stop_event = Event()
        self._thread = None
        self._lock = Lock()
        self._restart_requested = False
        self._identity_lock = Lock()
        self._identity = {}
        self._reload_requested = Event()
        self._pending_read_lock = Lock()
        self._pending_read = None
        self._details_enabled = Event()
        self._details_enabled.set()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        # The provider may read Qt widgets, so it must only run here on the
        # GUI thread.  The worker receives a plain-data snapshot below.
        if callable(self.identity_provider):
            self.set_identity(self.identity_provider())
        with self._lock:
            if self.running:
                # A cancelled SMB read cannot be forcibly interrupted by
                # Python. Queue a restart after it returns rather than start
                # a second poller on the same store while it is unwinding.
                if self._stop_event.is_set():
                    self._restart_requested = True
                return
            self._start_worker_locked()

    def _start_worker_locked(self):
        self._restart_requested = False
        stop_event = Event()
        self._stop_event = stop_event
        self._thread = Thread(
            target=self._run,
            args=(stop_event,),
            name="SaintIslandChatPoller",
            daemon=True,
        )
        self._thread.start()

    def stop(self, wait_timeout=1.5):
        with self._lock:
            self._restart_requested = False
            self._stop_event.set()
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=max(0.0, float(wait_timeout)))

    @staticmethod
    def _safe_emit(signal, *args):
        """Ignore a late worker result after its Qt owner was destroyed."""

        try:
            signal.emit(*args)
            return True
        except RuntimeError:
            return False

    def request_full_reload(self):
        self._reload_requested.set()

    def set_details_enabled(self, enabled):
        if enabled:
            self._details_enabled.set()
        else:
            self._details_enabled.clear()

    def mark_read(self, nickname, user_id, message):
        with self._pending_read_lock:
            self._pending_read = (nickname, user_id, message)

    def _take_pending_read(self):
        with self._pending_read_lock:
            pending = self._pending_read
            self._pending_read = None
            return pending

    def set_identity(self, identity):
        source = identity if isinstance(identity, dict) else {}
        snapshot = {
            "user_id": str(source.get("user_id", "")),
            "nickname": str(source.get("nickname", "")),
            "avatar_path": str(source.get("avatar_path", "")),
        }
        with self._identity_lock:
            self._identity = snapshot

    def _identity_snapshot(self):
        with self._identity_lock:
            return dict(self._identity)

    def _run(self, stop_event):
        initial_load = True
        session_id = uuid4().hex
        active_user_id = ""
        known_message_ids = set()
        last_ranking_poll = float("-inf")
        try:
            while not stop_event.is_set():
                try:
                    full_reload = initial_load or self._reload_requested.is_set()
                    if full_reload:
                        self._reload_requested.clear()
                    messages = (
                        self.store.load_recent_messages(lookback_days=2)
                        if full_reload
                        else self.store.load_active_messages()
                    )
                    initial_load = False
                    if stop_event.is_set():
                        break
                    if full_reload:
                        known_message_ids.clear()
                        last_ranking_poll = float("-inf")
                    known_message_ids.update(message.message_id for message in messages)
                    self._safe_emit(self.messages_received, messages)
                    identity = self._identity_snapshot()
                    if identity and identity.get("nickname"):
                        presence = self.store.update_presence(
                            identity["nickname"],
                            identity.get("user_id", ""),
                            session_id,
                            Path(identity["avatar_path"])
                            if identity.get("avatar_path")
                            else None,
                        )
                        active_user_id = presence.user_id
                    elif active_user_id:
                        self.store.remove_presence(active_user_id, session_id)
                        active_user_id = ""
                    if stop_event.is_set():
                        break
                    pending_read = self._take_pending_read()
                    if pending_read is not None:
                        self.store.update_read_receipt(*pending_read)
                    # Work pages only need unread messages. Avoid loading
                    # presence, receipts, reactions and all-time rankings for
                    # an invisible chat page on every one-second tick.
                    if self._details_enabled.is_set():
                        for signal, read in (
                            (self.presence_received, self.store.load_active_presence),
                            (self.receipts_received, self.store.load_read_receipts),
                            (self.reactions_received,
                             lambda: self.store.load_reactions(known_message_ids)),
                        ):
                            if stop_event.is_set():
                                break
                            result = read()
                            if not stop_event.is_set():
                                self._safe_emit(signal, result)
                        if stop_event.is_set():
                            break
                        if monotonic() - last_ranking_poll >= 5.0:
                            try:
                                rankings = self.ranking_store.leaderboard(3)
                            except Exception:
                                # Keep the last good ranking on a transient
                                # share failure instead of clearing the UI.
                                pass
                            else:
                                if not stop_event.is_set():
                                    self._safe_emit(self.rankings_received, rankings)
                            last_ranking_poll = monotonic()
                    if stop_event.is_set():
                        break
                    self._safe_emit(
                        self.connection_changed,
                        True,
                        f"已連線・現在時間 {datetime.now():%H:%M:%S}",
                    )
                except ChatRoomError as error:
                    if not stop_event.is_set():
                        self._safe_emit(
                            self.connection_changed,
                            False,
                            str(error),
                        )
                except Exception as error:
                    if not stop_event.is_set():
                        self._safe_emit(
                            self.connection_changed,
                            False,
                            f"聊天室更新發生未預期錯誤：{error}",
                        )
                stop_event.wait(self.interval)
        finally:
            if active_user_id:
                self.store.remove_presence(active_user_id, session_id)
            with self._lock:
                if self._restart_requested and self._thread is current_thread():
                    # The identity snapshot was already captured by start()
                    # on the GUI thread. Never call its widget provider here.
                    self._start_worker_locked()


def _media_resource_key(path):
    normalized = str(Path(path)).casefold().encode("utf-8", errors="replace")
    return "chat-media:" + hashlib.sha256(normalized).hexdigest()


class ChatMediaLoader(QObject):
    """Read and resize shared images away from the Qt GUI thread."""

    image_ready = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = Queue()
        self._lock = Lock()
        self._pending = set()
        self._completed = set()
        self._failed_at = {}
        self._stop_event = Event()
        self._thread = None
        self.destroyed.connect(lambda: self.stop())

    def request(self, path, maximum_size):
        if path is None:
            return ""
        normalized_path = Path(path)
        key = _media_resource_key(normalized_path)
        now = monotonic()
        with self._lock:
            if self._stop_event.is_set():
                return ""
            if key in self._completed or key in self._pending:
                return key
            if now - self._failed_at.get(key, 0.0) < 30.0:
                return key
            self._pending.add(key)
            if self._thread is None:
                self._thread = Thread(
                    target=self._run,
                    name="SaintIslandChatMediaLoader",
                    daemon=True,
                )
                self._thread.start()
            self._queue.put((key, normalized_path, QSize(maximum_size)))
        return key

    def stop(self):
        # Do not wait on a stalled SMB/image codec operation on the Qt thread.
        self._stop_event.set()
        with self._lock:
            self._pending.clear()
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except Empty:
                break

    def _run(self):
        while not self._stop_event.is_set():
            try:
                key, path, maximum_size = self._queue.get(timeout=0.2)
            except Empty:
                continue
            try:
                image = self._read_thumbnail(path, maximum_size)
            except Exception:
                image = QImage()
            with self._lock:
                self._pending.discard(key)
                if image.isNull():
                    self._failed_at[key] = monotonic()
                else:
                    self._completed.add(key)
                    self._failed_at.pop(key, None)
            if not image.isNull() and not self._stop_event.is_set():
                if not ChatPoller._safe_emit(self.image_ready, key, image):
                    self._stop_event.set()
            self._queue.task_done()

    @staticmethod
    def _read_thumbnail(path, maximum_size):
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        source_size = reader.size()
        if source_size.isValid() and (
            source_size.width() > maximum_size.width()
            or source_size.height() > maximum_size.height()
        ):
            source_size.scale(maximum_size, Qt.KeepAspectRatio)
            reader.setScaledSize(source_size)
        image = reader.read()
        if image.isNull():
            return QImage()
        return image


class ChatSender(QObject):
    """Copy an attachment and publish its message without blocking the UI."""

    sent = Signal(object)
    failed = Signal(str)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._thread = None
        self._lock = Lock()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def send(
        self,
        nickname,
        text,
        image_path,
        user_id,
        avatar_path,
        reply_to=None,
    ):
        with self._lock:
            if self.running:
                return False
            self._thread = Thread(
                target=self._run,
                args=(
                    nickname,
                    text,
                    image_path,
                    user_id,
                    avatar_path,
                    reply_to,
                ),
                name="SaintIslandChatSender",
                daemon=True,
            )
            self._thread.start()
        return True

    def _run(
        self,
        nickname,
        text,
        image_path,
        user_id,
        avatar_path,
        reply_to,
    ):
        try:
            message = self.store.send_message(
                nickname,
                text,
                Path(image_path) if image_path else None,
                user_id=user_id,
                avatar_path=Path(avatar_path) if avatar_path else None,
                reply_to=reply_to,
            )
            ChatPoller._safe_emit(self.sent, message)
        except Exception as error:
            ChatPoller._safe_emit(self.failed, str(error))


class ChatReactionWriter(QObject):
    """Persist reactions on a worker thread so SMB latency never freezes Qt."""

    saved = Signal(object)
    failed = Signal(str)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._lock = Lock()
        self._running = set()

    def save(self, message_id, user_id, nickname, emoji):
        key = (str(message_id), str(user_id), str(emoji))
        with self._lock:
            if key in self._running:
                return
            self._running.add(key)

        def run():
            try:
                reaction = self.store.set_reaction(
                    message_id,
                    user_id,
                    nickname,
                    emoji,
                )
                ChatPoller._safe_emit(self.saved, reaction)
            except Exception as error:
                ChatPoller._safe_emit(self.failed, str(error))
            finally:
                with self._lock:
                    self._running.discard(key)

        Thread(
            target=run,
            name="SaintIslandChatReactionWriter",
            daemon=True,
        ).start()


class ChatInputEdit(QTextEdit):
    send_requested = Signal()
    image_dropped = Signal(str)
    image_pasted = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    @staticmethod
    def _supported_image_path(mime_data):
        urls = mime_data.urls() if mime_data.hasUrls() else []
        if len(urls) != 1 or not urls[0].isLocalFile():
            return ""
        path = Path(urls[0].toLocalFile())
        return str(path) if path.suffix.lower() in ALLOWED_IMAGE_SUFFIXES else ""

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            if self._supported_image_path(event.mimeData()):
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            if self._supported_image_path(event.mimeData()):
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            image_path = self._supported_image_path(event.mimeData())
            if image_path:
                self.image_dropped.emit(image_path)
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        super().dropEvent(event)

    def keyPressEvent(self, event):
        if (
            event.key() == Qt.Key_V
            and event.modifiers() & Qt.ControlModifier
        ):
            clipboard = QApplication.clipboard()
            if clipboard is not None and clipboard.mimeData().hasImage():
                image = clipboard.image()
                if not image.isNull():
                    self.image_pasted.emit(image.copy())
                    event.accept()
                    return
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not event.modifiers() & Qt.ShiftModifier
        ):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ChatRoomPage(QWidget):
    """Secret page reachable only from the already-hidden snake page."""

    unread_count_changed = Signal(int)

    def __init__(
        self,
        go_home_callback,
        open_snake_callback,
        *,
        return_work_callback=None,
        open_pong_callback=None,
        open_tetris_callback=None,
        open_tank_callback=None,
        open_bulls_cows_callback=None,
        join_game_invitation_callback=None,
        chat_store=None,
        profile_store=None,
        poll_interval=CHAT_POLL_INTERVAL_SECONDS,
    ):
        super().__init__()
        self.go_home_callback = go_home_callback
        self.open_snake_callback = open_snake_callback
        self.return_work_callback = return_work_callback
        self.open_pong_callback = open_pong_callback
        self.open_tetris_callback = open_tetris_callback
        self.open_tank_callback = open_tank_callback
        self.open_bulls_cows_callback = open_bulls_cows_callback
        self.join_game_invitation_callback = join_game_invitation_callback
        self.chat_store = chat_store or ChatRoomStore()
        self.profile_store = profile_store or ChatProfileStore()
        self.poller = ChatPoller(
            self.chat_store,
            poll_interval,
            identity_provider=self._presence_identity,
            parent=self,
        )
        self.sender = ChatSender(self.chat_store, self)
        self.reaction_writer = ChatReactionWriter(self.chat_store, self)
        self.media_loader = ChatMediaLoader(self)
        self._profile_loaded = False
        self._rendered_message_ids = set()
        self._messages_by_id = {}
        self._read_receipts = []
        self._reactions_by_message = {}
        self._attachment_paths = {}
        self._message_ranges = []
        self._media_images = {}
        self._selected_image_path = ""
        self._staged_clipboard_image_path = ""
        self._user_id = ""
        self._avatar_path = ""
        self._last_read_message_id = ""
        self._notifications_enabled = True
        # Standalone pages render immediately (also useful for tests and local
        # previews).  MainWindow explicitly switches this off when starting
        # the silent background monitor on the home page.
        self._chat_visible = True
        self._session_unread_marker_id = ""
        self._initial_message_batch = False
        self._reply_message = None
        self._media_render_timer = QTimer(self)
        self._media_render_timer.setSingleShot(True)
        self._media_render_timer.setInterval(40)
        self._media_render_timer.timeout.connect(self._render_loaded_media)
        self._build_ui()
        self.poller.messages_received.connect(self._append_messages)
        self.poller.presence_received.connect(self._update_presence_list)
        self.poller.receipts_received.connect(self._update_read_receipts)
        self.poller.reactions_received.connect(self._update_reactions)
        self.poller.rankings_received.connect(self._update_game_rankings)
        self.poller.connection_changed.connect(self._set_connection_status)
        self.sender.sent.connect(self._on_message_sent)
        self.sender.failed.connect(self._on_send_failed)
        self.reaction_writer.saved.connect(self._on_reaction_saved)
        self.reaction_writer.failed.connect(self._on_reaction_failed)
        self.media_loader.image_ready.connect(self._on_media_ready)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(
                self._on_application_state_changed
            )
        self.setAcceptDrops(True)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 16)
        layout.setSpacing(8)

        self.arcade_navigation = ArcadeNavigationBar(
            current_id="chat",
            title="CHAT",
            go_home_callback=self.go_home_callback,
            return_work_callback=self.return_work_callback,
            callbacks={
                "pong": self.open_pong_callback,
                "tetris": self.open_tetris_callback,
                "tank": self.open_tank_callback,
                "bulls_and_cows": self.open_bulls_cows_callback,
                "snake": self.open_snake_callback,
            },
        )
        self.return_work_button = self.arcade_navigation.return_work_button
        self.notification_button = QPushButton("訊息提醒：開")
        self.notification_button.setObjectName("SecondaryButton")
        self.notification_button.setCheckable(True)
        self.notification_button.setChecked(True)
        self.notification_button.clicked.connect(
            self._set_notifications_enabled
        )
        self.arcade_navigation.add_trailing_widget(self.notification_button)
        self.connection_status = QLabel("尚未連線")
        self.connection_status.setObjectName("ChatDisconnected")
        self.arcade_navigation.add_trailing_widget(self.connection_status)
        layout.addWidget(self.arcade_navigation)

        profile_panel = QFrame()
        profile_panel.setObjectName("Panel")
        profile_layout = QHBoxLayout(profile_panel)
        profile_layout.setContentsMargins(12, 8, 12, 8)
        self.avatar_preview = QLabel("無\n頭貼")
        self.avatar_preview.setObjectName("ChatAvatarPreview")
        self.avatar_preview.setAlignment(Qt.AlignCenter)
        self.avatar_preview.setFixedSize(56, 56)
        profile_layout.addWidget(self.avatar_preview)
        self.select_avatar_button = QPushButton("設定／更換頭貼")
        self.select_avatar_button.setObjectName("SecondaryButton")
        self.select_avatar_button.clicked.connect(self.select_avatar)
        profile_layout.addWidget(self.select_avatar_button)
        profile_layout.addWidget(QLabel("聊天暱稱："))
        self.nickname_line = QLineEdit()
        self.nickname_line.setObjectName("InputLine")
        self.nickname_line.setPlaceholderText("第一次進入時請設定暱稱")
        self.nickname_line.setMaxLength(24)
        self.nickname_line.returnPressed.connect(self.save_nickname)
        profile_layout.addWidget(self.nickname_line, 1)
        self.save_nickname_button = QPushButton("儲存／變更暱稱")
        self.save_nickname_button.setObjectName("SecondaryButton")
        self.save_nickname_button.clicked.connect(self.save_nickname)
        profile_layout.addWidget(self.save_nickname_button)
        layout.addWidget(profile_panel)

        self.message_view = QTextBrowser()
        self.message_view.setObjectName("ChatMessageView")
        self.message_view.setOpenLinks(False)
        self.message_view.setOpenExternalLinks(False)
        self.message_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.message_view.setPlaceholderText(
            "聊天室尚無訊息。共享路徑連線成功後會自動載入新訊息。"
        )
        self.message_view.anchorClicked.connect(self._activate_message_link)
        self.message_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.message_view.customContextMenuRequested.connect(
            self._show_message_context_menu
        )
        conversation_row = QHBoxLayout()
        # Keep the conversation readable but reserve a wider centre panel for
        # every online colleague and a permanent top-three ranking at right.
        conversation_row.addWidget(self.message_view, 3)
        presence_panel = QFrame()
        presence_panel.setObjectName("Panel")
        presence_layout = QVBoxLayout(presence_panel)
        self.presence_title = QLabel("目前在聊天室（0）")
        self.presence_title.setObjectName("PanelTitle")
        presence_layout.addWidget(self.presence_title)
        self.presence_list = QListWidget()
        self.presence_list.setObjectName("ChatPresenceList")
        self.presence_list.setIconSize(QSize(30, 30))
        self.presence_list.setSelectionMode(QListWidget.NoSelection)
        self.presence_list.setUniformItemSizes(True)
        self.presence_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        presence_layout.addWidget(self.presence_list, 1)

        ranking_panel = QFrame()
        ranking_panel.setObjectName("Panel")
        ranking_layout = QVBoxLayout(ranking_panel)
        ranking_layout.setContentsMargins(10, 10, 10, 10)
        ranking_layout.setSpacing(8)
        ranking_title = QLabel("綜合遊戲積分榜")
        ranking_title.setObjectName("PanelTitle")
        ranking_title.setAlignment(Qt.AlignCenter)
        ranking_layout.addWidget(ranking_title)
        ranking_hint = QLabel("依電腦代號累計｜僅計算真人連線對戰勝場")
        ranking_hint.setAlignment(Qt.AlignCenter)
        ranking_hint.setWordWrap(True)
        ranking_layout.addWidget(ranking_hint)
        self.game_ranking_rows = []
        for rank in range(1, 4):
            row_panel = QFrame()
            row_panel.setObjectName(f"GameRankingCard{rank}")
            row_layout = QHBoxLayout(row_panel)
            row_layout.setContentsMargins(8, 6, 8, 6)
            avatar = QLabel("♟")
            avatar.setAlignment(Qt.AlignCenter)
            avatar.setFixedSize(58 if rank == 1 else 44, 58 if rank == 1 else 44)
            row_layout.addWidget(avatar)
            row = QLabel("尚無紀錄")
            row.setAlignment(Qt.AlignCenter)
            row.setWordWrap(True)
            row_layout.addWidget(row, 1)
            row_panel.setMinimumHeight(102 if rank == 1 else 72)
            if rank == 1:
                row_panel.setStyleSheet(
                    f"QFrame#GameRankingCard{rank} {{background:#3b2f0b;border:2px solid #facc15;"
                    "border-radius:10px;}"
                    "QLabel {background:transparent;color:#fff7c2;border:none;"
                    "font-size:18px;font-weight:700;padding:0;}"
                )
            else:
                row_panel.setStyleSheet(
                    f"QFrame#GameRankingCard{rank} {{background:#172033;border:1px solid #475569;"
                    "border-radius:8px;}"
                    "QLabel {background:transparent;color:#f8fafc;border:none;"
                    "font-size:15px;font-weight:600;padding:0;}"
                )
            self.game_ranking_rows.append((avatar, row))
            ranking_layout.addWidget(row_panel)
        ranking_layout.addStretch()
        self.chat_side_tabs = QTabWidget()
        self.chat_side_tabs.setObjectName("ChatSideTabs")
        self.chat_side_tabs.addTab(presence_panel, "在線人員")
        self.chat_side_tabs.addTab(ranking_panel, "遊戲排行榜")
        self.chat_side_tabs.setMinimumWidth(300)
        self.chat_side_tabs.setMaximumWidth(410)
        conversation_row.addWidget(self.chat_side_tabs, 2)
        layout.addLayout(conversation_row, 1)

        attachment_row = QHBoxLayout()
        self.select_image_button = QPushButton("選擇圖片")
        self.select_image_button.setObjectName("SecondaryButton")
        self.select_image_button.clicked.connect(self.select_image)
        attachment_row.addWidget(self.select_image_button)
        self.clear_image_button = QPushButton("取消圖片")
        self.clear_image_button.setObjectName("SecondaryButton")
        self.clear_image_button.clicked.connect(self.clear_selected_image)
        self.clear_image_button.setEnabled(False)
        attachment_row.addWidget(self.clear_image_button)
        self.attachment_label = QLabel(
            "未選擇圖片（可拖入圖片，或按 Ctrl+V 貼上螢幕截圖）"
        )
        self.attachment_label.setObjectName("ChatAttachmentStatus")
        attachment_row.addWidget(self.attachment_label, 1)
        layout.addLayout(attachment_row)

        self.reply_panel = QFrame()
        self.reply_panel.setObjectName("ChatReplyPanel")
        reply_layout = QHBoxLayout(self.reply_panel)
        reply_layout.setContentsMargins(10, 6, 10, 6)
        self.reply_avatar = QLabel("●")
        self.reply_avatar.setAlignment(Qt.AlignCenter)
        self.reply_avatar.setFixedSize(34, 34)
        reply_layout.addWidget(self.reply_avatar)
        self.reply_summary = QLabel("")
        self.reply_summary.setWordWrap(True)
        reply_layout.addWidget(self.reply_summary, 1)
        cancel_reply = QPushButton("取消回覆")
        cancel_reply.setObjectName("SecondaryButton")
        cancel_reply.clicked.connect(self._clear_reply)
        reply_layout.addWidget(cancel_reply)
        self.reply_panel.setVisible(False)
        layout.addWidget(self.reply_panel)

        emoji_row = QHBoxLayout()
        emoji_row.setSpacing(4)
        emoji_label = QLabel("表情：")
        emoji_label.setObjectName("ChatAttachmentStatus")
        emoji_row.addWidget(emoji_label)
        self.message_emoji_buttons = []
        for emoji in MESSAGE_COMPOSER_EMOJIS:
            button = QPushButton(emoji)
            button.setObjectName("SecondaryButton")
            button.setToolTip(f"插入 {emoji}")
            button.setAccessibleName(f"插入表情符號 {emoji}")
            button.setMinimumSize(34, 32)
            button.setMaximumWidth(44)
            button.clicked.connect(
                lambda _checked=False, value=emoji: self._insert_message_emoji(value)
            )
            emoji_row.addWidget(button)
            self.message_emoji_buttons.append(button)
        emoji_row.addStretch()
        layout.addLayout(emoji_row)

        compose_row = QHBoxLayout()
        self.message_input = ChatInputEdit()
        self.message_input.setObjectName("ChatMessageInput")
        self.message_input.setPlaceholderText(
            "輸入訊息；Enter 傳送，Shift+Enter 換行。可拖入圖片或按 Ctrl+V 貼上截圖。"
        )
        self.message_input.setFixedHeight(92)
        self.message_input.send_requested.connect(self.send_message)
        self.message_input.image_dropped.connect(self._set_selected_image)
        self.message_input.image_pasted.connect(self._stage_clipboard_image)
        compose_row.addWidget(self.message_input, 1)
        self.send_button = QPushButton("傳送")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.setMinimumWidth(120)
        self.send_button.clicked.connect(self.send_message)
        compose_row.addWidget(self.send_button)
        layout.addLayout(compose_row)

    def _insert_message_emoji(self, emoji):
        """Insert an emoji at the active caret without replacing the paragraph."""

        cursor = self.message_input.textCursor()
        cursor.insertText(str(emoji))
        self.message_input.setTextCursor(cursor)
        self.message_input.setFocus(Qt.OtherFocusReason)

    def prepare_chat(self):
        # Entering the room always rebuilds the visible two-day history from
        # the company share instead of retaining an older in-memory snapshot.
        self._chat_visible = True
        self.poller.set_details_enabled(True)
        self.message_view.clear()
        self._rendered_message_ids.clear()
        self._messages_by_id.clear()
        self._session_unread_marker_id = ""
        self._attachment_paths.clear()
        self._reactions_by_message.clear()
        self._clear_reply()
        self.presence_list.clear()
        self.presence_title.setText("目前在聊天室（0）")
        if not self._profile_loaded:
            self._profile_loaded = True
            try:
                profile = self.profile_store.load_profile()
                self._user_id = profile.user_id
                self._avatar_path = profile.avatar_path
                self._last_read_message_id = profile.last_read_message_id
                self._notifications_enabled = profile.notifications_enabled
                self.notification_button.setChecked(
                    self._notifications_enabled
                )
                self._refresh_notification_button()
                self.nickname_line.setText(profile.nickname)
                self._refresh_avatar_preview()
            except ChatRoomError as error:
                QMessageBox.warning(self, "暱稱讀取失敗", str(error))
        if not self.nickname_line.text().strip():
            nickname, accepted = QInputDialog.getText(
                self,
                "進入即時聊天室",
                "請設定聊天室暱稱：",
            )
            if accepted and nickname.strip():
                self.nickname_line.setText(nickname)
                if not self.save_nickname(show_confirmation=False):
                    self.nickname_line.clear()
        self.poller.set_identity(self._presence_identity())
        self.poller.request_full_reload()
        self.poller.start()
        self.message_input.setFocus(Qt.OtherFocusReason)

    def start_background_monitoring(self):
        """Monitor unread messages without showing this hidden page as online."""

        if not self._profile_loaded:
            self._profile_loaded = True
            try:
                profile = self.profile_store.load_profile()
                self._user_id = profile.user_id
                self._avatar_path = profile.avatar_path
                self._last_read_message_id = profile.last_read_message_id
                self._notifications_enabled = profile.notifications_enabled
                self.nickname_line.setText(profile.nickname)
                self.notification_button.setChecked(self._notifications_enabled)
                self._refresh_notification_button()
                self._refresh_avatar_preview()
            except ChatRoomError as error:
                self.connection_status.setToolTip(str(error))
        self._chat_visible = False
        self._initial_message_batch = True
        self.poller.set_details_enabled(False)
        self.poller.set_identity(None)
        self.poller.request_full_reload()
        self.poller.start()

    def set_return_work_available(self, available):
        self.return_work_button.setEnabled(bool(available))

    def deactivate(self):
        self._chat_visible = False
        self.poller.set_details_enabled(False)
        self.poller.set_identity(None)
        if self._session_unread_marker_id and self._last_read_message_id:
            self._session_unread_marker_id = ""
            self._render_messages()

    def shutdown(self):
        self._chat_visible = False
        self._media_render_timer.stop()
        self.poller.stop(wait_timeout=0)
        self.media_loader.stop()
        self._cleanup_staged_clipboard_image()

    def _presence_identity(self):
        if not self._chat_visible:
            return None
        nickname = self.nickname_line.text().strip()
        if not nickname:
            return None
        return {
            "user_id": self._user_id,
            "nickname": nickname,
            "avatar_path": self._avatar_path,
        }

    def _refresh_profile_state(self):
        profile = self.profile_store.load_profile()
        self._user_id = profile.user_id
        self._avatar_path = profile.avatar_path
        self._last_read_message_id = profile.last_read_message_id
        self._notifications_enabled = profile.notifications_enabled
        self.notification_button.setChecked(self._notifications_enabled)
        self._refresh_notification_button()
        self._refresh_avatar_preview()
        self.poller.set_identity(self._presence_identity())

    def _refresh_avatar_preview(self):
        pixmap = QPixmap(self._avatar_path) if self._avatar_path else QPixmap()
        if pixmap.isNull():
            self.avatar_preview.setPixmap(QPixmap())
            self.avatar_preview.setText("無\n頭貼")
            self.avatar_preview.setToolTip("尚未設定聊天室頭貼")
            return
        self.avatar_preview.setText("")
        self.avatar_preview.setPixmap(
            pixmap.scaled(
                self.avatar_preview.size(),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
        )
        self.avatar_preview.setToolTip(Path(self._avatar_path).name)

    def select_avatar(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "選擇聊天室頭貼",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp)",
        )
        if not file_path:
            return
        if QPixmap(file_path).isNull():
            QMessageBox.warning(self, "頭貼讀取失敗", "選擇的檔案不是有效圖片。")
            return
        try:
            self.profile_store.save_avatar(Path(file_path))
            self._refresh_profile_state()
        except ChatRoomError as error:
            QMessageBox.warning(self, "頭貼無法保存", str(error))

    def save_nickname(self, _checked=False, *, show_confirmation=True):
        try:
            nickname = self.profile_store.save_nickname(
                self.nickname_line.text()
            )
        except ChatRoomError as error:
            QMessageBox.warning(self, "暱稱無法保存", str(error))
            return False
        self.nickname_line.setText(nickname)
        try:
            self._refresh_profile_state()
        except ChatRoomError as error:
            QMessageBox.warning(self, "個人設定讀取失敗", str(error))
            return False
        if show_confirmation:
            self.connection_status.setText(f"暱稱已更新為：{nickname}")
        return True

    def _set_notifications_enabled(self, enabled):
        try:
            self._notifications_enabled = (
                self.profile_store.save_notifications_enabled(enabled)
            )
        except ChatRoomError as error:
            self.notification_button.setChecked(self._notifications_enabled)
            QMessageBox.warning(self, "提醒設定無法保存", str(error))
        self._refresh_notification_button()

    def _refresh_notification_button(self):
        self.notification_button.setText(
            "訊息提醒：開" if self._notifications_enabled else "訊息提醒：關"
        )
        self.notification_button.setToolTip(
            "收到其他人的新訊息時會閃爍工作列圖示"
            if self._notifications_enabled
            else "目前不會以工作列閃爍提示新訊息"
        )

    def select_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "選擇聊天室圖片",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp)",
        )
        if file_path:
            self._set_selected_image(file_path)

    def _set_selected_image(self, file_path):
        path = Path(file_path)
        if path.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
            QMessageBox.warning(
                self,
                "圖片格式錯誤",
                "聊天室只接受 PNG、JPG、JPEG 或 BMP 圖片。",
            )
            return False
        if (
            self._staged_clipboard_image_path
            and Path(self._staged_clipboard_image_path) != path
        ):
            self._cleanup_staged_clipboard_image()
        self._selected_image_path = str(path)
        self.attachment_label.setText(f"準備上傳：{path.name}")
        self.clear_image_button.setEnabled(True)
        return True

    def _stage_clipboard_image(self, image):
        if not isinstance(image, QImage) or image.isNull():
            return False
        self._cleanup_staged_clipboard_image()
        staging_root = self.profile_store.path.parent / "clipboard_staging"
        staging_path = staging_root / f"chat_clipboard_{uuid4().hex}.png"
        try:
            staging_root.mkdir(parents=True, exist_ok=True)
            if not image.save(str(staging_path), "PNG"):
                raise OSError("Qt 無法將剪貼簿影像轉存為 PNG")
        except OSError as error:
            QMessageBox.warning(
                self,
                "截圖貼上失敗",
                f"無法暫存剪貼簿中的截圖：\n{error}",
            )
            return False
        self._staged_clipboard_image_path = str(staging_path)
        self._selected_image_path = str(staging_path)
        self.attachment_label.setText("已貼上螢幕截圖（可與文字一併傳送）")
        self.clear_image_button.setEnabled(True)
        return True

    def _cleanup_staged_clipboard_image(self):
        staged = self._staged_clipboard_image_path
        self._staged_clipboard_image_path = ""
        if not staged:
            return
        try:
            Path(staged).unlink(missing_ok=True)
        except OSError:
            pass

    def clear_selected_image(self):
        self._cleanup_staged_clipboard_image()
        self._selected_image_path = ""
        self.attachment_label.setText(
            "未選擇圖片（可拖入圖片，或按 Ctrl+V 貼上螢幕截圖）"
        )
        self.clear_image_button.setEnabled(False)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if len(urls) == 1 and Path(urls[0].toLocalFile()).suffix.lower() in ALLOWED_IMAGE_SUFFIXES:
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if len(urls) == 1 and self._set_selected_image(urls[0].toLocalFile()):
            event.acceptProposedAction()
            return
        event.ignore()

    def send_message(self):
        if not self.save_nickname(show_confirmation=False):
            return
        if self.sender.running:
            return
        started = self.sender.send(
            self.nickname_line.text(),
            self.message_input.toPlainText(),
            self._selected_image_path,
            self._user_id,
            self._avatar_path,
            self._reply_message,
        )
        if started:
            self.send_button.setEnabled(False)
            self.send_button.setText("傳送中…")

    def _on_message_sent(self, message):
        self.send_button.setEnabled(True)
        self.send_button.setText("傳送")
        self.message_input.clear()
        self.clear_selected_image()
        self._clear_reply()
        self._append_messages([message])

    def _on_send_failed(self, message):
        self.send_button.setEnabled(True)
        self.send_button.setText("傳送")
        QMessageBox.warning(self, "訊息傳送失敗", message)

    def _set_connection_status(self, connected, text):
        self.connection_status.setObjectName(
            "ChatConnected" if connected else "ChatDisconnected"
        )
        self.connection_status.style().unpolish(self.connection_status)
        self.connection_status.style().polish(self.connection_status)
        self.connection_status.setText(text if connected else "聊天室離線")
        self.connection_status.setToolTip(text)

    def _update_presence_list(self, people):
        self.presence_list.clear()
        self.presence_title.setText(f"目前在聊天室（{len(people)}）")
        if hasattr(self, "chat_side_tabs"):
            self.chat_side_tabs.setTabText(0, f"在線人員 {len(people)}")
        for presence in people:
            computer_number = presence.computer_name.strip()
            label = (
                f"{presence.nickname}（{computer_number}）"
                if computer_number
                else presence.nickname
            )
            item = QListWidgetItem(label)
            item.setSizeHint(QSize(0, 40))
            avatar_path = self.chat_store.resolve_presence_avatar(presence)
            if avatar_path is not None:
                media_key = self.media_loader.request(
                    avatar_path,
                    QSize(84, 84),
                )
                item.setData(Qt.UserRole, media_key)
                image = self._media_images.get(media_key)
                if image is not None:
                    item.setIcon(self._presence_icon(image))
            item.setToolTip(
                f"暱稱：{presence.nickname}\n"
                f"電腦：{presence.computer_name}\n"
                f"最後更新：{self._display_time(presence.last_seen_utc)}"
            )
            self.presence_list.addItem(item)

    def _update_game_rankings(self, entries):
        medals = ("👑  🥇", "🥈", "🥉")
        for index, (avatar, label) in enumerate(self.game_ranking_rows):
            if index >= len(entries):
                label.setText(f"{medals[index]}\n尚無紀錄")
                label.setToolTip("")
                avatar.setPixmap(QPixmap())
                avatar.setText("♟")
                avatar.setProperty("rankingMediaKey", "")
                continue
            entry = entries[index]
            label.setText(
                f"{medals[index]}  第 {index + 1} 名\n"
                f"{entry.display_name}\n{entry.points} 積分"
            )
            label.setToolTip(
                f"累積勝場：{entry.wins}\n"
                + "、".join(
                    f"{game_id} {wins} 勝"
                    for game_id, wins in sorted(entry.by_game.items())
                )
            )
            avatar.setPixmap(QPixmap())
            avatar.setText("♟")
            relative_avatar = str(entry.avatar_relative_path or "").strip()
            avatar_path = self.chat_store.resolve_relative_media(relative_avatar)
            if avatar_path is not None:
                media_key = self.media_loader.request(avatar_path, QSize(120, 120))
                avatar.setProperty("rankingMediaKey", media_key)
                image = self._media_images.get(media_key)
                if image is not None:
                    self._set_ranking_avatar(avatar, image)

    @staticmethod
    def _set_ranking_avatar(label, image):
        label.setText("")
        label.setPixmap(
            QPixmap.fromImage(image).scaled(
                label.size(),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
        )

    @staticmethod
    def _presence_icon(image):
        return QIcon(
            QPixmap.fromImage(image).scaled(
                30,
                30,
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
        )

    def _on_media_ready(self, media_key, image):
        self._media_images[media_key] = image
        document = self.message_view.document()
        document.addResource(
            QTextDocument.ImageResource,
            QUrl(media_key),
            image,
        )
        for row in range(self.presence_list.count()):
            item = self.presence_list.item(row)
            if item.data(Qt.UserRole) == media_key:
                item.setIcon(self._presence_icon(image))
        for avatar, _label in self.game_ranking_rows:
            if avatar.property("rankingMediaKey") == media_key:
                self._set_ranking_avatar(avatar, image)
        if self._chat_visible and self._messages_by_id:
            if not self._media_render_timer.isActive():
                self._media_render_timer.start()

    def _render_loaded_media(self):
        if self._chat_visible and self._messages_by_id:
            self._render_messages()

    def _prepare_media_resource(self, path, maximum_size):
        media_key = self.media_loader.request(path, maximum_size)
        image = self._media_images.get(media_key)
        if image is not None:
            self.message_view.document().addResource(
                QTextDocument.ImageResource,
                QUrl(media_key),
                image,
            )
        return media_key

    @staticmethod
    def _display_time(timestamp):
        try:
            value = datetime.fromisoformat(timestamp)
            return value.astimezone().strftime("%Y/%m/%d %H:%M:%S")
        except (TypeError, ValueError):
            return str(timestamp)

    def _append_messages(self, messages):
        initial_batch = self._initial_message_batch
        self._initial_message_batch = False
        ordered = sorted(
            messages,
            key=lambda item: (item.created_at_utc, item.message_id),
        )
        new_messages = [
            message
            for message in ordered
            if message.message_id not in self._messages_by_id
        ]
        if not new_messages:
            return
        actively_reading = self._is_actively_reading()
        for message in new_messages:
            self._messages_by_id[message.message_id] = message
            self._rendered_message_ids.add(message.message_id)

        if initial_batch and not self._last_read_message_id:
            latest = self._ordered_messages()[-1]
            self._last_read_message_id = latest.message_id
            try:
                self.profile_store.save_last_read_message_id(latest.message_id)
            except ChatRoomError as error:
                self.connection_status.setToolTip(str(error))

        if self._last_read_message_id and not self._session_unread_marker_id:
            self._session_unread_marker_id = self._first_unread_message_id()

        incoming_from_others = [
            message
            for message in new_messages
            if not self._is_own_message(message)
        ]
        if (
            incoming_from_others
            and not actively_reading
            and self._notifications_enabled
            and not initial_batch
        ):
            QApplication.alert(self.window(), 0)

        if self._chat_visible:
            self._render_messages()
        self._emit_unread_count()
        if actively_reading:
            self._mark_all_messages_read()

    @staticmethod
    def _message_key(message):
        return (message.created_at_utc, message.message_id)

    def _ordered_messages(self):
        return sorted(self._messages_by_id.values(), key=self._message_key)

    def _is_own_message(self, message):
        if message.user_id and self._user_id:
            # Company installations may have been copied together with an
            # older chat profile, so multiple computers can share user_id.
            # A message is local only when both identity and computer match.
            return (
                message.user_id == self._user_id
                and message.computer_name.casefold()
                == self._local_computer_name().casefold()
            )
        return (
            message.sender == self.nickname_line.text().strip()
            and message.computer_name.casefold()
            == self._local_computer_name().casefold()
        )

    @staticmethod
    def _local_computer_name():
        import socket

        return socket.gethostname()

    def _first_unread_message_id(self):
        messages = self._ordered_messages()
        if not self._last_read_message_id:
            return ""
        cursor_index = next(
            (
                index
                for index, message in enumerate(messages)
                if message.message_id == self._last_read_message_id
            ),
            None,
        )
        candidates = (
            messages[cursor_index + 1 :]
            if cursor_index is not None
            else [
                message
                for message in messages
                if message.message_id > self._last_read_message_id
            ]
        )
        return candidates[0].message_id if candidates else ""

    def _unread_messages(self):
        marker = self._first_unread_message_id()
        if not marker:
            return []
        messages = self._ordered_messages()
        marker_index = next(
            index
            for index, message in enumerate(messages)
            if message.message_id == marker
        )
        return [
            message
            for message in messages[marker_index:]
            if not self._is_own_message(message)
        ]

    def _emit_unread_count(self):
        self.unread_count_changed.emit(len(self._unread_messages()))

    def _is_actively_reading(self):
        window = self.window()
        return bool(
            self._chat_visible
            and self.isVisible()
            and window is not None
            and window.isActiveWindow()
            and not window.isMinimized()
        )

    def _mark_all_messages_read(self):
        messages = self._ordered_messages()
        nickname = self.nickname_line.text().strip()
        if not messages or not nickname or not self._user_id:
            return
        latest = messages[-1]
        if latest.message_id == self._last_read_message_id:
            self._emit_unread_count()
            return
        self._last_read_message_id = latest.message_id
        try:
            self.profile_store.save_last_read_message_id(latest.message_id)
        except ChatRoomError as error:
            self.connection_status.setToolTip(str(error))
        self.poller.mark_read(nickname, self._user_id, latest)
        self._emit_unread_count()

    def _on_application_state_changed(self, state):
        if state == Qt.ApplicationActive:
            if self._is_actively_reading():
                self._mark_all_messages_read()
            return
        if self._session_unread_marker_id and not self._unread_messages():
            self._session_unread_marker_id = ""
            self._render_messages()

    def _update_read_receipts(self, receipts):
        old_counts = self._read_counts()
        self._read_receipts = list(receipts)
        if self._chat_visible and old_counts != self._read_counts():
            self._render_messages()

    def _update_reactions(self, reactions):
        grouped = {}
        for reaction in reactions:
            grouped.setdefault(reaction.message_id, []).append(reaction)
        normalized = {
            message_id: sorted(
                values,
                key=lambda item: (
                    item.emoji,
                    item.nickname.casefold(),
                    item.computer_name.casefold(),
                ),
            )
            for message_id, values in grouped.items()
        }
        if normalized != self._reactions_by_message:
            self._reactions_by_message = normalized
            if self._chat_visible:
                self._render_messages()

    def _on_reaction_saved(self, reaction):
        values = [
            item
            for item in self._reactions_by_message.get(reaction.message_id, [])
            if not (
                item.user_id == reaction.user_id
                and item.computer_name.casefold()
                == reaction.computer_name.casefold()
            )
        ]
        values.append(reaction)
        self._reactions_by_message[reaction.message_id] = values
        self._render_messages()

    def _on_reaction_failed(self, message):
        QMessageBox.warning(self, "表情回應失敗", message)

    def _set_reply(self, message):
        self._reply_message = message
        summary = message.text.strip()
        if not summary:
            summary = "[圖片]" if message.attachment_name else "[訊息]"
        if len(summary) > 140:
            summary = summary[:137] + "…"
        self.reply_summary.setText(f"回覆 {message.sender}\n{summary}")
        avatar_path = self.chat_store.resolve_avatar(message)
        pixmap = QPixmap(str(avatar_path)) if avatar_path else QPixmap()
        if pixmap.isNull():
            self.reply_avatar.setPixmap(QPixmap())
            self.reply_avatar.setText("●")
        else:
            self.reply_avatar.setText("")
            self.reply_avatar.setPixmap(
                pixmap.scaled(
                    self.reply_avatar.size(),
                    Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation,
                )
            )
        self.reply_panel.setVisible(True)
        self.message_input.setFocus(Qt.OtherFocusReason)

    def _clear_reply(self):
        self._reply_message = None
        if hasattr(self, "reply_panel"):
            self.reply_panel.setVisible(False)

    def _message_from_view_position(self, position):
        anchor = self.message_view.anchorAt(position)
        if anchor:
            url = QUrl(anchor)
            scheme = url.scheme()
            if scheme in {"chat-message", "chat-image", "chat-invite"}:
                message_id = anchor.split(":", 1)[-1]
                return self._messages_by_id.get(message_id)
        cursor_position = self.message_view.cursorForPosition(position).position()
        for start, end, message_id in self._message_ranges:
            if start <= cursor_position <= end:
                return self._messages_by_id.get(message_id)
        return None

    def _show_message_context_menu(self, position):
        message = self._message_from_view_position(position)
        if message is None:
            return
        menu = QMenu(self)
        reply_action = menu.addAction("回覆")
        copy_action = menu.addAction("複製")
        emoji_menu = menu.addMenu("emoji")
        emoji_actions = {}
        for emoji in ALLOWED_REACTION_EMOJIS:
            emoji_actions[emoji_menu.addAction(emoji)] = emoji
        selected = menu.exec(self.message_view.viewport().mapToGlobal(position))
        if selected == reply_action:
            self._set_reply(message)
            return
        if selected == copy_action:
            text = message.text or message.attachment_name or ""
            path = self._attachment_paths.get(message.message_id)
            if not message.text and path is not None:
                image = QImage(str(path))
                if not image.isNull():
                    QApplication.clipboard().setImage(image)
                    return
            QApplication.clipboard().setText(text)
            return
        emoji = emoji_actions.get(selected)
        if emoji:
            if not self.save_nickname(show_confirmation=False):
                return
            self.reaction_writer.save(
                message.message_id,
                self._user_id,
                self.nickname_line.text(),
                emoji,
            )

    def _read_counts(self):
        messages = self._ordered_messages()
        counts = {message.message_id: 0 for message in messages}
        for receipt in self._read_receipts:
            receipt_key = (
                receipt.last_read_created_at_utc,
                receipt.last_read_message_id,
            )
            for message in messages:
                if self._message_key(message) > receipt_key:
                    continue
                if (
                    receipt.user_id == message.user_id
                    and receipt.computer_name.casefold()
                    == message.computer_name.casefold()
                ):
                    continue
                counts[message.message_id] += 1
        return counts

    def _render_messages(self):
        self._media_render_timer.stop()
        scroll_bar = self.message_view.verticalScrollBar()
        previous_scroll_value = scroll_bar.value()
        follow_latest = (
            not self._messages_by_id
            or scroll_bar.maximum() - previous_scroll_value <= 24
        )
        self.message_view.clear()
        self._attachment_paths.clear()
        self._message_ranges.clear()
        read_counts = self._read_counts()
        for message in self._ordered_messages():
            message_anchor = (
                "chat-message:" + escape(message.message_id, quote=True)
            )
            if message.message_id == self._session_unread_marker_id:
                self.message_view.moveCursor(QTextCursor.End)
                self.message_view.insertHtml(
                    "<div style='margin:14px 2px 8px;padding:7px;"
                    "background:#fee2e2;border-top:2px solid #dc2626;"
                    "border-bottom:2px solid #dc2626;text-align:center;"
                    "font-weight:700;color:#b91c1c'>"
                    "從這裡開始為未讀訊息</div>"
                )
            attachment_html = ""
            avatar_path = self.chat_store.resolve_avatar(message)
            if avatar_path is not None:
                avatar_url = self._prepare_media_resource(
                    avatar_path,
                    QSize(96, 96),
                )
                avatar_html = (
                    "<img src='"
                    + escape(avatar_url, quote=True)
                    + "' width='48' height='48'>"
                )
            else:
                avatar_html = (
                    "<span style='font-size:24pt;color:#94a3b8'>●</span>"
                )
            attachment_path = self.chat_store.resolve_attachment(message)
            if attachment_path is not None:
                self._attachment_paths[message.message_id] = attachment_path
                image_url = self._prepare_media_resource(
                    attachment_path,
                    QSize(720, 720),
                )
                attachment_name = escape(
                    message.attachment_name or attachment_path.name
                )
                attachment_html = (
                    "<br><a href='chat-image:"
                    + escape(message.message_id, quote=True)
                    + "'><img src='"
                    + escape(image_url, quote=True)
                    + "'></a>"
                    + f"<br><span style='color:#475569'>圖片：{attachment_name}</span>"
                )
            body = escape(message.text).replace("\n", "<br>")
            reply_html = ""
            if message.reply_to_message_id:
                reply_avatar_html = ""
                reply_avatar_path = self.chat_store.resolve_relative_media(
                    message.reply_avatar_relative_path
                )
                if reply_avatar_path is not None:
                    reply_avatar_url = self._prepare_media_resource(
                        reply_avatar_path,
                        QSize(56, 56),
                    )
                    reply_avatar_html = (
                        "<img src='"
                        + escape(reply_avatar_url, quote=True)
                        + "' width='26' height='26'> "
                    )
                reply_html = (
                    "<div style='margin:2px 0 7px;padding:6px 8px;"
                    "background:#eef5fb;border-left:4px solid #4a90c2;"
                    "color:#475569;font-size:10pt'>"
                    f"{reply_avatar_html}<b>{escape(message.reply_sender)}</b> "
                    f"{escape(message.reply_text).replace(chr(10), '<br>')}"
                    "</div>"
                )
            invitation_html = ""
            if message.message_type == "game_invitation":
                title = escape(
                    str((message.metadata or {}).get("game_title", "對戰遊戲"))
                )
                invitation_html = (
                    "<br><a href='chat-invite:"
                    + escape(message.message_id, quote=True)
                    + "' style='display:inline-block;color:#ffffff;"
                    "background:#0f6fa8;font-weight:700;text-decoration:none'>"
                    f"　加入 {title} 房間　</a>"
                )
            reaction_groups = {}
            for reaction in self._reactions_by_message.get(message.message_id, []):
                reaction_groups.setdefault(reaction.emoji, []).append(reaction)
            reactions_html = ""
            if reaction_groups:
                chunks = []
                for emoji, values in reaction_groups.items():
                    names = "、".join(item.nickname for item in values)
                    chunks.append(
                        "<span title='"
                        + escape(names, quote=True)
                        + "' style='margin-right:8px;background:#eaf1f7;"
                        "border:1px solid #c7d7e5;padding:3px 7px'>"
                        + escape(emoji)
                        + (f" {len(values)}" if len(values) > 1 else "")
                        + "</span>"
                    )
                reactions_html = "<br>" + " ".join(chunks)
            read_status = ""
            if self._is_own_message(message):
                read_status = (
                    "<br><span style='font-size:10pt;color:#64748b'>"
                    f"已讀 {read_counts.get(message.message_id, 0)} 人</span>"
                )
            html = (
                "<div style='margin:8px 2px;padding:10px 12px;"
                "background:#ffffff;border:1px solid #d7e1eb;border-radius:9px;'>"
                "<table width='100%' cellspacing='0' cellpadding='4'><tr>"
                f"<td width='58' valign='top'>{avatar_html}</td>"
                "<td valign='top'>"
                f"<a href='{message_anchor}' style='text-decoration:none'>"
                f"<b style='font-size:14pt;color:#0f4c81'>{escape(message.sender)}</b></a>"
                f" <span style='color:#64748b'>"
                f"{escape(self._display_time(message.created_at_utc))}・"
                f"{escape(message.computer_name)}</span>"
                f"{reply_html}<br><a href='{message_anchor}' style='text-decoration:none'>"
                f"<span style='font-size:13pt;color:#172033'>{body}</span></a>"
                f"{invitation_html}{attachment_html}{reactions_html}{read_status}"
                "</td></tr></table></div>"
            )
            self.message_view.moveCursor(QTextCursor.End)
            range_start = self.message_view.textCursor().position()
            self.message_view.insertHtml(html)
            self.message_view.insertPlainText("\n")
            range_end = self.message_view.textCursor().position()
            self._message_ranges.append(
                (range_start, range_end, message.message_id)
            )
        self.message_view.moveCursor(QTextCursor.End)
        if follow_latest:
            scroll_bar.setValue(scroll_bar.maximum())
        else:
            scroll_bar.setValue(previous_scroll_value)

    def _activate_message_link(self, url):
        scheme = url.scheme()
        message_id = url.toString().split(":", 1)[-1]
        if scheme == "chat-message":
            return
        if scheme == "chat-image":
            path = self._attachment_paths.get(message_id)
            if path is None or not path.is_file():
                QMessageBox.warning(self, "圖片不存在", "找不到這則訊息的圖片檔案。")
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            return
        if scheme == "chat-invite":
            message = self._messages_by_id.get(message_id)
            metadata = message.metadata if message else None
            if not isinstance(metadata, dict):
                QMessageBox.warning(self, "邀請失效", "這則對戰邀請內容不完整。")
                return
            if callable(self.join_game_invitation_callback):
                self.join_game_invitation_callback(metadata)
