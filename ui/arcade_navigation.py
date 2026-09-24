"""Shared compact navigation for chat and arcade Easter-egg pages."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


ARCADE_NAVIGATION_HEIGHT = 50
ARCADE_DESTINATIONS = (
    ("home", "首頁", "返回首頁"),
    ("work", "工作進度", "返回目前的工作進度"),
    ("chat", "聊天室", "即時聊天室"),
    ("pong", "彈球", "雙人彈球"),
    ("tetris", "方塊", "俄羅斯方塊"),
    ("tank", "坦克", "坦克大戰"),
    ("bulls_and_cows", "1A2B", "1A2B 猜數字"),
    ("snake", "貪食蛇", "貪食蛇"),
)


class ArcadeNavigationBar(QFrame):
    """Keep every Easter-egg page's navigation order and sizing identical."""

    def __init__(
        self,
        *,
        current_id,
        title,
        go_home_callback,
        return_work_callback=None,
        callbacks=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("ArcadeNavigation")
        self.setFixedHeight(ARCADE_NAVIGATION_HEIGHT)
        self.buttons = {}
        self.navigation_layout = QHBoxLayout(self)
        self.navigation_layout.setContentsMargins(6, 4, 6, 4)
        self.navigation_layout.setSpacing(5)
        callback_map = dict(callbacks or {})
        callback_map["home"] = go_home_callback
        callback_map["work"] = return_work_callback

        for destination_id, label, tooltip in ARCADE_DESTINATIONS:
            callback = callback_map.get(destination_id)
            active = destination_id == current_id
            button = QPushButton(label)
            button.setObjectName(
                "ActiveFeatureNavigationButton"
                if active
                else "FeatureNavigationButton"
            )
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.setFixedHeight(36)
            button.setEnabled(not active and callable(callback))
            if callable(callback):
                button.clicked.connect(callback)
            self.navigation_layout.addWidget(button)
            self.buttons[destination_id] = button

        self.return_work_button = self.buttons["work"]
        self.return_work_button.setEnabled(False)
        self.navigation_layout.addStretch(1)
        self.title_label = QLabel(str(title))
        self.title_label.setObjectName("ArcadeTitle")
        self.title_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.navigation_layout.addWidget(self.title_label)

    def add_trailing_widget(self, widget):
        self.navigation_layout.insertWidget(
            self.navigation_layout.indexOf(self.title_label), widget
        )
