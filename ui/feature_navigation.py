"""Shared registry-driven navigation shown at the top of every feature page."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton


class FeatureNavigationBar(QFrame):
    """Render home and feature buttons without importing the feature registry."""

    def __init__(self, go_home_callback, current_feature_id, parent=None):
        super().__init__(parent)
        self.go_home_callback = go_home_callback
        self.current_feature_id = current_feature_id
        self.open_feature_callback = None
        self.setObjectName("FeatureNavigation")
        self.setFixedHeight(42)

        self.navigation_layout = QHBoxLayout(self)
        self.navigation_layout.setContentsMargins(6, 4, 6, 4)
        self.navigation_layout.setSpacing(6)
        self.navigation_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._add_button("回首頁", self.go_home_callback, active=False)
        self.navigation_layout.addStretch()

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
        button.setMaximumHeight(32)
        button.setMinimumWidth(92)
        button.clicked.connect(callback)
        self.navigation_layout.addWidget(button)
        return button
