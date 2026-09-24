"""Hidden 2–4 player tank deathmatch lobby and battlefield."""

from __future__ import annotations

import socket
from uuid import uuid4

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.features.chat_room.store import (
    ChatProfileStore,
    ChatRoomStore,
    format_ai_victory_announcement,
    publish_game_announcement,
    publish_game_invitation,
)
from app.features.tank_battle.network import (
    TANK_MAP_CHOICES,
    TankBattleEngine,
    TankClient,
    TankHost,
    TankRoom,
    TankRoomDirectory,
)
from app.features.chat_room.game_ranking import (
    GameRankingStore,
    award_multiplayer_victory,
)
from ui.arcade_navigation import ArcadeNavigationBar
from ui.arcade_skin_picker import ArcadeSkinPicker, scroll_lobby
from app.features.arcade_cosmetics import arcade_skin
from ui.arcade_skin_art import ArcadeEffects, paint_tank
from ui.arcade_particles import paint_glow, paint_light_trail


class TankBattleBoard(QWidget):
    action_requested = Signal(str)
    PLAYER_COLORS = (
        QColor("#38bdf8"),
        QColor("#fb7185"),
        QColor("#4ade80"),
        QColor("#facc15"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(560, 400)
        self.setFocusPolicy(Qt.StrongFocus)
        self.local_slot = 0
        self.state = TankBattleEngine().to_state()
        self.effects = ArcadeEffects(self)

    def set_state(self, state):
        self.effects.consume(state or {})
        self.state = dict(state or {})
        self.update()

    def keyPressEvent(self, event):
        action = {
            Qt.Key_Up: "up",
            Qt.Key_W: "up",
            Qt.Key_Down: "down",
            Qt.Key_S: "down",
            Qt.Key_Left: "left",
            Qt.Key_A: "left",
            Qt.Key_Right: "right",
            Qt.Key_D: "right",
            Qt.Key_Space: "shoot",
        }.get(event.key())
        if action:
            self.action_requested.emit(action)
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#07111f"))
        width = int(self.state.get("width", 24))
        height = int(self.state.get("height", 18))
        cell = min((self.width() - 26) / width, (self.height() - 92) / height)
        start_x = (self.width() - cell * width) / 2
        start_y = 70.0

        def cell_rect(x, y):
            return QRectF(start_x + x * cell, start_y + y * cell, cell, cell)

        terrain = self.state.get("map") or [[0] * width for _ in range(height)]
        for y in range(height):
            for x in range(width):
                value = int(terrain[y][x])
                rectangle = cell_rect(x, y)
                painter.fillRect(rectangle, QColor("#111827"))
                if value == 1:
                    painter.fillRect(rectangle.adjusted(1, 1, -1, -1), QColor("#a84b2a"))
                    painter.setPen(QPen(QColor("#e07a45"), 1))
                    painter.drawLine(rectangle.left(), rectangle.center().y(), rectangle.right(), rectangle.center().y())
                elif value == 2:
                    painter.fillRect(rectangle.adjusted(1, 1, -1, -1), QColor("#64748b"))
                    painter.setPen(QPen(QColor("#cbd5e1"), 2))
                    painter.drawRect(rectangle.adjusted(3, 3, -3, -3))
                elif value == 3:
                    painter.fillRect(rectangle.adjusted(1, 1, -1, -1), QColor("#075985"))
                    painter.setPen(QPen(QColor("#38bdf8"), 1))
                    painter.drawLine(rectangle.left() + 3, rectangle.center().y(), rectangle.right() - 3, rectangle.center().y())
                painter.setPen(QPen(QColor("#18283b"), 1))
                painter.drawRect(rectangle)

        players = self.state.get("players") or []
        for player in players:
            if not player.get("alive", True):
                continue
            slot = int(player.get("slot", 0))
            rectangle = cell_rect(player.get("x", 0), player.get("y", 0)).adjusted(3, 3, -3, -3)
            color = self.PLAYER_COLORS[slot % len(self.PLAYER_COLORS)]
            paint_tank(painter, rectangle, arcade_skin("tank",player.get("skin_id")),
                       player.get("direction","up"),color.name(),slot==self.local_slot,slot)
            if int(player.get("shield", 0)) > 0:
                paint_glow(painter, rectangle.center(), cell*.95, "#7dd3fc", .4)
                painter.setPen(QPen(QColor("#7dd3fc"), 2, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(rectangle.adjusted(-2, -2, 2, 2))

        painter.setBrush(QColor("#ffffff"))
        painter.setPen(Qt.NoPen)
        for bullet in self.state.get("bullets") or []:
            rectangle = cell_rect(bullet.get("x", 0), bullet.get("y", 0))
            radius = max(2.0, cell * 0.13)
            owner = next((player for player in players if player.get("slot")==bullet.get("owner")), {})
            skin = arcade_skin("tank",owner.get("skin_id"))
            painter.setBrush(QColor(skin.accent))
            painter.setPen(QPen(QColor(skin.accent),max(1,cell*.08)))
            center=rectangle.center()
            tail=center-QPointF(bullet.get("dx",0)*cell*1.2,bullet.get("dy",0)*cell*1.2)
            paint_light_trail(painter,[tail,center],skin.accent,max(1.5,cell*.15),.8)
            paint_glow(painter,center,cell*.75,skin.accent,.85)
            painter.drawLine(center,center-QPointF(bullet.get("dx",0)*cell*.45,bullet.get("dy",0)*cell*.45))
            painter.drawEllipse(center, radius, radius)

        heading_font = QFont(self.font())
        heading_font.setBold(True)
        heading_font.setPointSize(12)
        painter.setFont(heading_font)
        summary = []
        for player in players:
            slot = int(player.get("slot", 0))
            summary.append(
                f"P{slot + 1} {player.get('name', '玩家')}：{player.get('lives', 0)} 命"
                + (f"（隊伍 {int(player.get('team',0))+1}）" if self.state.get("team_mode")=="teams" else "")
            )
        painter.setPen(QColor("#e2e8f0"))
        painter.drawText(
            QRectF(10, 8, self.width() - 20, 48),
            Qt.AlignCenter | Qt.TextWordWrap,
            "　｜　".join(summary),
        )

        status = self.state.get("status", "waiting")
        if status != "playing":
            painter.fillRect(self.rect(), QColor(2, 10, 23, 155))
            overlay_font = QFont(self.font())
            overlay_font.setBold(True)
            overlay_font.setPointSize(25)
            painter.setFont(overlay_font)
            painter.setPen(QColor("#ffffff"))
            text = (
                f"{self.state.get('winner_name', '')} 勝利！"
                if status == "finished"
                else "等待房間補滿…"
            )
            painter.drawText(self.rect(), Qt.AlignCenter, text)
        self.effects.paint(painter,lambda x,y:cell_rect(x,y).center(),cell)
        painter.end()


class TankBattlePage(QWidget):
    def __init__(
        self,
        go_home_callback,
        open_chat_callback=None,
        open_pong_callback=None,
        open_tetris_callback=None,
        open_snake_callback=None,
        open_bulls_cows_callback=None,
        return_work_callback=None,
        *,
        host=None,
        client=None,
        room_directory=None,
        chat_store=None,
        profile_store=None,
    ):
        super().__init__()
        self.go_home_callback = go_home_callback
        self.open_chat_callback = open_chat_callback
        self.open_pong_callback = open_pong_callback
        self.open_tetris_callback = open_tetris_callback
        self.open_snake_callback = open_snake_callback
        self.open_bulls_cows_callback = open_bulls_cows_callback
        self.return_work_callback = return_work_callback
        self.host = host or TankHost(self)
        self.client = client or TankClient(self)
        self.room_directory = room_directory or TankRoomDirectory(parent=self)
        self.chat_store = chat_store or ChatRoomStore()
        self.profile_store = profile_store or ChatProfileStore()
        self.mode = ""
        self._hosted_room_id = ""
        self._known_rooms = {}
        self._announcement_sent = False
        self.local_engine = TankBattleEngine()
        self.local_timer = QTimer(self)
        self.local_timer.setInterval(90)
        self.local_timer.timeout.connect(self._advance_cpu_match)
        self._ai_difficulty = "normal"
        self._build_ui()
        self._connect_network()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 14)
        root.setSpacing(8)
        self.arcade_navigation = ArcadeNavigationBar(
            current_id="tank",
            title="TANK",
            go_home_callback=self.go_home_callback,
            return_work_callback=self.return_work_callback,
            callbacks={
                "chat": self.open_chat_callback,
                "pong": self.open_pong_callback,
                "tetris": self.open_tetris_callback,
                "bulls_and_cows": self.open_bulls_cows_callback,
                "snake": self.open_snake_callback,
            },
        )
        self.return_work_button = self.arcade_navigation.return_work_button
        root.addWidget(self.arcade_navigation)

        self.pages = QStackedWidget()
        self.lobby_page = self._build_lobby()
        self.game_page = self._build_game()
        self.pages.addWidget(self.lobby_page)
        self.pages.addWidget(self.game_page)
        root.addWidget(self.pages, 1)

    def _build_lobby(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setMinimumWidth(640)
        grid = QGridLayout(panel)
        grid.setContentsMargins(30, 24, 30, 24)
        grid.setSpacing(12)
        heading = QLabel("坦克大戰死鬥大廳")
        heading.setObjectName("PanelTitle")
        heading.setAlignment(Qt.AlignCenter)
        grid.addWidget(heading, 0, 0, 1, 4)
        grid.addWidget(QLabel("玩家名稱："), 1, 0)
        self.player_name = QLineEdit()
        self.player_name.setObjectName("InputLine")
        self.player_name.setReadOnly(True)
        grid.addWidget(self.player_name, 1, 1, 1, 3)
        self.skin_picker = ArcadeSkinPicker("tank", self.profile_store, self.chat_store.root, self)
        grid.addWidget(self.skin_picker, 2, 0, 1, 4)
        grid.addWidget(QLabel("戰場地圖："), 3, 0)
        self.map_choice = QComboBox()
        self.map_choice.setObjectName("InputLine")
        for map_id, map_name in TANK_MAP_CHOICES:
            self.map_choice.addItem(map_name, map_id)
        grid.addWidget(self.map_choice, 3, 1, 1, 3)
        grid.addWidget(QLabel("房間人數："), 4, 0)
        self.max_players = QSpinBox()
        self.max_players.setRange(2, 4)
        self.max_players.setValue(2)
        self.max_players.valueChanged.connect(self._sync_team_option)
        grid.addWidget(self.max_players, 4, 1)
        self.team_mode = QCheckBox("四人時採 2 對 2 分組")
        self.team_mode.setEnabled(False)
        grid.addWidget(self.team_mode, 4, 2, 1, 2)
        grid.addWidget(QLabel("AI："), 5, 0)
        self.cpu_count = QSpinBox()
        self.cpu_count.setRange(1, 3)
        self.cpu_count.setValue(1)
        self.cpu_count.setPrefix("× ")
        grid.addWidget(self.cpu_count, 5, 1)
        self.ai_difficulty = QComboBox()
        self.ai_difficulty.setObjectName("InputLine")
        self.ai_difficulty.addItem("簡單", "easy")
        self.ai_difficulty.addItem("普通", "normal")
        self.ai_difficulty.addItem("困難", "hard")
        self.ai_difficulty.setCurrentIndex(1)
        grid.addWidget(self.ai_difficulty, 5, 2, 1, 2)
        self.create_button = QPushButton("＋  建立區網房間")
        self.create_button.setObjectName("PrimaryButton")
        self.create_button.clicked.connect(self.create_room)
        grid.addWidget(self.create_button, 6, 0, 1, 2)
        self.cpu_button = QPushButton("▶  與電腦遊玩")
        self.cpu_button.setObjectName("SecondaryButton")
        self.cpu_button.clicked.connect(self.start_cpu_match)
        grid.addWidget(self.cpu_button, 6, 2, 1, 2)
        room_heading = QLabel("房間清單｜點選加入")
        room_heading.setObjectName("PanelTitle")
        grid.addWidget(room_heading, 7, 0, 1, 4)
        self.room_list = QListWidget()
        self.room_list.setObjectName("PongRoomList")
        self.room_list.setMinimumHeight(190)
        self.room_list.itemClicked.connect(self._join_room_item)
        grid.addWidget(self.room_list, 8, 0, 1, 4)
        instructions = QLabel(
            "⌨  WASD／方向鍵移動　Space 射擊　　♥×3"
        )
        instructions.setAlignment(Qt.AlignCenter)
        instructions.setWordWrap(True)
        grid.addWidget(instructions, 9, 0, 1, 4)
        self.lobby_status = QLabel("正在讀取目前房間清單…")
        self.lobby_status.setObjectName("ArcadeStatus")
        self.lobby_status.setAlignment(Qt.AlignCenter)
        self.lobby_status.setWordWrap(True)
        grid.addWidget(self.lobby_status, 10, 0, 1, 4)
        panel.setMaximumWidth(820)
        layout.addWidget(panel, alignment=Qt.AlignCenter)
        layout.addStretch()
        return scroll_lobby(page)

    def _build_game(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.mode_label = QLabel("尚未開始")
        self.mode_label.setObjectName("PanelTitle")
        row.addWidget(self.mode_label)
        self.game_status = QLabel("")
        self.game_status.setObjectName("ArcadeStatus")
        self.game_status.setAlignment(Qt.AlignCenter)
        self.game_status.setWordWrap(True)
        row.addWidget(self.game_status, 1)
        leave = QPushButton("離開比賽並返回大廳")
        leave.setObjectName("SecondaryButton")
        leave.clicked.connect(self.leave_match)
        row.addWidget(leave)
        layout.addLayout(row)
        self.board = TankBattleBoard()
        self.board.action_requested.connect(self._send_action)
        layout.addWidget(self.board, 1)
        hint = QLabel("⌨  WASD／方向鍵移動　Space 射擊　｜　磚・鋼・水")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)
        return page

    def _connect_network(self):
        self.host.state_changed.connect(self.board.set_state)
        self.host.status_changed.connect(self.game_status.setText)
        self.host.player_count_changed.connect(self._host_player_count_changed)
        self.host.match_finished.connect(self._match_finished)
        self.client.state_changed.connect(self.board.set_state)
        self.client.status_changed.connect(self.game_status.setText)
        self.client.error_occurred.connect(self._network_error)
        self.client.slot_changed.connect(self._client_slot_changed)
        self.client.match_finished.connect(self._match_finished)
        self.room_directory.rooms_received.connect(self._update_room_list)
        self.room_directory.error_occurred.connect(self.lobby_status.setText)

    def set_return_work_available(self, available):
        self.return_work_button.setEnabled(bool(available))

    def _sync_team_option(self, value):
        self.team_mode.setEnabled(int(value) == 4)
        if int(value) != 4:
            self.team_mode.setChecked(False)

    def prepare_lobby(self):
        self.room_directory.start()
        try:
            nickname = self.profile_store.load_profile().nickname
        except Exception as error:
            nickname = ""
            self.lobby_status.setText(f"無法讀取聊天室暱稱：{error}")
        self.player_name.setText(nickname)
        ready = bool(nickname)
        self.create_button.setEnabled(ready)
        self.cpu_button.setEnabled(ready)
        self.room_list.setEnabled(ready)
        if not ready:
            self.lobby_status.setText(
                "請先回聊天室設定暱稱，再建立、加入或進行單機對戰。"
            )
        if self.mode in {"host", "client", "cpu"}:
            self.pages.setCurrentWidget(self.game_page)
            self.board.setFocus(Qt.OtherFocusReason)
        else:
            self.pages.setCurrentWidget(self.lobby_page)
            self.room_list.setFocus(Qt.OtherFocusReason)

    def _player_name(self):
        name = self.player_name.text().strip()
        if not name:
            QMessageBox.warning(self, "尚未設定暱稱", "請先在聊天室設定暱稱。")
        return name

    def _update_room_list(self, rooms):
        self._known_rooms = {room.room_id: room for room in rooms}
        self.room_list.clear()
        if not rooms:
            item = QListWidgetItem("目前沒有坦克房間；你可以建立一個新房間。")
            item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.room_list.addItem(item)
            return
        for room in rooms:
            full = room.player_count >= room.max_players
            mode = "2 對 2" if room.team_mode == "teams" else "各自為戰"
            map_names = dict(TANK_MAP_CHOICES)
            map_name = map_names.get(room.map_id, map_names[TANK_MAP_CHOICES[0][0]])
            suffix = "已滿" if full else "點一下加入"
            item = QListWidgetItem(
                f"{room.host_name}｜{map_name}｜{room.player_count}/{room.max_players}｜"
                f"{mode}｜{suffix}"
            )
            item.setData(Qt.UserRole, room.room_id)
            if full or room.status != "waiting":
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.room_list.addItem(item)

    def _join_room_item(self, item):
        self.join_room(self._known_rooms.get(str(item.data(Qt.UserRole) or "")))

    def join_room(self, room):
        if room is None or room.player_count >= room.max_players or room.status != "waiting":
            QMessageBox.information(self, "無法加入", "這個房間目前已滿或已開始。")
            return
        name = self._player_name()
        if not name:
            return
        try:
            self.client.connect_to_room(
                room.room_code,
                name,
                self.profile_store.load_profile().user_id,
                skin_id=self.skin_picker.skin_id(),
            )
        except ValueError as error:
            QMessageBox.warning(self, "房間資料錯誤", str(error))
            return
        self.mode = "client"
        self.mode_label.setText("區網坦克死鬥｜等待分配位置")
        self.pages.setCurrentWidget(self.game_page)
        self.board.setFocus(Qt.OtherFocusReason)

    def create_room(self):
        name = self._player_name()
        if not name:
            return
        maximum = self.max_players.value()
        team_mode = "teams" if maximum == 4 and self.team_mode.isChecked() else "ffa"
        map_id = str(self.map_choice.currentData() or TANK_MAP_CHOICES[0][0])
        try:
            code = self.host.create_room(
                name,
                maximum,
                team_mode,
                map_id=map_id,
                user_id=self.profile_store.load_profile().user_id,
                skin_id=self.skin_picker.skin_id(),
            )
        except RuntimeError as error:
            QMessageBox.warning(self, "房間建立失敗", str(error))
            return
        self.mode = "host"
        self.board.local_slot = 0
        self._hosted_room_id = uuid4().hex
        self._announcement_sent = False
        self._publish_host_room(1, "waiting")
        room_payload = {
            "room_id": self._hosted_room_id,
            "room_code": code,
            "host_name": name,
            "computer_name": socket.gethostname(),
            "player_count": 1,
            "max_players": maximum,
            "team_mode": team_mode,
            "map_id": map_id,
            "status": "waiting",
        }
        publish_game_invitation(
            "tank",
            "坦克大戰",
            room_payload,
            name,
            store=self.chat_store,
        )
        self.mode_label.setText("區網坦克死鬥｜你是 P1 房主")
        self.pages.setCurrentWidget(self.game_page)
        self.board.setFocus(Qt.OtherFocusReason)

    def start_cpu_match(self):
        name = self._player_name()
        if not name:
            return
        self.shutdown_match()
        count = 1 + self.cpu_count.value()
        map_id = str(self.map_choice.currentData() or TANK_MAP_CHOICES[0][0])
        self._ai_difficulty = str(self.ai_difficulty.currentData() or "normal")
        self.local_engine.reset(
            [name] + [f"電腦{i}" for i in range(1, count)],
            "ffa",
            map_id,
            skins=[self.skin_picker.skin_id()],
        )
        self.mode = "cpu"
        self.board.local_slot = 0
        self.board.set_state(self.local_engine.to_state())
        self.mode_label.setText(f"單機坦克死鬥｜你對 {count - 1} 名電腦")
        self.game_status.setText(
            f"{self.map_choice.currentText()}｜{self.ai_difficulty.currentText()}｜三條命"
        )
        self.pages.setCurrentWidget(self.game_page)
        self.local_timer.start()
        self.board.setFocus(Qt.OtherFocusReason)

    def _advance_cpu_match(self):
        if self.local_engine.status != "playing":
            self.local_timer.stop()
            self._match_finished(self.local_engine.winner_name())
            return
        for slot in range(1, len(self.local_engine.players)):
            self.local_engine.ai_action(slot, self._ai_difficulty)
        self.local_engine.tick()
        self.board.set_state(self.local_engine.to_state())

    def _send_action(self, action):
        if self.mode == "host":
            self.host.action(action)
        elif self.mode == "client":
            self.client.send_action(action)
        elif self.mode == "cpu":
            self.local_engine.action(0, action)
            self.board.set_state(self.local_engine.to_state())

    def _client_slot_changed(self, slot):
        self.board.local_slot = int(slot)
        self.mode_label.setText(f"區網坦克死鬥｜你是 P{int(slot) + 1}")

    def _host_player_count_changed(self, count):
        status = "playing" if count >= self.host.max_players else "waiting"
        self._publish_host_room(count, status)

    def _publish_host_room(self, player_count, status):
        if not self._hosted_room_id or not self.host.room_code:
            return
        self.room_directory.set_hosted_room(TankRoom(
            room_id=self._hosted_room_id,
            room_code=self.host.room_code,
            host_name=self.player_name.text().strip(),
            computer_name=socket.gethostname(),
            player_count=player_count,
            max_players=self.host.max_players,
            team_mode=self.host.team_mode,
            status=status,
            map_id=self.host.map_id,
        ))

    def _match_finished(self, winner):
        self.game_status.setText(f"比賽結束：{winner} 勝利")
        if self.mode not in {"host", "cpu"} or self._announcement_sent:
            return
        self._announcement_sent = True
        if self.mode == "cpu" and winner == self.local_engine.players.get(0, {}).get("name"):
            report = format_ai_victory_announcement(
                winner,
                "坦克大戰",
                self._ai_difficulty,
            )
        else:
            report = f'"{winner}"剛剛贏得坦克大戰死鬥！'
        publish_game_announcement(
            report,
            store=self.chat_store,
        )
        if self.mode == "host":
            ranking_store = GameRankingStore(self.chat_store.root)
            for winner_name, user_id, computer in self.host.winner_ranking_identities():
                award_multiplayer_victory(
                    winner_name,
                    "tank",
                    match_id=f"{self._hosted_room_id}:{user_id or winner_name}",
                    user_id=user_id,
                    computer_name=computer,
                    store=ranking_store,
                )

    def _network_error(self, message):
        QMessageBox.warning(self, "坦克房間連線失敗", str(message))
        if self.mode == "client":
            self.leave_match()

    def leave_match(self):
        self.shutdown_match()
        self.pages.setCurrentWidget(self.lobby_page)
        self.lobby_status.setText("已離開上一場坦克比賽。")

    def shutdown_match(self):
        self.local_timer.stop()
        self.room_directory.clear_hosted_room()
        self.host.close()
        self.client.disconnect()
        self._hosted_room_id = ""
        self.mode = ""

    def abort_current_workflow(self):
        self.shutdown_match()
        self.pages.setCurrentWidget(self.lobby_page)

    def shutdown(self):
        self.shutdown_match()
        self.room_directory.stop()
