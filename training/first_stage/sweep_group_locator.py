"""Sweep complete-label locator thresholds on the frozen gold validation set.

The network runs once per page at the lowest requested confidence. All other
confidence/NMS combinations are replayed from the same raw candidates so the
comparison is deterministic and fast.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
from features.patent_ocr.onnx_detector import (
    _letterbox,
    _nms,
    load_onnx_network,
)
from training.manual_annotation.build_group_locator_dataset import (
    group_character_annotations,
)
from training.manual_annotation.common import Annotation


DEFAULT_DATASET = PROJECT_ROOT / "training" / "gold_validation_v1"
DEFAULT_MODEL = PROJECT_ROOT / "models" / "patent_label_group_v1.onnx"
DEFAULT_OUTPUT = PROJECT_ROOT / "models" / "gold_group_threshold_sweep.json"
DEFAULT_CACHE = DEFAULT_DATASET / "inference_cache"


def parse_float_list(value: str) -> list[float]:
    values = sorted({float(item.strip()) for item in value.split(",") if item.strip()})
    if not values:
        raise argparse.ArgumentTypeError("At least one value is required.")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep group-locator thresholds.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument(
        "--confidences",
        type=parse_float_list,
        default=parse_float_list("0.03,0.05,0.08,0.10,0.15,0.20,0.25,0.30,0.40"),
    )
    parser.add_argument(
        "--nms-ious",
        type=parse_float_list,
        default=parse_float_list("0.30,0.40,0.50,0.60,0.70"),
    )
    parser.add_argument(
        "--match-ious",
        type=parse_float_list,
        default=parse_float_list("0.30,0.50"),
    )
    parser.add_argument("--max-detections", type=int, default=500)
    parser.add_argument("--force-inference", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_gold_pages(dataset: Path) -> list[dict]:
    freeze_manifest = json.loads(
        (dataset / "freeze_manifest.json").read_text(encoding="utf-8")
    )
    manifest_by_page = {
        row["page_id"]: row for row in freeze_manifest.get("pages", [])
    }
    pages = []
    for label_path in sorted((dataset / "labels" / "val").glob("*.json")):
        record = json.loads(label_path.read_text(encoding="utf-8"))
        if not record.get("reviewed"):
            raise RuntimeError(f"Gold page is not reviewed: {record['page_id']}")
        annotations = [
            Annotation(
                **{
                    key: raw.get(key)
                    for key in ("label", "x1", "y1", "x2", "y2", "source")
                }
            )
            for raw in record.get("annotations", [])
        ]
        groups = group_character_annotations(annotations)
        manifest_row = manifest_by_page.get(record["page_id"], {})
        pages.append(
            {
                "page_id": record["page_id"],
                "publication_number": manifest_row.get("publication_number", ""),
                "image_path": (dataset / record["image_path"]).resolve(),
                "gold_boxes": np.asarray(
                    [[group.x1, group.y1, group.x2, group.y2] for group in groups],
                    dtype=np.float32,
                ).reshape(-1, 4),
                "gold_texts": [group.text for group in groups],
            }
        )
    return pages


def cache_identity(dataset: Path, model: Path, imgsz: int, minimum_conf: float) -> dict:
    return {
        "format_version": 1,
        "dataset_manifest_sha256": sha256(dataset / "freeze_manifest.json"),
        "model_sha256": sha256(model),
        "model_path": str(model.resolve()),
        "imgsz": int(imgsz),
        "minimum_confidence": float(minimum_conf),
    }


def cache_path_for(cache_dir: Path, identity: dict) -> Path:
    short_key = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return cache_dir / f"group_locator_raw_{short_key}.npz"


def raw_candidates(network, image_path: Path, imgsz: int, minimum_conf: float):
    image = read_image(image_path)
    if image is None:
        raise ValueError(f"Cannot read gold image: {image_path}")
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
    keep = scores >= float(minimum_conf)
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


def load_or_run_raw_cache(args, pages):
    identity = cache_identity(
        args.dataset.resolve(),
        args.model.resolve(),
        args.imgsz,
        min(args.confidences),
    )
    cache_path = cache_path_for(args.cache_dir.resolve(), identity)
    if cache_path.exists() and not args.force_inference:
        archive = np.load(cache_path, allow_pickle=False)
        cached_identity = json.loads(str(archive["identity"].item()))
        page_ids = json.loads(str(archive["page_ids"].item()))
        if cached_identity == identity and page_ids == [page["page_id"] for page in pages]:
            print(f"Using raw cache: {cache_path}", flush=True)
            return [
                (
                    archive[f"boxes_{index}"],
                    archive[f"scores_{index}"],
                    archive[f"classes_{index}"],
                )
                for index in range(len(pages))
            ], cache_path

    print(f"Loading ONNX model: {args.model.resolve()}", flush=True)
    network = load_onnx_network(args.model.resolve())
    raw = []
    total = len(pages)
    for index, page in enumerate(pages, start=1):
        raw.append(
            raw_candidates(
                network,
                page["image_path"],
                args.imgsz,
                min(args.confidences),
            )
        )
        if index == 1 or index % 10 == 0 or index == total:
            print(f"Raw inference {index}/{total}", flush=True)

    payload = {
        "identity": np.asarray(json.dumps(identity, sort_keys=True)),
        "page_ids": np.asarray(json.dumps([page["page_id"] for page in pages])),
    }
    for index, (boxes, scores, classes) in enumerate(raw):
        payload[f"boxes_{index}"] = boxes
        payload[f"scores_{index}"] = scores
        payload[f"classes_{index}"] = classes
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache_path, **payload)
    print(f"Saved raw cache: {cache_path}", flush=True)
    return raw, cache_path


def box_iou(reference: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    if not len(candidates):
        return np.empty(0, dtype=np.float32)
    intersection = np.maximum(
        0,
        np.minimum(reference[2:], candidates[:, 2:])
        - np.maximum(reference[:2], candidates[:, :2]),
    ).prod(axis=1)
    reference_area = max(0.0, (reference[2] - reference[0]) * (reference[3] - reference[1]))
    candidate_area = np.maximum(
        0,
        (candidates[:, 2] - candidates[:, 0])
        * (candidates[:, 3] - candidates[:, 1]),
    )
    return intersection / (reference_area + candidate_area - intersection + 1e-7)


def count_matches(gold_boxes: np.ndarray, predicted_boxes: np.ndarray, threshold: float) -> int:
    candidates = []
    for gold_index, gold_box in enumerate(gold_boxes):
        for prediction_index, overlap in enumerate(box_iou(gold_box, predicted_boxes)):
            if overlap >= threshold:
                candidates.append((float(overlap), gold_index, prediction_index))
    candidates.sort(reverse=True)
    used_gold = set()
    used_predictions = set()
    matches = 0
    for _overlap, gold_index, prediction_index in candidates:
        if gold_index in used_gold or prediction_index in used_predictions:
            continue
        used_gold.add(gold_index)
        used_predictions.add(prediction_index)
        matches += 1
    return matches


def metric_summary(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    beta2 = 4.0
    f2 = (
        (1 + beta2) * precision * recall / (beta2 * precision + recall)
        if precision and recall
        else 0.0
    )
    return {
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "f2_recall_weighted": f2,
        "miss_weighted_cost_5fn_plus_fp": int(5 * fn + fp),
    }


def evaluate_grid(args, pages, raw):
    rows = []
    for confidence in args.confidences:
        for nms_iou in args.nms_ious:
            totals = {threshold: Counter() for threshold in args.match_ious}
            page_counts = []
            for page, (boxes, scores, classes) in zip(pages, raw, strict=True):
                confident = scores >= confidence
                selected_boxes = boxes[confident]
                selected_scores = scores[confident]
                selected_classes = classes[confident]
                kept = _nms(
                    selected_boxes,
                    selected_scores,
                    selected_classes,
                    float(nms_iou),
                    max_detections=int(args.max_detections),
                )
                predicted = selected_boxes[kept]
                page_counts.append(len(predicted))
                for match_iou, total in totals.items():
                    matches = count_matches(page["gold_boxes"], predicted, match_iou)
                    total["tp"] += matches
                    total["fp"] += len(predicted) - matches
                    total["fn"] += len(page["gold_boxes"]) - matches
            rows.append(
                {
                    "confidence": confidence,
                    "nms_iou": nms_iou,
                    "predictions": int(sum(page_counts)),
                    "average_predictions_per_page": sum(page_counts) / len(page_counts),
                    "metrics": {
                        f"iou{int(round(match_iou * 100))}": metric_summary(
                            total["tp"], total["fp"], total["fn"]
                        )
                        for match_iou, total in totals.items()
                    },
                }
            )
    return rows


def best_row(rows, metric_name: str, field: str, reverse=True):
    return sorted(
        rows,
        key=lambda row: row["metrics"][metric_name][field],
        reverse=reverse,
    )[0]


def main() -> int:
    args = parse_args()
    args.dataset = args.dataset.resolve()
    args.model = args.model.resolve()
    args.output = args.output.resolve()
    qa_path = args.dataset / "qa_report.json"
    qa = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.exists() else {}
    if not qa.get("ready_for_threshold_sweep"):
        raise RuntimeError("Gold QA is not complete; threshold sweep is blocked.")

    pages = load_gold_pages(args.dataset)
    gold_groups = sum(len(page["gold_boxes"]) for page in pages)
    print(f"Gold pages: {len(pages)}; complete labels: {gold_groups}", flush=True)
    raw, cache_path = load_or_run_raw_cache(args, pages)
    rows = evaluate_grid(args, pages, raw)
    match_key = "iou30" if "iou30" in rows[0]["metrics"] else next(iter(rows[0]["metrics"]))
    report = {
        "format_version": 1,
        "evaluation_only": True,
        "dataset": str(args.dataset),
        "model": str(args.model),
        "model_sha256": sha256(args.model),
        "pages": len(pages),
        "gold_complete_labels": gold_groups,
        "imgsz": args.imgsz,
        "confidences": args.confidences,
        "nms_ious": args.nms_ious,
        "match_ious": args.match_ious,
        "raw_cache": str(cache_path),
        "production_baseline": next(
            (
                row
                for row in rows
                if abs(row["confidence"] - 0.25) < 1e-9
                and abs(row["nms_iou"] - 0.40) < 1e-9
            ),
            None,
        ),
        "recommended_by_f2": best_row(rows, match_key, "f2_recall_weighted"),
        "best_by_f1": best_row(rows, match_key, "f1"),
        "highest_recall": best_row(rows, match_key, "recall"),
        "lowest_miss_weighted_cost": best_row(
            rows,
            match_key,
            "miss_weighted_cost_5fn_plus_fp",
            reverse=False,
        ),
        "grid": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        key: report[key]
        for key in (
            "production_baseline",
            "recommended_by_f2",
            "best_by_f1",
            "highest_recall",
            "lowest_miss_weighted_cost",
        )
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"Report: {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
