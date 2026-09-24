import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from datetime import datetime
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QContextMenuEvent, QEnterEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLineEdit, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QPlainTextEdit, QVBoxLayout, QWidget,
)

from features.workflow_pet.guidance import GuidanceMemory, guidance_for, startup_greeting
from ui.feature_navigation import FeatureNavigationBar
from ui.pixel_cat import ACTIONS, ACTION_NAMES, MENU_ACTIONS, paint_cat
from ui.workflow_pet import HorizontalShakeDetector, WorkflowPet


class PetWindow(QStackedWidget):
    def __init__(self):
        super().__init__()
        self.resize(960, 640)
        self.home_page = QWidget()
        self.addWidget(self.home_page)
        self.feature_pages = {}
        self.workflow_context = SimpleNamespace()
        self.opened = None
        self.open_feature("patent_ocr")
        self.setCurrentWidget(self.home_page)

    def open_feature(self, key):
        self.opened = key
        if key not in self.feature_pages:
            page = QWidget()
            layout = QVBoxLayout(page)
            page.feature_navigation = FeatureNavigationBar(lambda: None, key)
            layout.addWidget(page.feature_navigation)
            page.entry = QLineEdit()
            layout.addWidget(page.entry)
            layout.addStretch()
            page.run_button = QPushButton("辨識")
            page.auto_rotate_button = QPushButton("自動旋轉")
            layout.addWidget(page.auto_rotate_button)
            layout.addWidget(page.run_button)
            page.image_paths = []
            page.all_results = []
            page.reference_items = []
            page.document = None
            page.review = None
            page.review_table = QTableWidget(0, 1)
            self.feature_pages[key] = page
            self.addWidget(page)
        self.setCurrentWidget(self.feature_pages[key])


class WorkflowPetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.settings = QSettings(str(Path(self.directory.name)/"pet.ini"), QSettings.IniFormat)
        self.now = 100.0
        self.window = PetWindow()
        self.pet = WorkflowPet(self.window, settings=self.settings, clock=lambda: self.now,
                               wall_clock=lambda: datetime(2026, 9, 10, 9, 30))
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()
        # The headless platform need not own desktop focus.
        self.active = patch.object(self.pet, "_available", return_value=True)
        self.active.start()
        self.pet._sync_activity()

    def tearDown(self):
        self.pet.shutdown()
        self.active.stop()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def test_idle_waits_ten_seconds_and_input_resets_deadline(self):
        with patch.object(self.pet, "start_action") as start:
            self.now += 9.9
            self.pet._pulse()
            start.assert_not_called()
            self.now += 0.1
            self.pet._pulse()
            self.assertEqual(start.call_count, 1)
            self.now += 9
            self.pet.note_activity()
            self.now += 9
            self.pet._pulse()
            self.assertEqual(start.call_count, 1)
            self.now += 1
            self.pet._pulse()
            self.assertEqual(start.call_count, 2)

    def test_startup_greeting_uses_current_date_and_stays_readable(self):
        self.assertTrue(self.pet._startup_greeted)
        text = self.pet.bubble.message.text()
        self.assertTrue(text.startswith("早安"))
        self.assertIn("2026 年 9 月 10 日（星期四）", text)
        self.assertIn("09:30", text)
        self.assertTrue(text.endswith("我是小助手，有操作困難請找我"))
        self.now += 1
        self.pet._pulse()
        self.assertEqual(self.pet.bubble.message.text(), text)
        self.now += 8
        self.pet._pulse()
        self.assertEqual(self.pet._hint.key, "home")
        self.assertNotEqual(self.pet.bubble.message.text(), text)

    def test_startup_greeting_is_not_repeated_on_navigation_or_reactivation(self):
        greeting = self.pet.bubble.message.text()
        self.window.open_feature("patent_ocr")
        self.pet._pulse()
        self.assertEqual(self.pet._hint.key, "ocr-empty")
        with patch.object(self.pet, "_available", return_value=False):
            self.pet._sync_activity()
        self.pet._sync_activity()
        self.window.setCurrentWidget(self.window.home_page)
        self.pet._pulse()
        self.assertNotEqual(self.pet.bubble.message.text(), greeting)
        self.pet.dismiss()
        self.pet.recall_cat()
        self.assertNotEqual(self.pet.bubble.message.text(), greeting)

    def test_previously_dismissed_companion_does_not_force_a_startup_greeting(self):
        self.pet.shutdown()
        self.settings.setValue("dismissed", True)
        companion = WorkflowPet(self.window, settings=self.settings)
        try:
            with patch.object(companion, "_available", return_value=True):
                companion._sync_activity()
                self.assertTrue(companion._startup_greeted)
                self.assertFalse(companion.bubble.isVisible())
                self.assertFalse(companion.cat.isVisible())
                self.assertTrue(companion.recall.isVisible())
        finally:
            companion.shutdown()

    def test_two_second_gesture_does_not_reset_random_action_deadline(self):
        self.pet.stop_action()
        self.assertEqual(self.pet.ambient_timer.interval(), 2000)
        self.assertEqual(self.pet.ambient_timer.timerType(), Qt.PreciseTimer)
        self.assertTrue(self.pet.ambient_timer.isActive())
        origin = self.pet.cat.pos()
        deadline = self.pet._next_idle
        for _ in range(3):
            self.pet.ambient_timer.timeout.emit()
            self.assertEqual(self.pet.action, "idle_fidget")
            self.now += 0.25
            self.pet._animate()
            self.assertEqual(self.pet.frame, 3)
            self.assertEqual(self.pet.cat.pos(), origin)
            self.now += 0.4
            self.pet._animate()
            self.assertEqual(self.pet.action, "idle")
            self.assertFalse(self.pet.frame_timer.isActive())
            self.assertEqual(self.pet._next_idle, deadline)
            self.now += 1.35

    def test_small_gesture_keeps_running_while_user_types(self):
        self.window.open_feature("patent_ocr")
        page = self.window.currentWidget()
        page.entry.setFocus()
        self.pet.stop_action()
        self.pet._ambient_tick()
        QTest.keyClicks(page.entry, "21B")
        self.assertEqual(page.entry.text(), "21B")
        self.assertIs(self.app.focusWidget(), page.entry)
        self.assertEqual(self.pet.action, "idle_fidget")

    def test_special_actions_pause_ambient_timer_until_they_finish(self):
        for action, _ in ACTIONS:
            with self.subTest(action=action):
                self.pet.move_cat(QPoint(400, 350))
                self.pet.stop_action()
                self.pet._ambient_tick()
                self.pet.start_action(action)
                self.assertFalse(self.pet.ambient_timer.isActive())
                self.pet._sync_activity()
                self.assertFalse(self.pet.ambient_timer.isActive())
                self.pet._ambient_tick()
                self.assertEqual(self.pet.action, action)
                self.now += self.pet._action_duration + 0.1
                self.pet._animate()
                self.assertEqual(self.pet.action, "idle")
                self.assertTrue(self.pet.ambient_timer.isActive())
                self.assertEqual(self.pet.ambient_timer.interval(), 2000)

    def test_leaving_pet_and_moving_pointer_does_not_hide_speech(self):
        self.window.open_feature("patent_ocr")
        page = self.window.currentWidget()
        self.pet.ask_hint()
        text = self.pet.bubble.message.text()
        for target in (self.pet.cat, self.pet.bubble, page.entry, page):
            self.app.sendEvent(target, QEvent(QEvent.Leave))
            local = QPoint(10, 10)
            move = QMouseEvent(QEvent.MouseMove, QPointF(local),
                               QPointF(target.mapToGlobal(local)), Qt.NoButton,
                               Qt.NoButton, Qt.NoModifier)
            self.app.sendEvent(target, move)
            self.assertTrue(self.pet.bubble.isVisible())
            self.assertEqual(self.pet.bubble.message.text(), text)
            self.assertEqual(self.pet.action, "blink")
        # Clicking the bubble background also must not dismiss its contents.
        QTest.mouseClick(self.pet.bubble, Qt.LeftButton, pos=QPoint(5, 5))
        self.assertTrue(self.pet.bubble.isVisible())
        # A deliberate click back in the work area keeps the existing behavior.
        QTest.mouseClick(page.entry, Qt.LeftButton)
        self.assertFalse(self.pet.bubble.isVisible())

    def test_small_gestures_do_not_interrupt_actions_or_dragging(self):
        self.pet.start_action("eat")
        self.pet._ambient_tick()
        self.assertEqual(self.pet.action, "eat")
        self.pet.stop_action()
        self.pet.cat._press = QPoint(10, 10)
        self.pet._ambient_tick()
        self.pet.hover_cat()
        self.now += 20
        self.pet._pulse()
        self.assertEqual(self.pet.action, "idle")
        self.pet.cat._press = None
        with patch("ui.workflow_pet.QApplication.activeModalWidget", return_value=object()):
            self.pet._ambient_tick()
            self.pet.hover_cat()
            self.assertEqual(self.pet.action, "idle")

    def test_hover_greets_pointer_without_moving_cat_or_stealing_focus(self):
        self.window.open_feature("patent_ocr")
        entry = self.window.currentWidget().entry
        entry.setFocus()
        original = self.pet.cat.pos()
        local = QPointF(70, 60)
        event = QEnterEvent(local, QPointF(original)+local,
                            QPointF(self.pet.cat.mapToGlobal(local.toPoint())))
        self.app.sendEvent(self.pet.cat, event)
        self.assertEqual(self.pet.action, "hover")
        self.now += 0.5
        self.pet._animate()
        self.assertEqual(self.pet.cat.pos(), original)
        self.assertIs(self.app.focusWidget(), entry)
        self.now += 1
        self.pet._animate()
        self.assertEqual(self.pet.action, "idle")

    def test_two_second_sprite_blinks_once_and_swishes_tail(self):
        images = []
        for frame in range(8):
            sprite = QPixmap(48, 40)
            sprite.fill(Qt.transparent)
            painter = QPainter(sprite)
            paint_cat(painter, sprite.rect(), "idle_fidget", frame)
            painter.end()
            images.append(sprite.toImage())
        # Both eyes close together for a single contiguous part of the cycle.
        closed = [i for i, img in enumerate(images)
                  if img.pixelColor(22, 16).name() != "#ffffff"]
        self.assertEqual(closed, [2, 3])
        self.assertGreater(len({bytes(img.copy(0, 19, 14, 14).constBits())
                                for img in images}), 3)
        self.assertEqual(images[0], images[-1])
        # In the two-second cycle, only the eyes and tail may change pixels.
        for img in images[1:]:
            for y in range(40):
                for x in range(48):
                    tail = x < 14 and 19 <= y <= 32
                    eyes = (22 <= x <= 25 or 30 <= x <= 33) and 16 <= y <= 18
                    if not (tail or eyes):
                        self.assertEqual(img.pixel(x, y), images[0].pixel(x, y), (x, y))

    def test_every_action_animates_then_stops_and_all_positions_stay_inside(self):
        for action, _ in ACTIONS:
            with self.subTest(action=action):
                self.pet.move_cat(QPoint(700, 350))
                self.pet.start_action(action)
                self.now += 0.6
                self.pet._animate()
                self.assertGreater(self.pet.frame, 0)
                self.assertTrue(self.window.rect().contains(self.pet.cat.geometry()))
                self.now += self.pet._action_duration
                self.pet._animate()
                self.assertEqual(self.pet.action, "idle")
                self.assertFalse(self.pet.frame_timer.isActive())

    def test_drag_clamps_and_resize_never_changes_top_level_size(self):
        self.pet.move_cat(QPoint(-400, -400))
        self.assertGreaterEqual(self.pet.cat.x(), 0)
        self.assertGreaterEqual(self.pet.cat.y(), 0)
        self.pet.move_cat(QPoint(99999, 99999))
        self.assertTrue(self.window.rect().contains(self.pet.cat.geometry()))
        self.window.resize(800, 550)
        self.app.processEvents()
        self.pet._layout()
        self.assertEqual(self.window.width(), 800)
        self.assertEqual(self.window.height(), 550)
        self.assertTrue(self.window.rect().contains(self.pet.cat.geometry()))
        self.pet.ask_hint()
        self.assertTrue(self.window.rect().contains(self.pet.bubble.geometry()))
        self.assertLessEqual(self.pet.bubble.geometry().bottom(), self.pet.cat.y()+8)

    def test_dismiss_recall_persist_without_moving_page_or_instantiating_pages(self):
        original = self.window.geometry()
        page = self.window.currentWidget()
        pages = len(self.window.feature_pages)
        self.pet.dismiss()
        self.assertFalse(self.pet.cat.isVisible())
        self.assertTrue(self.pet.recall.isVisible())
        self.assertFalse(self.pet.pulse_timer.isActive())
        self.assertFalse(self.pet.ambient_timer.isActive())
        self.assertFalse(self.pet.frame_timer.isActive())
        self.assertTrue(self.settings.value("dismissed", type=bool))
        QTest.mouseClick(self.pet.recall, Qt.LeftButton)
        self.assertTrue(self.pet.cat.isVisible())
        self.assertFalse(self.pet.ambient_timer.isActive())  # Recall starts a blink action.
        self.now += self.pet._action_duration + 0.1
        self.pet._animate()
        self.assertTrue(self.pet.ambient_timer.isActive())
        self.assertFalse(self.pet.recall.isVisible())
        self.assertEqual(self.window.geometry(), original)
        self.assertIs(self.window.currentWidget(), page)
        self.assertEqual(len(self.window.feature_pages), pages)

    def test_typing_reaches_editor_and_stops_roaming_without_focus_stealing(self):
        self.window.open_feature("patent_ocr")
        page = self.window.currentWidget()
        page.entry.setFocus()
        self.pet.start_action("run")
        QTest.keyClicks(page.entry, "17A")
        self.assertEqual(page.entry.text(), "17A")
        self.assertIs(self.app.focusWidget(), page.entry)
        self.assertEqual(self.pet.action, "idle")
        self.pet.ask_hint()
        self.assertIs(self.app.focusWidget(), page.entry)

    def test_new_workflow_stages_change_hints_without_running_actions(self):
        self.window.open_feature("patent_ocr")
        page = self.window.currentWidget()
        self.pet._pulse()
        self.assertEqual(self.pet._hint.key, "ocr-empty")
        page.image_paths = ["first.png"]
        page.auto_rotate_button.setEnabled(True)
        self.pet._pulse()
        self.assertEqual(self.pet._hint.key, "ocr-ready")
        clicks = []
        page.auto_rotate_button.clicked.connect(lambda: clicks.append(True))
        self.pet.follow_hint()
        self.assertEqual(clicks, [])
        self.assertIs(self.app.focusWidget(), page.auto_rotate_button)
        page._ocr_job_active = True
        self.pet._pulse()
        self.assertEqual(self.pet._hint.key, "ocr-busy")
        page._ocr_job_active = False
        page.all_results = [{"image_path": "first.png"}]
        page.review_table.setRowCount(1)
        self.pet._pulse()
        self.assertEqual(self.pet._hint.key, "ocr-review")

        page.reference_items = ["1"]
        self.pet._pulse()
        self.assertEqual(self.pet._hint.key, "ocr-review")
        page.reference_text = QPlainTextEdit("1")
        self.pet._pulse()
        self.assertEqual(self.pet._hint.key, "ocr-compare-ready")
        page._guidance_comparison_snapshot = (page.all_results, page.reference_text.document().revision())
        self.pet._pulse()
        self.assertEqual(self.pet._hint.key, "ocr-compared")
        page.reference_items = []
        page.reference_text.clear()
        self.pet._pulse()
        self.assertEqual(self.pet._hint.key, "ocr-review")

    def test_local_save_report_uses_pet_bubble_without_page_status_row(self):
        self.window.open_feature("patent_ocr")
        page = self.window.currentWidget()
        self.pet.report_save_status(page, "圖式結果與人工修改已保存在本機")
        self.assertEqual(self.pet.bubble.caption.text(), "墨墨 · 本機保存")
        self.assertIn("已保存在本機", self.pet.bubble.message.text())

    def _review_page(self):
        self.window.open_feature("patent_review")
        page = self.window.currentWidget()
        page.document = object()
        page.review = SimpleNamespace(issues=[SimpleNamespace(
            rule_id="REF005", section_key="embodiments", details={"candidate": "第一位置"}) for _ in range(4)])
        page.issue_table = QTableWidget(4, 1)
        page.document_whitelist_input = QLineEdit()
        page.layout().addWidget(page.issue_table)
        page.layout().addWidget(page.document_whitelist_input)
        self.app.processEvents()
        return page

    def test_repeated_warning_two_choices_never_add_whitelist_or_resolve_issues(self):
        page = self._review_page()
        original = list(page.review.issues)
        self.pet.ask_hint()
        self.assertTrue(self.pet.bubble.alternative_button.isVisible())
        self.pet.follow_hint()
        self.assertIs(self.app.focusWidget(), page.document_whitelist_input)
        self.assertEqual(page.document_whitelist_input.text(), "")
        self.pet.ask_hint()
        self.pet.follow_hint(alternative=True)
        self.assertTrue(self.pet._guidance_memory.skip_repeated)
        self.pet.ask_hint()
        self.assertEqual(self.pet._hint.key, "review-issues")
        self.assertEqual(page.review.issues, original)
        self.pet.follow_hint(alternative=True)
        self.assertEqual(self.window.opened, "patent_ocr")
        self.assertEqual(page.review.issues, original)

    def test_new_document_same_hint_key_does_not_execute_stale_choice(self):
        page = self._review_page()
        self.pet.ask_hint()
        page.document = object()
        self.pet.follow_hint(alternative=True)
        self.assertFalse(self.pet._guidance_memory.skip_repeated)
        self.assertEqual(self.window.opened, "patent_review")
        self.assertTrue(self.pet.bubble.isVisible())

    def test_long_two_choice_bubble_fits_without_resizing_work_window(self):
        from app.styles import APP_STYLE
        self.window.setStyleSheet(APP_STYLE)
        self._review_page()
        for width, height in ((800, 550), (960, 640), (1280, 720)):
            self.window.resize(width, height)
            self.app.processEvents()
            before = self.window.size()
            self.pet.ask_hint()
            self.assertEqual(before, self.window.size())
            self.assertTrue(self.window.rect().contains(self.pet.bubble.geometry()))
            self.assertTrue(self.pet.bubble.rect().contains(self.pet.bubble.alternative_button.geometry()))
            for button in (self.pet.bubble.next_button, self.pet.bubble.alternative_button):
                self.assertGreaterEqual(button.height(), button.sizeHint().height())

    def test_workflow_speech_actions_cleared_for_animation_speech(self):
        self._review_page()
        self.pet.ask_hint()
        self.assertTrue(self.pet.bubble.alternative_button.isVisible())
        self.pet._show_speech("喵～")
        self.assertFalse(self.pet.bubble.alternative_button.isVisible())
        self.assertFalse(self.pet.bubble.next_button.isVisible())

    def test_issue_click_is_observation_not_approval_and_auto_selection_is_not_counted(self):
        page = self._review_page()
        item = QTableWidgetItem("相似詞警告")
        item.setData(Qt.UserRole, "finding-1")
        page.issue_table.setItem(0, 0, item)
        self.pet._attached_pages.remove(page)
        self.pet._attach_page(page)
        page.issue_table.selectRow(0)
        self.pet.ask_hint()
        self.assertFalse(self.pet._guidance_memory.visited)
        page.issue_table.itemClicked.emit(item)
        self.assertEqual(self.pet._guidance_memory.visited, {"finding-1"})
        self.assertEqual(len(page.review.issues), 4)

    def test_recall_slot_is_reserved_once_and_does_not_move_on_dismissal(self):
        self.window.open_feature("patent_ocr")
        page = self.window.currentWidget()
        nav = page.feature_navigation
        margin = nav.navigation_layout.contentsMargins().right()
        self.assertGreaterEqual(margin, 44)
        self.window.setCurrentWidget(self.window.home_page)
        self.window.setCurrentWidget(page)
        self.pet.dismiss()
        self.assertEqual(nav.navigation_layout.contentsMargins().right(), margin)
        self.pet.recall_cat()
        self.assertEqual(nav.navigation_layout.contentsMargins().right(), margin)

    def test_hidden_or_inactive_window_stops_timers(self):
        self.pet.start_action("walk")
        with patch.object(self.pet, "_available", return_value=False):
            self.pet._sync_activity()
            self.assertFalse(self.pet.frame_timer.isActive())
            self.assertFalse(self.pet.pulse_timer.isActive())
            self.assertFalse(self.pet.ambient_timer.isActive())
            self.assertFalse(self.pet.bubble.isVisible())

    def test_shutdown_stops_all_animation_scheduling(self):
        self.pet._ambient_tick()
        self.pet.shutdown()
        for timer in (self.pet.frame_timer, self.pet.pulse_timer, self.pet.ambient_timer):
            self.assertFalse(timer.isActive())
        self.pet._ambient_tick()
        self.pet.hover_cat()
        self.assertFalse(self.pet.frame_timer.isActive())

    def test_mouse_drag_releases_at_clamped_location_and_right_menu_can_dismiss(self):
        cat = self.pet.cat
        origin = cat.pos()
        local = QPoint(70, 60)
        global_start = cat.mapToGlobal(local)
        QTest.mousePress(cat, Qt.LeftButton, pos=local)
        move = QMouseEvent(
            QEvent.MouseMove, QPointF(250, 160),
            QPointF(global_start + QPoint(180, 100)),
            Qt.NoButton, Qt.LeftButton, Qt.NoModifier,
        )
        self.app.sendEvent(cat, move)
        QTest.mouseRelease(cat, Qt.LeftButton, pos=local)
        self.assertNotEqual(cat.pos(), origin)
        self.assertTrue(self.window.rect().contains(cat.geometry()))
        event = QContextMenuEvent(QContextMenuEvent.Mouse, local, cat.mapToGlobal(local))
        self.app.sendEvent(cat, event)
        menu = self.pet._menu
        self.assertIsNotNone(menu)
        actions = [action for action in menu.actions() if action.text().startswith("趕走")]
        self.assertEqual(len(actions), 1)
        menu.close()
        actions[0].trigger()
        self.assertTrue(self.pet.dismissed)
        self.assertTrue(self.pet.recall.isVisible())

    def test_clean_package_template_includes_pet_without_private_features(self):
        import sys
        from tools import build_company_update as common
        with patch.dict(sys.modules, {"build_company_update": common}):
            from tools import build_clean_company_update as clean
        self.assertIn("self.workflow_pet = WorkflowPet(self)", clean.CLEAN_MAIN_WINDOW)
        self.assertIn("self.workflow_pet.shutdown()", clean.CLEAN_MAIN_WINDOW)
        root = Path(__file__).resolve().parents[1]
        source = (root/"ui/workflow_pet.py").read_text(encoding="utf-8")
        source += (root/"features/workflow_pet/guidance.py").read_text(encoding="utf-8")
        for token in clean.STANDARD_FORBIDDEN_TERMS:
            self.assertNotIn(token, source)

    def test_sprite_actions_have_different_pixel_frames(self):
        signatures = set()
        for action, _ in ACTIONS:
            sprite = QPixmap(144, 120)
            sprite.fill(Qt.transparent)
            painter = QPainter(sprite)
            paint_cat(painter, sprite.rect(), action, 3)
            painter.end()
            image = sprite.toImage()
            signatures.add(bytes(image.constBits()))
        self.assertGreaterEqual(len(signatures), 9)

    def test_walk_and_run_face_right_and_turn_inward_when_started_at_edge(self):
        for action in ("walk", "run"):
            self.pet.move_cat(QPoint(700, 350))
            self.pet.start_action(action)
            self.assertEqual(self.pet.facing, 1)
            self.now += 0.5
            self.pet._animate()
            self.assertGreater(self.pet.cat.x(), 700)
            self.pet.move_cat(QPoint(99999, 350))
            self.pet.start_action(action)
            edge = self.pet.cat.x()
            self.now += 0.1
            self.pet._animate()
            self.assertEqual(self.pet.action, action + "_left")
            self.assertEqual(self.pet.facing, -1)
            self.assertLess(self.pet.cat.x(), edge)
            self.assertTrue(self.window.rect().contains(self.pet.cat.geometry()))

    def test_menu_has_only_three_pet_actions_but_keeps_hint_and_dismiss(self):
        self.pet.open_menu(self.pet.cat.mapToGlobal(QPoint(40, 40)))
        menu = self.pet._menu
        labels = [item.text() for item in menu.actions()]
        self.assertEqual([label for label in labels if label in ACTION_NAMES.values()],
                         ["吃飯", "喝水", "玩毛線球"])
        self.assertEqual(tuple(MENU_ACTIONS),
                         (("eat", "吃飯"), ("drink", "喝水"), ("yarn", "玩毛線球")))
        self.assertIn("現在該做什麼？", labels)
        self.assertTrue(any(label.startswith("趕走") for label in labels))
        menu.close()

    def test_left_walk_run_move_left_and_cannot_cross_window_boundary(self):
        for action in ("walk_left", "run_left"):
            with self.subTest(action=action):
                self.pet.move_cat(QPoint(100, 350))
                self.pet.start_action(action)
                self.assertEqual(self.pet.facing, -1)
                self.now += 0.5
                self.pet._animate()
                self.assertLess(self.pet.cat.x(), 100)
                self.assertTrue(self.window.rect().contains(self.pet.cat.geometry()))
                self.now += 3.4 if action == "walk_left" else 1.5
                self.pet._animate()
                self.assertEqual(self.pet.action, "idle")
                self.assertEqual(self.pet.cat.x(), 0)
                self.pet.start_action(action)
                self.assertEqual(self.pet.facing, 1)

    def test_hover_and_idle_deadline_cannot_interrupt_full_action(self):
        self.pet.start_action("sleep")
        self.pet.hover_cat()
        self.assertEqual(self.pet.action, "sleep")
        self.pet._next_idle = self.now-1
        self.pet._last_input = self.now-20
        self.pet._greeting_until = 0
        self.pet._pulse()
        self.assertEqual(self.pet.action, "sleep")
        self.assertFalse(self.pet.ambient_timer.isActive())

    def test_new_actions_are_random_candidates_but_dizziness_is_not(self):
        options = dict(ACTIONS)
        for action in ("stretch", "yawn", "knead", "pounce", "chase_tail",
                       "walk_left", "run_left"):
            self.assertIn(action, options)
        self.assertNotIn("dizzy", options)
        self.pet._greeting_until = 0
        self.now += 11
        self.pet.stop_action()
        with patch.object(self.pet.random, "choice", return_value="knead") as choose:
            self.pet._pulse()
            self.assertIn("knead", choose.call_args.args[0])
            self.assertNotIn("dizzy", choose.call_args.args[0])
            self.assertEqual(self.pet.action, "knead")

    def test_fast_mouse_drag_triggers_dizziness_once_without_position_snapback(self):
        cat = self.pet.cat
        self.pet.move_cat(QPoint(300, 350))
        local = QPoint(70, 60)
        global_start = cat.mapToGlobal(local)
        QTest.mousePress(cat, Qt.LeftButton, pos=local)
        for dx in (35, -35, 35, -35):
            self.now += 0.1
            point = global_start+QPoint(dx, 0)
            self.app.sendEvent(cat, QMouseEvent(
                QEvent.MouseMove, QPointF(cat.mapFromGlobal(point)), QPointF(point),
                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        self.assertEqual(self.pet.action, "dizzy")
        started = self.pet._action_start
        self.assertFalse(self.pet.ambient_timer.isActive())
        self.pet._ambient_tick()
        self.pet.hover_cat()
        self.assertEqual(self.pet.action, "dizzy")
        self.now += 0.1
        point = global_start+QPoint(80, 0)
        self.app.sendEvent(cat, QMouseEvent(
            QEvent.MouseMove, QPointF(cat.mapFromGlobal(point)), QPointF(point),
            Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        moved = cat.pos()
        self.pet._animate()
        self.assertEqual(cat.pos(), moved)
        self.assertEqual(self.pet._action_start, started)
        QTest.mouseRelease(cat, Qt.LeftButton, pos=local)
        self.assertIsNone(cat._press)
        self.assertEqual(self.pet.action, "dizzy")
        self.now += 2.0
        self.pet._animate()
        self.assertGreaterEqual(self.pet.frame, 24)
        self.assertEqual(cat.pos(), moved)
        self.now += 3
        self.pet._animate()
        self.assertEqual(self.pet.action, "idle")
        self.assertTrue(self.pet.ambient_timer.isActive())
        self.assertTrue(self.window.rect().contains(cat.geometry()))

    def test_shake_cooldown_and_lost_mouse_grab_are_safe(self):
        self.pet.cat._press = QPoint(10, 10)
        self.pet.shake_cat()
        self.assertEqual(self.pet.action, "dizzy")
        self.pet.stop_action()
        self.pet.shake_cat()
        self.assertEqual(self.pet.action, "idle")
        self.app.sendEvent(self.pet.cat, QEvent(QEvent.UngrabMouse))
        self.assertIsNone(self.pet.cat._press)
        self.now += 6.1
        self.pet.shake_cat()
        self.assertEqual(self.pet.action, "idle")  # Must still hold the left button.
        self.pet.cat._press = QPoint(10, 10)
        self.pet.shake_cat()
        self.assertEqual(self.pet.action, "dizzy")
        with patch.object(self.pet, "_available", return_value=False):
            self.pet._sync_activity()
            self.assertIsNone(self.pet.cat._press)
            self.assertFalse(self.pet.frame_timer.isActive())

    def test_new_sprite_actions_animate_and_dizziness_spits_out_yarn_later(self):
        def render(action, frame, facing=1):
            sprite = QPixmap(144, 120)
            sprite.fill(Qt.transparent)
            painter = QPainter(sprite)
            paint_cat(painter, sprite.rect(), action, frame, facing)
            painter.end()
            return sprite.toImage()

        for action in ("stretch", "yawn", "knead", "pounce", "chase_tail", "dizzy"):
            frames = {bytes(render(action, frame).constBits()) for frame in (0, 3, 12, 30, 52)}
            self.assertGreater(len(frames), 1, action)
        def count_yarn(image):
            return sum(image.pixelColor(x, y).name() == "#bf83d9" and image.pixelColor(x, y).alpha() > 0
                       for x in range(image.width()) for y in range(image.height()))
        self.assertEqual(count_yarn(render("dizzy", 3)), 0)
        for facing in (-1, 1):
            self.assertGreater(count_yarn(render("dizzy", 42, facing)), 0)
        for action in ("walk", "run"):
            right = render(action, 3)
            left = render(action+"_left", 3)
            self.assertNotEqual(bytes(right.constBits()), bytes(left.constBits()))

    def test_side_profile_has_a_short_single_pixel_nose(self):
        for action in ("walk", "run"):
            for frame in range(8):
                with self.subTest(action=action, frame=frame):
                    sprite = QPixmap(48, 40)
                    sprite.fill(Qt.transparent)
                    painter = QPainter(sprite)
                    paint_cat(painter, sprite.rect(), action, frame)
                    painter.end()
                    image = sprite.toImage()
                    y = 21 + frame % 2
                    self.assertEqual(image.pixelColor(39, y).name(), "#f0a1b4")
                    self.assertNotEqual(image.pixelColor(39, y+1).name(), "#f0a1b4")
                    for x in range(41, 45):
                        for row in range(y-1, y+5):
                            self.assertEqual(image.pixelColor(x, row).alpha(), 0)

    def test_angry_sleep_and_belly_have_distinct_required_details(self):
        def render(action, frame=0):
            sprite = QPixmap(48, 40)
            sprite.fill(Qt.transparent)
            painter = QPainter(sprite)
            paint_cat(painter, sprite.rect(), action, frame)
            painter.end()
            return sprite.toImage()

        angry, sleep, roll = render("angry"), render("sleep"), render("roll")
        self.assertEqual(angry.pixelColor(8, 3).name(), "#e64b55")
        self.assertEqual(sleep.pixelColor(28, 17).name(), "#6d91b3")
        self.assertEqual(sleep.pixelColor(8, 22).alpha(), 0)
        self.assertEqual(roll.pixelColor(20, 20).name(), "#8792a2")
        self.assertIn(("angry", "生氣"), ACTIONS)
        self.assertIn(("sleep", "趴著睡覺"), ACTIONS)
        for action in ("blink", "groom", "meow", "angry", "jump", "hover"):
            with self.subTest(action=action):
                paw = render(action, 3)
                paw_y = 19 - (1 if action == "hover" else 2 if action == "jump" else 0)
                self.assertEqual(paw.pixelColor(34, paw_y).name(), "#f0a1b4")


class ShakeDetectorTests(unittest.TestCase):
    def test_four_quick_alternating_strokes_trigger_only_once(self):
        detector = HorizontalShakeDetector()
        detector.reset(QPoint(100, 100), 0)
        results = [detector.sample(QPoint(x, 100), (i+1)*0.1)
                   for i, x in enumerate((135, 65, 135, 65, 135, 65))]
        self.assertEqual(results, [False, False, False, True, False, False])

    def test_jitter_slow_reversals_vertical_and_one_way_drag_do_not_trigger(self):
        cases = (
            ([(100+x, 100) for x in (2, -2, 3, -3, 2, -2)], 0.1),
            ([(x, 100) for x in (135, 65, 135, 65, 135, 65)], 0.7),
            ([(100, y) for y in (135, 65, 135, 65, 135, 65)], 0.1),
            ([(x, 100) for x in (140, 180, 220, 260, 300, 340)], 0.1),
        )
        for points, interval in cases:
            detector = HorizontalShakeDetector()
            detector.reset(QPoint(100, 100), 0)
            self.assertFalse(any(detector.sample(QPoint(*point), (i+1)*interval)
                                 for i, point in enumerate(points)))

    def test_drag_strokes_do_not_carry_over_to_next_press(self):
        detector = HorizontalShakeDetector()
        detector.reset(QPoint(100, 100), 0)
        for i, x in enumerate((135, 65, 135)):
            self.assertFalse(detector.sample(QPoint(x, 100), (i+1)*0.1))
        detector.reset(QPoint(100, 100), 0.35)
        self.assertFalse(detector.sample(QPoint(65, 100), 0.4))


class PetGuidanceTests(unittest.TestCase):
    def test_greeting_time_boundaries_use_local_date_and_24_hour_clock(self):
        for hour, expected in ((0, "晚安"), (4, "晚安"), (5, "早安"), (11, "早安"),
                               (12, "午安"), (17, "午安"), (18, "晚安"), (23, "晚安")):
            with self.subTest(hour=hour):
                text = startup_greeting(datetime(2028, 2, 29, hour, 7))
                self.assertTrue(text.startswith(expected))
                self.assertIn("2028 年 2 月 29 日（星期二）", text)
                self.assertIn(f"{hour:02d}:07", text)
                self.assertTrue(text.endswith("我是小助手，有操作困難請找我"))

    def test_review_errors_do_not_claim_document_is_correct(self):
        page = SimpleNamespace(document=object(), review=SimpleNamespace(issues=[1, 2]))
        hint = guidance_for("patent_review", page)
        self.assertIn("2", hint.text)
        self.assertIn("Word", hint.text)
        self.assertEqual(hint.target, "issue_table")

    def test_comparison_prerequisites_and_conversion_preservation(self):
        page = SimpleNamespace(document=None, ocr_results=[])
        self.assertEqual(guidance_for("embodiment_figure_compare", page).feature,
                         "patent_review")
        page.document = object()
        self.assertEqual(guidance_for("embodiment_figure_compare", page).feature,
                         "patent_ocr")
        page._preview_source = "some text"
        self.assertIn("保留完整", guidance_for("taiwan_china_spec", page).text)


if __name__ == "__main__":
    unittest.main()
