"""A bounded, non-layout companion shared by all application editions."""

from __future__ import annotations

import math
import random
import time
from datetime import datetime

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QToolButton, QVBoxLayout, QWidget,
)

from features.workflow_pet.guidance import Guidance, GuidanceMemory, guidance_for, startup_greeting
from ui.pixel_cat import ACTIONS, ACTION_NAMES, MENU_ACTIONS, paint_cat


PET_STYLE = """
QFrame#PetSpeech { background: #edf7ff; border: 1px solid #a5c7e2; border-radius: 12px; }
QLabel { background: transparent; color: #243b53; border: none; font-size: 14px; }
QLabel#PetCaption { color: #4f7597; font-size: 12px; font-weight: 600; }
QPushButton { background: #d5eaff; color: #214d79; border: none; border-radius: 6px;
              padding: 5px 9px; font-size: 13px; min-width: 0; min-height: 0; }
QPushButton:hover { background: #bddbf7; }
QToolButton { background: transparent; color: #42698b; border: none; padding: 0;
              min-width: 0; min-height: 0; font-size: 15px; }
"""


class HorizontalShakeDetector:
    """Recognize deliberate alternating strokes, not ordinary drag or jitter."""

    MIN_TRAVEL = 24  # Qt logical pixels, independent of monitor scaling.
    MAX_STROKE_SECONDS = 0.35
    WINDOW_SECONDS = 1.1
    REQUIRED_STROKES = 4

    def reset(self, point, now):
        self.extreme = QPoint(point)
        self.direction = 0
        self.last_turn = now
        self.strokes = []
        self.triggered = False

    def sample(self, point, now):
        if self.triggered:
            return False
        dx, dy = point.x()-self.extreme.x(), point.y()-self.extreme.y()
        if abs(dy) > max(self.MIN_TRAVEL, abs(dx)):
            self.reset(point, now)
            return False
        direction = 1 if dx > 0 else -1
        if direction == self.direction:
            self.extreme = QPoint(point)
            return False
        if abs(dx) < self.MIN_TRAVEL:
            return False
        if now-self.last_turn > self.MAX_STROKE_SECONDS:
            self.strokes.clear()
        self.strokes = [t for t in self.strokes if now-t <= self.WINDOW_SECONDS]
        self.strokes.append(now)
        self.extreme, self.direction, self.last_turn = QPoint(point), direction, now
        self.triggered = len(self.strokes) >= self.REQUIRED_STROKES
        return self.triggered


class CatSprite(QWidget):
    def __init__(self, controller, parent):
        super().__init__(parent)
        self.controller = controller
        self.setFixedSize(144, 120)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("墨墨｜拖動換位置 · 點一下看提示 · 右鍵互動")
        self.setAccessibleName("黑貓助手墨墨")
        self._press = None
        self._origin = None
        self._dragged = False
        self._shake = HorizontalShakeDetector()

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_cat(painter, self.rect(), self.controller.action,
                  self.controller.frame, self.controller.facing)
        painter.end()

    def enterEvent(self, event):
        self.controller.hover_cat()
        super().enterEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.controller.stop_action()
            self._press = event.globalPosition().toPoint()
            self._origin = self.pos()
            self._dragged = False
            self._shake.reset(self._press, self.controller.clock())
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self._press
            if delta.manhattanLength() >= QApplication.startDragDistance():
                self._dragged = True
            if self._dragged:
                self.controller.move_cat(self._origin + delta)
                if self._shake.sample(event.globalPosition().toPoint(), self.controller.clock()):
                    self.controller.shake_cat()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press is not None:
            self._press = None
            self._origin = None
            self.controller.note_activity()
            self.setCursor(Qt.OpenHandCursor)
            if self._dragged:
                self.controller.save_position()
            else:
                self.controller.ask_hint()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def cancel_drag(self):
        # A lost mouse grab/deactivation must not leave idle animations blocked.
        self._press = self._origin = None
        self._dragged = False
        self.setCursor(Qt.OpenHandCursor)

    def event(self, event):
        if event.type() == QEvent.UngrabMouse and hasattr(self, "_press"):
            self.cancel_drag()
        return super().event(event)

    def contextMenuEvent(self, event):
        self.controller.open_menu(event.globalPos())
        event.accept()


