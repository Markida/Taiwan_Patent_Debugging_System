"""Fine-tune the proven one-class character locator on real patent pages."""

import argparse
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

    if hasattr(os, "add_dll_directory"):
        return [
            os.add_dll_directory(str(path))
            for path in candidates
            if path.exists()
        ]

    return []


CONDA_DLL_HANDLES = configure_conda_dll_paths()
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("YOLO_CONFIG_DIR", os.environ.get("TEMP", str(Path.cwd())))

import torch
from ultralytics import YOLO


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent


def parse_args():
    parser = argparse.ArgumentParser(description="Fine-tune the patent character locator.")
    parser.add_argument("--data", type=Path, default=SCRIPT_DIR / "data.yaml")
    parser.add_argument("--model", type=Path, default=PROJECT_ROOT / "models" / "best.pt")
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--device", default=None)
    parser.add_argument("--optimizer", default="SGD")
    parser.add_argument("--lr0", type=float, default=0.001)
    parser.add_argument("--degrees", type=float, default=3.0)
    parser.add_argument(
        "--resume-from",
        type=Path,
        default=None,
        help="Resume an interrupted Ultralytics run from its last.pt checkpoint.",
    )
    parser.add_argument("--name", default="patent_number_v2_candidate")
    parser.add_argument(
        "--export-model",
        type=Path,
        default=PROJECT_ROOT / "models" / "best_v2.pt",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    device = args.device or (0 if torch.cuda.is_available() else "cpu")
    if args.resume_from:
        model = YOLO(str(args.resume_from.resolve()))
        model.train(resume=True)
    else:
        model = YOLO(str(args.model.resolve()))
        model.train(
            data=str(args.data.resolve()),
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            device=device,
            workers=0,
            project=str((WORKSPACE_ROOT / "runs").resolve()),
            name=args.name,
            patience=15,
            optimizer=args.optimizer,
            lr0=args.lr0,
            lrf=0.01,
            degrees=args.degrees,
            translate=0.05,
            scale=0.15,
            shear=0.0,
            perspective=0.0,
            flipud=0.0,
            fliplr=0.0,
            mosaic=0.0,
            seed=20260713,
            deterministic=True,
            amp=True,
            plots=True,
        )

    best_path = Path(model.trainer.best)

    if not best_path.exists():
        raise FileNotFoundError(f"Training did not produce best.pt: {best_path}")

    args.export_model.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best_path, args.export_model)
    print(f"Candidate exported to: {args.export_model}")


if __name__ == "__main__":
    main()
