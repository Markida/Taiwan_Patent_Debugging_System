"""Run EasyOCR over cached full-page plus sliced group detections."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("YOLO_CONFIG_DIR", os.environ.get("TEMP", str(PROJECT_ROOT)))

from features.patent_ocr.easyocr_loader import create_easyocr_reader
from features.patent_ocr.onnx_detector import DetectionBox, DetectionResult
from training.first_stage.evaluate_group_ocr import evaluate_setting
from training.first_stage.evaluate_sliced_group_locator import (
    DEFAULT_CACHE,
    DEFAULT_SWEEP,
    combine,
    load_full_raw,
    load_or_run_slices,
)
from training.first_stage.sweep_group_locator import load_gold_pages, sha256


DEFAULT_DATASET = PROJECT_ROOT / "training" / "gold_validation_v1"
DEFAULT_MODEL = PROJECT_ROOT / "models" / "patent_label_group_v1.onnx"
DEFAULT_OUTPUT = PROJECT_ROOT / "models" / "gold_group_sliced_ocr.json"


class PrecomputedGroupDetector:
    names = {0: "patent_label"}

    def __init__(self, pages, rows):
        self.rows = {
            str(page["image_path"].resolve()).lower(): row
            for page, row in zip(pages, rows, strict=True)
        }

    def predict(self, source, **_kwargs):
        key = str(Path(source).resolve()).lower()
        boxes, scores, classes = self.rows[key]
        return [
            DetectionResult(
                [
                    DetectionBox(box.tolist(), float(score), int(class_id))
                    for box, score, class_id in zip(boxes, scores, classes, strict=True)
                ]
            )
        ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate OCR over sliced detections.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--full-sweep", type=Path, default=DEFAULT_SWEEP)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--confidence", type=float, default=0.08)
    parser.add_argument(
        "--slice-confidence",
        type=float,
        default=None,
        help="Optional stricter threshold for cached slice-only candidates.",
    )
    parser.add_argument("--nms-iou", type=float, default=0.30)
    parser.add_argument("--ocr-conf", type=float, default=0.20)
    parser.add_argument("--match-iou", type=float, default=0.30)
    parser.add_argument("--tile-fraction", type=float, default=0.58)
    parser.add_argument("--edge-margin", type=float, default=4.0)
    parser.add_argument("--max-detections", type=int, default=500)
    parser.add_argument("--force-inference", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for name in ("dataset", "model", "full_sweep", "cache", "output"):
        setattr(args, name, getattr(args, name).resolve())
    qa = json.loads((args.dataset / "qa_report.json").read_text(encoding="utf-8"))
    if not qa.get("ready_for_threshold_sweep"):
        raise RuntimeError("Gold QA is not complete; sliced OCR is blocked.")
    pages = load_gold_pages(args.dataset)
    full_rows = load_full_raw(args, pages)
    tile_rows = load_or_run_slices(args, pages)
    slice_confidence = (
        args.confidence
        if args.slice_confidence is None
        else float(args.slice_confidence)
    )
    if slice_confidence < args.confidence:
        raise ValueError(
            "Slice confidence cannot be lower than the confidence stored in the cache."
        )
    tile_rows = [
        (
            boxes[scores >= slice_confidence],
            scores[scores >= slice_confidence],
            classes[scores >= slice_confidence],
        )
        for boxes, scores, classes in tile_rows
    ]
    union_rows = [
        combine(full_row, tile_row, args)
        for full_row, tile_row in zip(full_rows, tile_rows, strict=True)
    ]
    detector = PrecomputedGroupDetector(pages, union_rows)
    row = evaluate_setting(
        args,
        pages,
        detector,
        create_easyocr_reader(False),
        args.confidence,
        args.nms_iou,
    )
    report = {
        "format_version": 1,
        "evaluation_only": True,
        "mode": "full_page_plus_overlapping_2x2_slices",
        "dataset": str(args.dataset),
        "model": str(args.model),
        "model_sha256": sha256(args.model),
        "pages": len(pages),
        "gold_complete_labels": sum(len(page["gold_boxes"]) for page in pages),
        "imgsz": args.imgsz,
        "full_confidence": args.confidence,
        "slice_confidence": slice_confidence,
        "nms_iou": args.nms_iou,
        "tile_fraction": args.tile_fraction,
        "result": row,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "detection": row["detection"],
                "text": row["text"],
                "categories": row["categories"],
                "report": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
