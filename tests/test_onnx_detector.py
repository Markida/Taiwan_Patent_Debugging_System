import json

import cv2
import numpy as np

from features.patent_ocr.onnx_detector import (
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
