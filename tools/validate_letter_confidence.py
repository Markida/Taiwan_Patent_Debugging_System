"""Measure the production letter-confidence policy on manual validation boxes."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.v3.common import configure_local_runtime_dirs

configure_local_runtime_dirs()

from ultralytics import YOLO

from app.config import LETTER_MIN_CONFIDENCE, V3_YOLO_CONF
from training.v3.calibrate_app_confidence import box_iou, load_ground_truth


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        type=Path,
        default=PROJECT_ROOT / "models" / "patent_char_v4_company_approved_recall.pt",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "training" / "char_yolo" / "dataset_v4_company_approved",
    )
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--device", default="0")
    parser.add_argument("--match-iou", type=float, default=0.50)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "models" / "letter_confidence_v108_validation.json",
    )
    return parser.parse_args()


def class_group(name: str) -> str:
    if name.upper() in {"I", "V", "X"}:
        return "roman_ivx"
    if len(name) == 1 and name.isalpha():
        return "ordinary_letters"
    return "digits_and_prime"


def accepted_threshold(name: str, ordinary_letter_minimum: float) -> float:
    if name.upper() == "J":
        return 0.90
    if class_group(name) == "ordinary_letters":
        return ordinary_letter_minimum
    return float(V3_YOLO_CONF)


def evaluate(records, names, ordinary_letter_minimum, match_iou):
    totals = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for ground_truth, predictions in records:
        matched = set()
        accepted = [
            item
            for item in predictions
            if item[1] >= accepted_threshold(names[item[0]], ordinary_letter_minimum)
        ]
        accepted.sort(key=lambda item: item[1], reverse=True)

        for class_id, _confidence, prediction_box in accepted:
            group = class_group(names[class_id])
            candidates = [
                (box_iou(prediction_box, truth_box), index)
                for index, (truth_class, truth_box) in enumerate(ground_truth)
                if index not in matched and truth_class == class_id
            ]
            best_iou, best_index = max(candidates, default=(0.0, -1))
            if best_iou >= match_iou:
                matched.add(best_index)
                totals["all"]["tp"] += 1
                totals[group]["tp"] += 1
            else:
                totals["all"]["fp"] += 1
                totals[group]["fp"] += 1

        for index, (class_id, _truth_box) in enumerate(ground_truth):
            if index in matched:
                continue
            group = class_group(names[class_id])
            totals["all"]["fn"] += 1
            totals[group]["fn"] += 1

    result = {}
    for group in ("all", "digits_and_prime", "roman_ivx", "ordinary_letters"):
        row = dict(totals[group])
        row["precision"] = row["tp"] / max(1, row["tp"] + row["fp"])
        row["recall"] = row["tp"] / max(1, row["tp"] + row["fn"])
        result[group] = row
    return result


def main():
    args = parse_args()
    dataset = args.dataset.resolve()
    image_paths = sorted((dataset / "images" / "val").glob("*"))
    model = YOLO(str(args.model.resolve()))
    names = {
        int(index): str(name)
        for index, name in (
            model.names.items()
            if isinstance(model.names, dict)
            else enumerate(model.names)
        )
    }
    predictions = model.predict(
        source=[str(path) for path in image_paths],
        imgsz=args.imgsz,
        conf=float(V3_YOLO_CONF),
        iou=0.40,
        device=args.device,
        verbose=False,
    )

    records = []
    for image_path, result in zip(image_paths, predictions):
        height, width = result.orig_shape
        ground_truth = load_ground_truth(
            dataset / "labels" / "val" / f"{image_path.stem}.txt",
            width,
            height,
        )
        boxes = []
        if result.boxes is not None:
            for box in result.boxes:
                boxes.append(
                    (
                        int(box.cls[0]),
                        float(box.conf[0]),
                        tuple(float(value) for value in box.xyxy[0]),
                    )
                )
        records.append((ground_truth, boxes))

    baseline = evaluate(records, names, V3_YOLO_CONF, args.match_iou)
    candidate = evaluate(records, names, LETTER_MIN_CONFIDENCE, args.match_iou)
    threshold_sweep = {
        f"{threshold:.2f}": evaluate(records, names, threshold, args.match_iou)
        for threshold in (0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50)
    }
    report = {
        "model": str(args.model.resolve()),
        "validation_images": len(image_paths),
        "policy": {
            "numeric_and_prime_minimum": V3_YOLO_CONF,
            "roman_ivx_minimum": V3_YOLO_CONF,
            "ordinary_letter_minimum": LETTER_MIN_CONFIDENCE,
            "j_minimum": 0.90,
        },
        "baseline": baseline,
        "candidate": candidate,
        "threshold_sweep": threshold_sweep,
        "change": {
            "ordinary_letter_false_positives_removed": (
                baseline["ordinary_letters"]["fp"]
                - candidate["ordinary_letters"]["fp"]
            ),
            "ordinary_letter_true_positives_lost": (
                baseline["ordinary_letters"]["tp"]
                - candidate["ordinary_letters"]["tp"]
            ),
            "digit_and_prime_true_positives_lost": (
                baseline["digits_and_prime"]["tp"]
                - candidate["digits_and_prime"]["tp"]
            ),
            "roman_ivx_true_positives_lost": (
                baseline["roman_ivx"]["tp"]
                - candidate["roman_ivx"]["tp"]
            ),
        },
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
