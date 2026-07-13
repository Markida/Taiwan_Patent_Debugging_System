"""Build a cleaned one-class YOLO dataset from crop review decisions."""

import argparse
import json
import os
import shutil
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
DEFAULT_SOURCE = WORKSPACE_ROOT / "dataset"
DEFAULT_MANIFEST = (
    PROJECT_ROOT
    / "training"
    / "crop_classifier"
    / "review_dataset"
    / "manifest.csv"
)
DEFAULT_OUTPUT = SCRIPT_DIR / "clean_dataset"


def parse_args():
    parser = argparse.ArgumentParser(description="Remove reviewed false boxes.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--suggestion-confidence", type=float, default=0.995)
    return parser.parse_args()


def read_manifest(path):
    import csv

    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def link_or_copy(source, destination):
    if destination.exists():
        return

    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def should_keep(row, suggestion_confidence):
    status = row.get("status")

    if status == "skipped":
        return False
    if status == "approved":
        return True
    if status == "suggested":
        return float(row.get("confidence") or 0.0) >= suggestion_confidence

    return False


def main():
    args = parse_args()
    source_root = args.source.resolve()
    output_root = args.output.resolve()
    rows = read_manifest(args.manifest.resolve())
    rows_by_key = {}

    for row in rows:
        key = (row["split"], Path(row["source_image"]).stem, int(row["box_index"]))

        if key in rows_by_key:
            raise ValueError(f"Duplicate manifest key: {key}")

        rows_by_key[key] = row

    stats = Counter()

    for split in ("train", "val"):
        output_images = output_root / "images" / split
        output_labels = output_root / "labels" / split
        output_images.mkdir(parents=True, exist_ok=True)
        output_labels.mkdir(parents=True, exist_ok=True)

        for image_path in sorted((source_root / "images" / split).glob("*.png")):
            source_label = source_root / "labels" / split / f"{image_path.stem}.txt"
            label_lines = [
                line
                for line in source_label.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            kept_lines = []

            for box_index, line in enumerate(label_lines, start=1):
                key = (split, image_path.stem, box_index)

                if key not in rows_by_key:
                    raise KeyError(f"Manifest is missing source annotation: {key}")

                row = rows_by_key[key]

                if should_keep(row, args.suggestion_confidence):
                    kept_lines.append(line)
                    stats[f"{split}_kept"] += 1
                else:
                    stats[f"{split}_removed"] += 1

            link_or_copy(image_path, output_images / image_path.name)
            (output_labels / source_label.name).write_text(
                "\n".join(kept_lines) + ("\n" if kept_lines else ""),
                encoding="utf-8",
            )
            stats[f"{split}_images"] += 1

    data_yaml = "\n".join(
        [
            f"path: {output_root.as_posix()}",
            "train: images/train",
            "val: images/val",
            "",
            "names:",
            "  0: patent_character",
            "",
        ]
    )
    (output_root / "data.yaml").write_text(data_yaml, encoding="utf-8")
    report = {
        "source": str(source_root),
        "review_manifest": str(args.manifest.resolve()),
        "suggestion_confidence": args.suggestion_confidence,
        "stats": dict(stats),
    }
    (output_root / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
