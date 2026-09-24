from copy import deepcopy
from pathlib import Path
from uuid import uuid4

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
    QAbstractItemView,
    QDoubleSpinBox,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QStyle,
    QCheckBox,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtCore import QItemSelectionModel, QSize, QTimer, Qt, Signal

from app.config import PDF_DPI, MAX_IMAGE_PREVIEW_ZOOM
from app.paths import get_models_dir, get_output_base_dir
from app.background_tasks import BackgroundTaskRunner

from features.patent_ocr.ocr_worker import (
    BatchRecognitionWorker,
    is_verified_experimental_figure_heading_model,
    resolve_figure_heading_model_path,
)
from features.patent_ocr.figure_orientation_batch import auto_orient_figure_images
from features.patent_ocr.pdf_tools import convert_pdf_to_images
from features.patent_ocr.image_tools import (
    rotate_image_clockwise_90,
    rotate_image_counterclockwise_90,
)
from features.patent_ocr.label_parser import normalize_label_text, parse_reference_items
from features.patent_ocr.label_matcher import build_result_summary_html
from features.patent_ocr.reference_reconciliation import (
    reconcile_label_to_reference,
)
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
from ui.figure_result_persistence import FigureResultPersistence


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


class RecognitionReviewTable(QTableWidget):
    """Keep Delete intuitive without interfering with in-cell text editing."""

    delete_rows_requested = Signal()

    def keyPressEvent(self, event):
        if (
            event.key() == Qt.Key.Key_Delete
            and self.state() != QAbstractItemView.State.EditingState
        ):
            self.delete_rows_requested.emit()
            event.accept()
            return
        # While a cell editor is open, Qt sends Delete to the editor.  This
        # preserves the user's selected characters instead of deleting a row.
        super().keyPressEvent(event)


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


