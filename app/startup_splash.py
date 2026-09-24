"""Small animated startup window shown while the main UI is constructed."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from app.paths import get_app_icon_path


class StartupSplash(QWidget):
    """Light-blue, non-blocking visual feedback for application startup."""

    _FRAMES = ("●○○○", "○●○○", "○○●○", "○○○●")

    def __init__(self):
        super().__init__(
            None,
            Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setObjectName("StartupSplash")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(540, 300)
        self._frame_index = 0
        self._build_ui()
        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(260)
        self.animation_timer.timeout.connect(self._advance_animation)
        self.animation_timer.start()

    def _build_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(18, 18, 18, 18)

        panel = QFrame()
        panel.setObjectName("StartupSplashPanel")
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 8)
        shadow.setColor(Qt.gray)
        panel.setGraphicsEffect(shadow)
        outer_layout.addWidget(panel)

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(34, 28, 34, 28)
        panel_layout.setSpacing(8)

        identity_layout = QHBoxLayout()
        identity_layout.setSpacing(16)
        icon_label = QLabel()
        icon_label.setObjectName("StartupSplashIcon")
        icon_label.setFixedSize(72, 72)
        icon_path = get_app_icon_path()
        if icon_path.is_file():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                icon_label.setPixmap(
                    pixmap.scaled(
                        68,
                        68,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
                icon_label.setAlignment(Qt.AlignCenter)
        identity_layout.addWidget(icon_label)

        identity_text_layout = QVBoxLayout()
        identity_text_layout.setSpacing(1)
        title = QLabel("Saint-Island_Patent_MDS")
        title.setObjectName("StartupSplashTitle")
        subtitle = QLabel("Mistake Detection System")
        subtitle.setObjectName("StartupSplashSubtitle")
        identity_text_layout.addWidget(title)
        identity_text_layout.addWidget(subtitle)
        identity_layout.addLayout(identity_text_layout, 1)
        panel_layout.addLayout(identity_layout)
        panel_layout.addStretch(1)

        self.status_label = QLabel("正在啟動主程式")
        self.status_label.setObjectName("StartupSplashStatus")
        self.status_label.setAlignment(Qt.AlignCenter)
        panel_layout.addWidget(self.status_label)

        self.animation_label = QLabel(self._FRAMES[0])
        self.animation_label.setObjectName("StartupSplashAnimation")
        self.animation_label.setAlignment(Qt.AlignCenter)
        panel_layout.addWidget(self.animation_label)

        progress = QProgressBar()
        progress.setObjectName("StartupSplashProgress")
        progress.setRange(0, 0)
        progress.setTextVisible(False)
        progress.setFixedHeight(10)
        panel_layout.addWidget(progress)

        self.setStyleSheet(
            """
            #StartupSplashPanel {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #f5fbff,
                    stop: 0.5 #e0f2fe,
                    stop: 1 #bfdbfe
                );
                border: 1px solid #93c5fd;
                border-radius: 22px;
            }
            #StartupSplashTitle {
                color: #0f2742;
                font-family: "Microsoft JhengHei", "Noto Sans TC", Arial;
                font-size: 24px;
                font-weight: 900;
            }
            #StartupSplashSubtitle {
                color: #1e3a5f;
                font-family: "Microsoft JhengHei", Arial;
                font-size: 14px;
                font-weight: 700;
            }
            #StartupSplashStatus {
                color: #172033;
                font-family: "Microsoft JhengHei", "Noto Sans TC", Arial;
                font-size: 15px;
                font-weight: 700;
            }
            #StartupSplashAnimation {
                color: #2563eb;
                font-family: Arial;
                font-size: 18px;
                font-weight: 900;
                letter-spacing: 5px;
            }
            #StartupSplashProgress {
                background: #ffffff;
                border: 1px solid #93c5fd;
                border-radius: 5px;
            }
            #StartupSplashProgress::chunk {
                background: #3b82f6;
                border-radius: 4px;
            }
            """
        )

    def showEvent(self, event):
        super().showEvent(event)
        screen = QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            self.move(
                geometry.center().x() - self.width() // 2,
                geometry.center().y() - self.height() // 2,
            )

    def _advance_animation(self):
        self._frame_index = (self._frame_index + 1) % len(self._FRAMES)
        self.animation_label.setText(self._FRAMES[self._frame_index])

    def set_status(self, message: str):
        self.status_label.setText(str(message or "正在啟動主程式"))
        QApplication.processEvents()

    def finish(self, main_window=None):
        self.animation_timer.stop()
        if main_window is not None:
            main_window.raise_()
            main_window.activateWindow()
        self.close()
        self.deleteLater()
