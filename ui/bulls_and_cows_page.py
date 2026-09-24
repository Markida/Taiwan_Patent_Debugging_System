"""Compact hidden-page UI for the offline four-digit 1A2B game."""

from __future__ import annotations

import socket
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.features.bulls_and_cows.engine import (
    BullsAndCowsError,
    LocalTwoPlayerGame,
    SinglePlayerGame,
)
from app.features.bulls_and_cows.network import BullsAndCowsClient, BullsAndCowsHost
from app.features.chat_room.arcade_lan import ArcadeRoom, ArcadeRoomDirectory
from app.features.chat_room.store import (
    ChatProfileStore,
    ChatRoomStore,
    publish_game_announcement,
    publish_game_invitation,
)
from app.features.chat_room.game_ranking import (
    GameRankingStore,
    award_multiplayer_victory,
)
from ui.arcade_navigation import ArcadeNavigationBar
from ui.arcade_skin_picker import ArcadeSkinPicker, scroll_lobby
from ui.arcade_skin_art import GuessReveal


class BullsAndCowsPage(QWidget):
    """A single-computer 1A2B page with one- and two-player modes."""

    def __init__(
        self,
        go_home_callback,
        open_chat_callback=None,
        open_pong_callback=None,
        open_tetris_callback=None,
        open_tank_callback=None,
        open_snake_callback=None,
        return_work_callback=None,
        *,
        single_game_factory=SinglePlayerGame,
        lan_host=None,
        lan_client=None,
        room_directory=None,
        chat_store=None,
        profile_store=None,
    ):
        super().__init__()
        self.go_home_callback = go_home_callback
        self.return_work_callback = return_work_callback
        self.single_game_factory = single_game_factory
        self.single_game: SinglePlayerGame | None = None
        self.two_player_game: LocalTwoPlayerGame | None = None
        self.mode = ""
        self.lan_host = lan_host or BullsAndCowsHost(self)
        self.lan_client = lan_client or BullsAndCowsClient(self)
        self.room_directory = room_directory or ArcadeRoomDirectory("bulls_and_cows", parent=self)
        self.chat_store = chat_store or ChatRoomStore()
        self.profile_store = profile_store or ChatProfileStore()
        self._known_rooms = {}
        self._hosted_room_id = ""
        self._lan_state = {}
        self._revealed_turn = 0
        self._reveal_match_id = ""
        self._announcement_sent = False
        self._build_ui(
            open_chat_callback,
            open_pong_callback,
            open_tetris_callback,
            open_tank_callback,
            open_snake_callback,
        )
        self._connect_lan()

    def _build_ui(
        self,
        open_chat_callback,
        open_pong_callback,
        open_tetris_callback,
        open_tank_callback,
        open_snake_callback,
    ):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 10, 18, 16)
        root.setSpacing(10)

        self.arcade_navigation = ArcadeNavigationBar(
            current_id="bulls_and_cows",
            title="1A2B",
            go_home_callback=self.go_home_callback,
            return_work_callback=self.return_work_callback,
            callbacks={
                "chat": open_chat_callback,
                "pong": open_pong_callback,
                "tetris": open_tetris_callback,
                "tank": open_tank_callback,
                "snake": open_snake_callback,
            },
        )
        self.return_work_button = self.arcade_navigation.return_work_button
        root.addWidget(self.arcade_navigation)

        self.pages = QStackedWidget()
        self.mode_page = self._build_mode_page()
        self.setup_page = self._build_setup_page()
        # The shared lobby is also the game home, matching the other arcade
        # pages instead of opening a separate mode-only splash screen.
        self.lan_lobby_page = self.mode_page
        self.game_page = self._build_game_page()
        self.pages.addWidget(self.mode_page)
        self.pages.addWidget(self.setup_page)
        self.pages.addWidget(self.game_page)
        root.addWidget(self.pages, 1)

    def _build_mode_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.addStretch()
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setMinimumWidth(640)
        layout = QGridLayout(panel)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)
        heading = QLabel("1A2B 遊戲大廳")
        heading.setObjectName("PanelTitle")
        heading.setAlignment(Qt.AlignCenter)
        layout.addWidget(heading, 0, 0, 1, 3)
        layout.addWidget(QLabel("玩家名稱："), 1, 0)
        self.lan_player_name = QLineEdit()
        self.lan_player_name.setReadOnly(True)
        self.lan_player_name.setObjectName("InputLine")
        self.lan_player_name.setPlaceholderText("請先在聊天室設定暱稱")
        layout.addWidget(self.lan_player_name, 1, 1, 1, 2)
        self.skin_picker = ArcadeSkinPicker("bulls_and_cows", self.profile_store, self.chat_store.root, self)
        layout.addWidget(self.skin_picker, 2, 0, 1, 3)

        hint = QLabel("四位不重複數字（首位可為 0）　A＝數字與位置正確　B＝數字正確但位置不同")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint, 3, 0, 1, 3)

        choices = QHBoxLayout()
        self.single_button = QPushButton("👤  單人挑戰")
        self.single_button.setObjectName("PrimaryButton")
        self.single_button.setMinimumHeight(48)
        self.single_button.clicked.connect(self.start_single_player)
        choices.addWidget(self.single_button)
        self.two_player_button = QPushButton("👥  雙人輪流")
        self.two_player_button.setObjectName("PrimaryButton")
        self.two_player_button.setMinimumHeight(48)
        self.two_player_button.clicked.connect(self.start_two_player)
        choices.addWidget(self.two_player_button)
        layout.addLayout(choices, 4, 0, 1, 3)

        self.lan_button = QPushButton("🌐  建立區網房間")
        self.lan_button.setObjectName("SecondaryButton")
        self.lan_button.clicked.connect(self.create_lan_room)
        layout.addWidget(self.lan_button, 5, 0, 1, 3)
        room_heading = QLabel("房間清單｜點選加入")
        room_heading.setObjectName("PanelTitle")
        layout.addWidget(room_heading, 6, 0, 1, 3)
        self.lan_room_list = QListWidget()
        self.lan_room_list.setObjectName("PongRoomList")
        self.lan_room_list.setMinimumHeight(180)
        self.lan_room_list.itemClicked.connect(self._join_room_item)
        layout.addWidget(self.lan_room_list, 7, 0, 1, 3)
        self.lan_status = QLabel("正在讀取房間清單…")
        self.lan_status.setObjectName("ArcadeStatus")
        self.lan_status.setAlignment(Qt.AlignCenter)
        self.lan_status.setWordWrap(True)
        layout.addWidget(self.lan_status, 8, 0, 1, 3)
        panel.setMaximumWidth(820)
        outer.addWidget(panel, alignment=Qt.AlignCenter)
        outer.addStretch()
        return scroll_lobby(page)

    def _build_setup_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.addStretch()
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(46, 34, 46, 34)
        layout.setSpacing(14)
        self.setup_title = QLabel("玩家 1 設定秘密數字")
        self.setup_title.setObjectName("PanelTitle")
        self.setup_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.setup_title)
        self.secret_input = self._number_input("輸入四位不重複數字")
        self.secret_input.setEchoMode(QLineEdit.Password)
        self.secret_input.returnPressed.connect(self.confirm_secret)
        layout.addWidget(self.secret_input)
        confirm_button = QPushButton("🔒  確認")
        confirm_button.setObjectName("PrimaryButton")
        confirm_button.clicked.connect(self.confirm_secret)
        layout.addWidget(confirm_button)
        back_button = QPushButton("返回模式選擇")
        back_button.setObjectName("SecondaryButton")
        back_button.clicked.connect(self.show_mode_page)
        layout.addWidget(back_button)
        outer.addWidget(panel, alignment=Qt.AlignHCenter)
        outer.addStretch()
        return page

    def _build_game_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(70, 25, 70, 25)
        layout.setSpacing(12)
        self.turn_label = QLabel()
        self.turn_label.setObjectName("ArcadeStatus")
        self.turn_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.turn_label)
        self.guess_reveal = GuessReveal()
        layout.addWidget(self.guess_reveal)

        input_row = QHBoxLayout()
        input_row.addStretch()
        self.guess_input = self._number_input("例如 1234")
        self.guess_input.returnPressed.connect(self.submit_guess)
        input_row.addWidget(self.guess_input)
        self.guess_button = QPushButton("🎯  猜")
        self.guess_button.setObjectName("PrimaryButton")
        self.guess_button.setMinimumHeight(48)
        self.guess_button.clicked.connect(self.submit_guess)
        input_row.addWidget(self.guess_button)
        input_row.addStretch()
        layout.addLayout(input_row)

        self.history_list = QListWidget()
        self.history_list.setObjectName("Panel")
        font = QFont(self.history_list.font())
        font.setPointSize(max(13, font.pointSize() + 2))
        self.history_list.setFont(font)
        self.history_pages = QStackedWidget()
        self.history_pages.addWidget(self.history_list)
        split_page = QWidget()
        split_layout = QHBoxLayout(split_page)
        split_layout.setContentsMargins(0, 0, 0, 0)
        split_layout.setSpacing(12)
        self.history_panels = {}
        self.history_headers = {}
        self.history_lists = {}
        for side in ("left", "right"):
            panel = QFrame()
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(10, 8, 10, 10)
            panel_layout.setSpacing(8)
            header = QLabel()
            header.setTextFormat(Qt.PlainText)
            header.setWordWrap(True)
            header.setMinimumHeight(38)
            header.setStyleSheet("color:#172033;font-weight:700;background:transparent;border:none;")
            records = QListWidget()
            records.setFont(font)
            records.setStyleSheet("QListWidget {background:#ffffff;color:#172033;border:none;}"
                                 "QListWidget::item {padding:6px 3px;}"
                                 "QListWidget::item:selected {background:#dbeafe;color:#172033;}")
            records.setAccessibleName("左側玩家猜測紀錄" if side=="left" else "右側玩家猜測紀錄")
            panel_layout.addWidget(header)
            panel_layout.addWidget(records, 1)
            split_layout.addWidget(panel, 1)
            self.history_panels[side] = panel
            self.history_headers[side] = header
            self.history_lists[side] = records
        self.left_history_list = self.history_lists["left"]
        self.right_history_list = self.history_lists["right"]
        self.history_pages.addWidget(split_page)
        layout.addWidget(self.history_pages, 1)

        controls = QHBoxLayout()
        controls.addStretch()
        new_button = QPushButton("↻  新遊戲")
        new_button.setObjectName("PrimaryButton")
        new_button.clicked.connect(self.restart_current_mode)
        controls.addWidget(new_button)
        mode_button = QPushButton("切換模式")
        mode_button.setObjectName("SecondaryButton")
        mode_button.clicked.connect(self.show_mode_page)
        controls.addWidget(mode_button)
        layout.addLayout(controls)
        return page

    def _clear_guess_history(self):
        self.history_list.clear()
        for records in self.history_lists.values():
            records.clear()

    @staticmethod
    def _sync_history_list(widget, texts):
        bar = widget.verticalScrollBar()
        following_latest = bar.value() >= bar.maximum()
        if widget.count() > len(texts) or any(
            widget.item(index).text() != text
            for index, text in enumerate(texts[:widget.count()])
        ):
            widget.clear()
        previous_count = widget.count()
        widget.addItems(texts[previous_count:])
        if following_latest and len(texts) > previous_count:
            widget.scrollToBottom()

    def _render_split_history(self, entries, names, active_side=None, local_side=None):
        self.history_pages.setCurrentIndex(1)
        for side, title in (("left", "房主" if self.mode.startswith("lan_") else "玩家 1"),
                            ("right", "訪客" if self.mode.startswith("lan_") else "玩家 2")):
            own_entries = [entry for entry in entries if entry.get("side") == side]
            texts = [
                f"#{int(entry.get('turn', 0)):02d}　{entry.get('guess', '')}　→　{entry.get('result', '')}"
                for entry in own_entries
            ]
            self._sync_history_list(self.history_lists[side], texts)
            name = str(names.get(side, title))
            identity = title if name == title else f"{title} · {name}"
            if side == local_side:
                identity += "（你）"
            self.history_headers[side].setText(
                identity + f"\n{len(own_entries)} 次猜測" + (" · 輪到此玩家" if side == active_side else "")
            )
            self.history_headers[side].setToolTip(identity)
            color = "#2563eb" if side == "left" else "#7c3aed"
            border = color if side == active_side else "#cbd5e1"
            self.history_panels[side].setStyleSheet(
                f"QFrame {{background:#ffffff;border:2px solid {border};border-radius:8px;}}"
            )

    def _render_local_two_history(self):
        game = self.two_player_game
        entries = [
            {"turn": record.turn, "side": "left" if record.player_index == 0 else "right",
             "guess": record.guess, "result": record.result.notation}
            for record in game.history
        ]
        self._render_split_history(
            entries, dict(zip(("left", "right"), game.player_names)),
            None if game.finished else ("left" if game.active_player == 0 else "right"),
        )

    def _number_input(self, placeholder):
        line_edit = QLineEdit()
        line_edit.setPlaceholderText(placeholder)
        line_edit.setMaxLength(4)
        line_edit.setAlignment(Qt.AlignCenter)
        line_edit.setMinimumSize(260, 48)
        font = QFont(line_edit.font())
        font.setPointSize(18)
        font.setBold(True)
        line_edit.setFont(font)
        return line_edit

    def set_return_work_available(self, available):
        self.return_work_button.setEnabled(bool(available) and callable(self.return_work_callback))

    def _connect_lan(self):
        self.room_directory.rooms_received.connect(self._update_lan_rooms)
        self.room_directory.error_occurred.connect(self.lan_status.setText)
        self.lan_host.state_changed.connect(self._apply_lan_state)
        self.lan_host.guest_joined.connect(self._lan_guest_joined)
        self.lan_host.guest_left.connect(self._lan_guest_left)
        self.lan_host.match_finished.connect(self._lan_match_finished)
        self.lan_client.state_changed.connect(self._apply_lan_state)
        self.lan_client.status_changed.connect(self.lan_status.setText)
        self.lan_client.error_occurred.connect(
            lambda message: QMessageBox.warning(self, "區網連線失敗", str(message))
        )
        self.lan_client.match_finished.connect(self._lan_match_finished)

    def prepare_game(self):
        """Open the lobby without discarding an unfinished local round."""

        self.room_directory.start()
        try:
            self.lan_player_name.setText(self.profile_store.load_profile().nickname)
        except Exception as error:
            self.lan_status.setText(f"無法讀取聊天室暱稱：{error}")
        ready = bool(self.lan_player_name.text().strip())
        self.lan_button.setEnabled(ready)
        self.lan_room_list.setEnabled(ready)
        if not ready:
            self.lan_status.setText("請先回聊天室設定暱稱，再建立或加入區網房間。")
        if not self.mode:
            self.pages.setCurrentWidget(self.mode_page)

    def abort_current_workflow(self):
        """ESC returns immediately without leaving a modal/setup state behind."""

        self.show_mode_page()

    def show_mode_page(self):
        if self.mode in {"lan_host", "lan_client"}:
            self.shutdown_lan_match()
        self.mode = ""
        self.guess_input.clear()
        self._clear_guess_history()
        self.pages.setCurrentWidget(self.mode_page)

    def show_lan_lobby(self):
        self.mode = ""
        self.room_directory.start()
        try:
            self.lan_player_name.setText(self.profile_store.load_profile().nickname)
        except Exception as error:
            self.lan_status.setText(str(error))
        ready = bool(self.lan_player_name.text().strip())
        self.lan_button.setEnabled(ready)
        self.lan_room_list.setEnabled(ready)
        self.pages.setCurrentWidget(self.mode_page)

    def _player_name(self):
        name = self.lan_player_name.text().strip()
        if not name:
            QMessageBox.warning(self, "尚未設定暱稱", "請先回聊天室設定暱稱。")
        return name

    def _update_lan_rooms(self, rooms):
        self._known_rooms = {room.room_id: room for room in rooms}
        self.lan_room_list.clear()
        if not rooms:
            self.lan_room_list.addItem("目前沒有房間；你可以建立一個新房間。")
            self.lan_room_list.item(0).setFlags(self.lan_room_list.item(0).flags() & ~Qt.ItemIsEnabled)
            return
        for room in rooms:
            full = room.player_count >= 2 or room.status != "waiting"
            item = QListWidgetItem(
                f"{room.host_name} 的房間｜{room.player_count}/2｜"
                f"{'已滿' if full else '點一下加入'}"
            )
            item.setData(Qt.UserRole, room.room_id)
            if full:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.lan_room_list.addItem(item)

    def create_lan_room(self):
        name = self._player_name()
        if not name:
            return
        self.shutdown_lan_match()
        try:
            self.lan_host.create_room(
                name,
                user_id=self.profile_store.load_profile().user_id,
                skin_id=self.skin_picker.skin_id(),
            )
        except RuntimeError as error:
            QMessageBox.warning(self, "房間建立失敗", str(error))
            return
        self.mode = "lan_host"
        self._hosted_room_id = uuid4().hex
        self._announcement_sent = False
        self._publish_lan_room(1, "waiting")
        room_payload = self._current_room().to_payload()
        publish_game_invitation(
            "bulls_and_cows",
            "1A2B",
            room_payload,
            name,
            store=self.chat_store,
        )
        self.setup_title.setText("設定你的秘密數字｜等待對手加入")
        self.secret_input.clear()
        self.pages.setCurrentWidget(self.setup_page)

    def _join_room_item(self, item):
        self.join_lan_room(self._known_rooms.get(str(item.data(Qt.UserRole) or "")))

    def join_lan_room(self, room):
        if room is None or room.player_count >= 2 or room.status != "waiting":
            QMessageBox.information(self, "無法加入", "這個房間目前已滿或已開始。")
            return
        name = self._player_name()
        if not name:
            return
        self.shutdown_lan_match()
        try:
            self.lan_client.connect_to_room(
                room.room_code,
                name,
                self.profile_store.load_profile().user_id,
                skin_id=self.skin_picker.skin_id(),
            )
        except (TypeError, ValueError) as error:
            QMessageBox.warning(self, "房間資料錯誤", str(error))
            return
        self.mode = "lan_client"
        self.setup_title.setText("設定你的秘密數字")
        self.secret_input.clear()
        self.pages.setCurrentWidget(self.setup_page)

    def join_invitation(self, payload):
        room = ArcadeRoom.from_payload(payload)
        self.show_lan_lobby()
        self.join_lan_room(room)

    def _current_room(self):
        return ArcadeRoom(
            game_id="bulls_and_cows",
            room_id=self._hosted_room_id,
            room_code=self.lan_host.room_code,
            host_name=self.lan_player_name.text().strip(),
            computer_name=socket.gethostname(),
            player_count=1,
            status="waiting",
        )

    def _publish_lan_room(self, player_count, status):
        if not self._hosted_room_id or not self.lan_host.room_code:
            return
        room = self._current_room()
        self.room_directory.set_hosted_room(ArcadeRoom(
            **{**room.__dict__, "player_count": player_count, "status": status}
        ))

    def _lan_guest_joined(self, _name):
        self._publish_lan_room(2, "setup")

    def _lan_guest_left(self):
        if self.mode == "lan_host":
            self._publish_lan_room(1, "waiting")

    def _apply_lan_state(self, state):
        self._lan_state = dict(state or {})
        if self.mode not in {"lan_host", "lan_client"}:
            return
        match_id = self._lan_state.get("match_id","")
        if match_id != self._reveal_match_id:
            self._reveal_match_id = match_id
            self._revealed_turn = 0
            self.guess_reveal.reset()
            self._clear_guess_history()
        history = self._lan_state.get("history", [])
        if history and len(history) > self._revealed_turn:
            entry = history[-1]
            self.guess_reveal.reveal(entry.get("guess",""),entry.get("result",""),
                                     entry.get("name","玩家"),self._lan_state.get(entry.get("side","left")+"_skin","classic"))
        self._revealed_turn = len(history)
        status = self._lan_state.get("status")
        self._render_split_history(
            history,
            {"left": self._lan_state.get("left_name", "房主"),
             "right": self._lan_state.get("right_name", "訪客")},
            self._lan_state.get("active_side") if status == "playing" else None,
            "left" if self.mode == "lan_host" else "right",
        )
        if status in {"playing", "finished"}:
            self.pages.setCurrentWidget(self.game_page)
        active_side = self._lan_state.get("active_side")
        local_side = "left" if self.mode == "lan_host" else "right"
        local_turn = status == "playing" and active_side == local_side
        self.guess_input.setEnabled(local_turn)
        self.guess_button.setEnabled(local_turn)
        if status == "finished":
            winner = (
                self._lan_state.get("left_name")
                if self._lan_state.get("winner") == "left"
                else self._lan_state.get("right_name")
            )
            self.turn_label.setText(f"🏆 {winner} 勝利")
        elif status == "playing":
            active_name = (
                self._lan_state.get("left_name")
                if active_side == "left"
                else self._lan_state.get("right_name")
            )
            self.turn_label.setText(
                f"輪到 {active_name}" + ("｜請輸入猜測" if local_turn else "｜等待對手")
            )
        else:
            self.turn_label.setText("等待兩位玩家完成秘密數字設定")

    def _lan_match_finished(self, winner):
        if self.mode != "lan_host" or self._announcement_sent:
            return
        self._announcement_sent = True
        self._publish_lan_room(2, "finished")
        loser = self.lan_host.guest_name if winner == self.lan_host.host_name else self.lan_host.host_name
        publish_game_announcement(
            f'"{winner}"在 1A2B 區網對戰中猜贏了"{loser}"',
            store=self.chat_store,
        )
        award_multiplayer_victory(
            winner,
            "bulls_and_cows",
            match_id=self._hosted_room_id,
            user_id=self.lan_host.winner_user_id(),
            computer_name=self.lan_host.winner_computer_name(),
            store=GameRankingStore(self.chat_store.root),
        )

    def shutdown_lan_match(self):
        self.room_directory.clear_hosted_room()
        self.lan_host.close()
        self.lan_client.disconnect()
        self._hosted_room_id = ""

    def start_single_player(self):
        self._local_skin_id = self.skin_picker.skin_id()
        self.guess_reveal.reset(self._local_skin_id)
        self.mode = "single"
        self.history_pages.setCurrentIndex(0)
        self.single_game = self.single_game_factory()
        self.two_player_game = None
        self._clear_guess_history()
        self.pages.setCurrentWidget(self.game_page)
        self.guess_input.clear()
        self._refresh_turn_label()

    def start_two_player(self):
        self._local_skin_id = self.skin_picker.skin_id()
        self.guess_reveal.reset(self._local_skin_id)
        self.mode = "two"
        self.two_player_game = LocalTwoPlayerGame()
        self.single_game = None
        self._clear_guess_history()
        self._render_local_two_history()
        self._show_secret_setup(0)

    def _show_secret_setup(self, player_index):
        self.setup_title.setText(f"玩家 {player_index + 1} 設定秘密數字")
        self.secret_input.clear()
        self.pages.setCurrentWidget(self.setup_page)
        self.secret_input.setFocus(Qt.OtherFocusReason)

    def confirm_secret(self):
        if self.mode in {"lan_host", "lan_client"}:
            try:
                if self.mode == "lan_host":
                    self.lan_host.set_secret("left", self.secret_input.text())
                else:
                    self.lan_client.set_secret(self.secret_input.text())
            except BullsAndCowsError as error:
                self._warn(str(error))
                return
            self.secret_input.clear()
            self.pages.setCurrentWidget(self.game_page)
            self.turn_label.setText("秘密數字已鎖定｜等待對手完成設定")
            self.guess_input.setEnabled(False)
            self.guess_button.setEnabled(False)
            return
        game = self.two_player_game
        if game is None:
            return
        player_index = game.setup_player_index
        if player_index is None:
            return
        try:
            game.set_secret(player_index, self.secret_input.text())
        except BullsAndCowsError as error:
            self._warn(str(error))
            return
        self.secret_input.clear()
        if not game.ready:
            QMessageBox.information(self, "換手", "請將畫面交給玩家 2。")
            self._show_secret_setup(1)
            return
        QMessageBox.information(self, "開始", "秘密數字已設定。請將畫面交給玩家 1。")
        self.pages.setCurrentWidget(self.game_page)
        self._refresh_turn_label()
        self.guess_input.setFocus(Qt.OtherFocusReason)

    def submit_guess(self):
        guess = self.guess_input.text()
        try:
            if self.mode == "single" and self.single_game is not None:
                record = self.single_game.submit_guess(guess)
                player_name = "你"
            elif self.mode == "two" and self.two_player_game is not None:
                player_index = self.two_player_game.active_player
                player_name = self.two_player_game.player_names[player_index]
                record = self.two_player_game.submit_guess(guess)
            elif self.mode == "lan_host":
                self.lan_host.submit_guess("left", guess)
                self.guess_input.clear()
                return
            elif self.mode == "lan_client":
                self.lan_client.submit_guess(guess)
                self.guess_input.clear()
                return
            else:
                return
        except BullsAndCowsError as error:
            self._warn(str(error))
            return

        self.guess_reveal.reveal(record.guess,record.result.notation,player_name,self._local_skin_id)
        if self.mode == "two":
            self._render_local_two_history()
        else:
            self.history_list.addItem(
                f"#{record.turn:02d}　{player_name}　{record.guess}　→　{record.result.notation}"
            )
            self.history_list.scrollToBottom()
        self.guess_input.clear()
        if record.result.won:
            self.guess_button.setEnabled(False)
            self.guess_input.setEnabled(False)
            self.turn_label.setText(f"🏆 {player_name} 勝利")
            return
        if self.mode == "two" and self.two_player_game is not None:
            next_name = self.two_player_game.player_names[self.two_player_game.active_player]
            QMessageBox.information(self, "換手", f"請將畫面交給{next_name}。")
        self._refresh_turn_label()

    def _refresh_turn_label(self):
        self.guess_button.setEnabled(True)
        self.guess_input.setEnabled(True)
        if self.mode == "single":
            self.turn_label.setText("找出電腦的秘密數字")
        elif self.two_player_game is not None:
            name = self.two_player_game.player_names[self.two_player_game.active_player]
            self.turn_label.setText(f"輪到{name}")
        self.guess_input.setFocus(Qt.OtherFocusReason)

    def restart_current_mode(self):
        if self.mode in {"lan_host", "lan_client"}:
            self.shutdown_lan_match()
            self.show_lan_lobby()
            return
        if self.mode == "two":
            self.start_two_player()
        else:
            self.start_single_player()

    def _warn(self, message):
        QMessageBox.warning(self, "輸入格式不正確", message)

    def shutdown(self):
        self.shutdown_lan_match()
        self.room_directory.stop()
