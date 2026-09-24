"""Promote a passing candidate model into the application model directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from features.patent_ocr.figure_heading_classes import FIGURE_HEADING_CLASS_NAMES
from training.figure_heading.dataset_integrity import verify_dataset_inventory
from training.figure_heading.evaluate_candidate import (
    release_report_passes,
    validate_release_thresholds,
)


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_MODEL = SCRIPT_DIR / "candidates" / "figure_heading_locator_v1.onnx"
DEFAULT_EVALUATION = (
    SCRIPT_DIR / "candidates" / "figure_heading_locator_v1_end_to_end_test.json"
)
PRODUCTION_MODEL_NAME = "figure_heading_locator_v1.onnx"
RELEASE_MANIFEST_NAME = "figure_heading_locator_v1.release.json"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--evaluation", type=Path, default=DEFAULT_EVALUATION)
    parser.add_argument("--models-dir", type=Path, default=PROJECT_ROOT / "models")
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Required explicit confirmation that this passing candidate may be activated.",
    )
    return parser.parse_args()


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def promote(model_path, evaluation_path, models_dir, *, approve=False):
    model_path = Path(model_path).resolve()
    evaluation_path = Path(evaluation_path).resolve()
    models_dir = Path(models_dir).resolve()
    if not approve:
        raise RuntimeError("必須明確加上 --approve 才能發布並啟用候選模型。")
    if not model_path.is_file() or not evaluation_path.is_file():
        raise FileNotFoundError("找不到候選模型或端到端測試報告。")
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    try:
        report_passes = release_report_passes(evaluation)
    except RuntimeError as error:
        raise RuntimeError(f"端到端測試報告無效：{error}") from error
    if (
        evaluation.get("passes_release_thresholds") is not True
        or not report_passes
    ):
        raise RuntimeError("候選模型未通過全部發布門檻，不得啟用。")
    if Path(str(evaluation.get("model") or "")).resolve() != model_path:
        raise RuntimeError("端到端測試報告不是針對這個候選模型產生。")
    thresholds = validate_release_thresholds(evaluation.get("thresholds"))
    model_digest = _sha256(model_path)
    if model_digest != str(evaluation.get("model_sha256") or "").lower():
        raise RuntimeError("候選模型與端到端測試報告的 SHA-256 不一致。")
    dataset_root = Path(evaluation["dataset"])
    dataset_report_path = dataset_root / "dataset_report.json"
    if (
        not dataset_report_path.is_file()
        or _sha256(dataset_report_path)
        != evaluation.get("dataset_report_sha256")
    ):
        raise RuntimeError("正式資料集報告已變更，必須重新執行端到端測試。")
    dataset_report = json.loads(dataset_report_path.read_text(encoding="utf-8"))
    dataset_integrity = verify_dataset_inventory(
        dataset_root,
        expected_content_sha256=evaluation.get("dataset_content_sha256"),
        expected_inventory_sha256=evaluation.get(
            "dataset_inventory_sha256"
        ),
    )
    if dataset_integrity["content_sha256"] != dataset_report.get(
        "dataset_content_sha256"
    ):
        raise RuntimeError("資料集內容與正式建立報告不一致。")

    names_source = model_path.with_suffix(".names.json")
    if not names_source.is_file():
        raise FileNotFoundError(f"找不到候選模型類別檔：{names_source}")
    names = json.loads(names_source.read_text(encoding="utf-8"))
    if names.get("names") != list(FIGURE_HEADING_CLASS_NAMES):
        raise RuntimeError("候選模型類別檔內容不正確。")

    production_model = models_dir / PRODUCTION_MODEL_NAME
    production_names = production_model.with_suffix(".names.json")
    release_manifest = models_dir / RELEASE_MANIFEST_NAME
    existing = [
        path
        for path in (production_model, production_names, release_manifest)
        if path.exists()
    ]
    if existing:
        raise FileExistsError(
            "正式圖題模型已存在，為避免覆寫不會繼續："
            + "、".join(str(path) for path in existing)
        )

    models_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model_path, production_model)
    shutil.copy2(names_source, production_names)
    manifest = {
        "version": 1,
        "approved_for_production": True,
        "approved_at_utc": datetime.now(timezone.utc).isoformat(),
        "classes": list(FIGURE_HEADING_CLASS_NAMES),
        "artifacts": {
            production_model.name: {"sha256": _sha256(production_model)},
            production_names.name: {"sha256": _sha256(production_names)},
        },
        "evaluation_report": str(evaluation_path),
        "evaluation_report_sha256": _sha256(evaluation_path),
        "dataset_report_sha256": evaluation["dataset_report_sha256"],
        "release_metrics": {
            key: evaluation[key]
            for key in (
                "detected_set_exact_accuracy",
                "auto_mapping_exact_accuracy",
                "orientation_accuracy",
                "rare_detected_set_exact_accuracy",
                "rare_auto_mapping_exact_accuracy",
                "negative_false_positive_rate",
            )
        },
        "thresholds": thresholds,
    }
    temporary_manifest = release_manifest.with_suffix(".json.pending")
    temporary_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_manifest.replace(release_manifest)
    return manifest


def main():
    args = parse_args()
    manifest = promote(
        args.model,
        args.evaluation,
        args.models_dir,
        approve=args.approve,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
