from PySide6.QtWidgets import (
    QWidget,
    QPushButton,
    QLabel,
    QHBoxLayout,
    QVBoxLayout,
    QFrame
)
from PySide6.QtCore import Qt, QTimer


class HomePage(QWidget):
    def __init__(
        self,
        features,
        open_feature_callback,
        open_secret_callback=None,
    ):
        super().__init__()

        self.features = features
        self.open_feature_callback = open_feature_callback
        self.open_secret_callback = open_secret_callback
        self._secret_click_count = 0
        self._secret_click_timer = QTimer(self)
        self._secret_click_timer.setSingleShot(True)
        self._secret_click_timer.setInterval(2500)
        self._secret_click_timer.timeout.connect(self._reset_secret_clicks)

        self.build_ui()

    def build_ui(self):
        self.setObjectName("HomePage")

        layout = QVBoxLayout()
        layout.setContentsMargins(70, 70, 70, 70)
        layout.setSpacing(24)

        title_row = QHBoxLayout()
        title_row.setSpacing(0)
        title_row.setAlignment(Qt.AlignCenter)
        title_prefix = QLabel("Saint-Island_")
        title_prefix.setObjectName("HomeTitle")
        title_suffix = QLabel("atent_MDS")
        title_suffix.setObjectName("HomeTitle")
        self.secret_p_button = QPushButton("P")
        self.secret_p_button.setObjectName("HiddenTitleLetter")
        self.secret_p_button.setFlat(True)
        self.secret_p_button.setFocusPolicy(Qt.NoFocus)
        self.secret_p_button.clicked.connect(self._secret_title_clicked)
        title_row.addWidget(title_prefix)
        title_row.addWidget(self.secret_p_button)
        title_row.addWidget(title_suffix)

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
            if not feature.get("show_on_home", True):
                continue
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
        layout.addLayout(title_row)
        layout.addWidget(subtitle)
        layout.addWidget(description)
        layout.addSpacing(14)
        layout.addWidget(card, alignment=Qt.AlignCenter)
        layout.addStretch()

        self.setLayout(layout)

    def _secret_title_clicked(self):
        self._secret_click_count += 1
        self._secret_click_timer.start()
        if self._secret_click_count < 5:
            return
        self._reset_secret_clicks()
        if callable(self.open_secret_callback):
            self.open_secret_callback()

    def _reset_secret_clicks(self):
        self._secret_click_timer.stop()
        self._secret_click_count = 0
