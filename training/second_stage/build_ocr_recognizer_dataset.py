"""Build gold-only complete-label crops for patent OCR recognizer fine-tuning."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_ocr.image_io import read_image, write_image
from training.second_stage.build_supervised_locator_v2_dataset import (
    choose_holdout,
    load_pages,
    target_counts,
)


DEFAULT_GOLD = PROJECT_ROOT / "training" / "gold_validation_v1"
DEFAULT_SPLIT = SCRIPT_DIR / "supervised_locator_v2_dataset" / "split_manifest.json"
DEFAULT_OUTPUT = SCRIPT_DIR / "ocr_recognizer_v2_dataset"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--internal-val-ratio", type=float, default=0.15)
    parser.add_argument("--padding", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def summarize(entries: list[dict]) -> dict:
    texts = [entry["text"] for entry in entries]
    characters = Counter(character for text in texts for character in text)
    return {
        "crops": len(entries),
        "publications": len({entry["publication_number"] for entry in entries}),
        "pages": len({entry["page_id"] for entry in entries}),
        "categories": dict(target_counts(texts)),
        "character_counts": dict(sorted(characters.items())),
    }


def main() -> int:
    args = parse_args()
    gold = args.gold.resolve()
    split_path = args.split_manifest.resolve()
    output = args.output.resolve()
    if output.exists():
        if not args.force:
            raise FileExistsError(f"Output already exists: {output}")
        shutil.rmtree(output)

    split = json.loads(split_path.read_text(encoding="utf-8"))
    if not split.get("ready_for_training"):
        raise RuntimeError("Locator split is incomplete; OCR crop export is blocked.")
    locator_train_ids = {
        page["page_id"] for page in split["pages"] if page["split"] == "train"
    }
    sealed_holdout_ids = set(split["holdout_page_ids"])
    pages, _manifest = load_pages(gold)
    train_pages = [page for page in pages if page["page_id"] in locator_train_ids]
    holdout_pages = [page for page in pages if page["page_id"] in sealed_holdout_ids]
    if len(train_pages) + len(holdout_pages) != len(pages):
        raise RuntimeError("OCR split does not cover every frozen gold page exactly once.")

    internal_val_publications = choose_holdout(
        train_pages,
        args.internal_val_ratio,
        30000,
        args.seed,
    )
    page_splits = {
        "train": [
            page
            for page in train_pages
            if page["publication_number"] not in internal_val_publications
        ],
        "val": [
            page
            for page in train_pages
            if page["publication_number"] in internal_val_publications
        ],
        "holdout": holdout_pages,
    }
    publications_by_split = {
        name: {page["publication_number"] for page in rows}
        for name, rows in page_splits.items()
    }
    for first, second in (("train", "val"), ("train", "holdout"), ("val", "holdout")):
        overlap = publications_by_split[first] & publications_by_split[second]
        if overlap:
            raise RuntimeError(f"Publication leakage between {first}/{second}: {overlap}")

    entries = []
    for split_name, split_pages in page_splits.items():
        for page in split_pages:
            image = read_image(page["image_path"])
            if image is None:
                raise ValueError(f"Cannot read gold image: {page['image_path']}")
            image_height, image_width = image.shape[:2]
            for group_index, group in enumerate(page["groups"], start=1):
                padding = max(0, int(args.padding))
                x1 = max(0, int(group.x1) - padding)
                y1 = max(0, int(group.y1) - padding)
                x2 = min(image_width, int(group.x2 + 0.9999) + padding)
                y2 = min(image_height, int(group.y2 + 0.9999) + padding)
                crop = image[y1:y2, x1:x2]
                if crop.size == 0:
                    raise RuntimeError(f"Empty OCR crop: {page['page_id']} #{group_index}")
                crop_name = f"{page['page_id']}_g{group_index:03d}.png"
                relative_crop = Path("images") / split_name / crop_name
                if not write_image(output / relative_crop, crop):
                    raise RuntimeError(f"Cannot write OCR crop: {output / relative_crop}")
                entries.append(
                    {
                        "crop": relative_crop.as_posix(),
                        "text": group.text,
                        "split": split_name,
                        "page_id": page["page_id"],
                        "publication_number": page["publication_number"],
                        "group_index": group_index,
                        "source_box": [group.x1, group.y1, group.x2, group.y2],
                        "crop_box": [x1, y1, x2, y2],
                    }
                )

    entries_by_split = {
        name: [entry for entry in entries if entry["split"] == name]
        for name in page_splits
    }
    report = {
        "format_version": 1,
        "complete": True,
        "gold_only": True,
        "pseudo_labels_used": False,
        "additional_manual_review_required": False,
        "source_gold": str(gold),
        "source_split_manifest": str(split_path),
        "padding": int(args.padding),
        "seed": args.seed,
        "internal_validation_publications": sorted(internal_val_publications),
        "sealed_holdout_publications": split["holdout_publications"],
        "leakage_checks": {
            "publication_cross_split": False,
            "sealed_holdout_used_for_training": False,
        },
        "train": summarize(entries_by_split["train"]),
        "val": summarize(entries_by_split["val"]),
        "holdout": summarize(entries_by_split["holdout"]),
        "entries": entries,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "entries"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
