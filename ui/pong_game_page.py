"""Hidden two-player LAN Pong lobby and game page."""

from __future__ import annotations

import socket
from uuid import uuid4

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
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
from app.features.pong.network import (
    DEFAULT_PONG_WINNING_SCORE,
    PongClient,
    PongEngine,
    PongHost,
    PongRoom,
    PongRoomDirectory,
)
from app.features.chat_room.game_ranking import (
    GameRankingStore,
    award_multiplayer_victory,
)
from ui.arcade_navigation import ArcadeNavigationBar
from ui.arcade_skin_picker import ArcadeSkinPicker, scroll_lobby
from app.features.arcade_cosmetics import arcade_skin
from ui.arcade_skin_art import ArcadeEffects, paint_paddle
from ui.arcade_particles import paint_glow, paint_light_trail


class PongBoard(QWidget):
    direction_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(560, 360)
        self.setFocusPolicy(Qt.StrongFocus)
        self._pressed = set()
        self.state = PongEngine().to_state()
        self.effects = ArcadeEffects(self)
        self.ball_trail = []

    def set_state(self, state):
        state = dict(state or {})
        old = self.state
        if (state.get("match_id") != old.get("match_id") or state.get("status") != "playing"
                or (state.get("left_score"), state.get("right_score")) != (old.get("left_score"), old.get("right_score"))
                or abs(state.get("ball_x",0)-old.get("ball_x",0)) > 160):
            self.ball_trail.clear()
        elif state.get("status") == "playing":
            self.ball_trail.append((state.get("ball_x",0), state.get("ball_y",0)))
            self.ball_trail = self.ball_trail[-14:]
        self.effects.consume(state)
        self.state = state
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_W, Qt.Key_Up):
            self._pressed.add("up")
        elif event.key() in (Qt.Key_S, Qt.Key_Down):
            self._pressed.add("down")
        else:
            super().keyPressEvent(event)
            return
        self._emit_direction()
        event.accept()

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            event.accept()
            return
        if event.key() in (Qt.Key_W, Qt.Key_Up):
            self._pressed.discard("up")
        elif event.key() in (Qt.Key_S, Qt.Key_Down):
            self._pressed.discard("down")
        else:
            super().keyReleaseEvent(event)
            return
        self._emit_direction()
        event.accept()

    def clear_input(self):
        self._pressed.clear()
        self.direction_changed.emit(0)

    def _emit_direction(self):
        direction = -1 if "up" in self._pressed else (1 if "down" in self._pressed else 0)
        self.direction_changed.emit(direction)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#07111f"))
        width = float(self.state.get("width", 1000.0))
        height = float(self.state.get("height", 600.0))
        scale = min(self.width() / width, self.height() / height)
        offset_x = (self.width() - width * scale) / 2
        offset_y = (self.height() - height * scale) / 2

        def mapped_rect(x, y, w, h):
            return QRectF(
                offset_x + float(x) * scale,
                offset_y + float(y) * scale,
                float(w) * scale,
                float(h) * scale,
            )

        painter.setPen(QPen(QColor("#24445f"), max(1.0, 2.0 * scale)))
        painter.drawRect(mapped_rect(0, 0, width, height))
        painter.setPen(QPen(QColor("#24445f"), max(1.0, 3.0 * scale), Qt.DashLine))
        painter.drawLine(
            offset_x + width * scale / 2,
            offset_y,
            offset_x + width * scale / 2,
            offset_y + height * scale,
        )
        powerups = self.state.get("powerups")
        if not isinstance(powerups, list):
            powerup_type = str(self.state.get("powerup_type", ""))
            powerups = ([{
                "type": powerup_type,
                "x": self.state.get("powerup_x", width / 2 - 22),
                "y": self.state.get("powerup_y", height / 2 - 22),
            }] if powerup_type else [])
        for powerup in powerups:
            powerup_type = str(powerup.get("type", ""))
            powerup_colors = {
                "speed": QColor("#facc15"),
                "swap": QColor("#a78bfa"),
                "giant": QColor("#fb923c"),
                "ghost": QColor("#cbd5e1"),
                "spin": QColor("#34d399"),
            }
            powerup_labels = {
                "speed": "快", "swap": "換", "giant": "大", "ghost": "虛",
                "spin": "旋",
            }
            powerup_rect = mapped_rect(
                powerup.get("x", width / 2 - 22),
                powerup.get("y", height / 2 - 22),
                self.state.get("powerup_size", 44),
                self.state.get("powerup_size", 44),
            )
            paint_glow(painter, powerup_rect.center(), powerup_rect.width(),
                       powerup_colors.get(powerup_type, QColor("#ffffff")), .55)
            painter.setBrush(powerup_colors.get(powerup_type, QColor("#ffffff")))
            painter.setPen(QPen(QColor("#ffffff"), max(1.0, 2.0 * scale)))
            painter.drawEllipse(powerup_rect)
            item_font = QFont(self.font())
            item_font.setBold(True)
            item_font.setPointSize(max(9, int(12 * scale)))
            painter.setFont(item_font)
            painter.setPen(QColor("#111827"))
            painter.drawText(
                powerup_rect,
                Qt.AlignCenter,
                powerup_labels.get(powerup_type, "?"),
            )
        paddle_width = self.state.get("paddle_width", 18.0)
        paddle_height = self.state.get("paddle_height", 112.0)
        for side, default, default_x in (("left","#38bdf8",34),("right","#fb7185",948)):
            paint_paddle(painter, mapped_rect(self.state.get(side+"_x",default_x),
                         self.state.get(side+"_y",244),paddle_width,paddle_height),
                         arcade_skin("pong",self.state.get(side+"_skin")),default)
        ball_size = self.state.get("ball_size", 18.0)
        ghost_ball = float(self.state.get("ghost_remaining", 0.0)) > 0
        ball_skin = arcade_skin("pong", self.state.get("ball_skin"))
        ball_color = QColor("#f8fafc" if ball_skin.skin_id == "classic" else ball_skin.accent)
        painter.setPen(Qt.NoPen)
        trail_points = [mapped_rect(x,y,ball_size,ball_size).center() for x,y in self.ball_trail]
        ball_center = mapped_rect(self.state.get("ball_x",491),self.state.get("ball_y",291),ball_size,ball_size).center()
        # Preserve the existing hollow ghost-ball cue with a much softer halo.
        glow_opacity = .18 if ghost_ball else .75
        paint_light_trail(painter, trail_points, ball_color, max(1.5, ball_size*scale*.4), glow_opacity)
        paint_glow(painter, ball_center, ball_size*scale*2.2, ball_color, glow_opacity)
        for index,(x,y) in enumerate(self.ball_trail):
            trail_color = QColor(ball_color); trail_color.setAlpha(12+index*7)
            painter.setBrush(trail_color)
            painter.drawEllipse(mapped_rect(x,y,ball_size,ball_size))
        painter.setBrush(Qt.NoBrush if ghost_ball else ball_color)
        painter.setPen(
            QPen(QColor("#f8fafc"), max(2.0, 3.0 * scale), Qt.DashLine)
            if ghost_ball
            else Qt.NoPen
        )
        painter.drawEllipse(
            mapped_rect(
                self.state.get("ball_x", 491.0),
                self.state.get("ball_y", 291.0),
                ball_size,
                ball_size,
            )
        )

        score_font = QFont(self.font())
        score_font.setBold(True)
        score_font.setPointSize(max(18, int(30 * scale)))
        painter.setFont(score_font)
        painter.setPen(QColor("#e2e8f0"))
        painter.drawText(
            QRectF(offset_x, offset_y + 18, width * scale / 2 - 20, 70),
            Qt.AlignRight | Qt.AlignVCenter,
            str(self.state.get("left_score", 0)),
        )
        painter.drawText(
            QRectF(offset_x + width * scale / 2 + 20, offset_y + 18, width * scale / 2 - 20, 70),
            Qt.AlignLeft | Qt.AlignVCenter,
            str(self.state.get("right_score", 0)),
        )
        name_font = QFont(self.font())
        name_font.setBold(True)
        name_font.setPointSize(max(10, int(13 * scale)))
        painter.setFont(name_font)
        painter.setPen(QColor("#93c5fd"))
        painter.drawText(
            QRectF(offset_x + 20, offset_y + 18, width * scale / 2 - 40, 40),
            Qt.AlignLeft | Qt.AlignVCenter,
            str(self.state.get("left_name", "房主")),
        )
        painter.setPen(QColor("#fda4af"))
        painter.drawText(
            QRectF(offset_x + width * scale / 2, offset_y + 18, width * scale / 2 - 20, 40),
            Qt.AlignRight | Qt.AlignVCenter,
            str(self.state.get("right_name", "訪客")),
        )

        status = self.state.get("status", "waiting")
        overlay = ""
        if status == "waiting":
            overlay = "等待對手加入…"
        elif status == "finished":
            overlay = (
                str(self.state.get("left_name", "房主"))
                if self.state.get("winner") == "left"
                else str(self.state.get("right_name", "訪客"))
            ) + " 勝利！"
        if overlay:
            painter.fillRect(self.rect(), QColor(2, 10, 23, 155))
            overlay_font = QFont(self.font())
            overlay_font.setBold(True)
            overlay_font.setPointSize(28)
            painter.setFont(overlay_font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(self.rect(), Qt.AlignCenter, overlay)
        elif float(self.state.get("swap_countdown", 0.0)) > 0:
            painter.fillRect(self.rect(), QColor(45, 20, 84, 145))
            swap_font = QFont(self.font())
            swap_font.setBold(True)
            swap_font.setPointSize(30)
            painter.setFont(swap_font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(self.rect(), Qt.AlignCenter, "交換位置！")
        self.effects.paint(painter, lambda x,y: QPointF(offset_x+x*scale,offset_y+y*scale),38*scale)
        painter.end()


class PongGamePage(QWidget):
    """Unregistered Easter-egg page with lobby, LAN match, and CPU match."""

    def __init__(
        self,
        go_home_callback,
        open_chat_callback=None,
        open_snake_callback=None,
        open_tetris_callback=None,
        open_tank_callback=None,
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
        self.open_snake_callback = open_snake_callback
        self.open_tetris_callback = open_tetris_callback
        self.open_tank_callback = open_tank_callback
        self.open_bulls_cows_callback = open_bulls_cows_callback
        self.return_work_callback = return_work_callback
        self.host = host or PongHost(self)
        self.client = client or PongClient(self)
        self.room_directory = room_directory or PongRoomDirectory(parent=self)
        self.chat_store = chat_store or ChatRoomStore()
        self.profile_store = profile_store or ChatProfileStore()
        self.local_engine = PongEngine()
        self.local_timer = QTimer(self)
        self.local_timer.setInterval(16)
        self.local_timer.timeout.connect(self._advance_cpu_match)
        self.mode = ""
        self._hosted_room_id = ""
        self._announcement_sent = False
        self._known_rooms = {}
        self._ai_target_y = self.local_engine.HEIGHT / 2
        self._ai_reaction_remaining = 0.0
        self._ai_difficulty = "normal"
        self._build_ui()
        self._connect_network()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 14)
        root.setSpacing(8)
        self.arcade_navigation = ArcadeNavigationBar(
            current_id="pong",
            title="PONG",
            go_home_callback=self.go_home_callback,
            return_work_callback=self.return_work_callback,
            callbacks={
                "chat": self.open_chat_callback,
                "tetris": self.open_tetris_callback,
                "tank": self.open_tank_callback,
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
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(14)
        heading = QLabel("雙人彈球遊戲大廳")
        heading.setObjectName("PanelTitle")
        heading.setAlignment(Qt.AlignCenter)
        grid.addWidget(heading, 0, 0, 1, 3)
        grid.addWidget(QLabel("玩家名稱："), 1, 0)
        self.player_name = QLineEdit()
        self.player_name.setObjectName("InputLine")
        self.player_name.setMaxLength(24)
        self.player_name.setReadOnly(True)
        self.player_name.setPlaceholderText("請先在聊天室設定暱稱")
        self.player_name.setToolTip("彈球玩家名稱固定使用目前的聊天室暱稱")
        grid.addWidget(self.player_name, 1, 1, 1, 2)
        self.skin_picker = ArcadeSkinPicker("pong", self.profile_store, self.chat_store.root, self)
        grid.addWidget(self.skin_picker, 2, 0, 1, 3)
        grid.addWidget(QLabel("獲勝分數："), 3, 0)
        self.winning_score = QSpinBox()
        self.winning_score.setObjectName("InputLine")
        self.winning_score.setRange(1, 30)
        self.winning_score.setValue(DEFAULT_PONG_WINNING_SCORE)
        self.winning_score.setSuffix(" 分")
        self.winning_score.setToolTip("房主可設定本場先取得幾分者獲勝")
        grid.addWidget(self.winning_score, 3, 1, 1, 2)
        grid.addWidget(QLabel("AI 難度："), 4, 0)
        self.ai_difficulty = QComboBox()
        self.ai_difficulty.setObjectName("InputLine")
        self.ai_difficulty.addItem("簡單", "easy")
        self.ai_difficulty.addItem("普通", "normal")
        self.ai_difficulty.addItem("困難", "hard")
        self.ai_difficulty.setCurrentIndex(1)
        self.ai_difficulty.setToolTip("僅影響與電腦遊玩")
        grid.addWidget(self.ai_difficulty, 4, 1, 1, 2)
        self.create_button = QPushButton("建立房間")
        self.create_button.setObjectName("PrimaryButton")
        self.create_button.clicked.connect(self.create_room)
        grid.addWidget(self.create_button, 5, 0, 1, 3)
        room_heading = QLabel("房間清單｜點選加入")
        room_heading.setObjectName("PanelTitle")
        grid.addWidget(room_heading, 6, 0, 1, 3)
        self.room_list = QListWidget()
        self.room_list.setObjectName("PongRoomList")
        self.room_list.setMinimumHeight(190)
        self.room_list.itemClicked.connect(self._join_room_item)
        grid.addWidget(self.room_list, 7, 0, 1, 3)
        self.cpu_button = QPushButton("▶  與電腦遊玩")
        self.cpu_button.setObjectName("SecondaryButton")
        self.cpu_button.clicked.connect(self.start_cpu_match)
        grid.addWidget(self.cpu_button, 8, 0, 1, 3)
        instructions = QLabel(
            "⌨  W/S 或 ↑/↓　　● 先達設定分數者勝"
        )
        instructions.setAlignment(Qt.AlignCenter)
        instructions.setWordWrap(True)
        grid.addWidget(instructions, 9, 0, 1, 3)
        self.lobby_status = QLabel("正在讀取目前房間清單…")
        self.lobby_status.setObjectName("ArcadeStatus")
        self.lobby_status.setAlignment(Qt.AlignCenter)
        self.lobby_status.setWordWrap(True)
        grid.addWidget(self.lobby_status, 10, 0, 1, 3)
        panel.setMaximumWidth(820)
        layout.addWidget(panel, alignment=Qt.AlignCenter)
        layout.addStretch()
        return scroll_lobby(page)

    def _build_game(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        status_row = QHBoxLayout()
        self.mode_label = QLabel("尚未開始")
        self.mode_label.setObjectName("PanelTitle")
        status_row.addWidget(self.mode_label)
        self.game_status = QLabel("")
        self.game_status.setObjectName("ArcadeStatus")
        self.game_status.setAlignment(Qt.AlignCenter)
        self.game_status.setWordWrap(True)
        status_row.addWidget(self.game_status, 1)
        leave = QPushButton("離開比賽並返回大廳")
        leave.setObjectName("SecondaryButton")
        leave.clicked.connect(self.leave_match)
        status_row.addWidget(leave)
        layout.addLayout(status_row)
        self.board = PongBoard()
        self.board.direction_changed.connect(self._set_direction)
        layout.addWidget(self.board, 1)
        self.game_hint = QLabel(
            "⌨ W/S 或 ↑/↓　｜　速・換・大・虛・旋　｜　最多同時 2 個道具"
        )
        self.game_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.game_hint)
        return page

    def _connect_network(self):
        self.host.state_changed.connect(self.board.set_state)
        self.host.room_created.connect(
            lambda _code: self.game_status.setText("房間已建立｜等待對手從清單加入")
        )
        self.host.status_changed.connect(self.game_status.setText)
        self.host.guest_joined.connect(self._host_guest_joined)
        self.host.guest_left.connect(self._host_guest_left)
        self.host.match_finished.connect(self._match_finished)
        self.client.state_changed.connect(self.board.set_state)
        self.client.status_changed.connect(self.game_status.setText)
        self.client.error_occurred.connect(self._network_error)
        self.client.match_finished.connect(self._match_finished)
        self.room_directory.rooms_received.connect(self._update_room_list)
        self.room_directory.error_occurred.connect(self._room_directory_error)

    def set_return_work_available(self, available):
        self.return_work_button.setEnabled(bool(available))

    def prepare_lobby(self):
        self.room_directory.start()
        self._refresh_chat_nickname()
        # Switching briefly to chat must not discard an active LAN room or
        # CPU match.  Re-enter the running board when a match still exists.
        if self.mode in {"host", "client", "cpu"}:
            self.pages.setCurrentWidget(self.game_page)
            self.board.setFocus(Qt.OtherFocusReason)
            return
        self.pages.setCurrentWidget(self.lobby_page)
        self.room_list.setFocus(Qt.OtherFocusReason)

    def _refresh_chat_nickname(self):
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
            self.lobby_status.setText("請先回聊天室設定暱稱，再進入雙人彈球。")
        return nickname

    def _player_name_or_warn(self):
        name = self.player_name.text().strip()
        if name:
            return name
        QMessageBox.information(
            self,
            "尚未設定聊天室暱稱",
            "雙人彈球固定使用聊天室暱稱，請先回聊天室完成設定。",
        )
        return ""

    def _update_room_list(self, rooms):
        selected_room_id = ""
        current = self.room_list.currentItem()
        if current is not None:
            selected_room_id = str(current.data(Qt.UserRole) or "")
        self._known_rooms = {room.room_id: room for room in rooms}
        self.room_list.clear()
        if not rooms:
            empty = QListWidgetItem("目前沒有房間；你可以建立一個新房間。")
            empty.setFlags(empty.flags() & ~Qt.ItemIsEnabled)
            self.room_list.addItem(empty)
            return
        status_titles = {
            "waiting": "等待玩家",
            "playing": "對戰中",
            "finished": "比賽結束",
        }
        for room in rooms:
            full = room.player_count >= 2
            suffix = "已滿，禁止加入" if full else "點一下加入"
            status = status_titles.get(room.status, room.status)
            item = QListWidgetItem(
                f"{room.host_name} 的房間｜先得 {room.winning_score} 分｜"
                f"{room.player_count}/2｜{status}｜{suffix}"
            )
            item.setData(Qt.UserRole, room.room_id)
            if full or room.status != "waiting":
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.room_list.addItem(item)
            if room.room_id == selected_room_id:
                self.room_list.setCurrentItem(item)

    def _join_room_item(self, item):
        room_id = str(item.data(Qt.UserRole) or "")
        room = self._known_rooms.get(room_id)
        if room is None:
            return
        if room.player_count >= 2 or room.status != "waiting":
            self.lobby_status.setText("這個房間目前已滿，無法加入。")
            return
        self.join_room(room)

    def create_room(self):
        name = self._player_name_or_warn()
        if not name:
            return
        self.room_directory.start()
        try:
            target_score = self.winning_score.value()
            code = self.host.create_room(
                name,
                winning_score=target_score,
                user_id=self.profile_store.load_profile().user_id,
                skin_id=self.skin_picker.skin_id(),
            )
        except RuntimeError as error:
            QMessageBox.warning(self, "房間建立失敗", str(error))
            return
        self.mode = "host"
        self._hosted_room_id = uuid4().hex
        self._announcement_sent = False
        self._publish_host_room(1, "waiting")
        publish_game_invitation(
            "pong",
            "雙人彈球",
            {
                "room_id": self._hosted_room_id,
                "room_code": code,
                "host_name": name,
                "computer_name": socket.gethostname(),
                "player_count": 1,
                "status": "waiting",
                "winning_score": target_score,
            },
            name,
            store=self.chat_store,
        )
        self.mode_label.setText("區網對戰｜你是左側房主")
        self.game_status.setText(
            f"房間已建立｜先取得 {target_score} 分者獲勝｜等待對手加入"
        )
        self._update_game_hint(target_score)
        self.pages.setCurrentWidget(self.game_page)
        self.board.setFocus(Qt.OtherFocusReason)

    def join_room(self, room):
        name = self._player_name_or_warn()
        if not name:
            return
        if room.player_count >= 2:
            self.lobby_status.setText("這個房間目前已滿，無法加入。")
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
        self._update_game_hint(room.winning_score)
        self.mode_label.setText("區網對戰｜你是右側加入者")
        self.pages.setCurrentWidget(self.game_page)
        self.board.setFocus(Qt.OtherFocusReason)

    def start_cpu_match(self):
        name = self._player_name_or_warn()
        if not name:
            return
        self.shutdown_match()
        target_score = self.winning_score.value()
        self.local_engine.winning_score = target_score
        self.local_engine.reset_match(name, "電腦", self.skin_picker.skin_id())
        self._ai_difficulty = str(self.ai_difficulty.currentData() or "normal")
        self.mode = "cpu"
        self._announcement_sent = False
        self.mode_label.setText("單機對戰｜你是左側玩家")
        self.game_status.setText(
            f"對戰開始｜{self.ai_difficulty.currentText()}｜"
            f"先取得 {target_score} 分者獲勝"
        )
        self._update_game_hint(target_score)
        self._ai_target_y = self.local_engine.HEIGHT / 2
        self._ai_reaction_remaining = 0.0
        self.board.set_state(self.local_engine.to_state())
        self.pages.setCurrentWidget(self.game_page)
        self.local_timer.start()
        self.board.setFocus(Qt.OtherFocusReason)

    def _advance_cpu_match(self):
        human_side = "right" if self.local_engine.controls_swapped else "left"
        ai_side = "left" if human_side == "right" else "right"
        dt = self.local_timer.interval() / 1000.0
        self._ai_reaction_remaining -= dt
        if self._ai_reaction_remaining <= 0:
            ball_center = (
                self.local_engine.ball_y
                + self.local_engine.current_ball_size / 2
            )
            moving_toward_ai = (
                self.local_engine.ball_vx < 0
                if ai_side == "left"
                else self.local_engine.ball_vx > 0
            )
            profiles = {
                "easy": (0.38, 88.0),
                "normal": (0.24, 48.0),
                "hard": (0.11, 18.0),
            }
            reaction, error_range = profiles.get(
                self._ai_difficulty,
                profiles["normal"],
            )
            if moving_toward_ai:
                target_y = ball_center
                if self._ai_difficulty == "hard" and abs(self.local_engine.ball_vx) > 1:
                    paddle_x = (
                        self.local_engine.LEFT_X
                        if ai_side == "left"
                        else self.local_engine.RIGHT_X
                    )
                    seconds = abs(
                        (paddle_x - self.local_engine.ball_x)
                        / self.local_engine.ball_vx
                    )
                    target_y += self.local_engine.ball_vy * seconds
                    span = self.local_engine.HEIGHT * 2
                    target_y %= span
                    if target_y > self.local_engine.HEIGHT:
                        target_y = span - target_y
                error = self.local_engine.random.uniform(-error_range, error_range)
                self._ai_target_y = target_y + error
            else:
                self._ai_target_y = self.local_engine.HEIGHT / 2
            self._ai_reaction_remaining = reaction
        paddle_y = (
            self.local_engine.left_y
            if ai_side == "left"
            else self.local_engine.right_y
        )
        paddle_center = paddle_y + self.local_engine.PADDLE_HEIGHT / 2
        difference = self._ai_target_y - paddle_center
        deadzone = {
            "easy": 50.0,
            "normal": 34.0,
            "hard": 18.0,
        }.get(self._ai_difficulty, 34.0)
        ai_direction = 0 if abs(difference) < deadzone else (1 if difference > 0 else -1)
        self.local_engine.set_input(ai_side, ai_direction)
        was_playing = self.local_engine.status == "playing"
        self.local_engine.step(dt)
        self.board.set_state(self.local_engine.to_state())
        if was_playing and self.local_engine.status == "finished":
            self.local_timer.stop()
            winner = (
                self.local_engine.left_name
                if self.local_engine.winner == "left"
                else self.local_engine.right_name
            )
            self._match_finished(winner)

    def _set_direction(self, direction):
        if self.mode == "host":
            self.host.set_host_direction(direction)
        elif self.mode == "client":
            self.client.set_direction(direction)
        elif self.mode == "cpu":
            human_side = "right" if self.local_engine.controls_swapped else "left"
            self.local_engine.set_input(human_side, direction)

    def _match_finished(self, winner):
        self.game_status.setText(f"比賽結束：{winner} 勝利")
        if self.mode == "host":
            self._publish_host_room(2, "finished")
            engine = self.host.engine
        elif self.mode == "cpu":
            engine = self.local_engine
        else:
            return
        if self._announcement_sent:
            return
        self._announcement_sent = True
        winner_name = (
            engine.left_name if engine.winner == "left" else engine.right_name
        )
        loser_name = (
            engine.right_name if engine.winner == "left" else engine.left_name
        )
        if self.mode == "cpu" and winner_name != "電腦":
            report = format_ai_victory_announcement(
                winner_name,
                "雙人彈球",
                self._ai_difficulty,
            )
        else:
            report = f'"{winner_name}"剛剛在雙人彈球比賽中屌虐了"{loser_name}"'
        publish_game_announcement(
            report,
            store=self.chat_store,
        )
        if self.mode == "host":
            award_multiplayer_victory(
                winner_name,
                "pong",
                match_id=self._hosted_room_id,
                user_id=self.host.winner_user_id(),
                computer_name=self.host.winner_computer_name(),
                store=GameRankingStore(self.chat_store.root),
            )

    def _host_guest_joined(self, _guest_name):
        self._announcement_sent = False
        self._publish_host_room(2, "playing")

    def _host_guest_left(self):
        if self.mode == "host":
            self._publish_host_room(1, "waiting")

    def _publish_host_room(self, player_count, status):
        if not self._hosted_room_id or not self.host.room_code:
            return
        self.room_directory.set_hosted_room(
            PongRoom(
                room_id=self._hosted_room_id,
                room_code=self.host.room_code,
                host_name=self.player_name.text().strip(),
                computer_name=socket.gethostname(),
                player_count=player_count,
                status=status,
                winning_score=self.host.engine.winning_score,
            )
        )

    def _update_game_hint(self, winning_score):
        self.game_hint.setText(
            f"⌨ W/S 或 ↑/↓　｜　先得 {int(winning_score)} 分　｜　"
            "速・換・大・虛・旋　｜　最多同時 2 個道具"
        )

    def _network_error(self, message):
        self.game_status.setText(f"連線失敗：{message}")
        if self.mode == "client":
            self.client.disconnect()
            self.mode = ""
            self.pages.setCurrentWidget(self.lobby_page)
            self.lobby_status.setText(f"無法加入房間：{message}")

    def _room_directory_error(self, message):
        if self.pages.currentWidget() is self.lobby_page:
            self.lobby_status.setText(message)

    def leave_match(self):
        self.shutdown_match()
        self.pages.setCurrentWidget(self.lobby_page)
        self.lobby_status.setText("已離開上一場比賽。")

    def shutdown_match(self):
        self.local_timer.stop()
        self.board.clear_input()
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
