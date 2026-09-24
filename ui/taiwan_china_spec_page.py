from __future__ import annotations

from difflib import SequenceMatcher
from html import escape
import os
from pathlib import Path
import threading

from PySide6.QtCore import QObject, QPointF, QSize, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from features.taiwan_china_spec import (
    ConversionError,
    TerminologyDictionaryStore,
    build_conversion_preview,
    convert_document,
    normalize_terminology_pairs,
    parse_edited_preview_text,
    replace_content_from_edited_claims,
)
from ui.feature_navigation import FeatureNavigationBar
from ui.file_drop import SingleFileDropController
from ui.spec_article_review import ArticleReviewPanel


TEMPLATE_OPTIONS = (
    ("北京泰吉", "beijing_taiji"),
    ("上海一品", "shanghai_yipin"),
)


def _source_key(source):
    # abspath is lexical; Path.resolve/is_file can block on an offline share.
    # Actual existence and Word parsing are checked by the background worker.
    return os.path.normcase(os.path.abspath(os.fspath(source)))


def _start_worker(worker, finished):
    """Run file/Word work without a QThread destructor blocking app shutdown.

    A disconnected company share can leave a filesystem call stuck inside
    Windows. Daemon threads let the GUI exit without terminating a QThread or
    waiting for that call. Workers own no widgets and deliver queued signals.
    """
    worker.finished.connect(finished)
    worker.finished.connect(worker.deleteLater)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    return thread


class PreviewWorker(QObject):
    completed = Signal(int, object, str)
    failed = Signal(int, str)
    finished = Signal()

    def __init__(self, revision, source, template_key, terminology_pairs):
        super().__init__()
        self.revision = revision
        self.source = source
        self.template_key = template_key
        self.terminology_pairs = tuple(terminology_pairs)
        self.cancelled = threading.Event()

    def run(self):
        try:
            if self.cancelled.is_set():
                return
            preview = build_conversion_preview(
                self.source,
                self.template_key,
                terminology_pairs=self.terminology_pairs,
                traditional_characters=True,
            )
            if self.cancelled.is_set():
                return
            html = TaiwanChinaSpecPage._preview_html(preview)
            if not self.cancelled.is_set():
                self.completed.emit(self.revision, preview, html)
        except Exception as exc:
            if not self.cancelled.is_set():
                self.failed.emit(self.revision, str(exc))
        finally:
            self.finished.emit()


