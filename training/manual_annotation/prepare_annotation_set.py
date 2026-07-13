"""Seed full-page manual annotations from the existing reviewed real boxes."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from PIL import Image

try:
    from .common import (
        Annotation,
        link_or_copy,
        make_page_record,
        normalize_label,
        write_page_record,
    )
except ImportError:
    from common import (
        Annotation,
        link_or_copy,
        make_page_record,
        normalize_label,
        write_page_record,
    )


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
DEFAULT_SOURCE = WORKSPACE_ROOT / "dataset"
DEFAULT_REVIEW_MANIFEST = (
    PROJECT_ROOT
    / "training"
    / "crop_classifier"
    / "review_dataset"
    / "manifest.csv"
)
DEFAULT_OUTPUT = SCRIPT_DIR / "annotation_set"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Prepare real patent pages for exhaustive character annotation."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--review-manifest", type=Path, default=DEFAULT_REVIEW_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--suggestion-confidence", type=float, default=0.995)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recreate page JSON files and discard full-page review progress.",
    )
    return parser.parse_args()


def read_review_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def use_seed_row(row, suggestion_confidence):
    label = normalize_label(row.get("label"))
    status = row.get("status")

    if not label or status == "skipped":
        return False
    if status == "approved":
        return True
    if status == "suggested":
        return float(row.get("confidence") or 0.0) >= suggestion_confidence
    return False


def main():
    args = parse_args()
    source_root = args.source.resolve()
    review_path = args.review_manifest.resolve()
    output_root = args.output.resolve()

    if not source_root.exists():
        raise FileNotFoundError(source_root)
    if not review_path.exists():
        raise FileNotFoundError(review_path)

    rows = read_review_rows(review_path)
    rows_by_key = {}
    for row in rows:
        key = (row["split"], Path(row["source_image"]).stem, int(row["box_index"]))
        rows_by_key[key] = row

    stats = Counter()
    seeded_classes = Counter()

    for split in ("train", "val"):
        source_images = source_root / "images" / split
        source_labels = source_root / "labels" / split

        for image_path in sorted(source_images.glob("*.png")):
            page_id = f"{split}_{image_path.stem}"
            image_relative = Path("images") / split / image_path.name
            label_relative = Path("labels") / split / f"{image_path.stem}.json"
            output_image = output_root / image_relative
            output_label = output_root / label_relative

            link_or_copy(image_path, output_image)

            if output_label.exists() and not args.force:
                stats["preserved_pages"] += 1
                continue

            with Image.open(image_path) as image:
                image_width, image_height = image.size

            yolo_lines = [
                line
                for line in (source_labels / f"{image_path.stem}.txt")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            annotations = []

            for box_index, line in enumerate(yolo_lines, start=1):
                key = (split, image_path.stem, box_index)
                row = rows_by_key.get(key)

                if row is None:
                    raise KeyError(f"Review manifest missing box: {key}")
                if not use_seed_row(row, args.suggestion_confidence):
                    stats["omitted_seed_boxes"] += 1
                    continue

                label = normalize_label(row["label"])
                annotation = Annotation.from_yolo(
                    line,
                    image_width=image_width,
                    image_height=image_height,
                    label=label,
                    source=f"seed_{row['status']}",
                )
                annotations.append(annotation)
                seeded_classes[label] += 1
                stats["seed_boxes"] += 1

            record = make_page_record(
                page_id=page_id,
                split=split,
                image_path=image_relative,
                source_path=image_path,
                image_width=image_width,
                image_height=image_height,
                annotations=annotations,
                reviewed=False,
            )
            write_page_record(output_label, record)
            stats[f"{split}_pages"] += 1
            stats["created_pages"] += 1

    report = {
        "source": str(source_root),
        "review_manifest": str(review_path),
        "suggestion_confidence": args.suggestion_confidence,
        "stats": dict(stats),
        "seeded_class_counts": dict(sorted(seeded_classes.items())),
        "important": (
            "Seed boxes are only a starting point. Every page remains unreviewed so "
            "missed I/V/letters/prime boxes can be added and false boxes removed."
        ),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "prepare_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
