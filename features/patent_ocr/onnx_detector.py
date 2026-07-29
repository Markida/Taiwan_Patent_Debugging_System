"""Small OpenCV-DNN adapter for the production YOLO ONNX detector."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from features.patent_ocr.image_io import read_image


def load_onnx_class_names(model_path):
    """Load an optional class-order sidecar without requiring the onnx package."""

    model_path = Path(model_path)
    sidecar = model_path.with_suffix(".names.json")
    if not sidecar.exists():
        return {0: "patent_label"}
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        values = payload.get("names", payload) if isinstance(payload, dict) else payload
        if isinstance(values, dict):
            names = {int(index): str(name) for index, name in values.items()}
        else:
            names = {index: str(name) for index, name in enumerate(values)}
    except Exception as error:
        raise RuntimeError(f"Invalid ONNX class-name sidecar: {sidecar}") from error
    if not names or sorted(names) != list(range(len(names))):
        raise RuntimeError(f"ONNX class-name sidecar is incomplete: {sidecar}")
    return names


def load_onnx_network(model_path):
    """Load ONNX with a Unicode-safe buffer fallback for Windows OpenCV."""

    model_path = Path(model_path)
    try:
        return cv2.dnn.readNetFromONNX(str(model_path))
    except cv2.error as path_error:
        try:
            model_bytes = np.fromfile(str(model_path), dtype=np.uint8)
            if not model_bytes.size:
                raise OSError(f"ONNX model is empty: {model_path}")
            return cv2.dnn.readNetFromONNX(model_bytes)
        except (OSError, ValueError, TypeError, cv2.error) as buffer_error:
            raise RuntimeError(f"ONNX model load failed: {model_path}") from buffer_error


class DetectionBox:
    def __init__(self, xyxy, confidence, class_id):
        self.xyxy = np.asarray([xyxy], dtype=np.float32)
        self.conf = np.asarray([confidence], dtype=np.float32)
        self.cls = np.asarray([class_id], dtype=np.float32)


class DetectionResult:
    def __init__(self, boxes):
        self.boxes = boxes


def _letterbox(image, size):
    height, width = image.shape[:2]
    ratio = min(size / height, size / width)
    resized_width = int(round(width * ratio))
    resized_height = int(round(height * ratio))
    if (resized_width, resized_height) != (width, height):
        image = cv2.resize(
            image,
            (resized_width, resized_height),
            interpolation=cv2.INTER_LINEAR,
        )

    horizontal_padding = (size - resized_width) / 2
    vertical_padding = (size - resized_height) / 2
    left = int(round(horizontal_padding - 0.1))
    right = int(round(horizontal_padding + 0.1))
    top = int(round(vertical_padding - 0.1))
    bottom = int(round(vertical_padding + 0.1))
    image = cv2.copyMakeBorder(
        image,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_CONSTANT,
        value=(114, 114, 114),
    )
    return image, ratio, (left, top)


def _box_iou(reference_box, candidate_boxes):
    intersection = np.maximum(
        0,
        np.minimum(reference_box[2:], candidate_boxes[:, 2:])
        - np.maximum(reference_box[:2], candidate_boxes[:, :2]),
    ).prod(axis=1)
    reference_area = (
        (reference_box[2] - reference_box[0])
        * (reference_box[3] - reference_box[1])
    )
    candidate_areas = (
        (candidate_boxes[:, 2] - candidate_boxes[:, 0])
        * (candidate_boxes[:, 3] - candidate_boxes[:, 1])
    )
    return intersection / (reference_area + candidate_areas - intersection + 1e-7)


def _nms(boxes, scores, classes, iou_threshold, max_detections=300):
    kept_indices = []
    for class_id in np.unique(classes):
        class_indices = np.where(classes == class_id)[0]
        order = class_indices[np.argsort(-scores[class_indices])]
        while len(order) and len(kept_indices) < max_detections:
            current = order[0]
            kept_indices.append(current)
            if len(order) == 1:
                break
            remaining = order[1:]
            overlaps = _box_iou(boxes[current], boxes[remaining])
            order = remaining[overlaps <= iou_threshold]

    if not kept_indices:
        return np.empty(0, dtype=np.int64)
    kept = np.asarray(kept_indices, dtype=np.int64)
    return kept[np.argsort(-scores[kept])][:max_detections]


class OnnxDetector:
    """Run the one-class production detector through bundled OpenCV DNN."""

    names = {0: "patent_label"}

    def __init__(self, model_path):
        self.model_path = Path(model_path)
        self.names = load_onnx_class_names(self.model_path)
        self.network = load_onnx_network(self.model_path)

    def predict(
        self,
        source,
        imgsz=1536,
        conf=0.25,
        iou=0.4,
        device="cpu",
        verbose=False,
    ):
        del device, verbose
        image = read_image(source)
        if image is None:
            raise ValueError(f"圖片讀取失敗：{source}")

        input_size = int(imgsz or 1536)
        padded, ratio, (left, top) = _letterbox(image, input_size)
        blob = np.ascontiguousarray(
            padded[:, :, ::-1].transpose(2, 0, 1),
            dtype=np.float32,
        )[None]
        blob /= 255.0
        self.network.setInput(blob)
        predictions = self.network.forward()[0]
        if predictions.shape[0] < predictions.shape[1]:
            predictions = predictions.transpose(1, 0)

        xywh = predictions[:, :4]
        class_scores = predictions[:, 4:].max(axis=1)
        classes = predictions[:, 4:].argmax(axis=1)
        confident = class_scores >= float(conf)
        xywh = xywh[confident]
        class_scores = class_scores[confident]
        classes = classes[confident]
        if not len(xywh):
            return [DetectionResult([])]

        boxes = np.empty_like(xywh)
        boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
        boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
        boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
        boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2

        kept = _nms(boxes, class_scores, classes, float(iou))
        boxes = boxes[kept]
        class_scores = class_scores[kept]
        classes = classes[kept]
        height, width = image.shape[:2]
        boxes[:, [0, 2]] = (boxes[:, [0, 2]] - left) / ratio
        boxes[:, [1, 3]] = (boxes[:, [1, 3]] - top) / ratio
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)

        runtime_boxes = [
            DetectionBox(box.tolist(), float(score), int(class_id))
            for box, score, class_id in zip(boxes, class_scores, classes)
        ]
        return [DetectionResult(runtime_boxes)]