class PlainOnlyPreviewEditor(QTextEdit):
    """Editable redline preview with no user-facing rich-text operations."""

    _BLOCKED_FORMAT_KEYS = {
        Qt.Key_B,
        Qt.Key_I,
        Qt.Key_U,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.review_history = None

    def insertFromMimeData(self, source):
        # Even when the clipboard supplies HTML, insert only its visible text.
        # This leaves add/delete/copy/cut/paste as plain-text operations while
        # retaining the program-generated red spans already in the document.
        if source.hasText():
            self.insertPlainText(source.text())

    def keyPressEvent(self, event):
        if self.review_history is not None:
            if event.matches(QKeySequence.Undo):
                self.undo()
                event.accept()
                return
            if event.matches(QKeySequence.Redo):
                self.redo()
                event.accept()
                return
        if (
            event.modifiers() & Qt.ControlModifier
            and event.key() in self._BLOCKED_FORMAT_KEYS
        ):
            event.accept()
            return
        super().keyPressEvent(event)

    def undo(self):
        if self.review_history is None:
            super().undo()
        else:
            self.review_history.undo()

    def redo(self):
        if self.review_history is None:
            super().redo()
        else:
            self.review_history.redo()

    def contextMenuEvent(self, event):
        if self.review_history is None:
            return super().contextMenuEvent(event)
        # Keyboard and context-menu undo share the article deletion history,
        # including the exact list selections and program-generated red spans.
        menu = QMenu(self)
        editable = self.isEnabled() and not self.isReadOnly()
        menu.addAction("復原\tCtrl+Z", self.undo).setEnabled(
            editable and self.review_history.can_undo()
        )
        menu.addAction("重做\tCtrl+Y", self.redo).setEnabled(
            editable and self.review_history.can_redo()
        )
        menu.addSeparator()
        selected = self.textCursor().hasSelection()
        menu.addAction("剪下\tCtrl+X", self.cut).setEnabled(editable and selected)
        menu.addAction("複製\tCtrl+C", self.copy).setEnabled(selected)
        menu.addAction("貼上\tCtrl+V", self.paste).setEnabled(
            editable and QApplication.clipboard().mimeData().hasText()
        )
        menu.addAction("刪除", self._delete_selected_text).setEnabled(editable and selected)
        menu.addSeparator()
        menu.addAction("全選\tCtrl+A", self.selectAll)
        menu.exec(event.globalPos())

    def _delete_selected_text(self):
        if not self.isReadOnly():
            self.textCursor().removeSelectedText()


class SpecConversionWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        source,
        output,
        template_key,
        terminology_pairs,
        edited_preview_text=None,
    ):
        super().__init__()
        self.source = source
        self.output = output
        self.template_key = template_key
        self.terminology_pairs = tuple(terminology_pairs)
        self.edited_preview_text = edited_preview_text

    @Slot()
    def run(self):
        try:
            report = convert_document(
                self.source,
                self.output,
                self.template_key,
                terminology_pairs=self.terminology_pairs,
                edited_preview_text=self.edited_preview_text,
            )
            self.completed.emit(report)
        except Exception as exc:  # The UI must always recover its enabled state.
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class TerminologyLoadWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, store):
        super().__init__()
        self.store = store

    @Slot()
    def run(self):
        try:
            self.completed.emit(self.store.load_preferred())
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class TerminologyUploadWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, store, pairs):
        super().__init__()
        self.store = store
        self.pairs = tuple(pairs)

    @Slot()
    def run(self):
        try:
            self.completed.emit(self.store.upload_pairs(self.pairs))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class TaiwanChinaSpecPage(QWidget):
    """Taiwan-to-China converter using the PatentNumberOCR visual system."""

    uses_isolated_file_drop = True

    def __init__(
        self,
        go_home_callback,
        dictionary_store=None,
        auto_load_dictionary=True,
    ):
        super().__init__()
        self.go_home_callback = go_home_callback
        self.dictionary_store = dictionary_store or TerminologyDictionaryStore()
        self._output_is_automatic = True
        self._dictionary_revision = 0
        self._dictionary_load_revision = 0
        self._dictionary_force_apply = False
        self._populating_dictionary = False
        self._conversion_running = False
        self._conversion_thread = None
        self._conversion_worker = None
        self._dictionary_thread = None
        self._dictionary_worker = None
        self._dictionary_upload_revision = 0
        self._dictionary_upload_thread = None
        self._dictionary_upload_worker = None
        self._preview_internal_update = False
        self._preview_dirty = False
        self._preview_source = ""
        self._preview_specification_kind = ""
        self._preview_fixed_content_layout = False
        self._content_claims_baseline = ()
        self._content_claim_history = []
        self._preview_revision = 0
        self._preview_thread = None
        self._preview_worker = None
        self._pending_preview = None
        self._closing = False
        self.build_ui()
        self.file_drop_controller = SingleFileDropController(
            self,
            allowed_suffix=(".doc", ".docx"),
            file_type_name="Word 文件",
            on_file=self.load_source,
        )
        # Build the top-level editor after installing the page drop filter so a
        # Word file dropped on the dictionary dialog is not treated as a new
        # conversion source.
        self._build_dictionary_dialog()
        self._apply_dictionary_snapshot(self.dictionary_store.load_bundled())
        if auto_load_dictionary:
            QTimer.singleShot(0, lambda: self.reload_dictionary(force=False))

    def set_feature_navigation(self, features, open_feature_callback):
        self.feature_navigation.configure(features, open_feature_callback)

    def _standard_icon(self, standard_pixmap):
        return QApplication.style().standardIcon(standard_pixmap)

    @staticmethod
    def _conversion_arrow_icon():
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#ffffff"), 6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(12, 32, 52, 32)
        painter.drawPolyline(QPolygonF([QPointF(36, 16), QPointF(52, 32), QPointF(36, 48)]))
        painter.end()
        return QIcon(pixmap)

    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 8)
        layout.setSpacing(6)

        self.feature_navigation = FeatureNavigationBar(
            self.go_home_callback,
            "taiwan_china_spec",
        )
        layout.addWidget(self.feature_navigation)

        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("台灣－大陸專利說明書轉換")
        title.setObjectName("PageTitle")
        title.setMaximumHeight(34)
        header.addWidget(title)
        header.addStretch()

        layout.addLayout(header)

        self.content_splitter = QSplitter(Qt.Horizontal)
        self.content_splitter.setObjectName("SpecConversionSplitter")
        self.content_splitter.setChildrenCollapsible(False)

        self.left_panel = QWidget()
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        input_panel = QFrame()
        input_panel.setObjectName("Panel")
        input_layout = QVBoxLayout(input_panel)
        input_layout.setContentsMargins(14, 12, 14, 12)
        input_layout.setSpacing(9)

        self.source_line = QLineEdit()
        self.source_line.setObjectName("InputLine")
        self.source_line.setReadOnly(True)
        self.source_line.setPlaceholderText(".doc / .docx，或直接拖入此頁面")
        self.source_button = QPushButton("Browse")
        self.source_button.setObjectName("ToolButton")
        self.source_button.setIcon(
            self._standard_icon(QStyle.SP_DialogOpenButton)
        )
        self.source_button.clicked.connect(self.select_source)
        input_layout.addLayout(
            self._file_row("①", "台灣專利說明書", self.source_line, self.source_button)
        )

        self.output_line = QLineEdit()
        self.output_line.setObjectName("InputLine")
        self.output_line.setPlaceholderText("輸出 .docx")
        self.output_line.textEdited.connect(self._mark_output_manual)
        self.template_combo = QComboBox()
        self.template_combo.setObjectName("InputLine")
        for label, key in TEMPLATE_OPTIONS:
            self.template_combo.addItem(label, key)
        self.template_combo.currentIndexChanged.connect(
            self._template_changed
        )
        input_layout.addLayout(
            self._file_row(
                "②",
                "大陸專利說明書",
                self.output_line,
                self.template_combo,
            )
        )
        left_layout.addWidget(input_panel)

        self.preview_panel = QFrame()
        self.preview_panel.setObjectName("Panel")
        preview_layout = QVBoxLayout(self.preview_panel)
        preview_layout.setContentsMargins(10, 8, 10, 10)
        preview_layout.setSpacing(5)

        preview_header = QHBoxLayout()
        preview_header.setSpacing(6)
        preview_badge = QLabel("③")
        preview_badge.setObjectName("PanelTitle")
        preview_badge.setAlignment(Qt.AlignCenter)
        preview_badge.setMinimumWidth(30)
        preview_header.addWidget(preview_badge)
        preview_title = QLabel("大陸案格式預覽")
        preview_title.setObjectName("PanelTitle")
        preview_header.addWidget(preview_title)
        self.preview_status = QLabel("尚未載入文件")
        self.preview_status.setObjectName("SyncStatus")
        preview_header.addStretch()
        preview_legend = QLabel("紅字＝自動變更")
        preview_legend.setStyleSheet("color:#b91c1c; font-weight:700")
        self.dictionary_manager_button = QPushButton("大陸用語辭典")
        self.dictionary_manager_button.setObjectName("PrimaryButton")
        self.dictionary_manager_button.setIcon(
            self._standard_icon(QStyle.SP_FileDialogDetailedView)
        )
        self.dictionary_manager_button.setToolTip("開啟、編輯、同步或上傳大陸用語辭典")
        self.dictionary_manager_button.clicked.connect(
            self.open_dictionary_manager
        )
        preview_header.addWidget(self.dictionary_manager_button)
        preview_layout.addLayout(preview_header)
        preview_meta = QHBoxLayout()
        preview_meta.addWidget(self.preview_status)
        preview_meta.addStretch()
        preview_meta.addWidget(preview_legend)
        preview_layout.addLayout(preview_meta)

        self.preview_output = PlainOnlyPreviewEditor()
        self.preview_output.setObjectName("ResultText")
        self.preview_output.setReadOnly(False)
        self.preview_output.setPlaceholderText(
            "請選擇或將一份 Word 文件拖入此頁面，即可預覽並編輯大陸案章節"
        )
        self.preview_output.setToolTip(
            "可直接修改繁體文字；輸出 Word 時會以當前內容轉為簡體。"
        )
        self.preview_output.textChanged.connect(self._mark_preview_edited)
        preview_layout.addWidget(self.preview_output, 1)

        action_row = QHBoxLayout()
        action_row.addStretch()
        self.convert_button = QPushButton("轉換")
        self.convert_button.setObjectName("PrimaryButton")
        self.convert_button.setIcon(self._conversion_arrow_icon())
        self.convert_button.setIconSize(QSize(32, 32))
        self.convert_button.setStyleSheet("font-size:18pt; font-weight:700; color:#ffffff;")
        self.convert_button.setMinimumWidth(200)
        self.convert_button.setMinimumHeight(56)
        self.convert_button.clicked.connect(self.start_conversion)
        action_row.addWidget(self.convert_button)
        action_row.addStretch()
        preview_layout.addLayout(action_row)
        left_layout.addWidget(self.preview_panel, 1)

        self.right_panel = QSplitter(Qt.Vertical)
        self.right_panel.setObjectName("SpecReviewSidebar")
        self.right_panel.setChildrenCollapsible(False)
        self.right_panel.setMinimumWidth(220)
        self.right_panel.setMaximumWidth(340)
        self.article_review = ArticleReviewPanel(self.preview_output)
        self.right_panel.addWidget(self.article_review)

        self.message_panel = QFrame()
        self.message_panel.setObjectName("Panel")
        message_layout = QVBoxLayout(self.message_panel)
        message_layout.setContentsMargins(8, 7, 8, 8)
        message_layout.setSpacing(4)
        message_title = QLabel("訊息欄...")
        message_title.setObjectName("PanelTitle")
        message_layout.addWidget(message_title)
        self.message_output = QPlainTextEdit()
        self.message_output.setObjectName("ResultText")
        self.message_output.setReadOnly(True)
        self.message_output.setMinimumHeight(54)
        self.message_output.setPlainText("訊息欄...")
        message_layout.addWidget(self.message_output, 1)
        self.right_panel.addWidget(self.message_panel)
        self.right_panel.setStretchFactor(0, 4)
        self.right_panel.setStretchFactor(1, 1)
        self.right_panel.setSizes([560, 140])

        self.content_splitter.addWidget(self.left_panel)
        self.content_splitter.addWidget(self.right_panel)
        self.content_splitter.setStretchFactor(0, 4)
        self.content_splitter.setStretchFactor(1, 1)
        self.content_splitter.setSizes([1000, 280])
        layout.addWidget(self.content_splitter, 1)

    def _build_dictionary_dialog(self):
        self.dictionary_dialog = QDialog(self)
        self.dictionary_dialog.setObjectName("TerminologyDictionaryDialog")
        self.dictionary_dialog.setWindowTitle("大陸用語辭典")
        self.dictionary_dialog.setSizeGripEnabled(True)
        # QDialog otherwise inherits the native Windows dark window surface on
        # some company PCs while the table and labels keep the application's
        # light-theme colours.  Pin only this dialog to the same light panel
        # surface as the rest of Patent MDS so every header remains readable.
        self.dictionary_dialog.setStyleSheet(
            "#TerminologyDictionaryDialog {"
            " background-color: #eef3f8; color: #102a43;"
            "}"
        )

        screen = QApplication.primaryScreen()
        if screen is None:
            dialog_width, dialog_height = 820, 560
        else:
            available = screen.availableGeometry()
            dialog_width = min(820, max(1, available.width() - 48))
            dialog_height = min(560, max(1, available.height() - 64))
        self.dictionary_dialog.setMinimumSize(
            min(560, dialog_width),
            min(400, dialog_height),
        )
        self.dictionary_dialog.resize(dialog_width, dialog_height)

        dictionary_layout = QVBoxLayout(self.dictionary_dialog)
        dictionary_layout.setContentsMargins(10, 8, 10, 10)
        dictionary_layout.setSpacing(7)

        dictionary_header = QHBoxLayout()
        dictionary_header.setSpacing(6)
        dictionary_title = QLabel("大陸用語辭典")
        dictionary_title.setObjectName("PanelTitle")
        dictionary_header.addWidget(dictionary_title)

        self.dictionary_status = QLabel("● 內建")
        self.dictionary_status.setObjectName("SyncStatus")
        dictionary_header.addWidget(self.dictionary_status)
        dictionary_header.addStretch()

        self.add_dictionary_button = QPushButton("新增")
        self.add_dictionary_button.setObjectName("ToolButton")
        self.add_dictionary_button.setIcon(
            self._standard_icon(QStyle.SP_FileIcon)
        )
        self.add_dictionary_button.setToolTip("新增一組用語")
        self.add_dictionary_button.clicked.connect(self.add_dictionary_row)
        dictionary_header.addWidget(self.add_dictionary_button)

        self.delete_dictionary_button = QPushButton("刪除")
        self.delete_dictionary_button.setObjectName("ToolButton")
        self.delete_dictionary_button.setIcon(
            self._standard_icon(QStyle.SP_TrashIcon)
        )
        self.delete_dictionary_button.setToolTip("刪除選取的用語")
        self.delete_dictionary_button.setEnabled(False)
        self.delete_dictionary_button.clicked.connect(
            self.delete_selected_dictionary_rows
        )
        dictionary_header.addWidget(self.delete_dictionary_button)

        self.reload_dictionary_button = QPushButton("同步")
        self.reload_dictionary_button.setObjectName("ToolButton")
        self.reload_dictionary_button.setIcon(
            self._standard_icon(QStyle.SP_BrowserReload)
        )
        self.reload_dictionary_button.setToolTip("重新載入雲端預設辭典")
        self.reload_dictionary_button.clicked.connect(
            lambda _checked=False: self.reload_dictionary(force=True)
        )
        dictionary_header.addWidget(self.reload_dictionary_button)

        self.upload_dictionary_button = QPushButton("上傳")
        self.upload_dictionary_button.setObjectName("ToolButton")
        self.upload_dictionary_button.setIcon(
            self._standard_icon(QStyle.SP_DialogSaveButton)
        )
        self.upload_dictionary_button.setToolTip("上傳目前辭典供公司使用者同步")
        self.upload_dictionary_button.clicked.connect(
            self.upload_dictionary
        )
        dictionary_header.addWidget(self.upload_dictionary_button)
        dictionary_layout.addLayout(dictionary_header)

        self.dictionary_table = QTableWidget(0, 2)
        self.dictionary_table.setObjectName("TerminologyTable")
        self.dictionary_table.setHorizontalHeaderLabels(
            ["台灣用語", "大陸用語"]
        )
        self.dictionary_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self.dictionary_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self.dictionary_table.verticalHeader().setVisible(False)
        self.dictionary_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.dictionary_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.dictionary_table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.dictionary_table.setAlternatingRowColors(True)
        self.dictionary_table.itemChanged.connect(self._mark_dictionary_dirty)
        self.dictionary_table.itemSelectionChanged.connect(
            self._update_dictionary_delete_button
        )
        dictionary_layout.addWidget(self.dictionary_table, 1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_button = QPushButton("完成")
        close_button.setObjectName("SecondaryButton")
        close_button.clicked.connect(self.dictionary_dialog.accept)
        close_row.addWidget(close_button)
        dictionary_layout.addLayout(close_row)

    def open_dictionary_manager(self):
        screen = (
            QApplication.screenAt(self.mapToGlobal(self.rect().center()))
            or QApplication.primaryScreen()
        )
        if screen is not None:
            available = screen.availableGeometry()
            dialog_width = min(820, max(1, available.width() - 48))
            dialog_height = min(560, max(1, available.height() - 64))
            self.dictionary_dialog.setMinimumSize(
                min(560, dialog_width),
                min(400, dialog_height),
            )
            self.dictionary_dialog.resize(dialog_width, dialog_height)
        revision = self._dictionary_revision
        self.dictionary_dialog.exec()
        self._update_dictionary_manager_button()
        if revision != self._dictionary_revision:
            self.refresh_preview()

    def _file_row(self, step, label_text, line_edit, trailing_widget):
        row = QHBoxLayout()
        row.setSpacing(8)
        badge = QLabel(step)
        badge.setObjectName("PanelTitle")
        badge.setAlignment(Qt.AlignCenter)
        badge.setMinimumWidth(30)
        row.addWidget(badge)
        label = QLabel(label_text)
        label.setMinimumWidth(145)
        label.setObjectName("PanelTitle")
        row.addWidget(label)
        line_edit.setMaximumHeight(36)
        line_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row.addWidget(line_edit, 1)
        trailing_widget.setMinimumWidth(116)
        trailing_widget.setMaximumHeight(36)
        row.addWidget(trailing_widget)
        return row

    def _mark_output_manual(self, _text):
        self._output_is_automatic = False

    def _suggest_output_path(self):
        source_text = self.source_line.text().strip()
        if not source_text:
            return
        source = Path(source_text)
        template_label = self.template_combo.currentText()
        self.output_line.setText(
            str(source.with_name(f"{source.stem}-{template_label}.docx"))
        )

    def _template_changed(self, _index):
        if self._output_is_automatic:
            self._suggest_output_path()
        self.refresh_preview()

    def select_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "台灣專利說明書",
            self.source_line.text(),
            "Word 文件 (*.doc *.docx)",
        )
        if not path:
            return
        self.load_source(path)

    def load_source(self, source_path):
        if self._closing or self._conversion_running:
            return False
        path = Path(source_path)
        if path.suffix.lower() not in {".doc", ".docx"}:
            self._show_input_error("台陸轉換頁面僅接受 Word 文件（.doc / .docx）。")
            return False
        resolved_path = _source_key(path)
        replacing_source = resolved_path != self._preview_source
        self.source_line.setText(str(path))
        self._output_is_automatic = True
        self._suggest_output_path()
        return self.refresh_preview(force=replacing_source)

    def refresh_preview(self, force=False):
        if self._closing or self._conversion_running:
            return False
        source = self.source_line.text().strip()
        if not source:
            return False
        resolved_source = _source_key(source)
        if (
            self._preview_dirty
            and self._preview_source == resolved_source
            and not force
        ):
            # Dictionary/template refreshes must never silently discard text
            # the user has already revised in the preview editor.
            # Returning to that source while another file is still loading
            # must also invalidate the other file's in-flight result.
            if self._preview_thread is not None or self._pending_preview is not None:
                self._preview_revision += 1
                self._pending_preview = None
                if self._preview_worker is not None:
                    self._preview_worker.cancelled.set()
            self.preview_status.setText("● 已人工修訂")
            self.preview_status.setStyleSheet("color:#b45309")
            return True
        try:
            terminology_pairs = self._dictionary_pairs_from_table()
        except ConversionError as exc:
            self._show_input_error(str(exc))
            return False

        self.preview_status.setText("● 讀取中")
        self.preview_status.setStyleSheet("color:#1d4ed8")
        self._preview_revision += 1
        # Keep a single active job and coalesce quick document/template changes
        # into the newest request. An old result must never overwrite a new file.
        self._pending_preview = (
            self._preview_revision, source, self.template_combo.currentData(),
            tuple(terminology_pairs),
        )
        if self._preview_worker is not None:
            self._preview_worker.cancelled.set()
        self.preview_output.setEnabled(False)
        self.article_review.setEnabled(False)
        self.convert_button.setEnabled(False)
        self._start_pending_preview()
        return True

    def _start_pending_preview(self):
        if self._closing or self._preview_thread is not None or self._pending_preview is None:
            return
        request, self._pending_preview = self._pending_preview, None
        self._preview_worker = PreviewWorker(*request)
        self._preview_worker.completed.connect(self._preview_completed)
        self._preview_worker.failed.connect(self._preview_failed)
        self._preview_thread = _start_worker(self._preview_worker, self._preview_finished)

    @Slot(int, object, str)
    def _preview_completed(self, revision, preview, html):
        if self._closing or revision != self._preview_revision:
            return
        self._render_preview(preview, html)
        changed_paragraphs = sum(
            1
            for paragraph in preview.paragraphs
            if paragraph.role != "heading"
            and paragraph.source_text != paragraph.converted_text
        )
        self.preview_status.setText(f"● {changed_paragraphs} 個段落有變更")
        self.preview_status.setStyleSheet("color:#18794e")
        message = "✓ 已讀取台灣專利說明書並產生大陸案預覽"
        if preview.warnings:
            message += "\n" + "\n".join(
                f"⚠ {warning}" for warning in preview.warnings
            )
        self.message_output.setPlainText(message)
    @Slot(int, str)
    def _preview_failed(self, revision, message):
        if self._closing or revision != self._preview_revision:
            return
        self._preview_internal_update = True
        self.article_review._internal_change = True
        try:
            self.preview_output.clear()
        finally:
            self._preview_internal_update = False
            self.article_review._internal_change = False
        self._preview_source = ""
        self._preview_dirty = False
        self._preview_fixed_content_layout = False
        self._content_claims_baseline = ()
        self._content_claim_history = []
        self.article_review.reset()
        self.preview_status.setText("● 無法預覽")
        self.preview_status.setStyleSheet("color:#b91c1c")
        self.message_output.setPlainText(f"✕ 預覽失敗\n{message}")

    @Slot()
    def _preview_finished(self):
        self._preview_worker = None
        self._preview_thread = None
        if self._closing:
            return
        if self._pending_preview is not None:
            self._start_pending_preview()
            return
        self.preview_output.setEnabled(not self._conversion_running)
        self.article_review.setEnabled(not self._conversion_running)
        self.convert_button.setEnabled(bool(self._preview_source) and not self._conversion_running)
        self.article_review._update_buttons()

    def _mark_preview_edited(self):
        if self._preview_internal_update or not self._preview_source:
            return
        self._preview_dirty = True
        self.preview_status.setText("● 已人工修訂")
        self.preview_status.setStyleSheet("color:#b45309")

    def replace_content_from_claims(self):
        """Explicitly overwrite content from the current reviewed claims only."""
        if self._closing or self._conversion_running:
            return False
        if not self.preview_output.isEnabled() or self.preview_output.isReadOnly():
            return False
        if self._preview_thread is not None or self._pending_preview is not None:
            self._show_input_error("文件預覽仍在讀取中，請完成後再更新內容。")
            return False
        if not self._preview_source or self._preview_source != _source_key(self.source_line.text().strip()):
            self._show_input_error("請先載入並確認目前文件的預覽。")
            return False
        current_text = self.preview_output.toPlainText()
        try:
            result = replace_content_from_edited_claims(
                current_text, self._preview_specification_kind,
                previous_claims=self._content_claims_baseline,
                claim_history=self._content_claim_history,
                fixed_content_layout=self._preview_fixed_content_layout,
            )
        except ConversionError as exc:
            self._show_input_error(f"未變更原內容：{exc}")
            return False
        changed = self.article_review.apply_text_edits(
            result.edits, current_text,
            content_edit=(result.content_start, result.content_end, result.replacement_text),
        )
        if not changed:
            current_claims = tuple(
                parse_edited_preview_text(
                    current_text, self._preview_specification_kind
                ).claims
            )
            if current_claims not in self._content_claim_history:
                self._content_claim_history.append(current_claims)
            self.message_output.setPlainText("✓ 發明／實用新型內容已與目前權利要求一致，無須重複更新。")
            return False
        current_claims = tuple(
            parse_edited_preview_text(result.text, self._preview_specification_kind).claims
        )
        if current_claims not in self._content_claim_history:
            self._content_claim_history.append(current_claims)
            del self._content_claim_history[:-50]
        self._mark_preview_edited()
        lines = [
            f"✓ 已依人工修訂後的 {result.claim_count} 項權利要求更新內容中的 claim 段落。",
            "原有功效、其他說明、其他章節與權利要求書均保持不變；可按 Ctrl+Z 復原。本操作尚未寫入 Word。",
        ]
        lines.extend(f"⚠ {warning}" for warning in result.warnings)
        self.message_output.setPlainText("\n".join(lines))
        return True

    @staticmethod
    def _converted_text_html(source_text, converted_text):
        if source_text == converted_text:
            # Identical repetitive paragraphs are SequenceMatcher's worst case;
            # the exact same rendering needs no diff at all.
            return escape(converted_text or "").replace("\n", "<br>")
        pieces = []
        matcher = SequenceMatcher(
            None,
            source_text or "",
            converted_text or "",
            autojunk=False,
        )
        for operation, _left_start, _left_end, right_start, right_end in matcher.get_opcodes():
            text = converted_text[right_start:right_end]
            if not text:
                continue
            rendered = escape(text).replace("\n", "<br>")
            if operation == "equal":
                pieces.append(rendered)
            else:
                pieces.append(
                    '<span style="color:#c62828; font-weight:700">'
                    f"{rendered}</span>"
                )
        return "".join(pieces)

    @staticmethod
    def _preview_html(preview):
        paragraphs = [
            '<div style="font-family:\'Microsoft JhengHei\'; '
            'font-size:15px; line-height:1.65; color:#111827">'
        ]
        for paragraph in preview.paragraphs:
            if paragraph.role == "heading":
                paragraphs.append(
                    '<p style="margin:14px 0 6px 0; font-size:17px; '
                    'font-weight:700; color:#0f2742">'
                    f"{escape(paragraph.converted_text)}</p>"
                )
                continue

            rendered = TaiwanChinaSpecPage._converted_text_html(
                paragraph.source_text,
                paragraph.converted_text,
            )
            prefix = escape(paragraph.prefix)
            if paragraph.role == "title":
                paragraphs.append(
                    '<p style="margin:7px 0 10px 0; text-align:center; '
                    f'font-weight:700">{rendered}</p>'
                )
            else:
                paragraphs.append(
                    f'<p style="margin:5px 0">{prefix}{rendered}</p>'
                )
        paragraphs.append("</div>")
        return "".join(paragraphs)

    def _render_preview(self, preview, html=None):
        self._preview_internal_update = True
        self.article_review._internal_change = True
        try:
            self.preview_output.setHtml(html if html is not None else self._preview_html(preview))
            self.preview_output.document().setModified(False)
            self.preview_output.document().clearUndoRedoStacks()
        finally:
            self._preview_internal_update = False
            self.article_review._internal_change = False
        self._preview_dirty = False
        self._preview_source = _source_key(preview.source)
        self._preview_specification_kind = preview.specification_kind
        self._preview_fixed_content_layout = bool(
            getattr(preview, "fixed_content_layout", False)
        )
        try:
            self._content_claims_baseline = tuple(
                parse_edited_preview_text(
                    self.preview_output.toPlainText(), preview.specification_kind
                ).claims
            )
        except ConversionError:
            # Lightweight test/status previews need not contain the complete
            # editable patent structure; they simply have no claim baseline.
            self._content_claims_baseline = ()
        self._content_claim_history = [self._content_claims_baseline]
        self.article_review.reset()

    def _apply_dictionary_snapshot(self, snapshot):
        self._populating_dictionary = True
        self.dictionary_table.blockSignals(True)
        self.dictionary_table.setUpdatesEnabled(False)
        try:
            self.dictionary_table.setRowCount(len(snapshot.pairs))
            for row, (source, target) in enumerate(snapshot.pairs):
                self.dictionary_table.setItem(row, 0, QTableWidgetItem(source))
                self.dictionary_table.setItem(row, 1, QTableWidgetItem(target))
        finally:
            self.dictionary_table.setUpdatesEnabled(True)
            self.dictionary_table.blockSignals(False)
            self._populating_dictionary = False
        self._dictionary_revision += 1
        color = {
            "雲端": "#18794e",
            "快取": "#1d4ed8",
            "內建": "#475569",
        }.get(snapshot.source_kind, "#475569")
        self.dictionary_status.setText(f"● {snapshot.source_kind}")
        self.dictionary_status.setStyleSheet(f"color:{color}")
        self._update_dictionary_delete_button()
        self._update_dictionary_manager_button()
        message = f"✓ 已載入{snapshot.source_kind}辭典：{len(snapshot.pairs)} 組"
        if snapshot.warning:
            message += f"\n⚠ {snapshot.warning}"
        self.message_output.setPlainText(message)
        self.refresh_preview()

    def _mark_dictionary_dirty(self, _item=None):
        if self._populating_dictionary:
            return
        self._dictionary_revision += 1
        self.dictionary_status.setText("● 已修訂")
        self.dictionary_status.setStyleSheet("color:#b45309")
        self._update_dictionary_manager_button()

    def _update_dictionary_manager_button(self):
        self.dictionary_manager_button.setText(
            f"大陸用語辭典（{self.dictionary_table.rowCount()}）"
        )

    def _dictionary_pairs_from_table(self):
        pairs = []
        for row in range(self.dictionary_table.rowCount()):
            source_item = self.dictionary_table.item(row, 0)
            target_item = self.dictionary_table.item(row, 1)
            source = source_item.text().strip() if source_item else ""
            target = target_item.text().strip() if target_item else ""
            if not source and not target:
                continue
            pairs.append((source, target))
        return normalize_terminology_pairs(pairs)

    def add_dictionary_row(self):
        row = self.dictionary_table.rowCount()
        self.dictionary_table.blockSignals(True)
        try:
            self.dictionary_table.insertRow(row)
            self.dictionary_table.setItem(row, 0, QTableWidgetItem(""))
            self.dictionary_table.setItem(row, 1, QTableWidgetItem(""))
        finally:
            self.dictionary_table.blockSignals(False)
        self._mark_dictionary_dirty()
        self.dictionary_table.setCurrentCell(row, 0)
        self.dictionary_table.editItem(self.dictionary_table.item(row, 0))

    def delete_selected_dictionary_rows(self):
        rows = sorted(
            {
                index.row()
                for index in self.dictionary_table.selectionModel().selectedRows()
            },
            reverse=True,
        )
        if not rows:
            return
        self.dictionary_table.blockSignals(True)
        try:
            for row in rows:
                self.dictionary_table.removeRow(row)
        finally:
            self.dictionary_table.blockSignals(False)
        self._mark_dictionary_dirty()
        self._update_dictionary_delete_button()

    def _update_dictionary_delete_button(self):
        selected = bool(
            self.dictionary_table.selectionModel().selectedRows()
        )
        self.delete_dictionary_button.setEnabled(
            selected and not self._conversion_running
        )

    def reload_dictionary(self, force=True):
        if (
            self._closing
            or self._dictionary_thread is not None
            or self._dictionary_upload_thread is not None
        ):
            return
        self._dictionary_load_revision = self._dictionary_revision
        self._dictionary_force_apply = bool(force)
        self.dictionary_status.setText("● 同步中")
        self.dictionary_status.setStyleSheet("color:#1d4ed8")
        self.reload_dictionary_button.setEnabled(False)
        self.upload_dictionary_button.setEnabled(False)

        self._dictionary_worker = TerminologyLoadWorker(self.dictionary_store)
        self._dictionary_worker.completed.connect(self._dictionary_loaded)
        self._dictionary_worker.failed.connect(self._dictionary_load_failed)
        self._dictionary_thread = _start_worker(
            self._dictionary_worker, self._dictionary_load_finished
        )

    @Slot(object)
    def _dictionary_loaded(self, snapshot):
        if self._closing:
            return
        should_apply = (
            self._dictionary_force_apply
            or self._dictionary_revision == self._dictionary_load_revision
        )
        if should_apply:
            self._apply_dictionary_snapshot(snapshot)
        else:
            self.dictionary_status.setText("● 已修訂")
            self.dictionary_status.setStyleSheet("color:#b45309")

    @Slot(str)
    def _dictionary_load_failed(self, message):
        if self._closing:
            return
        self.dictionary_status.setText("● 內建")
        self.dictionary_status.setStyleSheet("color:#b45309")
        self.message_output.setPlainText(f"⚠ 雲端辭典同步失敗\n{message}")

    @Slot()
    def _dictionary_load_finished(self):
        self._dictionary_worker = None
        self._dictionary_thread = None
        if self._closing:
            return
        enabled = (
            not self._conversion_running
            and self._dictionary_upload_thread is None
        )
        self.reload_dictionary_button.setEnabled(enabled)
        self.upload_dictionary_button.setEnabled(enabled)

    def upload_dictionary(self):
        if (
            self._closing
            or self._dictionary_upload_thread is not None
            or self._dictionary_thread is not None
        ):
            return
        try:
            terminology_pairs = self._dictionary_pairs_from_table()
        except ConversionError as exc:
            self._show_input_error(str(exc))
            return

        self._dictionary_upload_revision = self._dictionary_revision
        self.dictionary_status.setText("● 上傳中")
        self.dictionary_status.setStyleSheet("color:#1d4ed8")
        self.message_output.setPlainText("↑ 正在上傳公司辭典...")
        self.reload_dictionary_button.setEnabled(False)
        self.upload_dictionary_button.setEnabled(False)

        self._dictionary_upload_worker = TerminologyUploadWorker(
            self.dictionary_store,
            terminology_pairs,
        )
        self._dictionary_upload_worker.completed.connect(
            self._dictionary_uploaded
        )
        self._dictionary_upload_worker.failed.connect(
            self._dictionary_upload_failed
        )
        self._dictionary_upload_thread = _start_worker(
            self._dictionary_upload_worker, self._dictionary_upload_finished
        )

    @Slot(object)
    def _dictionary_uploaded(self, snapshot):
        if self._closing:
            return
        if self._dictionary_revision == self._dictionary_upload_revision:
            self.dictionary_status.setText("● 雲端")
            self.dictionary_status.setStyleSheet("color:#18794e")
        else:
            self.dictionary_status.setText("● 已修訂")
            self.dictionary_status.setStyleSheet("color:#b45309")

        message = f"✓ 已上傳公司辭典：{len(snapshot.pairs)} 組"
        if self._dictionary_revision != self._dictionary_upload_revision:
            message += "\n⚠ 上傳後又有新修訂，請再次上傳。"
        if snapshot.warning:
            message += f"\n⚠ {snapshot.warning}"
        self.message_output.setPlainText(message)

    @Slot(str)
    def _dictionary_upload_failed(self, message):
        if self._closing:
            return
        self.dictionary_status.setText("● 已修訂")
        self.dictionary_status.setStyleSheet("color:#b45309")
        self.message_output.setPlainText(f"✕ 辭典上傳失敗\n{message}")

    @Slot()
    def _dictionary_upload_finished(self):
        self._dictionary_upload_worker = None
        self._dictionary_upload_thread = None
        if self._closing:
            return
        enabled = (
            not self._conversion_running
            and self._dictionary_thread is None
        )
        self.reload_dictionary_button.setEnabled(enabled)
        self.upload_dictionary_button.setEnabled(enabled)

    def start_conversion(self):
        if self._closing or self._conversion_running:
            return
        if self._preview_thread is not None or self._pending_preview is not None:
            self._show_input_error("文件預覽仍在讀取中，請完成後再轉換。")
            return
        source = self.source_line.text().strip()
        output = self.output_line.text().strip()
        if not source:
            self._show_input_error("請選擇台灣專利說明書。")
            return
        if not output:
            self._show_input_error("請輸入大陸專利說明書輸出位置。")
            return
        try:
            terminology_pairs = self._dictionary_pairs_from_table()
        except ConversionError as exc:
            self._show_input_error(str(exc))
            return
        resolved_source = _source_key(source)
        if self._preview_source != resolved_source:
            self.refresh_preview(force=True)
            return
        edited_preview_text = self.preview_output.toPlainText()
        try:
            parse_edited_preview_text(
                edited_preview_text,
                self._preview_specification_kind,
            )
        except ConversionError as exc:
            self._show_input_error(str(exc))
            return
        if Path(output).suffix.lower() != ".docx":
            output = str(Path(output).with_suffix(".docx"))
            self.output_line.setText(output)

        self._conversion_running = True
        self._guidance_conversion_snapshot = None
        self._set_controls_enabled(False)
        self.message_output.setPlainText("▶ 正在轉換...")
        self._conversion_worker = SpecConversionWorker(
            source,
            output,
            self.template_combo.currentData(),
            terminology_pairs,
            edited_preview_text,
        )
        self._conversion_worker.completed.connect(self._conversion_completed)
        self._conversion_worker.failed.connect(self._conversion_failed)
        self._conversion_thread = _start_worker(
            self._conversion_worker, self._conversion_finished
        )

    def _set_controls_enabled(self, enabled):
        for widget in (
            self.source_button,
            self.output_line,
            self.template_combo,
            self.dictionary_manager_button,
            self.dictionary_table,
            self.add_dictionary_button,
            self.delete_dictionary_button,
            self.reload_dictionary_button,
            self.upload_dictionary_button,
            self.preview_output,
            self.article_review,
            self.convert_button,
        ):
            widget.setEnabled(enabled)
        self._update_dictionary_delete_button()
        self.article_review._update_buttons()
        dictionary_enabled = (
            enabled and self._dictionary_thread is None
            and self._dictionary_upload_thread is None
        )
        self.reload_dictionary_button.setEnabled(dictionary_enabled)
        self.upload_dictionary_button.setEnabled(dictionary_enabled)

    @Slot(object)
    def _conversion_completed(self, report):
        if self._closing:
            return
        from features.workflow_pet.guidance import conversion_scope
        self._guidance_conversion_snapshot = conversion_scope(self)
        replacement_total = sum(report.replacement_counts.values())
        lines = [
            "✓ 轉換完成",
            f"📄 {report.output}",
            f"✓ 權利要求：{report.claim_count} 項",
            f"✓ 用語處理：{replacement_total} 處",
        ]
        lines.extend(f"⚠ {warning}" for warning in report.warnings)
        lines.append("ⓘ 正式電子送件前請複核內容並轉為 CNIPA XML。")
        self.message_output.setPlainText("\n".join(lines))

    @Slot(str)
    def _conversion_failed(self, message):
        if self._closing:
            return
        self.message_output.setPlainText(f"✕ 轉換失敗\n{message}")

    @Slot()
    def _conversion_finished(self):
        self._conversion_running = False
        self._conversion_worker = None
        self._conversion_thread = None
        if not self._closing:
            self._set_controls_enabled(True)

    def shutdown(self):
        """Detach the UI immediately; never wait for Word/SMB on the GUI thread."""
        self._closing = True
        self._preview_revision += 1
        self._pending_preview = None
        if self._preview_worker is not None:
            self._preview_worker.cancelled.set()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def _show_input_error(self, message):
        self.message_output.setPlainText(f"⚠ {message}")
