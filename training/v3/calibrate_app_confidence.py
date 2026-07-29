"""Calibrate the production YOLO confidence threshold on manual validation boxes."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

try:
    from .common import configure_local_runtime_dirs
except ImportError:
    from common import configure_local_runtime_dirs

configure_local_runtime_dirs()

from ultralytics import YOLO


DEFAULT_THRESHOLDS = tuple(value / 100 for value in range(5, 95, 5))
APP_CLASS_MINIMUMS = {"J": 0.90, "j": 0.90}


def parse_args():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        type=Path,
        default=root / "models" / "patent_char_v3_consensus.pt",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=root / "training" / "char_yolo" / "dataset_v3_consensus",
    )
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--device", default="0")
    parser.add_argument("--iou", type=float, default=0.40)
    parser.add_argument("--match-iou", type=float, default=0.50)
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "models" / "patent_char_v3_confidence_calibration.json",
    )
    return parser.parse_args()


def box_iou(first, second):
    x1 = max(first[0], second[0])
    y1 = max(first[1], second[1])
    x2 = min(first[2], second[2])
    y2 = min(first[3], second[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


def load_ground_truth(label_path, width, height):
    boxes = []
    for line in label_path.read_text(encoding="utf-8").splitlines():
        values = line.split()
        if len(values) != 5:
            continue
        class_id = int(values[0])
        xc, yc, box_width, box_height = map(float, values[1:])
        xc *= width
        yc *= height
        box_width *= width
        box_height *= height
        boxes.append(
            (
                class_id,
                (
                    xc - box_width / 2,
                    yc - box_height / 2,
                    xc + box_width / 2,
                    yc + box_height / 2,
                ),
            )
        )
    return boxes


def measure(records, names, threshold, match_iou, use_app_minimums):
    totals = {"tp": 0, "fp": 0, "fn": 0}
    per_class = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for ground_truth, predictions in records:
        matched = set()
        accepted = []
        for class_id, confidence, box in predictions:
            class_name = str(names[class_id])
            required = float(threshold)
            if use_app_minimums:
                required = max(required, APP_CLASS_MINIMUMS.get(class_name, required))
            if confidence >= required:
                accepted.append((class_id, confidence, box))
        accepted.sort(key=lambda item: item[1], reverse=True)

        for class_id, _confidence, prediction_box in accepted:
            candidates = [
                (box_iou(prediction_box, truth_box), index)
                for index, (truth_class, truth_box) in enumerate(ground_truth)
                if index not in matched and truth_class == class_id
            ]
            best_iou, best_index = max(candidates, default=(0.0, -1))
            class_name = str(names[class_id])
            if best_iou >= match_iou:
                matched.add(best_index)
                totals["tp"] += 1
                per_class[class_name]["tp"] += 1
            else:
                totals["fp"] += 1
                per_class[class_name]["fp"] += 1

        for index, (class_id, _truth_box) in enumerate(ground_truth):
            if index not in matched:
                class_name = str(names[class_id])
                totals["fn"] += 1
                per_class[class_name]["fn"] += 1

    precision = totals["tp"] / max(1, totals["tp"] + totals["fp"])
    recall = totals["tp"] / max(1, totals["tp"] + totals["fn"])
    f1 = 2 * precision * recall / max(1e-12, precision + recall)
    return {
        "threshold": round(float(threshold), 2),
        **totals,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "risk_classes": {
            name: dict(per_class[name])
            for name in ("0", "6", "7", "J", "j", "prime")
        },
    }


def main():
    args = parse_args()
    model_path = args.model.resolve()
    dataset = args.dataset.resolve()
    image_paths = sorted((dataset / "images" / "val").glob("*"))
    if not image_paths:
        raise FileNotFoundError("No validation images were found.")

    model = YOLO(str(model_path))
    results = model.predict(
        source=[str(path) for path in image_paths],
        imgsz=args.imgsz,
        conf=min(DEFAULT_THRESHOLDS),
        iou=args.iou,
        device=args.device,
        verbose=False,
    )
    records = []
    for image_path, result in zip(image_paths, results):
        height, width = result.orig_shape
        label_path = dataset / "labels" / "val" / f"{image_path.stem}.txt"
        ground_truth = load_ground_truth(label_path, width, height)
        predictions = []
        if result.boxes is not None:
            for box in result.boxes:
                predictions.append(
                    (
                        int(box.cls[0]),
                        float(box.conf[0]),
                        tuple(float(value) for value in box.xyxy[0]),
                    )
                )
        records.append((ground_truth, predictions))

    raw = [
        measure(records, model.names, value, args.match_iou, False)
        for value in DEFAULT_THRESHOLDS
    ]
    app_filtered = [
        measure(records, model.names, value, args.match_iou, True)
        for value in DEFAULT_THRESHOLDS
    ]
    recommended = max(app_filtered, key=lambda row: (row["f1"], row["precision"]))
    report = {
        "model": str(model_path),
        "validation_images": len(image_paths),
        "match_iou": args.match_iou,
        "prediction_iou": args.iou,
        "application_class_minimums": APP_CLASS_MINIMUMS,
        "recommended_global_threshold": recommended["threshold"],
        "selection_policy": "maximum F1 after existing 6/7/J application filters",
        "raw": raw,
        "application_filtered": app_filtered,
    }
    args.output.resolve().write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
