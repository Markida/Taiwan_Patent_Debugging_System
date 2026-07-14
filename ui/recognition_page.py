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
    QApplication
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

from app.config import PDF_DPI, MAX_IMAGE_PREVIEW_ZOOM
from app.paths import get_models_dir, get_output_base_dir

from features.patent_ocr.ocr_engine import get_device_status_text
from features.patent_ocr.ocr_worker import BatchRecognitionWorker
from features.patent_ocr.pdf_tools import convert_pdf_to_images
from features.patent_ocr.image_tools import rotate_image_clockwise_90
from features.patent_ocr.label_parser import parse_reference_items
from features.patent_ocr.label_matcher import build_reference_comparison_text


class RecognitionPage(QWidget):
    def __init__(self, go_home_callback):
        super().__init__()

        self.go_home_callback = go_home_callback

        self.model_path = ""
        self.image_paths = []
        self.all_results = []
        self.reference_items = []
        self.current_preview_index = 0
        self.current_pixmap = None

        self.build_ui()

    def build_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(14, 8, 14, 10)
        main_layout.setSpacing(6)

        header_layout = QHBoxLayout()

        back_button = QPushButton("← 回首頁")
        back_button.setObjectName("SecondaryButton")
        back_button.setMaximumHeight(34)
        back_button.clicked.connect(self.go_home_callback)

        title = QLabel("圖片標號識別")
        title.setObjectName("PageTitle")
        title.setMaximumHeight(34)
        title.setContentsMargins(0, 0, 0, 0)

        header_layout.addWidget(back_button)
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
        self.image_line.setPlaceholderText("請選擇圖片或 PDF")
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

        action_layout = QHBoxLayout()

        self.run_button = QPushButton("開始批次辨識 / 比對")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self.run_batch_recognition)

        self.prev_button = QPushButton("上一張原圖")
        self.prev_button.setObjectName("SecondaryButton")
        self.prev_button.clicked.connect(self.show_prev_preview)
        self.prev_button.setEnabled(False)

        self.next_button = QPushButton("下一張原圖")
        self.next_button.setObjectName("SecondaryButton")
        self.next_button.clicked.connect(self.show_next_preview)
        self.next_button.setEnabled(False)

        self.rotate_button = QPushButton("右旋 90°")
        self.rotate_button.setObjectName("SecondaryButton")
        self.rotate_button.clicked.connect(self.rotate_current_image_clockwise)
        self.rotate_button.setEnabled(False)

        self.save_button = QPushButton("確認結果並儲存 TXT")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.clicked.connect(self.save_txt)
        self.save_button.setEnabled(False)

        for btn in [
            self.run_button,
            self.prev_button,
            self.next_button,
            self.rotate_button,
            self.save_button
        ]:
            btn.setMaximumHeight(36)

        action_layout.addWidget(self.run_button)
        action_layout.addWidget(self.prev_button)
        action_layout.addWidget(self.next_button)
        action_layout.addWidget(self.rotate_button)
        action_layout.addStretch()
        action_layout.addWidget(self.save_button)

        main_layout.addLayout(action_layout)

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

        self.image_preview = QLabel("請先選擇圖片或 PDF")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.setObjectName("ImagePreview")
        self.image_preview.setMinimumSize(760, 620)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.image_preview)
        self.scroll_area.setObjectName("ImageScrollArea")

        left_layout.addWidget(self.preview_title)
        left_layout.addWidget(self.scroll_area)

        left_panel.setLayout(left_layout)

        right_panel = QFrame()
        right_panel.setObjectName("Panel")

        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(6)

        reference_title = QLabel("標號清單輸入（可選）")
        reference_title.setObjectName("PanelTitle")
        reference_title.setMaximumHeight(30)

        self.reference_text = QTextEdit()
        self.reference_text.setObjectName("ReferenceText")
        self.reference_text.setFixedHeight(145)
        self.reference_text.setPlaceholderText(
            "可貼上目前專利圖的標號清單，例如：\n"
            "1:蓋子\n"
            "A:凹槽\n"
            "7':第一凸部\n"
            "10A:連接件\n\n"
            "程式會自動擷取前面的標號，並與辨識結果比對。"
        )

        result_title = QLabel("辨識結果 / 比對結果")
        result_title.setObjectName("PanelTitle")
        result_title.setMaximumHeight(30)

        self.result_text = QTextEdit()
        self.result_text.setObjectName("ResultText")
        self.result_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.result_text.setPlaceholderText(
            "辨識結果與標號清單比對結果會顯示在這裡。"
        )

        right_layout.addWidget(reference_title)
        right_layout.addWidget(self.reference_text)
        right_layout.addWidget(result_title)
        right_layout.addWidget(self.result_text)

        right_panel.setLayout(right_layout)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([860, 410])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

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

    def select_images(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "選擇一張或多張圖片",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp)"
        )

        if file_paths:
            self.image_paths = file_paths
            self.all_results = []
            self.result_text.clear()

            if len(file_paths) == 1:
                self.image_line.setText(file_paths[0])
            else:
                self.image_line.setText(f"已選擇 {len(file_paths)} 張圖片")

            self.current_preview_index = 0
            self.show_original_image(0)

            self.prev_button.setEnabled(len(file_paths) > 1)
            self.next_button.setEnabled(len(file_paths) > 1)
            self.rotate_button.setEnabled(True)
            self.save_button.setEnabled(False)

    def select_pdf(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "選擇 PDF 檔案",
            "",
            "PDF Files (*.pdf)"
        )

        if not file_path:
            return

        try:
            self.result_text.setText("正在將 PDF 轉換成圖片，請稍候...")
            QApplication.processEvents()

            pdf_image_paths = convert_pdf_to_images(
                pdf_path=file_path,
                output_root=get_output_base_dir() / "pdf_pages",
                dpi=PDF_DPI
            )

            if not pdf_image_paths:
                QMessageBox.warning(self, "PDF 轉換失敗", "沒有從 PDF 轉出任何圖片。")
                return

            self.image_paths = pdf_image_paths
            self.all_results = []
            self.current_preview_index = 0

            self.image_line.setText(
                f"已匯入 PDF：{Path(file_path).name}，共 {len(pdf_image_paths)} 頁"
            )

            self.result_text.setText(
                f"PDF 匯入完成：{Path(file_path).name}\n"
                f"已轉換頁數：{len(pdf_image_paths)}\n"
                f"輸出位置：{get_output_base_dir() / 'pdf_pages'}\n\n"
                f"請確認左側原圖後，按「開始批次辨識 / 比對」。"
            )

            self.show_original_image(0)

            self.prev_button.setEnabled(len(pdf_image_paths) > 1)
            self.next_button.setEnabled(len(pdf_image_paths) > 1)
            self.rotate_button.setEnabled(True)
            self.save_button.setEnabled(False)

            QMessageBox.information(
                self,
                "PDF 匯入完成",
                f"已將 PDF 轉換成 {len(pdf_image_paths)} 張圖片，可以開始辨識。"
            )

        except Exception as e:
            QMessageBox.critical(self, "PDF 匯入錯誤", f"{e}")
            self.result_text.setText(f"PDF 匯入失敗：\n{e}")

    def run_batch_recognition(self):
        if not self.model_line.text():
            QMessageBox.warning(self, "缺少模型", "請先選擇 YOLO 模型 .pt 檔。")
            return

        if not self.image_paths:
            QMessageBox.warning(self, "缺少檔案", "請先選擇圖片或 PDF。")
            return

        reference_raw_text = self.reference_text.toPlainText().strip()
        self.reference_items = parse_reference_items(reference_raw_text)

        if reference_raw_text and not self.reference_items:
            QMessageBox.warning(
                self,
                "標號清單格式錯誤",
                "有輸入標號清單，但程式沒有成功擷取到任何標號。\n\n"
                "請使用類似以下格式：\n"
                "1:蓋子\n"
                "A:凹槽\n"
                "7':第一凸部\n"
                "10A:連接件"
            )
            return

        if self.reference_items:
            reference_status = (
                "待比對標號：" +
                ", ".join(item["number"] for item in self.reference_items)
            )
        else:
            reference_status = "未輸入標號清單，僅執行一般辨識。"

        self.model_path = self.model_line.text()

        self.result_text.setText(
            f"批次辨識中，請稍候...\n"
            f"目前運算模式：{get_device_status_text()}\n"
            f"{reference_status}"
        )

        self.run_button.setEnabled(False)
        self.save_button.setEnabled(False)

        self.worker = BatchRecognitionWorker(
            image_paths=self.image_paths,
            model_path=self.model_path
        )

        self.worker.progress_signal.connect(self.on_progress)
        self.worker.finished_signal.connect(self.on_batch_finished)
        self.worker.error_signal.connect(self.on_error)
        self.worker.start()

    def on_progress(self, message):
        self.result_text.setText(message)

    def on_batch_finished(self, results):
        self.all_results = results

        blocks = []

        for result in self.all_results:
            blocks.append(result["result_text"])

        separator = "\n\n" + ("-" * 42) + "\n\n"
        recognition_text = separator.join(blocks)

        comparison_text = build_reference_comparison_text(
            all_results=self.all_results,
            reference_items=self.reference_items
        )

        if comparison_text:
            final_text = (
                recognition_text +
                "\n\n" +
                ("=" * 42) +
                "\n\n" +
                comparison_text
            )
        else:
            final_text = recognition_text

        self.result_text.setText(final_text)

        self.run_button.setEnabled(True)
        self.save_button.setEnabled(True)

        if self.all_results:
            self.current_preview_index = 0
            self.show_original_image(0)

        QMessageBox.information(
            self,
            "辨識完成",
            f"已完成 {len(self.all_results)} 張圖片辨識。"
        )

    def on_error(self, error_message):
        QMessageBox.critical(self, "辨識錯誤", error_message)
        self.result_text.setText(f"辨識失敗：\n{error_message}")

        self.run_button.setEnabled(True)
        self.save_button.setEnabled(False)

    def show_original_image(self, index):
        if not self.image_paths:
            return

        index = max(0, min(index, len(self.image_paths) - 1))
        self.current_preview_index = index

        image_path = self.image_paths[index]

        pixmap = QPixmap(image_path)

        if pixmap.isNull():
            self.image_preview.setText("圖片讀取失敗")
            return

        self.current_pixmap = pixmap
        self.update_scaled_preview()

        self.preview_title.setText(
            f"原圖預覽：{index + 1}/{len(self.image_paths)}　{Path(image_path).name}"
        )

    def update_scaled_preview(self):
        if self.current_pixmap is None:
            return

        viewport_size = self.scroll_area.viewport().size()

        available_w = max(100, viewport_size.width() - 8)
        available_h = max(100, viewport_size.height() - 8)

        pix_w = self.current_pixmap.width()
        pix_h = self.current_pixmap.height()

        if pix_w <= 0 or pix_h <= 0:
            return

        scale = min(
            available_w / pix_w,
            available_h / pix_h
        )

        scale = min(scale, MAX_IMAGE_PREVIEW_ZOOM)

        scaled_w = max(1, int(pix_w * scale))
        scaled_h = max(1, int(pix_h * scale))

        scaled = self.current_pixmap.scaled(
            scaled_w,
            scaled_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        self.image_preview.setPixmap(scaled)

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

            self.all_results = []
            self.save_button.setEnabled(False)

            self.result_text.setText(
                f"已將目前圖片向右旋轉 90 度。\n\n"
                f"原圖片：{Path(current_path).name}\n"
                f"旋轉後圖片：{Path(rotated_path).name}\n"
                f"輸出位置：{Path(rotated_path).parent}\n\n"
                f"請重新按「開始批次辨識 / 比對」。"
            )

            self.show_original_image(self.current_preview_index)

        except Exception as e:
            QMessageBox.critical(
                self,
                "圖片旋轉失敗",
                f"無法旋轉目前圖片：\n{e}"
            )

    def save_txt(self):
        text = self.result_text.toPlainText().strip()

        if not text:
            QMessageBox.warning(self, "沒有結果", "目前沒有可儲存的辨識結果。")
            return

        output_dir = get_output_base_dir() / "result_txt"
        output_dir.mkdir(parents=True, exist_ok=True)

        default_path = output_dir / "batch_ocr_results.txt"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "儲存批次 TXT 結果",
            str(default_path),
            "Text Files (*.txt)"
        )

        if file_path:
            Path(file_path).write_text(text, encoding="utf-8")

            QMessageBox.information(
                self,
                "儲存完成",
                f"TXT 已儲存：\n{file_path}"
            )
