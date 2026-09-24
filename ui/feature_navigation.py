"""Shared registry-driven navigation shown at the top of every feature page."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QToolButton


FEATURE_NAVIGATION_HEIGHT = 48


class FeatureNavigationBar(QFrame):
    """Render home and feature buttons without importing the feature registry."""

    def __init__(self, go_home_callback, current_feature_id, parent=None):
        super().__init__(parent)
        self.go_home_callback = go_home_callback
        self.current_feature_id = current_feature_id
        self.open_feature_callback = None
        self.open_chat_callback = None
        self.setObjectName("FeatureNavigation")
        # Every formal feature reserves exactly the same top strip.  The
        # slightly taller fixed slot accommodates the globally enlarged button
        # text without causing the navigation row to alter page geometry.
        self.setFixedHeight(FEATURE_NAVIGATION_HEIGHT)

        self.navigation_layout = QHBoxLayout(self)
        self.navigation_layout.setContentsMargins(6, 4, 6, 4)
        self.navigation_layout.setSpacing(6)
        self.navigation_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._add_button("回首頁", self.go_home_callback, active=False)
        self.navigation_layout.addStretch()
        self._add_unread_badge()

    def configure(self, features, open_feature_callback):
        self.open_feature_callback = open_feature_callback
        while self.navigation_layout.count():
            item = self.navigation_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        self._add_button("回首頁", self.go_home_callback, active=False)
        for feature in features:
            if not feature.get("show_in_navigation", True):
                continue
            feature_id = feature["id"]
            self._add_button(
                feature.get("navigation_title", feature["title"]),
                lambda checked=False, wanted=feature_id: self._open_feature(wanted),
                active=feature_id == self.current_feature_id,
            )
        self.navigation_layout.addStretch()
        self._add_unread_badge()

    def set_chat_unread_handler(self, callback):
        self.open_chat_callback = callback

    def set_unread_count(self, count):
        normalized = max(0, int(count))
        self.unread_badge.setText(str(normalized) if normalized else "")
        self.unread_badge.setVisible(normalized > 0)
        self.unread_badge.setToolTip(
            f"聊天室有 {normalized} 則未讀訊息；點擊前往聊天室"
            if normalized
            else ""
        )

    def _add_unread_badge(self):
        self.unread_badge = QToolButton()
        self.unread_badge.setObjectName("ChatUnreadBadge")
        self.unread_badge.setFixedSize(30, 30)
        self.unread_badge.setVisible(False)
        self.unread_badge.clicked.connect(self._open_chat)
        self.navigation_layout.addWidget(self.unread_badge)

    def _open_chat(self):
        if self.open_chat_callback is not None:
            self.open_chat_callback()

    def _open_feature(self, feature_id):
        if self.open_feature_callback is not None:
            self.open_feature_callback(feature_id)

    def _add_button(self, title, callback, *, active):
        button = QPushButton(title)
        button.setObjectName(
            "ActiveFeatureNavigationButton"
            if active
            else "FeatureNavigationButton"
        )
        button.setFixedHeight(38)
        button.setMinimumWidth(92)
        button.clicked.connect(callback)
        self.navigation_layout.addWidget(button)
        return button
