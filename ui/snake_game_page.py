"""Hidden keyboard-controlled snake game."""

from __future__ import annotations

import random

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.features.snake.score_store import (
    SnakeScoreError,
    SnakeScoreStore,
)


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
        self.setMinimumSize(720, 520)
        self.setFocusPolicy(Qt.StrongFocus)
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
            self.score += self.NORMAL_SCORE
            self.normal_food_count += 1
            self.food = self._empty_cell()
            if self.normal_food_count % 3 == 0:
                self.bonus_food = self._empty_cell()
                self.bonus_ticks = self.BONUS_LIFETIME_TICKS
        elif eats_bonus:
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
        painter.setRenderHint(QPainter.Antialiasing, False)
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
            painter.fillRect(cell_rect(self.food), QColor("#ef4444"))
        if self.bonus_food is not None:
            painter.fillRect(cell_rect(self.bonus_food), QColor("#facc15"))
            painter.setPen(QPen(QColor("#fff7ae"), 2))
            painter.drawRect(cell_rect(self.bonus_food))
        for index, position in enumerate(self.snake):
            painter.fillRect(
                cell_rect(position),
                QColor("#22d3a7" if index else "#67e8c4"),
            )
        painter.end()


class SnakeGamePage(QWidget):
    """A deliberately unregistered page reachable only through the title P."""

    def __init__(self, go_home_callback, score_store=None):
        super().__init__()
        self.go_home_callback = go_home_callback
        self.score_store = score_store or SnakeScoreStore()
        self._build_ui()
        self.refresh_leaderboard()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 16, 22, 18)
        layout.setSpacing(10)

        header = QHBoxLayout()
        back_button = QPushButton("返回首頁")
        back_button.setObjectName("SecondaryButton")
        back_button.clicked.connect(self.go_home_callback)
        header.addWidget(back_button)
        title = QLabel("SNAKE")
        title.setObjectName("SnakeTitle")
        title.setAlignment(Qt.AlignCenter)
        header.addStretch()
        header.addWidget(title)
        header.addStretch()
        self.score_label = QLabel("分數：0")
        self.score_label.setObjectName("SnakeScore")
        header.addWidget(self.score_label)
        layout.addLayout(header)

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
        leaderboard_title = QLabel("最高分前十名")
        leaderboard_title.setObjectName("PanelTitle")
        side_layout.addWidget(leaderboard_title)
        self.leaderboard = QTableWidget(0, 3)
        self.leaderboard.setObjectName("SnakeLeaderboard")
        self.leaderboard.setHorizontalHeaderLabels(["名次", "名字", "分數"])
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
            "方向鍵或 W/A/S/D 移動\n空白鍵暫停／繼續\n"
            "每吃 3 個紅色食物，會出現限時黃色大食物"
        )
        instructions.setWordWrap(True)
        instructions.setAlignment(Qt.AlignCenter)
        side_layout.addWidget(instructions)
        self.start_button = QPushButton("開始／重新開始")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self.board.restart_game)
        side_layout.addWidget(self.start_button)
        content.addWidget(side_panel)
        content.setStretch(0, 4)
        content.setStretch(1, 1)
        layout.addLayout(content, 1)

    def prepare_game(self):
        self.board.reset_game()
        self.refresh_leaderboard()
        self.board.setFocus(Qt.OtherFocusReason)

    def _set_bonus_status(self, text):
        self.bonus_label.setText(text)
        self.bonus_label.setVisible(bool(text))

    def _on_game_finished(self, score):
        name, accepted = QInputDialog.getText(
            self,
            "遊戲結束",
            f"本次分數：{score}\n輸入排行榜名稱：",
        )
        if accepted:
            try:
                self.score_store.record(name, score)
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
            values = (str(row + 1), entry.name, str(entry.score))
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
