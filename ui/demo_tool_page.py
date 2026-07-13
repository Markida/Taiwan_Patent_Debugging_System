from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class DemoToolPage(QWidget):
    """可複製使用的空白功能頁。"""

    def __init__(self, go_home_callback):
        super().__init__()

        self.go_home_callback = go_home_callback
        self.build_ui()

    def build_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(24, 18, 24, 24)
        main_layout.setSpacing(18)

        header_layout = QHBoxLayout()

        back_button = QPushButton("返回首頁")
        back_button.setObjectName("SecondaryButton")
        back_button.setMaximumHeight(34)
        back_button.clicked.connect(self.go_home_callback)

        title = QLabel("功能測試頁")
        title.setObjectName("PageTitle")
        title.setMaximumHeight(34)

        header_layout.addWidget(back_button)
        header_layout.addWidget(title)
        header_layout.addStretch()

        main_layout.addLayout(header_layout)

        content = QFrame()
        content.setObjectName("Panel")

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(36, 36, 36, 36)
        content_layout.setSpacing(12)

        placeholder_title = QLabel("空白功能模板")
        placeholder_title.setObjectName("PanelTitle")
        placeholder_title.setAlignment(Qt.AlignCenter)

        placeholder_text = QLabel(
            "這裡是新功能的內容區。\n"
            "複製此頁面與 features/demo_tool/ 後，即可開始加入下一個功能。"
        )
        placeholder_text.setAlignment(Qt.AlignCenter)
        placeholder_text.setWordWrap(True)

        content_layout.addStretch()
        content_layout.addWidget(placeholder_title)
        content_layout.addWidget(placeholder_text)
        content_layout.addStretch()

        content.setLayout(content_layout)
        main_layout.addWidget(content)

        self.setLayout(main_layout)
