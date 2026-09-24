"""Train and export the dedicated three-class figure-heading locator."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def configure_runtime_paths():
    runtime_root = Path(sys.executable).resolve().parent
    candidates = [
        runtime_root,
        runtime_root / "Library" / "bin",
        runtime_root / "Scripts",
        runtime_root / "bin",
    ]
    existing = [str(path) for path in candidates if path.exists()]
    if existing:
        os.environ["PATH"] = os.pathsep.join(
            existing + [os.environ.get("PATH", "")]
        )
    handles = []
    if hasattr(os, "add_dll_directory"):
        for path in candidates:
            if path.exists():
                handles.append(os.add_dll_directory(str(path)))
    return handles


DLL_HANDLES = configure_runtime_paths()
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import torch
from ultralytics import YOLO

from features.patent_ocr.figure_heading_classes import FIGURE_HEADING_CLASS_NAMES
from training.figure_heading.dataset_integrity import sha256_file, verify_dataset_inventory
from training.figure_heading.training_policy import validate_training_policy


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_DATA = SCRIPT_DIR / "figure_heading_yolo_v1" / "data.yaml"
DEFAULT_REPORT = SCRIPT_DIR / "figure_heading_yolo_v1" / "dataset_report.json"
DEFAULT_BASE_MODEL = PROJECT_ROOT / "models" / "patent_label_group_v2_gold_ft.pt"
DEFAULT_EXPORT = SCRIPT_DIR / "candidates" / "figure_heading_locator_v1.pt"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--dataset-report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--model", type=Path, default=DEFAULT_BASE_MODEL)
    parser.add_argument(
        "--resume-checkpoint",
        type=Path,
        help="Resume an interrupted Ultralytics run from its last.pt checkpoint.",
    )
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--device")
    parser.add_argument("--project", type=Path, default=PROJECT_ROOT / "runs")
    parser.add_argument("--name", default="figure_heading_locator_v1")
    parser.add_argument("--export-model", type=Path, default=DEFAULT_EXPORT)
    parser.add_argument("--experimental", action="store_true", help="Train a limited-data candidate without production approval.")
    parser.add_argument("--skip-test", action="store_true", help="Leave the frozen test set for a separate final evaluation.")
    parser.add_argument("--no-amp", action="store_true", help="Disable AMP and its external check-model dependency.")
    return parser.parse_args()


def _serializable_metrics(metrics):
    output = {}
    for key, value in getattr(metrics, "results_dict", {}).items():
        try:
            output[str(key)] = float(value)
        except (TypeError, ValueError):
            output[str(key)] = str(value)
    return output


def main():
    args = parse_args()
    data_path = args.data.resolve()
    report_path = args.dataset_report.resolve()
    base_model = args.model.resolve()
    resume_checkpoint = (
        args.resume_checkpoint.resolve() if args.resume_checkpoint else None
    )
    export_model = args.export_model.resolve()
    if not data_path.exists():
        raise FileNotFoundError(f"找不到圖題訓練資料：{data_path}")
    if not report_path.exists():
        raise FileNotFoundError(f"找不到資料集檢查報告：{report_path}")
    dataset_report = json.loads(report_path.read_text(encoding="utf-8"))
    validate_training_policy(
        dataset_report, experimental=args.experimental, export_model=export_model,
        production_root=PROJECT_ROOT / "models",
    )
    run_dir = (args.project / args.name).resolve()
    if export_model.exists():
        raise FileExistsError("候選模型已存在；請指定新的名稱，以保留前次實驗。")
    if resume_checkpoint is None and run_dir.exists():
        raise FileExistsError("訓練輸出已存在；請指定新的名稱，以保留前次實驗。")
    if resume_checkpoint is not None:
        expected_checkpoint = run_dir / "weights" / "last.pt"
        if not resume_checkpoint.exists():
            raise FileNotFoundError(f"找不到續訓檢查點：{resume_checkpoint}")
        if resume_checkpoint != expected_checkpoint:
            raise ValueError(
                "續訓檢查點必須是指定 project/name 下的 weights/last.pt："
                f"{expected_checkpoint}"
            )
    dataset_integrity = verify_dataset_inventory(
        data_path.parent,
        expected_content_sha256=dataset_report.get("dataset_content_sha256"),
        expected_inventory_sha256=dataset_report.get(
            "dataset_inventory_sha256"
        ),
    )
    if not base_model.exists():
        raise FileNotFoundError(f"找不到暖啟動模型：{base_model}")

    device = (
        args.device
        if args.device is not None
        else (0 if torch.cuda.is_available() else "cpu")
    )
    if resume_checkpoint is not None:
        model = YOLO(str(resume_checkpoint))
        model.train(resume=True, device=device)
    else:
        model = YOLO(str(base_model))
        model.train(
            data=str(data_path),
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            device=device,
            project=str(args.project.resolve()),
            name=args.name,
            workers=args.workers,
            patience=args.patience,
            degrees=3.0,
            translate=0.03,
            scale=0.10,
            shear=1.0,
            perspective=0.0,
            flipud=0.0,
            fliplr=0.0,
            mosaic=0.15,
            close_mosaic=8,
            mixup=0.0,
            copy_paste=0.0,
            hsv_h=0.005,
            hsv_s=0.03,
            hsv_v=0.05,
            seed=20260911,
            deterministic=True,
            amp=not args.no_amp,
            plots=True,
        )
    best = Path(model.trainer.best)
    if not best.exists():
        raise FileNotFoundError(f"訓練完成但找不到 best.pt：{best}")
    export_model.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, export_model)

    trained = YOLO(str(export_model))
    metrics = trained.val(
        data=str(data_path),
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        workers=args.workers,
        plots=True,
    )
    test_metrics = None if args.skip_test else trained.val(
        data=str(data_path),
        split="test",
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        workers=args.workers,
        plots=True,
    )
    report = {
        "model": str(export_model),
        "base_model": str(base_model),
        "base_model_sha256": sha256_file(base_model),
        "resumed_from": str(resume_checkpoint) if resume_checkpoint else None,
        "data": str(data_path),
        "dataset_report": dataset_report,
        "verified_dataset_content_sha256": dataset_integrity[
            "content_sha256"
        ],
        "epochs_requested": args.epochs,
        "epochs_completed": int(model.trainer.epoch) + 1,
        "seed": 20260911,
        "amp": not args.no_amp,
        "command_arguments": sys.argv[1:],
        "imgsz": args.imgsz,
        "batch": args.batch,
        "device": str(device),
        "classes": list(FIGURE_HEADING_CLASS_NAMES),
        "right_angle_augmentation": "offline 0/90/180/270",
        "small_skew_augmentation_degrees": 3.0,
        "validation_metrics": _serializable_metrics(metrics),
        "test_metrics": _serializable_metrics(test_metrics) if test_metrics is not None else None,
        "approved_for_production": False,
        "experimental": bool(args.experimental),
        "frozen_test_evaluated": not args.skip_test,
        "identifier_ground_truth_semantics": "Identifier text is read AFTER applying the caption correction clockwise; never train sideways text as upright.",
        "activation_note": (
            "候選模型不會由應用程式自動載入；須另經端到端圖號與方向"
            "驗證後才能發布到 models。"
        ),
        "training_run": str(Path(model.trainer.save_dir)),
    }
    try:
        exported = Path(
            trained.export(
                format="onnx",
                imgsz=args.imgsz,
                opset=17,
                simplify=False,
                dynamic=False,
                device=device,
            )
        )
        onnx_target = export_model.with_suffix(".onnx")
        if exported.resolve() != onnx_target.resolve():
            shutil.copy2(exported, onnx_target)
        names_path = onnx_target.with_suffix(".names.json")
        names_path.write_text(
            json.dumps(
                {"names": list(FIGURE_HEADING_CLASS_NAMES)},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        report["onnx_model"] = str(onnx_target)
        report["onnx_names"] = str(names_path)
    except Exception as error:
        report["onnx_export_error"] = f"{type(error).__name__}: {error}"

    metrics_path = export_model.with_name(f"{export_model.stem}_metrics.json")
    metrics_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
