"""Interactive side-by-side review of embodiment paragraphs and drawing OCR."""

from __future__ import annotations

from html import escape
from pathlib import Path
import re
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.config import MAX_IMAGE_PREVIEW_ZOOM
from features.patent_review.figure_ocr_checker import (
    EmbodimentFigureComparison,
    build_embodiment_figure_comparisons,
    build_ocr_figure_pages,
    drawing_description_figure_names,
    drawing_description_figure_numbers,
    mapped_ocr_figure_numbers,
    parse_figure_number_mapping,
)
from features.patent_review.symbol_transfer import extract_document_symbols
from ui.feature_navigation import FeatureNavigationBar


def _joined(values) -> str:
    return "、".join(str(value) for value in values) if values else "無"


def _editable_figure_text(values) -> str:
    return f"\u5716{_joined(values)}" if values else "\u672a\u6307\u5b9a"


def _named_figures_text(values, names_by_figure) -> str:
    output = []
    for value in values:
        name = str(names_by_figure.get(value, "") or "").strip()
        output.append(f"圖{value}（{name}）" if name else f"圖{value}")
    return "、".join(output) if output else "無"


def _parse_editable_figure_text(value: object):
    text = str(value or "").strip()
    if text in {"", "\u7121", "\u672a\u6307\u5b9a", "\u7121\u5716\u5f0f"}:
        return []
    return parse_figure_number_mapping(text)


def _symbol_name_map(document) -> dict[str, tuple[str, ...]]:
    """Return every complete-symbol name associated with each normalized label."""

    if document is None:
        return {}
    names_by_label: dict[str, list[str]] = {}
    for entry in extract_document_symbols(document).full_entries:
        label = str(entry.label or "").strip()
        name = str(entry.name or "").strip()
        if not label or not name:
            continue
        names = names_by_label.setdefault(label, [])
        if name not in names:
            names.append(name)
    return {label: tuple(names) for label, names in names_by_label.items()}


