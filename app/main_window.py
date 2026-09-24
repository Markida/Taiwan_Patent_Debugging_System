from PySide6.QtCore import QEvent, QSize, QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSizePolicy, QStackedWidget

from app.config import APP_NAME
from app.paths import get_app_icon_path
from app.styles import APP_STYLE
from ui.home_page import HomePage
from ui.file_drop import RoutedPatentFileDropController
from features.registry import FEATURES
from app.workflow_context import PatentWorkflowContext


class LazyFeaturePageMap(dict):
    """Keep the existing mapping API while constructing pages on first use."""

    def __init__(self, feature_ids, factory):
        super().__init__()
        self._feature_ids = frozenset(feature_ids)
        self._factory = factory

    def __getitem__(self, feature_id):
        if not dict.__contains__(self, feature_id):
            if feature_id not in self._feature_ids:
                raise KeyError(feature_id)
            self._factory(feature_id)
        return dict.__getitem__(self, feature_id)

    def get(self, feature_id, default=None):
        if feature_id not in self._feature_ids:
            return default
        return self[feature_id]

    def peek(self, feature_id):
        """Return an already-created page without triggering construction."""

        return dict.get(self, feature_id)

    def store(self, feature_id, page):
        dict.__setitem__(self, feature_id, page)


class MainWindow(QStackedWidget):
    DEFAULT_WINDOW_SIZE = QSize(1360, 860)
    MINIMUM_WINDOW_SIZE = QSize(960, 640)

    def __init__(self, startup_progress_callback=None):
        super().__init__()
        self._startup_progress_callback = startup_progress_callback

        self._report_startup("正在準備主視窗")
        self.setWindowTitle(APP_NAME)
        icon_path = get_app_icon_path()
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(self.MINIMUM_WINDOW_SIZE)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._resize_to_available_desktop()

        self._feature_definitions = {
            feature["id"]: feature for feature in FEATURES
        }
        self.feature_pages = LazyFeaturePageMap(
            self._feature_definitions,
            self._create_feature_page,
        )
        self._return_work_feature_id = None
        self._chat_unread_count = 0
        self.workflow_context = PatentWorkflowContext()

        self.home_page = HomePage(
            features=FEATURES,
            open_feature_callback=self.open_feature,
            open_secret_callback=self.open_chat_room,
        )

        self._add_stable_page(self.home_page)
        # The chat page remains active so unread monitoring works on every
        # formal page.  The game pages are genuinely lazy: they are not
        # imported or constructed until the hidden entrance is used.
        self._report_startup("正在載入聊天通知")
        from ui.chat_room_page import ChatRoomPage

        self._snake_page = None
        self._pong_page = None
        self._tetris_page = None
        self._tank_page = None
        self._bulls_cows_page = None
        self.chat_room_page = ChatRoomPage(
            go_home_callback=self.go_home,
            open_snake_callback=self.open_snake_game,
            return_work_callback=self.return_to_work_progress,
            open_pong_callback=self.open_pong_game,
            open_tetris_callback=self.open_tetris_game,
            open_tank_callback=self.open_tank_battle,
            open_bulls_cows_callback=self.open_bulls_and_cows,
            join_game_invitation_callback=self.join_game_invitation,
        )
        self._add_stable_page(self.chat_room_page)
        self.chat_room_page.unread_count_changed.connect(
            self._update_chat_unread_badges
        )

        for feature in FEATURES:
            # Keep progress feedback, but defer the expensive module import
            # and QWidget construction until this feature is first opened.
            self._report_startup(f"正在載入{feature['title']}")

        self.chat_room_page.start_background_monitoring()

        self._report_startup("正在完成介面設定")
        self.setCurrentWidget(self.home_page)
        self.setStyleSheet(APP_STYLE)
        self.routed_file_drop_controller = RoutedPatentFileDropController(
            self,
            on_docx=self.route_docx_file,
            on_pdf=self.route_pdf_file,
        )
        self.routed_file_drop_controller.register_widget_tree(self.home_page)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        from ui.workflow_pet import WorkflowPet
        self.workflow_pet = WorkflowPet(self)
        self._report_startup("主程式準備完成")

    def sizeHint(self):
        """Keep the top-level hint independent from the current lazy page."""

        return QSize(self.DEFAULT_WINDOW_SIZE)

    def minimumSizeHint(self):
        """Allow 1366x768 office displays without hiding the bottom panels."""

        return QSize(self.MINIMUM_WINDOW_SIZE)

    def _resize_to_available_desktop(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(self.DEFAULT_WINDOW_SIZE)
            return
        available = screen.availableGeometry()
        self.setMinimumSize(
            min(self.MINIMUM_WINDOW_SIZE.width(), available.width()),
            min(self.MINIMUM_WINDOW_SIZE.height(), available.height()),
        )
        horizontal_margin = min(32, max(0, available.width() // 20))
        vertical_margin = min(32, max(0, available.height() // 20))
        target_width = min(
            self.DEFAULT_WINDOW_SIZE.width(),
            max(self.MINIMUM_WINDOW_SIZE.width(), available.width() - horizontal_margin),
        )
        target_height = min(
            self.DEFAULT_WINDOW_SIZE.height(),
            max(self.MINIMUM_WINDOW_SIZE.height(), available.height() - vertical_margin),
        )
        # A display can be smaller than the preferred minimum under remote
        # desktop or aggressive Windows scaling.  Never request more physical
        # desktop space than is actually available.
        target_width = min(target_width, available.width())
        target_height = min(target_height, available.height())
        self.resize(target_width, target_height)
        self.move(
            available.center().x() - target_width // 2,
            available.center().y() - target_height // 2,
        )

    def _bounded_normal_geometry(self, geometry):
        screen = QApplication.screenAt(geometry.center()) or QApplication.primaryScreen()
        if screen is None:
            return geometry
        available = screen.availableGeometry()
        width = min(max(1, geometry.width()), available.width())
        height = min(max(1, geometry.height()), available.height())
        x = min(max(geometry.x(), available.left()), available.right() - width + 1)
        y = min(max(geometry.y(), available.top()), available.bottom() - height + 1)
        bounded = geometry
        bounded.setRect(x, y, width, height)
        return bounded

    def _restore_normal_geometry(self, geometry, expected_widget):
        if (
            self.currentWidget() is expected_widget
            and not self.isMaximized()
            and not self.isFullScreen()
        ):
            self.setGeometry(self._bounded_normal_geometry(geometry))

    def setCurrentWidget(self, widget):
        """Switch pages without allowing their size hints to move the window."""

        preserve_geometry = (
            self.isVisible() and not self.isMaximized() and not self.isFullScreen()
        )
        geometry = self.geometry() if preserve_geometry else None
        result = super().setCurrentWidget(widget)
        if geometry is not None:
            bounded = self._bounded_normal_geometry(geometry)
            self.setGeometry(bounded)
            # Some nested widgets deliver a delayed LayoutRequest after their
            # tables/images are populated.  Restore once more after that event
            # cycle so the active page cannot resize or move the top window.
            QTimer.singleShot(
                0,
                lambda wanted=widget, saved=bounded: self._restore_normal_geometry(
                    saved,
                    wanted,
                ),
            )
        return result

    def _create_feature_page(self, feature_id):
        page = self.feature_pages.peek(feature_id)
        if page is not None:
            return page
        feature = self._feature_definitions.get(feature_id)
        if feature is None:
            raise KeyError(feature_id)

        page = feature["page_class"](go_home_callback=self.go_home)
        save_report = getattr(page, "result_save_report", None)
        if save_report is not None and hasattr(self, "workflow_pet"):
            save_report.connect(
                lambda message, owner=page: self.workflow_pet.report_save_status(owner, message)
            )
        set_workflow_context = getattr(page, "set_workflow_context", None)
        if callable(set_workflow_context):
            set_workflow_context(self.workflow_context)
        set_open_feature_callback = getattr(
            page, "set_open_feature_callback", None
        )
        if callable(set_open_feature_callback):
            set_open_feature_callback(self.open_feature)
        set_feature_navigation = getattr(page, "set_feature_navigation", None)
        if callable(set_feature_navigation):
            set_feature_navigation(FEATURES, self.open_feature)
            page.feature_navigation.set_chat_unread_handler(
                lambda wanted=feature_id: self.open_chat_from_work(wanted)
            )
            page.feature_navigation.set_unread_count(self._chat_unread_count)

        self.feature_pages.store(feature_id, page)
        self._add_stable_page(page)
        if (
            hasattr(self, "routed_file_drop_controller")
            and not getattr(page, "uses_isolated_file_drop", False)
        ):
            self.routed_file_drop_controller.register_widget_tree(page)
        return page

    def _add_stable_page(self, page):
        """Add a page without letting its size hint resize the top window."""

        page.setMinimumSize(0, 0)
        page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.addWidget(page)
        return page

    @property
    def snake_page(self):
        return self._ensure_snake_page()

    def _ensure_snake_page(self):
        if self._snake_page is None:
            from ui.snake_game_page import SnakeGamePage

            self._snake_page = SnakeGamePage(
                go_home_callback=self.go_home,
                open_chat_callback=self.open_chat_room,
                open_pong_callback=self.open_pong_game,
                open_tetris_callback=self.open_tetris_game,
                open_tank_callback=self.open_tank_battle,
                open_bulls_cows_callback=self.open_bulls_and_cows,
                return_work_callback=self.return_to_work_progress,
            )
            self._snake_page.set_return_work_available(
                self._return_work_feature_id in self._feature_definitions
            )
            self._add_stable_page(self._snake_page)
        return self._snake_page

    @property
    def pong_page(self):
        return self._ensure_pong_page()

    def _ensure_pong_page(self):
        if self._pong_page is None:
            from ui.pong_game_page import PongGamePage

            self._pong_page = PongGamePage(
                go_home_callback=self.go_home,
                open_chat_callback=self.open_chat_room,
                open_snake_callback=self.open_snake_game,
                open_tetris_callback=self.open_tetris_game,
                open_tank_callback=self.open_tank_battle,
                open_bulls_cows_callback=self.open_bulls_and_cows,
                return_work_callback=self.return_to_work_progress,
            )
            self._pong_page.set_return_work_available(
                self._return_work_feature_id in self._feature_definitions
            )
            self._add_stable_page(self._pong_page)
        return self._pong_page

    @property
    def tetris_page(self):
        return self._ensure_tetris_page()

    def _ensure_tetris_page(self):
        if self._tetris_page is None:
            from ui.tetris_game_page import TetrisGamePage

            self._tetris_page = TetrisGamePage(
                go_home_callback=self.go_home,
                open_chat_callback=self.open_chat_room,
                open_pong_callback=self.open_pong_game,
                open_snake_callback=self.open_snake_game,
                open_tank_callback=self.open_tank_battle,
                open_bulls_cows_callback=self.open_bulls_and_cows,
                return_work_callback=self.return_to_work_progress,
            )
            self._tetris_page.set_return_work_available(
                self._return_work_feature_id in self._feature_definitions
            )
            self._add_stable_page(self._tetris_page)
        return self._tetris_page

    @property
    def tank_page(self):
        return self._ensure_tank_page()

    def _ensure_tank_page(self):
        if self._tank_page is None:
            from ui.tank_battle_page import TankBattlePage

            self._tank_page = TankBattlePage(
                go_home_callback=self.go_home,
                open_chat_callback=self.open_chat_room,
                open_pong_callback=self.open_pong_game,
                open_tetris_callback=self.open_tetris_game,
                open_snake_callback=self.open_snake_game,
                open_bulls_cows_callback=self.open_bulls_and_cows,
                return_work_callback=self.return_to_work_progress,
            )
            self._tank_page.set_return_work_available(
                self._return_work_feature_id in self._feature_definitions
            )
            self._add_stable_page(self._tank_page)
        return self._tank_page

    @property
    def bulls_cows_page(self):
        return self._ensure_bulls_cows_page()

    def _ensure_bulls_cows_page(self):
        if self._bulls_cows_page is None:
            from ui.bulls_and_cows_page import BullsAndCowsPage

            self._bulls_cows_page = BullsAndCowsPage(
                go_home_callback=self.go_home,
                open_chat_callback=self.open_chat_room,
                open_pong_callback=self.open_pong_game,
                open_tetris_callback=self.open_tetris_game,
                open_tank_callback=self.open_tank_battle,
                open_snake_callback=self.open_snake_game,
                return_work_callback=self.return_to_work_progress,
            )
            self._bulls_cows_page.set_return_work_available(
                self._return_work_feature_id in self._feature_definitions
            )
            self._add_stable_page(self._bulls_cows_page)
        return self._bulls_cows_page

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.KeyPress
            and event.key() == Qt.Key_Escape
            and self.currentWidget() is not self.home_page
        ):
            self.emergency_go_home()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _report_startup(self, message):
        if self._startup_progress_callback is not None:
            self._startup_progress_callback(message)

    def open_feature(self, feature_id):
        page = self.feature_pages.get(feature_id)

        if page:
            if self._snake_page is not None and self.currentWidget() is self._snake_page:
                self._snake_page.abort_current_workflow()
            if self.currentWidget() is self.chat_room_page:
                self.chat_room_page.deactivate()
            if self._pong_page is not None and self.currentWidget() is self._pong_page:
                self._pong_page.shutdown_match()
            if self._tetris_page is not None and self.currentWidget() is self._tetris_page:
                self._tetris_page.shutdown_match()
            if self._tank_page is not None and self.currentWidget() is self._tank_page:
                self._tank_page.shutdown_match()
            if self._bulls_cows_page is not None and self.currentWidget() is self._bulls_cows_page:
                self._bulls_cows_page.abort_current_workflow()
            self.setCurrentWidget(page)

    def route_docx_file(self, path):
        page = self.feature_pages["patent_review"]
        self.open_feature("patent_review")
        return page.load_document(path)

    def route_pdf_file(self, path):
        page = self.feature_pages["patent_ocr"]
        self.open_feature("patent_ocr")
        return page.import_pdf(path)

    def go_home(self):
        if self._snake_page is not None and self.currentWidget() is self._snake_page:
            self._snake_page.abort_current_workflow()
        if self.currentWidget() is self.chat_room_page:
            self.chat_room_page.deactivate()
        if self._pong_page is not None and self.currentWidget() is self._pong_page:
            self._pong_page.shutdown_match()
        if self._tetris_page is not None and self.currentWidget() is self._tetris_page:
            self._tetris_page.shutdown_match()
        if self._tank_page is not None and self.currentWidget() is self._tank_page:
            self._tank_page.shutdown_match()
        if self._snake_page is not None and self.currentWidget() is self._snake_page:
            self._snake_page.abort_current_workflow()
        if self._bulls_cows_page is not None and self.currentWidget() is self._bulls_cows_page:
            self._bulls_cows_page.abort_current_workflow()
        self.setCurrentWidget(self.home_page)

    def emergency_go_home(self):
        modal = QApplication.activeModalWidget()
        if modal is not None:
            modal.close()
        popup = QApplication.activePopupWidget()
        if popup is not None:
            popup.close()
        current = self.currentWidget()
        abort = getattr(current, "abort_current_workflow", None)
        if callable(abort):
            abort()
        self.go_home()

    def _update_chat_unread_badges(self, count):
        self._chat_unread_count = max(0, int(count))
        for page in self.feature_pages.values():
            page.feature_navigation.set_unread_count(self._chat_unread_count)

    def open_chat_from_work(self, feature_id):
        if feature_id in self.feature_pages:
            self._return_work_feature_id = feature_id
        self._sync_return_work_buttons()
        self.open_chat_room()

    def return_to_work_progress(self):
        feature_id = self._return_work_feature_id
        if feature_id not in self._feature_definitions:
            self.go_home()
            return
        self.open_feature(feature_id)

    def _sync_return_work_buttons(self):
        available = self._return_work_feature_id in self._feature_definitions
        self.chat_room_page.set_return_work_available(available)
        if self._snake_page is not None:
            self._snake_page.set_return_work_available(available)
        if self._pong_page is not None:
            self._pong_page.set_return_work_available(available)
        if self._tetris_page is not None:
            self._tetris_page.set_return_work_available(available)
        if self._tank_page is not None:
            self._tank_page.set_return_work_available(available)
        if self._bulls_cows_page is not None:
            self._bulls_cows_page.set_return_work_available(available)

    def open_snake_game(self):
        self.chat_room_page.deactivate()
        if self._pong_page is not None and self.currentWidget() is self._pong_page:
            self._pong_page.shutdown_match()
        if self._tetris_page is not None and self.currentWidget() is self._tetris_page:
            self._tetris_page.shutdown_match()
        if self._tank_page is not None and self.currentWidget() is self._tank_page:
            self._tank_page.shutdown_match()
        if self._bulls_cows_page is not None and self.currentWidget() is self._bulls_cows_page:
            self._bulls_cows_page.abort_current_workflow()
        page = self._ensure_snake_page()
        page.prepare_game()
        self.setCurrentWidget(page)

    def open_chat_room(self):
        if self._snake_page is not None and self.currentWidget() is self._snake_page:
            self._snake_page.abort_current_workflow()
        if self._bulls_cows_page is not None and self.currentWidget() is self._bulls_cows_page:
            self._bulls_cows_page.abort_current_workflow()
        self.setCurrentWidget(self.chat_room_page)
        self.chat_room_page.prepare_chat()

    def open_pong_game(self):
        self.chat_room_page.deactivate()
        if self._snake_page is not None and self.currentWidget() is self._snake_page:
            self._snake_page.abort_current_workflow()
        if self._tetris_page is not None and self.currentWidget() is self._tetris_page:
            self._tetris_page.shutdown_match()
        if self._tank_page is not None and self.currentWidget() is self._tank_page:
            self._tank_page.shutdown_match()
        if self._bulls_cows_page is not None and self.currentWidget() is self._bulls_cows_page:
            self._bulls_cows_page.abort_current_workflow()
        page = self._ensure_pong_page()
        page.prepare_lobby()
        self.setCurrentWidget(page)

    def open_tetris_game(self):
        self.chat_room_page.deactivate()
        if self._snake_page is not None and self.currentWidget() is self._snake_page:
            self._snake_page.abort_current_workflow()
        if self._pong_page is not None and self.currentWidget() is self._pong_page:
            self._pong_page.shutdown_match()
        if self._tank_page is not None and self.currentWidget() is self._tank_page:
            self._tank_page.shutdown_match()
        if self._bulls_cows_page is not None and self.currentWidget() is self._bulls_cows_page:
            self._bulls_cows_page.abort_current_workflow()
        page = self._ensure_tetris_page()
        page.prepare_lobby()
        self.setCurrentWidget(page)

    def open_tank_battle(self):
        self.chat_room_page.deactivate()
        if self._snake_page is not None and self.currentWidget() is self._snake_page:
            self._snake_page.abort_current_workflow()
        if self._pong_page is not None and self.currentWidget() is self._pong_page:
            self._pong_page.shutdown_match()
        if self._tetris_page is not None and self.currentWidget() is self._tetris_page:
            self._tetris_page.shutdown_match()
        if self._bulls_cows_page is not None and self.currentWidget() is self._bulls_cows_page:
            self._bulls_cows_page.abort_current_workflow()
        page = self._ensure_tank_page()
        page.prepare_lobby()
        self.setCurrentWidget(page)

    def open_bulls_and_cows(self):
        self.chat_room_page.deactivate()
        if self._snake_page is not None and self.currentWidget() is self._snake_page:
            self._snake_page.abort_current_workflow()
        if self._pong_page is not None and self.currentWidget() is self._pong_page:
            self._pong_page.shutdown_match()
        if self._tetris_page is not None and self.currentWidget() is self._tetris_page:
            self._tetris_page.shutdown_match()
        if self._tank_page is not None and self.currentWidget() is self._tank_page:
            self._tank_page.shutdown_match()
        page = self._ensure_bulls_cows_page()
        page.prepare_game()
        self.setCurrentWidget(page)

    def join_game_invitation(self, metadata):
        """Route a chat invitation to the matching lobby and join safely."""

        from PySide6.QtWidgets import QMessageBox

        source = metadata if isinstance(metadata, dict) else {}
        game_id = str(source.get("game_id", ""))
        payload = source.get("room") if isinstance(source.get("room"), dict) else {}
        try:
            if game_id == "pong":
                from app.features.pong.network import PongRoom
                room = PongRoom.from_payload(payload)
                self.open_pong_game()
                self.pong_page.join_room(room)
            elif game_id == "tetris":
                from app.features.tetris.network import TetrisRoom
                room = TetrisRoom.from_payload(payload)
                self.open_tetris_game()
                self.tetris_page.join_room(room)
            elif game_id == "tank":
                from app.features.tank_battle.network import TankRoom
                room = TankRoom.from_payload(payload)
                self.open_tank_battle()
                self.tank_page.join_room(room)
            elif game_id == "bulls_and_cows":
                self.open_bulls_and_cows()
                self.bulls_cows_page.join_invitation(payload)
            elif game_id == "snake":
                self.open_snake_game()
                self.snake_page.join_invitation(payload)
            else:
                QMessageBox.information(self, "邀請不支援", "這則邀請的遊戲版本不受支援。")
        except (TypeError, ValueError) as error:
            QMessageBox.warning(self, "邀請資料錯誤", str(error))

    def closeEvent(self, event):
        self.workflow_pet.shutdown()
        for page in tuple(self.feature_pages.values()):
            shutdown = getattr(page, "shutdown", None)
            if callable(shutdown):
                shutdown()
        if self._snake_page is not None:
            self._snake_page.board.timer.stop()
        self.chat_room_page.shutdown()
        if self._pong_page is not None:
            self._pong_page.shutdown()
        if self._tetris_page is not None:
            self._tetris_page.shutdown()
        if self._tank_page is not None:
            self._tank_page.shutdown()
        if self._bulls_cows_page is not None:
            self._bulls_cows_page.shutdown()
        super().closeEvent(event)
