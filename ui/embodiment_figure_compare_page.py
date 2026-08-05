"""Interactive side-by-side review of embodiment paragraphs and drawing OCR."""

from __future__ import annotations

from html import escape
from pathlib import Path
import re
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from features.patent_review.figure_ocr_checker import (
    EmbodimentFigureComparison,
    build_embodiment_figure_comparisons,
    build_ocr_figure_pages,
)
from ui.feature_navigation import FeatureNavigationBar


def _joined(values) -> str:
    return "、".join(str(value) for value in values) if values else "無"


def _highlight_labels(text: str, labels) -> str:
    """Render component labels as red boxed tokens, excluding figure numbers."""

    source = str(text or "")
    normalized_labels = sorted(
        {str(label) for label in labels if str(label)},
        key=len,
        reverse=True,
    )
    if not normalized_labels:
        return escape(source).replace("\n", "<br>")
    pattern = re.compile(
        r"(?<![0-9A-Za-z'])(?:"
        + "|".join(re.escape(label) for label in normalized_labels)
        + r")(?![0-9A-Za-z'])"
    )
    fragments = []
    cursor = 0
    for match in pattern.finditer(source):
        # A number in「圖1」is a figure reference, not a component label.
        if source[: match.start()].rstrip().endswith("圖"):
            continue
        fragments.append(escape(source[cursor:match.start()]))
        fragments.append(
            "<span style='color:#b91c1c;background-color:#fee2e2;"
            "border:2px solid #dc2626;border-radius:3px;font-weight:800;"
            "padding:0 2px;'>"
            + escape(match.group(0))
            + "</span>"
        )
        cursor = match.end()
    fragments.append(escape(source[cursor:]))
    return "".join(fragments).replace("\n", "<br>")