def _labels_with_component_names(values, names_by_label) -> str:
    """Format a missing label as ``1（底座）`` when its name is available."""

    formatted = []
    for value in values:
        label = str(value)
        names = names_by_label.get(label, ())
        if names:
            formatted.append(f"{label}（{'／'.join(names)}）")
        else:
            formatted.append(label)
    return "、".join(formatted) if formatted else "無"


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
    """Hold the full-resolution drawing used for repeated zoom rendering."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = QPixmap()
        self._source_path = ""
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setObjectName("ComparisonImage")
        self.setText("尚未載入圖式 OCR 結果")

    def set_image(self, path: str) -> bool:
        self._source_path = str(path or "")
        self._source = QPixmap(str(path or ""))
        if self._source.isNull():
            self.clear()
            self.setText(
                "找不到圖式圖片"
                if path
                else "此筆 OCR 結果沒有可顯示的圖片路徑"
            )
            return False
        return True

    @property
    def source_pixmap(self):
        return self._source


class ComparisonImageScrollArea(QScrollArea):
    """Use the mouse wheel to zoom the drawing under the cursor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom_handler = None

    def wheelEvent(self, event):
        angle_delta = event.angleDelta().y()
        pixel_delta = event.pixelDelta().y()
        if callable(self.zoom_handler) and (angle_delta or pixel_delta):
            steps = (
                angle_delta / 120.0
                if angle_delta
                else pixel_delta / 30.0
            )
            self.zoom_handler(steps, event.position().toPoint())
            event.accept()
            return
        super().wheelEvent(event)


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
        self.figure_names_by_number = {}
        self.symbol_names_by_label: dict[str, tuple[str, ...]] = {}
        self.figure_zoom_factor = 1.0
        self._figure_fit_scale = 1.0
        self._figure_image_path = ""
        self._loaded_document_identity = None
        self._reference_overrides: dict[tuple, tuple] = {}
        self._automatic_references: dict[tuple, tuple] = {}
        self._comparison_keys: list[tuple] = []
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

        self.figure_count_warning = QLabel()
        self.figure_count_warning.setObjectName("ComparisonFigureCountWarning")
        self.figure_count_warning.setWordWrap(True)
        self.figure_count_warning.setContentsMargins(8, 6, 8, 6)
        self.figure_count_warning.setToolTip(
            "數量依唯一圖號計算；例如圖8A到圖8E會計為5個圖號。"
        )
        self.figure_count_warning.setStyleSheet(
            "#ComparisonFigureCountWarning {"
            " background: #fff7ed; color: #9a3412;"
            " border: 2px solid #f97316; border-radius: 8px;"
            " font-weight: 800;"
            "}"
        )
        self.figure_count_warning.hide()
        left_layout.addWidget(self.figure_count_warning)

        self.paragraph_workspace_splitter = QSplitter(Qt.Vertical)
        self.paragraph_workspace_splitter.setChildrenCollapsible(False)

        paragraph_list_panel = QFrame()
        paragraph_list_panel.setObjectName("ComparisonParagraphListPanel")
        paragraph_list_layout = QVBoxLayout(paragraph_list_panel)
        paragraph_list_layout.setContentsMargins(0, 0, 0, 0)
        paragraph_list_layout.setSpacing(4)

        edit_hint = QLabel(
            "雙擊「參閱圖式」可逐段修改（例如：圖1、2、1-4 或 8A-8E）。"
        )
        edit_hint.setObjectName("ComparisonEditHint")
        edit_hint.setWordWrap(True)
        paragraph_list_layout.addWidget(edit_hint)

        self.paragraph_table = QTableWidget(0, 3)
        self.paragraph_table.setObjectName("ReviewTable")
        self.paragraph_table.setHorizontalHeaderLabels(
            ["段落", "參閱圖式（可編輯）", "圖式缺失標號"]
        )
        self.paragraph_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
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
        self.paragraph_table.itemChanged.connect(
            self._on_referenced_figures_item_changed
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
        right_title_row = QHBoxLayout()
        right_title = QLabel("圖式圖片")
        right_title.setObjectName("PanelTitle")
        right_title_row.addWidget(right_title)
        self.previous_figure_button = QPushButton("←")
        self.previous_figure_button.setObjectName("SecondaryButton")
        self.previous_figure_button.setToolTip("顯示上一張圖式")
        self.previous_figure_button.setEnabled(False)
        self.previous_figure_button.clicked.connect(self.show_previous_figure)
        right_title_row.addWidget(self.previous_figure_button)
        self.next_figure_button = QPushButton("→")
        self.next_figure_button.setObjectName("SecondaryButton")
        self.next_figure_button.setToolTip("顯示下一張圖式")
        self.next_figure_button.setEnabled(False)
        self.next_figure_button.clicked.connect(self.show_next_figure)
        right_title_row.addWidget(self.next_figure_button)
        right_title_row.addStretch()
        self.zoom_out_button = QPushButton("－")
        self.zoom_out_button.setObjectName("SecondaryButton")
        self.zoom_out_button.setToolTip("縮小圖式")
        self.zoom_out_button.clicked.connect(
            lambda: self.zoom_figure_at(-1, self._figure_viewport_center())
        )
        right_title_row.addWidget(self.zoom_out_button)
        self.zoom_reset_button = QPushButton("適合視窗")
        self.zoom_reset_button.setObjectName("SecondaryButton")
        self.zoom_reset_button.clicked.connect(self.reset_figure_zoom)
        right_title_row.addWidget(self.zoom_reset_button)
        self.zoom_in_button = QPushButton("＋")
        self.zoom_in_button.setObjectName("SecondaryButton")
        self.zoom_in_button.setToolTip("放大圖式")
        self.zoom_in_button.clicked.connect(
            lambda: self.zoom_figure_at(1, self._figure_viewport_center())
        )
        right_title_row.addWidget(self.zoom_in_button)
        self.figure_zoom_status = QLabel("滾輪縮放：適合視窗")
        self.figure_zoom_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.figure_zoom_status.setToolTip(
            "將滑鼠移到圖式上，向上滾動放大、向下滾動縮小。"
        )
        right_title_row.addWidget(self.figure_zoom_status)
        right_layout.addLayout(right_title_row)
        self.figure_combo = QComboBox()
        self.figure_combo.setObjectName("InputLine")
        self.figure_combo.currentIndexChanged.connect(self._show_selected_figure)
        right_layout.addWidget(self.figure_combo)
        self.figure_meta = QLabel("尚未取得圖式 OCR 結果")
        self.figure_meta.setWordWrap(True)
        right_layout.addWidget(self.figure_meta)
        # Keep OCR labels in their own information strip above the drawing.
        # This guarantees that the text never occupies the image viewport,
        # including on compact or high-DPI displays.
        self.figure_labels = QLabel("圖式辨識標號：無")
        self.figure_labels.setObjectName("ComparisonFigureLabels")
        self.figure_labels.setWordWrap(True)
        self.figure_labels.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.figure_labels.setContentsMargins(8, 5, 8, 5)
        self.figure_labels.setStyleSheet(
            "#ComparisonFigureLabels {"
            " background: #f8fafc; color: #1e293b;"
            " border: 1px solid #cbd5e1; border-radius: 6px;"
            "}"
        )
        right_layout.addWidget(self.figure_labels)
        self.image_preview = ScaledImageLabel()
        self.image_preview.setToolTip(
            "滑鼠滾輪：放大／縮小；放大後可使用捲軸移動。"
        )
        self.figure_scroll_area = ComparisonImageScrollArea()
        self.figure_scroll_area.setObjectName("ComparisonImageScrollArea")
        self.figure_scroll_area.setWidgetResizable(False)
        self.figure_scroll_area.setAlignment(Qt.AlignCenter)
        self.figure_scroll_area.setWidget(self.image_preview)
        self.figure_scroll_area.zoom_handler = self.zoom_figure_at
        # Let the drawing viewport share the available height with the result
        # panel.  A 400px fixed minimum made this page's layout taller than the
        # application's 960x640 minimum window and caused widgets to overlap
        # when QStackedWidget constrained the page.
        self.figure_scroll_area.setMinimumSize(240, 120)
        right_layout.addWidget(self.figure_scroll_area, 1)
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
        identity = self._document_identity(document)
        if identity != self._loaded_document_identity:
            self._reference_overrides.clear()
            self._automatic_references.clear()
        self._loaded_document_identity = identity
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
        self._comparison_keys = self._build_comparison_keys(self.comparisons)
        self._automatic_references = {
            key: tuple(comparison.referenced_figures)
            for key, comparison in zip(self._comparison_keys, self.comparisons)
        }
        valid_comparison_keys = set(self._comparison_keys)
        self._reference_overrides = {
            key: figures
            for key, figures in self._reference_overrides.items()
            if key in valid_comparison_keys
        }
        self.symbol_names_by_label = _symbol_name_map(self.document)
        self.figure_names_by_number = drawing_description_figure_names(
            self.document
        )
        self.figure_pages = build_ocr_figure_pages(self.ocr_results)
        self._update_figure_count_warning()
        for row, key in enumerate(self._comparison_keys):
            figures = self._reference_overrides.get(key)
            if figures is None:
                continue
            self._recalculate_comparison(
                self.comparisons[row],
                list(figures),
                inherited_reference=False,
            )

        self.paragraph_combo.blockSignals(True)
        self.paragraph_combo.clear()
        for row, comparison in enumerate(self.comparisons):
            self.paragraph_combo.addItem(
                self._paragraph_combo_text(row, comparison),
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
            figures = _editable_figure_text(comparison.referenced_figures)
            missing = self._comparison_status_text(comparison)
            for column, value in enumerate((location, figures, missing)):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, row)
                if column == 1:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                    self._style_reference_item(
                        item,
                        manual=self._is_manual_row(row),
                    )
                else:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if column == 2:
                    self._style_comparison_status_item(item, comparison)
                self.paragraph_table.setItem(row, column, item)
        if self.comparisons:
            self.paragraph_table.selectRow(self.paragraph_combo.currentIndex())
        self.paragraph_table.blockSignals(False)

        self.figure_combo.blockSignals(True)
        self.figure_combo.clear()
        for page in self.figure_pages:
            named_figures = _named_figures_text(
                page.figures,
                self.figure_names_by_number,
            )
            self.figure_combo.addItem(
                f"Pic_{page.page_index:02d} · {named_figures} · {page.image_name}",
                page.page_index,
            )
            self.figure_combo.setItemData(
                self.figure_combo.count() - 1,
                f"{named_figures}\n原始檔名：{page.image_name}",
                Qt.ToolTipRole,
            )
        self.figure_combo.setEnabled(bool(self.figure_pages))
        has_multiple_figures = len(self.figure_pages) > 1
        self.previous_figure_button.setEnabled(has_multiple_figures)
        self.next_figure_button.setEnabled(has_multiple_figures)
        if self.figure_pages:
            self.figure_combo.setCurrentIndex(
                min(max(previous_figure, 0), len(self.figure_pages) - 1)
            )
        self.figure_combo.blockSignals(False)

        self._set_source_status()
        self._sync_paragraph_table_selection()
        self._show_selected_paragraph()
        self.select_referenced_figure_page()
        self._show_selected_figure()

    def _update_figure_count_warning(self):
        if self.document is None or not self.ocr_results:
            self.figure_count_warning.clear()
            self.figure_count_warning.hide()
            return

        description_count = len(
            drawing_description_figure_numbers(self.document)
        )
        mapped_count = len(mapped_ocr_figure_numbers(self.ocr_results))
        if description_count == mapped_count:
            self.figure_count_warning.clear()
            self.figure_count_warning.hide()
            return

        self.figure_count_warning.setText(
            "⚠ 圖式數量不一致：文件「圖式簡單說明」共 "
            f"{description_count} 個圖號；「圖式標號」頁目前共設定 "
            f"{mapped_count} 個圖號。請確認是否漏載圖式、重複設定或圖號設定錯誤。"
        )
        self.figure_count_warning.show()

    @staticmethod
    def _document_identity(document):
        if document is None:
            return None
        return (
            str(getattr(document, "source_path", "") or ""),
            str(getattr(document, "sha256", "") or ""),
            len(getattr(document, "paragraphs", ()) or ()),
        )

    @staticmethod
    def _build_comparison_keys(comparisons):
        keys = []
        occurrences = {}
        for comparison in comparisons:
            paragraph_paths = tuple(
                str(getattr(paragraph, "source_path", "") or paragraph.index)
                for paragraph in comparison.paragraphs
            )
            reference_path = str(
                getattr(comparison.reference_paragraph, "source_path", "") or ""
            )
            base = (
                comparison.group_index,
                paragraph_paths,
                tuple(comparison.referenced_figures),
                str(comparison.scope_text or ""),
                reference_path,
            )
            occurrence = occurrences.get(base, 0)
            occurrences[base] = occurrence + 1
            keys.append((*base, occurrence))
        return keys

    def _is_manual_row(self, row):
        return (
            0 <= row < len(self._comparison_keys)
            and self._comparison_keys[row] in self._reference_overrides
        )

    def _set_source_status(self, message: str = ""):
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
        suffix = f"　｜　{message}" if message else ""
        self.source_status.setText(f"{document_text}　｜　{ocr_text}{suffix}")

    def _paragraph_combo_text(self, row, comparison):
        first = comparison.paragraphs[0]
        location = first.numbering_text or f"段落 {comparison.group_index}"
        figures = _editable_figure_text(comparison.referenced_figures)
        if self._is_manual_row(row):
            suffix = "（人工）"
        elif comparison.inherited_reference:
            suffix = "（沿用）"
        else:
            suffix = ""
        return f"{location} · 參閱{figures}{suffix}"

    def _comparison_status_text(self, comparison):
        if not comparison.referenced_figures:
            return "未指定圖式"
        if not self.ocr_results:
            return "等待 OCR"
        if comparison.missing_figures:
            return "缺少圖" + _joined(comparison.missing_figures)
        return _joined(comparison.labels_not_in_drawings)

    def _style_reference_item(self, item, *, manual):
        if manual:
            item.setBackground(QColor("#dbeafe"))
            item.setForeground(QColor("#1d4ed8"))
            item.setToolTip(
                "此段落已人工修改。雙擊可再次編輯；改回自動辨識值即可取消人工設定。"
            )
        else:
            item.setBackground(QBrush())
            item.setForeground(QBrush())
            item.setToolTip(
                "雙擊編輯。可輸入圖1、2、1-4、8A-8E；清空或輸入「未指定」可取消參閱圖式。"
            )

    def _style_comparison_status_item(self, item, comparison):
        item.setBackground(QBrush())
        item.setForeground(QBrush())
        if (
            self.ocr_results
            and comparison.referenced_figures
            and (comparison.labels_not_in_drawings or comparison.missing_figures)
        ):
            item.setBackground(QColor("#fee2e2"))
            item.setForeground(QColor("#991b1b"))

    def _recalculate_comparison(
        self,
        comparison,
        figures,
        *,
        inherited_reference=False,
    ):
        comparison.referenced_figures = list(figures)
        comparison.inherited_reference = bool(inherited_reference)
        figure_set = set(figures)
        relevant_pages = [
            page for page in self.figure_pages if figure_set.intersection(page.figures)
        ]
        drawing_labels = []
        seen_labels = set()
        for page in relevant_pages:
            for label in page.labels:
                if label not in seen_labels:
                    seen_labels.add(label)
                    drawing_labels.append(label)
        comparison.drawing_labels = drawing_labels
        comparison.relevant_page_indices = [
            page.page_index for page in relevant_pages
        ]
        comparison.missing_figures = [
            figure
            for figure in figures
            if not any(figure in page.figures for page in self.figure_pages)
        ]
        comparison.labels_not_in_drawings = (
            [
                label
                for label in comparison.paragraph_labels
                if label not in seen_labels
            ]
            if figures
            else []
        )
        comparison.labels_not_in_paragraph = []
        comparison.comparison_mode = (
            "exact"
            if all(set(page.figures).issubset(figure_set) for page in relevant_pages)
            else "page_subset"
        )

    def _on_referenced_figures_item_changed(self, item):
        if item.column() != 1:
            return
        row = item.row()
        if not (0 <= row < len(self.comparisons)):
            return
        comparison = self.comparisons[row]
        try:
            figures = _parse_editable_figure_text(item.text())
        except ValueError as error:
            self.paragraph_table.blockSignals(True)
            item.setText(_editable_figure_text(comparison.referenced_figures))
            self.paragraph_table.blockSignals(False)
            self._set_source_status(f"參閱圖式格式錯誤：{error}")
            return

        key = self._comparison_keys[row]
        automatic = self._automatic_references.get(key, ())
        if tuple(figures) == automatic:
            self._reference_overrides.pop(key, None)
            self._refresh_sources()
            return

        self._reference_overrides[key] = tuple(figures)
        self._recalculate_comparison(
            comparison,
            figures,
            inherited_reference=False,
        )
        self.paragraph_table.blockSignals(True)
        item.setText(_editable_figure_text(figures))
        self._style_reference_item(item, manual=True)
        status_item = self.paragraph_table.item(row, 2)
        status_item.setText(self._comparison_status_text(comparison))
        self._style_comparison_status_item(status_item, comparison)
        self.paragraph_table.blockSignals(False)
        self.paragraph_combo.setItemText(
            row,
            self._paragraph_combo_text(row, comparison),
        )
        location = self.paragraph_table.item(row, 0).text()
        self._set_source_status(
            f"{location} 的參閱圖式已人工修改為{_editable_figure_text(figures)}"
        )
        if self.paragraph_combo.currentIndex() == row:
            self._show_selected_paragraph()
            if not self.select_referenced_figure_page():
                self._update_manual_comparison()

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
            self._figure_image_path = ""
            self.figure_zoom_factor = 1.0
            self.image_preview.set_image("")
            self._set_zoom_controls_enabled(False)
            self.figure_zoom_status.setText("滾輪縮放：無圖片")
            self._update_manual_comparison()
            return
        named_figures = _named_figures_text(
            page.figures,
            self.figure_names_by_number,
        )
        self.figure_meta.setText(
            f"Pic_{page.page_index:02d}　｜　本頁圖式：{named_figures}　｜　"
            f"原始檔名：{page.image_name}"
        )
        self.figure_labels.setText(
            f"圖式辨識標號：{_joined(page.labels)}"
        )
        image_path = str(page.image_path or "")
        image_changed = image_path != self._figure_image_path
        loaded = self.image_preview.set_image(image_path)
        self._figure_image_path = image_path if loaded else ""
        if image_changed:
            self.figure_zoom_factor = 1.0
        self._set_zoom_controls_enabled(loaded)
        if loaded:
            self.update_scaled_figure()
        else:
            self.figure_zoom_status.setText("滾輪縮放：圖片讀取失敗")
        self._update_manual_comparison()

    def _set_zoom_controls_enabled(self, enabled):
        for button in (
            self.zoom_out_button,
            self.zoom_reset_button,
            self.zoom_in_button,
        ):
            button.setEnabled(bool(enabled))

    def _figure_viewport_center(self):
        return self.figure_scroll_area.viewport().rect().center()

    def reset_figure_zoom(self):
        self.figure_zoom_factor = 1.0
        self.update_scaled_figure()
        self.figure_scroll_area.horizontalScrollBar().setValue(0)
        self.figure_scroll_area.verticalScrollBar().setValue(0)

    def update_scaled_figure(self):
        source = self.image_preview.source_pixmap
        if source.isNull():
            return
        viewport_size = self.figure_scroll_area.viewport().size()
        available_width = max(100, viewport_size.width() - 8)
        available_height = max(100, viewport_size.height() - 8)
        source_width = source.width()
        source_height = source.height()
        if source_width < 1 or source_height < 1:
            return
        fit_scale = min(
            available_width / source_width,
            available_height / source_height,
        )
        self._figure_fit_scale = min(fit_scale, 1.0)
        scale = self._figure_fit_scale * self.figure_zoom_factor
        scaled_width = max(1, int(source_width * scale))
        scaled_height = max(1, int(source_height * scale))
        scaled = source.scaled(
            scaled_width,
            scaled_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image_preview.setPixmap(scaled)
        self.image_preview.resize(scaled.size())
        zoom_text = (
            "適合視窗"
            if abs(self.figure_zoom_factor - 1.0) < 0.01
            else f"{round(self.figure_zoom_factor * 100)}%"
        )
        self.figure_zoom_status.setText(f"滾輪縮放：{zoom_text}")

    def zoom_figure_at(self, steps, viewport_position):
        source = self.image_preview.source_pixmap
        if source.isNull():
            return
        old_width = max(1, self.image_preview.width())
        old_height = max(1, self.image_preview.height())
        local_anchor = self.image_preview.mapFrom(
            self.figure_scroll_area.viewport(),
            viewport_position,
        )
        anchor_x = min(1.0, max(0.0, local_anchor.x() / old_width))
        anchor_y = min(1.0, max(0.0, local_anchor.y() / old_height))
        new_zoom = self.figure_zoom_factor * (1.18 ** float(steps))
        new_zoom = min(MAX_IMAGE_PREVIEW_ZOOM, max(0.25, new_zoom))
        if abs(new_zoom - self.figure_zoom_factor) < 0.0001:
            return
        self.figure_zoom_factor = new_zoom
        self.update_scaled_figure()
        horizontal_bar = self.figure_scroll_area.horizontalScrollBar()
        vertical_bar = self.figure_scroll_area.verticalScrollBar()
        horizontal_bar.setValue(
            round(anchor_x * self.image_preview.width() - viewport_position.x())
        )
        vertical_bar.setValue(
            round(anchor_y * self.image_preview.height() - viewport_position.y())
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_scaled_figure()

    def select_referenced_figure_page(self):
        comparison = self._selected_comparison()
        if comparison is None or not comparison.referenced_figures:
            return False
        # Figure identifiers may include variants such as 3A/3B, so preserve
        # the document order instead of applying a numeric-only ``min``.
        first_referenced_figure = comparison.referenced_figures[0]
        for index, page in enumerate(self.figure_pages):
            if first_referenced_figure in page.figures:
                self.figure_combo.setCurrentIndex(index)
                return True
        return False

    def show_previous_figure(self):
        count = self.figure_combo.count()
        if count < 2:
            return False
        self.figure_combo.setCurrentIndex(
            (self.figure_combo.currentIndex() - 1) % count
        )
        return True

    def show_next_figure(self):
        count = self.figure_combo.count()
        if count < 2:
            return False
        self.figure_combo.setCurrentIndex(
            (self.figure_combo.currentIndex() + 1) % count
        )
        return True

    def _update_manual_comparison(self):
        comparison = self._selected_comparison()
        page = self._selected_figure_page()
        if comparison is None:
            self.comparison_result.setText(
                "<b>圖式缺失標號</b><br>尚未載入可供比對的實施方式段落。"
            )
            return
        if not self.ocr_results:
            self.comparison_result.setText(
                "<span style='font-size:20pt;font-weight:900;color:#92400e;'>"
                "圖式缺失標號：等待 OCR</span><br>"
                "請先到「圖式標號」完成圖片辨識。"
            )
            return
        if not comparison.referenced_figures:
            self.comparison_result.setText(
                "<span style='font-size:20pt;font-weight:900;color:#92400e;'>"
                "參閱圖式：未指定</span><br>"
                "請雙擊左側「參閱圖式」欄位，輸入此段落應比對的圖號。"
            )
            return

        paragraph_only = list(comparison.labels_not_in_drawings)
        if paragraph_only:
            missing_labels = _labels_with_component_names(
                paragraph_only,
                self.symbol_names_by_label,
            )
            verdict = (
                "<span style='font-size:20pt;color:#b91c1c;font-weight:900'>"
                f"圖式缺失標號：{escape(missing_labels)}</span>"
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
