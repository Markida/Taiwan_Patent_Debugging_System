"""Run the figure-heading orientation pass before component-number OCR."""

from pathlib import Path

from features.patent_ocr.model_loader import load_detection_model


def auto_orient_figure_images(image_paths, heading_model_path, *, cancel_event=None, progress=None):
    """Return per-page corrected paths without modifying any source image.

    An ambiguous or failed page is kept unchanged for manual review.  The
    caller commits the returned paths only after the whole pass completes.
    """

    from features.patent_ocr.easyocr_loader import create_easyocr_reader
    from features.patent_ocr.figure_heading import (
        analyze_figure_headings,
        empty_figure_heading_analysis,
        is_figure_heading_model,
    )
    from features.patent_ocr.image_tools import create_auto_oriented_image
    from features.patent_ocr.ocr_engine import get_compute_device, get_easyocr_gpu_flag

    model = load_detection_model(Path(heading_model_path))
    if not is_figure_heading_model(model):
        raise RuntimeError("圖題方向模型類別不正確，無法執行自動旋轉。")
    reader = create_easyocr_reader(get_easyocr_gpu_flag())
    pages = []
    total = len(image_paths)
    for index, source in enumerate(image_paths, 1):
        if cancel_event is not None and cancel_event.is_set():
            return None
        source = Path(source)
        if progress is not None:
            progress(f"[{index}/{total}] 正在判斷方向：{source.name}")
        try:
            analysis = analyze_figure_headings(
                image_path=source,
                model=model,
                reader=reader,
                device=get_compute_device(),
            )
            orientation = analysis.get("orientation") or {}
            degrees = orientation.get("correction_degrees")
            if orientation.get("status") == "needs_rotation" and degrees in {90, 180, 270}:
                effective = create_auto_oriented_image(source, correction_degrees=int(degrees))
                applied = int(degrees)
            else:
                effective = str(source)
                applied = 0
        except Exception as exc:
            analysis = empty_figure_heading_analysis("analysis_error")
            analysis["error"] = str(exc)
            effective = str(source)
            applied = 0
        pages.append({
            "original_image_path": str(source),
            "image_path": str(effective),
            "rotation_degrees": applied,
            "figure_heading": analysis,
        })
    if cancel_event is not None and cancel_event.is_set():
        return None
    return pages
