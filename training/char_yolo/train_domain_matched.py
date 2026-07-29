"""Train and export the domain-matched 37-class patent character model."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def configure_conda_dll_paths():
    environment_root = Path(sys.executable).resolve().parent
    candidates = [
        environment_root,
        environment_root / "Library" / "mingw-w64" / "bin",
        environment_root / "Library" / "usr" / "bin",
        environment_root / "Library" / "bin",
        environment_root / "Scripts",
        environment_root / "bin",
    ]
    existing = [str(path) for path in candidates if path.exists()]
    if existing:
        os.environ["PATH"] = os.pathsep.join(existing + [os.environ.get("PATH", "")])
    handles = []
    if hasattr(os, "add_dll_directory"):
        for path in candidates:
            if path.exists():
                handles.append(os.add_dll_directory(str(path)))
    return handles


CONDA_DLL_HANDLES = configure_conda_dll_paths()
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
YOLO_CONFIG_DIR = Path.cwd() / ".ultralytics_ai"
YOLO_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(YOLO_CONFIG_DIR))

import torch
from ultralytics import YOLO


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_DATA = SCRIPT_DIR / "dataset_v2_domain_matched" / "data.yaml"
DEFAULT_BASE_MODEL = PROJECT_ROOT / "models" / "patent_char_v1.pt"
DEFAULT_EXPORT_MODEL = PROJECT_ROOT / "models" / "patent_char_v2_domain.pt"


def parse_args():
    parser = argparse.ArgumentParser(description="Train the domain-matched patent OCR model.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--model", default=str(DEFAULT_BASE_MODEL))
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--device", default=None)
    parser.add_argument("--project", type=Path, default=PROJECT_ROOT / "runs")
    parser.add_argument("--name", default="patent_char_v2_domain")
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--export-model", type=Path, default=DEFAULT_EXPORT_MODEL)
    parser.add_argument(
        "--export-onnx",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    return parser.parse_args()


def serializable_metrics(metrics):
    result = {}
    for key, value in getattr(metrics, "results_dict", {}).items():
        try:
            result[str(key)] = float(value)
        except (TypeError, ValueError):
            result[str(key)] = str(value)
    return result


def main():
    args = parse_args()
    data_path = args.data.resolve()
    export_path = args.export_model.resolve()
    if not data_path.exists():
        raise FileNotFoundError(f"Dataset YAML not found: {data_path}")
    model_path = Path(args.model)
    if not model_path.exists():
        raise FileNotFoundError(f"Base model not found: {model_path}")

    device = args.device if args.device is not None else (0 if torch.cuda.is_available() else "cpu")
    print(f"Torch: {torch.__version__}")
    print(f"CUDA available: {torch.cuda.is_available()}")
    print(f"Training device: {device}")
    if torch.cuda.is_available():
        print(f"GPU: {torch.cuda.get_device_name(0)}")

    model = YOLO(str(model_path))
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
        degrees=0.0,
        translate=0.02,
        scale=0.08,
        shear=0.0,
        perspective=0.0,
        flipud=0.0,
        fliplr=0.0,
        mosaic=0.15,
        close_mosaic=10,
        hsv_h=0.005,
        hsv_s=0.05,
        hsv_v=0.08,
        seed=20260716,
        deterministic=True,
        amp=True,
        plots=True,
    )

    best_model = Path(model.trainer.best)
    if not best_model.exists():
        raise FileNotFoundError(f"Training finished but best.pt is missing: {best_model}")
    export_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best_model, export_path)
    class_map_source = data_path.parent / "class_map.json"
    if class_map_source.exists():
        shutil.copy2(class_map_source, export_path.parent / "class_map.json")

    validation_model = YOLO(str(export_path))
    metrics = validation_model.val(
        data=str(data_path),
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        workers=args.workers,
        plots=True,
    )
    report = {
        "model": str(export_path),
        "base_model": str(model_path.resolve()),
        "data": str(data_path),
        "epochs_requested": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "device": str(device),
        "rotation_augmentation": False,
        "metrics": serializable_metrics(metrics),
        "training_run": str(Path(model.trainer.save_dir)),
    }

    # Persist the successful training/validation result before optional exports.
    # Export dependencies are deliberately kept separate from the GPU training
    # environment, so a missing ONNX package must never hide a usable PT model.
    report_path = export_path.with_name(f"{export_path.stem}_metrics.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.export_onnx:
        try:
            exported = validation_model.export(
                format="onnx",
                imgsz=args.imgsz,
                opset=17,
                simplify=False,
                dynamic=False,
                device=device,
            )
            exported_path = Path(exported)
            target_onnx = export_path.with_suffix(".onnx")
            if exported_path.resolve() != target_onnx.resolve():
                shutil.copy2(exported_path, target_onnx)
            report["onnx_model"] = str(target_onnx)
        except Exception as exc:  # noqa: BLE001 - report export failures without losing PT metrics
            report["onnx_export_error"] = f"{type(exc).__name__}: {exc}"
            print(f"ONNX export skipped: {report['onnx_export_error']}")

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
