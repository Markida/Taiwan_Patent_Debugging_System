from copy import deepcopy
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget,
    QPushButton,
    QLabel,
    QTextEdit,
    QFileDialog,
    QVBoxLayout,
    QHBoxLayout,
    QMessageBox,
    QLineEdit,
    QFrame,
    QSplitter,
    QScrollArea,
    QApplication,
    QAbstractItemView,
    QDoubleSpinBox,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtCore import Qt

from app.config import PDF_DPI, MAX_IMAGE_PREVIEW_ZOOM
from app.paths import get_models_dir, get_output_base_dir

from features.patent_ocr.ocr_engine import get_device_status_text
from features.patent_ocr.ocr_worker import BatchRecognitionWorker
from features.patent_ocr.pdf_tools import convert_pdf_to_images
from features.patent_ocr.image_tools import rotate_image_clockwise_90
from features.patent_ocr.label_parser import normalize_label_text, parse_reference_items
from features.patent_ocr.label_matcher import build_result_summary_html
from features.patent_ocr.review_exporter import (
    ReviewPackageTransferError,
    export_review_package,
)
from features.patent_ocr.review_tools import detection_confidence
from features.patent_review.symbol_transfer import (
    FULL_SYMBOL_SOURCE,
    REPRESENTATIVE_SYMBOL_SOURCE,
    SOURCE_TITLES,
)
from features.patent_review.figure_ocr_checker import (
    infer_figure_number,
    parse_figure_number_mapping,
)
from ui.feature_navigation import FeatureNavigationBar
from ui.file_drop import SingleFileDropController


REVIEW_DATA_ROLE = Qt.ItemDataRole.UserRole
REVIEW_SORT_ROLE = Qt.ItemDataRole.UserRole + 1
PICTURE_COLUMN = 0
LABEL_COLUMN = 1
CONFIDENCE_COLUMN = 2
STATUS_COLUMN = 3


class ReviewOrderTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        own_value = self.data(REVIEW_SORT_ROLE)
        other_value = other.data(REVIEW_SORT_ROLE)
        if own_value is not None and other_value is not None:
            return tuple(own_value) < tuple(other_value)
        return super().__lt__(other)


