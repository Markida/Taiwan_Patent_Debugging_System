"""Build the v2.2.07c company standard distribution."""

from __future__ import annotations

from argparse import ArgumentParser
import ast
from datetime import date, datetime, timezone
import json
from pathlib import Path
import shutil
from zipfile import ZIP_DEFLATED, ZipFile

import build_company_update as common


CLEAN_VERSION = "2.2.07c"
CLEAN_DESCRIPTION = "公司標準乾淨版"
SOURCE_SHARED_RULES_PATH = r'C:\Documents'
STANDARD_SHARED_RULES_PATH = r'I:\Saint-Island_Patent_MDS'
STANDARD_FORBIDDEN_TERMS = (
    "彩蛋",
    "聊天室",
    "貪食蛇",
    "彈球",
    "俄羅斯方塊",
    "坦克大戰",
    "chat_room",
    "snake_game",
    "pong_game",
    "tetris_game",
    "tank_battle",
    "1A2B",
    "bulls_and_cows",
    "easter_egg",
    "arcade_navigation",
    "ArcadeNavigation",
    "arcade_cosmetics",
    "arcade_particles",
    "arcade_skin_art",
    "arcade_skin_picker",
    "CLM019",
    "claim_coverage",
    "syntax_lab",
    "SyntaxLabDialog",
    "語法對照",
)
STANDARD_TEXT_SUFFIXES = {
    ".bat",
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".txt",
    ".yaml",
    ".yml",
}
CLEAN_REQUIRED_FILES = (
    "Install_Update.bat",
    "Offline_Check.bat",
    "Saint-Island_Patent_MDS.exe",
    "Startup_Diagnostic.bat",
    common.CLOUD_DICTIONARY_SEED_FILENAME,
    "更新說明.txt",
    "app/main.py",
    "app/app/config.py",
    "app/app/background_tasks.py",
    "app/app/main_window.py",
    "app/app/paths.py",
    "app/app/resources/app_icon.ico",
    "app/app/resources/app_icon.png",
    "app/app/resources/taiwan_china_spec/BeijingTaijiTemplate.docx",
    "app/app/resources/taiwan_china_spec/ShanghaiYiPin.docx",
    "app/app/resources/taiwan_china_spec/terminology.tsv",
    "app/app/styles.py",
    "app/app/startup_splash.py",
    "app/app/workflow_context.py",
    "app/features/registry.py",
    "app/features/taiwan_china_spec/__init__.py",
    "app/features/taiwan_china_spec/converter.py",
    "app/features/taiwan_china_spec/terminology_store.py",
    "app/features/taiwan_china_spec/article_review.py",
    "app/features/patent_ocr/image_tools.py",
    "app/features/patent_ocr/figure_heading.py",
    "app/features/patent_ocr/figure_orientation_batch.py",
    "app/features/patent_ocr/result_store.py",
    "app/ui/figure_result_persistence.py",
    "app/features/patent_ocr/figure_heading_classes.py",
    "app/features/patent_ocr/figure_identifiers.py",
    "app/features/patent_ocr/reference_reconciliation.py",
    "app/features/patent_review/figure_ocr_checker.py",
    "app/features/patent_review/rule_engine.py",
    "app/ui/embodiment_figure_compare_page.py",
    "app/ui/file_drop.py",
    "app/ui/home_page.py",
    "app/ui/patent_review_page.py",
    "app/ui/recognition_page.py",
    "app/ui/taiwan_china_spec_page.py",
    "app/ui/spec_article_review.py",
    "app/ui/workflow_pet.py",
    "app/ui/pixel_cat.py",
    "app/features/workflow_pet/__init__.py",
    "app/features/workflow_pet/guidance.py",
    "app/models/patent_char_v4_company_approved_recall.onnx",
    "app/models/class_map.json",
    "app/models/patent_char_v4_company_approved_recall.names.json",
    "app/models/patent_char_v3_consensus.onnx",
    "app/models/patent_char_v3_consensus.names.json",
    "app/models/patent_label_group_v2_gold_ft.onnx",
    "app/models/patent_label_group_v1.onnx",
    "app/easyocr_models/english_g2.pth",
)


