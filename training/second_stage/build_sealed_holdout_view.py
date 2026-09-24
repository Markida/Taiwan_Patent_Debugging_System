"""Create a first-stage-evaluator-compatible view of the sealed v2 holdout."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from training.manual_annotation.common import link_or_copy


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_GOLD = PROJECT_ROOT / "training" / "gold_validation_v1"
DEFAULT_SPLIT = SCRIPT_DIR / "supervised_locator_v2_dataset" / "split_manifest.json"
DEFAULT_OUTPUT = SCRIPT_DIR / "sealed_holdout_gold_v2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    gold = args.gold.resolve()
    split_path = args.split_manifest.resolve()
    output = args.output.resolve()
    split = json.loads(split_path.read_text(encoding="utf-8"))
    holdout_ids = set(split["holdout_page_ids"])
    if output.exists():
        if not args.force:
            raise FileExistsError(f"Output already exists: {output}")
        shutil.rmtree(output)
    (output / "images" / "val").mkdir(parents=True, exist_ok=True)
    (output / "labels" / "val").mkdir(parents=True, exist_ok=True)

    original_manifest = json.loads(
        (gold / "freeze_manifest.json").read_text(encoding="utf-8")
    )
    selected_manifest = [
        page for page in original_manifest["pages"] if page["page_id"] in holdout_ids
    ]
    if len(selected_manifest) != len(holdout_ids):
        raise RuntimeError("Holdout page list does not match the frozen manifest.")
    for page in selected_manifest:
        page_id = page["page_id"]
        source_image = gold / "images" / "val" / f"{page_id}.png"
        source_label = gold / "labels" / "val" / f"{page_id}.json"
        link_or_copy(source_image, output / "images" / "val" / source_image.name)
        shutil.copy2(source_label, output / "labels" / "val" / source_label.name)

    publication_values = sorted(
        {page["publication_number"] for page in selected_manifest}
    )
    manifest = {
        **original_manifest,
        "purpose": "sealed patent-grouped holdout for locator v1/v2 comparison",
        "page_count": len(selected_manifest),
        "publication_count": len(publication_values),
        "publications": publication_values,
        "pages": selected_manifest,
        "sealed_holdout": True,
        "split_manifest": str(split_path),
        "training_prohibited": True,
    }
    (output / "freeze_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "pages": len(selected_manifest),
                "publications": manifest["publication_count"],
                "training_prohibited": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
