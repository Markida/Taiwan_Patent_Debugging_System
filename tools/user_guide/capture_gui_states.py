"""Capture deterministic screenshots of the current Saint-Island GUI.

The screenshots are unannotated. The PDF builder overlays vector callouts so
the interface remains pixel-accurate and readable.
"""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

YOLO_CONFIG_DIR = PROJECT_ROOT / ".ultralytics_ai"
YOLO_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(YOLO_CONFIG_DIR))

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from ultralytics import YOLO

from app.main_window import MainWindow
from features.patent_ocr.class_map import load_class_map
from features.patent_ocr.ocr_engine import (
    recognize_one_image,
    should_use_yolo_class_as_char,
)
from features.patent_ocr.pdf_tools import convert_pdf_to_images
from ui.recognition_page import LABEL_COLUMN


OUTPUT_DIR = PROJECT_ROOT / "tmp" / "user_guide" / "screenshots"
MODEL_PATH = PROJECT_ROOT / "models" / "patent_char_v2_domain.pt"
COMPANY_MODEL_DISPLAY = (
    r"C:\Saint-Island_Patent_MDS\models\patent_char_v2_domain.pt"
)
PDF_PATH = PROJECT_ROOT / "sample" / "test_pdf.pdf"
SAMPLE_IMAGES = [
    PROJECT_ROOT
    / "training"
    / "char_yolo"
    / "dataset_v2_domain_matched"
    / "images"
    / "val"
    / "manual_val_0003.png",
    PROJECT_ROOT
    / "training"
    / "char_yolo"
    / "dataset_v2_domain_matched"
    / "images"
    / "val"
    / "manual_val_0011.png",
]


def process_events():
    for _ in range(4):
        QApplication.processEvents()


def capture(window, name):
    process_events()
    target = OUTPUT_DIR / f"{name}.png"
    pixmap = window.grab()
    if not pixmap.save(str(target), "PNG"):
        raise RuntimeError(f"Screenshot save failed: {target}")
    return target


def widget_rect(widget, window):
    position = widget.mapTo(window, QPoint(0, 0))
    return [position.x(), position.y(), widget.width(), widget.height()]


def collect_geometry(window, page):
    buttons = {}
    for button in page.findChildren(QPushButton):
        text = button.text().strip()
        if text:
            buttons[text] = {
                "rect": widget_rect(button, window),
                "enabled": button.isEnabled(),
                "checked": button.isChecked() if button.isCheckable() else None,
            }
    home_buttons = {}
    for button in window.home_page.findChildren(QPushButton):
        text = button.text().strip()
        if text:
            home_buttons[text] = {"rect": widget_rect(button, window)}
    fields = {
        "model_line": widget_rect(page.model_line, window),
        "image_line": widget_rect(page.image_line, window),
        "image_preview": widget_rect(page.scroll_area, window),
        "review_table": widget_rect(page.review_table, window),
        "confidence_threshold": widget_rect(page.confidence_threshold, window),
        "reference_text": widget_rect(page.reference_text, window),
        "result_text": widget_rect(page.result_text, window),
        "result_title": widget_rect(page.result_title, window),
    }
    return {
        "window": [0, 0, window.width(), window.height()],
        "buttons": buttons,
        "home_buttons": home_buttons,
        "fields": fields,
    }