class PetSpeech(QFrame):
    def __init__(self, controller, parent):
        super().__init__(parent)
        self.setObjectName("PetSpeech")
        self.setAttribute(Qt.WA_NoMousePropagation)
        self.setStyleSheet(PET_STYLE)
        self.setFixedWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self.caption = QLabel("墨墨 · 下一步")
        self.caption.setObjectName("PetCaption")
        header.addWidget(self.caption)
        header.addStretch()
        close = QToolButton()
        close.setText("×")
        close.setFixedSize(22, 22)
        close.setToolTip("收起這則提示（點貓咪可再看）")
        close.clicked.connect(self.hide)
        header.addWidget(close)
        layout.addLayout(header)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        layout.addWidget(self.message)
        self.next_button = QPushButton()
        self.next_button.setFocusPolicy(Qt.NoFocus)
        self.next_button.clicked.connect(lambda _checked=False: controller.follow_hint())
        layout.addWidget(self.next_button, alignment=Qt.AlignLeft)
        self.alternative_button = QPushButton()
        self.alternative_button.setFocusPolicy(Qt.NoFocus)
        self.alternative_button.clicked.connect(
            lambda _checked=False: controller.follow_hint(alternative=True))
        layout.addWidget(self.alternative_button, alignment=Qt.AlignLeft)
        self.alternative_button.hide()


