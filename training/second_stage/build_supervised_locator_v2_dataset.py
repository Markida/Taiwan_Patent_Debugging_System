"""Build a patent-grouped supervised locator-v2 dataset from reviewed gold pages."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import string
import sys
from collections import Counter, defaultdict
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.manual_annotation.build_group_locator_dataset import (
    group_character_annotations,
)
from training.manual_annotation.common import Annotation, link_or_copy
from training.first_stage.sweep_group_locator import sha256


DEFAULT_GOLD = PROJECT_ROOT / "training" / "gold_validation_v1"
DEFAULT_OUTPUT = SCRIPT_DIR / "supervised_locator_v2_dataset"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--holdout-ratio", type=float, default=0.20)
    parser.add_argument("--search-iterations", type=int, default=50000)
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def target_counts(texts: list[str]) -> Counter:
    counts = Counter(groups=len(texts))
    for text in texts:
        if any(character in string.ascii_letters for character in text):
            counts["letters"] += 1
        if any(character in string.ascii_lowercase for character in text):
            counts["lowercase"] += 1
        if "'" in text:
            counts["prime"] += 1
        if len(text) > 1 and set(text) <= set("IVX"):
            counts["roman_multi"] += 1
    return counts


def load_pages(gold: Path) -> tuple[list[dict], dict]:
    manifest = json.loads((gold / "freeze_manifest.json").read_text(encoding="utf-8"))
    manifest_by_id = {page["page_id"]: page for page in manifest["pages"]}
    pages = []
    for record_path in sorted((gold / "labels" / "val").glob("*.json")):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if not record.get("reviewed"):
            raise RuntimeError(f"Gold page is not reviewed: {record['page_id']}")
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
        manifest_row = manifest_by_id[record["page_id"]]
        image_path = (gold / record["image_path"]).resolve()
        if sha256(image_path) != manifest_row["file_sha256"]:
            raise RuntimeError(f"Gold image hash changed: {record['page_id']}")
        pages.append(
            {
                "page_id": record["page_id"],
                "publication_number": manifest_row["publication_number"],
                "image_path": image_path,
                "image_sha256": manifest_row["file_sha256"],
                "width": record["width"],
                "height": record["height"],
                "groups": groups,
                "texts": [group.text for group in groups],
            }
        )
    return pages, manifest


def choose_holdout(pages: list[dict], ratio: float, iterations: int, seed: int) -> set[str]:
    by_publication = defaultdict(list)
    for page in pages:
        by_publication[page["publication_number"]].append(page)
    publications = sorted(by_publication)
    target_publications = max(1, round(len(publications) * ratio))
    target_pages = len(pages) * ratio
    overall = target_counts([text for page in pages for text in page["texts"]])
    keys = ("groups", "letters", "lowercase", "prime", "roman_multi")
    weights = {
        "groups": 1.0,
        "letters": 2.0,
        "lowercase": 5.0,
        "prime": 5.0,
        "roman_multi": 3.0,
    }
    minimums = {
        "letters": min(overall["letters"], max(20, round(overall["letters"] * 0.10))),
        "lowercase": min(overall["lowercase"], 5),
        "prime": min(overall["prime"], 5),
        "roman_multi": min(overall["roman_multi"], 3),
    }
    rng = random.Random(seed)
    best = None
    for _index in range(iterations):
        selected = set(rng.sample(publications, target_publications))
        selected_pages = [
            page for publication in selected for page in by_publication[publication]
        ]
        counts = target_counts([text for page in selected_pages for text in page["texts"]])
        if any(counts[key] < minimum for key, minimum in minimums.items()):
            continue
        score = abs(len(selected_pages) - target_pages) / max(1.0, target_pages) * 3.0
        for key in keys:
            desired = overall[key] * ratio
            score += weights[key] * abs(counts[key] - desired) / max(1.0, desired)
        tie = hashlib.sha256("|".join(sorted(selected)).encode("utf-8")).hexdigest()
        candidate = (score, tie, selected)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    if best is None:
        raise RuntimeError("Could not find a patent-grouped holdout with rare-label coverage.")
    return best[2]


def summarize(pages: list[dict]) -> dict:
    texts = [text for page in pages for text in page["texts"]]
    return {
        "pages": len(pages),
        "publications": len({page["publication_number"] for page in pages}),
        "character_boxes": sum(len(group.characters) for page in pages for group in page["groups"]),
        "complete_label_groups": len(texts),
        "categories": dict(target_counts(texts)),
    }


def main() -> int:
    args = parse_args()
    gold = args.gold.resolve()
    output = args.output.resolve()
    qa = json.loads((gold / "qa_report.json").read_text(encoding="utf-8"))
    if not qa.get("ready_for_threshold_sweep"):
        raise RuntimeError("Gold set is incomplete; supervised split is blocked.")
    if output.exists():
        if not args.force:
            raise FileExistsError(f"Output already exists: {output}")
        shutil.rmtree(output)

    pages, manifest = load_pages(gold)
    holdout_publications = choose_holdout(
        pages,
        args.holdout_ratio,
        args.search_iterations,
        args.seed,
    )
    split_pages = {
        "train": [
            page for page in pages if page["publication_number"] not in holdout_publications
        ],
        "val": [
            page for page in pages if page["publication_number"] in holdout_publications
        ],
    }
    if {
        page["publication_number"] for page in split_pages["train"]
    } & {
        page["publication_number"] for page in split_pages["val"]
    }:
        raise RuntimeError("A publication crossed train/holdout split.")

    all_hashes = set()
    page_rows = []
    for split, rows in split_pages.items():
        for page in rows:
            if page["image_sha256"] in all_hashes:
                raise RuntimeError(f"Duplicate gold image: {page['page_id']}")
            all_hashes.add(page["image_sha256"])
            destination = output / "images" / split / f"{page['page_id']}.png"
            label_path = output / "labels" / split / f"{page['page_id']}.txt"
            link_or_copy(page["image_path"], destination)
            label_path.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                group.to_annotation().to_yolo(page["width"], page["height"], class_id=0)
                for group in page["groups"]
            ]
            label_path.write_text(
                "\n".join(line for line in lines if line) + ("\n" if lines else ""),
                encoding="utf-8",
            )
            page_rows.append(
                {
                    "page_id": page["page_id"],
                    "publication_number": page["publication_number"],
                    "split": split,
                    "image_sha256": page["image_sha256"],
                    "complete_label_groups": len(page["groups"]),
                }
            )

    data_yaml = "\n".join(
        [
            f'path: "{output.as_posix()}"',
            "train: images/train",
            "val: images/val",
            "names:",
            "  0: patent_label",
            "",
        ]
    )
    (output / "data.yaml").write_text(data_yaml, encoding="utf-8")
    excluded_path = gold / "excluded_pages.json"
    excluded_pages = []
    if excluded_path.exists():
        excluded_payload = json.loads(excluded_path.read_text(encoding="utf-8"))
        excluded_pages = excluded_payload.get("excluded_pages", [])

    report = {
        "format_version": 1,
        "complete": True,
        "ready_for_training": True,
        "source_gold": str(gold),
        "source_manifest_sha256": sha256(gold / "freeze_manifest.json"),
        "supervision_policy": (
            "Reviewed gold annotations are used directly; no pseudo labels and no "
            "additional manual review are used."
        ),
        "evaluation_policy": (
            "Only the patent-grouped val split is sealed holdout for v1/v2 comparison. "
            "The original 172-page aggregate is no longer independent after this split."
        ),
        "holdout_ratio_requested": args.holdout_ratio,
        "seed": args.seed,
        "train": summarize(split_pages["train"]),
        "holdout": summarize(split_pages["val"]),
        "train_publications": sorted(
            {page["publication_number"] for page in split_pages["train"]}
        ),
        "holdout_publications": sorted(holdout_publications),
        "holdout_page_ids": [page["page_id"] for page in split_pages["val"]],
        "excluded_gold_pages_preserved": len(excluded_pages),
        "leakage_checks": {
            "publication_cross_split": False,
            "exact_image_duplicates": 0,
            "pseudo_labels_used": False,
        },
        "pages": page_rows,
    }
    (output / "split_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in report.items() if key != "pages"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