def run_recognition():
    model = YOLO(str(MODEL_PATH))
    class_map = load_class_map(MODEL_PATH)
    direct = should_use_yolo_class_as_char(model, class_map)
    results = []
    for image_path in SAMPLE_IMAGES:
        result = recognize_one_image(
            image_path=image_path,
            model=model,
            reader=None,
            model_name=MODEL_PATH.name,
            class_map=class_map,
            use_yolo_class_as_char=direct,
            imgsz=1536,
            yolo_conf=0.20,
            ocr_conf=0.20,
        )
        result["original_image_path"] = str(image_path)
        result["image_path"] = str(image_path)
        results.append(result)

    # Deliberately turn the one real low-confidence sample into a plausible
    # typo so the guide can demonstrate a correction without inventing a box.
    for detection in results[0].get("detections", []):
        if detection.get("label") == "55'":
            detection["label"] = "5S'"
            detection["number"] = "5S'"
            detection["original_label"] = "5S'"
            break
    return results


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    for font_path in (
        Path("C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/msjhbd.ttc"),
        Path("C:/Windows/Fonts/NotoSansTC-VF.ttf"),
    ):
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Microsoft JhengHei", 10))

    # Avoid modal dialogs while capturing the same post-action UI states.
    QMessageBox.information = staticmethod(lambda *args, **kwargs: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *args, **kwargs: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *args, **kwargs: QMessageBox.Ok)

    window = MainWindow()
    window.resize(1600, 950)
    window.show()
    process_events()
    capture(window, "01_home")

    window.open_feature("patent_ocr")
    page = window.feature_pages["patent_ocr"]
    page.model_path = str(MODEL_PATH)
    page.model_line.setText(COMPANY_MODEL_DISPLAY)
    process_events()
    capture(window, "02_blank_workspace")

    pdf_pages = convert_pdf_to_images(
        PDF_PATH,
        PROJECT_ROOT / "tmp" / "user_guide" / "pdf_pages",
        dpi=300,
    )
    page.source_image_paths = list(pdf_pages)
    page.image_paths = list(pdf_pages)
    page.current_preview_index = 0
    page.image_line.setText(f"已匯入 PDF：{PDF_PATH.name}，共 {len(pdf_pages)} 頁")
    page.result_text.setPlainText(
        f"PDF 匯入完成：{PDF_PATH.name}\n"
        f"已轉換頁數：{len(pdf_pages)}\n\n"
        "請確認左側圖片方向後，按「第一步.圖片數字英文辨識」。"
    )
    page.prev_button.setEnabled(len(pdf_pages) > 1)
    page.next_button.setEnabled(len(pdf_pages) > 1)
    page.rotate_button.setEnabled(True)
    page.show_original_image(0)
    process_events()
    capture(window, "03_pdf_imported")

    recognition_results = run_recognition()
    page.source_image_paths = [str(path) for path in SAMPLE_IMAGES]
    page.image_line.setText("已選擇 2 張圖片")
    page.on_batch_finished(deepcopy(recognition_results))
    process_events()
    capture(window, "04_recognition_complete")

    low_confidence_row = None
    for row in range(page.review_table.rowCount()):
        item = page.review_table.item(row, LABEL_COLUMN)
        if item and item.text() == "5S'":
            low_confidence_row = row
            break
    if low_confidence_row is None:
        raise RuntimeError("Low-confidence demonstration row not found")
    page.review_table.selectRow(low_confidence_row)
    page.on_review_selection_changed()
    process_events()
    capture(window, "05_low_confidence_selected")

    label_item = page.review_table.item(low_confidence_row, LABEL_COLUMN)
    label_item.setText("55'")
    process_events()
    capture(window, "06_error_corrected")

    page.reference_text.setPlainText(
        "5:上框\n"
        "6:下框\n"
        "31:主軸\n"
        "32:支撐件\n"
        "51:內板\n"
        "52:側板\n"
        "55:固定部\n"
        "55':扣合部\n"
        "65:卡扣\n"
        "A:方向A\n"
        "B:方向B\n"
        "C:方向C\n"
        "D:方向D\n"
        "99:示範缺漏"
    )
    page.compare_with_reference_list()
    process_events()
    capture(window, "07_comparison_all")

    page.result_view_button.setChecked(True)
    process_events()
    capture(window, "08_comparison_current")

    geometry = collect_geometry(window, page)
    (OUTPUT_DIR / "geometry.json").write_text(
        json.dumps(geometry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUTPUT_DIR / "recognition_results.json").write_text(
        json.dumps(recognition_results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    window.close()
    print(json.dumps({"screenshots": 8, "output": str(OUTPUT_DIR)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