CLEAN_MAIN_WINDOW = '''
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
    """Keep the page mapping API while constructing clean-edition pages on demand."""

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
        self.workflow_context = PatentWorkflowContext()
        self.home_page = HomePage(
            features=FEATURES,
            open_feature_callback=self.open_feature,
        )
        self._add_stable_page(self.home_page)

        for feature in FEATURES:
            self._report_startup(f"正在載入{feature['title']}")

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
        return QSize(self.DEFAULT_WINDOW_SIZE)

    def minimumSizeHint(self):
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
        width = min(
            self.DEFAULT_WINDOW_SIZE.width(),
            max(self.MINIMUM_WINDOW_SIZE.width(), available.width() - horizontal_margin),
            available.width(),
        )
        height = min(
            self.DEFAULT_WINDOW_SIZE.height(),
            max(self.MINIMUM_WINDOW_SIZE.height(), available.height() - vertical_margin),
            available.height(),
        )
        self.resize(width, height)
        self.move(
            available.center().x() - width // 2,
            available.center().y() - height // 2,
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
        preserve_geometry = (
            self.isVisible() and not self.isMaximized() and not self.isFullScreen()
        )
        geometry = self.geometry() if preserve_geometry else None
        result = super().setCurrentWidget(widget)
        if geometry is not None:
            bounded = self._bounded_normal_geometry(geometry)
            self.setGeometry(bounded)
            QTimer.singleShot(
                0,
                lambda wanted=widget, saved=bounded: self._restore_normal_geometry(
                    saved, wanted
                ),
            )
        return result

    def _add_stable_page(self, page):
        page.setMinimumSize(0, 0)
        page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.addWidget(page)
        return page

    def _create_feature_page(self, feature_id):
        page = self.feature_pages.peek(feature_id)
        if page is not None:
            return page
        feature = self._feature_definitions.get(feature_id)
        if feature is None:
            raise KeyError(feature_id)

        page = feature["page_class"](go_home_callback=self.go_home)
        set_workflow_context = getattr(page, "set_workflow_context", None)
        if callable(set_workflow_context):
            set_workflow_context(self.workflow_context)
        set_open_feature_callback = getattr(page, "set_open_feature_callback", None)
        if callable(set_open_feature_callback):
            set_open_feature_callback(self.open_feature)
        set_feature_navigation = getattr(page, "set_feature_navigation", None)
        if callable(set_feature_navigation):
            set_feature_navigation(FEATURES, self.open_feature)

        self.feature_pages.store(feature_id, page)
        self._add_stable_page(page)
        if (
            hasattr(self, "routed_file_drop_controller")
            and not getattr(page, "uses_isolated_file_drop", False)
        ):
            self.routed_file_drop_controller.register_widget_tree(page)
        return page

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
        self.setCurrentWidget(self.home_page)

    def closeEvent(self, event):
        self.workflow_pet.shutdown()
        for page in tuple(self.feature_pages.values()):
            shutdown = getattr(page, "shutdown", None)
            if callable(shutdown):
                shutdown()
        super().closeEvent(event)

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
'''.lstrip()


CLEAN_HOME_PAGE = '''
from PySide6.QtWidgets import QWidget, QPushButton, QLabel, QVBoxLayout, QFrame
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

        card.setLayout(card_layout)
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(description)
        layout.addSpacing(14)
        layout.addWidget(card, alignment=Qt.AlignCenter)
        layout.addStretch()
        self.setLayout(layout)
'''.lstrip()


CLEAN_FEATURE_NAVIGATION = '''
"""Shared registry-driven navigation shown at the top of every feature page."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton


class FeatureNavigationBar(QFrame):
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
'''.lstrip()


def clean_styles(source: str) -> str:
    hidden_start = source.index("#HiddenTitleLetter {")
    hidden_end = source.index("#FeatureNavigation {", hidden_start)
    source = source[:hidden_start] + source[hidden_end:]

    arcade_start = source.index("#ArcadeNavigation {")
    arcade_end = source.index("#FeatureNavigationButton {", arcade_start)
    source = source[:arcade_start] + source[arcade_end:]

    unread_start = source.index("#ChatUnreadBadge {")
    unread_end = source.index("#ResultText QScrollBar:vertical {", unread_start)
    source = source[:unread_start] + source[unread_end:]

    egg_start = source.index("#SnakeTitle {")
    egg_end = source.index("#ReviewTable QHeaderView::section {", egg_start)
    return source[:egg_start] + source[egg_end:]


def _remove_source_nodes(source: str, nodes: list[ast.AST]) -> str:
    """Remove complete source statements without reformatting retained code."""

    lines = source.splitlines(keepends=True)
    omitted_lines = set()
    for node in nodes:
        start = node.lineno
        end = node.end_lineno
        if not end:
            raise RuntimeError("A standard review source range is unavailable.")
        prefix = lines[start - 1].encode("utf-8")[:node.col_offset].decode("utf-8")
        suffix = lines[end - 1].encode("utf-8")[node.end_col_offset:].decode("utf-8").strip()
        suffix = suffix.removeprefix(",").lstrip()
        if prefix.strip() or (suffix and not suffix.startswith("#")):
            raise RuntimeError("A standard review source range is not a complete statement.")
        for decorator in getattr(node, "decorator_list", ()):
            start = min(start, decorator.lineno)
        selected = set(range(start - 1, end))
        if selected.intersection(omitted_lines):
            raise RuntimeError("Standard review source ranges overlap.")
        omitted_lines.update(selected)
    result = "".join(line for index, line in enumerate(lines) if index not in omitted_lines)
    compile(result, "standard_review_source.py", "exec")
    return result


def clean_patent_rule_engine(source: str) -> str:
    """Remove the correspondence rule from the standard edition only."""

    tree = ast.parse(source)
    definitions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_claim_disclosure_issues"
    ]
    catalog_entries = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "RuleDefinition"
        and node.args and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "CLM019"
    ]
    invocations = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.YieldFrom)
        and isinstance(node.value.value, ast.Call)
        and isinstance(node.value.value.func, ast.Name)
        and node.value.value.func.id == "_claim_disclosure_issues"
    ]
    if tuple(map(len, (definitions, catalog_entries, invocations))) != (1, 1, 1):
        raise RuntimeError("The standard review exclusion markers are missing or ambiguous.")
    result = _remove_source_nodes(source, definitions + catalog_entries + invocations)
    if any(marker in result for marker in ("CLM019", "_claim_disclosure_issues", "claim_coverage")):
        raise RuntimeError("The standard review exclusion left an active reference.")
    return result


