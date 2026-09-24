import json

import cv2
import numpy as np

from features.patent_ocr.onnx_detector import (
    OnnxDetector,
    load_onnx_class_names,
    load_onnx_network,
)


def test_onnx_names_sidecar_supports_case_sensitive_v3_classes(tmp_path):
    model_path = tmp_path / "model.onnx"
    sidecar = tmp_path / "model.names.json"
    sidecar.write_text(
        json.dumps({"names": ["0", "A", "prime", "a"]}),
        encoding="utf-8",
    )

    assert load_onnx_class_names(model_path) == {
        0: "0",
        1: "A",
        2: "prime",
        3: "a",
    }


def test_onnx_names_default_remains_group_locator(tmp_path):
    assert load_onnx_class_names(tmp_path / "locator.onnx") == {
        0: "patent_label"
    }


def test_onnx_model_uses_unicode_safe_buffer_fallback(tmp_path, monkeypatch):
    model_path = tmp_path / "中文模型.onnx"
    model_path.write_bytes(b"fake-onnx")
    expected_network = object()
    calls = []

    def fake_loader(source):
        calls.append(source)
        if isinstance(source, str):
            raise cv2.error("path loading failed")
        assert isinstance(source, np.ndarray)
        assert source.tobytes() == b"fake-onnx"
        return expected_network

    monkeypatch.setattr(cv2.dnn, "readNetFromONNX", fake_loader)

    assert load_onnx_network(model_path) is expected_network
    assert isinstance(calls[0], str)
    assert isinstance(calls[1], np.ndarray)


def test_sliced_inference_combines_full_page_and_four_tiles(monkeypatch):
    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    monkeypatch.setattr(
        "features.patent_ocr.onnx_detector.read_image",
        lambda _source: image,
    )
    detector = OnnxDetector.__new__(OnnxDetector)
    calls = []

    def fake_infer(input_image, input_size, confidence):
        calls.append((input_image.shape[:2], input_size, confidence))
        if len(calls) == 1:
            box = [5, 5, 15, 15]
        else:
            box = [20, 20, 30, 30]
        return (
            np.asarray([box], dtype=np.float32),
            np.asarray([0.9], dtype=np.float32),
            np.asarray([0], dtype=np.int32),
        )

    detector._infer_image = fake_infer
    results = detector.predict(
        "unused.png",
        imgsz=1536,
        conf=0.08,
        iou=0.30,
        sliced=True,
        slice_conf=0.25,
        tile_fraction=0.58,
    )

    assert len(calls) == 5
    assert calls[0] == ((100, 100), 1536, 0.08)
    assert all(call[0] == (58, 58) for call in calls[1:])
    assert all(call[2] == 0.25 for call in calls[1:])
    assert len(results[0].boxes) == 5
