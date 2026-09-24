from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=4)
def _load_detection_model_cached(path_text, modified_ns, size_bytes):
    """Load one immutable on-disk model version and retain it for reuse."""

    del modified_ns, size_bytes
    model_path = Path(path_text)

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


def load_detection_model(model_path):
    """Load a model once per path/version instead of once per recognition run."""

    model_path = Path(model_path).resolve()
    stat = model_path.stat()
    return _load_detection_model_cached(
        str(model_path),
        stat.st_mtime_ns,
        stat.st_size,
    )


def clear_detection_model_cache():
    """Release retained models for tests or explicit maintenance workflows."""

    _load_detection_model_cached.cache_clear()
