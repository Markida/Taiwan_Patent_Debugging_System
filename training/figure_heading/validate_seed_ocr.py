"""Validate text-layer figure-identifier crops with the production reader."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_ocr.easyocr_loader import create_easyocr_reader
from features.patent_ocr.figure_heading import (
    _identifier_crop,
    _recognize_identifier,
    pair_figure_heading_boxes,
)
from features.patent_ocr.figure_heading_classes import FIGURE_PREFIX_CLASS_NAMES
from features.patent_ocr.figure_identifiers import normalize_figure_identifier
from features.patent_ocr.image_io import read_image
from training.manual_annotation.common import (
    Annotation,
    find_page_records,
    read_page_record,
)


DEFAULT_ANNOTATIONS = (
    PROJECT_ROOT.parent
    / "AI訓練圖集"
    / "prepared_dataset_v1"
    / "figure_heading_annotation_v2"
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--minimum-confidence", type=float, default=0.70)
    return parser.parse_args()


def validate(annotation_root, minimum_confidence=0.70):
    annotation_root = Path(annotation_root).resolve()
    records = [read_page_record(path) for path in find_page_records(annotation_root)]

    reader = create_easyocr_reader(False)
    confidences = []
    mismatches = []
    validation_errors = []
    evaluated_pairs = 0
    for record in records:
        annotations = [
            Annotation(**raw).normalized(record["width"], record["height"])
            for raw in record.get("annotations", [])
        ]
        prefix_annotations = [
            item for item in annotations if item.label in FIGURE_PREFIX_CLASS_NAMES
        ]
        identifier_annotations = [
            item for item in annotations if item.label == "figure_identifier"
        ]
        if not prefix_annotations and not identifier_annotations:
            continue

        def as_box(annotation):
            return {
                "x1": annotation.x1,
                "y1": annotation.y1,
                "x2": annotation.x2,
                "y2": annotation.y2,
                "confidence": 1.0,
                "class_name": annotation.label,
            }

        pairs = pair_figure_heading_boxes(
            [as_box(item) for item in prefix_annotations],
            [as_box(item) for item in identifier_annotations],
        )
        if len(pairs) != len(prefix_annotations) or len(pairs) != len(
            identifier_annotations
        ):
            validation_errors.append(
                {
                    "page_id": record["page_id"],
                    "error": "圖與圖號框無法完整配對",
                }
            )
            continue

        image = read_image(annotation_root / record["image_path"])
        if image is None:
            validation_errors.append(
                {"page_id": record["page_id"], "error": "圖片讀取失敗"}
            )
            continue
        for pair in pairs:
            identifier = identifier_annotations[pair["identifier_index"]]
            try:
                expected = str(normalize_figure_identifier(identifier.text))
            except ValueError as error:
                validation_errors.append(
                    {"page_id": record["page_id"], "error": str(error)}
                )
                continue
            crop = _identifier_crop(
                image,
                pair["identifier_box"],
                int(pair["correction_degrees"]),
            )
            value, raw_text, confidence = _recognize_identifier(reader, crop)
            evaluated_pairs += 1
            confidences.append(float(confidence))
            actual = "" if value is None else str(value)
            if actual.upper() != expected.upper() or confidence < float(
                minimum_confidence
            ):
                mismatches.append(
                    {
                        "page_id": record["page_id"],
                        "expected": expected,
                        "actual": None if value is None else str(value),
                        "raw_text": raw_text,
                        "confidence": round(float(confidence), 6),
                    }
                )

    sorted_confidences = sorted(confidences)
    report = {
        "version": 1,
        "source": "current_annotation_records",
        "pair_count": evaluated_pairs,
        "minimum_required_confidence": float(minimum_confidence),
        "exact_and_confident_count": evaluated_pairs - len(mismatches),
        "exact_and_confident_rate": (
            round((evaluated_pairs - len(mismatches)) / evaluated_pairs, 6)
            if evaluated_pairs
            else 0.0
        ),
        "confidence_minimum": (
            round(sorted_confidences[0], 6) if sorted_confidences else 0.0
        ),
        "confidence_median": (
            round(sorted_confidences[len(sorted_confidences) // 2], 6)
            if sorted_confidences
            else 0.0
        ),
        "confidence_maximum": (
            round(sorted_confidences[-1], 6) if sorted_confidences else 0.0
        ),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "validation_error_count": len(validation_errors),
        "validation_errors": validation_errors,
    }
    report_path = (
        annotation_root / "manifests" / "seed_ocr_validation.json"
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main():
    args = parse_args()
    report = validate(
        args.annotations,
        minimum_confidence=args.minimum_confidence,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(
        0
        if not report["mismatch_count"] and not report["validation_error_count"]
        else 1
    )


if __name__ == "__main__":
    main()
