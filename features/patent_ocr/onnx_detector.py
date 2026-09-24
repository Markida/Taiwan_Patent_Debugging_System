"""Small OpenCV-DNN adapter for the production YOLO ONNX detector."""

from __future__ import annotations

import json
import math
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
    supports_sliced_inference = True

    def __init__(self, model_path):
        self.model_path = Path(model_path)
        self.names = load_onnx_class_names(self.model_path)
        self.network = load_onnx_network(self.model_path)

    def _infer_image(self, image, input_size, confidence):
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
        classes = predictions[:, 4:].argmax(axis=1).astype(np.int32)
        confident = class_scores >= float(confidence)
        xywh = xywh[confident]
        class_scores = class_scores[confident].astype(np.float32)
        classes = classes[confident]
        if not len(xywh):
            return (
                np.empty((0, 4), dtype=np.float32),
                np.empty(0, dtype=np.float32),
                np.empty(0, dtype=np.int32),
            )

        boxes = np.empty_like(xywh, dtype=np.float32)
        boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
        boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
        boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
        boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
        height, width = image.shape[:2]
        boxes[:, [0, 2]] = (boxes[:, [0, 2]] - left) / ratio
        boxes[:, [1, 3]] = (boxes[:, [1, 3]] - top) / ratio
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)
        valid = (boxes[:, 2] - boxes[:, 0] >= 1) & (
            boxes[:, 3] - boxes[:, 1] >= 1
        )
        return boxes[valid], class_scores[valid], classes[valid]

    @staticmethod
    def _tiles(width, height, fraction):
        tile_width = min(width, max(1, int(math.ceil(width * fraction))))
        tile_height = min(height, max(1, int(math.ceil(height * fraction))))
        x_starts = sorted({0, width - tile_width})
        y_starts = sorted({0, height - tile_height})
        return [
            (x1, y1, x1 + tile_width, y1 + tile_height)
            for y1 in y_starts
            for x1 in x_starts
        ]

    @staticmethod
    def _apply_nms(boxes, scores, classes, iou):
        if not len(boxes):
            return boxes, scores, classes
        kept = _nms(boxes, scores, classes, float(iou), max_detections=500)
        return boxes[kept], scores[kept], classes[kept]

    def _infer_slices(
        self,
        image,
        input_size,
        confidence,
        iou,
        tile_fraction,
        edge_margin,
    ):
        height, width = image.shape[:2]
        all_boxes = []
        all_scores = []
        all_classes = []
        for x1, y1, x2, y2 in self._tiles(width, height, tile_fraction):
            boxes, scores, classes = self._infer_image(
                image[y1:y2, x1:x2],
                input_size,
                confidence,
            )
            if not len(boxes):
                continue
            tile_width = x2 - x1
            tile_height = y2 - y1
            complete = np.ones(len(boxes), dtype=bool)
            if x1 > 0:
                complete &= boxes[:, 0] > edge_margin
            if y1 > 0:
                complete &= boxes[:, 1] > edge_margin
            if x2 < width:
                complete &= boxes[:, 2] < tile_width - edge_margin
            if y2 < height:
                complete &= boxes[:, 3] < tile_height - edge_margin
            boxes = boxes[complete]
            scores = scores[complete]
            classes = classes[complete]
            boxes[:, [0, 2]] += x1
            boxes[:, [1, 3]] += y1
            all_boxes.append(boxes)
            all_scores.append(scores)
            all_classes.append(classes)
        if not all_boxes:
            return (
                np.empty((0, 4), dtype=np.float32),
                np.empty(0, dtype=np.float32),
                np.empty(0, dtype=np.int32),
            )
        return self._apply_nms(
            np.concatenate(all_boxes),
            np.concatenate(all_scores),
            np.concatenate(all_classes),
            iou,
        )

    def predict(
        self,
        source,
        imgsz=1536,
        conf=0.25,
        iou=0.4,
        device="cpu",
        verbose=False,
        sliced=False,
        slice_conf=0.25,
        tile_fraction=0.58,
        edge_margin=4.0,
    ):
        del device, verbose
        image = read_image(source)
        if image is None:
            raise ValueError(f"圖片讀取失敗：{source}")

        input_size = int(imgsz or 1536)
        boxes, class_scores, classes = self._infer_image(
            image,
            input_size,
            conf,
        )
        boxes, class_scores, classes = self._apply_nms(
            boxes,
            class_scores,
            classes,
            iou,
        )
        if sliced:
            sliced_rows = self._infer_slices(
                image,
                input_size,
                float(slice_conf),
                float(iou),
                float(tile_fraction),
                float(edge_margin),
            )
            boxes = np.concatenate([boxes, sliced_rows[0]])
            class_scores = np.concatenate([class_scores, sliced_rows[1]])
            classes = np.concatenate([classes, sliced_rows[2]])
            boxes, class_scores, classes = self._apply_nms(
                boxes,
                class_scores,
                classes,
                iou,
            )

        runtime_boxes = [
            DetectionBox(box.tolist(), float(score), int(class_id))
            for box, score, class_id in zip(boxes, class_scores, classes)
        ]
        return [DetectionResult(runtime_boxes)]