def clean_patent_review_page(source: str) -> str:
    """Remove correspondence-only presentation from the standard review page."""

    tree = ast.parse(source)
    branches = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Attribute)
        and node.test.left.attr == "rule_id"
        and len(node.test.ops) == 1 and isinstance(node.test.ops[0], ast.Eq)
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "CLM019"
    ]
    methods = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_coverage_evidence_html"
    ]
    if (len(branches), len(methods)) != (1, 1) or branches[0].orelse:
        raise RuntimeError("The standard review presentation markers are missing or ambiguous.")
    lab_methods = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "open_syntax_lab"
    ]
    lab_statements = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.Expr))
        and any(isinstance(child, ast.Attribute) and child.attr == "syntax_lab_button"
                for child in ast.walk(node))
    ]
    # Fail closed if the shared UI changes: never ship a hidden experiment.
    if (len(lab_methods), len(lab_statements)) != (1, 6):
        raise RuntimeError("The standard syntax preview markers are missing or ambiguous.")
    result = _remove_source_nodes(source, branches + methods + lab_methods + lab_statements)
    if any(marker in result for marker in ("CLM019", "_coverage_evidence_html",
                                          "syntax_lab", "SyntaxLabDialog", "語法對照")):
        raise RuntimeError("The standard review presentation exclusion is incomplete.")
    return result


def configure_standard_review(app_dir: Path) -> None:
    """Apply review edition isolation only to a copied application payload."""

    rule_engine = app_dir / "features" / "patent_review" / "rule_engine.py"
    review_page = app_dir / "ui" / "patent_review_page.py"
    rule_text = clean_patent_rule_engine(rule_engine.read_text(encoding="utf-8"))
    page_text = clean_patent_review_page(review_page.read_text(encoding="utf-8"))
    rule_engine.write_text(rule_text, encoding="utf-8")
    review_page.write_text(page_text, encoding="utf-8")
    (app_dir / "features" / "patent_review" / "claim_coverage.py").unlink()
    (app_dir / "features" / "patent_review" / "syntax_lab.py").unlink()
    (app_dir / "ui" / "syntax_lab_dialog.py").unlink()


def clean_installer(source: str) -> str:
    omitted = (
        '    "app\\features\\arcade_cosmetics.py"\n',
        '    "ui\\arcade_particles.py"\n',
        '    "ui\\arcade_skin_art.py"\n',
        '    "ui\\arcade_skin_picker.py"\n',
        '    "features\\patent_review\\claim_coverage.py"\n',
        '    "features\\patent_review\\syntax_lab.py"\n',
        '    "ui\\syntax_lab_dialog.py"\n',
        '    "app\\features\\chat_room\\__init__.py"\n',
        '    "app\\features\\bulls_and_cows\\__init__.py"\n',
        '    "app\\features\\bulls_and_cows\\engine.py"\n',
        '    "app\\features\\bulls_and_cows\\network.py"\n',
        '    "app\\features\\chat_room\\store.py"\n',
        '    "app\\features\\chat_room\\arcade_lan.py"\n',
        '    "app\\features\\chat_room\\game_ranking.py"\n',
        '    "app\\features\\chat_room\\game_report_avatar.jpg"\n',
        '    "app\\features\\pong\\__init__.py"\n',
        '    "app\\features\\pong\\network.py"\n',
        '    "app\\features\\snake\\__init__.py"\n',
        '    "app\\features\\snake\\score_store.py"\n',
        '    "app\\features\\snake\\network.py"\n',
        '    "app\\features\\tetris\\__init__.py"\n',
        '    "app\\features\\tetris\\network.py"\n',
        '    "app\\features\\tetris\\themes.py"\n',
        '    "app\\features\\tank_battle\\__init__.py"\n',
        '    "app\\features\\tank_battle\\network.py"\n',
        '    "ui\\chat_room_page.py"\n',
        '    "ui\\arcade_navigation.py"\n',
        '    "ui\\bulls_and_cows_page.py"\n',
        '    "ui\\pong_game_page.py"\n',
        '    "ui\\snake_game_page.py"\n',
        '    "ui\\tetris_game_page.py"\n',
        '    "ui\\tank_battle_page.py"\n',
    )
    for line in omitted:
        source = source.replace(line, "")

    preflight = r'''
rem Stop before changing files if an obsolete code folder contains user data.
if exist "%TARGET%\app\app\features" (
    for /f "eol=| delims=" %%F in ('dir /b /s /a-d "%TARGET%\app\app\features" 2^>nul') do (
        if /I not "%%~xF"==".py" if /I not "%%~xF"==".pyc" if /I not "%%~xF"==".pyo" (
            echo [ERROR] Existing data was found in an obsolete application folder.
            echo No files have been changed by this installer.
            echo Back up your data and use the correct package edition.
            pause
            exit /b 7
        )
    )
)
for /f "eol=| delims=" %%D in ('dir /b /ad "%TARGET%\app\features" 2^>nul') do (
    if not exist "%SOURCE%\features\%%D\" (
        for /f "eol=| delims=" %%F in ('dir /b /s /a-d "%TARGET%\app\features\%%D" 2^>nul') do (
            if /I not "%%~xF"==".py" if /I not "%%~xF"==".pyc" if /I not "%%~xF"==".pyo" (
                echo [ERROR] Existing data was found in an obsolete application folder.
                echo No files have been changed by this installer.
                echo Back up your data and use the correct package edition.
                pause
                exit /b 7
            )
        )
    )
)

'''.lstrip()
    copy_marker = 'robocopy "%SOURCE%" "%TARGET%\\app" /E'
    if source.count(copy_marker) != 1:
        raise RuntimeError("The installer application-copy marker is missing or ambiguous.")
    source = source.replace(copy_marker, preflight + copy_marker, 1)

    cleanup = r'''
rem Remove obsolete Python modules that are absent from this standard package.
for %%F in ("%TARGET%\app\ui\*.py") do (
    if not exist "%SOURCE%\ui\%%~nxF" del /q "%%~fF"
)
if exist "%TARGET%\app\ui\__pycache__" rmdir /s /q "%TARGET%\app\ui\__pycache__"
if exist "%TARGET%\app\app\features" rmdir /s /q "%TARGET%\app\app\features"
for /f "eol=| delims=" %%D in ('dir /b /ad "%TARGET%\app\features" 2^>nul') do (
    if not exist "%SOURCE%\features\%%D\" rmdir /s /q "%TARGET%\app\features\%%D"
)
for %%F in ("%TARGET%\app\features\patent_review\*.py" "%TARGET%\app\features\patent_review\*.pyc" "%TARGET%\app\features\patent_review\*.pyo") do (
    if not exist "%SOURCE%\features\patent_review\%%~nF.py" del /q "%%~fF"
)
for %%F in ("%TARGET%\app\features\patent_review\__pycache__\*.pyc") do (
    if exist "%%~fF" del /q "%%~fF"
)

'''.lstrip()
    marker = 'copy /Y "%PACKAGE%Saint-Island_Patent_MDS.exe"'
    source = source.replace(marker, cleanup + marker, 1)
    return (
        source.replace("v2.2.07 launcher", "v2.2.07c launcher")
        .replace("SaintIsland_v2207_update_check.json", "SaintIsland_v2207c_update_check.json")
        .replace("[PASS] v2.2.07 update installed", "[PASS] v2.2.07c update installed")
    )


