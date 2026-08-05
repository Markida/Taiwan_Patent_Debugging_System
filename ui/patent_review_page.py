from html import escape
from pathlib import Path
import re

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
    CustomRuleError,
    CustomTextRuleStore,
    FULL_SYMBOL_SOURCE,
    REPRESENTATIVE_SYMBOL_SOURCE,
    PatentDocxError,
    extract_document_symbols,
    parse_docx,
    rebuild_transfer_from_reference_texts,
    review_document,
)
from ui.custom_text_rule_dialog import CustomTextRuleDialog
from ui.feature_navigation import FeatureNavigationBar
from ui.file_drop import SingleFileDropController


class PatentReviewPage(QWidget):
    """Read-only DOCX review page and controlled symbol-list handoff."""

    def __init__(self, go_home_callback, custom_rule_store=None):
        super().__init__()
        self.go_home_callback = go_home_callback
        self.open_feature_callback = None
        self.workflow_context = None
        self.document = None
        self.review = None
        self.symbol_transfer = None
        self.issue_by_id = {}
        self.custom_rule_store = custom_rule_store or CustomTextRuleStore()
        self.custom_rules = []
        self.custom_rule_load_error = ""
        self._reload_custom_rules()
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
        self.custom_rules_button = QPushButton(
            f"自訂文字規則（{len(self.custom_rules)}）"
        )
        self.custom_rules_button.setObjectName("SecondaryButton")
        self.custom_rules_button.setToolTip(
            f"新增或刪除特定文字偵測規則\n儲存位置：{self.custom_rule_store.path}"
        )
        self.custom_rules_button.clicked.connect(self.open_custom_rule_manager)
        issue_header.addWidget(self.custom_rules_button)
        issue_header.addWidget(self.issue_summary)
        issue_layout.addLayout(issue_header)

        self.issue_table = QTableWidget(0, 4)
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

        self.original_paragraph_view = QTextEdit()
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

    def _reload_custom_rules(self):
        try:
            self.custom_rules = self.custom_rule_store.load()
            self.custom_rule_load_error = ""
            return True
        except CustomRuleError as error:
            self.custom_rules = []
            self.custom_rule_load_error = str(error)
            return False

    def _update_custom_rule_button(self):
        self.custom_rules_button.setText(
            f"自訂文字規則（{len(self.custom_rules)}）"
        )

    def open_custom_rule_manager(self):
        dialog = CustomTextRuleDialog(self.custom_rule_store, self)
        dialog.exec()
        self._reload_custom_rules()
        self._update_custom_rule_button()
        if dialog.rules_changed and self.document is not None:
            review = review_document(
                self.document,
                custom_rules=self.custom_rules,
            )
            transfer = extract_document_symbols(self.document, review)
            self.show_review(self.document, review, transfer)
        elif dialog.rules_changed:
            self.status_label.setText(
                f"已儲存 {len(self.custom_rules)} 條自訂文字規則；載入文件後會自動套用。"
            )

    def load_document(self, source_path):
        path = Path(source_path)
        try:
            document = parse_docx(path)
            self._reload_custom_rules()
            self._update_custom_rule_button()
            review = review_document(
                document,
                custom_rules=self.custom_rules,
            )
            transfer = extract_document_symbols(document, review)
        except (PatentDocxError, OSError, ValueError) as error:
            QMessageBox.critical(self, "DOCX 檢核失敗", str(error))
            return False

        self.show_review(document, review, transfer)
        self.file_line.setText(str(path))
        self.reload_button.setEnabled(True)
        return True

    def show_review(self, document, review, transfer, *, publish_symbols=True):
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
            f"已套用 {len(self.custom_rules)} 條自訂文字規則。"
            "請檢查問題與清單，必要時直接修改。"
        )
        if self.custom_rule_load_error:
            self.status_label.setText(
                self.status_label.text()
                + " 自訂規則檔讀取失敗，本次僅執行內建規則。"
            )

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
            location = self._logical_issue_location(issue)
            values = [
                severity_text.get(issue.severity, issue.severity),
                issue.section_title or issue.section_key or "整份文件",
                location,
                issue.message,
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
        paragraph_map = {
            paragraph.index: paragraph for paragraph in self.document.paragraphs
        }
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
        self.original_paragraph_view.setHtml(
            self._highlighted_paragraph(original, start, end)
        )

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
    def _highlighted_paragraph(text, start, end):
        if not text:
            return "<span style='color:#64748b'>此問題屬於文件層級，沒有單一原始段落。</span>"
        if start is None or end is None or not (0 <= start <= end <= len(text)):
            return f"<div style='white-space:pre-wrap'>{escape(text)}</div>"
        return (
            "<div style='white-space:pre-wrap'>"
            f"{escape(text[:start])}"
            "<span style='background:#fecaca;color:#991b1b;font-weight:700'>"
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
