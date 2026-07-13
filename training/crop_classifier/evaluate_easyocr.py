"""Measure EasyOCR digit accuracy on human-approved real patent crops."""

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import cv2

from common import read_manifest
from prepare_crops import preprocess_for_ocr, rotate_image


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_MANIFEST = SCRIPT_DIR / "review_dataset" / "manifest.csv"
DEFAULT_REPORT = SCRIPT_DIR / "review_dataset" / "easyocr_evaluation.json"
DEFAULT_DETAILS = SCRIPT_DIR / "review_dataset" / "easyocr_evaluation.csv"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from features.patent_ocr.easyocr_loader import create_easyocr_reader  # noqa: E402
from features.patent_ocr.ocr_engine import (  # noqa: E402
    _best_easyocr_character,
    recognize_easyocr_character,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate EasyOCR on approved crops.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    return parser.parse_args()


def metric_summary(rows, prediction_key):
    total = len(rows)
    recognized = sum(bool(row[prediction_key]) for row in rows)
    correct = sum(row[prediction_key] == row["ground_truth"] for row in rows)
    return {
        "total": total,
        "recognized": recognized,
        "correct": correct,
        "coverage": recognized / total if total else 0.0,
        "accuracy": correct / total if total else 0.0,
        "recognized_accuracy": correct / recognized if recognized else 0.0,
    }


def main():
    args = parse_args()
    manifest_path = args.manifest.resolve()
    dataset_root = manifest_path.parent
    rows = [
        row
        for row in read_manifest(manifest_path)
        if row.get("split") == "val"
        and row.get("status") == "approved"
        and row.get("label") in "0123456789"
    ]
    reader = create_easyocr_reader(False)
    details = []

    for index, row in enumerate(rows, start=1):
        crop = cv2.imread(str(dataset_root / row["crop_path"]))

        if crop is None:
            raise ValueError(f"Cannot read crop: {row['crop_path']}")

        crop = rotate_image(crop, int(row.get("rotation") or 0))
        processed = preprocess_for_ocr(crop)
        raw_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        raw_gray = cv2.resize(
            raw_gray,
            None,
            fx=4,
            fy=4,
            interpolation=cv2.INTER_CUBIC,
        )
        raw_with_border = cv2.copyMakeBorder(
            raw_gray,
            30,
            30,
            30,
            30,
            cv2.BORDER_CONSTANT,
            value=255,
        )
        old_results = reader.readtext(
            processed,
            allowlist="0123456789",
            paragraph=False,
        )
        old_text, old_confidence = _best_easyocr_character(old_results)
        new_text, new_confidence = recognize_easyocr_character(
            reader,
            processed,
            min_confidence=0.20,
        )
        raw_text, raw_confidence = recognize_easyocr_character(
            reader,
            raw_with_border,
            min_confidence=0.20,
        )
        details.append(
            {
                "id": row["id"],
                "ground_truth": row["label"],
                "old_prediction": old_text,
                "old_confidence": f"{old_confidence:.6f}",
                "new_prediction": new_text,
                "new_confidence": f"{new_confidence:.6f}",
                "raw_border_prediction": raw_text,
                "raw_border_confidence": f"{raw_confidence:.6f}",
            }
        )

        if index % 100 == 0:
            print(f"Evaluated {index}/{len(rows)} crops...", flush=True)

    report = {
        "approved_digit_crops": len(details),
        "readtext_baseline": metric_summary(details, "old_prediction"),
        "full_roi_with_fallback": metric_summary(details, "new_prediction"),
        "raw_border_full_roi": metric_summary(details, "raw_border_prediction"),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with args.details.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=details[0].keys())
        writer.writeheader()
        writer.writerows(details)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
