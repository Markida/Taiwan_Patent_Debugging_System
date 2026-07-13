"""Evaluate a complete-label detector at the application's fixed thresholds."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
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
from PIL import Image
from ultralytics import YOLO

try:
    from .build_group_locator_dataset import group_character_annotations
    from .build_training_datasets import (
        DEFAULT_ANNOTATIONS,
        DEFAULT_CROP_MANIFEST,
        load_annotation_state,
        load_page_rotations,
        rotate_page,
    )
    from .evaluate_locator_by_class import box_iou
except ImportError:
    from build_group_locator_dataset import group_character_annotations
    from build_training_datasets import (
        DEFAULT_ANNOTATIONS,
        DEFAULT_CROP_MANIFEST,
        load_annotation_state,
        load_page_rotations,
        rotate_page,
    )
    from evaluate_locator_by_class import box_iou


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_IMAGES = SCRIPT_DIR / "group_locator_dataset_v2" / "images"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate complete-label detector.")
    parser.add_argument("model", type=Path)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--rotation-manifest", type=Path, default=DEFAULT_CROP_MANIFEST)
    parser.add_argument("--normalized-images", type=Path, default=DEFAULT_IMAGES)
    parser.add_argument("--split", choices=("train", "val"), default="val")
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--nms-iou", type=float, default=0.4)
    parser.add_argument("--with-ocr", action="store_true")
    parser.add_argument(
        "--targets-only",
        action="store_true",
        help="Only evaluate pages containing a multi-character Roman or prime label.",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def match_boxes(ground_truth, predictions, threshold):
    candidates = []
    for gt_index, gt in enumerate(ground_truth):
        gt_box = (gt.x1, gt.y1, gt.x2, gt.y2)
        for prediction_index, prediction in enumerate(predictions):
            predicted_box = (
                prediction["x1"],
                prediction["y1"],
                prediction["x2"],
                prediction["y2"],
            )
            iou = box_iou(gt_box, predicted_box)
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


def metric_summary(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def evaluate(args):
    annotation_root = args.annotations.resolve()
    rotations = load_page_rotations(args.rotation_manifest.resolve())
    records, _counts, _sizes, _reviewed = load_annotation_state(annotation_root)
    records = [record for record in records if record["split"] == args.split]
    model = YOLO(str(args.model.resolve()))
    device = 0 if torch.cuda.is_available() else "cpu"

    reader = None
    recognize_one_image = None
    if args.with_ocr:
        from features.patent_ocr.easyocr_loader import create_easyocr_reader
        from features.patent_ocr.ocr_engine import recognize_one_image as runtime_recognize

        reader = create_easyocr_reader(False)
        recognize_one_image = runtime_recognize

    page_results = []
    totals_by_threshold = {0.3: Counter(), 0.5: Counter()}
    text_totals = Counter()
    target_totals = {
        "roman": Counter(),
        "prime": Counter(),
    }
    target_by_text = {
        "roman": defaultdict(Counter),
        "prime": defaultdict(Counter),
    }

    for record in records:
        split = record["split"]
        page_id = record["page_id"]
        source_stem = Path(record["image_path"]).stem
        rotation = rotations.get(
            (split, source_stem),
            int(record.get("rotation") or 0) % 360,
        )
        source_path = annotation_root / record["image_path"]
        with Image.open(source_path) as image:
            rotated_image, annotations = rotate_page(
                image.convert("RGB"),
                record["_annotations"],
                rotation,
            )
        ground_truth = group_character_annotations(annotations)
        has_target = any(
            "'" in group.text
            or (len(group.text) > 1 and set(group.text) <= set("IVX"))
            for group in ground_truth
        )
        if args.targets_only and not has_target:
            continue
        normalized_image_path = (
            args.normalized_images.resolve() / split / f"{page_id}.png"
        )

        prediction = model.predict(
            source=str(normalized_image_path),
            imgsz=args.imgsz,
            conf=args.conf,
            iou=args.nms_iou,
            device=device,
            verbose=False,
        )[0]
        boxes = []
        if prediction.boxes is not None:
            for box in prediction.boxes:
                x1, y1, x2, y2 = (float(value) for value in box.xyxy[0])
                boxes.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2})

        for threshold, totals in totals_by_threshold.items():
            matches = match_boxes(ground_truth, boxes, threshold)
            totals["tp"] += len(matches)
            totals["fp"] += len(boxes) - len(matches)
            totals["fn"] += len(ground_truth) - len(matches)

        page_row = {
            "page_id": page_id,
            "ground_truth": len(ground_truth),
            "predictions": len(boxes),
        }

        if args.with_ocr:
            runtime_result = recognize_one_image(
                image_path=normalized_image_path,
                model=model,
                reader=reader,
                model_name=args.model.name,
                yolo_conf=args.conf,
                imgsz=args.imgsz,
            )
            detections = runtime_result["detections"]
            matches = match_boxes(ground_truth, detections, 0.3)
            correct = 0
            for gt_index, prediction_index, _iou in matches:
                gt = ground_truth[gt_index]
                predicted_text = detections[prediction_index]["label"]
                is_correct = predicted_text == gt.text
                correct += int(is_correct)
                text_totals["detected"] += 1
                text_totals["correct"] += int(is_correct)

                targets = []
                if len(gt.text) > 1 and set(gt.text) <= set("IVX"):
                    targets.append("roman")
                if "'" in gt.text:
                    targets.append("prime")
                for target in targets:
                    target_totals[target]["detected"] += 1
                    target_totals[target]["correct"] += int(is_correct)
                    target_by_text[target][gt.text]["detected"] += 1
                    target_by_text[target][gt.text]["correct"] += int(is_correct)

            matched_gt = {match[0] for match in matches}
            for index, gt in enumerate(ground_truth):
                targets = []
                if len(gt.text) > 1 and set(gt.text) <= set("IVX"):
                    targets.append("roman")
                if "'" in gt.text:
                    targets.append("prime")
                for target in targets:
                    target_totals[target]["total"] += 1
                    target_by_text[target][gt.text]["total"] += 1
            text_totals["total"] += len(ground_truth)
            page_row.update(
                {
                    "ocr_detections": len(detections),
                    "matched": len(matches),
                    "correct_text": correct,
                    "labels": runtime_result["labels"],
                }
            )

        page_results.append(page_row)

    report = {
        "model": str(args.model.resolve()),
        "model_names": model.names,
        "split": args.split,
        "images": len(page_results),
        "imgsz": args.imgsz,
        "confidence": args.conf,
        "nms_iou": args.nms_iou,
        "rotation_normalized": True,
        "metrics": {
            f"iou{int(threshold * 100)}": metric_summary(
                totals["tp"], totals["fp"], totals["fn"]
            )
            for threshold, totals in totals_by_threshold.items()
        },
        "pages": page_results,
    }
    if args.with_ocr:
        detected = text_totals["detected"]
        total = text_totals["total"]
        report["ocr"] = {
            "matched_boxes": detected,
            "correct_text": text_totals["correct"],
            "accuracy_when_detected": (
                text_totals["correct"] / detected if detected else 0.0
            ),
            "end_to_end_recall": text_totals["correct"] / total if total else 0.0,
            "targets": {
                name: {
                    "total": counts["total"],
                    "detected": counts["detected"],
                    "correct": counts["correct"],
                    "end_to_end_recall": (
                        counts["correct"] / counts["total"]
                        if counts["total"]
                        else 0.0
                    ),
                    "by_text": {
                        text: {
                            "total": values["total"],
                            "detected": values["detected"],
                            "correct": values["correct"],
                            "end_to_end_recall": (
                                values["correct"] / values["total"]
                                if values["total"]
                                else 0.0
                            ),
                        }
                        for text, values in sorted(target_by_text[name].items())
                    },
                }
                for name, counts in target_totals.items()
            },
        }
    return report


def main():
    args = parse_args()
    report = evaluate(args)
    output = args.output or (PROJECT_ROOT / "models" / "group_locator_v1_report.json")
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