class WorkflowPet(QObject):
    IDLE_SECONDS = 10.0
    AMBIENT_MS = 2000
    FRAME_MS = 80

    def __init__(self, window, *, settings=None, clock=time.monotonic, rng=None,
                 wall_clock=None):
        super().__init__(window)
        self.window = window
        self.settings = settings if settings is not None else QSettings(
            "Saint-Island", "Patent_MDS_Companion")
        self.clock = clock
        self.wall_clock = wall_clock if wall_clock is not None else datetime.now
        self.random = rng or random.Random()
        self.dismissed = self.settings.value("dismissed", False, type=bool)
        self.sound_enabled = self.settings.value("sound", False, type=bool)
        # Respect a previously dismissed companion. Otherwise greet once when
        # the real window first becomes visible and active, not during import.
        self._startup_greeted = self.dismissed
        self._greeting_until = 0.0
        self.action, self.frame, self.facing = "idle", 0, 1
        self._closed = False
        self._menu = None
        self._last_input = self.clock()
        self._next_idle = self._last_input + self.IDLE_SECONDS
        self._last_hint_key = ""
        self._hint = Guidance("initial", "")
        self._guidance_memory = GuidanceMemory()
        self._hint_scope = None
        self._last_hint_scope = None
        self._action_start = 0.0
        self._action_origin = QPoint()
        self._action_duration = 0.0
        self._attached_pages = set()
        self._error = None
        self._positioned = False
        self._sound = None
        self._sound_file = None
        self._next_shake = 0.0
        self.cat = CatSprite(self, window)
        self.bubble = PetSpeech(self, window)
        self.bubble.hide()
        self.recall = QToolButton(window)
        self.recall.setObjectName("RecallWorkflowCat")
        self.recall.setStyleSheet(
            "QToolButton { background:#e3f1fd; border:1px solid #9dbbd5;"
            "border-radius:8px; padding:0; min-width:0; min-height:0; }"
            "QToolButton:hover { background:#c9e5fc; }")
        self.recall.setFixedSize(38, 34)
        icon = QPixmap(48, 40)
        icon.fill(Qt.transparent)
        painter = QPainter(icon)
        paint_cat(painter, icon.rect())
        painter.end()
        self.recall.setIcon(QIcon(icon))
        self.recall.setIconSize(QSize(38, 32))
        self.recall.setToolTip("叫回黑貓助手墨墨")
        self.recall.setAccessibleName("叫回黑貓助手")
        self.recall.clicked.connect(self.recall_cat)
        self.recall.hide()
        self.cat.setVisible(not self.dismissed)
        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(self.FRAME_MS)
        self.frame_timer.timeout.connect(self._animate)
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(1000)
        self.pulse_timer.timeout.connect(self._pulse)
        self.ambient_timer = QTimer(self)
        self.ambient_timer.setInterval(self.AMBIENT_MS)
        self.ambient_timer.setTimerType(Qt.PreciseTimer)
        self.ambient_timer.timeout.connect(self._ambient_tick)
        self.bubble_timer = QTimer(self)
        self.bubble_timer.setSingleShot(True)
        self.bubble_timer.timeout.connect(self.bubble.hide)
        window.currentChanged.connect(self._page_changed)
        application = QApplication.instance()
        application.installEventFilter(self)
        application.applicationStateChanged.connect(self._sync_activity)
        self._page_changed()

    def _available(self):
        return (not self._closed and self.window.isVisible()
                and not self.window.isMinimized()
                and QApplication.applicationState() == Qt.ApplicationActive)

    def _sync_activity(self, *_args):
        if self._closed:
            return
        if self._available() and not self.dismissed:
            if not self.pulse_timer.isActive():
                self.note_activity()
                self.pulse_timer.start()
                self._pulse()
            self._resume_ambient()
        else:
            self.cat.cancel_drag()
            self.pulse_timer.stop()
            self.ambient_timer.stop()
            self.stop_action()
            self.bubble_timer.stop()
            self.bubble.hide()

    def _feature(self):
        current = self.window.currentWidget()
        if current is getattr(self.window, "home_page", None):
            return "home", current
        for key, page in self.window.feature_pages.items():
            if page is current:
                return key, page
        return "other", current

    def _page_changed(self, *_args):
        if self._closed:
            return
        self.stop_action()
        self.cat.cancel_drag()
        self._greeting_until = 0.0
        self.bubble.hide()
        self.note_activity()
        self._last_hint_key = ""
        self._error = None
        page = self.window.currentWidget()
        self._attach_page(page)
        self._layout()
        self.cat.setVisible(not self.dismissed)
        self.recall.setVisible(self.dismissed)
        self.cat.raise_()
        self.bubble.raise_()
        self.recall.raise_()
        self._sync_activity()

    def _attach_page(self, page):
        if page is None or page in self._attached_pages:
            return
        self._attached_pages.add(page)
        table = getattr(page, "issue_table", None)
        if table is not None:
            # Only explicit clicks/keyboard activation count as viewed. The
            # automatic first-row selection after loading is not user review.
            table.itemClicked.connect(lambda item, owner=page: self._visit_issue(owner, item))
            table.itemActivated.connect(lambda item, owner=page: self._visit_issue(owner, item))
        # A fixed empty slot keeps the recall icon clear of existing controls.
        # It stays reserved in both states, so dismissal cannot move a layout.
        for nav in page.findChildren(QFrame):
            if hasattr(nav, "navigation_layout"):
                layout = nav.navigation_layout
                if layout is not None:
                    margins = layout.contentsMargins()
                    layout.setContentsMargins(margins.left(), margins.top(),
                                              margins.right() + 44, margins.bottom())
        for name in ("_review_tasks", "_pdf_tasks", "_rotation_tasks"):
            runner = getattr(page, name, None)
            if runner is not None:
                runner.failed.connect(lambda error, owner=page: self._task_failed(owner))
                runner.succeeded.connect(lambda _result, owner=page: self._task_succeeded(owner))

    def _task_failed(self, page):
        if page is self.window.currentWidget():
            self._error = (page, self.clock() + 25)
            self._last_hint_key = ""

    def _task_succeeded(self, page):
        if self._error and self._error[0] is page:
            self._error = None

    def note_activity(self):
        self._last_input = self.clock()
        self._next_idle = self._last_input + self.IDLE_SECONDS

    def eventFilter(self, watched, event):
        if self._closed:
            return False
        kind = event.type()
        if watched is self.window and kind in (
            QEvent.Resize, QEvent.Show, QEvent.WindowStateChange, QEvent.Hide, QEvent.Close,
        ):
            if kind == QEvent.Close:
                self.shutdown()
            else:
                self._layout()
                self._sync_activity()
        elif kind in (QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.KeyPress,
                      QEvent.Wheel, QEvent.TouchBegin, QEvent.Drop):
            if isinstance(watched, QWidget) and watched.window() is self.window:
                self.note_activity()
                # Moving away is not a dismissal or an instruction to stop an
                # animation. Only deliberate interaction with the work area is.
                inside_pet = (watched is self.cat or watched is self.bubble
                              or self.bubble.isAncestorOf(watched))
                if kind != QEvent.MouseMove and not inside_pet:
                    self.bubble.hide()
                    # Small in-place gestures must not be cut off by typing.
                    if self.action not in ("idle_fidget", "hover"):
                        self.stop_action()
        return False  # Never consume typing, wheel, shortcuts or file drops.

    def _bounds(self):
        # Keep the title/navigation area free. Coordinates are Qt logical pixels.
        top = min(56 + self.bubble.sizeHint().height(),
                  max(0, self.window.height()-self.cat.height()))
        return QRect(0, top,
                     max(0, self.window.width()-self.cat.width()),
                     max(0, self.window.height()-self.cat.height()-top))

    def move_cat(self, position):
        bounds = self._bounds()
        self.cat.move(min(max(position.x(), bounds.x()), bounds.x()+bounds.width()),
                      min(max(position.y(), bounds.y()), bounds.y()+bounds.height()))
        if self.bubble.isVisible():
            self._place_bubble()

    def _layout(self):
        if not self._positioned and self.window.width() > 0:
            self._positioned = True
            x = self.settings.value("x", 0.76, type=float)
            y = self.settings.value("y", 0.77, type=float)
            if not math.isfinite(x): x = 0.76
            if not math.isfinite(y): y = 0.77
            self.cat.move(round(max(0.0, min(1.0, x)) *
                                max(0, self.window.width()-self.cat.width())),
                          round(max(0.0, min(1.0, y)) *
                                max(0, self.window.height()-self.cat.height())))
        self.move_cat(self.cat.pos())
        self.recall.move(max(0, self.window.width()-50), 10)
        self.recall.raise_()

    def _place_bubble(self):
        self.bubble.setFixedWidth(min(300, max(140, self.window.width()-12)))
        self.bubble.ensurePolished()
        layout = self.bubble.layout()
        layout.invalidate()
        layout.activate()
        self.bubble.adjustSize()
        # Wrapped text plus two choices needs its height-for-width, not a
        # stale pre-polish size hint from the previous, shorter bubble.
        height = layout.totalHeightForWidth(self.bubble.width())
        if height > 0:
            self.bubble.resize(self.bubble.width(), height)
        layout.activate()
        minimum_y = min(56+self.bubble.height(),
                        max(0, self.window.height()-self.cat.height()))
        if self.cat.y() < minimum_y:
            self.cat.move(self.cat.x(), minimum_y)
        x = self.cat.x() + self.cat.width()//2 - self.bubble.width()//2
        y = self.cat.y() - self.bubble.height() + 8
        self.bubble.move(max(0, min(x, self.window.width()-self.bubble.width())),
                         max(0, min(y, self.window.height()-self.bubble.height())))

    def save_position(self):
        self.settings.setValue("x", self.cat.x()/max(1, self.window.width()-self.cat.width()))
        self.settings.setValue("y", self.cat.y()/max(1, self.window.height()-self.cat.height()))

    def _get_hint(self):
        feature, page = self._feature()
        if self._error and self._error[0] is page and self.clock() < self._error[1]:
            return Guidance("task-error", "這一步沒有完成。請先閱讀錯誤訊息，"
                            "確認檔案仍可讀取，再重試；既有內容請先確認後再繼續。")
        return guidance_for(feature, page, getattr(self.window, "workflow_context", None),
                            self._guidance_memory)

    def _visit_issue(self, page, item):
        first = page.issue_table.item(item.row(), 0)
        if first is not None:
            self._guidance_memory.visit_issue(page, first.data(Qt.UserRole))

    def _guidance_scope(self):
        _, page = self._feature()
        return (id(page), id(getattr(page, "document", None)),
                id(getattr(page, "review", None)), id(getattr(page, "all_results", None)))

    def _present_hint(self, hint):
        self._hint = hint
        self._hint_scope = self._guidance_scope()
        self._show_speech(hint.text, button=hint.button,
                          alternative=hint.alternative.button)

    def _show_speech(self, text, *, caption="墨墨 · 下一步", button="", alternative=""):
        if self.dismissed or not self._available():
            return
        self.bubble.caption.setText(caption)
        self.bubble.message.setText(text)
        self.bubble.next_button.setText(button)
        self.bubble.next_button.setVisible(bool(button))
        self.bubble.alternative_button.setText(alternative)
        self.bubble.alternative_button.setVisible(bool(alternative))
        self._place_bubble()
        self.bubble.show()
        self.bubble.raise_()
        self.bubble_timer.start(min(24000, max(12000, len(text) * 100)))

    def report_save_status(self, page, message):
        """Announce local figure-result persistence without a permanent UI row."""

        if (self.window.currentWidget() is not page
                or self.dismissed or not self._available()):
            return
        self._show_speech(message, caption="墨墨 · 本機保存")
        hint = self._get_hint()
        self._last_hint_key = hint.key
        self._last_hint_scope = self._guidance_scope()

    def ask_hint(self):
        self._greeting_until = 0.0
        self.note_activity()
        self._present_hint(self._get_hint())
        self.start_action("blink")

    def follow_hint(self, *, alternative=False):
        # Re-resolve after any state change; a visible old hint is never an action.
        hint = self._get_hint()
        if (hint.key != self._hint.key or self._hint_scope != self._guidance_scope()
                or (hint.button, hint.feature, hint.target, hint.alternative)
                != (self._hint.button, self._hint.feature, self._hint.target, self._hint.alternative)):
            self.ask_hint()
            return
        choice = hint.alternative if alternative else hint
        if not choice.button:
            return
        self.bubble.hide()
        self.note_activity()
        if getattr(choice, "effect", "") == "skip_repeated":
            self._guidance_memory.skip_repeated = True
        if choice.feature:
            self.window.open_feature(choice.feature)
        elif choice.target:
            target = getattr(self.window.currentWidget(), choice.target, None)
            if isinstance(target, QWidget) and target.isVisible() and target.isEnabled():
                target.setFocus(Qt.OtherFocusReason)

    def _pulse(self):
        if not self._available() or self.dismissed:
            self._sync_activity()
            return
        if (QApplication.activeModalWidget() or QApplication.activePopupWidget()
                or self.cat._press is not None):
            self.note_activity()
            return
        if not self._startup_greeted:
            self._startup_greeted = True
            self._greeting_until = self.clock() + 8.0
            self._show_speech(startup_greeting(self.wall_clock()), caption="墨墨 · 跟你打聲招呼")
            self._start_gesture("hover", 1.2)
            return
        if self.clock() < self._greeting_until and self.bubble.isVisible():
            return  # Let the greeting be read before showing the next-step tip.
        hint = self._get_hint()
        # Counts may change on every edit; announce only a new workflow stage.
        scope = self._guidance_scope()
        if hint.key != self._last_hint_key or scope != self._last_hint_scope:
            self._last_hint_key = hint.key
            self._last_hint_scope = scope
            self._present_hint(hint)
        now = self.clock()
        if (self.action not in ACTION_NAMES and now >= self._next_idle
                and now-self._last_input >= self.IDLE_SECONDS):
            self._next_idle = now + self.IDLE_SECONDS
            options = [key for key, _ in ACTIONS if key != self.action]
            self.start_action(self.random.choice(options))

    def _gesture_available(self):
        return (not self._closed and not self.dismissed and self._available()
                and self._menu is None and self.cat._press is None
                and not QApplication.activeModalWidget()
                and not QApplication.activePopupWidget())

    def _start_gesture(self, action, duration):
        self.stop_action()
        self.action, self.frame = action, 0
        self._action_origin = self.cat.pos()
        self._action_start = self.clock()
        self._action_duration = duration
        self.frame_timer.start()
        self.cat.update()

    def _ambient_tick(self):
        # Independent of user input and the ten-second random activity timer.
        # Do not interrupt a full action, a menu, or a drag.
        if self._gesture_available() and self.action == "idle":
            self._start_gesture("idle_fidget", 0.64)

    def hover_cat(self):
        if self._gesture_available() and self.action not in ACTION_NAMES:
            self.note_activity()
            self._start_gesture("hover", 1.2)

    def shake_cat(self):
        now = self.clock()
        if (self.cat._press is None or not self._available() or self.dismissed
                or self._closed or now < self._next_shake):
            return
        self._next_shake = now + 6.0
        self.note_activity()
        self.start_action("dizzy")
        self._show_speech("喵嗚～轉圈圈了……咳，怎麼吐出一顆毛線球！",
                          caption="墨墨 · 暈乎乎")

    def start_action(self, action):
        if self._closed or self.dismissed or not self._available():
            return
        if action not in ACTION_NAMES:
            return
        self.stop_action()
        self.ambient_timer.stop()
        self.action, self.frame = action, 0
        self._action_origin = self.cat.pos()
        self._action_start = self.clock()
        self._action_duration = (8.0 if action == "sleep" else
                                 4.8 if action == "dizzy" else
                                 2.2 if action in ("blink", "jump", "meow") else 5.0)
        self.facing = (-1 if action in ("walk_left", "run_left") else
                       1 if action in ("walk", "run") else
                       (-1 if self.cat.x() > self.window.width()/2 else 1))
        # Auto-roaming always chooses a direction with enough space to move.
        if action in ("walk", "run", "walk_left", "run_left"):
            bounds = self._bounds()
            left = self.cat.x()-bounds.x()
            right = bounds.x()+bounds.width()-self.cat.x()
            room = right if self.facing > 0 else left
            if room < 30 and max(left, right) > room:
                self.facing *= -1
            base = action.removesuffix("_left")
            self.action = base + ("_left" if self.facing < 0 else "")
        if action == "meow":
            self._show_speech("喵～ 做得好，記得休息一下！", caption="墨墨 · 喵喵叫")
            self._play_meow()
        self.frame_timer.start()
        self.cat.update()

    def _animate(self):
        if not self._available() or self.dismissed or self._menu is not None:
            self.stop_action()
            return
        elapsed = self.clock()-self._action_start
        if elapsed >= self._action_duration:
            self.stop_action()
            return
        self.frame = int(elapsed * 12)
        offset = QPoint()
        if self.action in ("walk", "run", "walk_left", "run_left"):
            distance = 26 if self.action.startswith("walk") else 55
            offset.setX(round(self.facing*distance*elapsed))
            bounds = self._bounds()
            next_x = self._action_origin.x()+offset.x()
            if next_x >= bounds.x()+bounds.width() or next_x <= bounds.x():
                self.move_cat(QPoint(next_x, self.cat.y()))
                self.stop_action()
                return
        elif self.action == "jump":
            offset.setY(-round(65*math.sin(math.pi*elapsed/self._action_duration)))
        elif self.action == "roll":
            offset.setX(round(self.facing*8*elapsed))
        if self.action not in ("idle_fidget", "hover", "dizzy"):
            self.move_cat(self._action_origin + offset)
        self.cat.update()

    def _resume_ambient(self):
        if (not self._closed and not self.dismissed and self._available()
                and self.action not in ACTION_NAMES and not self.ambient_timer.isActive()):
            self.ambient_timer.start(self.AMBIENT_MS)

    def stop_action(self):
        if self.action == "jump":
            self.move_cat(self._action_origin)
        self.frame_timer.stop()
        self.action, self.frame = "idle", 0
        self.cat.update()
        if self._sound is not None:
            self._sound.stop()
        self._resume_ambient()

    def dismiss(self):
        self.cat.cancel_drag()
        self.stop_action()
        self.dismissed = True
        self.settings.setValue("dismissed", True)
        self.save_position()
        self.cat.hide()
        self.bubble.hide()
        self.bubble_timer.stop()
        self.pulse_timer.stop()
        self.ambient_timer.stop()
        self.recall.show()
        self.recall.raise_()

    def recall_cat(self):
        self.dismissed = False
        self.settings.setValue("dismissed", False)
        self.recall.hide()
        self.cat.show()
        self.cat.raise_()
        self._layout()
        self._last_hint_key = ""
        self._sync_activity()
        self.ask_hint()

    def open_menu(self, position):
        self.stop_action()
        self.note_activity()
        menu = QMenu(self.cat)
        self._menu = menu
        menu.addAction("現在該做什麼？", self.ask_hint)
        menu.addSeparator()
        for key, label in MENU_ACTIONS:
            menu.addAction(label, lambda action=key: self.start_action(action))
        menu.addSeparator()
        sound = menu.addAction("喵叫聲音（預設安靜）")
        sound.setCheckable(True)
        sound.setChecked(self.sound_enabled)
        sound.toggled.connect(self._set_sound)
        menu.addAction("趕走墨墨，先去休息（可叫回）", self.dismiss)
        def closed():
            self._menu = None
            self.note_activity()
            menu.deleteLater()
        menu.aboutToHide.connect(closed)
        menu.popup(position)

    def _set_sound(self, enabled):
        self.sound_enabled = bool(enabled)
        self.settings.setValue("sound", self.sound_enabled)
        if not self.sound_enabled and self._sound is not None:
            self._sound.stop()

    def _play_meow(self):
        if not self.sound_enabled:
            return
        try:
            if self._sound is None:
                # Tiny synthesized chirp, created once on explicit sound opt-in.
                # Multimedia is optional and never imported during app startup.
                import io
                import struct
                import wave
                from PySide6.QtCore import QTemporaryFile, QUrl
                from PySide6.QtMultimedia import QSoundEffect
                rate, duration = 22050, 0.6
                samples = bytearray()
                phase = 0.0
                for i in range(int(rate*duration)):
                    t = i/rate
                    phase += 2*math.pi*(650-370*t/duration)/rate
                    envelope = math.sin(math.pi*t/duration)**1.5
                    value = envelope*(math.sin(phase)+0.3*math.sin(2*phase))
                    samples.extend(struct.pack("<h", round(9000*value)))
                data = io.BytesIO()
                with wave.open(data, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(rate)
                    wav.writeframes(samples)
                self._sound_file = QTemporaryFile(self)
                if not self._sound_file.open():
                    return
                self._sound_file.write(data.getvalue())
                self._sound_file.flush()
                self._sound = QSoundEffect(self)
                self._sound.setVolume(0.12)
                self._sound.setSource(QUrl.fromLocalFile(self._sound_file.fileName()))
            self._sound.play()
        except (ImportError, RuntimeError, OSError):
            self._show_speech("喵～（這台電腦暫時無法播放聲音，動畫照常。）",
                              caption="墨墨 · 喵喵叫")

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.cat.cancel_drag()
        self.frame_timer.stop()
        self.pulse_timer.stop()
        self.ambient_timer.stop()
        self.bubble_timer.stop()
        if self._sound is not None:
            self._sound.stop()
        if self._menu is not None:
            self._menu.close()
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        self.cat.hide()
        self.bubble.hide()
        self.recall.hide()
