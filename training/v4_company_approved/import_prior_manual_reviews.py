"""Merge only the previously reviewed v3 real crops into the v4 train split.

This deliberately reads the v3 manual-review records directly.  It never reads
or copies the old v3 pseudo-labelled dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

try:
    from training.v3.common import (
        CLASS_TO_ID,
        DEFAULT_CONFIG,
        link_or_copy,
        load_config,
        read_image,
    )
except ImportError:
    from ..v3.common import (
        CLASS_TO_ID,
        DEFAULT_CONFIG,
        link_or_copy,
        load_config,
        read_image,
    )

from training.manual_annotation.common import (
    Annotation,
    find_page_records,
    read_page_record,
)


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
V4_CONFIG = SCRIPT_DIR / "config.yaml"
DEFAULT_SOURCE = PROJECT_ROOT / "training" / "v3" / "manual_review"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Import the 400 reviewed v3 real crops into the v4 train split."
    )
    parser.add_argument("--config", type=Path, default=V4_CONFIG)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--expected-reviewed", type=int, default=400)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_paths(folder: Path):
    for suffix in ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.tif", "*.tiff"):
        yield from folder.glob(suffix)


def main():
    args = parse_args()
    config = load_config(args.config)
    source = args.source.resolve()
    output = (args.dataset or config["paths"]["output_dataset"]).resolve()
    build_report_path = output / "build_report.json"

    if not build_report_path.exists():
        raise FileNotFoundError(f"v4 build report is missing: {build_report_path}")
    if not source.exists():
        raise FileNotFoundError(f"Previous manual review is missing: {source}")

    records = [read_page_record(path) for path in find_page_records(source)]
    reviewed_records = [record for record in records if record.get("reviewed")]
    if len(reviewed_records) != args.expected_reviewed:
        raise RuntimeError(
            f"Expected {args.expected_reviewed} reviewed records, "
            f"but found {len(reviewed_records)}."
        )

    train_images = output / "images" / "train"
    train_labels = output / "labels" / "train"
    train_images.mkdir(parents=True, exist_ok=True)
    train_labels.mkdir(parents=True, exist_ok=True)

    # Ignore our own prefix so rerunning the importer stays idempotent.
    existing_hashes = {
        sha256(path): path.name
        for path in image_paths(train_images)
        if not path.stem.startswith("prior_manual_")
    }

    imported = empty = duplicate_skipped = 0
    class_counts = Counter()
    duplicate_records = []
    source_methods = Counter()

    for record in reviewed_records:
        image_path = source / record["image_path"]
        image = read_image(image_path)
        if image is None:
            raise RuntimeError(f"Cannot read reviewed crop: {image_path}")
        height, width = image.shape[:2]
        if int(record.get("width", width)) != width or int(record.get("height", height)) != height:
            raise RuntimeError(f"Recorded dimensions do not match image: {image_path}")

        annotations = []
        for raw in record.get("annotations", []):
            item = Annotation(**raw).normalized(width, height)
            if not item.is_valid(width, height):
                raise RuntimeError(
                    f"Invalid reviewed annotation in {record['page_id']}: {raw}"
                )
            if item.label not in CLASS_TO_ID:
                raise RuntimeError(
                    f"Unknown reviewed class {item.label!r} in {record['page_id']}"
                )
            annotations.append(item)

        stem = f"prior_manual_{record['page_id']}"
        target_image = train_images / f"{stem}.png"
        target_label = train_labels / f"{stem}.txt"
        digest = sha256(image_path)

        if not target_image.exists() and digest in existing_hashes:
            duplicate_skipped += 1
            duplicate_records.append(
                {
                    "page_id": record["page_id"],
                    "duplicate_of": existing_hashes[digest],
                    "sha256": digest,
                }
            )
            continue

        source_methods[link_or_copy(image_path, target_image)] += 1
        lines = [
            item.to_yolo(width, height, class_id=CLASS_TO_ID[item.label])
            for item in annotations
        ]
        target_label.write_text(
            "\n".join(lines) + ("\n" if lines else ""),
            encoding="utf-8",
        )
        existing_hashes[digest] = target_image.name
        imported += 1
        empty += int(not annotations)
        class_counts.update(item.label for item in annotations)

    report = {
        "policy": (
            "v3 reviewed real crops only; no v3 pseudo-labelled dataset was read or copied"
        ),
        "source": str(source),
        "records_found": len(records),
        "reviewed_verified": len(reviewed_records),
        "imported": imported,
        "empty_hard_negatives": empty,
        "exact_duplicate_images_skipped": duplicate_skipped,
        "file_methods": dict(source_methods),
        "class_counts": dict(sorted(class_counts.items())),
        "duplicates": duplicate_records,
    }
    report_path = output / "prior_manual_import.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    build = json.loads(build_report_path.read_text(encoding="utf-8"))
    build["prior_manual_review"] = report
    build["image_counts"] = {
        split: len([path for path in image_paths(output / "images" / split) if path.is_file()])
        for split in ("train", "val")
    }
    build_report_path.write_text(
        json.dumps(build, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
