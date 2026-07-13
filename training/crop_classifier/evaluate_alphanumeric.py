"""Evaluate EasyOCR alphanumeric recognition on all approved real crops."""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import cv2

from common import read_manifest
from prepare_crops import create_reader, rotate_image
from suggest_alphanumeric import DEFAULT_ALLOWLIST, recognize_candidate


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR / "review_dataset" / "manifest.csv"
DEFAULT_REPORT = SCRIPT_DIR / "review_dataset" / "alphanumeric_evaluation.json"
DEFAULT_DETAILS = SCRIPT_DIR / "review_dataset" / "alphanumeric_evaluation.csv"


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate approved alphanumeric crops.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    return parser.parse_args()


def summarize(rows):
    total = len(rows)
    recognized = sum(bool(row["prediction"]) for row in rows)
    correct = sum(row["prediction"] == row["ground_truth"] for row in rows)
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
    approved = [
        row
        for row in read_manifest(manifest_path)
        if row.get("status") == "approved"
        and len(row.get("label", "")) == 1
        and row.get("label") in DEFAULT_ALLOWLIST
    ]
    reader = create_reader()
    details = []

    for index, row in enumerate(approved, start=1):
        crop = cv2.imread(str(dataset_root / row["crop_path"]))

        if crop is None:
            raise ValueError(f"Cannot read crop: {row['crop_path']}")

        crop = rotate_image(crop, int(row.get("rotation") or 0))
        prediction, confidence = recognize_candidate(
            reader,
            crop,
            DEFAULT_ALLOWLIST,
        )
        details.append(
            {
                "id": row["id"],
                "split": row["split"],
                "ground_truth": row["label"],
                "prediction": prediction,
                "confidence": f"{confidence:.6f}",
                "category": "digit" if row["label"].isdigit() else "letter",
            }
        )

        if index % 100 == 0:
            print(f"Evaluated {index}/{len(approved)} crops...", flush=True)

    report = {
        "overall": summarize(details),
        "digits": summarize([row for row in details if row["category"] == "digit"]),
        "letters": summarize([row for row in details if row["category"] == "letter"]),
        "ground_truth_counts": dict(Counter(row["ground_truth"] for row in details)),
    }
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
