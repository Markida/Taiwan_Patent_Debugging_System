"""Fine-tune the complete-label locator on reviewed gold supervision."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def configure_runtime_paths():
    root = Path(sys.executable).resolve().parent
    candidates = [
        root,
        root / "Library" / "bin",
        root / "Scripts",
        root / "bin",
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


DLL_HANDLES = configure_runtime_paths()
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("YOLO_CONFIG_DIR", str(Path.cwd() / ".ultralytics_ai"))

import torch
from ultralytics import YOLO


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_DATA = SCRIPT_DIR / "supervised_locator_v2_dataset" / "data.yaml"
DEFAULT_SPLIT_MANIFEST = SCRIPT_DIR / "supervised_locator_v2_dataset" / "split_manifest.json"
DEFAULT_BASE_MODEL = PROJECT_ROOT / "models" / "patent_label_group_v1.pt"
DEFAULT_EXPORT = PROJECT_ROOT / "models" / "patent_label_group_v2_gold_ft.pt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument(
        "--model",
        type=Path,
        default=DEFAULT_BASE_MODEL,
        help="Warm-start weights; defaults to the production v1 locator.",
    )
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--patience", type=int, default=12)
    parser.add_argument("--device")
    parser.add_argument("--project", type=Path, default=PROJECT_ROOT / "runs")
    parser.add_argument("--name", default="patent_label_group_v2_gold_ft")
    parser.add_argument("--export-model", type=Path, default=DEFAULT_EXPORT)
    return parser.parse_args()


def serializable_metrics(metrics) -> dict:
    values = {}
    for key, value in getattr(metrics, "results_dict", {}).items():
        try:
            values[str(key)] = float(value)
        except (TypeError, ValueError):
            values[str(key)] = str(value)
    return values


def main() -> int:
    args = parse_args()
    data = args.data.resolve()
    split_manifest = args.split_manifest.resolve()
    base_model = args.model.resolve()
    export = args.export_model.resolve()
    split = json.loads(split_manifest.read_text(encoding="utf-8"))
    if not split.get("ready_for_training"):
        raise RuntimeError("Supervised locator-v2 dataset QA is incomplete.")
    if split.get("leakage_checks", {}).get("publication_cross_split"):
        raise RuntimeError("Patent-level train/holdout leakage detected.")
    if not data.exists():
        raise FileNotFoundError(f"Supervised dataset is missing: {data}")
    if not base_model.exists():
        raise FileNotFoundError(f"Base locator is missing: {base_model}")

    device = args.device if args.device is not None else (0 if torch.cuda.is_available() else "cpu")
    model = YOLO(str(base_model))
    model.train(
        data=str(data),
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
        close_mosaic=8,
        hsv_h=0.005,
        hsv_s=0.04,
        hsv_v=0.06,
        seed=20260825,
        deterministic=True,
        amp=True,
        plots=True,
    )
    best = Path(model.trainer.best)
    if not best.exists():
        raise FileNotFoundError(f"Training completed without best.pt: {best}")
    export.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, export)

    trained = YOLO(str(export))
    metrics = trained.val(
        data=str(data),
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        workers=args.workers,
        plots=True,
    )
    report = {
        "model": str(export),
        "base_model": str(base_model),
        "v1_weights_reused": True,
        "supervision": "reviewed gold train split; no pseudo labels",
        "sealed_holdout_publications": split["holdout_publications"],
        "data": str(data),
        "epochs_requested": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "device": str(device),
        "rotation_augmentation": False,
        "metrics": serializable_metrics(metrics),
        "training_run": str(Path(model.trainer.save_dir)),
    }
    report_path = export.with_name(f"{export.stem}_metrics.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
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
        target = export.with_suffix(".onnx")
        if exported.resolve() != target.resolve():
            shutil.copy2(exported, target)
        report["onnx_model"] = str(target)
    except Exception as error:  # preserve usable PT output if optional export fails
        report["onnx_primary_export_error"] = f"{type(error).__name__}: {error}"
        fallback_python = PROJECT_ROOT.parent / "SantoOCR" / "runtime" / "python.exe"
        if fallback_python.exists() and fallback_python.resolve() != Path(sys.executable).resolve():
            target = export.with_suffix(".onnx")
            export_code = (
                "from ultralytics import YOLO; "
                f"YOLO({str(export)!r}).export(format='onnx', imgsz={args.imgsz}, "
                "opset=12, simplify=False, dynamic=False, device='cpu')"
            )
            fallback_env = os.environ.copy()
            fallback_env["YOLO_CONFIG_DIR"] = str(PROJECT_ROOT / ".ultralytics_export")
            try:
                subprocess.run(
                    [str(fallback_python), "-c", export_code],
                    cwd=PROJECT_ROOT,
                    env=fallback_env,
                    check=True,
                )
                if not target.exists():
                    raise FileNotFoundError(f"Fallback export did not create: {target}")
                report["onnx_model"] = str(target)
                report["onnx_export_runtime"] = str(fallback_python)
            except Exception as fallback_error:
                report["onnx_fallback_export_error"] = (
                    f"{type(fallback_error).__name__}: {fallback_error}"
                )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
