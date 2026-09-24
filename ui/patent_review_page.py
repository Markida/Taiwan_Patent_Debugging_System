from html import escape
from pathlib import Path
import re
from collections import Counter
from dataclasses import replace

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from features.patent_review import (
    CustomRuleError,
    CustomTextRule,
    CustomTextRuleStore,
    DocumentSimilarityWhitelistStore,
    FULL_SYMBOL_SOURCE,
    REPRESENTATIVE_SYMBOL_SOURCE,
    PatentDocxError,
    extract_document_symbols,
    parse_docx,
    rebuild_transfer_from_reference_texts,
    review_document,
)
from features.patent_review.custom_rules import CUSTOM_RULE_WHITELIST
from ui.custom_text_rule_dialog import CustomTextRuleDialog
from ui.feature_navigation import FeatureNavigationBar
from ui.file_drop import SingleFileDropController
from app.background_tasks import BackgroundTaskRunner


EMPTY_DOCUMENT_PROMPT = "請拖選專利文件進入視窗或透過上方欄位瀏覽"
ISSUE_TYPE_WRAP_WIDTH = 30


def _wrap_text_by_character_count(text, width=ISSUE_TYPE_WRAP_WIDTH):
    """Insert display-only line breaks without changing the issue message."""

    wrapped_lines = []
    for source_line in str(text).split("\n"):
        if not source_line:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(
            source_line[index:index + width]
            for index in range(0, len(source_line), width)
        )
    return "\n".join(wrapped_lines)


class CenteredPlaceholderTableWidget(QTableWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.centered_placeholder = ""

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.centered_placeholder and self.rowCount() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor("#64748b"))
            painter.drawText(
                self.viewport().rect().adjusted(24, 24, -24, -24),
                Qt.AlignCenter | Qt.TextWordWrap,
                self.centered_placeholder,
            )