def release_notes() -> str:
    return f'''Saint-Island_Patent_MDS v{CLEAN_VERSION} 公司標準乾淨版
================================================

本包只更新應用程式與模型備援檔，不會更換公司電腦既有的 runtime，
也不需要安裝 Python、Conda、套件或連線到外網。

主要更新（v{CLEAN_VERSION}）
本次新增（2026-09-21，優先於下方歷史功能摘要）
- 墨墨會依實際進度提供兩種下一步選擇；同一無標號元件反覆出現相似詞警告時，可自行決定加入本文件白名單或先看其他結果。新增後提醒重新檢核，不會自行排除錯誤。
- 圖式操作改為第一步自動旋轉、第二步圖片標號辨識、第三步清單比對。公司包不含尚未正式核准的圖題方向模型，第一步遇缺模型提示時，請手動轉正並直接使用第二步；正式標號OCR模型維持不變。
- 圖式辨識及手動增刪、修訂、圖號映射、方向與清單會保存在本機；再次匯入同名來源可還原。同名檔內容不同仍會載入舊結果並提醒，請核對後再重新處理。保存失敗時請保留視窗。
- 段落圖式比對會由圖式簡單說明提取圖名，支援多圖、範圍及英文字尾，無法可靠提取時不猜測。人工參閱圖號設定與缺失元件名稱顯示維持可用。
- 台陸轉換已撤回 D01–D50 規則改版，還原原先的繁體預覽、簡體 Word 輸出與說明書首行三字縮排；第 2 項起的權利要求依原附屬項規則插於原檔內文最後一段與有益效果之間。
- 本次保持各版本共用規則路徑與本機個人資料；不覆蓋custom_text_rules.json，不更新runtime，不需要連接外網。
- 台陸用語辭典讀寫位置改為主程式EXE旁的taiwan_china_terminology.txt，舊網路路徑覆寫設定不再生效；本機快取使用v4。請保留自行維護的辭典，更新包另附198筆公司辭典供核對。

歷史功能摘要（台陸格式轉換以本次上述規則選擇為準）
- 墨墨右鍵手動動作精簡為吃飯、喝水、玩毛線球；其餘動作由閒置十秒自動觸發，新增伸懶腰、打哈欠、踩奶、撲葉子及追尾巴。
- 墨墨新增向左走路／跑步；按住左鍵快速左右晃動會出現漩渦眼睛並吐出毛線球，慢速搬動不會誤觸，動畫與拖曳均限制在主視窗內。
- 段落圖式比對的「參閱圖式」可逐段雙擊修改，修改後立即重算缺失標號；保留人工設定並以藍色標示。
- 圖號範圍支援圖8-圖12、圖8A-圖8E、圖8A到圖8E與小寫字尾；描述元件「顯示如圖…的圖像」時，不再取代該段及後續段落原本參閱的圖式。
- 圖式標號頁整理本頁圖號、翻頁及旋轉按鈕。新增圖題辨識的相容支援，但本包不啟用尚未完成標註、訓練與正式核准的新圖題模型，自動轉向亦不會因此啟用。
- 維持正式OCR模型、公司共用規則與使用者資料；更新包僅納入可離線使用且已完成驗證的功能。
- 新增像素黑貓助手「墨墨」：可在程式視窗內拖曳，依目前頁面與操作進度提供離線提示；平常每兩秒甩尾並眨眼，滑鼠移入會抬頭揮爪回應，閒置每十秒隨機活動。右鍵可暫時收起，右上角圖示可叫回，並記住各使用者的顯示與聲音設定。
- 墨墨走跑使用左右方向的四腳側面步態，打滾時露出肚子，多數動作穿插甩尾與前爪搔臉；保留紅色怒氣符號的生氣動作，以及收尾趴睡、冒出 Zzz 的睡覺動作。
- 墨墨每次啟動會依電腦當下的日期與時間主動打招呼，自動切換早安、午安、晚安；切換頁面不重複招呼，並尊重已收起墨墨的設定。
- 墨墨每兩秒僅搖尾、眨眼；特殊動作期間暫停兩秒小動作，完成後恢復。滑鼠移開貓咪或提示框時，不再立即收起提示文字。
- 台陸轉換的「發明／實用新型內容」改為獨立人工檢查分類，不再混入一般說明書段落，方便單獨核對來源內容。
- 轉換時保留原始「發明／實用新型內容」的揭露文字與有益效果，只移除已用於固定開頭的來源句；第2項起的權利要求依既定附屬項規則轉寫，插入原檔內文與有益效果之間，不覆蓋原有技術內容。
- 移除人工檢查區的「以權利要求更新內容」按鈕，避免誤覆蓋原說明書內容；人工選取、刪除與復原功能維持不變。
- 圖式標號頁的旋轉按鈕統一使用清楚的 90° 標示，並維持預覽操作列的穩定排列。
- 台陸轉換的公司固定版型會移除權利要求1重複的標的與「並包含」引言，第三段直接由可安全辨識的「所述……」元件細節開始；附屬項仍保留原本的標的開頭，無法安全定位時保留原文並提示人工確認。
- 台陸轉換的公司固定內容格式會移除第二個來源機制句開頭多餘的「於是，」，保留其完整技術內容。
- 文件偵錯新增 REF007：實施方式的單一段落最多參閱四張不同圖式；圖號範圍會展開並去重，五張起列為錯誤。
- 圖式參閱解析補強「參閱圖1到圖3，及圖6」等逗號加連接詞寫法，避免漏算或錯算段落所引用的圖式。
- 文件檢核結果的「錯誤種類」欄會自動分行並依內容調整列高；滑鼠停留仍可查看完整原訊息，不改變任何規則結果。
- 每份文件專用的無標號元件白名單改存於各 Windows 使用者的 %LOCALAPPDATA%\\Saint-Island_Patent_MDS，避免多人共用安裝目錄時互相覆蓋；公司共用黑白名單路徑維持 I:\\Saint-Island_Patent_MDS 不變。
- 台陸轉換在同時辨識到公司標準「因此…目的…」與「於是…」句時，會依固定順序保留目的句、完整機制句、全部權利要求敘述與原有益效果；舊格式文件仍採原有無損回退流程。
- 「於是」句中的構件清單只會在第一構件一致且插入位置明確時補入權利要求1；條件不明確時保留原文並提示，不會猜測或硬改。標的「無線滑鼠組」維持原名，構件語境的「滑鼠」則依公司用語轉為「鼠標」。
- 台陸辭典新增第 198 筆「兩個合一個 → 二合一」，快取版本同步更新，更新包根目錄亦附上完整 198 筆公司共用辭典種子。
- 圖式標號頁的「全部右旋90度」改在背景執行；處理中會防止重複匯入或辨識，按 Esc 可安全取消，只有全部完成後才替換目前圖片與清除舊辨識結果。
- 圖片旋轉改用可安全在背景執行的 QImage；拖曳調整高解析預覽視窗時，會合併短時間內的重複重繪，降低介面卡頓。
- 台陸共用辭典與本機快取內容相同時不再重複寫檔，降低網路磁碟、同步資料夾及防毒掃描造成的等待。
- 台陸轉換後的說明書正文統一採 Word 首行縮排三個中文字寬，技術領域、背景技術、發明／實用新型內容、附圖說明與具體實施方式均適用，標題、摘要及權利要求不受影響。
- 台陸轉換側邊「一個檢查」新增可折疊的「附圖說明」分類，並可在各說明書子章節間維持正確分組。
- 文件偵錯補強 CLM012：元件先以複數型態揭露後，「對應的該 A／相對應之該 A」仍視為單數指稱並列為錯誤，問題詞句會完整標示。
- 數量詞解析不再把「其中兩者」錯套到後方元件；「一繞該軸線的第一周向」會正確將第一周向界定為單數，後續使用「該第一周向」不再誤報。
- 文件偵錯新增 CLM009：同一個請求項必須且只能出現一個全形句號「。」，且須位於句尾；跨行或跨 Word 段落仍合併計算，小數點與表格內容不會誤計。
- 公司標準版暫不提供「請求項1與發明／實用新型內容對應」提醒；摘要字數與重複請求項提醒維持啟用。
- 支援同一權利要求分多行修訂，保留其項次及技術內容；段落標題或項次有缺漏時不覆蓋原內容，也不直接更動原始 Word。
- 底部轉換按鈕改為較大的白色箭頭與文字。

操作效能與回應改善
- 功能頁改為首次使用時建立，OCR、PDF 與文件檢核所需的較大型模組延後載入，降低啟動初期的等待。
- 文件讀取與檢核、PDF 轉圖及共用規則讀取改由背景執行，處理期間可繼續操作視窗；取消後不會以遲到的結果覆蓋目前工作。
- 台陸轉換的 Word 讀取、預覽與紅字差異計算移至背景；快速切換來源時只套用目前檔案的結果，失敗或取消後恢復控制項並保留既有人工編輯。
- 減少重複文章的差異計算與清單重建，並快取單次文件檢核中重複使用的文字比對候選；除上述暫停項目外，其餘規則判定結果保持不變。
- 關閉視窗不再等待背景讀取完成，已取消或已關閉頁面的結果不會回填。

文件偵錯新增提醒
- ABS001 中文摘要字數提醒：以 250 字為原則，分別顯示非空白字元數與中英文字數估計；估計超過時列為警告，僅因計數口徑可能超過時列為資訊提醒。
- CLM018 重複請求項提醒：忽略自身項次、排版空白及等價全半形標點後，比對不同請求項的完整內文；完全相同者合併提醒，點選可查看完整請求項。
- 以上提醒供人工核對，不直接判斷法律上的權利範圍，也不修改原始 Word。

台陸轉換「一個」檢查與累積更新
- 右側訊息欄縮小，下方保留訊息，上方新增預覽全文的「一個」逐處清單。
- 點選清單可跳到原句，以螢光底色標示句子與對應詞語；標示不會寫入輸出 Word。
- 支援 Shift／Ctrl 複選，再按「刪除所選項目」只刪除指定位置的「一個」兩字，保留句子其餘內容；刪除後自動選取下一處。
- Ctrl+Z 可復原，Ctrl+Y 可重做批次刪除及文字編輯；保留人工修訂後的 Word 輸出。
- 優先置頂：每一個、各一個、上一個、下一個、其中一個、另一個、其中另一個、至少一個、第一個，以及「一個」後 10 字內接「空間」的項目。
- 繼續編輯文章時同步更新清單定位與選取範圍；保留台陸頁的獨立拖曳規則，並調整小視窗排版。

既有功能與累積更新
1. 實施方式的小段落即使先以「步驟E」或「在本實施例中」開頭，仍可正確擷取段落中途出現的參閱圖式，不再顯示多餘的沿用圖式空結果。
2. 新增淺藍色啟動等待動畫與分階段載入提示，並以延遲匯入降低啟動等待時間。
3. 文件偵錯頁提供「實施方式段落出現之無標號元件」，可排除指定詞語的模糊比對及標號偵測，並保留重新檢核功能。
4. 共用自訂文字規則由公司端 I:\\Saint-Island_Patent_MDS 路徑讀寫；此設定為本標準版專用。
5. 請求項相同元件與相同內容的單複數錯誤，在同一請求項只顯示一次。
6. 支援「各自的該」、「二個／多個該元件」等合法複數成員指稱。
7. 支援列舉型步驟規則；「步驟」不嚴格檢查冠詞單複數，但仍檢查實施方式中的標號。
8. 實施方式非末段可用全形或半形冒號承接下一小段，不再誤報缺少句號。
9. 段落圖式比對支援「例如為圖5」累加依附、同段單列顯示，並從「綜上所述」起停止後續比對。
10. 圖式標號頁提供縮小、適合視窗及放大按鈕；段落比對頁已移除重複提示文字。
11. 所有非首頁頁面皆可按 Esc 立即中止目前流程並返回首頁；OCR 辨識中止後會自動恢復控制項。
12. 既有 OCR 模型、人工修訂、符號清單及離線 runtime 均維持相容。
13. 圖式標號預設使用黃金資料微調的完整標號定位器 v2；封存集完整標號定位率為 496/502（98.80%）。
14. 文件已有符號清單時，OCR 會保守修正唯一且同長度的已知字形混淆（例如 Ll→L1、WI→W1），並保留模型原始值及使用者手動修改。
15. 上述校正在封存黃金集由 466/502 提升至 473/502，逐筆稽核為 7 筆改善、0 筆誤改；不會自動增加或刪除字元。
16. 主視窗會依螢幕可用範圍決定啟動大小；切換頁面或更新內容時保留既有位置、尺寸及最大化狀態，避免控制項因視窗變動而被遮住。
17. 所有正式功能頁恢復跨頁檔案拖放：DOCX 自動切換至文件偵錯，PDF 自動切換至圖式標號，不支援的格式會明確提示。
18. 全部按鈕字體增加 2pt，頂部功能列同步加高並維持各頁固定位置。
19. 圖式標號框改為細線，原圖預覽以螢幕實際像素密度重新取樣，在 Windows 高 DPI 縮放下仍保留細節。
20. 大章節間的下一頁分節符號改為選用，未插入分節符號不再列為錯誤。
21. 提高數字 0 的辨識門檻並過濾相對過小的獨立圓圈；完整標號已包含子框時，會保留完整標號並捨棄重複子標號。
22. 圖式標號頁移除模型選擇列，正式模型維持自動載入；原圖預覽與辨識暫存區向上延伸放大。
23. 辨識暫存支援 Delete 快捷操作：選取整列時刪除標號，雙擊進入文字編輯後只刪除反白字元；新增與刪除按鈕不再遮住清單。
24. 專利文件偵錯頁移除冗長的符號擷取狀態列，三個主要工作視窗獲得更多垂直空間。
25. 新增台灣－大陸專利說明書轉換頁，可使用北京／上海範本，並自動載入公司預設辭典供使用者修訂後轉換。
26. 權利要求章節改採嚴格標題辨識；實施方式內提及「申請專利範圍」時不再把符號說明誤轉成第一項權利要求。
27. 台陸轉換頁的辭典改為高對比白底，右側訊息欄縮小，並移除右上角使用說明與關於按鈕。
28. 圖式標號頁移除第二階段清單輸入欄，放大辨識結果，並將兩個結果切換按鈕排列於同一列。
29. 台陸辭典納入 198 條公司核定規則及人工更正，完全依核定列序逐條轉換。
30. 台陸轉換頁保留「同步」並新增「上傳」；共用 TXT 位於 \\\\sic11\\Doc\\_CP\\Saint-Island\\_Patent\\_MDS\\taiwan_china_terminology.txt。
31. 權利要求段落移除 Word 黑色段落標記，但保留自動數字編號與段落樣式。
32. 符號說明與發明申請專利範圍銜接文字不再誤入權利要求書。
33. 台陸轉換新增繁體、可編輯的大陸案格式預覽，修改處以紅字標示；圖式標號頁同步完成代表圖自動跳轉、上下搜尋箭頭及 3:2 視窗配置，文件偵錯頁的符號說明切換按鈕也已統一外觀。
34. 台陸轉換的發明／新型內容依原請求項順序納入各項技術內容，保留既有目的、非重複敘述與有益效果；來源沒有記載的效果不會自行補寫。
35. 可辨識的附屬請求項改寫為該項自身主題的敘述，僅移除開頭的依附引用與銜接詞，保留後續技術內容；無法可靠解析時保留原文並提醒確認。
36. 預覽、直接輸出與人工編輯後輸出的內容處理維持一致；人工修訂的內容不會重新生成或重複套用辭典，技術文字中的空白與額外特徵也不會因近似比對而刪除。
37. 「一個」清單依摘要、說明書及權利要求書分組，保留各組的優先項目及原文順序，並維持可正確跳轉與編輯的文字位置。

安裝方式
1. 完全關閉 Saint-Island_Patent_MDS。
2. 將本更新資料夾完整解壓到公司程式根目錄內。
3. 雙擊 Install_Update.bat。
4. 安裝器會依序執行離線模型推論與 GUI 啟動測試。
5. 看到 [PASS] v{CLEAN_VERSION} update installed 即完成。

重要
- 請勿只複製單一 EXE；請完整解壓更新資料夾並執行安裝器，保留公司程式根目錄原有的 runtime。
- 安裝器不會刪除或覆蓋 custom_text_rules.json 或其他個人 JSON 資料。
- 若既有應用程式的待清理模組資料夾含非程式資料，安裝器會在更新任何檔案前停止；請先備份並確認使用正確版本的更新包。
'''


