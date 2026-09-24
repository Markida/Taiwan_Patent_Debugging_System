"""Hidden keyboard-controlled snake game."""

from __future__ import annotations

import random
import socket
from uuid import uuid4

from PySide6.QtCore import QPointF, QRectF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.features.snake.score_store import (
    SnakeScoreError,
    SnakeScoreStore,
)
from app.features.chat_room.store import (
    ChatProfileStore,
    ChatRoomStore,
    publish_game_announcement,
    publish_game_invitation,
)
from app.features.chat_room.arcade_lan import ArcadeRoom, ArcadeRoomDirectory
from app.features.snake.network import SnakeClient, SnakeHost
from app.features.chat_room.game_ranking import (
    GameRankingStore,
    local_computer_name,
    award_multiplayer_victory,
)
from ui.arcade_navigation import ArcadeNavigationBar
from ui.arcade_skin_picker import ArcadeSkinPicker, scroll_lobby
from app.features.arcade_cosmetics import arcade_skin
from ui.arcade_skin_art import ArcadeEffects, paint_snake_cell
from ui.arcade_particles import paint_glow, paint_light_trail


class SnakeBoard(QWidget):
    score_changed = Signal(int)
    game_finished = Signal(int)
    bonus_changed = Signal(str)

    COLUMNS = 30
    ROWS = 22
    NORMAL_SCORE = 10
    BONUS_SCORE = 30
    BONUS_LIFETIME_TICKS = 50

    def __init__(self, parent=None, *, random_source=None):
        super().__init__(parent)
        self.random = random_source or random.Random()
        self.timer = QTimer(self)
        self.timer.setInterval(105)
        self.timer.timeout.connect(self.advance)
        self.setMinimumSize(560, 400)
        self.setFocusPolicy(Qt.StrongFocus)
        self.skin_id = "classic"
        self.effects = ArcadeEffects(self)
        self.snake = []
        self.direction = (1, 0)
        self.pending_direction = (1, 0)
        self.food = None
        self.bonus_food = None
        self.bonus_ticks = 0
        self.normal_food_count = 0
        self.score = 0
        self.running = False
        self.reset_game()

    def reset_game(self):
        self.effects.clear()
        self.timer.stop()
        center_x = self.COLUMNS // 2
        center_y = self.ROWS // 2
        self.snake = [
            (center_x, center_y),
            (center_x - 1, center_y),
            (center_x - 2, center_y),
        ]
        self.direction = (1, 0)
        self.pending_direction = (1, 0)
        self.food = None
        self.bonus_food = None
        self.food = self._empty_cell()
        self.bonus_ticks = 0
        self.normal_food_count = 0
        self.score = 0
        self.running = False
        self.score_changed.emit(self.score)
        self.bonus_changed.emit("")
        self.update()

    def start_game(self):
        if self.running:
            return
        if not self.snake:
            self.reset_game()
        self.running = True
        self.timer.start()
        self.setFocus(Qt.OtherFocusReason)

    def restart_game(self):
        self.reset_game()
        self.start_game()

    def keyPressEvent(self, event):
        directions = {
            Qt.Key_Left: (-1, 0),
            Qt.Key_A: (-1, 0),
            Qt.Key_Right: (1, 0),
            Qt.Key_D: (1, 0),
            Qt.Key_Up: (0, -1),
            Qt.Key_W: (0, -1),
            Qt.Key_Down: (0, 1),
            Qt.Key_S: (0, 1),
        }
        wanted = directions.get(event.key())
        if wanted is not None:
            if wanted != (-self.direction[0], -self.direction[1]):
                self.pending_direction = wanted
            if not self.running:
                self.start_game()
            event.accept()
            return
        if event.key() == Qt.Key_Space:
            if self.running:
                self.timer.stop()
                self.running = False
            else:
                self.start_game()
            event.accept()
            return
        super().keyPressEvent(event)

    def advance(self):
        if not self.running:
            return
        self.direction = self.pending_direction
        head_x, head_y = self.snake[0]
        next_head = (
            head_x + self.direction[0],
            head_y + self.direction[1],
        )
        eats_normal = next_head == self.food
        eats_bonus = next_head == self.bonus_food
        body_to_check = self.snake if (eats_normal or eats_bonus) else self.snake[:-1]
        if (
            next_head[0] < 0
            or next_head[0] >= self.COLUMNS
            or next_head[1] < 0
            or next_head[1] >= self.ROWS
            or next_head in body_to_check
        ):
            self._finish()
            return

        self.snake.insert(0, next_head)
        if eats_normal:
            self.effects.push("eat",*next_head,text="+10",color=arcade_skin("snake",self.skin_id).accent)
            self.score += self.NORMAL_SCORE
            self.normal_food_count += 1
            self.food = self._empty_cell()
            if self.normal_food_count % 3 == 0:
                self.bonus_food = self._empty_cell()
                self.bonus_ticks = self.BONUS_LIFETIME_TICKS
                if self.bonus_food:
                    self.effects.push("bonus",*self.bonus_food,text="限時獎勵",color="#facc15")
        elif eats_bonus:
            self.effects.push("bonus",*next_head,text="+30",color="#facc15")
            self.score += self.BONUS_SCORE
            self.bonus_food = None
            self.bonus_ticks = 0
        else:
            self.snake.pop()

        if self.bonus_food is not None and not eats_bonus:
            self.bonus_ticks -= 1
            if self.bonus_ticks <= 0:
                self.bonus_food = None
                self.bonus_ticks = 0
        self.score_changed.emit(self.score)
        self._emit_bonus_status()
        self.update()

    def _finish(self):
        self.effects.push("explosion",*self.snake[0],color="#fb7185",text="碰撞！")
        self.timer.stop()
        self.running = False
        self.update()
        self.game_finished.emit(self.score)

    def _empty_cell(self):
        occupied = set(self.snake)
        if self.food is not None:
            occupied.add(self.food)
        if self.bonus_food is not None:
            occupied.add(self.bonus_food)
        candidates = [
            (x, y)
            for y in range(self.ROWS)
            for x in range(self.COLUMNS)
            if (x, y) not in occupied
        ]
        return self.random.choice(candidates) if candidates else None

    def _emit_bonus_status(self):
        if self.bonus_food is None:
            self.bonus_changed.emit("")
            return
        seconds = max(1, (self.bonus_ticks * self.timer.interval() + 999) // 1000)
        self.bonus_changed.emit(f"限時大食物：{seconds} 秒")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#07111f"))
        cell = max(
            1,
            min(self.width() // self.COLUMNS, self.height() // self.ROWS),
        )
        board_width = cell * self.COLUMNS
        board_height = cell * self.ROWS
        offset_x = (self.width() - board_width) // 2
        offset_y = (self.height() - board_height) // 2

        painter.setPen(QPen(QColor("#10243a"), 1))
        for x in range(self.COLUMNS + 1):
            px = offset_x + x * cell
            painter.drawLine(px, offset_y, px, offset_y + board_height)
        for y in range(self.ROWS + 1):
            py = offset_y + y * cell
            painter.drawLine(offset_x, py, offset_x + board_width, py)

        def cell_rect(position):
            x, y = position
            return QRect(
                offset_x + x * cell + 1,
                offset_y + y * cell + 1,
                max(1, cell - 2),
                max(1, cell - 2),
            )

        if self.food is not None:
            paint_glow(painter,QRectF(cell_rect(self.food)).center(),cell*1.15,"#fb7185",.55)
            painter.fillRect(cell_rect(self.food), QColor("#ef4444"))
        if self.bonus_food is not None:
            paint_glow(painter,QRectF(cell_rect(self.bonus_food)).center(),cell*1.8,"#facc15",.85)
            painter.fillRect(cell_rect(self.bonus_food), QColor("#facc15"))
            painter.setPen(QPen(QColor("#fff7ae"), 2))
            painter.drawRect(cell_rect(self.bonus_food))
        skin = arcade_skin("snake",self.skin_id)
        path = [QRectF(cell_rect(position)).center() for position in self.snake[:8]]
        paint_light_trail(painter,path,skin.color,cell*.34,.45)
        if path:
            paint_glow(painter,path[0],cell*1.1,skin.accent,.45)
        for index, position in enumerate(self.snake):
            paint_snake_cell(painter,cell_rect(position),arcade_skin("snake",self.skin_id),
                             index==0,"#22d3a7" if index else "#67e8c4",self.direction)
        self.effects.paint(painter,lambda x,y:QPointF(offset_x+(x+.5)*cell,offset_y+(y+.5)*cell),cell)
        painter.end()


class SnakeLanBoard(QWidget):
    action_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(560, 400)
        self.setFocusPolicy(Qt.StrongFocus)
        self.local_side = "left"
        self.state = {"status": "waiting"}
        self.effects = ArcadeEffects(self)

    def set_state(self, state):
        self.effects.consume(state or {})
        self.state = dict(state or {})
        self.update()

    def keyPressEvent(self, event):
        direction = {
            Qt.Key_Left: "left", Qt.Key_A: "left",
            Qt.Key_Right: "right", Qt.Key_D: "right",
            Qt.Key_Up: "up", Qt.Key_W: "up",
            Qt.Key_Down: "down", Qt.Key_S: "down",
        }.get(event.key())
        if direction:
            self.action_requested.emit(direction)
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#07111f"))
        painter.setRenderHint(QPainter.Antialiasing, True)
        columns, rows = 30, 22
        cell = max(1, min(self.width() // columns, (self.height() - 50) // rows))
        board_width, board_height = cell * columns, cell * rows
        offset_x = (self.width() - board_width) // 2
        offset_y = 44 + (self.height() - 44 - board_height) // 2
        painter.setPen(QPen(QColor("#10243a"), 1))
        for x in range(columns + 1):
            painter.drawLine(offset_x + x * cell, offset_y, offset_x + x * cell, offset_y + board_height)
        for y in range(rows + 1):
            painter.drawLine(offset_x, offset_y + y * cell, offset_x + board_width, offset_y + y * cell)

        def draw_cell(position, color):
            x, y = position
            painter.fillRect(
                QRect(offset_x + x * cell + 1, offset_y + y * cell + 1, max(1, cell - 2), max(1, cell - 2)),
                QColor(color),
            )

        food = self.state.get("food")
        if food:
            paint_glow(painter,QPointF(offset_x+(food[0]+.5)*cell,offset_y+(food[1]+.5)*cell),cell*1.3,"#facc15",.65)
            draw_cell(food, "#facc15")
        for side,body_color,head_color,label in (("left","#22d3a7","#67e8c4","P1"),("right","#fb7185","#fda4af","P2")):
            skin=arcade_skin("snake",self.state.get(side+"_skin"))
            snake=self.state.get(side+"_snake") or []
            direction=(snake[0][0]-snake[1][0],snake[0][1]-snake[1][1]) if len(snake)>1 else (1,0)
            path=[QPointF(offset_x+(x+.5)*cell,offset_y+(y+.5)*cell) for x,y in snake[:8]]
            paint_light_trail(painter,path,skin.color if skin.skin_id!="classic" else body_color,cell*.34,.45)
            if path:
                paint_glow(painter,path[0],cell*1.1,skin.accent,.45)
            for index,(x,y) in enumerate(snake):
                r=QRectF(offset_x+x*cell+1,offset_y+y*cell+1,cell-2,cell-2)
                paint_snake_cell(painter,r,skin,index==0,head_color if index==0 else body_color,direction)
                if index==0:
                    painter.setPen(QPen(QColor(head_color),2))
                    painter.setBrush(Qt.NoBrush);painter.drawRect(r.adjusted(-1,-1,1,1))
                    tag=QRectF(r.left()-3,r.top()-15,30,14)
                    painter.fillRect(tag,QColor("#0b1628"));painter.setPen(QColor(head_color))
                    font=QFont(self.font());font.setPixelSize(10);font.setBold(True);painter.setFont(font)
                    painter.drawText(tag,Qt.AlignCenter,label)
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSize(14)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            QRect(0, 4, self.width(), 34),
            Qt.AlignCenter,
            f"P1 {self.state.get('left_name', '房主')} {self.state.get('left_score', 0)}  ：  "
            f"{self.state.get('right_score', 0)} {self.state.get('right_name', '訪客')} P2",
        )
        if self.state.get("status") != "playing":
            painter.fillRect(self.rect(), QColor(2, 10, 23, 150))
            winner = self.state.get("winner")
            if self.state.get("status") == "finished":
                text = "平手" if winner == "draw" else (
                    self.state.get("left_name") if winner == "left" else self.state.get("right_name")
                ) + " 勝利！"
            else:
                text = "等待對手加入…"
            painter.drawText(self.rect(), Qt.AlignCenter, str(text))
        self.effects.paint(painter,lambda x,y:QPointF(offset_x+(x+.5)*cell,offset_y+(y+.5)*cell),cell)
        painter.end()


class SnakeGamePage(QWidget):
    """A deliberately unregistered page reachable only through the title P."""

    def __init__(
        self,
        go_home_callback,
        score_store=None,
        open_chat_callback=None,
        open_pong_callback=None,
        open_tetris_callback=None,
        open_tank_callback=None,
        open_bulls_cows_callback=None,
        return_work_callback=None,
        chat_store=None,
        profile_store=None,
        lan_host=None,
        lan_client=None,
        room_directory=None,
    ):
        super().__init__()
        self.go_home_callback = go_home_callback
        self.open_chat_callback = open_chat_callback
        self.open_pong_callback = open_pong_callback
        self.open_tetris_callback = open_tetris_callback
        self.open_tank_callback = open_tank_callback
        self.open_bulls_cows_callback = open_bulls_cows_callback
        self.return_work_callback = return_work_callback
        self.score_store = score_store or SnakeScoreStore()
        self.chat_store = chat_store or ChatRoomStore()
        self.profile_store = profile_store or ChatProfileStore()
        self.lan_host = lan_host or SnakeHost(self)
        self.lan_client = lan_client or SnakeClient(self)
        self.room_directory = room_directory or ArcadeRoomDirectory("snake", parent=self)
        self.mode = ""
        self._hosted_room_id = ""
        self._known_rooms = {}
        self._announcement_sent = False
        self._build_ui()
        self._connect_lan()
        self.refresh_leaderboard()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 14)
        layout.setSpacing(8)

        self.arcade_navigation = ArcadeNavigationBar(
            current_id="snake",
            title="SNAKE",
            go_home_callback=self.go_home_callback,
            return_work_callback=self.return_work_callback,
            callbacks={
                "chat": self.open_chat_callback,
                "pong": self.open_pong_callback,
                "tetris": self.open_tetris_callback,
                "tank": self.open_tank_callback,
                "bulls_and_cows": self.open_bulls_cows_callback,
            },
        )
        self.return_work_button = self.arcade_navigation.return_work_button
        layout.addWidget(self.arcade_navigation)

        content = QHBoxLayout()
        self.board = SnakeBoard()
        self.board.setObjectName("SnakeBoard")
        self.board.score_changed.connect(
            lambda score: self.score_label.setText(f"分數：{score}")
        )
        self.board.game_finished.connect(self._on_game_finished)
        self.board.bonus_changed.connect(self._set_bonus_status)
        content.addWidget(self.board, 1)

        side_panel = QFrame()
        side_panel.setObjectName("Panel")
        side_layout = QVBoxLayout(side_panel)
        self.score_label = QLabel("分數：0")
        self.score_label.setObjectName("SnakeScore")
        self.score_label.setAlignment(Qt.AlignCenter)
        side_layout.addWidget(self.score_label)
        leaderboard_title = QLabel("最高分前十名")
        leaderboard_title.setObjectName("PanelTitle")
        side_layout.addWidget(leaderboard_title)
        self.leaderboard = QTableWidget(0, 3)
        self.leaderboard.setObjectName("SnakeLeaderboard")
        self.leaderboard.setHorizontalHeaderLabels(["名次", "電腦代號", "分數"])
        self.leaderboard.verticalHeader().setVisible(False)
        self.leaderboard.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.leaderboard.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.leaderboard.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self.leaderboard.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        side_layout.addWidget(self.leaderboard)
        self.bonus_label = QLabel("")
        self.bonus_label.setObjectName("SnakeBonus")
        self.bonus_label.setAlignment(Qt.AlignCenter)
        side_layout.addWidget(self.bonus_label)
        instructions = QLabel(
            "⌨ 方向鍵／WASD　Space 暫停\n"
            "●×3 → ★ 限時大食物"
        )
        instructions.setWordWrap(True)
        instructions.setAlignment(Qt.AlignCenter)
        side_layout.addWidget(instructions)
        self.start_button = QPushButton("開始／重新開始")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self.board.restart_game)
        side_layout.addWidget(self.start_button)
        lan_button = QPushButton("🌐 區網雙人對戰")
        lan_button.setObjectName("PrimaryButton")
        lan_button.clicked.connect(self.show_lan_lobby)
        side_layout.addWidget(lan_button)
        content.addWidget(side_panel)
        content.setStretch(0, 4)
        content.setStretch(1, 1)
        local_page = QWidget()
        local_page.setLayout(content)

        self.pages = QStackedWidget()
        self.local_page = local_page
        self.lan_lobby_page = self._build_lan_lobby()
        self.lobby_page = self.lan_lobby_page
        self.lan_game_page = self._build_lan_game()
        self.pages.addWidget(self.lan_lobby_page)
        self.pages.addWidget(self.local_page)
        self.pages.addWidget(self.lan_game_page)
        layout.addWidget(self.pages, 1)

    def _build_lan_lobby(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.addStretch()
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setMinimumWidth(640)
        body = QGridLayout(panel)
        body.setContentsMargins(30, 24, 30, 24)
        body.setHorizontalSpacing(12)
        body.setVerticalSpacing(12)
        heading = QLabel("貪食蛇遊戲大廳")
        heading.setObjectName("PanelTitle")
        heading.setAlignment(Qt.AlignCenter)
        body.addWidget(heading, 0, 0, 1, 3)
        body.addWidget(QLabel("玩家名稱："), 1, 0)
        self.lan_player_name = QLineEdit()
        self.lan_player_name.setReadOnly(True)
        self.lan_player_name.setObjectName("InputLine")
        self.lan_player_name.setPlaceholderText("請先在聊天室設定暱稱")
        body.addWidget(self.lan_player_name, 1, 1, 1, 2)
        self.skin_picker = ArcadeSkinPicker("snake", self.profile_store, self.chat_store.root, self)
        body.addWidget(self.skin_picker, 2, 0, 1, 3)
        self.create_lan_button = QPushButton("建立雙人房間")
        self.create_lan_button.setObjectName("PrimaryButton")
        self.create_lan_button.clicked.connect(self.create_lan_room)
        body.addWidget(self.create_lan_button, 3, 0, 1, 3)
        self.local_play_button = QPushButton("▶  單人排行榜模式")
        self.local_play_button.setObjectName("SecondaryButton")
        self.local_play_button.clicked.connect(self.show_local_mode)
        body.addWidget(self.local_play_button, 4, 0, 1, 3)
        room_heading = QLabel("房間清單｜點選加入")
        room_heading.setObjectName("PanelTitle")
        body.addWidget(room_heading, 5, 0, 1, 3)
        self.lan_room_list = QListWidget()
        self.lan_room_list.setObjectName("PongRoomList")
        self.lan_room_list.setMinimumHeight(190)
        self.lan_room_list.itemClicked.connect(self._join_room_item)
        body.addWidget(self.lan_room_list, 6, 0, 1, 3)
        instructions = QLabel("⌨ 方向鍵／WASD 移動　Space 暫停　●×3 → ★ 限時大食物")
        instructions.setAlignment(Qt.AlignCenter)
        instructions.setWordWrap(True)
        body.addWidget(instructions, 7, 0, 1, 3)
        self.lan_status = QLabel("正在讀取房間清單…")
        self.lan_status.setObjectName("ArcadeStatus")
        self.lan_status.setAlignment(Qt.AlignCenter)
        self.lan_status.setWordWrap(True)
        body.addWidget(self.lan_status, 8, 0, 1, 3)
        panel.setMaximumWidth(820)
        outer.addWidget(panel, alignment=Qt.AlignCenter)
        outer.addStretch()
        return scroll_lobby(page)

    def _build_lan_game(self):
        page = QWidget()
        body = QVBoxLayout(page)
        row = QHBoxLayout()
        self.lan_game_status = QLabel("等待對手加入")
        self.lan_game_status.setObjectName("ArcadeStatus")
        self.lan_game_status.setWordWrap(True)
        row.addWidget(self.lan_game_status, 1)
        leave = QPushButton("離開並返回大廳")
        leave.setObjectName("SecondaryButton")
        leave.clicked.connect(self.show_lan_lobby)
        row.addWidget(leave)
        body.addLayout(row)
        self.lan_board = SnakeLanBoard()
        self.lan_board.action_requested.connect(self._send_lan_action)
        body.addWidget(self.lan_board, 1)
        hint = QLabel("雙人共用競技場｜方向鍵／WASD 操作｜撞牆、撞自己或撞對手即落敗")
        hint.setAlignment(Qt.AlignCenter)
        body.addWidget(hint)
        return page

    def _connect_lan(self):
        self.room_directory.rooms_received.connect(self._update_lan_rooms)
        self.room_directory.error_occurred.connect(self.lan_status.setText)
        self.lan_host.state_changed.connect(self.lan_board.set_state)
        self.lan_host.guest_joined.connect(self._lan_guest_joined)
        self.lan_host.guest_left.connect(self._lan_guest_left)
        self.lan_host.match_finished.connect(self._lan_match_finished)
        self.lan_client.state_changed.connect(self.lan_board.set_state)
        self.lan_client.status_changed.connect(self.lan_game_status.setText)
        self.lan_client.error_occurred.connect(
            lambda message: QMessageBox.warning(self, "貪食蛇連線失敗", str(message))
        )
        self.lan_client.match_finished.connect(self._lan_match_finished)

    def prepare_game(self):
        self.room_directory.start()
        try:
            self.lan_player_name.setText(self.profile_store.load_profile().nickname)
        except Exception as error:
            self.lan_status.setText(f"無法讀取聊天室暱稱：{error}")
        ready = bool(self.lan_player_name.text().strip())
        self.create_lan_button.setEnabled(ready)
        self.lan_room_list.setEnabled(ready)
        if not ready:
            self.lan_status.setText("請先回聊天室設定暱稱，再建立或加入區網房間。")
        if self.mode == "local":
            self.board.reset_game()
            self.refresh_leaderboard()
            self.pages.setCurrentWidget(self.local_page)
            self.board.setFocus(Qt.OtherFocusReason)
        elif self.mode in {"lan_host", "lan_client"}:
            self.pages.setCurrentWidget(self.lan_game_page)
            self.lan_board.setFocus(Qt.OtherFocusReason)
        else:
            self.pages.setCurrentWidget(self.lobby_page)
            self.lan_room_list.setFocus(Qt.OtherFocusReason)

    def show_local_mode(self):
        self.leave_lan_match()
        self.mode = "local"
        self.board.skin_id = self.skin_picker.skin_id()
        self.board.reset_game()
        self.refresh_leaderboard()
        self.pages.setCurrentWidget(self.local_page)
        self.board.setFocus(Qt.OtherFocusReason)

    def show_lan_lobby(self):
        self.board.timer.stop()
        self.board.running = False
        self.leave_lan_match()
        self.mode = ""
        self.room_directory.start()
        try:
            self.lan_player_name.setText(self.profile_store.load_profile().nickname)
        except Exception as error:
            self.lan_status.setText(str(error))
        ready = bool(self.lan_player_name.text().strip())
        self.create_lan_button.setEnabled(ready)
        self.lan_room_list.setEnabled(ready)
        self.pages.setCurrentWidget(self.lobby_page)

    def _player_name(self):
        name = self.lan_player_name.text().strip()
        if not name:
            QMessageBox.warning(self, "尚未設定暱稱", "請先回聊天室設定暱稱。")
        return name

    def _update_lan_rooms(self, rooms):
        self._known_rooms = {room.room_id: room for room in rooms}
        self.lan_room_list.clear()
        if not rooms:
            item = QListWidgetItem("目前沒有房間；你可以建立一個新房間。")
            item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.lan_room_list.addItem(item)
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

    def _current_room(self):
        return ArcadeRoom(
            game_id="snake",
            room_id=self._hosted_room_id,
            room_code=self.lan_host.room_code,
            host_name=self.lan_player_name.text().strip(),
            computer_name=socket.gethostname(),
            player_count=1,
            status="waiting",
        )

    def _publish_room(self, player_count, status):
        if not self._hosted_room_id or not self.lan_host.room_code:
            return
        room = self._current_room()
        self.room_directory.set_hosted_room(ArcadeRoom(
            **{**room.__dict__, "player_count": player_count, "status": status}
        ))

    def create_lan_room(self):
        name = self._player_name()
        if not name:
            return
        self.leave_lan_match()
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
        self._publish_room(1, "waiting")
        publish_game_invitation(
            "snake",
            "貪食蛇",
            self._current_room().to_payload(),
            name,
            store=self.chat_store,
        )
        self.lan_board.local_side = "left"
        self.lan_board.set_state(self.lan_host.state())
        self.lan_game_status.setText("房間已建立｜等待對手加入")
        self.pages.setCurrentWidget(self.lan_game_page)
        self.lan_board.setFocus(Qt.OtherFocusReason)

    def _join_room_item(self, item):
        self.join_lan_room(self._known_rooms.get(str(item.data(Qt.UserRole) or "")))

    def join_lan_room(self, room):
        if room is None or room.player_count >= 2 or room.status != "waiting":
            QMessageBox.information(self, "無法加入", "這個房間目前已滿或已開始。")
            return
        name = self._player_name()
        if not name:
            return
        self.leave_lan_match()
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
        self.lan_board.local_side = "right"
        self.pages.setCurrentWidget(self.lan_game_page)
        self.lan_board.setFocus(Qt.OtherFocusReason)

    def join_invitation(self, payload):
        room = ArcadeRoom.from_payload(payload)
        self.show_lan_lobby()
        self.join_lan_room(room)

    def _lan_guest_joined(self, name):
        self._publish_room(2, "playing")
        self.lan_game_status.setText(f"已連線：{name}")

    def _lan_guest_left(self):
        if self.mode == "lan_host":
            self._publish_room(1, "waiting")
            self.lan_game_status.setText("對手已離線；等待新玩家加入")

    def _send_lan_action(self, direction):
        if self.mode == "lan_host":
            self.lan_host.action("left", direction)
        elif self.mode == "lan_client":
            self.lan_client.action(direction)

    def _lan_match_finished(self, winner):
        self.lan_game_status.setText(f"比賽結束：{winner}")
        if self.mode != "lan_host" or self._announcement_sent or winner == "平手":
            return
        self._announcement_sent = True
        self._publish_room(2, "finished")
        loser = self.lan_host.guest_name if winner == self.lan_host.host_name else self.lan_host.host_name
        publish_game_announcement(
            f'"{winner}"在貪食蛇區網對戰中擊敗了"{loser}"',
            store=self.chat_store,
        )
        award_multiplayer_victory(
            winner,
            "snake",
            match_id=self._hosted_room_id,
            user_id=self.lan_host.winner_user_id(),
            computer_name=self.lan_host.winner_computer_name(),
            store=GameRankingStore(self.chat_store.root),
        )

    def leave_lan_match(self):
        self.room_directory.clear_hosted_room()
        self.lan_host.close()
        self.lan_client.disconnect()
        self._hosted_room_id = ""

    def shutdown(self):
        self.board.timer.stop()
        self.leave_lan_match()
        self.room_directory.stop()

    def abort_current_workflow(self):
        self.board.timer.stop()
        self.board.running = False
        self.leave_lan_match()
        self.mode = ""
        self.pages.setCurrentWidget(self.lobby_page)

    def set_return_work_available(self, available):
        self.return_work_button.setEnabled(bool(available))

    def _set_bonus_status(self, text):
        self.bonus_label.setText(text)
        self.bonus_label.setVisible(bool(text))

    def _on_game_finished(self, score):
        computer = local_computer_name()
        try:
            entry = self.score_store.record(computer, score, computer_name=computer)
            rank = next((index+1 for index, candidate in enumerate(self.score_store.top(10))
                         if candidate == entry), None)
            if rank is not None:
                publish_game_announcement(
                    f'"{computer}"在貪食蛇中破了第{rank}名的紀錄，太神啦',
                    store=self.chat_store,
                )
        except SnakeScoreError as error:
            QMessageBox.warning(self, "排行榜未儲存", str(error))
        self.refresh_leaderboard()
        self.start_button.setText("再玩一次")

    def refresh_leaderboard(self):
        try:
            entries = self.score_store.top(10)
        except SnakeScoreError as error:
            entries = []
            self.leaderboard.setToolTip(str(error))
        self.leaderboard.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = (str(row + 1), entry.display_name, str(entry.score))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                item.setForeground(QColor("#ffffff"))
                if column == 1:
                    name_font = QFont(self.leaderboard.font())
                    name_font.setPointSize(18)
                    name_font.setBold(True)
                    item.setFont(name_font)
                self.leaderboard.setItem(row, column, item)
            self.leaderboard.setRowHeight(row, 46)
