"""Benchmark production figure-number OCR on reviewed ground-truth crops.

This intentionally bypasses the locator. It uses the reviewed identifier box
and its paired prefix class, then runs the same crop rotation and OCR helpers as
the application. A figure_prefix_rotate_right pair is rotated clockwise
90 degrees before OCR because its saved text is the post-rotation truth.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_ocr.easyocr_loader import create_easyocr_reader
from features.patent_ocr.figure_heading import (
    _identifier_crop,
    _recognize_identifier,
)
from features.patent_ocr.figure_heading_classes import (
    FIGURE_IDENTIFIER_CLASS,
    FIGURE_PREFIX_CLASS,
    FIGURE_PREFIX_CLASS_NAMES,
    FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
)
from features.patent_ocr.figure_identifiers import normalize_figure_identifier
from features.patent_ocr.image_io import read_image
from training.manual_annotation.common import (
    Annotation,
    find_page_records,
    read_page_record,
)


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ANNOTATIONS = (
    PROJECT_ROOT.parent
    / "AI訓練圖集"
    / "prepared_dataset_v1"
    / "figure_heading_annotation_v2"
)
DEFAULT_OUTPUT = SCRIPT_DIR / "pilot_250_20260917_crop_baseline.json"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--minimum-confidence", type=float, default=0.70)
    parser.add_argument("--require-reviewed-pages", type=int)
    parser.add_argument("--require-normal-pairs", type=int)
    parser.add_argument("--require-rotate-right-pairs", type=int)
    parser.add_argument("--progress-every", type=int, default=25)
    return parser.parse_args()


def _annotation_box(annotation):
    return {
        "x1": annotation.x1,
        "y1": annotation.y1,
        "x2": annotation.x2,
        "y2": annotation.y2,
    }


def _rate(numerator, denominator):
    return round(numerator / denominator, 6) if denominator else 0.0


def _summary(items, minimum_confidence):
    confidences = sorted(float(item["confidence"]) for item in items)
    exact = sum(bool(item["exact"]) for item in items)
    exact_and_confident = sum(
        bool(item["exact"])
        and float(item["confidence"]) >= float(minimum_confidence)
        for item in items
    )
    valid = sum(item["actual"] is not None for item in items)
    return {
        "pair_count": len(items),
        "normalized_value_count": valid,
        "normalized_exact_count": exact,
        "normalized_exact_accuracy": _rate(exact, len(items)),
        "exact_and_confident_count": exact_and_confident,
        "exact_and_confident_accuracy": _rate(
            exact_and_confident,
            len(items),
        ),
        "confidence_minimum": (
            round(confidences[0], 6) if confidences else 0.0
        ),
        "confidence_median": (
            round(statistics.median(confidences), 6) if confidences else 0.0
        ),
        "confidence_maximum": (
            round(confidences[-1], 6) if confidences else 0.0
        ),
    }


def _is_six_nine_swap(expected, actual):
    if not actual or len(expected) != len(actual) or expected == actual:
        return False
    changed = False
    for truth_character, actual_character in zip(expected, actual):
        if truth_character == actual_character:
            continue
        if (truth_character, actual_character) not in {("6", "9"), ("9", "6")}:
            return False
        changed = True
    return changed


def _reviewed_pair_tasks(annotation_root, records):
    tasks = []
    errors = []
    positive_pages = 0
    for record in records:
        annotations = [
            Annotation(**raw).normalized(record["width"], record["height"])
            for raw in record.get("annotations", [])
        ]
        grouped = defaultdict(lambda: {"prefixes": [], "identifiers": []})
        for annotation in annotations:
            if annotation.label not in (
                *FIGURE_PREFIX_CLASS_NAMES,
                FIGURE_IDENTIFIER_CLASS,
            ):
                continue
            if not annotation.pair_id:
                errors.append(
                    {
                        "page_id": record["page_id"],
                        "error": f"{annotation.label} 缺少 pair_id",
                    }
                )
                continue
            bucket = (
                "identifiers"
                if annotation.label == FIGURE_IDENTIFIER_CLASS
                else "prefixes"
            )
            grouped[annotation.pair_id][bucket].append(annotation)
        positive_pages += int(bool(grouped))

        for pair_id, group in sorted(grouped.items()):
            prefixes = group["prefixes"]
            identifiers = group["identifiers"]
            if len(prefixes) != 1 or len(identifiers) != 1:
                errors.append(
                    {
                        "page_id": record["page_id"],
                        "pair_id": pair_id,
                        "error": (
                            "每組必須恰有一個圖字框及一個圖號框；"
                            f"目前為 {len(prefixes)} / {len(identifiers)}"
                        ),
                    }
                )
                continue
            prefix = prefixes[0]
            identifier = identifiers[0]
            try:
                expected = str(normalize_figure_identifier(identifier.text))
            except ValueError as error:
                errors.append(
                    {
                        "page_id": record["page_id"],
                        "pair_id": pair_id,
                        "error": f"圖號文字真值無效：{error}",
                    }
                )
                continue
            if prefix.label == FIGURE_PREFIX_ROTATE_RIGHT_CLASS:
                correction_degrees = 90
            elif prefix.label == FIGURE_PREFIX_CLASS:
                correction_degrees = 0
            else:
                errors.append(
                    {
                        "page_id": record["page_id"],
                        "pair_id": pair_id,
                        "error": f"未知圖字類別：{prefix.label}",
                    }
                )
                continue
            tasks.append(
                {
                    "page_id": record["page_id"],
                    "split": record["split"],
                    "pair_id": pair_id,
                    "image_path": str(annotation_root / record["image_path"]),
                    "prefix_class": prefix.label,
                    "correction_degrees": correction_degrees,
                    "expected": expected,
                    "identifier_box": _annotation_box(identifier),
                }
            )
    return tasks, errors, positive_pages


def _required_count_errors(
    *,
    reviewed_pages,
    rotation_counts,
    require_reviewed_pages,
    require_normal_pairs,
    require_rotate_right_pairs,
):
    errors = []
    requirements = (
        ("reviewed_pages", require_reviewed_pages, reviewed_pages),
        ("normal_pairs", require_normal_pairs, rotation_counts[0]),
        (
            "rotate_right_pairs",
            require_rotate_right_pairs,
            rotation_counts[90],
        ),
    )
    for name, required, actual in requirements:
        if required is not None and int(required) != int(actual):
            errors.append(
                {
                    "error": (
                        f"{name} 數量不符：預期 {int(required)}，實際 {int(actual)}"
                    )
                }
            )
    return errors


def evaluate(
    annotation_root,
    output_path,
    *,
    minimum_confidence=0.70,
    require_reviewed_pages=None,
    require_normal_pairs=None,
    require_rotate_right_pairs=None,
    progress_every=25,
):
    annotation_root = Path(annotation_root).resolve()
    output_path = Path(output_path).resolve()
    records = [
        read_page_record(path)
        for path in find_page_records(annotation_root)
    ]
    reviewed_records = [
        record for record in records if record.get("reviewed") is True
    ]
    tasks, validation_errors, positive_pages = _reviewed_pair_tasks(
        annotation_root,
        reviewed_records,
    )
    rotation_counts = {
        degrees: sum(
            int(task["correction_degrees"] == degrees) for task in tasks
        )
        for degrees in (0, 90)
    }
    validation_errors.extend(
        _required_count_errors(
            reviewed_pages=len(reviewed_records),
            rotation_counts=rotation_counts,
            require_reviewed_pages=require_reviewed_pages,
            require_normal_pairs=require_normal_pairs,
            require_rotate_right_pairs=require_rotate_right_pairs,
        )
    )
    if validation_errors:
        report = {
            "version": 1,
            "status": "INVALID_INPUT",
            "annotation_root": str(annotation_root),
            "reviewed_pages": len(reviewed_records),
            "reviewed_positive_pages": positive_pages,
            "pair_counts_by_correction": {
                str(key): value for key, value in rotation_counts.items()
            },
            "validation_error_count": len(validation_errors),
            "validation_errors": validation_errors,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return report

    reader = create_easyocr_reader(False)
    items = []
    current_image_path = None
    current_image = None
    for index, task in enumerate(tasks, start=1):
        image_path = task["image_path"]
        if image_path != current_image_path:
            current_image = read_image(image_path)
            current_image_path = image_path
        if current_image is None:
            validation_errors.append(
                {
                    "page_id": task["page_id"],
                    "pair_id": task["pair_id"],
                    "error": f"圖片讀取失敗：{image_path}",
                }
            )
            continue
        crop = _identifier_crop(
            current_image,
            task["identifier_box"],
            task["correction_degrees"],
        )
        if crop is None:
            validation_errors.append(
                {
                    "page_id": task["page_id"],
                    "pair_id": task["pair_id"],
                    "error": "圖號裁切為空",
                }
            )
            continue
        value, raw_text, confidence = _recognize_identifier(reader, crop)
        actual = None if value is None else str(value)
        expected = task["expected"]
        exact = actual is not None and actual.upper() == expected.upper()
        contains_six_or_nine = "6" in expected or "9" in expected
        counterfactual_predictions = []
        if not exact:
            for alternate_degrees in (0, 90, 180, 270):
                if alternate_degrees == task["correction_degrees"]:
                    continue
                alternate_crop = _identifier_crop(
                    current_image,
                    task["identifier_box"],
                    alternate_degrees,
                )
                alternate_value, alternate_raw, alternate_confidence = (
                    _recognize_identifier(reader, alternate_crop)
                )
                alternate_actual = (
                    None
                    if alternate_value is None
                    else str(alternate_value)
                )
                counterfactual_predictions.append(
                    {
                        "correction_degrees": alternate_degrees,
                        "actual": alternate_actual,
                        "raw_text": alternate_raw,
                        "confidence": round(
                            float(alternate_confidence),
                            6,
                        ),
                        "exact": (
                            alternate_actual is not None
                            and alternate_actual.upper() == expected.upper()
                        ),
                    }
                )
        likely_rotation_label_mismatch = any(
            alternate["exact"]
            and float(alternate["confidence"]) >= float(minimum_confidence)
            for alternate in counterfactual_predictions
        )
        item = {
            **task,
            "actual": actual,
            "raw_text": raw_text,
            "confidence": round(float(confidence), 6),
            "exact": exact,
            "confident": float(confidence) >= float(minimum_confidence),
            "exact_and_confident": (
                exact and float(confidence) >= float(minimum_confidence)
            ),
            "contains_6_or_9": contains_six_or_nine,
            "six_nine_swap": _is_six_nine_swap(expected, actual),
            "likely_rotation_label_mismatch": (
                likely_rotation_label_mismatch
            ),
            "counterfactual_predictions": counterfactual_predictions,
        }
        items.append(item)
        if progress_every and (
            index % int(progress_every) == 0 or index == len(tasks)
        ):
            print(f"[{index}/{len(tasks)}] reviewed crop OCR", flush=True)

    by_rotation = {
        str(degrees): _summary(
            [
                item
                for item in items
                if item["correction_degrees"] == degrees
            ],
            minimum_confidence,
        )
        for degrees in (0, 90)
    }
    six_nine_by_rotation = {
        str(degrees): _summary(
            [
                item
                for item in items
                if item["correction_degrees"] == degrees
                and item["contains_6_or_9"]
            ],
            minimum_confidence,
        )
        for degrees in (0, 90)
    }
    mismatches = [
        item for item in items if not item["exact_and_confident"]
    ]
    six_nine_items = [
        item for item in items if item["contains_6_or_9"]
    ]
    six_nine_mismatches = [
        item for item in six_nine_items if not item["exact_and_confident"]
    ]
    rotation_label_diagnostics = [
        item for item in items if item["likely_rotation_label_mismatch"]
    ]
    report = {
        "version": 1,
        "status": "PASS" if not validation_errors else "INVALID_INPUT",
        "purpose": "reviewed_ground_truth_crop_ocr_baseline",
        "annotation_root": str(annotation_root),
        "ocr_device": "cpu",
        "ocr_pipeline": "_identifier_crop + _recognize_identifier",
        "truth_semantics": (
            "figure_prefix_rotate_right text is the correct identifier after "
            "a clockwise 90-degree crop correction"
        ),
        "minimum_required_confidence": float(minimum_confidence),
        "reviewed_pages": len(reviewed_records),
        "reviewed_positive_pages": positive_pages,
        "reviewed_negative_pages": len(reviewed_records) - positive_pages,
        "overall": _summary(items, minimum_confidence),
        "by_correction_degrees": by_rotation,
        "six_nine": {
            "overall": _summary(six_nine_items, minimum_confidence),
            "by_correction_degrees": six_nine_by_rotation,
            "swap_count": sum(item["six_nine_swap"] for item in items),
            "mismatch_count": len(six_nine_mismatches),
            "mismatches": six_nine_mismatches,
        },
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "likely_rotation_label_mismatch_count": len(
            rotation_label_diagnostics
        ),
        "likely_rotation_label_mismatches": rotation_label_diagnostics,
        "validation_error_count": len(validation_errors),
        "validation_errors": validation_errors,
        "items": items,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main():
    args = parse_args()
    report = evaluate(
        args.annotations,
        args.output,
        minimum_confidence=args.minimum_confidence,
        require_reviewed_pages=args.require_reviewed_pages,
        require_normal_pairs=args.require_normal_pairs,
        require_rotate_right_pairs=args.require_rotate_right_pairs,
        progress_every=args.progress_every,
    )
    summary = {
        key: report.get(key)
        for key in (
            "status",
            "reviewed_pages",
            "reviewed_positive_pages",
            "reviewed_negative_pages",
            "overall",
            "by_correction_degrees",
            "six_nine",
            "mismatch_count",
            "likely_rotation_label_mismatch_count",
            "validation_error_count",
            "validation_errors",
        )
        if key in report
    }
    if "six_nine" in summary:
        summary["six_nine"] = {
            key: summary["six_nine"][key]
            for key in (
                "overall",
                "by_correction_degrees",
                "swap_count",
                "mismatch_count",
            )
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
