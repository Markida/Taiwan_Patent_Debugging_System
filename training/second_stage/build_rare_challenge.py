"""Extract rare-label crops from the sealed locator-v2 holdout pages."""

from __future__ import annotations

import argparse
import json
import shutil
import string
import sys
from collections import Counter
from pathlib import Path

from PIL import Image


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.manual_annotation.build_group_locator_dataset import (
    group_character_annotations,
)
from training.manual_annotation.common import Annotation


DEFAULT_GOLD = PROJECT_ROOT / "training" / "gold_validation_v1"
DEFAULT_SPLIT_MANIFEST = SCRIPT_DIR / "supervised_locator_v2_dataset" / "split_manifest.json"
DEFAULT_OUTPUT = SCRIPT_DIR / "rare_character_holdout_v2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pad", type=int, default=12)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def categories(text: str) -> list[str]:
    values = []
    if any(character in string.ascii_uppercase for character in text):
        values.append("uppercase")
    if any(character in string.ascii_lowercase for character in text):
        values.append("lowercase")
    if "'" in text:
        values.append("prime")
    if len(text) > 1 and set(text) <= set("IVX"):
        values.append("roman_multi_character")
    return values


def main() -> int:
    args = parse_args()
    gold = args.gold.resolve()
    split_manifest = args.split_manifest.resolve()
    output = args.output.resolve()
    qa = json.loads((gold / "qa_report.json").read_text(encoding="utf-8"))
    if not qa.get("ready_for_threshold_sweep"):
        raise RuntimeError("Gold validation is incomplete; challenge export is blocked.")
    split = json.loads(split_manifest.read_text(encoding="utf-8"))
    holdout_page_ids = set(split.get("holdout_page_ids", []))
    if not holdout_page_ids:
        raise RuntimeError("Sealed holdout page list is empty.")
    if output.exists():
        if not args.force:
            raise FileExistsError(f"Output already exists: {output}")
        shutil.rmtree(output)
    crops_dir = output / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    category_counts = Counter()
    text_counts = Counter()
    for record_path in sorted((gold / "labels" / "val").glob("*.json")):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record["page_id"] not in holdout_page_ids:
            continue
        annotations = [
            Annotation(
                label=raw["label"],
                x1=raw["x1"],
                y1=raw["y1"],
                x2=raw["x2"],
                y2=raw["y2"],
                source=raw.get("source", "gold"),
            )
            for raw in record.get("annotations", [])
        ]
        groups = group_character_annotations(annotations)
        image_path = gold / record["image_path"]
        with Image.open(image_path) as source:
            source = source.convert("RGB")
            for group_index, group in enumerate(groups, start=1):
                group_categories = categories(group.text)
                if not group_categories:
                    continue
                x1 = max(0, int(group.x1) - args.pad)
                y1 = max(0, int(group.y1) - args.pad)
                x2 = min(source.width, int(group.x2 + 0.999) + args.pad)
                y2 = min(source.height, int(group.y2 + 0.999) + args.pad)
                crop_name = f"{record['page_id']}_g{group_index:03d}.png"
                source.crop((x1, y1, x2, y2)).save(
                    crops_dir / crop_name,
                    format="PNG",
                    optimize=True,
                )
                row = {
                    "crop": f"crops/{crop_name}",
                    "page_id": record["page_id"],
                    "source_image": record["image_path"],
                    "text": group.text,
                    "categories": group_categories,
                    "group_box": [group.x1, group.y1, group.x2, group.y2],
                    "crop_box": [x1, y1, x2, y2],
                }
                rows.append(row)
                text_counts[group.text] += 1
                category_counts.update(group_categories)

    report = {
        "format_version": 1,
        "evaluation_only": True,
        "training_prohibited": True,
        "source_gold": str(gold),
        "split_manifest": str(split_manifest),
        "holdout_pages": len(holdout_page_ids),
        "challenge_crops": len(rows),
        "category_counts": dict(sorted(category_counts.items())),
        "unique_texts": len(text_counts),
        "text_counts": dict(sorted(text_counts.items())),
        "coverage_warning": (
            "Only naturally occurring rare labels in the frozen gold set are present; "
            "classes absent from gold remain unmeasured."
        ),
        "items": rows,
    }
    (output / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in report.items() if key != "items"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
