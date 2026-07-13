"""Refresh unreviewed training suggestions with an alphanumeric allowlist."""

import argparse
from pathlib import Path

import cv2

from common import normalize_label, read_manifest, write_manifest
from prepare_crops import create_reader, preprocess_for_ocr, rotate_image


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR / "review_dataset" / "manifest.csv"
DEFAULT_ALLOWLIST = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'"


def parse_args():
    parser = argparse.ArgumentParser(description="Suggest alphanumeric crop labels.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--split", choices=("train", "val"), default="train")
    parser.add_argument("--allowlist", default=DEFAULT_ALLOWLIST)
    return parser.parse_args()


def recognize_candidate(reader, image, allowlist):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    raw = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    raw = cv2.copyMakeBorder(
        raw,
        30,
        30,
        30,
        30,
        cv2.BORDER_CONSTANT,
        value=255,
    )
    variants = (raw, preprocess_for_ocr(image))
    best_label = ""
    best_confidence = 0.0

    for variant in variants:
        height, width = variant.shape[:2]
        results = reader.recognize(
            variant,
            horizontal_list=[[0, width, 0, height]],
            free_list=[],
            allowlist=allowlist,
            detail=1,
            rotation_info=None,
            paragraph=False,
        )

        for result in results or []:
            if len(result) < 3:
                continue

            label = normalize_label(result[1])
            confidence = float(result[2])

            if label and confidence > best_confidence:
                best_label = label
                best_confidence = confidence

        if best_label and best_confidence >= 0.20:
            break

    return best_label, best_confidence


def main():
    args = parse_args()
    manifest_path = args.manifest.resolve()
    dataset_root = manifest_path.parent
    rows = read_manifest(manifest_path)
    reader = create_reader()
    updated = 0

    for row in rows:
        if row.get("split") != args.split:
            continue
        if row.get("status") in {"approved", "skipped"}:
            continue

        crop = cv2.imread(str(dataset_root / row["crop_path"]))

        if crop is None:
            raise ValueError(f"Cannot read crop: {row['crop_path']}")

        crop = rotate_image(crop, int(row.get("rotation") or 0))
        label, confidence = recognize_candidate(reader, crop, args.allowlist)
        row["label"] = label
        row["confidence"] = f"{confidence:.6f}"
        row["status"] = "suggested" if label else "pending"
        updated += 1

        if updated % 100 == 0:
            write_manifest(manifest_path, rows)
            print(f"Updated {updated} suggestions...", flush=True)

    write_manifest(manifest_path, rows)
    print(f"Updated suggestions: {updated}")


if __name__ == "__main__":
    main()
