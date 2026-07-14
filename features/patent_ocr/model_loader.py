from pathlib import Path


def load_detection_model(model_path):
    """Load a compact production model or a development-time Ultralytics model."""

    model_path = Path(model_path)
    if model_path.suffix.lower() == ".onnx":
        from features.patent_ocr.onnx_detector import OnnxDetector

        return OnnxDetector(model_path)

    try:
        from ultralytics import YOLO
    except ImportError as error:
        raise RuntimeError(
            "此離線版只包含正式 ONNX 模型，無法載入 .pt 檔案。"
        ) from error
    return YOLO(str(model_path))
