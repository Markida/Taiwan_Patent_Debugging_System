"""Keep limited-data pilots separate from production-qualified training."""

from pathlib import Path

from features.patent_ocr.figure_heading_classes import FIGURE_HEADING_CLASS_NAMES


def validate_training_policy(report, *, experimental=False, export_model=None, production_root=None):
    if not report.get("ready_for_training") and not experimental:
        raise RuntimeError("圖題資料尚未達正式訓練門檻；小樣本試訓練須明確指定 --experimental。")
    if not experimental:
        return
    if not report.get("document_level_split_preserved") or report.get("cross_split_duplicate_images") != 0:
        raise RuntimeError("試訓練仍須遵守文件級分割與影像不跨集合的規則。")
    if report.get("classes") != {name: index for index, name in enumerate(FIGURE_HEADING_CLASS_NAMES)}:
        raise RuntimeError("試訓練資料的三個圖題類別順序不正確。")
    for split in ("train", "val", "test"):
        if int(report.get("counts", {}).get(f"{split}_images", 0)) <= 0:
            raise RuntimeError(f"試訓練的 {split} 集合不得為空。")
    if not report.get("dataset_content_sha256") or not report.get("dataset_inventory_sha256"):
        raise RuntimeError("試訓練資料必須先凍結內容與檔案清單。")
    if export_model is not None and production_root is not None:
        if Path(export_model).resolve().is_relative_to(Path(production_root).resolve()):
            raise RuntimeError("試訓練模型不得輸出至正式 models 目錄，請使用獨立 candidates 目錄。")
