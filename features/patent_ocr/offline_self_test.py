"""Runtime verification used by the portable offline release."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import cv2
import numpy as np
import torch

from app.paths import get_easyocr_model_dir, get_models_dir
from features.patent_ocr.easyocr_loader import create_easyocr_reader
from features.patent_ocr.model_loader import load_detection_model


def _write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_offline_self_test(report_path=None):
    report_path = Path(
        report_path
        or (Path(tempfile.gettempdir()) / "Saint-IslandPatentOCR_offline_check.json")
    )
    model_candidates = [
        get_models_dir() / "patent_char_v4_company_approved_recall.onnx",
        get_models_dir() / "patent_char_v3_consensus.onnx",
        get_models_dir() / "patent_label_group_v1.onnx",
    ]
    model_path = next(
        (path for path in model_candidates if path.exists()),
        model_candidates[0],
    )
    ocr_path = get_easyocr_model_dir() / "english_g2.pth"
    report = {
        "status": "FAILED",
        "torch": torch.__version__,
        "model": str(model_path),
        "ocr_model": str(ocr_path),
    }

    try:
        if not model_path.exists():
            raise FileNotFoundError(f"缺少正式模型：{model_path}")
        detector = load_detection_model(model_path)
        model_names = getattr(detector, "names", {})
        is_character_model = len(model_names) in {37, 63}
        if not is_character_model and not ocr_path.exists():
            raise FileNotFoundError(f"缺少 OCR 模型：{ocr_path}")
        reader = None if is_character_model else create_easyocr_reader(False)

        with tempfile.TemporaryDirectory(prefix="Saint-IslandPatentOCR_check_") as folder:
            image_path = Path(folder) / "runtime_test.png"
            image = np.full((192, 512, 3), 255, dtype=np.uint8)
            cv2.putText(
                image,
                "123A",
                (50, 135),
                cv2.FONT_HERSHEY_SIMPLEX,
                2.5,
                (0, 0, 0),
                5,
                cv2.LINE_AA,
            )
            if not cv2.imwrite(str(image_path), image):
                raise RuntimeError("無法建立暫存測試圖片。")

            detections = detector.predict(
                source=str(image_path),
                imgsz=1536,
                conf=0.05 if is_character_model else 0.25,
                iou=0.4,
                device="cpu",
                verbose=False,
            )
            if reader is not None:
                grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                reader.recognize(
                    grey,
                    horizontal_list=[[0, grey.shape[1], 0, grey.shape[0]]],
                    free_list=[],
                    allowlist="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'",
                    detail=1,
                    paragraph=False,
                )

        report.update(
            {
                "status": "PASS",
                "message": "離線執行環境與正式模型皆可正常載入及推論。",
                "dummy_detections": len(detections[0].boxes),
                "model_class_count": len(model_names),
                "easyocr_checked": reader is not None,
            }
        )
        _write_report(report_path, report)
        return 0
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        _write_report(report_path, report)
        return 1
