from html import escape
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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
    FULL_SYMBOL_SOURCE,
    REPRESENTATIVE_SYMBOL_SOURCE,
    PatentDocxError,
    extract_document_symbols,
    parse_docx,
    rebuild_transfer_from_reference_texts,
    review_document,
)
from ui.file_drop import SingleFileDropController


class PatentReviewPage(QWidget):
    """Read-only DOCX review page and controlled symbol-list handoff."""

    def __init__(self, go_home_callback):
        super().__init__()
        self.go_home_callback = go_home_callback
        self.open_feature_callback = None
        self.workflow_context = None
        self.document = None
        self.review = None
        self.symbol_transfer = None
        self.issue_by_id = {}
        self.build_ui()
        self.file_drop_controller = SingleFileDropController(
            self,
            allowed_suffix=".docx",
            file_type_name="Word 文件",
            on_file=self.load_document,
        )

    def set_workflow_context(self, workflow_context):
        self.workflow_context = workflow_context

    def set_open_feature_callback(self, callback):
        self.open_feature_callback = callback

    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 8)
        layout.setSpacing(3)

        self.file_header_layout = QHBoxLayout()
        self.file_header_layout.setSpacing(6)
        back_button = QPushButton("← 回首頁")
        back_button.setObjectName("SecondaryButton")
        back_button.setMaximumHeight(34)
        back_button.clicked.connect(self.go_home_callback)
        title = QLabel("專利文件偵錯")
        title.setObjectName("PageTitle")
        title.setMaximumHeight(34)
        self.file_header_layout.addWidget(back_button)
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
        self.reload_button = QPushButton("重新檢核")
        self.reload_button.setObjectName("SecondaryButton")
        self.reload_button.setMaximumHeight(34)
        self.reload_button.setEnabled(False)
        self.reload_button.clicked.connect(self.reload_document)
        self.file_header_layout.addWidget(self.reload_button)
        layout.addLayout(self.file_header_layout)

        self.status_label = QLabel(
            "可選擇或直接拖入 DOCX；載入後會執行文字規則，並擷取完整與代表圖兩套符號清單。"
        )
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setMaximumHeight(38)
        self.status_label.setContentsMargins(6, 0, 6, 0)
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.status_label)

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
        issue_header.addWidget(self.issue_summary)
        issue_layout.addLayout(issue_header)

        self.issue_table = QTableWidget(0, 6)
        self.issue_table.setObjectName("ReviewTable")
        self.issue_table.setHorizontalHeaderLabels(
            ["等級", "規則", "章節", "位置", "問題", "建議"]
        )
        self.issue_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.issue_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.issue_table.verticalHeader().setVisible(False)
        for column in (0, 1, 2, 3):
            self.issue_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeToContents
            )
        self.issue_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.issue_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
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

        self.error_type_label = QLabel(
            "錯誤種類：請先點選上方問題"
        )
        self.error_type_label.setWordWrap(True)
        location_layout.addWidget(self.error_type_label)
        self.error_location_label = QLabel("原文位置：尚未選取")
        self.error_location_label.setWordWrap(True)
        location_layout.addWidget(self.error_location_label)
        location_layout.addWidget(QLabel("原始 Word 段落（紅色為問題位置）："))
        self.original_paragraph_view = QTextEdit()
        self.original_paragraph_view.setReadOnly(True)
        self.original_paragraph_view.setMinimumHeight(110)
        location_layout.addWidget(self.original_paragraph_view, 1)

        self.issue_workspace_splitter.addWidget(location_panel)
        self.issue_workspace_splitter.setSizes([430, 230])
        self.issue_workspace_splitter.setStretchFactor(0, 3)
        self.issue_workspace_splitter.setStretchFactor(1, 2)
        issue_layout.addWidget(self.issue_workspace_splitter)

        symbol_panel = QFrame()
        symbol_panel.setObjectName("Panel")
        symbol_layout = QVBoxLayout(symbol_panel)
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

        hint = QLabel(
            "兩套清單會分開傳送。可在此人工修改；此操作只改暫存清單，不會寫回 Word。"
        )
        hint.setWordWrap(True)
        hint.setMaximumHeight(38)
        symbol_layout.addWidget(hint)

        self.symbol_tabs = QTabWidget()
        self.symbol_tabs.tabBar().setExpanding(True)
        self.symbol_tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #334155; border-radius: 0 0 6px 6px; }"
            "QTabBar::tab {"
            " background: #334155; color: #f8fafc; font-weight: 700;"
            " min-height: 30px; padding: 5px 18px; border: 1px solid #475569;"
            "}"
            "QTabBar::tab:selected {"
            " background: #0f172a; color: #ffffff; border-bottom: 3px solid #60a5fa;"
            "}"
            "QTabBar::tab:hover:!selected { background: #475569; }"
        )
        self.full_symbol_text = QTextEdit()
        self.full_symbol_text.setObjectName("ReferenceText")
        self.full_symbol_text.setPlaceholderText("完整符號說明，例如 10:主箱體")
        self.representative_symbol_text = QTextEdit()
        self.representative_symbol_text.setObjectName("ReferenceText")
        self.representative_symbol_text.setPlaceholderText(
            "代表圖符號說明，例如 10:主箱體"
        )
        self.symbol_tabs.addTab(self.full_symbol_text, "完整符號說明")
        self.symbol_tabs.addTab(self.representative_symbol_text, "代表圖符號說明")
        symbol_layout.addWidget(self.symbol_tabs)

        self.main_splitter.addWidget(issue_panel)
        self.main_splitter.addWidget(symbol_panel)
        self.main_splitter.setSizes([760, 540])
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)
        layout.addWidget(self.main_splitter, 1)

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
        if self.file_line.text():
            self.load_document(self.file_line.text())

    def load_document(self, source_path):
        path = Path(source_path)
        try:
            document = parse_docx(path)
            review = review_document(document)
            transfer = extract_document_symbols(document, review)
        except (PatentDocxError, OSError, ValueError) as error:
            QMessageBox.critical(self, "DOCX 檢核失敗", str(error))
            return False

        self.show_review(document, review, transfer)
        self.file_line.setText(str(path))
        self.reload_button.setEnabled(True)
        return True

    def show_review(self, document, review, transfer):
        """Display one completed core review; separated for offscreen testing."""

        self.document = document
        self.review = review
        self.symbol_transfer = transfer
        self.issue_by_id = {issue.issue_id: issue for issue in review.issues}
        self._populate_issue_table(review)
        self.full_symbol_text.setPlainText(
            transfer.reference_text_for(FULL_SYMBOL_SOURCE)
        )
        self.representative_symbol_text.setPlainText(
            transfer.reference_text_for(REPRESENTATIVE_SYMBOL_SOURCE)
        )
        self.send_button.setEnabled(True)

        counts = review.to_dict()["summary"]["by_severity"]
        self.issue_summary.setText(
            f"錯誤 {counts.get('error', 0)} · 警告 {counts.get('warning', 0)} · "
            f"提示 {counts.get('info', 0)}"
        )
        full_count = len(transfer.full_entries)
        representative_count = len(transfer.representative_entries)
        self.status_label.setText(
            f"已擷取完整符號 {full_count} 個、代表圖符號 {representative_count} 個。"
            "請檢查問題與清單，必要時直接修改。"
        )

        if self.workflow_context is not None and (
            transfer.is_source_ready(FULL_SYMBOL_SOURCE)
            or transfer.is_source_ready(REPRESENTATIVE_SYMBOL_SOURCE)
        ):
            self.workflow_context.publish_symbol_transfer(transfer)
            self.status_label.setText(
                self.status_label.text()
                + " 有效清單已自動暫存至圖片 OCR 功能。"
            )

    def _populate_issue_table(self, review):
        self.issue_table.setRowCount(0)
        severity_text = {"error": "錯誤", "warning": "警告", "info": "提示"}
        severity_color = {
            "error": QColor("#fee2e2"),
            "warning": QColor("#fef3c7"),
            "info": QColor("#dbeafe"),
        }
        for issue in review.issues:
            row = self.issue_table.rowCount()
            self.issue_table.insertRow(row)
            location = "文件層級"
            if issue.paragraph_index is not None:
                location = f"段落 {issue.paragraph_index}"
                if issue.char_start is not None and issue.char_end is not None:
                    location += f" · {issue.char_start}:{issue.char_end}"
            values = [
                severity_text.get(issue.severity, issue.severity),
                issue.rule_id,
                issue.section_title or issue.section_key or "整份文件",
                location,
                issue.message,
                issue.suggestion,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setBackground(severity_color.get(issue.severity, QColor("#ffffff")))
                if column == 0:
                    item.setData(Qt.UserRole, issue.issue_id)
                self.issue_table.setItem(row, column, item)
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

    def on_issue_selection_changed(self):
        rows = self.issue_table.selectionModel().selectedRows()
        if not rows:
            return
        issue = self._issue_for_row(rows[0].row())
        if issue is not None:
            self._show_issue(issue)

    def _show_issue(self, issue):
        severity_text = {"error": "錯誤", "warning": "警告", "info": "提示"}
        rule_title = next(
            (
                rule.title
                for rule in self.review.rule_catalog
                if rule.rule_id == issue.rule_id
            ),
            issue.message,
        )
        self.selected_issue_title.setText(
            f"錯誤原文定位 · {issue.section_title or issue.section_key or '整份文件'}"
        )
        self.error_type_label.setText(
            f"錯誤種類：{issue.rule_id} · {rule_title}"
            f"（{severity_text.get(issue.severity, issue.severity)}）"
        )
        location_parts = [
            f"章節：{issue.section_title or issue.section_key or '整份文件'}"
        ]
        if issue.paragraph_index is None:
            location_parts.append("文件層級")
            original = ""
        else:
            location_parts.append(f"段落：{issue.paragraph_index}")
            if issue.char_start is not None and issue.char_end is not None:
                location_parts.append(
                    f"字元：{issue.char_start}:{issue.char_end}"
                )
            if issue.run_indices:
                location_parts.append(
                    "Word run：" + ", ".join(map(str, issue.run_indices))
                )
            paragraph = next(
                (
                    paragraph
                    for paragraph in self.document.paragraphs
                    if paragraph.index == issue.paragraph_index
                ),
                None,
            )
            original = paragraph.text if paragraph is not None else ""
        self.error_location_label.setText(
            "原文位置：" + " · ".join(location_parts)
        )
        self.original_paragraph_view.setHtml(
            self._highlighted_paragraph(original, issue.char_start, issue.char_end)
        )

    @staticmethod
    def _highlighted_paragraph(text, start, end):
        if not text:
            return "<span style='color:#64748b'>此問題屬於文件層級，沒有單一原始段落。</span>"
        if start is None or end is None or not (0 <= start <= end <= len(text)):
            return f"<div style='white-space:pre-wrap'>{escape(text)}</div>"
        return (
            "<div style='white-space:pre-wrap'>"
            f"{escape(text[:start])}"
            "<span style='background:#fecaca;color:#991b1b;font-weight:700'>"
            f"{escape(text[start:end])}</span>{escape(text[end:])}</div>"
        )

    def _clear_location_panel(self, message):
        self.selected_issue_title.setText("錯誤原文定位")
        self.error_type_label.setText(f"錯誤種類：{message}")
        self.error_location_label.setText("原文位置：無")
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
