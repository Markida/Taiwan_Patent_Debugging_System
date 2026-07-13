from pathlib import Path

from PySide6.QtCore import QThread, Signal
from ultralytics import YOLO

from features.patent_ocr.class_map import load_class_map
from features.patent_ocr.easyocr_loader import create_easyocr_reader
from features.patent_ocr.ocr_engine import (
    get_device_status_text,
    get_easyocr_gpu_flag,
    should_use_yolo_class_as_char,
    recognize_one_image
)


class BatchRecognitionWorker(QThread):
    progress_signal = Signal(str)
    finished_signal = Signal(object)
    error_signal = Signal(str)

    def __init__(self, image_paths, model_path):
        super().__init__()
        self.image_paths = image_paths
        self.model_path = model_path

    def run(self):
        try:
            model_path_obj = Path(self.model_path)
            model_name = model_path_obj.name

            device_status = get_device_status_text()

            self.progress_signal.emit(
                f"正在載入 YOLO 模型...\n目前運算模式：{device_status}"
            )

            model = YOLO(str(model_path_obj))

            class_map = load_class_map(model_path_obj)
            use_yolo_class_as_char = should_use_yolo_class_as_char(model, class_map)

            reader = None

            if use_yolo_class_as_char:
                self.progress_signal.emit(
                    f"已啟用新版 YOLO 字元模型模式。\n"
                    f"目前運算模式：{device_status}"
                )
            else:
                ocr_gpu = get_easyocr_gpu_flag()

                if ocr_gpu:
                    self.progress_signal.emit(
                        "正在載入 EasyOCR...\n目前運算模式：GPU 模式"
                    )
                else:
                    self.progress_signal.emit(
                        "正在載入 EasyOCR...\n目前運算模式：CPU 模式"
                    )

                reader = create_easyocr_reader(ocr_gpu)

            all_results = []
            total = len(self.image_paths)

            for index, image_path in enumerate(self.image_paths, start=1):
                image_path_obj = Path(image_path)

                mode_text = "YOLO 字元模型" if use_yolo_class_as_char else "YOLO + EasyOCR"

                self.progress_signal.emit(
                    f"[{index}/{total}] 正在辨識：{image_path_obj.name}\n"
                    f"辨識模式：{mode_text}\n"
                    f"目前運算模式：{device_status}"
                )

                result = recognize_one_image(
                    image_path=image_path_obj,
                    model=model,
                    reader=reader,
                    model_name=model_name,
                    class_map=class_map,
                    use_yolo_class_as_char=use_yolo_class_as_char
                )

                all_results.append(result)

            self.finished_signal.emit(all_results)

        except Exception as e:
            self.error_signal.emit(str(e))