class ScaledImageLabel(QLabel):
    """Keep the selected drawing readable while its panel is resized."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = QPixmap()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(420, 400)
        self.setObjectName("ComparisonImage")
        self.setText("尚未載入圖式 OCR 結果")

    def set_image(self, path: str) -> bool:
        self._source = QPixmap(str(path or ""))
        if self._source.isNull():
            self.clear()
            self.setText(
                "找不到圖式圖片"
                if path
                else "此筆 OCR 結果沒有可顯示的圖片路徑"
            )
            return False
        self._refresh_pixmap()
        return True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self):
        if self._source.isNull():
            return
        target = self.contentsRect().size()
        if target.width() < 1 or target.height() < 1:
            return
        self.setPixmap(
            self._source.scaled(
                target,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )


class EmbodimentFigureComparePage(QWidget):
    """Third major feature: independently select a paragraph and drawing page."""

    def __init__(self, go_home_callback):
        super().__init__()
        self.go_home_callback = go_home_callback
        self.open_feature_callback = None
        self.workflow_context = None
        self.document = None
        self.ocr_results = []
        self.comparisons: list[EmbodimentFigureComparison] = []
        self.figure_pages = []
        self._build_ui()

    def set_workflow_context(self, workflow_context):
        self.workflow_context = workflow_context
        workflow_context.subscribe_document(self.load_document)
        workflow_context.subscribe_ocr_results(self.load_ocr_results)

    def set_open_feature_callback(self, callback):
        self.open_feature_callback = callback

    def set_feature_navigation(self, features, open_feature_callback):
        self.feature_navigation.configure(features, open_feature_callback)

    def _open_feature(self, feature_id: str):
        if callable(self.open_feature_callback):
            self.open_feature_callback(feature_id)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 8)
        layout.setSpacing(5)

        self.feature_navigation = FeatureNavigationBar(
            self.go_home_callback,
            "embodiment_figure_compare",
        )
        layout.addWidget(self.feature_navigation)

        header = QHBoxLayout()
        title = QLabel("實施方式與圖式比對")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch()
        document_button = QPushButton("前往文件偵錯")
        document_button.setObjectName("SecondaryButton")
        document_button.clicked.connect(
            lambda _checked=False: self._open_feature("patent_review")
        )
        header.addWidget(document_button)
        ocr_button = QPushButton("前往圖式標號")
        ocr_button.setObjectName("SecondaryButton")
        ocr_button.clicked.connect(
            lambda _checked=False: self._open_feature("patent_ocr")
        )
        header.addWidget(ocr_button)
        layout.addLayout(header)

        self.source_status = QLabel(
            "請先在「文件偵錯」載入 Word，並在「圖式標號」完成圖片辨識。"
        )
        self.source_status.setObjectName("ComparisonStatus")
        self.source_status.setWordWrap(True)
        layout.addWidget(self.source_status)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        left_panel = QFrame()
        left_panel.setObjectName("Panel")
        left_layout = QVBoxLayout(left_panel)
        left_title = QLabel("實施方式段落")
        left_title.setObjectName("PanelTitle")
        left_layout.addWidget(left_title)

        self.paragraph_workspace_splitter = QSplitter(Qt.Vertical)
        self.paragraph_workspace_splitter.setChildrenCollapsible(False)

        paragraph_list_panel = QFrame()
        paragraph_list_panel.setObjectName("ComparisonParagraphListPanel")
        paragraph_list_layout = QVBoxLayout(paragraph_list_panel)
        paragraph_list_layout.setContentsMargins(0, 0, 0, 0)
        paragraph_list_layout.setSpacing(4)
        paragraph_list_title = QLabel("選擇實施方式段落")
        paragraph_list_title.setObjectName("PanelTitle")
        paragraph_list_layout.addWidget(paragraph_list_title)

        self.paragraph_table = QTableWidget(0, 3)
        self.paragraph_table.setObjectName("ReviewTable")
        self.paragraph_table.setHorizontalHeaderLabels(
            ["段落", "參閱圖式", "圖式缺失標號"]
        )
        self.paragraph_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.paragraph_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.paragraph_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.paragraph_table.verticalHeader().setVisible(False)
        self.paragraph_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.paragraph_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self.paragraph_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )
        self.paragraph_table.itemSelectionChanged.connect(
            self._on_paragraph_table_selection_changed
        )
        paragraph_list_layout.addWidget(self.paragraph_table, 1)
        self.paragraph_workspace_splitter.addWidget(paragraph_list_panel)

        paragraph_content_panel = QFrame()
        paragraph_content_panel.setObjectName("ComparisonParagraphContentPanel")
        paragraph_content_layout = QVBoxLayout(paragraph_content_panel)
        paragraph_content_layout.setContentsMargins(0, 4, 0, 0)
        paragraph_content_layout.setSpacing(4)
        paragraph_content_title = QLabel("段落原文（紅框為文件標號）")
        paragraph_content_title.setObjectName("PanelTitle")
        paragraph_content_layout.addWidget(paragraph_content_title)
        self.paragraph_meta = QLabel("尚未取得實施方式段落")
        self.paragraph_meta.setWordWrap(True)
        paragraph_content_layout.addWidget(self.paragraph_meta)
        self.paragraph_text = QTextBrowser()
        self.paragraph_text.setObjectName("ComparisonText")
        self.paragraph_text.setOpenExternalLinks(False)
        paragraph_content_layout.addWidget(self.paragraph_text, 1)
        self.paragraph_labels = QLabel("段落標號：無")
        self.paragraph_labels.setWordWrap(True)
        paragraph_content_layout.addWidget(self.paragraph_labels)
        self.paragraph_workspace_splitter.addWidget(paragraph_content_panel)
        self.paragraph_workspace_splitter.setSizes([280, 430])
        self.paragraph_workspace_splitter.setStretchFactor(0, 1)
        self.paragraph_workspace_splitter.setStretchFactor(1, 2)
        left_layout.addWidget(self.paragraph_workspace_splitter, 1)

        # Keep the combo as a lightweight internal selection model for
        # compatibility with existing workflow integrations.  The visible UI
        # is the review-style table above.
        self.paragraph_combo = QComboBox(left_panel)
        self.paragraph_combo.hide()
        self.paragraph_combo.currentIndexChanged.connect(
            self._on_paragraph_index_changed
        )
        self.auto_match_button = QPushButton(
            "切換到本段落參閱的圖式頁", left_panel
        )
        self.auto_match_button.setObjectName("PrimaryButton")
        self.auto_match_button.setEnabled(False)
        self.auto_match_button.clicked.connect(self.select_referenced_figure_page)
        self.auto_match_button.hide()
        splitter.addWidget(left_panel)

        right_panel = QFrame()
        right_panel.setObjectName("Panel")
        right_layout = QVBoxLayout(right_panel)
        right_title = QLabel("圖式圖片")
        right_title.setObjectName("PanelTitle")
        right_layout.addWidget(right_title)
        self.figure_combo = QComboBox()
        self.figure_combo.setObjectName("InputLine")
        self.figure_combo.currentIndexChanged.connect(self._show_selected_figure)
        right_layout.addWidget(self.figure_combo)
        self.figure_meta = QLabel("尚未取得圖式 OCR 結果")
        self.figure_meta.setWordWrap(True)
        right_layout.addWidget(self.figure_meta)
        self.image_preview = ScaledImageLabel()
        right_layout.addWidget(self.image_preview, 1)
        self.figure_labels = QLabel("圖式辨識標號：無")
        self.figure_labels.setWordWrap(True)
        right_layout.addWidget(self.figure_labels)
        splitter.addWidget(right_panel)

        splitter.setSizes([650, 650])
        layout.addWidget(splitter, 1)

        self.comparison_result = QLabel(
            "選擇左側段落與右側圖式後，這裡會顯示目前兩個視窗的標號差異。"
        )
        self.comparison_result.setObjectName("ComparisonResult")
        self.comparison_result.setWordWrap(True)
        self.comparison_result.setTextFormat(Qt.RichText)
        self.comparison_result.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.comparison_result.setMinimumHeight(120)
        layout.addWidget(self.comparison_result)

    def load_document(self, document):
        self.document = document
        self._refresh_sources()

    def load_ocr_results(self, results):
        self.ocr_results = list(results or [])
        self._refresh_sources()

    def _refresh_sources(self):
        previous_paragraph = self.paragraph_combo.currentIndex()
        previous_figure = self.figure_combo.currentIndex()
        self.comparisons = (
            build_embodiment_figure_comparisons(
                self.document,
                self.ocr_results,
            )
            if self.document is not None
            else []
        )
        self.figure_pages = build_ocr_figure_pages(self.ocr_results)

        self.paragraph_combo.blockSignals(True)
        self.paragraph_combo.clear()
        for comparison in self.comparisons:
            first = comparison.paragraphs[0]
            location = first.numbering_text or f"段落 {comparison.group_index}"
            figures = _joined(comparison.referenced_figures)
            inherited = "（沿用）" if comparison.inherited_reference else ""
            self.paragraph_combo.addItem(
                f"{location} · 參閱圖{figures}{inherited}",
                comparison.group_index,
            )
        self.paragraph_combo.setEnabled(bool(self.comparisons))
        if self.comparisons:
            self.paragraph_combo.setCurrentIndex(
                min(max(previous_paragraph, 0), len(self.comparisons) - 1)
            )
        self.paragraph_combo.blockSignals(False)

        self.paragraph_table.blockSignals(True)
        self.paragraph_table.setRowCount(len(self.comparisons))
        for row, comparison in enumerate(self.comparisons):
            first = comparison.paragraphs[0]
            location = first.numbering_text or f"段落 {comparison.group_index}"
            figures = (
                f"圖{_joined(comparison.referenced_figures)}"
                if comparison.referenced_figures
                else "未指定"
            )
            if not self.ocr_results:
                missing = "等待 OCR"
            elif comparison.missing_figures:
                missing = "缺少圖" + _joined(comparison.missing_figures)
            else:
                missing = _joined(comparison.labels_not_in_drawings)
            for column, value in enumerate((location, figures, missing)):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, row)
                if (
                    column == 2
                    and self.ocr_results
                    and (
                        comparison.labels_not_in_drawings
                        or comparison.missing_figures
                    )
                ):
                    item.setBackground(QColor("#fee2e2"))
                    item.setForeground(QColor("#991b1b"))
                self.paragraph_table.setItem(row, column, item)
        if self.comparisons:
            self.paragraph_table.selectRow(self.paragraph_combo.currentIndex())
        self.paragraph_table.blockSignals(False)

        self.figure_combo.blockSignals(True)
        self.figure_combo.clear()
        for page in self.figure_pages:
            self.figure_combo.addItem(
                f"Pic_{page.page_index:02d} · 圖{_joined(page.figures)} · {page.image_name}",
                page.page_index,
            )
        self.figure_combo.setEnabled(bool(self.figure_pages))
        if self.figure_pages:
            self.figure_combo.setCurrentIndex(
                min(max(previous_figure, 0), len(self.figure_pages) - 1)
            )
        self.figure_combo.blockSignals(False)

        document_text = (
            f"Word：{self.document.file_name}，實施方式 {len(self.comparisons)} 段"
            if self.document is not None
            else "Word：尚未載入"
        )
        ocr_text = (
            f"OCR：{len(self.figure_pages)} 個圖片頁面"
            if self.figure_pages
            else "OCR：尚未完成圖片辨識"
        )
        self.source_status.setText(f"{document_text}　｜　{ocr_text}")
        self._sync_paragraph_table_selection()
        self._show_selected_paragraph()
        self.select_referenced_figure_page()
        self._show_selected_figure()

    def _sync_paragraph_table_selection(self):
        index = self.paragraph_combo.currentIndex()
        if not (0 <= index < self.paragraph_table.rowCount()):
            return
        selected_rows = self.paragraph_table.selectionModel().selectedRows()
        if selected_rows and selected_rows[0].row() == index:
            return
        self.paragraph_table.blockSignals(True)
        self.paragraph_table.selectRow(index)
        self.paragraph_table.blockSignals(False)

    def _on_paragraph_table_selection_changed(self):
        rows = self.paragraph_table.selectionModel().selectedRows()
        if not rows:
            return
        index = rows[0].row()
        if self.paragraph_combo.currentIndex() != index:
            self.paragraph_combo.setCurrentIndex(index)
        else:
            self._show_selected_paragraph()
            self.select_referenced_figure_page()

    def _on_paragraph_index_changed(self, _index=-1):
        self._sync_paragraph_table_selection()
        self._show_selected_paragraph()
        self.select_referenced_figure_page()

    def _selected_comparison(self) -> Optional[EmbodimentFigureComparison]:
        index = self.paragraph_combo.currentIndex()
        if 0 <= index < len(self.comparisons):
            return self.comparisons[index]
        return None

    def _selected_figure_page(self):
        index = self.figure_combo.currentIndex()
        if 0 <= index < len(self.figure_pages):
            return self.figure_pages[index]
        return None

    def _show_selected_paragraph(self, _index=-1):
        comparison = self._selected_comparison()
        if comparison is None:
            self.paragraph_text.setPlainText("尚未載入可供比對的實施方式段落。")
            self.paragraph_meta.setText("請先到「文件偵錯」載入 Word 文件。")
            self.paragraph_labels.setText("段落標號：無")
            self.auto_match_button.setEnabled(False)
            self._update_manual_comparison()
            return

        blocks = []
        for paragraph in comparison.paragraphs:
            number = paragraph.numbering_text or ""
            blocks.append(
                "<div style='margin-bottom:12px;line-height:1.65;'>"
                f"<b>{escape(number)}</b>"
                f"{_highlight_labels(paragraph.text, comparison.paragraph_labels)}"
                "</div>"
            )
        self.paragraph_text.setHtml("".join(blocks))
        mode = "沿用上一段參閱圖式" if comparison.inherited_reference else "本段明確指定"
        overall = (
            "圖式缺失標號"
            if comparison.labels_not_in_drawings or comparison.missing_figures
            else "一致"
        )
        if not self.ocr_results:
            overall = "等待 OCR 結果"
        self.paragraph_meta.setText(
            f"{mode}：圖{_joined(comparison.referenced_figures)}　｜　"
            f"自動合併比對：{overall}"
        )
        self.paragraph_labels.setText(
            f"段落擷取標號：{_joined(comparison.paragraph_labels)}"
        )
        self.auto_match_button.setEnabled(
            bool(comparison.referenced_figures and self.figure_pages)
        )
        self._update_manual_comparison()

    def _show_selected_figure(self, _index=-1):
        page = self._selected_figure_page()
        if page is None:
            self.figure_meta.setText("請先到「圖式標號」完成辨識。")
            self.figure_labels.setText("圖式辨識標號：無")
            self.image_preview.set_image("")
            self._update_manual_comparison()
            return
        self.figure_meta.setText(
            f"Pic_{page.page_index:02d}　｜　本頁包含圖號："
            f"{_joined(page.figures)}　｜　{page.image_name}"
        )
        self.figure_labels.setText(
            f"圖式辨識標號：{_joined(page.labels)}"
        )
        self.image_preview.set_image(page.image_path)
        self._update_manual_comparison()

    def select_referenced_figure_page(self):
        comparison = self._selected_comparison()
        if comparison is None or not comparison.referenced_figures:
            return False
        first_referenced_figure = min(comparison.referenced_figures)
        for index, page in enumerate(self.figure_pages):
            if first_referenced_figure in page.figures:
                self.figure_combo.setCurrentIndex(index)
                return True
        return False

    def _update_manual_comparison(self):
        comparison = self._selected_comparison()
        page = self._selected_figure_page()
        if comparison is None:
            self.comparison_result.setText(
                "<b>圖式缺失標號</b><br>請先選擇實施方式段落。"
            )
            return
        if not self.ocr_results:
            self.comparison_result.setText(
                "<span style='font-size:20pt;font-weight:900;color:#92400e;'>"
                "圖式缺失標號：等待 OCR</span><br>"
                "請先到「圖式標號」完成圖片辨識。"
            )
            return

        paragraph_only = list(comparison.labels_not_in_drawings)
        if paragraph_only:
            verdict = (
                "<span style='font-size:20pt;color:#b91c1c;font-weight:900'>"
                f"圖式缺失標號：{escape(_joined(paragraph_only))}</span>"
            )
            explanation = "上述標號出現在本實施方式段落，但未出現在其參閱圖式的 OCR 結果中。"
        else:
            verdict = (
                "<span style='font-size:20pt;color:#15803d;font-weight:900'>"
                "圖式缺失標號：無</span>"
            )
            explanation = "本段落出現的標號均可在其參閱圖式中找到。"
        missing_figure_text = ""
        if comparison.missing_figures:
            missing_figure_text = (
                "<br><span style='color:#b91c1c;font-weight:800'>"
                f"尚未找到參閱圖式：圖{escape(_joined(comparison.missing_figures))}"
                "</span>"
            )
        if page is None:
            relation = "右側目前沒有可顯示的圖片。"
        elif set(comparison.referenced_figures).intersection(page.figures):
            relation = "右側目前顯示本段所參閱的圖式頁。"
        else:
            relation = "右側目前為人工切換的其他圖式頁；缺失結果仍以本段全部參閱圖式合併計算。"
        self.comparison_result.setText(
            f"{verdict}<br><span style='font-size:13pt;font-weight:700'>"
            f"{escape(explanation)}</span>{missing_figure_text}<br>"
            f"<span style='color:#475569'>{escape(relation)}</span>"
        )
