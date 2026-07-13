"""Import additional real images or PDF pages into the annotation set."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

try:
    from .common import make_page_record, stable_split, write_page_record
except ImportError:
    from common import make_page_record, stable_split, write_page_record


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def _source_digest(path):
    return hashlib.sha1(str(Path(path).resolve()).encode("utf-8")).hexdigest()[:12]


def _write_imported_page(
    image,
    source_path,
    page_number,
    annotation_root,
    split,
):
    annotation_root = Path(annotation_root)
    digest = _source_digest(source_path)
    page_id = f"manual_{digest}_{page_number:04d}"
    image_relative = Path("images") / split / f"{page_id}.png"
    label_relative = Path("labels") / split / f"{page_id}.json"
    image_path = annotation_root / image_relative
    label_path = annotation_root / label_relative

    if label_path.exists():
        return label_path, False

    image_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(image_path, format="PNG", optimize=True)
    record = make_page_record(
        page_id=page_id,
        split=split,
        image_path=image_relative,
        source_path=source_path,
        image_width=image.width,
        image_height=image.height,
        annotations=[],
        reviewed=False,
    )
    write_page_record(label_path, record)
    return label_path, True


def import_source(path, annotation_root, dpi=300, val_ratio=0.2):
    path = Path(path).resolve()
    annotation_root = Path(annotation_root).resolve()

    if not path.exists():
        raise FileNotFoundError(path)

    # Keep all pages belonging to one PDF/patent folder in the same split.
    group_key = path if path.suffix.lower() == ".pdf" else path.parent
    split = stable_split(group_key, val_ratio=val_ratio)
    imported = []

    if path.suffix.lower() == ".pdf":
        import fitz

        scale = dpi / 72.0
        with fitz.open(path) as document:
            for page_index, page in enumerate(document, start=1):
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(scale, scale),
                    colorspace=fitz.csRGB,
                    alpha=False,
                )
                image = Image.frombytes(
                    "RGB",
                    (pixmap.width, pixmap.height),
                    pixmap.samples,
                )
                label_path, created = _write_imported_page(
                    image,
                    source_path=path,
                    page_number=page_index,
                    annotation_root=annotation_root,
                    split=split,
                )
                if created:
                    imported.append(label_path)
        return imported

    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f"Unsupported source file: {path}")

    with Image.open(path) as image:
        label_path, created = _write_imported_page(
            image,
            source_path=path,
            page_number=1,
            annotation_root=annotation_root,
            split=split,
        )
    return [label_path] if created else []


def import_sources(paths, annotation_root, dpi=300, val_ratio=0.2):
    imported = []
    for path in paths:
        imported.extend(
            import_source(
                path,
                annotation_root=annotation_root,
                dpi=dpi,
                val_ratio=val_ratio,
            )
        )
    return imported
