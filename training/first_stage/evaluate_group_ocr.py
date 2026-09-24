"""Measure end-to-end complete-label OCR on the frozen gold set."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("YOLO_CONFIG_DIR", os.environ.get("TEMP", str(PROJECT_ROOT)))

from features.patent_ocr.easyocr_loader import create_easyocr_reader
from features.patent_ocr.compact_ocr_reader import CompactEnglishReader
from features.patent_ocr.ocr_engine import recognize_one_image
from features.patent_ocr.onnx_detector import OnnxDetector
from features.patent_ocr.reference_reconciliation import reconcile_label_to_reference
from training.first_stage.sweep_group_locator import (
    box_iou,
    load_gold_pages,
    metric_summary,
    sha256,
)


DEFAULT_DATASET = PROJECT_ROOT / "training" / "gold_validation_v1"
DEFAULT_MODEL = PROJECT_ROOT / "models" / "patent_label_group_v1.onnx"
DEFAULT_OUTPUT = PROJECT_ROOT / "models" / "gold_group_ocr_comparison.json"


def parse_settings(value: str) -> list[tuple[float, float]]:
    settings = []
    for token in value.split(","):
        confidence, nms_iou = token.strip().split(":", 1)
        pair = (float(confidence), float(nms_iou))
        if pair not in settings:
            settings.append(pair)
    if not settings:
        raise argparse.ArgumentTypeError("At least one confidence:NMS pair is required.")
    return settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate group locator plus OCR.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--ocr-conf", type=float, default=0.20)
    parser.add_argument(
        "--ocr-model",
        type=Path,
        help="Optional recognizer weights; defaults to the production EasyOCR model.",
    )
    parser.add_argument("--match-iou", type=float, default=0.30)
    parser.add_argument(
        "--settings",
        type=parse_settings,
        default=parse_settings("0.25:0.40,0.10:0.30,0.05:0.30"),
    )
    parser.add_argument(
        "--simulate-reference-reconciliation",
        action="store_true",
        help="Evaluate conservative correction against publication gold symbol lists.",
    )
    return parser.parse_args()


def greedy_matches(gold_boxes: np.ndarray, detections: list[dict], threshold: float):
    predicted_boxes = np.asarray(
        [
            [item["x1"], item["y1"], item["x2"], item["y2"]]
            for item in detections
        ],
        dtype=np.float32,
    ).reshape(-1, 4)
    candidates = []
    for gold_index, gold_box in enumerate(gold_boxes):
        for predicted_index, overlap in enumerate(box_iou(gold_box, predicted_boxes)):
            if overlap >= threshold:
                candidates.append((float(overlap), gold_index, predicted_index))
    candidates.sort(reverse=True)
    used_gold = set()
    used_predictions = set()
    matches = []
    for overlap, gold_index, predicted_index in candidates:
        if gold_index in used_gold or predicted_index in used_predictions:
            continue
        used_gold.add(gold_index)
        used_predictions.add(predicted_index)
        matches.append((gold_index, predicted_index, overlap))
    return matches


def target_categories(text: str) -> set[str]:
    categories = {"all"}
    if text.isdigit():
        categories.add("digits_only")
    if any(character.isalpha() for character in text):
        categories.add("contains_letter")
    if any(character.islower() for character in text):
        categories.add("contains_lowercase")
    if "'" in text:
        categories.add("contains_prime")
    if len(text) > 1 and set(text) <= set("IVX"):
        categories.add("roman_multi_character")
    if len(text) > 1:
        categories.add("multi_character")
    return categories


def category_summary(counts: Counter) -> dict:
    total = counts["total"]
    correct = counts["correct"]
    return {
        "total": total,
        "localized": counts["localized"],
        "correct": correct,
        "end_to_end_recall": correct / total if total else None,
    }


def evaluate_setting(
    args,
    pages,
    model,
    reader,
    confidence,
    nms_iou,
    references_by_publication,
):
    detection_counts = Counter()
    text_counts = Counter()
    categories = defaultdict(Counter)
    confusions = Counter()
    page_rows = []
    reconciliation_audit = []

    for page_index, page in enumerate(pages, start=1):
        result = recognize_one_image(
            image_path=page["image_path"],
            model=model,
            reader=reader,
            model_name=args.model.name,
            yolo_conf=confidence,
            ocr_conf=args.ocr_conf,
            imgsz=args.imgsz,
            nms_iou=nms_iou,
            sliced_group_inference=False,
        )
        detections = result["detections"]
        reconciled = 0
        if args.simulate_reference_reconciliation:
            references = references_by_publication.get(page["publication_number"], set())
            for prediction_index, detection in enumerate(detections):
                original_label = detection.get("label", "")
                corrected, changed = reconcile_label_to_reference(
                    original_label,
                    references,
                )
                if changed:
                    detection["label"] = corrected
                    detection["_reconciliation_original_label"] = original_label
                    detection["_reconciliation_prediction_index"] = prediction_index
                    reconciled += 1
        matches = greedy_matches(page["gold_boxes"], detections, args.match_iou)
        expected_by_prediction = {
            prediction_index: page["gold_texts"][gold_index]
            for gold_index, prediction_index, _overlap in matches
        }
        if args.simulate_reference_reconciliation:
            for prediction_index, detection in enumerate(detections):
                if "_reconciliation_original_label" not in detection:
                    continue
                original_label = detection["_reconciliation_original_label"]
                corrected_label = detection.get("label", "")
                expected = expected_by_prediction.get(prediction_index)
                before_correct = expected is not None and original_label == expected
                after_correct = expected is not None and corrected_label == expected
                if after_correct and not before_correct:
                    outcome = "improved"
                elif before_correct and not after_correct:
                    outcome = "harmed"
                elif expected is None:
                    outcome = "unmatched_prediction"
                else:
                    outcome = "still_wrong"
                reconciliation_audit.append(
                    {
                        "page_id": page["page_id"],
                        "publication_number": page["publication_number"],
                        "original": original_label,
                        "corrected": corrected_label,
                        "expected": expected,
                        "outcome": outcome,
                        "confidence": detection.get("confidence"),
                        "ocr_confidence": detection.get("ocr_conf"),
                        "locator_confidence": detection.get("yolo_conf"),
                        "box": [
                            detection.get("x1"),
                            detection.get("y1"),
                            detection.get("x2"),
                            detection.get("y2"),
                        ],
                    }
                )
        correct = 0
        wrong = 0
        for gold_index, prediction_index, _overlap in matches:
            expected = page["gold_texts"][gold_index]
            predicted = detections[prediction_index]["label"]
            is_correct = expected == predicted
            correct += int(is_correct)
            wrong += int(not is_correct)
            if not is_correct:
                confusions[(expected, predicted)] += 1
            for category in target_categories(expected):
                categories[category]["localized"] += 1
                categories[category]["correct"] += int(is_correct)

        for expected in page["gold_texts"]:
            for category in target_categories(expected):
                categories[category]["total"] += 1

        gold_count = len(page["gold_boxes"])
        prediction_count = len(detections)
        localized = len(matches)
        detection_counts["tp"] += localized
        detection_counts["fp"] += prediction_count - localized
        detection_counts["fn"] += gold_count - localized
        text_counts["gold"] += gold_count
        text_counts["predictions"] += prediction_count
        text_counts["correct"] += correct
        text_counts["wrong_text"] += wrong
        text_counts["rejected"] += len(result.get("rejected_detections", []))
        page_rows.append(
            {
                "page_id": page["page_id"],
                "gold": gold_count,
                "predictions": prediction_count,
                "localized": localized,
                "correct_text": correct,
                "wrong_text": wrong,
                "missed": gold_count - localized,
                "reference_reconciliations": reconciled,
            }
        )
        if page_index == 1 or page_index % 10 == 0 or page_index == len(pages):
            print(
                f"OCR conf={confidence:.2f} nms={nms_iou:.2f}: "
                f"{page_index}/{len(pages)}",
                flush=True,
            )

    correct = text_counts["correct"]
    predictions = text_counts["predictions"]
    gold = text_counts["gold"]
    precision = correct / predictions if predictions else 0.0
    recall = correct / gold if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    beta2 = 4.0
    f2 = (
        (1 + beta2) * precision * recall / (beta2 * precision + recall)
        if precision and recall
        else 0.0
    )
    return {
        "confidence": confidence,
        "nms_iou": nms_iou,
        "detection": metric_summary(
            detection_counts["tp"],
            detection_counts["fp"],
            detection_counts["fn"],
        ),
        "text": {
            "gold": gold,
            "displayed_predictions": predictions,
            "correct": correct,
            "wrong_text_on_localized_box": text_counts["wrong_text"],
            "rejected_by_ocr_rules": text_counts["rejected"],
            "end_to_end_precision": precision,
            "end_to_end_recall": recall,
            "end_to_end_f1": f1,
            "end_to_end_f2_recall_weighted": f2,
            "miss_weighted_cost_5fn_plus_fp": 5 * (gold - correct) + (predictions - correct),
        },
        "categories": {
            name: category_summary(counts)
            for name, counts in sorted(categories.items())
        },
        "top_confusions": [
            {"expected": expected, "predicted": predicted, "count": count}
            for (expected, predicted), count in confusions.most_common(50)
        ],
        "reference_reconciliation_summary": dict(
            Counter(row["outcome"] for row in reconciliation_audit)
        ),
        "reference_reconciliation_audit": reconciliation_audit,
        "worst_pages": sorted(
            page_rows,
            key=lambda row: (
                row["correct_text"] / row["gold"] if row["gold"] else 1.0,
                -row["gold"],
                row["page_id"],
            ),
        )[:30],
    }


def main() -> int:
    args = parse_args()
    args.dataset = args.dataset.resolve()
    args.model = args.model.resolve()
    args.output = args.output.resolve()
    if args.ocr_model is not None:
        args.ocr_model = args.ocr_model.resolve()
    qa = json.loads((args.dataset / "qa_report.json").read_text(encoding="utf-8"))
    if not qa.get("ready_for_threshold_sweep"):
        raise RuntimeError("Gold QA is not complete; OCR evaluation is blocked.")

    pages = load_gold_pages(args.dataset)
    references_by_publication = defaultdict(set)
    for page in pages:
        references_by_publication[page["publication_number"]].update(page["gold_texts"])
    model = OnnxDetector(args.model)
    reader = (
        CompactEnglishReader(args.ocr_model, gpu=False)
        if args.ocr_model is not None
        else create_easyocr_reader(False)
    )
    settings = []
    for confidence, nms_iou in args.settings:
        settings.append(
            evaluate_setting(
                args,
                pages,
                model,
                reader,
                confidence,
                nms_iou,
                references_by_publication,
            )
        )

    recommended = max(
        settings,
        key=lambda row: row["text"]["end_to_end_f2_recall_weighted"],
    )
    report = {
        "format_version": 1,
        "evaluation_only": True,
        "dataset": str(args.dataset),
        "model": str(args.model),
        "model_sha256": sha256(args.model),
        "ocr_model": str(args.ocr_model) if args.ocr_model is not None else "production_default",
        "ocr_model_sha256": sha256(args.ocr_model) if args.ocr_model is not None else None,
        "pages": len(pages),
        "gold_complete_labels": sum(len(page["gold_boxes"]) for page in pages),
        "imgsz": args.imgsz,
        "ocr_confidence": args.ocr_conf,
        "match_iou": args.match_iou,
        "simulated_reference_reconciliation": bool(
            args.simulate_reference_reconciliation
        ),
        "recommended_by_end_to_end_f2": recommended,
        "settings": settings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "recommended": {
                    "confidence": recommended["confidence"],
                    "nms_iou": recommended["nms_iou"],
                    "detection": recommended["detection"],
                    "text": recommended["text"],
                    "categories": recommended["categories"],
                },
                "settings": [
                    {
                        "confidence": row["confidence"],
                        "nms_iou": row["nms_iou"],
                        "detection": row["detection"],
                        "text": row["text"],
                    }
                    for row in settings
                ],
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
