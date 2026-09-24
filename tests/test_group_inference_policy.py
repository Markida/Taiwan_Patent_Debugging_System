import cv2
import numpy as np

from features.patent_ocr.ocr_engine import recognize_one_image
from features.patent_ocr.onnx_detector import DetectionResult


class EmptyGroupDetector:
    names = {0: "patent_label"}
    supports_sliced_inference = True

    def __init__(self):
        self.kwargs = None

    def predict(self, **kwargs):
        self.kwargs = kwargs
        return [DetectionResult([])]


def test_complete_label_model_uses_calibrated_gold_policy(tmp_path):
    image_path = tmp_path / "page.png"
    cv2.imwrite(str(image_path), np.full((80, 100, 3), 255, dtype=np.uint8))
    model = EmptyGroupDetector()

    result = recognize_one_image(
        image_path=image_path,
        model=model,
        reader=object(),
        model_name="patent_label_group_v2_gold_ft.onnx",
    )

    assert result["detections"] == []
    assert model.kwargs["conf"] == 0.10
    assert model.kwargs["iou"] == 0.30
    assert model.kwargs["sliced"] is False
    assert model.kwargs["slice_conf"] == 0.25
    assert model.kwargs["tile_fraction"] == 0.58


def test_evaluation_can_override_group_thresholds_and_disable_slices(tmp_path):
    image_path = tmp_path / "page.png"
    cv2.imwrite(str(image_path), np.full((80, 100, 3), 255, dtype=np.uint8))
    model = EmptyGroupDetector()

    recognize_one_image(
        image_path=image_path,
        model=model,
        reader=object(),
        model_name="patent_label_group_v2_gold_ft.onnx",
        yolo_conf=0.25,
        nms_iou=0.40,
        sliced_group_inference=False,
    )

    assert model.kwargs["conf"] == 0.25
    assert model.kwargs["iou"] == 0.40
    assert model.kwargs["sliced"] is False
