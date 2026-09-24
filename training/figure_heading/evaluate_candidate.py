"""Run frozen-test end-to-end checks for a candidate figure-heading model."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_ocr.easyocr_loader import create_easyocr_reader
from features.patent_ocr.figure_heading import (
    analyze_figure_headings,
    is_figure_heading_model,
)
from features.patent_ocr.figure_heading_classes import FIGURE_HEADING_CLASS_NAMES
from features.patent_ocr.figure_identifiers import normalize_figure_identifier
from features.patent_ocr.model_loader import load_detection_model
from features.patent_ocr.ocr_engine import get_compute_device
from training.figure_heading.dataset_integrity import verify_dataset_inventory


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET = SCRIPT_DIR / "figure_heading_yolo_v1"
DEFAULT_MODEL = SCRIPT_DIR / "candidates" / "figure_heading_locator_v1.onnx"
OFFICIAL_RELEASE_FLOORS = {
    "minimum_positive_items": 40,
    "minimum_negative_items": 20,
    "minimum_positive_pages": 10,
    "minimum_negative_pages": 5,
    "minimum_positive_documents": 5,
    "minimum_negative_documents": 5,
    "minimum_rare_identifier_items": 8,
    "minimum_rare_identifier_pages": 2,
    "minimum_rare_identifier_documents": 2,
    "minimum_detected_set_accuracy": 0.98,
    "minimum_auto_mapping_accuracy": 0.97,
    "minimum_orientation_accuracy": 0.99,
    "minimum_rare_detected_set_accuracy": 0.95,
    "minimum_rare_auto_mapping_accuracy": 0.95,
    "maximum_negative_false_positive_rate": 0.005,
}
_COUNT_THRESHOLD_KEYS = (
    "minimum_positive_items",
    "minimum_negative_items",
    "minimum_positive_pages",
    "minimum_negative_pages",
    "minimum_positive_documents",
    "minimum_negative_documents",
    "minimum_rare_identifier_items",
    "minimum_rare_identifier_pages",
    "minimum_rare_identifier_documents",
)
_MINIMUM_RATE_THRESHOLD_KEYS = (
    "minimum_detected_set_accuracy",
    "minimum_auto_mapping_accuracy",
    "minimum_orientation_accuracy",
    "minimum_rare_detected_set_accuracy",
    "minimum_rare_auto_mapping_accuracy",
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--experimental",
        action="store_true",
        help=(
            "Allow diagnostics on a dataset that is not release-ready. "
            "Experimental reports can never pass production release gates."
        ),
    )
    parser.add_argument(
        "--split",
        choices=("val", "test"),
        default="test",
        help="Evaluate val or test; val is only allowed with --experimental.",
    )
    for key in _COUNT_THRESHOLD_KEYS:
        parser.add_argument(
            "--" + key.replace("_", "-"),
            type=int,
            default=OFFICIAL_RELEASE_FLOORS[key],
        )
    parser.add_argument(
        "--minimum-detected-set-accuracy",
        type=float,
        default=OFFICIAL_RELEASE_FLOORS["minimum_detected_set_accuracy"],
    )
    parser.add_argument(
        "--minimum-auto-mapping-accuracy",
        type=float,
        default=OFFICIAL_RELEASE_FLOORS["minimum_auto_mapping_accuracy"],
    )
    parser.add_argument(
        "--minimum-orientation-accuracy",
        type=float,
        default=OFFICIAL_RELEASE_FLOORS["minimum_orientation_accuracy"],
    )
    parser.add_argument(
        "--minimum-rare-detected-set-accuracy",
        type=float,
        default=OFFICIAL_RELEASE_FLOORS[
            "minimum_rare_detected_set_accuracy"
        ],
    )
    parser.add_argument(
        "--minimum-rare-auto-mapping-accuracy",
        type=float,
        default=OFFICIAL_RELEASE_FLOORS[
            "minimum_rare_auto_mapping_accuracy"
        ],
    )
    parser.add_argument(
        "--maximum-negative-false-positive-rate",
        type=float,
        default=OFFICIAL_RELEASE_FLOORS[
            "maximum_negative_false_positive_rate"
        ],
    )
    return parser.parse_args()


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_counter(values):
    normalized = []
    for value in values:
        try:
            normalized.append(str(normalize_figure_identifier(value)))
        except ValueError:
            normalized.append(f"INVALID:{value}")
    return Counter(normalized)


def _rate(numerator, denominator):
    return round(numerator / denominator, 6) if denominator else 0.0


def _correction_bucket(value):
    if value is None:
        return "none"
    try:
        normalized = int(value) % 360
    except (TypeError, ValueError, OverflowError):
        return "invalid"
    return str(normalized) if normalized in {0, 90, 180, 270} else "invalid"


def _new_correction_metrics():
    return {
        "items": 0,
        "positive_items": 0,
        "negative_items": 0,
        "detected_set_exact": 0,
        "auto_mapping_exact": 0,
        "orientation_exact": 0,
        "auto_decision_count": 0,
        "correct_auto_decisions": 0,
        "wrong_auto_decisions": 0,
        "negative_false_positives": 0,
        "analysis_error_count": 0,
    }


def _finalize_correction_metrics(metrics):
    output = {}
    for bucket, values in metrics.items():
        positive = values["positive_items"]
        negative = values["negative_items"]
        output[bucket] = {
            **values,
            "detected_set_exact_accuracy": _rate(
                values["detected_set_exact"], positive
            ),
            "auto_mapping_exact_accuracy": _rate(
                values["auto_mapping_exact"], positive
            ),
            "orientation_accuracy": _rate(values["orientation_exact"], positive),
            "abstentions": positive - values["auto_decision_count"],
            "auto_decision_precision": _rate(
                values["correct_auto_decisions"],
                values["auto_decision_count"],
            ),
            "negative_false_positive_rate": _rate(
                values["negative_false_positives"], negative
            ),
        }
    return output


def validate_release_thresholds(thresholds):
    """Require the official release floor while allowing stricter checks."""

    if not isinstance(thresholds, dict):
        raise RuntimeError("端到端測試報告缺少完整發布門檻。")
    normalized = {}
    for key in _COUNT_THRESHOLD_KEYS:
        value = thresholds.get(key)
        if isinstance(value, bool):
            raise RuntimeError(f"發布門檻 {key} 格式不正確。")
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise RuntimeError(f"發布門檻 {key} 格式不正確。") from None
        if value < OFFICIAL_RELEASE_FLOORS[key]:
            raise RuntimeError(f"發布門檻 {key} 不得低於正式安全線。")
        normalized[key] = value
    for key in _MINIMUM_RATE_THRESHOLD_KEYS:
        try:
            value = float(thresholds.get(key))
        except (TypeError, ValueError):
            raise RuntimeError(f"發布門檻 {key} 格式不正確。") from None
        if not math.isfinite(value) or value < OFFICIAL_RELEASE_FLOORS[key]:
            raise RuntimeError(f"發布門檻 {key} 不得低於正式安全線。")
        normalized[key] = value
    key = "maximum_negative_false_positive_rate"
    try:
        value = float(thresholds.get(key))
    except (TypeError, ValueError):
        raise RuntimeError(f"發布門檻 {key} 格式不正確。") from None
    if (
        not math.isfinite(value)
        or value < 0.0
        or value > OFFICIAL_RELEASE_FLOORS[key]
    ):
        raise RuntimeError(f"發布門檻 {key} 不得高於正式安全線。")
    normalized[key] = value
    return normalized


def release_report_passes(report):
    """Recompute a report's result instead of trusting its boolean flag."""

    thresholds = validate_release_thresholds(report.get("thresholds"))
    count_checks = {
        "minimum_positive_items": "positive_items",
        "minimum_negative_items": "negative_items",
        "minimum_positive_pages": "positive_pages",
        "minimum_negative_pages": "negative_pages",
        "minimum_positive_documents": "positive_documents",
        "minimum_negative_documents": "negative_documents",
        "minimum_rare_identifier_items": "rare_identifier_items",
        "minimum_rare_identifier_pages": "rare_identifier_pages",
        "minimum_rare_identifier_documents": "rare_identifier_documents",
    }
    try:
        counts_ok = all(
            int(report[report_key]) >= thresholds[threshold_key]
            for threshold_key, report_key in count_checks.items()
        )
        rates_ok = (
            float(report["detected_set_exact_accuracy"])
            >= thresholds["minimum_detected_set_accuracy"]
            and float(report["auto_mapping_exact_accuracy"])
            >= thresholds["minimum_auto_mapping_accuracy"]
            and float(report["orientation_accuracy"])
            >= thresholds["minimum_orientation_accuracy"]
            and float(report["rare_detected_set_exact_accuracy"])
            >= thresholds["minimum_rare_detected_set_accuracy"]
            and float(report["rare_auto_mapping_exact_accuracy"])
            >= thresholds["minimum_rare_auto_mapping_accuracy"]
            and float(report["negative_false_positive_rate"])
            <= thresholds["maximum_negative_false_positive_rate"]
        )
        structure_ok = (
            report.get("version") == 2
            and report.get("experimental") is not True
            and report.get("evaluation_mode", "release") == "release"
            and report.get("dataset_ready_for_training", True) is True
            and report.get("evaluated_split") == "test"
            and int(report["test_items"])
            == int(report["positive_items"]) + int(report["negative_items"])
            and int(report["orientation_eligible_items"])
            == int(report["positive_items"])
            and int(report["analysis_error_count"]) == 0
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return bool(counts_ok and rates_ok and structure_ok)


def evaluate(
    dataset_root,
    model_path,
    *,
    output_path=None,
    experimental=False,
    split="test",
    minimum_positive_items=40,
    minimum_negative_items=20,
    minimum_positive_pages=10,
    minimum_negative_pages=5,
    minimum_positive_documents=5,
    minimum_negative_documents=5,
    minimum_rare_identifier_items=8,
    minimum_rare_identifier_pages=2,
    minimum_rare_identifier_documents=2,
    minimum_detected_set_accuracy=0.98,
    minimum_auto_mapping_accuracy=0.97,
    minimum_orientation_accuracy=0.99,
    minimum_rare_detected_set_accuracy=0.95,
    minimum_rare_auto_mapping_accuracy=0.95,
    maximum_negative_false_positive_rate=0.005,
):
    dataset_root = Path(dataset_root).resolve()
    model_path = Path(model_path).resolve()
    split = str(split).strip().lower()
    if split not in {"val", "test"}:
        raise ValueError("評估 split 只接受 val 或 test。")
    if split != "test" and not experimental:
        raise RuntimeError("非 test split 只允許使用 --experimental 評估。")
    thresholds = validate_release_thresholds(
        {
            "minimum_positive_items": minimum_positive_items,
            "minimum_negative_items": minimum_negative_items,
            "minimum_positive_pages": minimum_positive_pages,
            "minimum_negative_pages": minimum_negative_pages,
            "minimum_positive_documents": minimum_positive_documents,
            "minimum_negative_documents": minimum_negative_documents,
            "minimum_rare_identifier_items": minimum_rare_identifier_items,
            "minimum_rare_identifier_pages": minimum_rare_identifier_pages,
            "minimum_rare_identifier_documents": (
                minimum_rare_identifier_documents
            ),
            "minimum_detected_set_accuracy": minimum_detected_set_accuracy,
            "minimum_auto_mapping_accuracy": minimum_auto_mapping_accuracy,
            "minimum_orientation_accuracy": minimum_orientation_accuracy,
            "minimum_rare_detected_set_accuracy": (
                minimum_rare_detected_set_accuracy
            ),
            "minimum_rare_auto_mapping_accuracy": (
                minimum_rare_auto_mapping_accuracy
            ),
            "maximum_negative_false_positive_rate": (
                maximum_negative_false_positive_rate
            ),
        }
    )
    manifest_path = dataset_root / "evaluation_manifest.json"
    dataset_report_path = dataset_root / "dataset_report.json"
    if not model_path.is_file():
        raise FileNotFoundError(f"找不到候選模型：{model_path}")
    if not manifest_path.is_file() or not dataset_report_path.is_file():
        raise FileNotFoundError("找不到正式資料集的端到端測試清單或檢查報告。")
    dataset_report = json.loads(dataset_report_path.read_text(encoding="utf-8"))
    if not dataset_report.get("ready_for_training") and not experimental:
        raise RuntimeError("資料集未通過正式門檻，不能執行候選模型發布測試。")
    dataset_integrity = verify_dataset_inventory(
        dataset_root,
        expected_content_sha256=dataset_report.get("dataset_content_sha256"),
        expected_inventory_sha256=dataset_report.get(
            "dataset_inventory_sha256"
        ),
    )
    evaluation_items = [
        item
        for item in json.loads(manifest_path.read_text(encoding="utf-8"))
        if item.get("split") == split
    ]
    if not evaluation_items:
        raise RuntimeError(f"{split} split 沒有可評估項目。")

    model = load_detection_model(model_path)
    if not is_figure_heading_model(model):
        raise RuntimeError(
            "候選模型類別必須依序為："
            + "、".join(FIGURE_HEADING_CLASS_NAMES)
        )
    reader = create_easyocr_reader(False)
    device = get_compute_device()

    positive_items = 0
    negative_items = 0
    detected_set_exact = 0
    auto_mapping_exact = 0
    orientation_eligible = 0
    orientation_exact = 0
    auto_decision_count = 0
    correct_auto_decisions = 0
    wrong_auto_decisions = 0
    negative_false_positives = 0
    rare_identifier_items = 0
    rare_detected_set_exact = 0
    rare_auto_mapping_exact = 0
    positive_page_ids = set()
    negative_page_ids = set()
    positive_document_ids = set()
    negative_document_ids = set()
    rare_page_ids = set()
    rare_document_ids = set()
    errors = []
    predictions = []
    correction_metrics = {
        bucket: _new_correction_metrics()
        for bucket in ("0", "90", "180", "270", "none", "invalid")
    }
    source_correction_metrics = {
        bucket: _new_correction_metrics()
        for bucket in ("0", "90", "180", "270", "none", "invalid")
    }
    for item in evaluation_items:
        expected = item.get("expected_figure_numbers", [])
        is_positive = bool(expected)
        is_rare_identifier_item = any(
            any(character.isalpha() for character in str(value))
            or str(value).endswith("'")
            for value in expected
        )
        positive_items += int(is_positive)
        negative_items += int(not is_positive)
        orientation_eligible += int(is_positive)
        page_id = str(item.get("page_id") or item.get("item_id") or "")
        document_id = str(item.get("document_id") or "")
        (positive_page_ids if is_positive else negative_page_ids).add(page_id)
        if document_id:
            (
                positive_document_ids if is_positive else negative_document_ids
            ).add(document_id)
        if is_rare_identifier_item:
            rare_identifier_items += 1
            rare_page_ids.add(page_id)
            if document_id:
                rare_document_ids.add(document_id)
        expected_correction = item.get("expected_correction_degrees")
        correction_bucket = _correction_bucket(expected_correction)
        bucket_metrics = correction_metrics[correction_bucket]
        rotation_applied = item.get("rotation_applied_clockwise")
        source_correction = None
        source_correction_bucket = "none"
        if expected_correction is not None:
            try:
                source_correction = (
                    int(expected_correction) + int(rotation_applied)
                ) % 360
                source_correction_bucket = _correction_bucket(source_correction)
            except (TypeError, ValueError, OverflowError):
                source_correction_bucket = "invalid"
        source_bucket_metrics = source_correction_metrics[
            source_correction_bucket
        ]
        for grouped_metrics in (bucket_metrics, source_bucket_metrics):
            grouped_metrics["items"] += 1
            grouped_metrics["positive_items"] += int(is_positive)
            grouped_metrics["negative_items"] += int(not is_positive)
        prediction = {
            "item_id": item.get("item_id"),
            "page_id": page_id,
            "document_id": document_id,
            "image_path": item.get("image_path"),
            "expected_figure_numbers": list(expected),
            "expected_orientation_status": item.get(
                "expected_orientation_status"
            ),
            "expected_correction_degrees": expected_correction,
            "rotation_applied_clockwise": rotation_applied,
            "source_correction_degrees": source_correction,
            "detected_figure_numbers": [],
            "auto_figure_numbers": [],
            "actual_orientation": {},
            "figure_caption_detections": [],
            "prefix_detection_count": 0,
            "identifier_detection_count": 0,
            "strong_prefix_count": 0,
            "detected_set_matches": False,
            "auto_mapping_matches": False,
            "orientation_matches": False,
            "negative_has_effect": False,
        }
        try:
            analysis = analyze_figure_headings(
                dataset_root / item["image_path"],
                model,
                reader,
                device=device,
            )
        except Exception as error:
            error_text = f"{type(error).__name__}: {error}"
            errors.append({"item_id": item.get("item_id"), "error": error_text})
            for grouped_metrics in (bucket_metrics, source_bucket_metrics):
                grouped_metrics["analysis_error_count"] += 1
            prediction["error"] = error_text
            predictions.append(prediction)
            continue

        detected = analysis.get("detected_figure_numbers", [])
        automatic = analysis.get("auto_figure_numbers", [])
        orientation = analysis.get("orientation", {})
        detected_matches = _normalized_counter(detected) == _normalized_counter(expected)
        auto_matches = _normalized_counter(automatic) == _normalized_counter(expected)
        if is_rare_identifier_item:
            rare_detected_set_exact += int(detected_matches)
            rare_auto_mapping_exact += int(auto_matches)
        orientation_matches = True
        has_auto_decision = False
        negative_has_effect = False
        if is_positive:
            detected_set_exact += int(detected_matches)
            auto_mapping_exact += int(auto_matches)
            actual_correction = orientation.get("correction_degrees")
            actual_status = orientation.get("status")
            has_auto_decision = (
                actual_correction in {0, 90, 180, 270}
                and actual_status
                == (
                    "upright"
                    if actual_correction == 0
                    else "needs_rotation"
                )
            )
            orientation_matches = (
                actual_correction
                == (
                    int(expected_correction)
                    if expected_correction is not None
                    else None
                )
                and actual_status
                == item.get("expected_orientation_status")
            )
            orientation_exact += int(orientation_matches)
            auto_decision_count += int(has_auto_decision)
            correct_auto_decisions += int(
                has_auto_decision and orientation_matches
            )
            wrong_auto_decisions += int(
                has_auto_decision and not orientation_matches
            )
        else:
            negative_has_effect = bool(
                detected
                or automatic
                or orientation.get("correction_degrees") is not None
                or orientation.get("status") != "no_evidence"
            )
            negative_false_positives += int(negative_has_effect)

        for grouped_metrics in (bucket_metrics, source_bucket_metrics):
            grouped_metrics["detected_set_exact"] += int(
                is_positive and detected_matches
            )
            grouped_metrics["auto_mapping_exact"] += int(
                is_positive and auto_matches
            )
            grouped_metrics["orientation_exact"] += int(
                is_positive and orientation_matches
            )
            grouped_metrics["auto_decision_count"] += int(
                is_positive and has_auto_decision
            )
            grouped_metrics["correct_auto_decisions"] += int(
                is_positive and has_auto_decision and orientation_matches
            )
            grouped_metrics["wrong_auto_decisions"] += int(
                is_positive and has_auto_decision and not orientation_matches
            )
            grouped_metrics["negative_false_positives"] += int(
                not is_positive and negative_has_effect
            )
        prediction.update(
            {
                "detected_figure_numbers": list(detected),
                "auto_figure_numbers": list(automatic),
                "actual_orientation": dict(orientation),
                "mapping": dict(analysis.get("mapping") or {}),
                "figure_caption_detections": list(
                    analysis.get("figure_caption_detections") or []
                ),
                "prefix_detection_count": int(
                    analysis.get("prefix_detection_count", 0) or 0
                ),
                "identifier_detection_count": int(
                    analysis.get("identifier_detection_count", 0) or 0
                ),
                "strong_prefix_count": int(
                    analysis.get("strong_prefix_count", 0) or 0
                ),
                "detected_set_matches": bool(detected_matches),
                "auto_mapping_matches": bool(auto_matches),
                "orientation_matches": bool(orientation_matches),
                "negative_has_effect": bool(negative_has_effect),
            }
        )
        predictions.append(prediction)

        if (
            (
                is_positive
                and (
                    not detected_matches
                    or not auto_matches
                    or not orientation_matches
                )
            )
            or (not is_positive and negative_has_effect)
        ):
            errors.append(
                {
                    "item_id": item.get("item_id"),
                    "expected": expected,
                    "detected": detected,
                    "automatic": automatic,
                    "expected_correction_degrees": item.get(
                        "expected_correction_degrees"
                    ),
                    "actual_orientation": orientation,
                }
            )

    detected_accuracy = _rate(detected_set_exact, positive_items)
    mapping_accuracy = _rate(auto_mapping_exact, positive_items)
    orientation_accuracy = _rate(orientation_exact, orientation_eligible)
    abstentions = orientation_eligible - auto_decision_count
    auto_decision_precision = _rate(
        correct_auto_decisions,
        auto_decision_count,
    )
    negative_false_positive_rate = _rate(
        negative_false_positives,
        negative_items,
    )
    rare_detected_accuracy = _rate(
        rare_detected_set_exact,
        rare_identifier_items,
    )
    rare_mapping_accuracy = _rate(
        rare_auto_mapping_exact,
        rare_identifier_items,
    )
    positive_pages = len(positive_page_ids)
    negative_pages = len(negative_page_ids)
    positive_documents = len(positive_document_ids)
    negative_documents = len(negative_document_ids)
    rare_identifier_pages = len(rare_page_ids)
    rare_identifier_documents = len(rare_document_ids)
    passes = (
        not experimental
        and split == "test"
        and dataset_report.get("ready_for_training") is True
        and positive_items >= thresholds["minimum_positive_items"]
        and negative_items >= thresholds["minimum_negative_items"]
        and positive_pages >= thresholds["minimum_positive_pages"]
        and negative_pages >= thresholds["minimum_negative_pages"]
        and positive_documents >= thresholds["minimum_positive_documents"]
        and negative_documents >= thresholds["minimum_negative_documents"]
        and rare_identifier_items
        >= thresholds["minimum_rare_identifier_items"]
        and rare_identifier_pages
        >= thresholds["minimum_rare_identifier_pages"]
        and rare_identifier_documents
        >= thresholds["minimum_rare_identifier_documents"]
        and not any("error" in item for item in errors)
        and detected_accuracy >= thresholds["minimum_detected_set_accuracy"]
        and mapping_accuracy >= thresholds["minimum_auto_mapping_accuracy"]
        and orientation_accuracy >= thresholds["minimum_orientation_accuracy"]
        and rare_detected_accuracy
        >= thresholds["minimum_rare_detected_set_accuracy"]
        and rare_mapping_accuracy
        >= thresholds["minimum_rare_auto_mapping_accuracy"]
        and negative_false_positive_rate
        <= thresholds["maximum_negative_false_positive_rate"]
    )
    report = {
        "version": 2,
        "passes_release_thresholds": passes,
        "approved_for_production": False,
        "experimental": bool(experimental),
        "evaluation_mode": "experimental" if experimental else "release",
        "dataset_ready_for_training": bool(
            dataset_report.get("ready_for_training")
        ),
        "model": str(model_path),
        "model_sha256": _sha256(model_path),
        "dataset": str(dataset_root),
        "dataset_report_sha256": _sha256(dataset_report_path),
        "dataset_content_sha256": dataset_integrity["content_sha256"],
        "dataset_inventory_sha256": dataset_report[
            "dataset_inventory_sha256"
        ],
        "evaluated_split": split,
        "evaluated_items": len(evaluation_items),
        "test_items": len(evaluation_items) if split == "test" else 0,
        "positive_items": positive_items,
        "negative_items": negative_items,
        "positive_pages": positive_pages,
        "negative_pages": negative_pages,
        "positive_documents": positive_documents,
        "negative_documents": negative_documents,
        "rare_identifier_items": rare_identifier_items,
        "rare_identifier_pages": rare_identifier_pages,
        "rare_identifier_documents": rare_identifier_documents,
        "detected_set_exact_accuracy": detected_accuracy,
        "auto_mapping_exact_accuracy": mapping_accuracy,
        "orientation_accuracy": orientation_accuracy,
        "auto_decision_count": auto_decision_count,
        "correct_auto_decisions": correct_auto_decisions,
        "wrong_auto_decisions": wrong_auto_decisions,
        "abstentions": abstentions,
        "auto_decision_precision": auto_decision_precision,
        "rare_detected_set_exact_accuracy": rare_detected_accuracy,
        "rare_auto_mapping_exact_accuracy": rare_mapping_accuracy,
        "orientation_eligible_items": orientation_eligible,
        "negative_false_positive_rate": negative_false_positive_rate,
        "analysis_error_count": sum("error" in item for item in errors),
        "metrics_by_expected_correction": _finalize_correction_metrics(
            correction_metrics
        ),
        "metrics_by_source_correction": _finalize_correction_metrics(
            source_correction_metrics
        ),
        "thresholds": thresholds,
        "failures": errors,
        "predictions": predictions,
    }
    default_output_name = (
        f"{model_path.stem}_experimental_{split}.json"
        if experimental
        else f"{model_path.stem}_end_to_end_test.json"
    )
    output_path = Path(
        output_path
        or model_path.with_name(default_output_name)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main():
    args = parse_args()
    report = evaluate(
        args.dataset,
        args.model,
        output_path=args.output,
        experimental=args.experimental,
        split=args.split,
        minimum_positive_items=args.minimum_positive_items,
        minimum_negative_items=args.minimum_negative_items,
        minimum_positive_pages=args.minimum_positive_pages,
        minimum_negative_pages=args.minimum_negative_pages,
        minimum_positive_documents=args.minimum_positive_documents,
        minimum_negative_documents=args.minimum_negative_documents,
        minimum_rare_identifier_items=args.minimum_rare_identifier_items,
        minimum_rare_identifier_pages=args.minimum_rare_identifier_pages,
        minimum_rare_identifier_documents=args.minimum_rare_identifier_documents,
        minimum_detected_set_accuracy=args.minimum_detected_set_accuracy,
        minimum_auto_mapping_accuracy=args.minimum_auto_mapping_accuracy,
        minimum_orientation_accuracy=args.minimum_orientation_accuracy,
        minimum_rare_detected_set_accuracy=(
            args.minimum_rare_detected_set_accuracy
        ),
        minimum_rare_auto_mapping_accuracy=(
            args.minimum_rare_auto_mapping_accuracy
        ),
        maximum_negative_false_positive_rate=(
            args.maximum_negative_false_positive_rate
        ),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(
        0 if args.experimental or report["passes_release_thresholds"] else 1
    )


if __name__ == "__main__":
    main()
