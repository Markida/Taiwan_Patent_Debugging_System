from PySide6.QtWidgets import (
    QWidget,
    QPushButton,
    QLabel,
    QVBoxLayout,
    QFrame
)
from PySide6.QtCore import Qt


class HomePage(QWidget):
    def __init__(self, features, open_feature_callback):
        super().__init__()

        self.features = features
        self.open_feature_callback = open_feature_callback

        self.build_ui()

    def build_ui(self):
        self.setObjectName("HomePage")

        layout = QVBoxLayout()
        layout.setContentsMargins(70, 70, 70, 70)
        layout.setSpacing(24)

        title = QLabel("Saint-Island_Patent_MDS")
        title.setAlignment(Qt.AlignCenter)
        title.setObjectName("HomeTitle")

        subtitle = QLabel("Patent Mistake Detection System")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setObjectName("HomeSubtitle")

        description = QLabel("專利文件與圖式處理、錯誤檢核、標號識別及資料比對平台")
        description.setAlignment(Qt.AlignCenter)
        description.setObjectName("HomeDescription")

        card = QFrame()
        card.setObjectName("HomeCard")

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(42, 38, 42, 38)
        card_layout.setSpacing(18)

        card_title = QLabel("功能入口")
        card_title.setAlignment(Qt.AlignCenter)
        card_title.setObjectName("CardTitle")

        card_layout.addWidget(card_title)

        for feature in self.features:
            button = QPushButton(feature["title"])
            button.setObjectName("PrimaryHomeButton")
            button.setMinimumHeight(56)

            feature_id = feature["id"]
            button.clicked.connect(
                lambda checked=False, fid=feature_id: self.open_feature_callback(fid)
            )

            card_layout.addWidget(button)

        placeholder_button = QPushButton("更多功能即將加入")
        placeholder_button.setObjectName("DisabledHomeButton")
        placeholder_button.setMinimumHeight(46)
        placeholder_button.setEnabled(False)

        card_layout.addWidget(placeholder_button)

        card.setLayout(card_layout)

        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(description)
        layout.addSpacing(14)
        layout.addWidget(card, alignment=Qt.AlignCenter)
        layout.addStretch()

        self.setLayout(layout)
