"""Compare full-page and overlapping 2x2 sliced group-locator inference."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("YOLO_CONFIG_DIR", os.environ.get("TEMP", str(PROJECT_ROOT)))

from features.patent_ocr.image_io import read_image
from features.patent_ocr.onnx_detector import _letterbox, _nms, load_onnx_network
from training.first_stage.sweep_group_locator import (
    count_matches,
    load_gold_pages,
    metric_summary,
    sha256,
)


DEFAULT_DATASET = PROJECT_ROOT / "training" / "gold_validation_v1"
DEFAULT_MODEL = PROJECT_ROOT / "models" / "patent_label_group_v1.onnx"
DEFAULT_SWEEP = PROJECT_ROOT / "models" / "gold_group_threshold_sweep.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "models" / "gold_group_sliced_comparison.json"
DEFAULT_CACHE = DEFAULT_DATASET / "inference_cache" / "group_locator_sliced_2x2.npz"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate sliced group inference.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--full-sweep", type=Path, default=DEFAULT_SWEEP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--confidence", type=float, default=0.08)
    parser.add_argument("--nms-iou", type=float, default=0.30)
    parser.add_argument("--tile-fraction", type=float, default=0.58)
    parser.add_argument("--edge-margin", type=float, default=4.0)
    parser.add_argument("--max-detections", type=int, default=500)
    parser.add_argument("--force-inference", action="store_true")
    return parser.parse_args()


def infer_array(network, image: np.ndarray, imgsz: int, confidence: float):
    height, width = image.shape[:2]
    padded, ratio, (left, top) = _letterbox(image, int(imgsz))
    blob = np.ascontiguousarray(
        padded[:, :, ::-1].transpose(2, 0, 1),
        dtype=np.float32,
    )[None]
    blob /= 255.0
    network.setInput(blob)
    predictions = network.forward()[0]
    if predictions.shape[0] < predictions.shape[1]:
        predictions = predictions.transpose(1, 0)
    xywh = predictions[:, :4]
    scores = predictions[:, 4:].max(axis=1)
    classes = predictions[:, 4:].argmax(axis=1).astype(np.int32)
    keep = scores >= float(confidence)
    xywh = xywh[keep]
    scores = scores[keep].astype(np.float32)
    classes = classes[keep]
    boxes = np.empty_like(xywh, dtype=np.float32)
    boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
    boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
    boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
    boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - left) / ratio
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - top) / ratio
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)
    valid = (boxes[:, 2] - boxes[:, 0] >= 1) & (boxes[:, 3] - boxes[:, 1] >= 1)
    return boxes[valid], scores[valid], classes[valid]


def two_by_two_tiles(width: int, height: int, fraction: float):
    tile_width = min(width, max(1, int(math.ceil(width * fraction))))
    tile_height = min(height, max(1, int(math.ceil(height * fraction))))
    x_starts = sorted({0, width - tile_width})
    y_starts = sorted({0, height - tile_height})
    return [
        (x, y, x + tile_width, y + tile_height)
        for y in y_starts
        for x in x_starts
    ]


def infer_tiles(network, image, args):
    height, width = image.shape[:2]
    all_boxes = []
    all_scores = []
    all_classes = []
    for x1, y1, x2, y2 in two_by_two_tiles(width, height, args.tile_fraction):
        boxes, scores, classes = infer_array(
            network,
            image[y1:y2, x1:x2],
            args.imgsz,
            args.confidence,
        )
        if not len(boxes):
            continue
        tile_width = x2 - x1
        tile_height = y2 - y1
        margin = float(args.edge_margin)
        complete = np.ones(len(boxes), dtype=bool)
        if x1 > 0:
            complete &= boxes[:, 0] > margin
        if y1 > 0:
            complete &= boxes[:, 1] > margin
        if x2 < width:
            complete &= boxes[:, 2] < tile_width - margin
        if y2 < height:
            complete &= boxes[:, 3] < tile_height - margin
        boxes = boxes[complete]
        scores = scores[complete]
        classes = classes[complete]
        boxes[:, [0, 2]] += x1
        boxes[:, [1, 3]] += y1
        all_boxes.append(boxes)
        all_scores.append(scores)
        all_classes.append(classes)
    if not all_boxes:
        return (
            np.empty((0, 4), dtype=np.float32),
            np.empty(0, dtype=np.float32),
            np.empty(0, dtype=np.int32),
        )
    return (
        np.concatenate(all_boxes),
        np.concatenate(all_scores),
        np.concatenate(all_classes),
    )


def apply_nms(boxes, scores, classes, args):
    if not len(boxes):
        return boxes, scores, classes
    kept = _nms(
        boxes,
        scores,
        classes,
        float(args.nms_iou),
        max_detections=int(args.max_detections),
    )
    return boxes[kept], scores[kept], classes[kept]


def load_full_raw(args, pages):
    sweep = json.loads(args.full_sweep.read_text(encoding="utf-8"))
    archive = np.load(Path(sweep["raw_cache"]), allow_pickle=False)
    page_ids = json.loads(str(archive["page_ids"].item()))
    expected = [page["page_id"] for page in pages]
    if page_ids != expected:
        raise RuntimeError("Full-page raw cache does not match the current gold set.")
    rows = []
    for index in range(len(pages)):
        boxes = archive[f"boxes_{index}"]
        scores = archive[f"scores_{index}"]
        classes = archive[f"classes_{index}"]
        keep = scores >= args.confidence
        rows.append(apply_nms(boxes[keep], scores[keep], classes[keep], args))
    return rows


def cache_identity(args, pages):
    return {
        "format_version": 1,
        "model_sha256": sha256(args.model),
        "manifest_sha256": sha256(args.dataset / "freeze_manifest.json"),
        "page_ids": [page["page_id"] for page in pages],
        "imgsz": args.imgsz,
        "confidence": args.confidence,
        "nms_iou": args.nms_iou,
        "tile_fraction": args.tile_fraction,
        "edge_margin": args.edge_margin,
    }


def load_or_run_slices(args, pages):
    identity = cache_identity(args, pages)
    if args.cache.exists() and not args.force_inference:
        archive = np.load(args.cache, allow_pickle=False)
        if json.loads(str(archive["identity"].item())) == identity:
            print(f"Using sliced cache: {args.cache}", flush=True)
            return [
                (
                    archive[f"boxes_{index}"],
                    archive[f"scores_{index}"],
                    archive[f"classes_{index}"],
                )
                for index in range(len(pages))
            ]

    network = load_onnx_network(args.model)
    rows = []
    for index, page in enumerate(pages, start=1):
        image = read_image(page["image_path"])
        if image is None:
            raise ValueError(f"Cannot read gold image: {page['image_path']}")
        rows.append(apply_nms(*infer_tiles(network, image, args), args))
        if index == 1 or index % 10 == 0 or index == len(pages):
            print(f"Sliced inference {index}/{len(pages)}", flush=True)
    payload = {"identity": np.asarray(json.dumps(identity, sort_keys=True))}
    for index, (boxes, scores, classes) in enumerate(rows):
        payload[f"boxes_{index}"] = boxes
        payload[f"scores_{index}"] = scores
        payload[f"classes_{index}"] = classes
    args.cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.cache, **payload)
    print(f"Saved sliced cache: {args.cache}", flush=True)
    return rows


def combine(full_row, tile_row, args):
    boxes = np.concatenate([full_row[0], tile_row[0]])
    scores = np.concatenate([full_row[1], tile_row[1]])
    classes = np.concatenate([full_row[2], tile_row[2]])
    return apply_nms(boxes, scores, classes, args)


def evaluate_mode(pages, rows):
    totals = {0.30: Counter(), 0.50: Counter()}
    for page, (boxes, _scores, _classes) in zip(pages, rows, strict=True):
        for threshold, counts in totals.items():
            matches = count_matches(page["gold_boxes"], boxes, threshold)
            counts["tp"] += matches
            counts["fp"] += len(boxes) - matches
            counts["fn"] += len(page["gold_boxes"]) - matches
    return {
        "predictions": sum(len(row[0]) for row in rows),
        "metrics": {
            f"iou{int(threshold * 100)}": metric_summary(
                counts["tp"], counts["fp"], counts["fn"]
            )
            for threshold, counts in totals.items()
        },
    }


def main() -> int:
    args = parse_args()
    for name in ("dataset", "model", "full_sweep", "output", "cache"):
        setattr(args, name, getattr(args, name).resolve())
    qa = json.loads((args.dataset / "qa_report.json").read_text(encoding="utf-8"))
    if not qa.get("ready_for_threshold_sweep"):
        raise RuntimeError("Gold QA is not complete; sliced evaluation is blocked.")
    pages = load_gold_pages(args.dataset)
    full_rows = load_full_raw(args, pages)
    tile_rows = load_or_run_slices(args, pages)
    union_rows = [
        combine(full_row, tile_row, args)
        for full_row, tile_row in zip(full_rows, tile_rows, strict=True)
    ]
    report = {
        "format_version": 1,
        "evaluation_only": True,
        "dataset": str(args.dataset),
        "model": str(args.model),
        "pages": len(pages),
        "gold_complete_labels": sum(len(page["gold_boxes"]) for page in pages),
        "imgsz": args.imgsz,
        "confidence": args.confidence,
        "nms_iou": args.nms_iou,
        "tile_fraction": args.tile_fraction,
        "edge_margin": args.edge_margin,
        "cache": str(args.cache),
        "modes": {
            "full_page": evaluate_mode(pages, full_rows),
            "sliced_2x2": evaluate_mode(pages, tile_rows),
            "full_plus_sliced": evaluate_mode(pages, union_rows),
        },
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["modes"], ensure_ascii=False, indent=2), flush=True)
    print(f"Report: {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
