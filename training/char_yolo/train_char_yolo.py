"""訓練 0-9 / A-Z / prime 字元級 YOLO 模型。"""

import argparse
import os
import shutil
import sys
from pathlib import Path


def configure_conda_dll_paths():
    """直接執行 Conda python.exe 時補上 Windows 原生 DLL 搜尋路徑。"""

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

    dll_handles = []
    if hasattr(os, "add_dll_directory"):
        for path in candidates:
            if path.exists():
                dll_handles.append(os.add_dll_directory(str(path)))

    return dll_handles


CONDA_DLL_HANDLES = configure_conda_dll_paths()
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import torch
from ultralytics import YOLO


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent

DEFAULT_DATA = SCRIPT_DIR / "dataset" / "data.yaml"
DEFAULT_PATENT_MODEL = (
    WORKSPACE_ROOT / "runs" / "patent_number_v1-6" / "weights" / "best.pt"
)
DEFAULT_BASE_MODEL = (
    str(DEFAULT_PATENT_MODEL) if DEFAULT_PATENT_MODEL.exists() else "yolov8n.pt"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="訓練專利圖式的 37 類字元 YOLO 模型。"
    )
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--model", default=DEFAULT_BASE_MODEL)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--device", default=None)
    parser.add_argument("--project", type=Path, default=WORKSPACE_ROOT / "runs")
    parser.add_argument("--name", default="patent_char_v1")
    parser.add_argument("--patience", type=int, default=25)
    parser.add_argument(
        "--amp",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="啟用或停用 Automatic Mixed Precision。",
    )
    parser.add_argument(
        "--plots",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="啟用或停用 Ultralytics 訓練圖表。",
    )
    parser.add_argument(
        "--export-model",
        type=Path,
        default=PROJECT_ROOT / "models" / "patent_char_v1.pt",
    )
    return parser.parse_args()


def choose_device(requested):
    if requested:
        return requested

    return 0 if torch.cuda.is_available() else "cpu"


def main():
    args = parse_args()
    data_path = args.data.resolve()

    if not data_path.exists():
        raise FileNotFoundError(
            f"找不到資料設定：{data_path}\n"
            "請先執行 prepare_dataset.py。"
        )

    device = choose_device(args.device)
    print(f"Torch：{torch.__version__}")
    print(f"CUDA 可用：{torch.cuda.is_available()}")
    print(f"訓練裝置：{device}")
    if torch.cuda.is_available():
        print(f"GPU：{torch.cuda.get_device_name(0)}")

    model = YOLO(args.model)
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
        degrees=5.0,
        translate=0.05,
        scale=0.25,
        shear=2.0,
        perspective=0.0002,
        flipud=0.0,
        fliplr=0.0,
        mosaic=0.25,
        close_mosaic=10,
        seed=20260713,
        deterministic=True,
        amp=args.amp,
        plots=args.plots,
    )

    save_dir = Path(model.trainer.save_dir)
    best_model = Path(model.trainer.best)

    if not best_model.exists():
        raise FileNotFoundError(f"訓練完成但找不到 best.pt：{best_model}")

    args.export_model.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best_model, args.export_model)

    class_map_source = data_path.parent / "class_map.json"
    if class_map_source.exists():
        shutil.copy2(
            class_map_source,
            args.export_model.parent / "class_map.json",
        )

    print(f"最佳模型：{best_model}")
    print(f"已匯出模型：{args.export_model}")


if __name__ == "__main__":
    main()
