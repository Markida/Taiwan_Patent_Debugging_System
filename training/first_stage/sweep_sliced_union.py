"""Sweep the sliced-candidate confidence while keeping full-page settings fixed.

The expensive 2x2 inference has already been cached by
``evaluate_sliced_group_locator.py``.  This script replays that cache so we can
measure how much extra recall each sliced-candidate threshold buys without
running the detector again.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.first_stage.evaluate_sliced_group_locator import (
    apply_nms,
    combine,
    evaluate_mode,
    load_full_raw,
)
from training.first_stage.sweep_group_locator import load_gold_pages


DEFAULT_DATASET = PROJECT_ROOT / "training" / "gold_validation_v1"
DEFAULT_FULL_SWEEP = PROJECT_ROOT / "models" / "gold_group_threshold_sweep.json"
DEFAULT_SLICE_CACHE = (
    DEFAULT_DATASET / "inference_cache" / "group_locator_sliced_2x2.npz"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "models" / "gold_group_sliced_threshold_sweep.json"


def parse_float_list(value: str) -> list[float]:
    values = sorted({float(item.strip()) for item in value.split(",") if item.strip()})
    if not values:
        raise argparse.ArgumentTypeError("At least one threshold is required.")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--full-sweep", type=Path, default=DEFAULT_FULL_SWEEP)
    parser.add_argument("--slice-cache", type=Path, default=DEFAULT_SLICE_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--full-confidence", type=float, default=0.08)
    parser.add_argument("--nms-iou", type=float, default=0.30)
    parser.add_argument(
        "--slice-confidences",
        type=parse_float_list,
        default=parse_float_list("0.08,0.10,0.12,0.15,0.20,0.25"),
    )
    parser.add_argument("--max-detections", type=int, default=500)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.dataset = args.dataset.resolve()
    args.full_sweep = args.full_sweep.resolve()
    args.slice_cache = args.slice_cache.resolve()
    args.output = args.output.resolve()
    args.confidence = args.full_confidence

    pages = load_gold_pages(args.dataset)
    full_rows = load_full_raw(args, pages)
    archive = np.load(args.slice_cache, allow_pickle=False)
    identity = json.loads(str(archive["identity"].item()))
    cached_minimum = float(identity["confidence"])
    if min(args.slice_confidences) < cached_minimum:
        raise RuntimeError(
            f"Slice cache starts at {cached_minimum:.2f}; cannot replay a lower threshold."
        )

    cached_rows = [
        (
            archive[f"boxes_{index}"],
            archive[f"scores_{index}"],
            archive[f"classes_{index}"],
        )
        for index in range(len(pages))
    ]
    settings = []
    for slice_confidence in args.slice_confidences:
        filtered_rows = []
        for boxes, scores, classes in cached_rows:
            keep = scores >= slice_confidence
            filtered_rows.append(
                apply_nms(boxes[keep], scores[keep], classes[keep], args)
            )
        union_rows = [
            combine(full_row, slice_row, args)
            for full_row, slice_row in zip(full_rows, filtered_rows, strict=True)
        ]
        result = evaluate_mode(pages, union_rows)
        settings.append(
            {
                "full_confidence": args.full_confidence,
                "slice_confidence": slice_confidence,
                "nms_iou": args.nms_iou,
                **result,
            }
        )

    best = max(
        settings,
        key=lambda row: row["metrics"]["iou30"]["f2_recall_weighted"],
    )
    report = {
        "format_version": 1,
        "evaluation_only": True,
        "dataset": str(args.dataset),
        "pages": len(pages),
        "gold_complete_labels": sum(len(page["gold_boxes"]) for page in pages),
        "slice_cache": str(args.slice_cache),
        "slice_cache_identity": identity,
        "recommended_by_locator_f2": best,
        "settings": settings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
