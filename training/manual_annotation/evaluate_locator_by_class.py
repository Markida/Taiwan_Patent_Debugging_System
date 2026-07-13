"""Evaluate one-class locator recall by manually assigned character class."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path


def configure_conda_dll_paths():
    environment_root = Path(sys.executable).resolve().parent
    candidates = [
        environment_root,
        environment_root / "Library" / "mingw-w64" / "bin",
        environment_root / "Library" / "usr" / "bin",
        environment_root / "Library" / "bin",
        environment_root / "Scripts",
        environment_root / "bin",
    ]
    existing = [str(path) for path in candidates if path.exists()]
    if existing:
        os.environ["PATH"] = os.pathsep.join(existing + [os.environ.get("PATH", "")])
    if hasattr(os, "add_dll_directory"):
        return [os.add_dll_directory(str(path)) for path in candidates if path.exists()]
    return []


CONDA_DLL_HANDLES = configure_conda_dll_paths()
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("YOLO_CONFIG_DIR", os.environ.get("TEMP", str(Path.cwd())))

import torch
from ultralytics import YOLO
from PIL import Image

try:
    from .common import Annotation, find_page_records, read_page_record
    from .build_training_datasets import load_page_rotations, rotate_page
except ImportError:
    from common import Annotation, find_page_records, read_page_record
    from build_training_datasets import load_page_rotations, rotate_page


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_ANNOTATIONS = SCRIPT_DIR / "annotation_set"
DEFAULT_CROP_MANIFEST = (
    PROJECT_ROOT
    / "training"
    / "crop_classifier"
    / "review_dataset"
    / "manifest.csv"
)


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate locator recall by glyph class.")
    parser.add_argument("models", nargs="+", type=Path)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--split", choices=("train", "val"), default="val")
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--nms-iou", type=float, default=0.4)
    parser.add_argument("--normalize-rotation", action="store_true")
    parser.add_argument("--rotation-manifest", type=Path, default=DEFAULT_CROP_MANIFEST)
    parser.add_argument("--output", type=Path, default=None)
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


def match_page(ground_truth, predictions, threshold):
    candidates = []
    for gt_index, gt in enumerate(ground_truth):
        gt_box = (gt.x1, gt.y1, gt.x2, gt.y2)
        for prediction_index, prediction in enumerate(predictions):
            iou = box_iou(gt_box, prediction)
            if iou >= threshold:
                candidates.append((iou, gt_index, prediction_index))
    candidates.sort(reverse=True)
    used_gt = set()
    used_predictions = set()
    matches = []
    for iou, gt_index, prediction_index in candidates:
        if gt_index in used_gt or prediction_index in used_predictions:
            continue
        used_gt.add(gt_index)
        used_predictions.add(prediction_index)
        matches.append((gt_index, prediction_index, iou))
    return matches


def evaluate_model(
    model_path,
    annotation_root,
    split,
    imgsz,
    conf,
    nms_iou,
    normalize_rotation=False,
    page_rotations=None,
):
    model = YOLO(str(model_path.resolve()))
    device = 0 if torch.cuda.is_available() else "cpu"
    records = [
        read_page_record(path)
        for path in find_page_records(annotation_root)
        if read_page_record(path).get("split") == split
    ]
    reports = {}

    for threshold in (0.3, 0.5):
        totals = Counter()
        matched_by_class = Counter()
        total_by_class = Counter()

        for record in records:
            ground_truth = [
                Annotation(**raw).normalized(record["width"], record["height"])
                for raw in record.get("annotations", [])
            ]
            ground_truth = [
                item
                for item in ground_truth
                if item.is_valid(record["width"], record["height"])
            ]
            image_path = annotation_root / record["image_path"]
            prediction_source = str(image_path)

            if normalize_rotation:
                rotation = (page_rotations or {}).get(
                    (record["split"], Path(record["image_path"]).stem),
                    int(record.get("rotation") or 0) % 360,
                )
                with Image.open(image_path) as source_image:
                    rotated_image, ground_truth = rotate_page(
                        source_image.convert("RGB"),
                        ground_truth,
                        rotation,
                    )
                prediction_source = rotated_image

            prediction = model.predict(
                source=prediction_source,
                imgsz=imgsz,
                conf=conf,
                iou=nms_iou,
                device=device,
                verbose=False,
            )[0]
            boxes = []
            if prediction.boxes is not None:
                for box in prediction.boxes:
                    boxes.append(tuple(float(value) for value in box.xyxy[0]))

            matches = match_page(ground_truth, boxes, threshold)
            matched_gt = {item[0] for item in matches}
            totals["tp"] += len(matches)
            totals["fp"] += len(boxes) - len(matches)
            totals["fn"] += len(ground_truth) - len(matches)

            for index, annotation in enumerate(ground_truth):
                total_by_class[annotation.label] += 1
                if index in matched_gt:
                    matched_by_class[annotation.label] += 1

        precision = totals["tp"] / (totals["tp"] + totals["fp"]) if totals["tp"] + totals["fp"] else 0.0
        recall = totals["tp"] / (totals["tp"] + totals["fn"]) if totals["tp"] + totals["fn"] else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        reports[f"iou{int(threshold * 100)}"] = {
            "tp": totals["tp"],
            "fp": totals["fp"],
            "fn": totals["fn"],
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "per_class_recall": {
                name: {
                    "matched": matched_by_class[name],
                    "total": total_by_class[name],
                    "recall": matched_by_class[name] / total_by_class[name],
                }
                for name in sorted(total_by_class)
            },
        }

    return {
        "model": str(model_path.resolve()),
        "split": split,
        "images": len(records),
        "imgsz": imgsz,
        "confidence": conf,
        "nms_iou": nms_iou,
        "rotation_normalized": normalize_rotation,
        "metrics": reports,
    }


def main():
    args = parse_args()
    annotation_root = args.annotations.resolve()
    page_rotations = (
        load_page_rotations(args.rotation_manifest.resolve())
        if args.normalize_rotation
        else {}
    )
    report = {
        "annotations": str(annotation_root),
        "models": [
            evaluate_model(
                path,
                annotation_root=annotation_root,
                split=args.split,
                imgsz=args.imgsz,
                conf=args.conf,
                nms_iou=args.nms_iou,
                normalize_rotation=args.normalize_rotation,
                page_rotations=page_rotations,
            )
            for path in args.models
        ],
    }
    output = args.output or (PROJECT_ROOT / "models" / "locator_comparison.json")
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