class CenteredPlaceholderTextEdit(QTextEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.centered_placeholder = ""

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.centered_placeholder and not self.toPlainText():
            painter = QPainter(self.viewport())
            painter.setPen(QColor("#64748b"))
            painter.drawText(
                self.viewport().rect().adjusted(24, 24, -24, -24),
                Qt.AlignCenter | Qt.TextWordWrap,
                self.centered_placeholder,
            )


class PatentReviewPage(QWidget):
    """Read-only DOCX review page and controlled symbol-list handoff."""

    def __init__(
        self,
        go_home_callback,
        custom_rule_store=None,
        document_whitelist_store=None,
    ):
        super().__init__()
        self.go_home_callback = go_home_callback
        self.open_feature_callback = None
        self.workflow_context = None
        self.document = None
        self.review = None
        self.symbol_transfer = None
        self.issue_by_id = {}
        self.custom_rule_store = custom_rule_store or CustomTextRuleStore()
        self.document_whitelist_store = (
            document_whitelist_store or DocumentSimilarityWhitelistStore()
        )
        self.custom_rules = []
        self.custom_rule_load_error = ""
        self.document_whitelist_terms = []
        self.document_whitelist_load_error = ""
        self._document_whitelist_key = ""
        self._paragraph_map = {}
        self._paragraph_map_document = None
        self._custom_rule_generation = 0
        self._initial_rules_pending = True
        self._rules_tasks = BackgroundTaskRunner(self)
        self._rules_tasks.succeeded.connect(self._on_custom_rules_ready)
        self._review_tasks = BackgroundTaskRunner(self)
        self._review_tasks.succeeded.connect(self._on_review_ready)
        self._review_tasks.failed.connect(self._on_review_failed)
        self._review_tasks.finished.connect(lambda: self._set_review_busy(False))
        self.build_ui()
        self.file_drop_controller = SingleFileDropController(
            self,
            allowed_suffix=".docx",
            file_type_name="Word 文件",
            on_file=self.load_document,
        )
        QTimer.singleShot(0, self._start_custom_rules_refresh)

    def set_workflow_context(self, workflow_context):
        self.workflow_context = workflow_context

    def set_open_feature_callback(self, callback):
        self.open_feature_callback = callback

    def set_feature_navigation(self, features, open_feature_callback):
        self.feature_navigation.configure(features, open_feature_callback)

    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 8)
        layout.setSpacing(3)

        self.feature_navigation = FeatureNavigationBar(
            self.go_home_callback,
            "patent_review",
        )
        layout.addWidget(self.feature_navigation)

        self.file_header_layout = QHBoxLayout()
        self.file_header_layout.setSpacing(6)
        title = QLabel("專利文件偵錯")
        title.setObjectName("PageTitle")
        title.setMaximumHeight(34)
        self.file_header_layout.addWidget(title)
        self.file_header_layout.addSpacing(8)

        self.file_header_layout.addWidget(QLabel("Word 文件："))
        self.file_line = QLineEdit()
        self.file_line.setObjectName("InputLine")
        self.file_line.setReadOnly(True)
        self.file_line.setMaximumHeight(34)
        self.file_line.setPlaceholderText(
            "請選擇完整專利說明書 .docx，或將檔案拖入此視窗"
        )
        self.file_header_layout.addWidget(self.file_line, 1)
        self.select_button = QPushButton("選擇 DOCX")
        self.select_button.setObjectName("ToolButton")
        self.select_button.setMaximumHeight(34)
        self.select_button.clicked.connect(self.select_docx)
        self.file_header_layout.addWidget(self.select_button)
        layout.addLayout(self.file_header_layout)

        self.status_label = QLabel(
            "可選擇或直接拖入 DOCX；載入後會執行文字規則，並擷取完整與代表圖兩套符號清單。"
        )
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setMaximumHeight(38)
        self.status_label.setContentsMargins(6, 0, 6, 0)
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # Status remains available to workflow code and diagnostics, but the
        # verbose "已擷取完整符號…" row no longer occupies the production UI.
        self.status_label.setVisible(False)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setObjectName("MainSplitter")
        self.main_splitter.setChildrenCollapsible(False)

        issue_panel = QFrame()
        issue_panel.setObjectName("Panel")
        issue_layout = QVBoxLayout(issue_panel)
        issue_layout.setContentsMargins(8, 6, 8, 8)
        issue_layout.setSpacing(3)
        issue_header = QHBoxLayout()
        issue_title = QLabel("文件檢核結果")
        issue_title.setObjectName("PanelTitle")
        self.issue_summary = QLabel("尚未載入文件")
        issue_header.addWidget(issue_title)
        issue_header.addStretch()
        self.custom_rules_button = QPushButton(
            f"自訂文字規則（{len(self.custom_rules)}）"
        )
        self.custom_rules_button.setObjectName("PrimaryButton")
        self.custom_rules_button.setToolTip(
            f"新增或刪除特定文字偵測規則\n儲存位置：{self.custom_rule_store.path}"
        )
        self.custom_rules_button.clicked.connect(self.open_custom_rule_manager)
        issue_header.addWidget(self.custom_rules_button)
        self.syntax_lab_button = QPushButton("語法對照（內測）")
        self.syntax_lab_button.setObjectName("ToolButton")
        self.syntax_lab_button.setToolTip("手動唯讀候選對照；不影響正式錯誤清單")
        self.syntax_lab_button.clicked.connect(self.open_syntax_lab)
        issue_header.addWidget(self.syntax_lab_button)
        issue_header.addWidget(self.issue_summary)
        issue_layout.addLayout(issue_header)

        self.issue_table = CenteredPlaceholderTableWidget(0, 4)
        self.issue_table.setObjectName("ReviewTable")
        review_font = self.issue_table.font()
        review_font.setPointSizeF(12.0)
        self.issue_table.setFont(review_font)
        self.issue_table.horizontalHeader().setFont(review_font)
        self.issue_table.setHorizontalHeaderLabels(
            ["等級", "章節", "位置", "錯誤種類"]
        )
        self.issue_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.issue_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.issue_table.setWordWrap(True)
        self.issue_table.verticalHeader().setVisible(False)
        for column in (0, 1, 2):
            self.issue_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeToContents
            )
        self.issue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.issue_table.itemSelectionChanged.connect(
            self.on_issue_selection_changed
        )

        self.issue_workspace_splitter = QSplitter(Qt.Vertical)
        self.issue_workspace_splitter.setChildrenCollapsible(False)
        self.issue_workspace_splitter.addWidget(self.issue_table)

        location_panel = QFrame()
        location_panel.setObjectName("ErrorLocationPanel")
        location_panel.setStyleSheet(
            "#ErrorLocationPanel { background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 7px; }"
        )
        location_layout = QVBoxLayout(location_panel)
        location_layout.setContentsMargins(8, 6, 8, 8)
        location_layout.setSpacing(4)

        self.selected_issue_title = QLabel("錯誤原文定位")
        self.selected_issue_title.setObjectName("PanelTitle")
        location_layout.addWidget(self.selected_issue_title)

        self.original_paragraph_view = CenteredPlaceholderTextEdit()
        self.original_paragraph_view.setReadOnly(True)
        self.original_paragraph_view.setMinimumHeight(190)
        context_font = self.original_paragraph_view.font()
        context_font.setPointSizeF(14.0)
        self.original_paragraph_view.setFont(context_font)
        self.original_paragraph_view.document().setDefaultFont(context_font)
        location_layout.addWidget(self.original_paragraph_view, 1)

        self.issue_workspace_splitter.addWidget(location_panel)
        self.issue_workspace_splitter.setSizes([350, 310])
        self.issue_workspace_splitter.setStretchFactor(0, 1)
        self.issue_workspace_splitter.setStretchFactor(1, 1)
        issue_layout.addWidget(self.issue_workspace_splitter)

        self.symbol_panel = QFrame()
        self.symbol_panel.setObjectName("Panel")
        symbol_layout = QVBoxLayout(self.symbol_panel)
        symbol_layout.setContentsMargins(8, 6, 8, 8)
        symbol_layout.setSpacing(3)
        symbol_header = QHBoxLayout()
        symbol_title = QLabel("送往圖片 OCR 的標號清單")
        symbol_title.setObjectName("PanelTitle")
        symbol_header.addWidget(symbol_title)
        symbol_header.addStretch()
        self.send_button = QPushButton("確認清單並前往圖片標號識別")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self.publish_to_ocr)
        symbol_header.addWidget(self.send_button)
        symbol_layout.addLayout(symbol_header)

        self.symbol_tabs = QTabWidget()
        self.symbol_tabs.setObjectName("SymbolSourceTabs")
        self.symbol_tabs.tabBar().setObjectName("SymbolSourceTabBar")
        self.symbol_tabs.tabBar().setExpanding(True)
        self.symbol_tabs.tabBar().setUsesScrollButtons(False)
        self.symbol_tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        self.symbol_tabs.setStyleSheet(
            "QTabWidget#SymbolSourceTabs::pane {"
            " background: #ffffff; border: 1px solid #b8c8d8;"
            " border-radius: 0 0 10px 10px; top: -1px;"
            "}"
            "QTabBar#SymbolSourceTabBar { background: transparent; }"
            "QTabBar#SymbolSourceTabBar::tab {"
            " background: #f8fafc; color: #18324b;"
            " font-family: 'Microsoft JhengHei', 'Noto Sans TC', Arial;"
            " font-size: 12.5pt; font-weight: 700;"
            " min-height: 32px; padding: 5px 14px;"
            " border: 1px solid #b8c8d8; border-bottom: 2px solid #8fa7bf;"
            "}"
            "QTabBar#SymbolSourceTabBar::tab:first {"
            " border-top-left-radius: 8px;"
            "}"
            "QTabBar#SymbolSourceTabBar::tab:last {"
            " border-top-right-radius: 8px;"
            "}"
            "QTabBar#SymbolSourceTabBar::tab:selected {"
            " background: #0f4c81; color: #ffffff; font-weight: 800;"
            " border-color: #0f4c81; border-bottom: 3px solid #60a5fa;"
            "}"
            "QTabBar#SymbolSourceTabBar::tab:hover:!selected {"
            " background: #e7eef6; color: #0f2742; border-color: #7893ad;"
            "}"
            "QTabBar#SymbolSourceTabBar::tab:disabled {"
            " background: #e5e7eb; color: #64748b; border-color: #cbd5e1;"
            "}"
        )
        self.full_symbol_text = CenteredPlaceholderTextEdit()
        self.full_symbol_text.setObjectName("ReferenceText")
        self.full_symbol_text.setPlaceholderText("完整符號說明，例如 10:主箱體")
        self.representative_symbol_text = CenteredPlaceholderTextEdit()
        self.representative_symbol_text.setObjectName("ReferenceText")
        self.representative_symbol_text.setPlaceholderText(
            "代表圖符號說明，例如 10:主箱體"
        )
        self.symbol_tabs.addTab(self.full_symbol_text, "完整符號說明")
        self.symbol_tabs.addTab(self.representative_symbol_text, "代表圖符號說明")
        symbol_layout.addWidget(self.symbol_tabs)

        document_whitelist_panel = QFrame()
        document_whitelist_panel.setObjectName("DocumentWhitelistPanel")
        document_whitelist_layout = QVBoxLayout(document_whitelist_panel)
        document_whitelist_layout.setContentsMargins(8, 6, 8, 8)
        document_whitelist_layout.setSpacing(4)

        document_whitelist_header = QHBoxLayout()
        self.document_whitelist_title = QLabel(
            "實施方式段落出現之無標號元件"
        )
        self.document_whitelist_title.setObjectName("DocumentWhitelistTitle")
        document_whitelist_header.addWidget(self.document_whitelist_title)
        document_whitelist_header.addStretch()
        self.reload_button = QPushButton("重新檢核")
        self.reload_button.setObjectName("PrimaryButton")
        self.reload_button.setEnabled(False)
        self.reload_button.setToolTip(
            "重新讀取目前 Word、共用規則及本文件排除元件，並重新執行全部檢核"
        )
        self.reload_button.clicked.connect(
            lambda _checked=False: self.reload_document()
        )
        document_whitelist_header.addWidget(self.reload_button)
        document_whitelist_layout.addLayout(document_whitelist_header)

        self.document_whitelist_hint = QLabel(
            "在此處手動新增之元件不參與模糊比對以及標號偵測"
        )
        self.document_whitelist_hint.setWordWrap(True)
        document_whitelist_layout.addWidget(self.document_whitelist_hint)

        document_whitelist_input_layout = QHBoxLayout()
        self.document_whitelist_input = QLineEdit()
        self.document_whitelist_input.setObjectName("DocumentWhitelistInput")
        self.document_whitelist_input.setPlaceholderText(
            "例如：第一位置、第一端、第二位置"
        )
        self.document_whitelist_input.setEnabled(False)
        self.document_whitelist_input.returnPressed.connect(
            self.add_document_whitelist_term
        )
        document_whitelist_input_layout.addWidget(
            self.document_whitelist_input,
            1,
        )
        self.add_document_whitelist_button = QPushButton("新增")
        self.add_document_whitelist_button.setObjectName("PrimaryButton")
        self.add_document_whitelist_button.setEnabled(False)
        self.add_document_whitelist_button.clicked.connect(
            lambda _checked=False: self.add_document_whitelist_term()
        )
        document_whitelist_input_layout.addWidget(
            self.add_document_whitelist_button
        )
        document_whitelist_layout.addLayout(document_whitelist_input_layout)

        self.document_whitelist_table = CenteredPlaceholderTableWidget(0, 1)
        self.document_whitelist_table.setObjectName("DocumentWhitelistTable")
        self.document_whitelist_table.setHorizontalHeaderLabels(
            ["不參與模糊比對及標號偵測的元件或詞語"]
        )
        self.document_whitelist_table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.Stretch,
        )
        self.document_whitelist_table.verticalHeader().setVisible(False)
        self.document_whitelist_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )
        self.document_whitelist_table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )
        self.document_whitelist_table.setSelectionMode(
            QAbstractItemView.ExtendedSelection
        )
        self.document_whitelist_table.itemSelectionChanged.connect(
            self._update_document_whitelist_delete_button
        )
        document_whitelist_layout.addWidget(self.document_whitelist_table, 1)

        self.delete_document_whitelist_button = QPushButton("刪除選取項目")
        self.delete_document_whitelist_button.setObjectName("PrimaryButton")
        self.delete_document_whitelist_button.setEnabled(False)
        self.delete_document_whitelist_button.clicked.connect(
            lambda _checked=False: self.delete_selected_document_whitelist_terms()
        )
        document_whitelist_layout.addWidget(
            self.delete_document_whitelist_button,
            0,
            Qt.AlignLeft,
        )

        self.right_splitter = QSplitter(Qt.Vertical)
        self.right_splitter.setObjectName("PatentReviewRightSplitter")
        self.right_splitter.setChildrenCollapsible(False)
        self.right_splitter.addWidget(self.symbol_panel)
        self.right_splitter.addWidget(document_whitelist_panel)
        # The document-specific exclusion list needs more vertical room than
        # before.  Keep the ratio-based splitter layout so it remains stable
        # across Windows DPI and screen-size changes.
        self.right_splitter.setSizes([390, 290])
        self.right_splitter.setStretchFactor(0, 4)
        self.right_splitter.setStretchFactor(1, 3)

        self.main_splitter.addWidget(issue_panel)
        self.main_splitter.addWidget(self.right_splitter)
        self.main_splitter.setSizes([760, 540])
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)
        layout.addWidget(self.main_splitter, 1)
        self._show_empty_document_state()

    def _show_empty_document_state(self):
        self.issue_table.centered_placeholder = EMPTY_DOCUMENT_PROMPT
        self.original_paragraph_view.centered_placeholder = EMPTY_DOCUMENT_PROMPT
        self.full_symbol_text.centered_placeholder = EMPTY_DOCUMENT_PROMPT
        self.representative_symbol_text.centered_placeholder = EMPTY_DOCUMENT_PROMPT
        self.document_whitelist_table.centered_placeholder = (
            "載入 Word 後可新增實施方式段落出現之無標號元件"
        )
        self.issue_table.viewport().update()
        self.original_paragraph_view.viewport().update()
        self.full_symbol_text.viewport().update()
        self.representative_symbol_text.viewport().update()
        self.document_whitelist_table.viewport().update()

    def _clear_empty_document_state(self):
        self.issue_table.centered_placeholder = ""
        self.original_paragraph_view.centered_placeholder = ""
        self.full_symbol_text.centered_placeholder = ""
        self.representative_symbol_text.centered_placeholder = ""
        self.document_whitelist_table.centered_placeholder = ""

    def select_docx(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "選擇完整專利說明書",
            "",
            "Word document (*.docx)",
        )
        if path:
            self.load_document(path)

    def reload_document(self):
        source_path = self.file_line.text().strip()
        if source_path:
            loaded = self.load_document(source_path)
            if loaded:
                self.status_label.setText(
                    self.status_label.text()
                    + " 已重新讀取 Word 並完成檢核。"
                )
            return loaded
        # ``show_review`` is also used by tests and internal callers that do
        # not populate the file field.  They can still re-run the current
        # parsed document and apply a newly edited whitelist.
        return self.rerun_review()

    def _reload_custom_rules(self):
        try:
            self.custom_rules = self.custom_rule_store.load()
            self.custom_rule_load_error = ""
            return True
        except CustomRuleError as error:
            self.custom_rules = []
            self.custom_rule_load_error = str(error)
            return False

    def _start_custom_rules_refresh(self):
        self._initial_rules_pending = False
        if self._review_tasks.busy:
            return
        store = self.custom_rule_store
        generation = self._custom_rule_generation

        def load(cancel):
            try:
                return generation, store.load(), ""
            except CustomRuleError as error:
                return generation, [], str(error)

        self._rules_tasks.start(load)

    def _on_custom_rules_ready(self, payload):
        generation, rules, error = payload
        if generation != self._custom_rule_generation:
            return
        self.custom_rules = rules
        self.custom_rule_load_error = error
        self._update_custom_rule_button()

    def _load_document_whitelist(self, document):
        self._document_whitelist_key = (
            self.document_whitelist_store.document_key(document)
        )
        try:
            self.document_whitelist_terms = (
                self.document_whitelist_store.load(document)
            )
            self.document_whitelist_load_error = ""
            self._populate_document_whitelist_table()
            return True
        except CustomRuleError as error:
            self.document_whitelist_terms = []
            self.document_whitelist_load_error = str(error)
            self._populate_document_whitelist_table()
            return False

    def _document_whitelist_rules(self):
        return [
            CustomTextRule.from_text(term, CUSTOM_RULE_WHITELIST)
            for term in self.document_whitelist_terms
        ]

    def _all_active_custom_rules(self):
        return [*self.custom_rules, *self._document_whitelist_rules()]

    def _populate_document_whitelist_table(self):
        if not hasattr(self, "document_whitelist_table"):
            return
        self.document_whitelist_table.setRowCount(0)
        for term in self.document_whitelist_terms:
            row = self.document_whitelist_table.rowCount()
            self.document_whitelist_table.insertRow(row)
            self.document_whitelist_table.setItem(
                row,
                0,
                QTableWidgetItem(term),
            )
        self._update_document_whitelist_delete_button()

    def _update_document_whitelist_delete_button(self):
        if not hasattr(self, "delete_document_whitelist_button"):
            return
        self.delete_document_whitelist_button.setEnabled(
            self.document is not None
            and not self._review_tasks.busy
            and bool(
                self.document_whitelist_table.selectionModel().selectedRows()
            )
        )

    def add_document_whitelist_term(self):
        if self.document is None or self._review_tasks.busy:
            return False
        try:
            term, created = self.document_whitelist_store.add(
                self.document,
                self.document_whitelist_input.text(),
            )
            self.document_whitelist_terms = (
                self.document_whitelist_store.load(self.document)
            )
            self.document_whitelist_load_error = ""
        except CustomRuleError as error:
            self.document_whitelist_hint.setText(str(error))
            self.document_whitelist_hint.setStyleSheet("color:#b91c1c")
            return False
        self._populate_document_whitelist_table()
        if not created:
            self.document_whitelist_hint.setText(
                f"「{term}」已存在於這份文件的排除清單。"
            )
            self.document_whitelist_hint.setStyleSheet("color:#b45309")
            return False
        self.document_whitelist_input.clear()
        self.document_whitelist_hint.setText(
            f"已新增「{term}」；按右上角「重新檢核」後套用。"
        )
        self.document_whitelist_hint.setStyleSheet("color:#111111")
        return True

    def delete_selected_document_whitelist_terms(self):
        if self.document is None or self._review_tasks.busy:
            return 0
        selected = []
        for index in self.document_whitelist_table.selectionModel().selectedRows():
            item = self.document_whitelist_table.item(index.row(), 0)
            if item is not None:
                selected.append(item.text())
        if not selected:
            return 0
        try:
            removed = self.document_whitelist_store.remove(
                self.document,
                selected,
            )
            self.document_whitelist_terms = (
                self.document_whitelist_store.load(self.document)
            )
            self.document_whitelist_load_error = ""
        except CustomRuleError as error:
            self.document_whitelist_hint.setText(str(error))
            self.document_whitelist_hint.setStyleSheet("color:#b91c1c")
            return 0
        self._populate_document_whitelist_table()
        self.document_whitelist_hint.setText(
            f"已刪除 {removed} 個詞語；按右上角「重新檢核」後套用。"
        )
        self.document_whitelist_hint.setStyleSheet("color:#111111")
        return removed

    def rerun_review(self):
        if self.document is None:
            return False
        return self._start_review(document=self.document)

    def _update_custom_rule_button(self):
        self.custom_rules_button.setText(
            f"自訂文字規則（{len(self.custom_rules)}）"
        )

    def open_custom_rule_manager(self):
        dialog = CustomTextRuleDialog(self.custom_rule_store, self)
        dialog.exec()
        self._custom_rule_generation += 1
        self._start_custom_rules_refresh()
        if dialog.rules_changed and self.document is not None:
            self.rerun_review()
        elif dialog.rules_changed:
            self.status_label.setText(
                f"已儲存 {len(self.custom_rules)} 條自訂文字規則；載入文件後會自動套用。"
            )

    def load_document(self, source_path):
        """Queue a read-only review; True means accepted, not yet completed."""
        return self._start_review(source_path=Path(source_path))

    def _start_review(self, *, source_path=None, document=None):
        if self._review_tasks.busy:
            return False
        self._custom_rule_generation += 1
        rule_store = self.custom_rule_store
        whitelist_store = self.document_whitelist_store
        ocr_results = (
            self.workflow_context.ocr_results
            if document is not None and self.workflow_context is not None
            else ()
        )

        def work(cancel):
            parsed = parse_docx(source_path) if source_path is not None else document
            if cancel.is_set():
                return None
            rules_error = whitelist_error = ""
            try:
                rules = rule_store.load()
            except CustomRuleError as error:
                rules, rules_error = [], str(error)
            try:
                terms = whitelist_store.load(parsed)
            except CustomRuleError as error:
                terms, whitelist_error = [], str(error)
            if cancel.is_set():
                return None
            active_rules = [
                *rules,
                *(CustomTextRule.from_text(term, CUSTOM_RULE_WHITELIST) for term in terms),
            ]
            review = review_document(
                parsed,
                custom_rules=active_rules,
                ocr_results=ocr_results,
                component_reference_whitelist=terms,
            )
            if cancel.is_set():
                return None
            return (parsed, review, extract_document_symbols(parsed, review),
                    rules, rules_error, terms, whitelist_error, source_path,
                    whitelist_store.document_key(parsed))

        self._set_review_busy(True)
        if not self._review_tasks.start(work):
            self._set_review_busy(False)
            return False
        return True

    def open_syntax_lab(self):
        if self.document is None:
            QMessageBox.information(self, "語法對照（內測）", "請先載入完整專利DOCX文件。")
            return
        if self._review_tasks.busy:
            return
        from ui.syntax_lab_dialog import SyntaxLabDialog
        dialog = SyntaxLabDialog(self.document, self)
        dialog.exec()
        dialog.deleteLater()

    def _set_review_busy(self, busy):
        self.select_button.setEnabled(not busy)
        self.custom_rules_button.setEnabled(not busy)
        self.syntax_lab_button.setEnabled(not busy)
        for button in (self.reload_button, self.send_button,
                       self.add_document_whitelist_button,
                       self.delete_document_whitelist_button):
            button.setEnabled(not busy and self.document is not None)
        self.document_whitelist_input.setEnabled(not busy and self.document is not None)
        self.document_whitelist_table.setEnabled(not busy)
        if busy:
            self.issue_summary.setText("正在背景檢核…（Esc 可取消）")
        else:
            self._update_document_whitelist_delete_button()

    def _on_review_ready(self, payload):
        if payload is None:
            return
        (document, review, transfer, self.custom_rules, self.custom_rule_load_error,
         self.document_whitelist_terms, self.document_whitelist_load_error,
         source_path, whitelist_key) = payload
        self._document_whitelist_key = whitelist_key
        self._update_custom_rule_button()
        self._populate_document_whitelist_table()
        self.show_review(document, review, transfer, whitelist_loaded=True)
        if source_path is not None:
            self.file_line.setText(str(source_path))

    def _on_review_failed(self, error):
        self.issue_summary.setText("檢核失敗（保留原結果）")
        QMessageBox.critical(self, "DOCX 檢核失敗", error)

    def abort_current_workflow(self):
        if self._review_tasks.busy:
            self._review_tasks.cancel()
            self.issue_summary.setText("已取消檢核（保留原結果）")

    def shutdown(self):
        self._rules_tasks.shutdown()
        self._review_tasks.shutdown()

    def show_review(self, document, review, transfer, *, publish_symbols=True,
                    whitelist_loaded=False):
        """Display one completed core review; separated for offscreen testing."""

        if not whitelist_loaded:
            document_whitelist_key = self.document_whitelist_store.document_key(document)
            if document_whitelist_key != self._document_whitelist_key:
                self._load_document_whitelist(document)
        self.document = document
        self._paragraph_map = {p.index: p for p in document.paragraphs}
        self._paragraph_map_document = document
        self.review = review
        self._guidance_review_whitelist = tuple(self.document_whitelist_terms)
        self.symbol_transfer = transfer
        self._clear_empty_document_state()
        self.issue_by_id = {issue.issue_id: issue for issue in review.issues}
        self._populate_issue_table(review)
        self.full_symbol_text.setPlainText(
            transfer.reference_text_for(FULL_SYMBOL_SOURCE)
        )
        self.representative_symbol_text.setPlainText(
            transfer.reference_text_for(REPRESENTATIVE_SYMBOL_SOURCE)
        )
        self.send_button.setEnabled(True)
        self.reload_button.setEnabled(True)
        self.document_whitelist_input.setEnabled(True)
        self.add_document_whitelist_button.setEnabled(True)
        self._update_document_whitelist_delete_button()

        counts = Counter(issue.severity for issue in review.issues)
        self.issue_summary.setText(
            f"錯誤 {counts.get('error', 0)} · 警告 {counts.get('warning', 0)} · "
            f"提示 {counts.get('info', 0)}"
        )
        full_count = len(transfer.full_entries)
        representative_count = len(transfer.representative_entries)
        self.status_label.setText(
            f"已擷取完整符號 {full_count} 個、代表圖符號 {representative_count} 個。"
            f"已套用 {len(self.custom_rules)} 條共用規則、"
            f"{len(self.document_whitelist_terms)} 個本文件排除元件或詞語。"
            "請檢查問題與清單，必要時直接修改。"
        )
        if self.custom_rule_load_error:
            self.status_label.setText(
                self.status_label.text()
                + " 自訂規則檔讀取失敗，本次僅執行內建規則。"
            )
        if self.document_whitelist_load_error:
            self.document_whitelist_hint.setText(
                self.document_whitelist_load_error
            )
            self.document_whitelist_hint.setStyleSheet("color:#b91c1c")
            self.status_label.setText(
                self.status_label.text()
                + " 本文件排除元件讀取失敗，本次未套用。"
            )
        else:
            self.document_whitelist_hint.setText(
                "僅排除目前 Word 的元件相似詞警告；不影響共用白名單、標號或其他錯誤。"
            )
            self.document_whitelist_hint.setStyleSheet("color:#111111")

        if publish_symbols and self.workflow_context is not None and (
            transfer.is_source_ready(FULL_SYMBOL_SOURCE)
            or transfer.is_source_ready(REPRESENTATIVE_SYMBOL_SOURCE)
        ):
            self.workflow_context.publish_symbol_transfer(transfer)
            self.status_label.setText(
                self.status_label.text()
                + " 有效清單已自動暫存至圖片 OCR 功能。"
            )
        if self.workflow_context is not None:
            self.workflow_context.publish_document(document)

    def _populate_issue_table(self, review):
        self.issue_table.setUpdatesEnabled(False)
        self.issue_table.blockSignals(True)
        self.issue_table.setRowCount(0)
        self.issue_table.setRowCount(len(review.issues))
        severity_text = {"error": "錯誤", "warning": "警告", "info": "提示"}
        severity_color = {
            "error": QColor("#fee2e2"),
            "warning": QColor("#fef3c7"),
            "info": QColor("#dbeafe"),
        }
        for row, issue in enumerate(review.issues):
            location = self._logical_issue_location(issue)
            values = [
                severity_text.get(issue.severity, issue.severity),
                issue.section_title or issue.section_key or "整份文件",
                location,
                issue.message,
            ]
            for column, value in enumerate(values):
                display_value = (
                    _wrap_text_by_character_count(value)
                    if column == 3
                    else str(value)
                )
                item = QTableWidgetItem(display_value)
                item.setBackground(severity_color.get(issue.severity, QColor("#ffffff")))
                if column == 3:
                    item.setToolTip(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, issue.issue_id)
                self.issue_table.setItem(row, column, item)
        self.issue_table.resizeRowsToContents()
        self.issue_table.blockSignals(False)
        self.issue_table.setUpdatesEnabled(True)
        if self.issue_table.rowCount():
            self.issue_table.selectRow(0)
        else:
            self._clear_location_panel("目前沒有需要定位的文字檢核問題。")

    def _issue_for_row(self, row):
        if row < 0:
            return None
        item = self.issue_table.item(row, 0)
        if item is None:
            return None
        issue_id = item.data(Qt.UserRole)
        if not issue_id:
            return None
        return self.issue_by_id.get(issue_id)

    @staticmethod
    def _visible_heading_token(text):
        match = re.match(r"^\s*([【〖][^】〗]+[】〗])", text or "")
        if match is None:
            return ""
        token = match.group(1)
        if token.startswith("〖"):
            token = "【" + token[1:-1] + "】"
        return re.sub(r"\s+", "", token)

    @staticmethod
    def _visible_numbering_token(paragraph):
        numbering = (paragraph.numbering_text or "").strip()
        if numbering:
            if numbering.startswith("〖") and numbering.endswith("〗"):
                return "【" + numbering[1:-1] + "】"
            return numbering
        match = re.match(
            r"^\s*([【〖](?:\d{4}|請求項\s*\d+)[】〗])",
            paragraph.text or "",
        )
        if match is None:
            return ""
        token = match.group(1)
        if token.startswith("〖") and token.endswith("〗"):
            token = "【" + token[1:-1] + "】"
        return re.sub(r"\s+", "", token)

    def _logical_issue_location(self, issue):
        """Prefer Word automatic numbering, then the owning section heading."""

        if issue.paragraph_index is None or self.document is None:
            return issue.section_title or issue.section_key or "整份文件"
        if self._paragraph_map_document is not self.document:
            self._paragraph_map = {p.index: p for p in self.document.paragraphs}
            self._paragraph_map_document = self.document
        paragraph_map = self._paragraph_map
        paragraph = paragraph_map.get(issue.paragraph_index)
        if paragraph is None:
            return issue.section_title or issue.section_key or "整份文件"
        if paragraph.source_kind == "table":
            table_number = (
                paragraph.table_index + 1
                if paragraph.table_index is not None
                else "?"
            )
            row_number = (
                paragraph.row_index + 1
                if paragraph.row_index is not None
                else "?"
            )
            cell_number = (
                paragraph.cell_index + 1
                if paragraph.cell_index is not None
                else "?"
            )
            return (
                f"表格{table_number}／第{row_number}列／第{cell_number}欄"
            )

        candidate_sections = [
            section
            for section in self.document.sections
            if section.key == paragraph.section_key
            and section.major_section_key == (paragraph.major_section_key or "")
            and section.heading_paragraph_index <= paragraph.index
        ]
        section = max(
            candidate_sections,
            key=lambda item: item.heading_paragraph_index,
            default=None,
        )
        lower_bound = section.heading_paragraph_index if section is not None else 0
        for index in range(paragraph.index, lower_bound - 1, -1):
            candidate = paragraph_map.get(index)
            if candidate is None:
                continue
            numbering = self._visible_numbering_token(candidate)
            if numbering:
                return numbering

        if section is not None:
            heading = paragraph_map.get(section.heading_paragraph_index)
            token = self._visible_heading_token(heading.text if heading else "")
            if token:
                return token
        token = self._visible_heading_token(paragraph.text)
        if token:
            return token
        return issue.section_title or issue.section_key or "整份文件"

    def on_issue_selection_changed(self):
        rows = self.issue_table.selectionModel().selectedRows()
        if not rows:
            return
        issue = self._issue_for_row(rows[0].row())
        if issue is not None:
            self._show_issue(issue)

    def _show_issue(self, issue):
        logical_location = self._logical_issue_location(issue)
        self.selected_issue_title.setText(
            f"錯誤原文定位 · {logical_location}"
        )
        claim_context = self._claim_context(issue)
        logical_paragraph_context = self._logical_paragraph_context_text(issue)
        if claim_context is not None:
            original = claim_context["text"]
        elif logical_paragraph_context is not None:
            original = logical_paragraph_context
        elif issue.paragraph_index is None:
            original = ""
        else:
            paragraph = next(
                (
                    paragraph
                    for paragraph in self.document.paragraphs
                    if paragraph.index == issue.paragraph_index
                ),
                None,
            )
            original = paragraph.text if paragraph is not None else ""
        if claim_context is not None:
            start, end = self._resolved_claim_issue_span(
                claim_context,
                issue,
            )
        else:
            start, end = self._resolved_issue_span(original, issue)
        html = self._highlighted_paragraph(original, start, end)
        if issue.rule_id == "CLM019":
            grouped_items = issue.details.get("coverage_items", [])
            if grouped_items and claim_context is not None:
                spans = []
                for item in grouped_items:
                    fragment_issue = replace(issue, details=dict(
                        issue.details,
                        highlight_text=item.get("claim_text", ""),
                        body_offset=item.get("body_offset", 0),
                    ))
                    spans.append(self._resolved_claim_issue_span(claim_context, fragment_issue))
                html = self._highlighted_ranges(original, spans)
            self.selected_issue_title.setText("請求項1與發明／新型內容對應")
            html = "<h4>請求項1原文</h4>" + html + self._coverage_evidence_html(issue)
        self.original_paragraph_view.setHtml(html)

    def _coverage_evidence_html(self, issue):
        """Show actual disclosure evidence, never generated replacement prose."""

        items = issue.details.get("coverage_items") or [issue.details.get("coverage_item", {})]
        parts = ["<hr><h4>發明／新型內容 · 對應檢核</h4>"]
        paragraph_map = {p.index: p for p in self.document.paragraphs} if self.document else {}
        for position, item in enumerate(items, 1):
            if len(items) > 1:
                parts.append(f"<p><b>待確認 {position}：{escape(str(item.get('claim_text', '')))}</b></p>")
            parts.append(f"<p>{escape(str(item.get('reason', issue.message)))}</p>")
            seen = set()
            for evidence in item.get("evidence", []):
                index = evidence.get("paragraph_index")
                paragraph = paragraph_map.get(index)
                if paragraph is None or paragraph.section_key != "disclosure":
                    continue
                start, end = evidence.get("char_start"), evidence.get("char_end")
                key = (index, start, end)
                if key in seen:
                    continue
                seen.add(key)
                location = self._logical_issue_location(replace(
                    issue, paragraph_index=index, section_key="disclosure",
                    section_title=paragraph.section_title,
                ))
                parts.append(f"<p><b>{escape(location)} · 候選原文（不代表已涵蓋）</b></p>")
                if not isinstance(start, int) or not isinstance(end, int):
                    start = end = None
                parts.append(self._highlighted_paragraph(paragraph.text, start, end, candidate=True))
            if not seen:
                parts.append("<p>尚未找到足夠的對應候選，或此語法無法可靠解析；請人工確認，不能直接視為內容遺漏。</p>")
        parts.append("<p style='color:#64748b'>僅比對發明／新型內容，不以其他章節補足；本結果為寫作輔助，不判斷法律支持性。</p>")
        return "".join(parts)

    @staticmethod
    def _highlighted_ranges(text, spans):
        valid = sorted(
            (start, end) for start, end in spans
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text)
        )
        merged = []
        for start, end in valid:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        parts = ["<div style='white-space:pre-wrap'>"]
        cursor = 0
        for start, end in merged:
            parts.extend((escape(text[cursor:start]), "<span style='background:#fecaca;color:#991b1b;font-weight:700'>", escape(text[start:end]), "</span>"))
            cursor = end
        parts.extend((escape(text[cursor:]), "</div>"))
        return "".join(parts)

    def _logical_paragraph_context_text(self, issue):
        """Return all Word paragraphs belonging to one numbered paragraph."""

        if self.document is None:
            return None
        indices = issue.details.get("logical_paragraph_indices", [])
        if not indices:
            return None
        wanted = set(indices)
        paragraphs = [
            paragraph
            for paragraph in self.document.paragraphs
            if paragraph.index in wanted
        ]
        if not paragraphs:
            return None
        rendered = []
        for position, paragraph in enumerate(paragraphs):
            text = paragraph.text or ""
            token = self._visible_numbering_token(paragraph)
            if position == 0 and token and not re.match(
                rf"^\s*{re.escape(token)}",
                text,
            ):
                text = f"{token}{text}"
            rendered.append(text)
        return "\n\n".join(rendered)

    def _claim_context_text(self, issue):
        """Return every Word paragraph belonging to the selected claim."""

        context = self._claim_context(issue)
        return context["text"] if context is not None else None

    def _claim_context(self, issue):
        """Return rendered claim text plus source-to-view offset mappings."""

        if (
            self.document is None
            or issue.paragraph_index is None
            or not (
                issue.category == "claims"
                or issue.section_key == "claims"
                or str(issue.rule_id).startswith("CLM")
            )
        ):
            return None

        claim_paragraphs = [
            paragraph
            for paragraph in self.document.paragraphs
            if not paragraph.is_heading
            and (
                paragraph.major_section_key == "claims"
                or paragraph.section_key == "claims"
            )
        ]
        anchor_position = next(
            (
                position
                for position, paragraph in enumerate(claim_paragraphs)
                if paragraph.index == issue.paragraph_index
            ),
            None,
        )
        if anchor_position is None:
            return None

        start_position = None
        for position in range(anchor_position, -1, -1):
            token = self._visible_numbering_token(claim_paragraphs[position])
            if re.search(r"請求項\s*\d+", token):
                start_position = position
                break
        if start_position is None:
            return None

        end_position = len(claim_paragraphs)
        for position in range(start_position + 1, len(claim_paragraphs)):
            token = self._visible_numbering_token(claim_paragraphs[position])
            if re.search(r"請求項\s*\d+", token):
                end_position = position
                break

        rendered_paragraphs = []
        paragraph_spans = []
        rendered_offset = 0
        for position in range(start_position, end_position):
            paragraph = claim_paragraphs[position]
            text = paragraph.text or ""
            token = self._visible_numbering_token(paragraph)
            inserted_prefix_length = 0
            if token and not re.match(
                rf"^\s*{re.escape(token)}", text
            ):
                text = f"{token}{text}"
                inserted_prefix_length = len(token)
            if rendered_paragraphs:
                rendered_offset += 2
            paragraph_spans.append(
                {
                    "paragraph_index": paragraph.index,
                    "rendered_start": rendered_offset,
                    "rendered_end": rendered_offset + len(text),
                    "source_start": rendered_offset + inserted_prefix_length,
                    "source_length": len(paragraph.text or ""),
                }
            )
            rendered_paragraphs.append(text)
            rendered_offset += len(text)
        return {
            "text": "\n\n".join(rendered_paragraphs),
            "paragraph_spans": paragraph_spans,
        }

    @staticmethod
    def _non_whitespace_character_map(text, start=0):
        characters = []
        rendered_positions = []
        for position in range(start, len(text)):
            character = text[position]
            if character.isspace():
                continue
            characters.append(character)
            rendered_positions.append(position)
        return "".join(characters), rendered_positions

    @classmethod
    def _claim_body_character_map(cls, text):
        """Map whitespace-free claim-body offsets back to rendered text."""

        prefix = re.match(
            r"^\s*(?:[【〖]\s*)?請求項\s*\d+\s*(?:[】〗])?",
            text or "",
        )
        body_start = prefix.end() if prefix is not None else 0
        separator = re.match(r"\s*[:：、.．]\s*", text[body_start:])
        if separator is not None:
            body_start += separator.end()
        return cls._non_whitespace_character_map(text, body_start)

    @classmethod
    def _resolved_claim_issue_span(cls, context, issue):
        """Resolve a claim issue against the complete multi-paragraph view."""

        text = context["text"]
        if not text:
            return issue.char_start, issue.char_end

        highlight_text = re.sub(
            r"\s+",
            "",
            str(issue.details.get("highlight_text", "")),
        )
        body_offset = issue.details.get("body_offset")
        component_name = re.sub(
            r"\s+",
            "",
            str(issue.details.get("component_name", "")),
        )
        if highlight_text and isinstance(body_offset, int):
            normalized_body, positions = cls._claim_body_character_map(text)
            if positions:
                component_offset = (
                    highlight_text.rfind(component_name)
                    if component_name and component_name in highlight_text
                    else 0
                )
                candidates = [
                    match.start()
                    for match in re.finditer(
                        re.escape(highlight_text),
                        normalized_body,
                    )
                ]
                if candidates:
                    normalized_start = min(
                        candidates,
                        key=lambda candidate: abs(
                            candidate + component_offset - body_offset
                        ),
                    )
                    normalized_end = normalized_start + len(highlight_text)
                    if normalized_end <= len(positions):
                        return (
                            positions[normalized_start],
                            positions[normalized_end - 1] + 1,
                        )

        # A rule's character range is local to its anchor Word paragraph.
        # Convert it through the matching rendered segment before considering
        # any claim-wide textual fallback.
        for span in context.get("paragraph_spans", []):
            if span["paragraph_index"] != issue.paragraph_index:
                continue
            start, end = issue.char_start, issue.char_end
            if highlight_text:
                segment = text[span["rendered_start"]:span["rendered_end"]]
                normalized_segment, positions = cls._non_whitespace_character_map(
                    segment
                )
                normalized_index = normalized_segment.find(highlight_text)
                if normalized_index >= 0 and positions:
                    return (
                        span["rendered_start"] + positions[normalized_index],
                        span["rendered_start"]
                        + positions[normalized_index + len(highlight_text) - 1]
                        + 1,
                    )
            if (
                start is not None
                and end is not None
                and 0 <= start < end <= span["source_length"]
            ):
                return span["source_start"] + start, span["source_start"] + end
            break

        if highlight_text:
            index = text.find(str(issue.details.get("highlight_text", "")).strip())
            if index >= 0:
                return index, index + len(str(issue.details.get("highlight_text", "")).strip())
        return cls._resolved_issue_span(text, issue)

    @staticmethod
    def _resolved_issue_span(text, issue):
        if not text:
            return issue.char_start, issue.char_end

        highlight_text = str(issue.details.get("highlight_text", "")).strip()
        if highlight_text:
            index = text.find(highlight_text)
            if index >= 0:
                return index, index + len(highlight_text)

        start, end = issue.char_start, issue.char_end
        has_precise_span = (
            start is not None
            and end is not None
            and 0 <= start < end <= len(text)
            and end - start <= 32
        )
        if has_precise_span:
            return start, end

        for candidate in re.findall(r"「([^」]{1,48})」", issue.message):
            index = text.find(candidate)
            if index >= 0:
                return index, index + len(candidate)
        return start, end

    @staticmethod
    def _highlighted_paragraph(text, start, end, *, candidate=False):
        if not text:
            return "<span style='color:#64748b'>此問題屬於文件層級，沒有單一原始段落。</span>"
        if start is None or end is None or not (0 <= start <= end <= len(text)):
            return f"<div style='white-space:pre-wrap'>{escape(text)}</div>"
        colors = "background:#dbeafe;color:#1e40af" if candidate else "background:#fecaca;color:#991b1b"
        return (
            "<div style='white-space:pre-wrap'>"
            f"{escape(text[:start])}"
            f"<span style='{colors};font-weight:700'>"
            f"{escape(text[start:end])}</span>"
            f"{escape(text[end:])}</div>"
        )

    def _clear_location_panel(self, message):
        self.selected_issue_title.setText("錯誤原文定位")
        self.original_paragraph_view.setPlaceholderText(message)
        self.original_paragraph_view.clear()

    def publish_to_ocr(self):
        if self.symbol_transfer is None:
            return
        confirmed = rebuild_transfer_from_reference_texts(
            self.symbol_transfer,
            self.full_symbol_text.toPlainText(),
            self.representative_symbol_text.toPlainText(),
        )
        ready_sources = [
            source
            for source in (FULL_SYMBOL_SOURCE, REPRESENTATIVE_SYMBOL_SOURCE)
            if confirmed.is_source_ready(source)
        ]
        if not ready_sources:
            details = "\n".join(
                f"• {warning.message}"
                for warning in confirmed.warnings
                if warning.blocking
            )
            QMessageBox.warning(
                self,
                "清單尚無法傳送",
                "兩套清單目前都沒有可安全使用的內容。\n\n" + details,
            )
            return

        self.symbol_transfer = confirmed
        if self.workflow_context is not None:
            self.workflow_context.publish_symbol_transfer(confirmed)
        self.status_label.setText(
            "人工確認後的清單已送往圖片標號識別；原始 Word 未被修改。"
        )
        if self.open_feature_callback is not None:
            self.open_feature_callback("patent_ocr")
