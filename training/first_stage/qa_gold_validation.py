"""Validate the frozen gold set without modifying its images or annotations."""

from __future__ import annotations

import argparse
import hashlib
import json
import string
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.manual_annotation.common import Annotation, normalize_label

DEFAULT_DATASET = PROJECT_ROOT / "training" / "gold_validation_v1"
CLASS_NAMES = tuple("0123456789") + tuple(string.ascii_uppercase) + ("prime",) + tuple(string.ascii_lowercase)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit frozen gold validation data.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--training-images",
        type=Path,
        action="append",
        default=None,
        help=(
            "Training image directory to audit. Repeat for multiple roots. "
            "When omitted, every training/**/images/train directory is scanned."
        ),
    )
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    dataset = args.dataset.resolve()
    freeze_path = dataset / "freeze_manifest.json"
    if not freeze_path.exists():
        raise FileNotFoundError(freeze_path)
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    frozen_pages = {page["page_id"]: page for page in freeze["pages"]}
    label_paths = sorted((dataset / "labels" / "val").glob("*.json"))
    images = sorted((dataset / "images" / "val").glob("*"))
    image_by_stem = {path.stem: path for path in images}

    invalid = []
    missing = []
    reviewed = 0
    class_counts = Counter()
    frozen_hashes = set()
    for page_id, page in frozen_pages.items():
        image_path = image_by_stem.get(page_id)
        label_path = dataset / "labels" / "val" / f"{page_id}.json"
        if image_path is None or not label_path.exists():
            missing.append(page_id)
            continue
        actual_hash = sha256(image_path)
        frozen_hashes.add(actual_hash)
        if actual_hash != page["file_sha256"]:
            invalid.append({"page_id": page_id, "reason": "image_hash_changed"})
        record = json.loads(label_path.read_text(encoding="utf-8"))
        if record.get("split") != "val":
            invalid.append({"page_id": page_id, "reason": "split_is_not_val"})
        if not record.get("gold_validation", {}).get("evaluation_only"):
            invalid.append({"page_id": page_id, "reason": "evaluation_only_flag_missing"})
        reviewed += int(bool(record.get("reviewed")))
        for index, raw in enumerate(record.get("annotations", []), start=1):
            allowed = {key: raw.get(key) for key in ("label", "x1", "y1", "x2", "y2", "source")}
            annotation = Annotation(**allowed).normalized(record["width"], record["height"])
            label = normalize_label(annotation.label)
            if not label or not annotation.is_valid(record["width"], record["height"]):
                invalid.append(
                    {"page_id": page_id, "annotation": index, "reason": "invalid_box_or_label"}
                )
                continue
            class_counts[label] += 1

    if args.training_images:
        training_roots = [path.resolve() for path in args.training_images]
    else:
        training_roots = sorted(
            path.resolve()
            for path in (PROJECT_ROOT / "training").glob("**/images/train")
            if path.is_dir() and dataset not in path.resolve().parents
        )
    training_overlaps = []
    training_files_checked = 0
    seen_training_paths = set()
    for root in training_roots:
        if not root.exists():
            continue
        for path in root.glob("*"):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen_training_paths:
                continue
            seen_training_paths.add(resolved)
            training_files_checked += 1
            digest = sha256(path)
            if digest in frozen_hashes:
                training_overlaps.append(str(resolved))

    zero_classes = [name for name in CLASS_NAMES if class_counts[name] == 0]
    below_3 = {name: class_counts[name] for name in CLASS_NAMES if 0 < class_counts[name] < 3}
    below_20 = {name: class_counts[name] for name in CLASS_NAMES if 0 < class_counts[name] < 20}
    complete = (
        len(frozen_pages) == len(images) == len(label_paths)
        and reviewed == len(frozen_pages)
        and not missing
        and not invalid
        and not training_overlaps
    )
    report = {
        "format_version": 1,
        "evaluation_only": True,
        "complete": complete,
        "ready_for_threshold_sweep": complete,
        "frozen_pages": len(frozen_pages),
        "publications": int(freeze["publication_count"]),
        "image_files": len(images),
        "annotation_records": len(label_paths),
        "reviewed_pages": reviewed,
        "remaining_pages": len(frozen_pages) - reviewed,
        "annotation_count": sum(class_counts.values()),
        "class_counts": dict(sorted(class_counts.items())),
        "classes_with_no_validation_examples": zero_classes,
        "classes_with_1_or_2_examples": below_3,
        "classes_below_20_examples": below_20,
        "missing_pages": missing,
        "invalid_records": invalid,
        "training_image_roots_checked": [str(path) for path in training_roots],
        "training_files_checked": training_files_checked,
        "exact_training_image_overlaps": training_overlaps,
        "coverage_note": (
            "Thresholds are reliable only for classes represented in this gold set. "
            "Rare-class challenge crops should supplement naturally absent classes."
        ),
    }
    output = (args.output or dataset / "qa_report.json").resolve()
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.require_complete and not complete:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
