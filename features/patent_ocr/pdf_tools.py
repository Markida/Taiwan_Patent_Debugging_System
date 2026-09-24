from pathlib import Path

from app.paths import get_output_base_dir


def convert_pdf_to_images(pdf_path, output_root=None, dpi=300, *, cancel_event=None):
    """
    將 PDF 每一頁轉成 PNG 圖片。
    回傳轉出的圖片路徑 list。
    """

    import pymupdf

    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"找不到 PDF 檔案：{pdf_path}")

    if output_root is None:
        output_root = get_output_base_dir() / "pdf_pages"

    output_root = Path(output_root)
    output_dir = output_root / pdf_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    image_paths = []

    try:
        with pymupdf.open(str(pdf_path)) as doc:
            if len(doc) == 0:
                raise ValueError("PDF 頁數為 0，無法轉換。")

            for page_index in range(len(doc)):
                if cancel_event is not None and cancel_event.is_set():
                    return []
                page = doc[page_index]
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                output_path = output_dir / f"{pdf_path.stem}_page_{page_index + 1:03d}.png"
                pix.save(str(output_path))
                if not output_path.exists():
                    raise RuntimeError(
                        f"PDF 第 {page_index + 1} 頁轉圖失敗：{output_path}"
                    )
                image_paths.append(str(output_path))

    except Exception as e:
        raise RuntimeError(
            f"PDF 轉圖失敗。\n"
            f"PDF：{pdf_path}\n"
            f"輸出資料夾：{output_dir}\n"
            f"錯誤原因：{e}"
        )

    return image_paths