class ClickableImageLabel(QLabel):
    """Forward preview clicks so boxed detections can select review rows."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.detection_click_handler = None

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and callable(self.detection_click_handler)
        ):
            self.detection_click_handler(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)


class RecognitionPage(QWidget, FigureResultPersistence):
    _result_saved = Signal(object)
    result_save_report = Signal(str)
    orientation_progress = Signal(str)

    def __init__(self, go_home_callback, *, result_store=None):
        super().__init__()

        self.go_home_callback = go_home_callback
        self.open_feature_callback = None
        self._closing = False
        self._ocr_job_active = False
        self._ocr_cancel_requested = False
        self._heading_stage_enabled = False
        self._orientation_stage_pages = []
        self._save_success_reported = False

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
        self._show_all_boxes = True
        self.workflow_context = None
        self.document_symbol_transfer = None
        self._reference_symbol_source = FULL_SYMBOL_SOURCE
        self._document_reference_drafts = {}
        self._updating_reference_source = False
        self.figure_number_mappings = {}
        self.figure_mapping_sources = {}
        self._pending_manual_figure_mappings = set()
        self.current_preview_index = 0
        self.current_pixmap = None
        self._preview_image_key = None
        self._preview_fit_scale = 1.0
        self.preview_zoom_factor = 1.0
        self._review_search_query = ""
        self._review_search_position = None
        self._pdf_tasks = BackgroundTaskRunner(self)
        self._pdf_tasks.succeeded.connect(self._on_pdf_ready)
        self._pdf_tasks.failed.connect(self._on_pdf_error)
        self._pdf_tasks.finished.connect(lambda: self._set_pdf_busy(False))
        self._rotation_tasks = BackgroundTaskRunner(self)
        self._rotation_tasks.succeeded.connect(self._on_rotate_all_ready)
        self._rotation_tasks.failed.connect(self._on_rotate_all_error)
        self._rotation_tasks.finished.connect(
            lambda: self._set_rotation_busy(False)
        )
        self._orientation_tasks = BackgroundTaskRunner(self)
        self._orientation_tasks.succeeded.connect(self._on_auto_orientation_ready)
        self._orientation_tasks.failed.connect(self._on_auto_orientation_error)
        self._orientation_tasks.finished.connect(lambda: self._set_orientation_busy(False))
        self.orientation_progress.connect(self._on_orientation_progress)
        # High-resolution image scaling is deliberately expensive. During a
        # window resize Qt can deliver dozens of resize events in one drag;
        # coalesce them so only the final geometry is rendered.
        self._preview_resize_timer = QTimer(self)
        self._preview_resize_timer.setSingleShot(True)
        self._preview_resize_timer.setInterval(40)
        self._preview_resize_timer.timeout.connect(self.update_scaled_preview)

        self.build_ui()
        self._init_result_persistence(result_store)
        self.file_drop_controller = SingleFileDropController(
            self,
            allowed_suffix=".pdf",
            file_type_name="PDF 文件",
            on_file=self.import_pdf,
        )

    def set_open_feature_callback(self, callback):
        """Allow direct navigation back to the document review workflow."""

        self.open_feature_callback = callback

    def abort_current_workflow(self):
        if self._pdf_tasks.busy:
            self._pdf_tasks.cancel()
            self.result_text.setText("已取消 PDF 匯入，保留原圖片與標號。")
        if self._rotation_tasks.busy:
            self._rotation_tasks.cancel()
            self.result_text.setText("已取消全部圖片旋轉，保留原圖片與標號。")
        if self._orientation_tasks.busy:
            self._orientation_tasks.cancel()
            self.result_text.setText("已取消自動轉正，保留原圖片與標號。")
        worker = getattr(self, "worker", None)
        if worker is not None and (worker.isRunning() or self._ocr_job_active):
            self._ocr_cancel_requested = True
            worker.requestInterruption()

    def shutdown(self):
        if self._closing:
            return
        self._shutdown_result_persistence()
        self._closing = True
        self._preview_resize_timer.stop()
        self._pdf_tasks.shutdown()
        self._rotation_tasks.shutdown()
        self._orientation_tasks.shutdown()
        self.abort_current_workflow()

    def _recognition_is_running(self):
        worker = getattr(self, "worker", None)
        return self._ocr_job_active or (worker is not None and worker.isRunning())

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

        self._queue_result_save()
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
            self.reference_text.clear()
            self.reference_items = []
            self.compare_button.setEnabled(False)
            self.reference_text.setToolTip(
                "文件符號清單仍有未解決問題，目前沒有可用清單。"
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
        if self.all_results:
            self.apply_reference_reconciliation(self.reference_items)
            self.refresh_result_display()
            self.result_title.setText(
                f"{SOURCE_TITLES[symbol_source]}比對結果"
            )
        self.compare_button.setEnabled(
            bool(self.all_results and self.reference_text.toPlainText().strip())
        )
        if symbol_source == REPRESENTATIVE_SYMBOL_SOURCE:
            self._focus_representative_figure()
        self._queue_result_save()

    def _sync_reference_source_button(self, symbol_source):
        self._updating_reference_source = True
        self.reference_source_button.setChecked(
            symbol_source == REPRESENTATIVE_SYMBOL_SOURCE
        )
        self.reference_source_button.setText(
            "切換至完整圖"
            if symbol_source == REPRESENTATIVE_SYMBOL_SOURCE
            else "切換至代表圖"
        )
        representative_figure = str(
            getattr(self.document_symbol_transfer, "representative_figure_number", "")
            or ""
        )
        figure_note = (
            f"\n指定代表圖：圖{representative_figure}"
            if representative_figure
            else ""
        )
        self.reference_source_button.setToolTip(
            "在完整符號說明與代表圖符號說明之間切換。\n"
            f"目前使用：{SOURCE_TITLES[symbol_source]}{figure_note}"
        )
        self._updating_reference_source = False

    def on_reference_source_toggled(self, checked):
        if self._updating_reference_source:
            return
        symbol_source = (
            REPRESENTATIVE_SYMBOL_SOURCE if checked else FULL_SYMBOL_SOURCE
        )
        self._apply_document_reference_source(symbol_source)

    def _representative_image_index(self):
        """Locate the page whose editable mapping contains the designated figure."""

        transfer = self.document_symbol_transfer
        representative_figure = str(
            getattr(transfer, "representative_figure_number", "") or ""
        )
        if not representative_figure or not self.image_paths:
            return None
        try:
            target = parse_figure_number_mapping(representative_figure)[0]
        except (IndexError, ValueError):
            return None
        target_key = str(target).upper()
        for image_index in range(len(self.image_paths)):
            figures = self._figure_numbers_for_index(image_index)
            if any(str(figure).upper() == target_key for figure in figures):
                return image_index
        return None

    def _focus_representative_figure(self):
        """Show the designated drawing and only its representative-list result."""

        image_index = self._representative_image_index()
        if image_index is None:
            return False

        self._result_view_current_only = True
        self.result_view_button.blockSignals(True)
        self.result_view_button.setChecked(True)
        self.result_view_button.setText("顯示完整結果")
        self.result_view_button.blockSignals(False)
        self.show_original_image(image_index)
        if self.all_results:
            self.refresh_result_display()
            representative_figure = getattr(
                self.document_symbol_transfer,
                "representative_figure_number",
                "",
            )
            self.result_title.setText(
                f"代表圖與清單比對結果（圖{representative_figure}）"
            )
        return True

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
        header_layout.addSpacing(10)

        # The production model is selected automatically.  Keep the two
        # controls as hidden compatibility objects because older workflow and
        # tests still read ``model_line``, but no model-selection row consumes
        # screen space in the operator UI.
        self.model_line = QLineEdit()
        self.model_line.setPlaceholderText("請選擇 YOLO 模型，例如 best.pt")
        self.model_line.setObjectName("InputLine")
        self.model_line.setMaximumHeight(34)

        recommended_models = [
            get_models_dir() / "patent_label_group_v2_gold_ft.onnx",
            get_models_dir() / "patent_label_group_v2_gold_ft.pt",
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
        self.model_line.setVisible(False)
        self.model_button.setVisible(False)

        self.image_line = QLineEdit()
        self.image_line.setPlaceholderText(
            "請選擇圖片／PDF，或將一份 PDF 拖入此視窗"
        )
        self.image_line.setObjectName("InputLine")
        self.image_line.setMaximumHeight(34)
        self.image_line.setMinimumWidth(180)

        self.image_button = QPushButton("選擇多張圖片")
        self.image_button.setObjectName("ToolButton")
        self.image_button.setMaximumHeight(34)
        self.image_button.clicked.connect(self.select_images)

        self.pdf_button = QPushButton("選擇 PDF")
        self.pdf_button.setObjectName("ToolButton")
        self.pdf_button.setMaximumHeight(34)
        self.pdf_button.clicked.connect(self.select_pdf)

        header_layout.addWidget(QLabel("檔案："))
        header_layout.addWidget(self.image_line, 1)
        header_layout.addWidget(self.image_button)
        header_layout.addWidget(self.pdf_button)

        main_layout.addLayout(header_layout)

        self.result_save_status = QLabel("完成辨識後會自動保存結果與人工修改", self)
        self.result_save_status.setWordWrap(True)
        self.result_save_status.hide()  # Status remains available to 墨墨 and tests.

        self.preview_action_layout = QHBoxLayout()

        self.run_button = QPushButton("第二步.圖片標號辨識")
        self.run_button.setObjectName("RecognitionStartButton")
        self.run_button.clicked.connect(self.run_batch_recognition)

        self.compare_button = QPushButton("第三步.辨識結果與標號清單比對")
        self.compare_button.setObjectName("RecognitionStartButton")
        self.compare_button.clicked.connect(self.compare_with_reference_list)
        self.compare_button.setEnabled(False)

        self.experimental_heading_model_path = (
            get_models_dir() / "figure_heading_pilot_v1.onnx"
        )
        self.experimental_heading_checkbox = QCheckBox("試用圖題自動轉正", self)
        experimental_heading_available = (
            is_verified_experimental_figure_heading_model(
                self.experimental_heading_model_path
            )
        )
        self.experimental_heading_checkbox.setChecked(
            experimental_heading_available
        )
        self.experimental_heading_checkbox.hide()  # Explicit first-step button is the opt-in.
        self.experimental_heading_checkbox.setToolTip(
            "使用已驗證但尚未達正式資料覆蓋門檻的圖題模型："
            "先判斷頁面方向、非破壞性轉正，再辨識圖號。"
        )

        self.prev_button = QPushButton("上一張")
        self.prev_button.setObjectName("GreenActionButton")
        self.prev_button.clicked.connect(self.show_prev_preview)
        self.prev_button.setEnabled(False)

        self.next_button = QPushButton("下一張")
        self.next_button.setObjectName("GreenActionButton")
        self.next_button.clicked.connect(self.show_next_preview)
        self.next_button.setEnabled(False)

        self.figure_mapping_line = QLineEdit()
        self.figure_mapping_line.setObjectName("InputLine")
        self.figure_mapping_line.setPlaceholderText("例如 1,2、3A、1' 或 B")
        self.figure_mapping_line.setMaximumWidth(112)
        self.figure_mapping_line.setMaximumHeight(34)
        self.figure_mapping_line.setEnabled(False)
        self.figure_mapping_line.setToolTip(
            "設定目前這一頁包含的圖號，例如 1、1,2、1-4、3A、1' 或 B。\n"
            "單圖頁採完全一致比對；多圖頁只引用部分圖式時會自動改採子集合比對。"
        )
        self.figure_mapping_line.editingFinished.connect(
            self.apply_current_figure_mapping
        )
        self.figure_mapping_line.textEdited.connect(
            self._mark_figure_mapping_as_manually_edited
        )

        self.rotate_button = QPushButton("右旋 90°")
        self.rotate_button.setObjectName("GreenActionButton")
        self.rotate_button.clicked.connect(self.rotate_current_image_clockwise)
        self.rotate_button.setEnabled(False)

        self.rotate_left_button = QPushButton("左旋90°")
        self.rotate_left_button.setObjectName("GreenActionButton")
        self.rotate_left_button.clicked.connect(
            self.rotate_current_image_counterclockwise
        )
        self.rotate_left_button.setEnabled(False)

        self.rotate_all_right_button = QPushButton("全部右旋90°")
        self.rotate_all_right_button.setObjectName("GreenActionButton")
        self.rotate_all_right_button.clicked.connect(
            self.rotate_all_images_clockwise
        )
        self.rotate_all_right_button.setEnabled(False)

        self.auto_rotate_button = QPushButton("第一步.自動旋轉")
        self.auto_rotate_button.setObjectName("RecognitionStartButton")
        self.auto_rotate_button.clicked.connect(self.auto_orient_all_images)
        self.auto_rotate_button.setEnabled(False)

        for btn in [
            self.run_button,
            self.compare_button,
            self.prev_button,
            self.next_button,
            self.rotate_button,
            self.rotate_left_button,
            self.rotate_all_right_button,
            self.auto_rotate_button,
        ]:
            btn.setMaximumHeight(36)

        self.preview_action_layout.addWidget(QLabel("本頁包含圖號："))
        self.preview_action_layout.addWidget(self.figure_mapping_line)
        self.preview_action_layout.addWidget(self.prev_button)
        self.preview_action_layout.addWidget(self.next_button)
        self.preview_action_layout.addWidget(self.rotate_button)
        self.preview_action_layout.addWidget(self.rotate_left_button)
        self.preview_action_layout.addWidget(self.rotate_all_right_button)
        self.preview_action_layout.addWidget(self.auto_rotate_button)
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
        self.preview_zoom_out_button = QPushButton("－")
        self.preview_zoom_out_button.setObjectName("SecondaryButton")
        self.preview_zoom_out_button.setToolTip("縮小圖片")
        self.preview_zoom_out_button.setEnabled(False)
        self.preview_zoom_out_button.clicked.connect(
            lambda: self.zoom_preview_at(-1, self._preview_viewport_center())
        )
        preview_title_layout.addWidget(self.preview_zoom_out_button)
        self.preview_zoom_reset_button = QPushButton("適合視窗")
        self.preview_zoom_reset_button.setObjectName("SecondaryButton")
        self.preview_zoom_reset_button.setToolTip("將圖片重設為適合視窗大小")
        self.preview_zoom_reset_button.setEnabled(False)
        self.preview_zoom_reset_button.clicked.connect(self.reset_preview_zoom)
        preview_title_layout.addWidget(self.preview_zoom_reset_button)
        self.preview_zoom_in_button = QPushButton("＋")
        self.preview_zoom_in_button.setObjectName("SecondaryButton")
        self.preview_zoom_in_button.setToolTip("放大圖片")
        self.preview_zoom_in_button.setEnabled(False)
        self.preview_zoom_in_button.clicked.connect(
            lambda: self.zoom_preview_at(1, self._preview_viewport_center())
        )
        preview_title_layout.addWidget(self.preview_zoom_in_button)
        preview_title_layout.addWidget(self.preview_zoom_status)

        self.image_preview = ClickableImageLabel("請先選擇圖片或 PDF")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.setObjectName("ImagePreview")
        self.image_preview.setMinimumSize(1, 1)
        self.image_preview.resize(600, 500)
        self.image_preview.setToolTip(
            "滑鼠滾輪：放大／縮小；放大後可使用捲軸移動檢視範圍。"
        )
        self.image_preview.detection_click_handler = (
            self.on_preview_detection_clicked
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

        self.reference_source_button = QPushButton("切換至代表圖")
        self.reference_source_button.setObjectName("LightBlueActionButton")
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
        # Keep the transferred document list as hidden state for the existing
        # reconciliation workflow. The manual input panel is intentionally no
        # longer part of the operator UI.
        self.reference_text = QTextEdit(right_panel)
        self.reference_text.setObjectName("ReferenceText")
        self.reference_text.setVisible(False)

        review_header = QHBoxLayout()
        review_title = QLabel("辨識暫存（雙擊標號可修改）")
        review_title.setObjectName("PanelTitle")
        review_header.addWidget(review_title)

        self.review_search_line = QLineEdit()
        self.review_search_line.setObjectName("ReviewSearchLine")
        self.review_search_line.setPlaceholderText("查找標號")
        self.review_search_line.setClearButtonEnabled(True)
        self.review_search_line.setMaximumWidth(112)
        self.review_search_line.setToolTip(
            "輸入完整標號後，以右側箭頭逐筆檢查；英文大小寫視為不同標號。"
        )
        self.review_search_line.textChanged.connect(
            self.on_review_search_text_changed
        )
        self.review_search_line.returnPressed.connect(
            lambda: self.navigate_review_search(1)
        )
        review_header.addWidget(self.review_search_line)

        self.review_search_previous_button = QPushButton()
        self.review_search_previous_button.setObjectName("SearchArrowButton")
        self.review_search_previous_button.setFixedSize(30, 30)
        self.review_search_previous_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp)
        )
        self.review_search_previous_button.setIconSize(QSize(16, 16))
        self.review_search_previous_button.setAccessibleName("上一個相同標號")
        self.review_search_previous_button.setToolTip("上一個相同標號")
        self.review_search_previous_button.setEnabled(False)
        self.review_search_previous_button.clicked.connect(
            lambda: self.navigate_review_search(-1)
        )
        review_header.addWidget(self.review_search_previous_button)

        self.review_search_next_button = QPushButton()
        self.review_search_next_button.setObjectName("SearchArrowButton")
        self.review_search_next_button.setFixedSize(30, 30)
        self.review_search_next_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)
        )
        self.review_search_next_button.setIconSize(QSize(16, 16))
        self.review_search_next_button.setAccessibleName("下一個相同標號")
        self.review_search_next_button.setToolTip("下一個相同標號")
        self.review_search_next_button.setEnabled(False)
        self.review_search_next_button.clicked.connect(
            lambda: self.navigate_review_search(1)
        )
        review_header.addWidget(self.review_search_next_button)
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

        self.review_table = RecognitionReviewTable(0, 4)
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
        self.review_table.setMinimumHeight(150)
        self.review_table.itemChanged.connect(self.on_review_item_changed)
        self.review_table.itemSelectionChanged.connect(
            self.on_review_selection_changed
        )
        self.review_table.cellClicked.connect(self.on_review_cell_clicked)
        self.review_table.delete_rows_requested.connect(
            self.delete_selected_labels
        )
        self.review_table.setToolTip(
            "選取整列後按 Delete 可刪除標號；雙擊標號進入編輯後，"
            "Delete 僅刪除反白選取的字元。"
        )

        review_actions = QHBoxLayout()
        self.add_label_button = QPushButton("新增目前圖片標號")
        self.add_label_button.setObjectName("OrangeActionButton")
        self.add_label_button.clicked.connect(self.add_manual_label)
        self.add_label_button.setEnabled(False)
        self.delete_label_button = QPushButton("刪除選取標號")
        self.delete_label_button.setObjectName("OrangeActionButton")
        self.delete_label_button.clicked.connect(self.delete_selected_labels)
        self.delete_label_button.setEnabled(False)
        review_actions.addWidget(self.add_label_button)
        review_actions.addWidget(self.delete_label_button)
        review_actions.addStretch()

        self.result_header_layout = QHBoxLayout()
        self.result_title = QLabel("辨識結果")
        self.result_title.setObjectName("PanelTitle")
        self.result_title.setMaximumHeight(30)

        self.result_view_button = QPushButton("僅顯示當前圖片")
        self.result_view_button.setObjectName("LightBlueActionButton")
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

        self.result_header_layout.addWidget(self.result_title)
        self.result_header_layout.addStretch()
        self.result_header_layout.addWidget(self.reference_source_button)
        self.result_header_layout.addWidget(self.result_view_button)

        self.result_text = QTextEdit()
        self.result_text.setObjectName("ResultText")
        self.result_text.setReadOnly(True)
        self.result_text.setMinimumHeight(96)
        self.result_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.result_text.setPlaceholderText(
            "辨識結果與標號清單比對結果會顯示在這裡。"
        )

        right_layout.addLayout(review_header)
        right_layout.addWidget(self.review_table, 3)
        right_layout.addLayout(review_actions)
        right_layout.addLayout(self.result_header_layout)
        right_layout.addWidget(self.result_text, 2)

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
        self.figure_mapping_sources = {
            index: "fallback"
            for index in range(len(image_paths))
        }
        self._pending_manual_figure_mappings.clear()
        self._sync_figure_mapping_control(self.current_preview_index)

    def _mark_figure_mapping_as_manually_edited(self, _text):
        if self.image_paths:
            self._pending_manual_figure_mappings.add(
                max(0, min(self.current_preview_index, len(self.image_paths) - 1))
            )
            self._queue_result_save()

    def _figure_numbers_for_index(self, image_index):
        configured = self.figure_number_mappings.get(image_index)
        if configured:
            return list(configured)
        if 0 <= image_index < len(self.all_results):
            result = self.all_results[image_index]
            result_figures = result.get("figure_numbers")
            if result_figures in (None, "", []):
                result_figures = result.get("figure_number")
            if result_figures not in (None, "", []):
                try:
                    source = (
                        ",".join(str(value) for value in result_figures)
                        if isinstance(result_figures, (list, tuple, set))
                        else result_figures
                    )
                    return parse_figure_number_mapping(source)
                except ValueError:
                    pass
        return [image_index + 1]

    def _set_result_figure_mapping(self, result, image_index):
        figures = self._figure_numbers_for_index(image_index)
        result["figure_numbers"] = figures
        result["figure_number_source"] = self.figure_mapping_sources.get(
            image_index,
            "fallback",
        )
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
            self._pending_manual_figure_mappings.discard(image_index)
            self.figure_mapping_line.setText(
                ",".join(str(number) for number in previous)
            )
            QMessageBox.warning(self, "圖號格式錯誤", str(error))
            return False

        self.figure_number_mappings[image_index] = figures
        if image_index in self._pending_manual_figure_mappings:
            self.figure_mapping_sources[image_index] = "manual"
            self._pending_manual_figure_mappings.discard(image_index)
        self.figure_mapping_line.setText(
            ",".join(str(number) for number in figures)
        )
        if image_index < len(self.all_results):
            self._set_result_figure_mapping(
                self.all_results[image_index],
                image_index,
            )
            self._publish_reviewed_ocr_results()
        else:
            self._queue_result_save()
        return True

    def _adopt_automatic_figure_mapping(self, result, image_index):
        """Use a complete high-confidence caption set unless the user edited it."""

        if self.figure_mapping_sources.get(image_index) == "manual":
            return False
        analysis = result.get("figure_heading") or {}
        mapping = analysis.get("mapping") or {}
        automatic = result.get("auto_figure_numbers") or []
        if mapping.get("status") != "accepted" or not automatic:
            return False
        try:
            figures = parse_figure_number_mapping(
                ",".join(str(value) for value in automatic)
            )
        except ValueError:
            return False
        self.figure_number_mappings[image_index] = figures
        self.figure_mapping_sources[image_index] = "auto"
        return True

    def select_images(self):
        if (
            self._pdf_tasks.busy
            or self._rotation_tasks.busy
            or self._recognition_is_running()
        ):
            return
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "選擇一張或多張圖片",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp)"
        )

        if file_paths:
            self.import_images(file_paths)

    def import_images(self, file_paths, *, use_saved=True, run_after=False):
        if (self._pdf_tasks.busy or self._rotation_tasks.busy
                or self._orientation_tasks.busy or self._recognition_is_running()):
            return False
        if not file_paths:
            return False
        self._commit_result_editor()
        self._queue_result_save()
        paths = list(map(str, file_paths))

        def work(cancel):
            identity, state, error = self._load_result_for_import(paths, "images", use_saved=use_saved)
            return {"identity": identity, "state": state, "cache_error": error,
                    "kind": "images", "input_paths": paths, "run_after": run_after}

        self._set_pdf_busy(True)
        self.result_text.setText("正在讀取圖片及本機圖式結果…")
        return self._pdf_tasks.start(work)

    def _accept_source_images(self, file_paths):
        self._heading_stage_enabled = False
        self._orientation_stage_pages = []
        self.source_image_paths = list(file_paths)
        self.image_paths = list(file_paths)
        self.reset_review_state()
        self._initialize_figure_number_mappings(file_paths)
        self.image_line.setText(file_paths[0] if len(file_paths) == 1 else f"已選擇 {len(file_paths)} 張圖片")
        self.current_preview_index = 0
        self.show_original_image(0)
        self.prev_button.setEnabled(len(file_paths) > 1)
        self.next_button.setEnabled(len(file_paths) > 1)
        self._set_rotation_buttons_enabled(True)

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

    def import_pdf(self, file_path, *, use_saved=True, run_after=False):
        """Import a PDF from either the dialog or drag-and-drop."""

        if (
            self._pdf_tasks.busy
            or self._rotation_tasks.busy
            or self._orientation_tasks.busy
            or self._recognition_is_running()
        ):
            return False

        pdf_path = Path(file_path)
        if pdf_path.suffix.lower() != ".pdf":
            QMessageBox.warning(
                self,
                "PDF 格式錯誤",
                "OCR 頁面的拖放匯入僅接受 .pdf 檔案。\n\n"
                f"選取檔案：{pdf_path.name}",
            )
            return False

        # Each import owns its output directory.  A cancelled/repeated import
        # must not overwrite the previous session's already-reviewed images.
        self._commit_result_editor()
        self._queue_result_save()
        def work(cancel):
            identity, state, error = self._load_result_for_import([str(pdf_path)], "pdf", use_saved=use_saved)
            payload = {"identity": identity, "state": state, "cache_error": error,
                       "kind": "pdf", "input_paths": [str(pdf_path)], "run_after": run_after}
            if state is not None or cancel.is_set():
                return payload
            output_root = get_output_base_dir() / "pdf_pages" / f"import_{uuid4().hex[:12]}"
            paths = convert_pdf_to_images(
                pdf_path=str(pdf_path),
                output_root=output_root,
                dpi=PDF_DPI,
                cancel_event=cancel,
            )
            payload["pdf_payload"] = (pdf_path, paths, output_root)
            return payload

        self.result_text.setText("正在讀取本機圖式結果；新檔案將轉換成圖片…（Esc 可取消）")
        self._set_pdf_busy(True)
        return self._pdf_tasks.start(work)

    def _set_pdf_busy(self, busy):
        for button in (self.image_button, self.pdf_button, self.run_button, self.auto_rotate_button):
            button.setEnabled(not busy)
        self.review_table.setEnabled(not busy)
        self.figure_mapping_line.setEnabled(not busy and bool(self.image_paths))
        self.confidence_threshold.setEnabled(not busy)
        for button in (self.add_label_button, self.delete_label_button, self.result_view_button):
            button.setEnabled(not busy and bool(self.all_results))
        self.compare_button.setEnabled(
            not busy and bool(self.all_results and self.reference_text.toPlainText().strip())
        )
        transfer = self.document_symbol_transfer
        self.reference_source_button.setEnabled(
            not busy and transfer is not None and all(
                transfer.is_source_ready(source)
                for source in (FULL_SYMBOL_SOURCE, REPRESENTATIVE_SYMBOL_SOURCE)
            )
        )
        self._set_rotation_buttons_enabled(not busy and bool(self.image_paths))

    def _on_pdf_error(self, message):
        QMessageBox.critical(self, "PDF 匯入錯誤", message)
        self.result_text.setText(f"PDF 匯入失敗（保留原圖片）：\n{message}")

    def _on_pdf_ready(self, payload):
        if self._closing:
            return False
        run_after = False
        if isinstance(payload, dict):
            if payload.get("state") is None and payload["kind"] == "pdf" and not payload["pdf_payload"][1]:
                QMessageBox.warning(self, "PDF 轉換失敗", "沒有從 PDF 轉出任何圖片。")
                return False
            if self._finish_result_import(payload):
                return True
            run_after = payload.get("run_after", False)
            if payload["kind"] == "images":
                self._accept_source_images(payload["input_paths"])
                if run_after == "orientation":
                    self.auto_orient_all_images()
                elif run_after:
                    self.run_batch_recognition()
                return True
            payload = payload["pdf_payload"]
        pdf_path, pdf_image_paths, output_root = payload
        try:

            if not pdf_image_paths:
                QMessageBox.warning(self, "PDF 轉換失敗", "沒有從 PDF 轉出任何圖片。")
                return False

            self.image_paths = pdf_image_paths
            self.source_image_paths = list(pdf_image_paths)
            self._heading_stage_enabled = False
            self._orientation_stage_pages = []
            self.reset_review_state()
            self._initialize_figure_number_mappings(pdf_image_paths)
            self.current_preview_index = 0

            self.image_line.setText(
                f"已匯入 PDF：{pdf_path.name}，共 {len(pdf_image_paths)} 頁"
            )

            self.result_text.setText(
                f"PDF 匯入完成：{pdf_path.name}\n"
                f"已轉換頁數：{len(pdf_image_paths)}\n"
                f"輸出位置：{output_root}\n\n"
                f"請確認左側圖片後，按「第一步.自動旋轉」。"
            )

            self.show_original_image(0)

            self.prev_button.setEnabled(len(pdf_image_paths) > 1)
            self.next_button.setEnabled(len(pdf_image_paths) > 1)
            self._set_rotation_buttons_enabled(True)

            if run_after == "orientation":
                self.auto_orient_all_images()
            elif run_after:
                self.run_batch_recognition()
            else:
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

    def auto_orient_all_images(self):
        """First step: orient every loaded page without running component OCR."""

        if (self._pdf_tasks.busy or self._rotation_tasks.busy
                or self._orientation_tasks.busy or self._recognition_is_running()):
            return False
        if self._result_source_changed and self._result_input_paths:
            if self._result_identity["kind"] == "pdf":
                return self.import_pdf(self._result_input_paths[0], use_saved=False, run_after="orientation")
            return self.import_images(self._result_input_paths, use_saved=False, run_after="orientation")
        source_paths = self._source_paths_for_rotation()
        if not source_paths:
            QMessageBox.warning(self, "沒有圖片", "請先選擇圖片或 PDF。")
            return False
        model_path = resolve_figure_heading_model_path(
            self.model_line.text(),
            str(self.experimental_heading_model_path)
            if self.experimental_heading_checkbox.isChecked() else None,
            allow_experimental=self.experimental_heading_checkbox.isChecked(),
        )
        if model_path is None:
            QMessageBox.warning(
                self, "缺少圖題方向模型",
                "目前沒有通過驗證的圖題方向模型，無法安全自動轉正。"
                "仍可手動旋轉圖片，再按第二步辨識。",
            )
            return False
        save_future = None
        if self.all_results:
            answer = QMessageBox.question(
                self, "重新轉正圖片",
                "現有辨識與人工修改會先保存在本機。若方向改變，"
                "目前辨識框需清除並於第二步重新辨識。是否繼續？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
            self._commit_result_editor()
            save_future = self._queue_result_save()
            if save_future is None:
                QMessageBox.warning(
                    self, "既有結果尚未保存",
                    "無法先保存目前的人工修改，為避免遺失紀錄，已取消自動旋轉。",
                )
                return False

        self.result_title.setText("圖片方向判斷中")
        self.result_text.setText(
            f"正在背景檢查 {len(source_paths)} 張圖片方向；"
            "只會轉正有足夠證據的圖片，原圖不會被覆蓋。"
        )
        self._set_orientation_busy(True)
        def work(cancel):
            if save_future is not None:
                save_error = save_future.result()
                if save_error:
                    raise RuntimeError(f"既有人工修改未能保存，已取消自動轉正：{save_error}")
            if cancel.is_set():
                return None
            return auto_orient_figure_images(
                source_paths, model_path, cancel_event=cancel,
                progress=self.orientation_progress.emit,
            )

        if not self._orientation_tasks.start(work):
            self._set_orientation_busy(False)
            return False
        return True

    def _set_orientation_busy(self, busy):
        available = not busy and not self._pdf_tasks.busy and not self._rotation_tasks.busy
        for button in (self.image_button, self.pdf_button, self.run_button, self.auto_rotate_button):
            button.setEnabled(available)
        self.compare_button.setEnabled(
            available and bool(self.all_results and self.reference_text.toPlainText().strip())
        )
        self.review_table.setEnabled(available)
        self._set_rotation_buttons_enabled(available and bool(self.image_paths))

    def _on_orientation_progress(self, message):
        if not self._closing and self._orientation_tasks.busy:
            self.result_text.setText(message + "\n只會轉正有足夠方向證據的圖片。")

    def _on_auto_orientation_ready(self, pages):
        if self._closing or pages is None:
            return
        changed = sum(bool(page["rotation_degrees"]) for page in pages)
        previous = {
            page["image_path"]: page for page in self._orientation_stage_pages
        }
        for page in pages:
            earlier = previous.get(page["original_image_path"])
            if earlier is not None:
                if not page["rotation_degrees"]:
                    page["figure_heading"] = earlier["figure_heading"]
                page["original_image_path"] = earlier["original_image_path"]
                page["rotation_degrees"] = (
                    int(earlier["rotation_degrees"]) + int(page["rotation_degrees"])
                ) % 360
        uncertain = sum(
            (page["figure_heading"].get("orientation") or {}).get("status")
            in {"ambiguous", "mixed_orientation", "no_evidence", "analysis_error"}
            or page["figure_heading"].get("status") == "analysis_error"
            for page in pages
        )
        if changed:
            current_index = self.current_preview_index
            paths = [page["image_path"] for page in pages]
            self.source_image_paths = paths
            self.image_paths = list(paths)
            self.reset_review_state()
            self._result_has_recognized = False
            self._result_save_revision += 1  # An older save must not describe the new images.
            self._save_success_reported = False
            self.result_save_status.setText("自動轉正完成；新圖片完成第二步後會保存")
            self.current_preview_index = min(current_index, len(paths) - 1)
            self.show_original_image(self.current_preview_index)
        self._orientation_stage_pages = pages
        self._heading_stage_enabled = True
        self.result_title.setText("第一步：圖片方向校正結果")
        self.result_text.setText(
            f"已檢查 {len(pages)} 張圖片；自動轉正 {changed} 張，"
            f"保留原方向 {len(pages) - changed} 張。\n"
            + (f"其中 {uncertain} 張方向證據不足或分析失敗，請人工檢查。\n" if uncertain else "")
            + "原圖未覆蓋。請核對方向後，按「第二步.圖片標號辨識」。"
        )

    def _on_auto_orientation_error(self, message):
        if self._closing:
            return
        self.result_title.setText("自動旋轉失敗")
        self.result_text.setText("自動轉正未完成；目前圖片與辨識結果均未替換。")
        QMessageBox.critical(self, "自動旋轉失敗", message)

    def run_batch_recognition(self):
        if (
            self._pdf_tasks.busy
            or self._rotation_tasks.busy
            or self._orientation_tasks.busy
            or self._recognition_is_running()
        ):
            return
        if self._result_source_changed and self._result_input_paths:
            if self._result_identity["kind"] == "pdf":
                self.import_pdf(self._result_input_paths[0], use_saved=False, run_after=True)
            else:
                self.import_images(self._result_input_paths, use_saved=False, run_after=True)
            return
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

        self._queue_result_save()
        self._result_rerun_snapshot = self._capture_result_state() if self.all_results else None

        self.model_path = self.model_line.text()
        self.reference_items = []
        self.deleted_review_entries = {}
        self.review_table.setRowCount(0)
        self.result_view_button.setChecked(False)

        self.result_text.setText(
            "第二步：圖片標號辨識中，請稍候...\n"
            "正在後台啟動辨識引擎"
        )
        self.result_title.setText("辨識進度")

        self.run_button.setEnabled(False)
        self.compare_button.setEnabled(False)
        self.result_view_button.setEnabled(False)
        self.add_label_button.setEnabled(False)
        self.delete_label_button.setEnabled(False)
        self.review_table.setEnabled(False)
        self.figure_mapping_line.setEnabled(False)
        self._set_rotation_buttons_enabled(False)
        self.auto_rotate_button.setEnabled(False)
        self.experimental_heading_checkbox.setEnabled(False)

        experimental_heading_enabled = (
            self._heading_stage_enabled
            and self.experimental_heading_checkbox.isChecked()
        )
        self.worker = BatchRecognitionWorker(
            image_paths=self.source_image_paths,
            model_path=self.model_path,
            figure_heading_model_path=(
                str(self.experimental_heading_model_path)
                if experimental_heading_enabled
                else None
            ),
            allow_experimental_figure_heading_model=(
                experimental_heading_enabled
            ),
            auto_orient=False,
        )

        self.worker.progress_signal.connect(self.on_progress)
        self.worker.finished_signal.connect(self.on_batch_finished)
        self.worker.error_signal.connect(self.on_error)
        self.worker.cancelled_signal.connect(self.on_recognition_cancelled)
        self._ocr_job_active = True
        self._ocr_cancel_requested = False
        self.worker.start()

    def on_progress(self, message):
        if self._closing or self._ocr_cancel_requested:
            return
        self.result_text.setText(message)

    def on_batch_finished(self, results):
        self._ocr_job_active = False
        if self._closing:
            return
        if self._ocr_cancel_requested:
            self.on_recognition_cancelled()
            return
        for image_index, result in enumerate(results):
            if len(self._orientation_stage_pages) == len(results):
                stage = self._orientation_stage_pages[image_index]
                if result.get("image_path") == stage.get("image_path"):
                    result["original_image_path"] = stage["original_image_path"]
                    result["rotation_degrees"] = stage["rotation_degrees"]
                    result["orientation"] = dict(
                        stage.get("figure_heading", {}).get("orientation") or {}
                    )
            result["source_image_name"] = Path(
                result.get("original_image_path", result.get("image_path", ""))
            ).name
            result["image_name"] = f"Pic_{image_index + 1:02d}"
            self._adopt_automatic_figure_mapping(result, image_index)
            self._set_result_figure_mapping(result, image_index)
            for detection in result.get("detections", []):
                detection.setdefault(
                    "original_label",
                    detection.get("label", detection.get("number", "")),
                )
                detection.setdefault("manual_edited", False)
                detection.setdefault("manual_added", False)

        self.all_results = results
        self._result_has_recognized = True
        self._result_rerun_snapshot = None
        self.current_pixmap = None
        self._preview_image_key = None
        self.image_paths = [result["image_path"] for result in results]
        self.current_preview_index = 0
        self._result_view_current_only = False
        self._sort_result_numbers = True
        self.result_view_button.blockSignals(True)
        self.result_view_button.setChecked(False)
        self.result_view_button.setText("僅顯示當前圖片")
        self.result_view_button.blockSignals(False)
        self.populate_review_table()

        automatic_corrections = self.apply_reference_reconciliation(
            parse_reference_items(self.reference_text.toPlainText())
        )

        self.reference_items = []
        self.refresh_result_display()
        self.result_title.setText("圖片辨識結果（確認後再進行第三步比對）")

        self.run_button.setEnabled(True)
        self.compare_button.setEnabled(
            bool(self.reference_text.toPlainText().strip())
        )
        self.result_view_button.setEnabled(True)
        self.add_label_button.setEnabled(True)
        self.delete_label_button.setEnabled(True)
        self.review_table.setEnabled(True)
        self.figure_mapping_line.setEnabled(True)
        self._set_rotation_buttons_enabled(bool(self.image_paths))
        self.auto_rotate_button.setEnabled(bool(self.image_paths))
        self.experimental_heading_checkbox.setEnabled(True)

        if self.all_results:
            self.show_original_image(0)
            self._publish_reviewed_ocr_results()

        completion_message = f"已完成 {len(self.all_results)} 張圖片辨識。\n\n"
        automatic_mapping_count = sum(
            source == "auto"
            for source in self.figure_mapping_sources.values()
        )
        automatic_rotation_count = sum(
            bool(int(result.get("rotation_degrees", 0)))
            for result in self.all_results
        )
        heading_model_missing = bool(self.all_results) and all(
            (result.get("figure_heading") or {}).get("status")
            == "model_unavailable"
            for result in self.all_results
        )
        if automatic_mapping_count:
            completion_message += (
                f"已由「圖＋圖號」自動設定 {automatic_mapping_count} 頁圖號。\n"
            )
        if automatic_rotation_count:
            completion_message += (
                f"已非破壞性自動轉正 {automatic_rotation_count} 頁；原圖仍保留。\n"
            )
        if heading_model_missing:
            completion_message += (
                "尚未找到圖題定位模型，本次沿用原本的頁碼／檔名圖號。\n"
            )
        if automatic_mapping_count or automatic_rotation_count or heading_model_missing:
            completion_message += "\n"
        completion_message += (
                f"已依符號清單保守修正 {automatic_corrections} 個易混淆字元。\n\n"
                if automatic_corrections
                else ""
        )
        completion_message += (
            f"低於 {self.confidence_threshold.value():.2f} 的標號已用紅框標示，"
            "請先確認或修改，再進行第三步比對。"
        )
        QMessageBox.information(
            self,
            "辨識完成",
            completion_message,
        )

    def on_error(self, error_message):
        self._ocr_job_active = False
        if self._closing:
            return
        if self._ocr_cancel_requested:
            self.on_recognition_cancelled()
            return
        if self._result_rerun_snapshot is not None:
            self._restore_result_state(self._result_rerun_snapshot)
            self._result_rerun_snapshot = None
        QMessageBox.critical(self, "辨識錯誤", error_message)
        self.result_text.setText(f"辨識失敗：\n{error_message}")

        self.run_button.setEnabled(True)
        self.review_table.setEnabled(True)
        self.figure_mapping_line.setEnabled(bool(self.image_paths))
        self._set_rotation_buttons_enabled(bool(self.image_paths))
        self.auto_rotate_button.setEnabled(bool(self.image_paths))
        self.experimental_heading_checkbox.setEnabled(True)

    def on_recognition_cancelled(self):
        """Restore the OCR page after the global Escape shortcut aborts work."""
        self._ocr_job_active = False
        if self._closing:
            return
        if self._result_rerun_snapshot is not None:
            self._restore_result_state(self._result_rerun_snapshot)
            self._result_rerun_snapshot = None

        self.result_title.setText("辨識已中止")
        self.result_text.setText(
            "目前辨識流程已由使用者中止，可重新按下第二步開始辨識。"
        )
        self.run_button.setEnabled(True)
        has_results = bool(self.all_results)
        self.compare_button.setEnabled(
            bool(has_results and self.reference_text.toPlainText().strip())
        )
        self.result_view_button.setEnabled(has_results)
        self.add_label_button.setEnabled(has_results)
        self.delete_label_button.setEnabled(has_results)
        self.review_table.setEnabled(True)
        self.figure_mapping_line.setEnabled(bool(self.source_image_paths))
        self._set_rotation_buttons_enabled(bool(self.source_image_paths))
        self.auto_rotate_button.setEnabled(bool(self.source_image_paths))
        self.experimental_heading_checkbox.setEnabled(True)

    def reset_review_state(self):
        self._guidance_comparison_snapshot = None
        self.current_pixmap = None
        self._preview_image_key = None
        self.all_results = []
        self.reference_items = []
        self.deleted_review_entries = {}
        self._review_sequence = 0
        self._sort_result_numbers = True
        self._updating_review_table = True
        self.review_table.setRowCount(0)
        self._updating_review_table = False
        self.review_search_line.clear()
        self._set_review_search_buttons_enabled(False)
        self.result_text.clear()
        self.result_title.setText("辨識結果")
        self.compare_button.setEnabled(False)
        self.result_view_button.setChecked(False)
        self.result_view_button.setEnabled(False)
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
        self._review_search_position = None
        self.on_review_search_text_changed(self.review_search_line.text())

    def apply_reference_reconciliation(self, reference_items):
        """Apply only audited one-glyph corrections to untouched OCR rows.

        The model output remains in ``original_label``. Switching between the
        complete and representative lists therefore re-evaluates from the raw
        OCR value instead of stacking corrections, while a user's manual edit
        always takes precedence.
        """

        references = {
            normalize_label_text(item.get("number", ""))
            for item in reference_items or []
            if normalize_label_text(item.get("number", ""))
        }
        active_corrections = 0
        self._updating_review_table = True
        self.review_table.blockSignals(True)
        for row in range(self.review_table.rowCount()):
            label_item = self.review_table.item(row, LABEL_COLUMN)
            if label_item is None:
                continue
            metadata = label_item.data(REVIEW_DATA_ROLE) or {}
            if metadata.get("manual_added"):
                continue

            current_label = normalize_label_text(label_item.text().strip())
            initial_label = normalize_label_text(
                metadata.get("review_initial_label", current_label)
            )
            if current_label != initial_label:
                # The user has edited this row; never overwrite that decision.
                continue

            raw_label = normalize_label_text(
                metadata.get("original_label", current_label)
            )
            corrected_label, changed = reconcile_label_to_reference(
                raw_label,
                references,
            )
            display_label = corrected_label if changed else raw_label
            metadata["review_initial_label"] = display_label
            metadata["auto_reference_corrected"] = bool(changed)
            detection = deepcopy(metadata.get("detection", {}))
            detection["label"] = display_label
            detection["number"] = display_label
            detection["original_label"] = raw_label
            if changed:
                active_corrections += 1
                detection["auto_reference_corrected"] = True
                detection["reference_reconciliation_from"] = raw_label
                detection["reference_reconciliation_to"] = display_label
            else:
                detection.pop("auto_reference_corrected", None)
                detection.pop("reference_reconciliation_from", None)
                detection.pop("reference_reconciliation_to", None)
            metadata["detection"] = detection
            label_item.setText(display_label)
            label_item.setData(REVIEW_DATA_ROLE, metadata)

        self.review_table.blockSignals(False)
        self._updating_review_table = False
        for row in range(self.review_table.rowCount()):
            self.apply_review_row_style(row)
        self._review_search_position = None
        self.on_review_search_text_changed(self.review_search_line.text())
        return active_corrections

    def _set_review_search_buttons_enabled(self, enabled):
        enabled = bool(enabled and self.review_table.rowCount())
        self.review_search_previous_button.setEnabled(enabled)
        self.review_search_next_button.setEnabled(enabled)

    def on_review_search_text_changed(self, text):
        query = normalize_label_text(text.strip())
        if query != self._review_search_query:
            self._review_search_position = None
        self._review_search_query = query
        self._set_review_search_buttons_enabled(
            bool(self._matching_review_rows(self._review_search_query))
        )

    def _matching_review_rows(self, query):
        if not query:
            return []
        matches = []
        for row in range(self.review_table.rowCount()):
            label_item = self.review_table.item(row, LABEL_COLUMN)
            if (
                label_item is not None
                and normalize_label_text(label_item.text().strip()) == query
            ):
                matches.append(row)
        return matches

    def navigate_review_search(self, direction):
        """Select the previous/next exact label match, wrapping at either end."""

        query = normalize_label_text(self.review_search_line.text().strip())
        matches = self._matching_review_rows(query)
        if not matches:
            self._set_review_search_buttons_enabled(False)
            return False
        if self._review_search_position is None:
            position = len(matches) - 1 if direction < 0 else 0
        else:
            step = -1 if direction < 0 else 1
            position = (self._review_search_position + step) % len(matches)
        row = matches[position]
        self._review_search_position = position
        return self._select_review_row(row, focus_preview=True)

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
            "review_initial_label": label,
            "auto_reference_corrected": bool(
                detection.get("auto_reference_corrected", False)
            ),
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
        initial_label = normalize_label_text(
            metadata.get("review_initial_label", metadata.get("original_label", ""))
        )
        auto_reference_corrected = bool(
            metadata.get("auto_reference_corrected")
        )
        low_confidence = (
            not manual_added
            and detection_confidence(detection) < self.confidence_threshold.value()
        )
        manually_edited = (
            not manual_added
            and bool(current_label)
            and current_label != initial_label
        )

        if not current_label:
            status = "請輸入標號"
            background = QColor("#fee2e2")
            foreground = QColor("#b91c1c")
        elif manual_added:
            status = "手動新增"
            background = QColor("#dbeafe")
            foreground = QColor("#1d4ed8")
        elif auto_reference_corrected and not manually_edited:
            status = (
                "清單自動修正（原低信心）"
                if low_confidence
                else "依清單自動修正"
            )
            background = QColor("#e0f2fe")
            foreground = QColor("#075985")
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
        font.setBold(
            low_confidence
            or manual_added
            or manually_edited
            or auto_reference_corrected
        )
        label_item.setFont(font)
        self.review_table.blockSignals(False)

    def collect_reviewed_results(self, *, only_image_index=None):
        # Preview redraws need one image, not a deep copy of every page and
        # every character confidence in the entire PDF.
        reviewed_results = [
            deepcopy(result) if only_image_index is None or index == only_image_index else {}
            for index, result in enumerate(self.all_results)
        ]
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
            if only_image_index is not None and image_index != only_image_index:
                continue
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
            initial_label = normalize_label_text(
                metadata.get("review_initial_label", original_label)
            )
            detection["label"] = label
            detection["number"] = label
            detection["original_label"] = original_label
            detection["manual_added"] = manual_added
            detection["review_id"] = metadata.get("review_id")
            detection["manual_edited"] = (
                not manual_added and label != initial_label
            )
            detections_by_image[image_index].append(detection)

        for image_index, result in enumerate(reviewed_results):
            if only_image_index is not None and image_index != only_image_index:
                continue
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
        self.result_title.setText("圖片辨識結果（修改後請重新進行第三步比對）")

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
        # Advice tracks an actually rendered comparison, not merely a loaded
        # reference list. Editor revisions invalidate it without parsing text
        # on the companion's one-second UI tick.
        self._guidance_comparison_snapshot = (
            (self.all_results, self.reference_text.document().revision())
            if self.reference_items and self.reference_items == parse_reference_items(
                self.reference_text.toPlainText()) else None
        )

    def on_result_view_toggled(self, checked):
        self._result_view_current_only = bool(checked)
        self.result_view_button.setText(
            "顯示完整結果" if checked else "僅顯示當前圖片"
        )
        self.refresh_result_display()

    def on_review_item_changed(self, item):
        if self._updating_review_table or item.column() != LABEL_COLUMN:
            return
        self.apply_review_row_style(item.row())
        self._review_search_position = None
        self.on_review_search_text_changed(self.review_search_line.text())
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
        self._review_search_position = None
        self.on_review_search_text_changed(self.review_search_line.text())
        self.refresh_detection_summary()
        self.show_original_image(self.current_preview_index)
        self._publish_reviewed_ocr_results()

    def on_confidence_threshold_changed(self, _value):
        for row in range(self.review_table.rowCount()):
            self.apply_review_row_style(row)
        self.sort_review_rows_by_picture_priority()
        if self.image_paths:
            self.show_original_image(self.current_preview_index)
        self._queue_result_save()

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
        matches = self._matching_review_rows(self._review_search_query)
        if selected_rows[0].row() in matches:
            self._review_search_position = matches.index(selected_rows[0].row())
        self._focus_preview_for_review_metadata(metadata or {})

    def on_review_cell_clicked(self, row, _column):
        """Re-focus even when the user clicks the already-selected row."""

        if self._updating_review_table or not self.all_results:
            return
        label_item = self.review_table.item(row, LABEL_COLUMN)
        metadata = label_item.data(REVIEW_DATA_ROLE) if label_item else {}
        self._focus_preview_for_review_metadata(metadata or {})

    def _focus_preview_for_review_metadata(self, metadata):
        image_index = int(metadata.get("image_index", -1))
        if not 0 <= image_index < len(self.image_paths):
            return False
        self.show_original_image(image_index)
        detection = metadata.get("detection", {})
        return self._center_preview_on_detection(detection, image_index)

    def _center_preview_on_detection(self, detection, image_index):
        """Keep the selected source box inside the center of the viewport."""

        if (
            image_index != self.current_preview_index
            or self.current_pixmap is None
            or self.current_pixmap.isNull()
        ):
            return False
        try:
            source_x = (float(detection["x1"]) + float(detection["x2"])) / 2.0
            source_y = (float(detection["y1"]) + float(detection["y2"])) / 2.0
        except (KeyError, TypeError, ValueError):
            return False

        preview_key = self._preview_image_key

        def center_selected_box():
            if (
                self._preview_image_key != preview_key
                or self.current_pixmap is None
                or self.current_pixmap.isNull()
            ):
                return
            preview_x = source_x * self.image_preview.width() / self.current_pixmap.width()
            preview_y = source_y * self.image_preview.height() / self.current_pixmap.height()
            viewport = self.scroll_area.viewport()
            self.scroll_area.horizontalScrollBar().setValue(
                round(preview_x - viewport.width() / 2)
            )
            self.scroll_area.verticalScrollBar().setValue(
                round(preview_y - viewport.height() / 2)
            )

        # Apply immediately and once more after Qt has updated scrollbar ranges
        # for a newly loaded or newly scaled image.
        center_selected_box()
        QTimer.singleShot(0, center_selected_box)
        return True

    def compare_with_reference_list(self):
        if not self.all_results:
            QMessageBox.warning(self, "尚未辨識", "請先完成第二步圖片標號辨識。")
            return

        reference_raw_text = self.reference_text.toPlainText().strip()
        if not reference_raw_text:
            QMessageBox.warning(
                self,
                "缺少標號清單",
                "請先輸入標號清單，再進行第三步比對。",
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

        self.apply_reference_reconciliation(reference_items)
        self.reference_items = reference_items
        self.refresh_result_display()
        self.result_title.setText("辨識結果與標號清單比對")
        self._publish_reviewed_ocr_results()

    def on_preview_detection_clicked(self, preview_position):
        """Select the boxed detection without changing the table order."""

        if (
            not self.all_results
            or self.current_pixmap is None
            or self.current_pixmap.isNull()
            or self.image_preview.width() <= 0
            or self.image_preview.height() <= 0
        ):
            return False
        source_x = (
            preview_position.x()
            * self.current_pixmap.width()
            / self.image_preview.width()
        )
        source_y = (
            preview_position.y()
            * self.current_pixmap.height()
            / self.image_preview.height()
        )
        candidates = []
        for row in range(self.review_table.rowCount()):
            label_item = self.review_table.item(row, LABEL_COLUMN)
            metadata = label_item.data(REVIEW_DATA_ROLE) if label_item else {}
            metadata = metadata or {}
            if int(metadata.get("image_index", -1)) != self.current_preview_index:
                continue
            detection = metadata.get("detection", {})
            if metadata.get("manual_added"):
                continue
            try:
                x1 = float(detection["x1"])
                y1 = float(detection["y1"])
                x2 = float(detection["x2"])
                y2 = float(detection["y2"])
            except (KeyError, TypeError, ValueError):
                continue
            if x1 <= source_x <= x2 and y1 <= source_y <= y2:
                candidates.append((max(1.0, (x2 - x1) * (y2 - y1)), row))
        if not candidates:
            return False
        _area, row = min(candidates)
        self._select_review_row(row)
        return True

    def _select_review_row(self, row, *, focus_preview=False):
        if not 0 <= row < self.review_table.rowCount():
            return False
        label_item = self.review_table.item(row, LABEL_COLUMN)
        if label_item is None:
            return False
        model_index = self.review_table.model().index(row, LABEL_COLUMN)
        selection_model = self.review_table.selectionModel()
        self._updating_review_table = True
        try:
            selection_model.setCurrentIndex(
                model_index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            self.review_table.scrollToItem(
                label_item,
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )
        finally:
            self._updating_review_table = False
        # A click on the preview should not move that preview. Search navigation
        # explicitly requests focus_preview so it can jump across images/boxes.
        self.review_table.setFocus(Qt.FocusReason.OtherFocusReason)
        self.review_table.viewport().update()
        self.update_scaled_preview()
        if focus_preview:
            metadata = label_item.data(REVIEW_DATA_ROLE) or {}
            self._focus_preview_for_review_metadata(metadata)
        return bool(
            self.review_table.currentRow() == row
            and any(index.row() == row for index in selection_model.selectedRows())
        )

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
        reviewed_results = self.collect_reviewed_results(only_image_index=image_index)
        if image_index >= len(reviewed_results):
            return pixmap

        if coordinate_scale_y is None:
            coordinate_scale_y = coordinate_scale_x

        threshold = self.confidence_threshold.value()
        show_all_boxes = self._show_all_boxes
        selected_ids = self.selected_review_ids(image_index)
        painter = QPainter(pixmap)
        # A one-pixel review border should stay crisp instead of being blended
        # into a wider, pale line by shape antialiasing.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # Keep the outline narrow so a small patent label is not hidden by its
        # own review box.  The old minimum of three pixels (six when selected)
        # covered too much of a digit after the page had been fitted to the
        # preview panel.
        pen_width = max(1, int(max(pixmap.width(), pixmap.height()) / 1200))
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
                current_pen_width = pen_width + 1
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

        pixmap = (
            self.current_pixmap
            if not image_changed and self.current_pixmap is not None
            else QPixmap(image_path)
        )

        if pixmap.isNull():
            self.current_pixmap = None
            self._preview_image_key = None
            self.image_preview.clear()
            self.image_preview.setText("圖片讀取失敗")
            self._set_preview_zoom_controls_enabled(False)
            self.preview_zoom_status.setText("滾輪縮放：圖片讀取失敗")
            return

        # Keep the full-resolution source untouched.  Every zoom level is
        # rendered again from this pixmap instead of enlarging a prior preview.
        self.current_pixmap = pixmap
        self._preview_image_key = preview_key
        if image_changed:
            self.preview_zoom_factor = 1.0
        self._set_preview_zoom_controls_enabled(True)
        self.update_scaled_preview()

        if self.all_results and index < len(self.all_results):
            result = self.all_results[index]
            display_name = result.get("image_name", f"Pic_{index + 1:02d}")
            source_name = result.get("source_image_name", Path(image_path).name)
            orientation = result.get("orientation") or {}
            orientation_status = orientation.get("status")
            correction = int(result.get("rotation_degrees", 0) or 0) % 360
            if correction == 90:
                direction_text = "　方向：已自動右旋 90°"
            elif correction == 180:
                direction_text = "　方向：已自動旋轉 180°"
            elif correction == 270:
                direction_text = "　方向：已自動左旋 90°"
            elif orientation_status == "upright":
                direction_text = "　方向：正向"
            elif orientation_status in {"ambiguous", "mixed_orientation"}:
                direction_text = "　方向：請人工確認"
            elif orientation_status == "model_unavailable":
                direction_text = "　方向：尚未啟用圖題模型"
            else:
                direction_text = ""
            self.preview_title.setText(
                f"{display_name}　{source_name}{direction_text}"
            )
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

        # Render at the monitor's physical pixel density and expose the result
        # at its logical size.  This is especially visible on the 125%/150%
        # Windows scaling commonly used by office notebooks: text strokes no
        # longer lose detail merely because the preview widget uses logical
        # pixels.
        device_ratio = max(1.0, float(self.devicePixelRatioF()))
        render_w = max(1, round(scaled_w * device_ratio))
        render_h = max(1, round(scaled_h * device_ratio))

        scaled = self.current_pixmap.scaled(
            render_w,
            render_h,
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
        scaled.setDevicePixelRatio(device_ratio)
        self.image_preview.setPixmap(scaled)
        self.image_preview.resize(
            max(1, round(scaled.width() / device_ratio)),
            max(1, round(scaled.height() / device_ratio)),
        )
        if abs(self.preview_zoom_factor - 1.0) < 0.01:
            zoom_text = "適合視窗"
        else:
            zoom_text = f"{round(self.preview_zoom_factor * 100)}%"
        self.preview_zoom_status.setText(f"滾輪縮放：{zoom_text}")

    def _set_preview_zoom_controls_enabled(self, enabled):
        for button in (
            self.preview_zoom_out_button,
            self.preview_zoom_reset_button,
            self.preview_zoom_in_button,
        ):
            button.setEnabled(bool(enabled))

    def _preview_viewport_center(self):
        return self.scroll_area.viewport().rect().center()

    def reset_preview_zoom(self):
        if self.current_pixmap is None or self.current_pixmap.isNull():
            return False
        self.preview_zoom_factor = 1.0
        self.update_scaled_preview()
        self.scroll_area.horizontalScrollBar().setValue(0)
        self.scroll_area.verticalScrollBar().setValue(0)
        return True

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
        self._preview_resize_timer.start()

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

    def _set_rotation_buttons_enabled(self, enabled):
        for button in (
            self.rotate_button,
            self.rotate_left_button,
            self.rotate_all_right_button,
            self.auto_rotate_button,
        ):
            button.setEnabled(bool(enabled))

    def _source_paths_for_rotation(self):
        """Prefer clean input images over rendered recognition previews."""

        if self.source_image_paths:
            return list(self.source_image_paths)
        return list(self.image_paths)

    def _rotate_current_image(self, *, clockwise):
        if (
            self._pdf_tasks.busy
            or self._rotation_tasks.busy
            or self._orientation_tasks.busy
            or self._recognition_is_running()
        ):
            return
        if not self.image_paths:
            QMessageBox.warning(self, "沒有圖片", "請先選擇圖片或 PDF。")
            return

        try:
            source_paths = self._source_paths_for_rotation()
            image_index = max(
                0,
                min(self.current_preview_index, len(source_paths) - 1),
            )
            current_path = source_paths[image_index]
            rotate = (
                rotate_image_clockwise_90
                if clockwise
                else rotate_image_counterclockwise_90
            )
            rotated_path = rotate(current_path)
            source_paths[image_index] = rotated_path
            self.source_image_paths = source_paths
            self.image_paths = list(source_paths)
            self._heading_stage_enabled = False
            self._orientation_stage_pages = []
            self.reset_review_state()

            direction = "右" if clockwise else "左"
            self.result_text.setText(
                f"已將目前圖片向{direction}旋轉 90 度。\n\n"
                f"原圖片：{Path(current_path).name}\n"
                f"旋轉後圖片：{Path(rotated_path).name}\n"
                f"輸出位置：{Path(rotated_path).parent}\n\n"
                f"請重新按「第二步.圖片標號辨識」。"
            )
            self.show_original_image(image_index)

            self._queue_result_save()

        except Exception as error:
            QMessageBox.critical(
                self,
                "圖片旋轉失敗",
                f"無法旋轉目前圖片：\n{error}",
            )

    def rotate_current_image_clockwise(self):
        self._rotate_current_image(clockwise=True)

    def rotate_current_image_counterclockwise(self):
        self._rotate_current_image(clockwise=False)

    def rotate_all_images_clockwise(self):
        source_paths = self._source_paths_for_rotation()
        if (
            self._pdf_tasks.busy
            or self._rotation_tasks.busy
            or self._orientation_tasks.busy
            or self._recognition_is_running()
        ):
            return False
        if not source_paths:
            QMessageBox.warning(self, "沒有圖片", "請先選擇圖片或 PDF。")
            return False

        def work(cancel):
            rotated_paths = []
            for source_path in source_paths:
                if cancel.is_set():
                    return None
                rotated_paths.append(rotate_image_clockwise_90(source_path))
            if cancel.is_set():
                return None
            return rotated_paths

        self.result_text.setText(
            f"正在背景將全部 {len(source_paths)} 張圖片向右旋轉 90 度…\n"
            "可繼續檢視目前圖片；按 Esc 可取消。"
        )
        self._set_rotation_busy(True)
        if not self._rotation_tasks.start(work):
            self._set_rotation_busy(False)
            return False
        return True

    def _set_rotation_busy(self, busy):
        available = (
            not busy
            and not self._pdf_tasks.busy
            and not self._recognition_is_running()
        )
        for button in (self.image_button, self.pdf_button, self.run_button, self.auto_rotate_button):
            button.setEnabled(available)
        self.prev_button.setEnabled(available and len(self.image_paths) > 1)
        self.next_button.setEnabled(available and len(self.image_paths) > 1)
        self.figure_mapping_line.setEnabled(available and bool(self.image_paths))
        self._set_rotation_buttons_enabled(available and bool(self.image_paths))

    def _on_rotate_all_ready(self, rotated_paths):
        if self._closing or not rotated_paths:
            return
        self.source_image_paths = list(rotated_paths)
        self.image_paths = list(rotated_paths)
        self._heading_stage_enabled = False
        self._orientation_stage_pages = []
        self.reset_review_state()
        self.current_preview_index = min(
            self.current_preview_index,
            len(rotated_paths) - 1,
        )
        self.result_text.setText(
            f"已將全部 {len(rotated_paths)} 張圖片向右旋轉 90 度。\n\n"
            "原有辨識結果已清除，請重新按「第二步.圖片標號辨識」。"
        )
        self.show_original_image(self.current_preview_index)

        self._queue_result_save()

    def _on_rotate_all_error(self, message):
        if self._closing:
            return
        self.result_text.setText(
            "全部圖片右旋失敗；尚未替換目前載入的圖片。"
        )
        QMessageBox.critical(
            self,
            "全部圖片旋轉失敗",
            f"無法完成全部圖片右旋 90 度：\n{message}",
        )
