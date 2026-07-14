from pathlib import Path

from app.paths import get_easyocr_model_dir
from features.patent_ocr.compact_ocr_reader import CompactEnglishReader


def create_easyocr_reader(ocr_gpu):
    """
    建立 EasyOCR Reader。

    強制使用 App 資料夾底下的 easyocr_models，
    避免同事電腦第一次啟動時自動下載模型。
    """

    easyocr_model_dir = get_easyocr_model_dir()

    if not easyocr_model_dir.exists():
        raise RuntimeError(
            f"找不到 EasyOCR 模型資料夾：{easyocr_model_dir}\n\n"
            f"請確認 SantoPatentOCR.exe 或 main.py 同層資料夾內有 easyocr_models。\n"
            f"資料夾內至少應包含 english_g2.pth。"
        )

    required_model_files = ["english_g2.pth"]

    missing_files = [
        file_name
        for file_name in required_model_files
        if not (easyocr_model_dir / file_name).exists()
    ]

    if missing_files:
        raise RuntimeError(
            f"EasyOCR 模型檔不完整。\n\n"
            f"模型資料夾：{easyocr_model_dir}\n"
            f"缺少檔案：{', '.join(missing_files)}\n\n"
            f"請從開發電腦的 {Path.home() / '.EasyOCR' / 'model'} "
            f"複製缺少的 .pth 檔案到 easyocr_models。"
        )

    reader = CompactEnglishReader(
        easyocr_model_dir / "english_g2.pth",
        gpu=ocr_gpu,
    )

    return reader