class ZoomableImageScrollArea(QScrollArea):
    """Turn an ordinary mouse wheel into a preview zoom control."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom_handler = None

    def wheelEvent(self, event):
        angle_delta = event.angleDelta().y()
        pixel_delta = event.pixelDelta().y()
        if callable(self.zoom_handler) and (angle_delta or pixel_delta):
            # A conventional mouse-wheel notch is 120 angle units.  The pixel
            # fallback also supports touchpads which do not provide angleDelta.
            steps = (
                angle_delta / 120.0
                if angle_delta
                else pixel_delta / 30.0
            )
            self.zoom_handler(steps, event.position().toPoint())
            event.accept()
            return
        super().wheelEvent(event)


class RecognitionPage(QWidget):
    def __init__(self, go_home_callback):
        super().__init__()

        self.go_home_callback = go_home_callback
        self.open_feature_callback = None

        self.model_path = ""
        self.image_paths = []
        self.source_image_paths = []
        self.all_results = []
        self.reference_items = []
        self.deleted_review_entries = {}
        self._updating_review_table = False
        self._review_sequence = 0
        self._result_view_current_only = False
        self._sort_result_numbers = True
        self.workflow_context = None
        self.document_symbol_transfer = None
        self._reference_symbol_source = FULL_SYMBOL_SOURCE
        self._document_reference_drafts = {}
        self._updating_reference_source = False
        self.figure_number_mappings = {}
        self.current_preview_index = 0
        self.current_pixmap = None
        self._preview_image_key = None
        self._preview_fit_scale = 1.0
        self.preview_zoom_factor = 1.0

        self.build_ui()
        self.file_drop_controller = SingleFileDropController(
            self,
            allowed_suffix=".pdf",
            file_type_name="PDF 文件",
            on_file=self.import_pdf,
        )

    def set_open_feature_callback(self, callback):
        """Allow direct navigation back to the document review workflow."""

        self.open_feature_callback = callback

    def set_feature_navigation(self, features, open_feature_callback):
        self.feature_navigation.configure(features, open_feature_callback)

    def open_patent_review(self):
        if callable(self.open_feature_callback):
            self.open_feature_callback("patent_review")
        else:
            self.go_home_callback()

    def set_workflow_context(self, workflow_context):
        """Subscribe to a reviewed symbol list published by the document page."""

        self.workflow_context = workflow_context
        workflow_context.subscribe_symbol_transfer(
            self.load_document_symbol_transfer
        )
        if self.all_results:
            self._publish_reviewed_ocr_results()

    def _publish_reviewed_ocr_results(self):
        """Keep document-to-drawing checks synchronized with manual edits."""

        if self.workflow_context is None or not self.all_results:
            return
        self.workflow_context.publish_ocr_results(
            self.collect_reviewed_results()
        )

    def load_document_symbol_transfer(self, transfer):
        """Receive both document lists and default to the complete list."""

        self.document_symbol_transfer = transfer
        self._document_reference_drafts = {
            source: transfer.reference_text_for(source)
            for source in (FULL_SYMBOL_SOURCE, REPRESENTATIVE_SYMBOL_SOURCE)
        }
        ready_sources = [
            source
            for source in (FULL_SYMBOL_SOURCE, REPRESENTATIVE_SYMBOL_SOURCE)
            if transfer.is_source_ready(source)
        ]
        if not ready_sources:
            self.reference_source_button.setEnabled(False)
            self.reference_text.setToolTip(
                "文件符號清單仍有未解決問題，尚未自動覆蓋目前清單。"
            )
            return

        default_source = (
            FULL_SYMBOL_SOURCE
            if FULL_SYMBOL_SOURCE in ready_sources
            else REPRESENTATIVE_SYMBOL_SOURCE
        )
        self._apply_document_reference_source(default_source, save_current=False)
        self.reference_source_button.setEnabled(
            sum(
                transfer.is_source_ready(source)
                for source in (FULL_SYMBOL_SOURCE, REPRESENTATIVE_SYMBOL_SOURCE)
            )
            > 1
        )

    def _apply_document_reference_source(self, symbol_source, *, save_current=True):
        transfer = self.document_symbol_transfer
        if transfer is None:
            return
        if save_current and self._reference_symbol_source in self._document_reference_drafts:
            self._document_reference_drafts[
                self._reference_symbol_source
            ] = self.reference_text.toPlainText()

        entries = transfer.entries_for(symbol_source)
        if not entries:
            QMessageBox.information(
                self,
                "沒有可用清單",
                f"文件沒有可擷取的「{SOURCE_TITLES[symbol_source]}」。",
            )
            self._sync_reference_source_button(self._reference_symbol_source)
            return

        self._reference_symbol_source = symbol_source
        self.reference_text.setPlainText(
            self._document_reference_drafts.get(
                symbol_source,
                transfer.reference_text_for(symbol_source),
            )
        )
        self.reference_items = transfer.reference_items_for(symbol_source)
        self._sync_reference_source_button(symbol_source)
        source_name = transfer.file_name or "專利文件"
        self.reference_text.setToolTip(
            f"已由文件偵錯功能自動匯入：{source_name}\n"
            f"目前使用：{SOURCE_TITLES[symbol_source]}，共 {len(entries)} 個標號。\n"
            "兩套清單的手動修改會分別保留。"
        )

    def _sync_reference_source_button(self, symbol_source):
        self._updating_reference_source = True
        self.reference_source_button.setChecked(
            symbol_source == REPRESENTATIVE_SYMBOL_SOURCE
        )
        self.reference_source_button.setText("完整/代表圖 符號切換")
        self.reference_source_button.setToolTip(
            "在完整符號說明與代表圖符號說明之間切換。\n"
            f"目前使用：{SOURCE_TITLES[symbol_source]}"
        )
        self._updating_reference_source = False

    def on_reference_source_toggled(self, checked):
        if self._updating_reference_source:
            return
        symbol_source = (
            REPRESENTATIVE_SYMBOL_SOURCE if checked else FULL_SYMBOL_SOURCE
        )
        self._apply_document_reference_source(symbol_source)

    def build_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(10, 4, 10, 10)
        main_layout.setSpacing(6)

        self.feature_navigation = FeatureNavigationBar(
            self.go_home_callback,
            "patent_ocr",
        )
        main_layout.addWidget(self.feature_navigation)

        header_layout = QHBoxLayout()

        title = QLabel("圖片標號識別")
        title.setObjectName("PageTitle")
        title.setMaximumHeight(34)
        title.setContentsMargins(0, 0, 0, 0)

        header_layout.addWidget(title)
        header_layout.addStretch()

        main_layout.addLayout(header_layout)

        model_layout = QHBoxLayout()

        self.model_line = QLineEdit()
        self.model_line.setPlaceholderText("請選擇 YOLO 模型，例如 best.pt")
        self.model_line.setObjectName("InputLine")
        self.model_line.setMaximumHeight(34)

        recommended_models = [
            get_models_dir() / "patent_label_group_v1.onnx",
            get_models_dir() / "patent_label_group_v1.pt",
            get_models_dir() / "patent_char_v4_company_approved_recall.onnx",
            get_models_dir() / "patent_char_v4_company_approved_recall.pt",
            get_models_dir() / "patent_char_v3_consensus.onnx",
            get_models_dir() / "patent_char_v3_consensus.pt",
        ]
        for recommended_model in recommended_models:
            if recommended_model.exists():
                self.model_path = str(recommended_model)
                self.model_line.setText(self.model_path)
                break

        self.model_button = QPushButton("選擇模型")
        self.model_button.setObjectName("ToolButton")
        self.model_button.setMaximumHeight(34)
        self.model_button.clicked.connect(self.select_model)

        model_layout.addWidget(QLabel("模型："))
        model_layout.addWidget(self.model_line)
        model_layout.addWidget(self.model_button)

        main_layout.addLayout(model_layout)

        file_layout = QHBoxLayout()

        self.image_line = QLineEdit()
        self.image_line.setPlaceholderText(
            "請選擇圖片／PDF，或將一份 PDF 拖入此視窗"
        )
        self.image_line.setObjectName("InputLine")
        self.image_line.setMaximumHeight(34)

        self.image_button = QPushButton("選擇多張圖片")
        self.image_button.setObjectName("ToolButton")
        self.image_button.setMaximumHeight(34)
        self.image_button.clicked.connect(self.select_images)

        self.pdf_button = QPushButton("選擇 PDF")
        self.pdf_button.setObjectName("ToolButton")
        self.pdf_button.setMaximumHeight(34)
        self.pdf_button.clicked.connect(self.select_pdf)

        file_layout.addWidget(QLabel("檔案："))
        file_layout.addWidget(self.image_line)
        file_layout.addWidget(self.image_button)
        file_layout.addWidget(self.pdf_button)

        main_layout.addLayout(file_layout)

        self.preview_action_layout = QHBoxLayout()

        self.run_button = QPushButton("第一步.圖片數字英文辨識")
        self.run_button.setObjectName("RecognitionStartButton")
        self.run_button.clicked.connect(self.run_batch_recognition)

        self.compare_button = QPushButton("第二步.辨識結果與標號清單比對")
        self.compare_button.setObjectName("PrimaryButton")
        self.compare_button.clicked.connect(self.compare_with_reference_list)
        self.compare_button.setEnabled(False)

        self.prev_button = QPushButton("上一張")
        self.prev_button.setObjectName("SecondaryButton")
        self.prev_button.clicked.connect(self.show_prev_preview)
        self.prev_button.setEnabled(False)

        self.next_button = QPushButton("下一張")
        self.next_button.setObjectName("SecondaryButton")
        self.next_button.clicked.connect(self.show_next_preview)
        self.next_button.setEnabled(False)

        self.figure_mapping_line = QLineEdit()
        self.figure_mapping_line.setObjectName("InputLine")
        self.figure_mapping_line.setPlaceholderText("例如 1,2 或 1-4")
        self.figure_mapping_line.setMaximumWidth(112)
        self.figure_mapping_line.setMaximumHeight(34)
        self.figure_mapping_line.setEnabled(False)
        self.figure_mapping_line.setToolTip(
            "設定目前這一頁包含的圖號，例如 1、1,2 或 1-4。\n"
            "單圖頁採完全一致比對；多圖頁只引用部分圖式時會自動改採子集合比對。"
        )
        self.figure_mapping_line.editingFinished.connect(
            self.apply_current_figure_mapping
        )

        self.rotate_button = QPushButton("右旋 90°")
        self.rotate_button.setObjectName("SecondaryButton")
        self.rotate_button.clicked.connect(self.rotate_current_image_clockwise)
        self.rotate_button.setEnabled(False)

        self.show_all_boxes_button = QPushButton("隱藏一般辨識框")
        self.show_all_boxes_button.setObjectName("SecondaryButton")
        self.show_all_boxes_button.setCheckable(True)
        self.show_all_boxes_button.setChecked(True)
        self.show_all_boxes_button.setToolTip(
            "開啟時顯示所有辨識框；關閉時只保留低信心紅框。\n"
            "在右側選取標號列，可用粗藍框快速定位。"
        )
        self.show_all_boxes_button.toggled.connect(self.on_show_all_boxes_toggled)
        self.show_all_boxes_button.setEnabled(False)

        self.export_review_button = QPushButton("匯出錯誤回報")
        self.export_review_button.setObjectName("SecondaryButton")
        self.export_review_button.clicked.connect(self.export_review_data)
        self.export_review_button.setEnabled(False)

        for btn in [
            self.run_button,
            self.compare_button,
            self.prev_button,
            self.next_button,
            self.rotate_button,
            self.show_all_boxes_button,
            self.export_review_button,
        ]:
            btn.setMaximumHeight(36)

        self.preview_action_layout.addWidget(QLabel("圖片預覽："))
        self.preview_action_layout.addWidget(self.prev_button)
        self.preview_action_layout.addWidget(self.next_button)
        self.preview_action_layout.addWidget(QLabel("本頁包含圖號："))
        self.preview_action_layout.addWidget(self.figure_mapping_line)
        self.preview_action_layout.addWidget(self.rotate_button)
        self.preview_action_layout.addWidget(self.show_all_boxes_button)
        self.preview_action_layout.addWidget(self.export_review_button)
        self.preview_action_layout.addStretch()
        self.preview_action_layout.addWidget(self.run_button)
        self.preview_action_layout.addWidget(self.compare_button)

        main_layout.addLayout(self.preview_action_layout)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("MainSplitter")

        left_panel = QFrame()
        left_panel.setObjectName("Panel")

        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(6)

        self.preview_title = QLabel("原圖預覽")
        self.preview_title.setObjectName("PanelTitle")
        self.preview_title.setMaximumHeight(30)

        self.preview_zoom_status = QLabel("滾輪縮放：適合視窗")
        self.preview_zoom_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.preview_zoom_status.setToolTip(
            "將滑鼠移到圖片上，向上滾動放大、向下滾動縮小。"
        )

        preview_title_layout = QHBoxLayout()
        preview_title_layout.setContentsMargins(0, 0, 0, 0)
        preview_title_layout.addWidget(self.preview_title)
        preview_title_layout.addStretch()
        preview_title_layout.addWidget(self.preview_zoom_status)

        self.image_preview = QLabel("請先選擇圖片或 PDF")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.setObjectName("ImagePreview")
        self.image_preview.setMinimumSize(1, 1)
        self.image_preview.resize(600, 500)
        self.image_preview.setToolTip(
            "滑鼠滾輪：放大／縮小；放大後可使用捲軸移動檢視範圍。"
        )

        self.scroll_area = ZoomableImageScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setWidget(self.image_preview)
        self.scroll_area.setObjectName("ImageScrollArea")
        self.scroll_area.zoom_handler = self.zoom_preview_at

        left_layout.addLayout(preview_title_layout)
        left_layout.addWidget(self.scroll_area)

        left_panel.setLayout(left_layout)

        right_panel = QFrame()
        right_panel.setObjectName("Panel")

        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(6)

        reference_title = QLabel("標號清單輸入（第二階段使用）")
        reference_title.setObjectName("PanelTitle")
        reference_title.setMaximumHeight(30)

        reference_header = QHBoxLayout()
        reference_header.addWidget(reference_title)
        reference_header.addStretch()
        self.reference_source_button = QPushButton("完整/代表圖 符號切換")
        self.reference_source_button.setObjectName("SecondaryButton")
        self.reference_source_button.setCheckable(True)
        self.reference_source_button.setChecked(False)
        self.reference_source_button.setEnabled(False)
        self.reference_source_button.setMaximumHeight(30)
        self.reference_source_button.setToolTip(
            "在完整符號說明與代表圖符號說明之間切換；預設使用完整符號說明。"
        )
        self.reference_source_button.toggled.connect(
            self.on_reference_source_toggled
        )
        reference_header.addWidget(self.reference_source_button)

        self.reference_text = QTextEdit()
        self.reference_text.setObjectName("ReferenceText")
        self.reference_text.setFixedHeight(78)
        self.reference_text.setPlaceholderText(
            "貼上標號清單，例如：1:蓋子、A:凹槽、7':第一凸部、10A:連接件\n"
            "完成辨識與人工確認後，再進行第二階段比對。"
        )

        review_header = QHBoxLayout()
        review_title = QLabel("辨識暫存（雙擊標號可修改）")
        review_title.setObjectName("PanelTitle")
        review_header.addWidget(review_title)
        review_header.addStretch()
        review_header.addWidget(QLabel("低信心 <"))

        self.confidence_threshold = QDoubleSpinBox()
        self.confidence_threshold.setRange(0.0, 1.0)
        self.confidence_threshold.setDecimals(2)
        self.confidence_threshold.setSingleStep(0.05)
        self.confidence_threshold.setValue(0.60)
        self.confidence_threshold.setMaximumWidth(74)
        self.confidence_threshold.valueChanged.connect(
            self.on_confidence_threshold_changed
        )
        review_header.addWidget(self.confidence_threshold)

        self.review_table = QTableWidget(0, 4)
        self.review_table.setObjectName("ReviewTable")
        self.review_table.setHorizontalHeaderLabels(
            ["圖片", "辨識標號（可修改）", "信心度", "狀態"]
        )
        self.review_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.review_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.review_table.verticalHeader().setVisible(False)
        self.review_table.horizontalHeader().setSectionResizeMode(
            PICTURE_COLUMN, QHeaderView.ResizeToContents
        )
        self.review_table.horizontalHeader().setSectionResizeMode(
            LABEL_COLUMN, QHeaderView.Stretch
        )
        self.review_table.horizontalHeader().setSectionResizeMode(
            CONFIDENCE_COLUMN, QHeaderView.ResizeToContents
        )
        self.review_table.horizontalHeader().setSectionResizeMode(
            STATUS_COLUMN, QHeaderView.ResizeToContents
        )
        self.review_table.setMinimumHeight(190)
        self.review_table.itemChanged.connect(self.on_review_item_changed)
        self.review_table.itemSelectionChanged.connect(
            self.on_review_selection_changed
        )

        review_actions = QHBoxLayout()
        self.add_label_button = QPushButton("新增目前圖片標號")
        self.add_label_button.setObjectName("ToolButton")
        self.add_label_button.clicked.connect(self.add_manual_label)
        self.add_label_button.setEnabled(False)
        self.delete_label_button = QPushButton("刪除選取標號")
        self.delete_label_button.setObjectName("ToolButton")
        self.delete_label_button.clicked.connect(self.delete_selected_labels)
        self.delete_label_button.setEnabled(False)
        review_actions.addWidget(self.add_label_button)
        review_actions.addWidget(self.delete_label_button)
        review_actions.addStretch()

        result_header = QHBoxLayout()
        self.result_title = QLabel("辨識結果")
        self.result_title.setObjectName("PanelTitle")
        self.result_title.setMaximumHeight(30)

        self.result_view_button = QPushButton("只看目前圖片")
        self.result_view_button.setObjectName("SecondaryButton")
        self.result_view_button.setCheckable(True)
        self.result_view_button.setChecked(False)
        self.result_view_button.setEnabled(False)
        self.result_view_button.setMaximumHeight(30)
        self.result_view_button.setToolTip(
            "在全部圖片結果與左側目前圖片結果之間切換。"
        )
        self.result_view_button.toggled.connect(
            self.on_result_view_toggled
        )

        self.result_sort_button = QPushButton("恢復原始順序")
        self.result_sort_button.setObjectName("SecondaryButton")
        self.result_sort_button.setCheckable(True)
        self.result_sort_button.setChecked(True)
        self.result_sort_button.setEnabled(False)
        self.result_sort_button.setMaximumHeight(30)
        self.result_sort_button.setToolTip(
            "逐位比較並排序，例如：1、121、131、2、21、3、33、34。"
        )
        self.result_sort_button.toggled.connect(
            self.on_result_sort_toggled
        )
        result_header.addWidget(self.result_title)
        result_header.addStretch()
        result_header.addWidget(self.result_sort_button)
        result_header.addWidget(self.result_view_button)

        self.result_text = QTextEdit()
        self.result_text.setObjectName("ResultText")
        self.result_text.setReadOnly(True)
        self.result_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.result_text.setPlaceholderText(
            "辨識結果與標號清單比對結果會顯示在這裡。"
        )

        right_layout.addLayout(review_header)
        right_layout.addWidget(self.review_table)
        right_layout.addLayout(review_actions)
        right_layout.addLayout(reference_header)
        right_layout.addWidget(self.reference_text)
        right_layout.addLayout(result_header)
        right_layout.addWidget(self.result_text)

        right_panel.setLayout(right_layout)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([760, 510])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        main_layout.addWidget(splitter)

        self.setLayout(main_layout)

    def select_model(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "選擇 YOLO 模型",
            "",
            "YOLO Model (*.onnx *.pt)"
        )

        if file_path:
            self.model_path = file_path
            self.model_line.setText(file_path)

    def _initialize_figure_number_mappings(self, image_paths):
        self.figure_number_mappings = {
            index: [infer_figure_number(path, index + 1)]
            for index, path in enumerate(image_paths)
        }
        self._sync_figure_mapping_control(self.current_preview_index)

    def _figure_numbers_for_index(self, image_index):
        return list(
            self.figure_number_mappings.get(image_index, [image_index + 1])
        )

    def _set_result_figure_mapping(self, result, image_index):
        figures = self._figure_numbers_for_index(image_index)
        result["figure_numbers"] = figures
        if len(figures) == 1:
            result["figure_number"] = figures[0]
        else:
            result.pop("figure_number", None)

    def _sync_figure_mapping_control(self, image_index):
        has_image = bool(self.image_paths) and 0 <= image_index < len(self.image_paths)
        self.figure_mapping_line.setEnabled(has_image)
        if not has_image:
            self.figure_mapping_line.clear()
            return
        figures = self._figure_numbers_for_index(image_index)
        self.figure_mapping_line.setText(",".join(str(number) for number in figures))

    def apply_current_figure_mapping(self):
        if not self.image_paths:
            return True
        image_index = max(
            0,
            min(self.current_preview_index, len(self.image_paths) - 1),
        )
        previous = self._figure_numbers_for_index(image_index)
        try:
            figures = parse_figure_number_mapping(
                self.figure_mapping_line.text()
            )
        except ValueError as error:
            self.figure_mapping_line.setText(
                ",".join(str(number) for number in previous)
            )
            QMessageBox.warning(self, "圖號格式錯誤", str(error))
            return False

        self.figure_number_mappings[image_index] = figures
        self.figure_mapping_line.setText(
            ",".join(str(number) for number in figures)
        )
        if image_index < len(self.all_results):
            self._set_result_figure_mapping(
                self.all_results[image_index],
                image_index,
            )
            self._publish_reviewed_ocr_results()
        return True

    def select_images(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "選擇一張或多張圖片",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp)"
        )

        if file_paths:
            self.source_image_paths = list(file_paths)
            self.image_paths = list(file_paths)
            self.reset_review_state()
            self._initialize_figure_number_mappings(file_paths)

            if len(file_paths) == 1:
                self.image_line.setText(file_paths[0])
            else:
                self.image_line.setText(f"已選擇 {len(file_paths)} 張圖片")

            self.current_preview_index = 0
            self.show_original_image(0)

            self.prev_button.setEnabled(len(file_paths) > 1)
            self.next_button.setEnabled(len(file_paths) > 1)
            self.rotate_button.setEnabled(True)

    def select_pdf(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "選擇 PDF 檔案",
            "",
            "PDF Files (*.pdf)"
        )

        if not file_path:
            return

        self.import_pdf(file_path)

    def import_pdf(self, file_path):
        """Import a PDF from either the dialog or drag-and-drop."""

        pdf_path = Path(file_path)
        if pdf_path.suffix.lower() != ".pdf":
            QMessageBox.warning(
                self,
                "PDF 格式錯誤",
                "OCR 頁面的拖放匯入僅接受 .pdf 檔案。\n\n"
                f"選取檔案：{pdf_path.name}",
            )
            return False

        try:
            self.result_text.setText("正在將 PDF 轉換成圖片，請稍候...")
            QApplication.processEvents()

            pdf_image_paths = convert_pdf_to_images(
                pdf_path=str(pdf_path),
                output_root=get_output_base_dir() / "pdf_pages",
                dpi=PDF_DPI
            )

            if not pdf_image_paths:
                QMessageBox.warning(self, "PDF 轉換失敗", "沒有從 PDF 轉出任何圖片。")
                return False

            self.image_paths = pdf_image_paths
            self.source_image_paths = list(pdf_image_paths)
            self.reset_review_state()
            self._initialize_figure_number_mappings(pdf_image_paths)
            self.current_preview_index = 0

            self.image_line.setText(
                f"已匯入 PDF：{pdf_path.name}，共 {len(pdf_image_paths)} 頁"
            )

            self.result_text.setText(
                f"PDF 匯入完成：{pdf_path.name}\n"
                f"已轉換頁數：{len(pdf_image_paths)}\n"
                f"輸出位置：{get_output_base_dir() / 'pdf_pages'}\n\n"
                f"請確認左側圖片後，按「第一步.圖片數字英文辨識」。"
            )

            self.show_original_image(0)

            self.prev_button.setEnabled(len(pdf_image_paths) > 1)
            self.next_button.setEnabled(len(pdf_image_paths) > 1)
            self.rotate_button.setEnabled(True)

            QMessageBox.information(
                self,
                "PDF 匯入完成",
                f"已將 PDF 轉換成 {len(pdf_image_paths)} 張圖片，可以開始辨識。"
            )
            return True

        except Exception as e:
            QMessageBox.critical(self, "PDF 匯入錯誤", f"{e}")
            self.result_text.setText(f"PDF 匯入失敗：\n{e}")
            return False

    def run_batch_recognition(self):
        if not self.model_line.text():
            QMessageBox.warning(
                self,
                "缺少模型",
                "請先選擇 YOLO 模型 .onnx 或 .pt 檔。",
            )
            return

        if not self.source_image_paths:
            QMessageBox.warning(self, "缺少檔案", "請先選擇圖片或 PDF。")
            return

        if not self.apply_current_figure_mapping():
            return

        self.model_path = self.model_line.text()
        self.reference_items = []
        self.deleted_review_entries = {}
        self.review_table.setRowCount(0)
        self.show_all_boxes_button.setChecked(True)
        self.result_view_button.setChecked(False)
        self.result_sort_button.setChecked(True)

        self.result_text.setText(
            f"第一階段：圖片數字英文辨識中，請稍候...\n"
            f"目前運算模式：{get_device_status_text()}"
        )
        self.result_title.setText("辨識進度")

        self.run_button.setEnabled(False)
        self.compare_button.setEnabled(False)
        self.show_all_boxes_button.setEnabled(False)
        self.result_view_button.setEnabled(False)
        self.result_sort_button.setEnabled(False)
        self.export_review_button.setEnabled(False)
        self.add_label_button.setEnabled(False)
        self.delete_label_button.setEnabled(False)
        self.review_table.setEnabled(False)
        self.figure_mapping_line.setEnabled(False)

        self.worker = BatchRecognitionWorker(
            image_paths=self.source_image_paths,
            model_path=self.model_path,
        )

        self.worker.progress_signal.connect(self.on_progress)
        self.worker.finished_signal.connect(self.on_batch_finished)
        self.worker.error_signal.connect(self.on_error)
        self.worker.start()

    def on_progress(self, message):
        self.result_text.setText(message)

    def on_batch_finished(self, results):
        for image_index, result in enumerate(results):
            result["source_image_name"] = Path(
                result.get("original_image_path", result.get("image_path", ""))
            ).name
            result["image_name"] = f"Pic_{image_index + 1:02d}"
            self._set_result_figure_mapping(result, image_index)
            for detection in result.get("detections", []):
                detection.setdefault(
                    "original_label",
                    detection.get("label", detection.get("number", "")),
                )
                detection.setdefault("manual_edited", False)
                detection.setdefault("manual_added", False)

        self.all_results = results
        self.image_paths = [result["image_path"] for result in results]
        self.current_preview_index = 0
        self._result_view_current_only = False
        self._sort_result_numbers = True
        self.result_view_button.blockSignals(True)
        self.result_view_button.setChecked(False)
        self.result_view_button.setText("只看目前圖片")
        self.result_view_button.blockSignals(False)
        self.result_sort_button.blockSignals(True)
        self.result_sort_button.setChecked(True)
        self.result_sort_button.setText("恢復原始順序")
        self.result_sort_button.blockSignals(False)
        self.populate_review_table()

        self.reference_items = []
        self.refresh_result_display()
        self.result_title.setText("圖片辨識結果（確認後再進行第二階段比對）")

        self.run_button.setEnabled(True)
        self.compare_button.setEnabled(True)
        self.show_all_boxes_button.setEnabled(True)
        self.result_view_button.setEnabled(True)
        self.result_sort_button.setEnabled(True)
        self.export_review_button.setEnabled(True)
        self.add_label_button.setEnabled(True)
        self.delete_label_button.setEnabled(True)
        self.review_table.setEnabled(True)
        self.figure_mapping_line.setEnabled(True)

        if self.all_results:
            self.show_original_image(0)
            self._publish_reviewed_ocr_results()

        QMessageBox.information(
            self,
            "辨識完成",
            f"已完成 {len(self.all_results)} 張圖片辨識。\n\n"
            f"低於 {self.confidence_threshold.value():.2f} 的標號已用紅框標示，"
            "請先確認或修改，再進行第二階段比對。"
        )

    def on_error(self, error_message):
        QMessageBox.critical(self, "辨識錯誤", error_message)
        self.result_text.setText(f"辨識失敗：\n{error_message}")

        self.run_button.setEnabled(True)
        self.review_table.setEnabled(True)
        self.figure_mapping_line.setEnabled(bool(self.image_paths))

    def reset_review_state(self):
        self.all_results = []
        self.reference_items = []
        self.deleted_review_entries = {}
        self._review_sequence = 0
        self._sort_result_numbers = True
        self._updating_review_table = True
        self.review_table.setRowCount(0)
        self._updating_review_table = False
        self.result_text.clear()
        self.result_title.setText("辨識結果")
        self.compare_button.setEnabled(False)
        self.show_all_boxes_button.setChecked(True)
        self.show_all_boxes_button.setEnabled(False)
        self.result_view_button.setChecked(False)
        self.result_view_button.setEnabled(False)
        self.result_sort_button.setChecked(True)
        self.result_sort_button.setEnabled(False)
        self.export_review_button.setEnabled(False)
        self.add_label_button.setEnabled(False)
        self.delete_label_button.setEnabled(False)
        if self.workflow_context is not None:
            self.workflow_context.clear_ocr_results()

    def populate_review_table(self):
        self._updating_review_table = True
        self.review_table.setRowCount(0)
        self._review_sequence = 0
        self.deleted_review_entries = {
            image_index: []
            for image_index in range(len(self.all_results))
        }
        for image_index, result in enumerate(self.all_results):
            for detection in result.get("detections", []):
                self.insert_review_row(image_index, detection)
        for row in range(self.review_table.rowCount()):
            self.apply_review_row_style(row)
        self.sort_review_rows_by_picture_priority()
        self._updating_review_table = False

    def insert_review_row(self, image_index, detection, manual_added=False):
        row = self.review_table.rowCount()
        self.review_table.insertRow(row)
        picture_name = f"Pic_{image_index + 1:02d}"

        picture_item = QTableWidgetItem(picture_name)
        picture_item.setFlags(
            picture_item.flags() & ~Qt.ItemFlag.ItemIsEditable
        )
        picture_font = picture_item.font()
        picture_font.setBold(True)
        picture_item.setFont(picture_font)

        label = str(detection.get("label", detection.get("number", "")))
        label_item = QTableWidgetItem(label)
        metadata = {
            "image_index": int(image_index),
            "detection": deepcopy(detection),
            "original_label": str(detection.get("original_label", label)),
            "manual_added": bool(manual_added),
        }
        self._review_sequence += 1
        sequence = self._review_sequence
        review_id = detection.get("review_id")
        if not review_id:
            review_id = f"{image_index}:{sequence}"
        metadata["review_id"] = review_id
        metadata["review_sequence"] = sequence
        metadata["detection"]["review_id"] = review_id
        label_item.setData(REVIEW_DATA_ROLE, metadata)

        if manual_added:
            confidence_text = "—"
        else:
            confidence_text = f"{detection_confidence(detection):.2f}"
        confidence_item = ReviewOrderTableWidgetItem(confidence_text)
        confidence_item.setData(
            REVIEW_SORT_ROLE,
            (1, int(image_index), sequence),
        )
        confidence_item.setFlags(
            confidence_item.flags() & ~Qt.ItemFlag.ItemIsEditable
        )
        confidence_item.setTextAlignment(Qt.AlignCenter)

        status_item = QTableWidgetItem("")
        status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

        original_name = self.all_results[image_index].get(
            "source_image_name", picture_name
        )
        picture_item.setToolTip(f"原始檔名：{original_name}")

        self.review_table.setItem(row, PICTURE_COLUMN, picture_item)
        self.review_table.setItem(row, LABEL_COLUMN, label_item)
        self.review_table.setItem(row, CONFIDENCE_COLUMN, confidence_item)
        self.review_table.setItem(row, STATUS_COLUMN, status_item)

        if not self._updating_review_table:
            self.apply_review_row_style(row)
        return row

    def sort_review_rows_by_picture_priority(self):
        """Keep low-confidence labels first, ordered by picture and detection."""

        self.review_table.blockSignals(True)
        threshold = self.confidence_threshold.value()
        for row in range(self.review_table.rowCount()):
            label_item = self.review_table.item(row, LABEL_COLUMN)
            confidence_item = self.review_table.item(row, CONFIDENCE_COLUMN)
            if label_item is None or confidence_item is None:
                continue
            metadata = label_item.data(REVIEW_DATA_ROLE) or {}
            detection = metadata.get("detection", {})
            manual_added = bool(metadata.get("manual_added"))
            low_confidence = (
                not manual_added
                and detection_confidence(detection) < threshold
            )
            confidence_item.setData(
                REVIEW_SORT_ROLE,
                (
                    0 if low_confidence else 1,
                    int(metadata.get("image_index", 0)),
                    int(metadata.get("review_sequence", row)),
                ),
            )
        self.review_table.sortItems(
            CONFIDENCE_COLUMN,
            Qt.SortOrder.AscendingOrder,
        )
        self.review_table.blockSignals(False)

    def apply_review_row_style(self, row):
        label_item = self.review_table.item(row, LABEL_COLUMN)
        status_item = self.review_table.item(row, STATUS_COLUMN)
        if label_item is None or status_item is None:
            return

        metadata = label_item.data(REVIEW_DATA_ROLE) or {}
        detection = metadata.get("detection", {})
        manual_added = bool(metadata.get("manual_added"))
        current_label = normalize_label_text(label_item.text().strip())
        original_label = normalize_label_text(metadata.get("original_label", ""))
        low_confidence = (
            not manual_added
            and detection_confidence(detection) < self.confidence_threshold.value()
        )
        manually_edited = (
            not manual_added
            and bool(current_label)
            and current_label != original_label
        )

        if not current_label:
            status = "請輸入標號"
            background = QColor("#fee2e2")
            foreground = QColor("#b91c1c")
        elif manual_added:
            status = "手動新增"
            background = QColor("#dbeafe")
            foreground = QColor("#1d4ed8")
        elif low_confidence and manually_edited:
            status = "已修正（原低信心）"
            background = QColor("#fee2e2")
            foreground = QColor("#b91c1c")
        elif low_confidence:
            status = "低信心，請確認"
            background = QColor("#fee2e2")
            foreground = QColor("#b91c1c")
        elif manually_edited:
            status = "已修正"
            background = QColor("#dcfce7")
            foreground = QColor("#166534")
        else:
            status = "正常"
            background = QColor("#ffffff")
            foreground = QColor("#111827")

        self.review_table.blockSignals(True)
        status_item.setText(status)
        for column in range(self.review_table.columnCount()):
            item = self.review_table.item(row, column)
            if item is not None:
                item.setBackground(background)
                item.setForeground(foreground)
        font = label_item.font()
        font.setBold(low_confidence or manual_added or manually_edited)
        label_item.setFont(font)
        self.review_table.blockSignals(False)

    def collect_reviewed_results(self):
        reviewed_results = deepcopy(self.all_results)
        detections_by_image = {
            image_index: []
            for image_index in range(len(reviewed_results))
        }

        for row in range(self.review_table.rowCount()):
            label_item = self.review_table.item(row, LABEL_COLUMN)
            if label_item is None:
                continue
            metadata = label_item.data(REVIEW_DATA_ROLE) or {}
            image_index = int(metadata.get("image_index", -1))
            if image_index not in detections_by_image:
                continue
            label = normalize_label_text(label_item.text().strip())
            if not label:
                continue

            detection = deepcopy(metadata.get("detection", {}))
            original_label = normalize_label_text(
                metadata.get("original_label", "")
            )
            manual_added = bool(metadata.get("manual_added"))
            detection["label"] = label
            detection["number"] = label
            detection["original_label"] = original_label
            detection["manual_added"] = manual_added
            detection["review_id"] = metadata.get("review_id")
            detection["manual_edited"] = (
                not manual_added and label != original_label
            )
            detections_by_image[image_index].append(detection)

        for image_index, result in enumerate(reviewed_results):
            detections = detections_by_image[image_index]
            numbers = [detection["label"] for detection in detections]
            result["detections"] = detections
            result["numbers"] = numbers
            result["labels"] = numbers
            result["number_count"] = len(numbers)
            result["review_deleted"] = deepcopy(
                self.deleted_review_entries.get(image_index, [])
            )
            self._set_result_figure_mapping(result, image_index)

        return reviewed_results

    def refresh_detection_summary(self):
        if not self.all_results:
            return
        self.reference_items = []
        self.refresh_result_display()
        self.result_title.setText("圖片辨識結果（修改後請重新進行第二階段比對）")

    def refresh_result_display(self):
        if not self.all_results:
            return
        reviewed_results = self.collect_reviewed_results()
        include_global_summary = True
        if self._result_view_current_only:
            image_index = max(
                0,
                min(self.current_preview_index, len(reviewed_results) - 1),
            )
            reviewed_results = [reviewed_results[image_index]]
            include_global_summary = False
        self.result_text.setHtml(
            build_result_summary_html(
                reviewed_results,
                self.reference_items,
                include_global_summary=include_global_summary,
                sort_numbers=self._sort_result_numbers,
            )
        )

    def on_result_view_toggled(self, checked):
        self._result_view_current_only = bool(checked)
        self.result_view_button.setText(
            "顯示全部結果" if checked else "只看目前圖片"
        )
        self.refresh_result_display()

    def on_result_sort_toggled(self, checked):
        self._sort_result_numbers = bool(checked)
        self.result_sort_button.setText(
            "恢復原始順序" if checked else "數字由小到大"
        )
        self.refresh_result_display()

    def on_review_item_changed(self, item):
        if self._updating_review_table or item.column() != LABEL_COLUMN:
            return
        self.apply_review_row_style(item.row())
        self.refresh_detection_summary()
        self.show_original_image(self.current_preview_index)
        self._publish_reviewed_ocr_results()

    def add_manual_label(self):
        if not self.all_results:
            return
        image_index = max(
            0,
            min(self.current_preview_index, len(self.all_results) - 1),
        )
        row = self.insert_review_row(
            image_index,
            {
                "label": "",
                "number": "",
                "original_label": "",
                "manual_added": True,
            },
            manual_added=True,
        )
        self.review_table.setCurrentCell(row, LABEL_COLUMN)
        self.review_table.editItem(self.review_table.item(row, LABEL_COLUMN))

    def delete_selected_labels(self):
        rows = sorted(
            {index.row() for index in self.review_table.selectionModel().selectedRows()},
            reverse=True,
        )
        if not rows:
            QMessageBox.information(self, "尚未選取", "請先選取要刪除的標號列。")
            return

        next_row = min(rows)
        self._updating_review_table = True
        for row in rows:
            label_item = self.review_table.item(row, LABEL_COLUMN)
            metadata = label_item.data(REVIEW_DATA_ROLE) if label_item else {}
            metadata = metadata or {}
            if not metadata.get("manual_added"):
                image_index = int(metadata.get("image_index", -1))
                if image_index >= 0:
                    deleted = deepcopy(metadata.get("detection", {}))
                    deleted["original_label"] = metadata.get(
                        "original_label",
                        deleted.get("label", ""),
                    )
                    deleted["deleted"] = True
                    self.deleted_review_entries.setdefault(image_index, []).append(
                        deleted
                    )
            self.review_table.removeRow(row)
        self._updating_review_table = False
        if self.review_table.rowCount():
            next_row = min(next_row, self.review_table.rowCount() - 1)
            self.review_table.clearSelection()
            self.review_table.setCurrentCell(next_row, LABEL_COLUMN)
            self.review_table.selectRow(next_row)
        self.refresh_detection_summary()
        self.show_original_image(self.current_preview_index)
        self._publish_reviewed_ocr_results()

    def on_confidence_threshold_changed(self, _value):
        for row in range(self.review_table.rowCount()):
            self.apply_review_row_style(row)
        self.sort_review_rows_by_picture_priority()
        if self.image_paths:
            self.show_original_image(self.current_preview_index)

    def on_show_all_boxes_toggled(self, checked):
        self.show_all_boxes_button.setText(
            "隱藏一般辨識框" if checked else "顯示全部辨識框"
        )
        if self.image_paths:
            self.show_original_image(self.current_preview_index)

    def selected_review_ids(self, image_index):
        selected_ids = set()
        for index in self.review_table.selectionModel().selectedRows():
            label_item = self.review_table.item(index.row(), LABEL_COLUMN)
            metadata = label_item.data(REVIEW_DATA_ROLE) if label_item else {}
            metadata = metadata or {}
            if int(metadata.get("image_index", -1)) == image_index:
                selected_ids.add(metadata.get("review_id"))
        return selected_ids

    def on_review_selection_changed(self):
        if self._updating_review_table or not self.all_results:
            return
        selected_rows = self.review_table.selectionModel().selectedRows()
        if not selected_rows:
            self.show_original_image(self.current_preview_index)
            return
        label_item = self.review_table.item(
            selected_rows[0].row(),
            LABEL_COLUMN,
        )
        metadata = label_item.data(REVIEW_DATA_ROLE) if label_item else {}
        image_index = int((metadata or {}).get("image_index", -1))
        if 0 <= image_index < len(self.image_paths):
            self.current_preview_index = image_index
            self.show_original_image(image_index)

    def compare_with_reference_list(self):
        if not self.all_results:
            QMessageBox.warning(self, "尚未辨識", "請先完成第一階段圖片辨識。")
            return

        reference_raw_text = self.reference_text.toPlainText().strip()
        if not reference_raw_text:
            QMessageBox.warning(
                self,
                "缺少標號清單",
                "請先輸入標號清單，再進行第二階段比對。",
            )
            return
        reference_items = parse_reference_items(reference_raw_text)
        if not reference_items:
            QMessageBox.warning(
                self,
                "標號清單格式錯誤",
                "程式沒有成功擷取到任何標號。\n\n"
                "請使用類似：1:蓋子、A:凹槽、7':第一凸部。",
            )
            return

        self.reference_items = reference_items
        self.refresh_result_display()
        self.result_title.setText("辨識結果與標號清單比對")

    def export_review_data(self):
        if not self.all_results:
            return
        try:
            output_path = export_review_package(
                self.collect_reviewed_results(),
                confidence_threshold=self.confidence_threshold.value(),
            )
            QMessageBox.information(
                self,
                "錯誤回報已傳送",
                "已傳送到公司區域網路。\n\n"
                f"資料夾：{output_path.parent}\n"
                f"檔案名稱：{output_path.name}\n\n"
                "內容包含問題圖片、裁切小圖、框座標、原始判斷、"
                "使用者修正值與信心度。",
            )
        except ValueError as error:
            QMessageBox.information(self, "沒有可匯出的錯誤", str(error))
        except ReviewPackageTransferError as error:
            QMessageBox.warning(self, "錯誤回報尚未送出", str(error))
        except Exception as error:
            QMessageBox.critical(self, "匯出錯誤回報失敗", str(error))

    def draw_low_confidence_boxes(
        self,
        pixmap,
        image_index,
        coordinate_scale_x=1.0,
        coordinate_scale_y=None,
    ):
        if not self.all_results or pixmap.isNull():
            return pixmap
        reviewed_results = self.collect_reviewed_results()
        if image_index >= len(reviewed_results):
            return pixmap

        if coordinate_scale_y is None:
            coordinate_scale_y = coordinate_scale_x

        threshold = self.confidence_threshold.value()
        show_all_boxes = self.show_all_boxes_button.isChecked()
        selected_ids = self.selected_review_ids(image_index)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen_width = max(3, int(max(pixmap.width(), pixmap.height()) / 600))
        font = QFont(painter.font())
        font.setBold(True)
        font.setPointSize(max(9, min(16, int(pixmap.width() / 120))))
        painter.setFont(font)

        for detection in reviewed_results[image_index].get("detections", []):
            if detection.get("manual_added"):
                continue
            confidence = detection_confidence(detection)
            low_confidence = confidence < threshold
            selected = detection.get("review_id") in selected_ids
            if not low_confidence and not show_all_boxes:
                continue
            try:
                x1 = round(float(detection["x1"]) * coordinate_scale_x)
                y1 = round(float(detection["y1"]) * coordinate_scale_y)
                x2 = round(float(detection["x2"]) * coordinate_scale_x)
                y2 = round(float(detection["y2"]) * coordinate_scale_y)
            except (KeyError, TypeError, ValueError):
                continue
            if selected:
                color = QColor("#2563eb")
                current_pen_width = pen_width * 2
            elif low_confidence:
                color = QColor("#dc2626")
                current_pen_width = pen_width
            else:
                color = QColor("#16a34a")
                current_pen_width = pen_width
            painter.setPen(QPen(color, current_pen_width))
            painter.drawRect(x1, y1, max(1, x2 - x1), max(1, y2 - y1))
            label = detection.get("label", detection.get("number", ""))
            painter.drawText(x1, max(18, y1 - 5), f"{label} {confidence:.2f}")

        painter.end()
        return pixmap

    def show_original_image(self, index):
        if not self.image_paths:
            return

        index = max(0, min(index, len(self.image_paths) - 1))
        self.current_preview_index = index
        self._sync_figure_mapping_control(index)
        if self._result_view_current_only and self.all_results:
            self.refresh_result_display()

        image_path = self.image_paths[index]
        preview_key = (index, str(image_path))
        image_changed = preview_key != self._preview_image_key

        pixmap = QPixmap(image_path)

        if pixmap.isNull():
            self.current_pixmap = None
            self._preview_image_key = None
            self.image_preview.clear()
            self.image_preview.setText("圖片讀取失敗")
            return

        # Keep the full-resolution source untouched.  Every zoom level is
        # rendered again from this pixmap instead of enlarging a prior preview.
        self.current_pixmap = pixmap
        self._preview_image_key = preview_key
        if image_changed:
            self.preview_zoom_factor = 1.0
        self.update_scaled_preview()

        if self.all_results and index < len(self.all_results):
            result = self.all_results[index]
            display_name = result.get("image_name", f"Pic_{index + 1:02d}")
            source_name = result.get("source_image_name", Path(image_path).name)
            self.preview_title.setText(f"{display_name}　{source_name}")
        else:
            self.preview_title.setText(
                f"原圖預覽：{index + 1}/{len(self.image_paths)}　{Path(image_path).name}"
            )

    def update_scaled_preview(self):
        if self.current_pixmap is None or self.current_pixmap.isNull():
            return

        viewport_size = self.scroll_area.viewport().size()

        available_w = max(100, viewport_size.width() - 8)
        available_h = max(100, viewport_size.height() - 8)

        pix_w = self.current_pixmap.width()
        pix_h = self.current_pixmap.height()

        if pix_w <= 0 or pix_h <= 0:
            return

        fit_scale = min(
            available_w / pix_w,
            available_h / pix_h
        )

        # Do not enlarge a small source merely to fill the panel.  User zoom is
        # separate and explicit, while the initial view stays sharp.
        self._preview_fit_scale = min(fit_scale, 1.0)
        scale = self._preview_fit_scale * self.preview_zoom_factor

        scaled_w = max(1, int(pix_w * scale))
        scaled_h = max(1, int(pix_h * scale))

        scaled = self.current_pixmap.scaled(
            scaled_w,
            scaled_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        # Paint the boxes after scaling so their lines and confidence labels
        # remain crisp at every zoom level.
        scaled = self.draw_low_confidence_boxes(
            scaled,
            self.current_preview_index,
            coordinate_scale_x=scaled.width() / pix_w,
            coordinate_scale_y=scaled.height() / pix_h,
        )
        self.image_preview.setPixmap(scaled)
        self.image_preview.resize(scaled.size())
        if abs(self.preview_zoom_factor - 1.0) < 0.01:
            zoom_text = "適合視窗"
        else:
            zoom_text = f"{round(self.preview_zoom_factor * 100)}%"
        self.preview_zoom_status.setText(f"滾輪縮放：{zoom_text}")

    def zoom_preview_at(self, steps, viewport_position):
        """Zoom around the pixel currently beneath the mouse cursor."""

        if self.current_pixmap is None or self.current_pixmap.isNull():
            return

        old_width = max(1, self.image_preview.width())
        old_height = max(1, self.image_preview.height())
        local_anchor = self.image_preview.mapFrom(
            self.scroll_area.viewport(),
            viewport_position,
        )
        anchor_x = min(1.0, max(0.0, local_anchor.x() / old_width))
        anchor_y = min(1.0, max(0.0, local_anchor.y() / old_height))

        new_zoom = self.preview_zoom_factor * (1.18 ** float(steps))
        new_zoom = min(MAX_IMAGE_PREVIEW_ZOOM, max(0.25, new_zoom))
        if abs(new_zoom - self.preview_zoom_factor) < 0.0001:
            return

        self.preview_zoom_factor = new_zoom
        self.update_scaled_preview()

        # Keep the same source-image point beneath the cursor after resizing.
        horizontal_bar = self.scroll_area.horizontalScrollBar()
        vertical_bar = self.scroll_area.verticalScrollBar()
        horizontal_bar.setValue(
            round(anchor_x * self.image_preview.width() - viewport_position.x())
        )
        vertical_bar.setValue(
            round(anchor_y * self.image_preview.height() - viewport_position.y())
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_scaled_preview()

    def show_prev_preview(self):
        if not self.image_paths:
            return

        new_index = self.current_preview_index - 1

        if new_index < 0:
            new_index = len(self.image_paths) - 1

        self.show_original_image(new_index)

    def show_next_preview(self):
        if not self.image_paths:
            return

        new_index = self.current_preview_index + 1

        if new_index >= len(self.image_paths):
            new_index = 0

        self.show_original_image(new_index)

    def rotate_current_image_clockwise(self):
        if not self.image_paths:
            QMessageBox.warning(self, "沒有圖片", "請先選擇圖片或 PDF。")
            return

        try:
            current_path = self.image_paths[self.current_preview_index]

            rotated_path = rotate_image_clockwise_90(current_path)

            self.image_paths[self.current_preview_index] = rotated_path
            if self.current_preview_index < len(self.source_image_paths):
                self.source_image_paths[self.current_preview_index] = rotated_path
            self.reset_review_state()

            self.result_text.setText(
                f"已將目前圖片向右旋轉 90 度。\n\n"
                f"原圖片：{Path(current_path).name}\n"
                f"旋轉後圖片：{Path(rotated_path).name}\n"
                f"輸出位置：{Path(rotated_path).parent}\n\n"
                f"請重新按「第一步.圖片數字英文辨識」。"
            )

            self.show_original_image(self.current_preview_index)

        except Exception as e:
            QMessageBox.critical(
                self,
                "圖片旋轉失敗",
                f"無法旋轉目前圖片：\n{e}"
            )