def validate_standard_package_language(package_dir: Path) -> None:
    """Reject standard payload references to features excluded from this edition."""

    violations = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(package_dir).as_posix()
        relative_folded = relative.casefold()
        for term in STANDARD_FORBIDDEN_TERMS:
            if term.casefold() in relative_folded:
                violations.append(f"{relative}: path contains {term!r}")
        if path.suffix.casefold() not in STANDARD_TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        folded = text.casefold()
        for term in STANDARD_FORBIDDEN_TERMS:
            if term.casefold() in folded:
                violations.append(f"{relative}: content contains {term!r}")
    if violations:
        raise RuntimeError(
            "Standard package language isolation failed:\n"
            + "\n".join(violations)
        )


def build_package(output_root: Path, package_name: str, *, include_figure_heading_trial=False) -> tuple[Path, Path, Path]:
    heading_files = common.figure_heading_package_files(include_figure_heading_trial)
    package_dir = output_root / package_name
    zip_path = output_root / f"{package_name}.zip"
    checksum_path = output_root / f"{package_name}.zip.sha256.txt"
    if package_dir.exists() or zip_path.exists() or checksum_path.exists():
        raise FileExistsError("Release output already exists; choose a new package name.")

    app_dir = package_dir / "app"
    app_dir.mkdir(parents=True)
    shutil.copy2(common.PROJECT_ROOT / "main.py", app_dir / "main.py")
    for folder in ("app", "features", "ui"):
        common.copy_source_tree(common.PROJECT_ROOT / folder, app_dir / folder)

    configure_standard_review(app_dir)

    shutil.rmtree(app_dir / "app" / "features" / "chat_room")
    shutil.rmtree(app_dir / "app" / "features" / "bulls_and_cows")
    shutil.rmtree(app_dir / "app" / "features" / "pong")
    shutil.rmtree(app_dir / "app" / "features" / "snake")
    shutil.rmtree(app_dir / "app" / "features" / "tetris")
    shutil.rmtree(app_dir / "app" / "features" / "tank_battle")
    (app_dir / "app" / "features" / "arcade_cosmetics.py").unlink()
    (app_dir / "ui" / "arcade_particles.py").unlink()
    (app_dir / "ui" / "arcade_skin_art.py").unlink()
    (app_dir / "ui" / "arcade_skin_picker.py").unlink()
    (app_dir / "ui" / "chat_room_page.py").unlink()
    (app_dir / "ui" / "arcade_navigation.py").unlink()
    (app_dir / "ui" / "bulls_and_cows_page.py").unlink()
    (app_dir / "ui" / "pong_game_page.py").unlink()
    (app_dir / "ui" / "snake_game_page.py").unlink()
    (app_dir / "ui" / "tetris_game_page.py").unlink()
    (app_dir / "ui" / "tank_battle_page.py").unlink()

    config_path = app_dir / "app" / "config.py"
    config_text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        config_text.replace('APP_VERSION = "2.2.07"', f'APP_VERSION = "{CLEAN_VERSION}"', 1),
        encoding="utf-8",
    )
    custom_rules_path = app_dir / "features" / "patent_review" / "custom_rules.py"
    custom_rules_text = custom_rules_path.read_text(encoding="utf-8")
    if SOURCE_SHARED_RULES_PATH not in custom_rules_text:
        raise RuntimeError(
            "The source shared-rule path marker is missing from custom_rules.py."
        )
    custom_rules_path.write_text(
        custom_rules_text.replace(
            SOURCE_SHARED_RULES_PATH,
            STANDARD_SHARED_RULES_PATH,
            1,
        ),
        encoding="utf-8",
    )
    (app_dir / "app" / "main_window.py").write_text(CLEAN_MAIN_WINDOW, encoding="utf-8")
    (app_dir / "ui" / "home_page.py").write_text(CLEAN_HOME_PAGE, encoding="utf-8")
    (app_dir / "ui" / "feature_navigation.py").write_text(
        CLEAN_FEATURE_NAVIGATION,
        encoding="utf-8",
    )
    styles_path = app_dir / "app" / "styles.py"
    styles_path.write_text(clean_styles(styles_path.read_text(encoding="utf-8")), encoding="utf-8")

    model_dir = app_dir / "models"
    model_dir.mkdir()
    for file_name in common.MODEL_FILES:
        shutil.copy2(common.PROJECT_ROOT / "models" / file_name, model_dir / file_name)
    for file_name in heading_files:
        shutil.copy2(common.PROJECT_ROOT / "models" / file_name, model_dir / file_name)

    easyocr_model_dir = app_dir / "easyocr_models"
    easyocr_model_dir.mkdir()
    for file_name in common.EASYOCR_MODEL_FILES:
        shutil.copy2(
            common.PROJECT_ROOT / "easyocr_models" / file_name,
            easyocr_model_dir / file_name,
        )

    installer = (common.PROJECT_ROOT / "packaging" / "Install_Update.bat").read_text(
        encoding="utf-8"
    )
    (package_dir / "Install_Update.bat").write_text(
        clean_installer(installer), encoding="ascii", newline="\r\n"
    )
    shutil.copy2(common.PROJECT_ROOT / "packaging" / "Startup_Diagnostic.bat", package_dir)
    shutil.copy2(common.PROJECT_ROOT / "Offline_Check.bat", package_dir)
    common.write_cloud_dictionary_seed(
        package_dir / common.CLOUD_DICTIONARY_SEED_FILENAME
    )
    clean_launcher_source = package_dir / ".clean_launcher.cs"
    launcher_text = (
        common.PROJECT_ROOT
        / "packaging"
        / "Saint-IslandPatentOCR.Launcher.cs"
    ).read_text(encoding="utf-8")
    clean_launcher_source.write_text(
        launcher_text.replace(
            '[assembly: AssemblyInformationalVersion("2.2.07")]',
            '[assembly: AssemblyInformationalVersion("2.2.07c")]',
            1,
        ),
        encoding="utf-8",
    )
    try:
        common.compile_launcher(
            package_dir / "Saint-Island_Patent_MDS.exe",
            source_path=clean_launcher_source,
        )
    finally:
        clean_launcher_source.unlink(missing_ok=True)
    (package_dir / "更新說明.txt").write_text(
        (common.FIGURE_HEADING_TRIAL_NOTES if include_figure_heading_trial else "") + release_notes(), encoding="utf-8-sig", newline="\r\n"
    )

    for relative_path in CLEAN_REQUIRED_FILES:
        if not (package_dir / relative_path).is_file():
            raise FileNotFoundError(f"Incomplete clean package: {relative_path}")

    forbidden = (
        app_dir / "app" / "features" / "arcade_cosmetics.py",
        app_dir / "ui" / "arcade_particles.py",
        app_dir / "ui" / "arcade_skin_art.py",
        app_dir / "ui" / "arcade_skin_picker.py",
        app_dir / "features" / "patent_review" / "claim_coverage.py",
        app_dir / "features" / "patent_review" / "syntax_lab.py",
        app_dir / "ui" / "syntax_lab_dialog.py",
        app_dir / "ui" / "chat_room_page.py",
        app_dir / "ui" / "arcade_navigation.py",
        app_dir / "ui" / "bulls_and_cows_page.py",
        app_dir / "ui" / "pong_game_page.py",
        app_dir / "ui" / "snake_game_page.py",
        app_dir / "ui" / "tetris_game_page.py",
        app_dir / "ui" / "tank_battle_page.py",
        app_dir / "app" / "features" / "chat_room",
        app_dir / "app" / "features" / "bulls_and_cows",
        app_dir / "app" / "features" / "pong",
        app_dir / "app" / "features" / "snake",
        app_dir / "app" / "features" / "tetris",
        app_dir / "app" / "features" / "tank_battle",
    )
    if any(path.exists() for path in forbidden):
        raise RuntimeError("Standard package unexpectedly contains optional code.")

    files = sorted(path for path in package_dir.rglob("*") if path.is_file())
    manifest = {
        "product": "Saint-Island_Patent_MDS",
        "version": CLEAN_VERSION,
        "package_name": package_name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_included": False,
        "figure_heading_trial_included": include_figure_heading_trial,
        "requires_existing_runtime": True,
        "distribution_profile": "company_standard_clean",
        "files": [
            {
                "path": path.relative_to(package_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": common.sha256(path),
            }
            for path in files
        ],
    }
    (package_dir / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    validate_standard_package_language(package_dir)

    with ZipFile(zip_path, "w", ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(path, (Path(package_name) / path.relative_to(package_dir)).as_posix())

    checksum_path.write_text(f"{common.sha256(zip_path)}\n", encoding="ascii")
    return package_dir, zip_path, checksum_path


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=common.RELEASE_ROOT)
    parser.add_argument("--include-figure-heading-trial", action="store_true")
    parser.add_argument(
        "--name",
        default=(
            f"Saint-Island_Patent_MDS_v{CLEAN_VERSION}_{CLEAN_DESCRIPTION}_"
            f"{date.today():%Y%m%d}"
        ),
    )
    args = parser.parse_args()
    package_dir, zip_path, checksum_path = build_package(
        args.output_root.resolve(), args.name,
        include_figure_heading_trial=args.include_figure_heading_trial,
    )
    print(f"PACKAGE_DIR={package_dir}")
    print(f"ZIP={zip_path}")
    print(f"SHA256={checksum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
