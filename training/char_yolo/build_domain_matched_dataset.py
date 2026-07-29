"""Build a domain-matched 37-class patent-character dataset.

The validation split is copied only from manually reviewed real pages.  The
training split combines reviewed pages, exact PDF text-layer glyphs, embedded
PDF fonts, reviewed crop feedback, conservative photometric augmentation, and
hard negatives.  No geometric rotation augmentation is used.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import cv2
import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

try:
    from training.manual_annotation.build_training_datasets import (
        load_annotation_state,
        load_page_rotations,
        rotate_page,
    )
    from training.manual_annotation.common import (
        Annotation,
        CLASS_NAMES,
        CLASS_TO_ID,
        normalize_label,
    )
except ImportError:
    import sys

    PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORT))
    from training.manual_annotation.build_training_datasets import (
        load_annotation_state,
        load_page_rotations,
        rotate_page,
    )
    from training.manual_annotation.common import (
        Annotation,
        CLASS_NAMES,
        CLASS_TO_ID,
        normalize_label,
    )


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
DEFAULT_PDF_SOURCE = WORKSPACE_ROOT / "pdf-100"
DEFAULT_ANNOTATIONS = PROJECT_ROOT / "training" / "manual_annotation" / "annotation_set"
DEFAULT_CROP_MANIFEST = (
    PROJECT_ROOT
    / "training"
    / "crop_classifier"
    / "review_dataset"
    / "manifest.csv"
)
DEFAULT_ERROR_ROOT = PROJECT_ROOT / "outputs"
DEFAULT_OUTPUT = SCRIPT_DIR / "dataset_v2_domain_matched"
DISPLAY_MAP = {"prime": "'"}
REQUIRED_PHRASES = ("A", "B", "10A", "IV", "VII", "VIII", "7'", "8'", "9'")


@dataclass
class Glyph:
    label: str
    mask: Image.Image
    normalized_height: float
    source: str


@dataclass
class BackgroundPage:
    image_path: Path
    annotations: tuple[Annotation, ...]
    page_id: str


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build a real-scale 0-9/A-Z/prime YOLO training dataset."
    )
    parser.add_argument("--pdf-source", type=Path, default=DEFAULT_PDF_SOURCE)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--crop-manifest", type=Path, default=DEFAULT_CROP_MANIFEST)
    parser.add_argument("--error-root", type=Path, default=DEFAULT_ERROR_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--composite-count", type=int, default=370)
    parser.add_argument("--hard-negative-count", type=int, default=60)
    parser.add_argument("--augment-copies", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260716)
    parser.add_argument("--limit-pdfs", type=int, default=None)
    return parser.parse_args()


def ensure_empty_output(output_root):
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(
            f"Output directory is not empty: {output_root}\n"
            "Choose a new --output path to avoid mixing dataset versions."
        )
    for split in ("train", "val"):
        (output_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (output_root / "labels" / split).mkdir(parents=True, exist_ok=True)
    (output_root / "previews").mkdir(parents=True, exist_ok=True)
    (output_root / "provenance" / "fonts").mkdir(parents=True, exist_ok=True)


def display_label(label):
    return DISPLAY_MAP.get(label, label)


def annotation_line(annotation, width, height):
    return annotation.to_yolo(width, height, class_id=CLASS_TO_ID[annotation.label])


def write_sample(
    output_root,
    split,
    stem,
    image,
    annotations,
    source_kind,
    report_state,
):
    image_path = output_root / "images" / split / f"{stem}.png"
    label_path = output_root / "labels" / split / f"{stem}.txt"
    image.save(image_path, format="PNG", optimize=True)
    lines = [annotation_line(item, image.width, image.height) for item in annotations]
    lines = [line for line in lines if line]
    label_path.write_text(
        "\n".join(lines) + ("\n" if lines else ""),
        encoding="utf-8",
    )
    report_state["source_counts"][split][source_kind] += 1
    report_state["class_counts"][split].update(item.label for item in annotations)
    scale = 1536.0 / max(image.width, image.height)
    for item in annotations:
        report_state["box_sizes"][source_kind].append(
            (
                (item.x2 - item.x1) * scale,
                (item.y2 - item.y1) * scale,
            )
        )
    report_state["preview_candidates"].append((split, stem, source_kind))
    return image_path


def crop_to_mask(image, annotation=None):
    if annotation is not None:
        x1 = max(0, int(np.floor(annotation.x1)))
        y1 = max(0, int(np.floor(annotation.y1)))
        x2 = min(image.width, int(np.ceil(annotation.x2)))
        y2 = min(image.height, int(np.ceil(annotation.y2)))
        if x2 <= x1 or y2 <= y1:
            return None
        image = image.crop((x1, y1, x2, y2))
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    if gray.size == 0:
        return None
    alpha = np.clip((238 - gray.astype(np.int16)) * 4, 0, 255).astype(np.uint8)
    coordinates = cv2.findNonZero((alpha > 12).astype(np.uint8))
    if coordinates is None:
        return None
    x, y, width, height = cv2.boundingRect(coordinates)
    if width < 1 or height < 1:
        return None
    return Image.fromarray(alpha[y : y + height, x : x + width], mode="L")


def add_glyph(glyph_bank, size_profiles, label, mask, normalized_height, source):
    label = normalize_label(label)
    if not label or mask is None or mask.width < 1 or mask.height < 1:
        return
    normalized_height = float(normalized_height)
    if normalized_height <= 0:
        normalized_height = 0.012
    aspect_ratio = mask.width / max(1, mask.height)
    maximum_aspect = 1.8 if label != "prime" else 1.25
    if not 0.035 <= aspect_ratio <= maximum_aspect:
        return
    if not 0.002 <= normalized_height <= 0.032:
        return
    glyph_bank[label].append(
        Glyph(
            label=label,
            mask=mask,
            normalized_height=normalized_height,
            source=source,
        )
    )
    size_profiles[label].append(normalized_height)


def read_manual_pages(args, report_state, glyph_bank, size_profiles):
    annotation_root = args.annotations.resolve()
    records, _counts, _sizes, reviewed_pages = load_annotation_state(annotation_root)
    if reviewed_pages != len(records):
        raise RuntimeError(
            f"Manual annotation set is incomplete: {reviewed_pages}/{len(records)} reviewed."
        )
    rotations = load_page_rotations(args.crop_manifest.resolve())
    backgrounds = []
    validation_hashes = []

    for record in records:
        source_image = annotation_root / record["image_path"]
        source_stem = Path(record["image_path"]).stem
        rotation = rotations.get(
            (record["split"], source_stem),
            int(record.get("rotation") or 0) % 360,
        )
        with Image.open(source_image) as opened:
            image, annotations = rotate_page(
                opened.convert("RGB"),
                record["_annotations"],
                rotation,
            )
        stem = f"manual_{record['page_id']}"
        output_path = write_sample(
            args.output,
            record["split"],
            stem,
            image,
            annotations,
            "manual_real",
            report_state,
        )
        if record["split"] == "val":
            validation_hashes.extend(image_hashes(image))
            continue

        backgrounds.append(
            BackgroundPage(
                image_path=output_path,
                annotations=tuple(annotations),
                page_id=record["page_id"],
            )
        )
        maximum = max(image.width, image.height)
        for annotation in annotations:
            mask = crop_to_mask(image, annotation)
            add_glyph(
                glyph_bank,
                size_profiles,
                annotation.label,
                mask,
                (annotation.y2 - annotation.y1) / maximum,
                "manual_real",
            )
    return backgrounds, validation_hashes


def image_hash(image, hash_size=16):
    gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    values = np.asarray(gray)
    return np.packbits(values[:, 1:] > values[:, :-1]).tobytes()


def image_hashes(image):
    values = []
    current = image
    for _ in range(4):
        values.append(image_hash(current))
        current = current.transpose(Image.Transpose.ROTATE_90)
    return values


def hash_distance(first, second):
    return sum((left ^ right).bit_count() for left, right in zip(first, second))


def resembles_validation(image, validation_hashes, threshold=5):
    for candidate in image_hashes(image):
        if any(hash_distance(candidate, known) <= threshold for known in validation_hashes):
            return True
    return False


def count_extractable_characters(pdf_path):
    count = 0
    try:
        with fitz.open(pdf_path) as document:
            for page in document:
                raw = page.get_text("rawdict")
                for block in raw.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            for character in span.get("chars", []):
                                if normalize_label(character.get("c", "")):
                                    count += 1
    except Exception:
        return 0
    return count


def collect_training_pdfs(source_root):
    selected = []
    for project_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
        pdf_paths = sorted(project_dir.glob("*.pdf"))
        single_pages = [path for path in pdf_paths if path.stem.isdigit()]
        if sum(count_extractable_characters(path) for path in single_pages) > 0:
            selected.extend(single_pages)
            continue
        complete = [
            (count_extractable_characters(path), path)
            for path in pdf_paths
            if path not in single_pages
        ]
        complete = [item for item in complete if item[0] > 0]
        if complete:
            complete.sort(key=lambda item: (item[0], item[1].stat().st_size))
            selected.append(complete[-1][1])
    return selected or sorted(source_root.rglob("*.pdf"))


def render_pdf_page(page, dpi):
    scale = dpi / 72.0
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        colorspace=fitz.csRGB,
        alpha=False,
    )
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    annotations = []
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for character in span.get("chars", []):
                    raw_character = str(character.get("c", ""))
                    # The target vocabulary is uppercase only.  Patent tables
                    # also contain lowercase unit symbols (m, a, etc.); mapping
                    # those shapes to uppercase classes creates destructive
                    # label noise even though normalize_label uppercases text.
                    if raw_character.isalpha() and raw_character != raw_character.upper():
                        continue
                    label = normalize_label(raw_character)
                    if not label:
                        continue
                    x1, y1, x2, y2 = character["bbox"]
                    annotation = Annotation(
                        label=label,
                        x1=x1 * scale,
                        y1=y1 * scale,
                        x2=x2 * scale,
                        y2=y2 * scale,
                        source="pdf_text_layer",
                    )
                    runtime_scale = 1536.0 / max(image.width, image.height)
                    runtime_width = (annotation.x2 - annotation.x1) * runtime_scale
                    runtime_height = (annotation.y2 - annotation.y1) * runtime_scale
                    aspect_ratio = runtime_width / max(0.01, runtime_height)
                    if (
                        annotation.is_valid(image.width, image.height, min_size=1.0)
                        and 0.8 <= runtime_width <= 45.0
                        and 3.0 <= runtime_height <= 45.0
                        and 0.035 <= aspect_ratio <= 2.2
                    ):
                        annotations.append(annotation)
    return image, annotations


def usable_font_coverage(font_bytes):
    font = ImageFont.truetype(io.BytesIO(font_bytes), 48)
    coverage = []
    glyph_hashes = set()
    for character in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'":
        box = font.getbbox(character)
        if box[2] > box[0] and box[3] > box[1]:
            canvas = Image.new(
                "L",
                (max(2, box[2] - box[0] + 8), max(2, box[3] - box[1] + 8)),
                0,
            )
            ImageDraw.Draw(canvas).text(
                (4 - box[0], 4 - box[1]),
                character,
                font=font,
                fill=255,
            )
            actual = canvas.getbbox()
            if actual is None:
                continue
            glyph = canvas.crop(actual)
            glyph_hashes.add(
                hashlib.sha1(
                    f"{glyph.size}".encode("ascii") + glyph.tobytes()
                ).hexdigest()
            )
            coverage.append(character)
    # PDF subset fonts sometimes render the same .notdef box for every
    # Unicode character.  A bbox-only check incorrectly treated that as full
    # coverage, so require most glyphs to be visually distinct as well.
    usable = len(coverage) == 37 and len(glyph_hashes) >= 30
    return "".join(coverage), len(glyph_hashes), usable


def extract_document_fonts(document, output_root, seen_fonts, font_records):
    usable_paths = []
    for page_index in range(document.page_count):
        for descriptor in document.get_page_fonts(page_index, full=True):
            xref = int(descriptor[0])
            if xref <= 0:
                continue
            try:
                base_name, extension, font_type, content = document.extract_font(xref)
                digest = hashlib.sha1(content).hexdigest()
                if digest in seen_fonts:
                    continue
                seen_fonts.add(digest)
                coverage, unique_glyphs, usable = usable_font_coverage(content)
                record = {
                    "sha1": digest,
                    "base_name": base_name,
                    "extension": extension,
                    "type": font_type,
                    "bytes": len(content),
                    "coverage": coverage,
                    "unique_rendered_glyphs": unique_glyphs,
                    "usable_full_vocabulary": usable,
                }
                if usable:
                    extension = extension if extension in {"ttf", "otf"} else "ttf"
                    font_path = output_root / "provenance" / "fonts" / f"{digest}.{extension}"
                    font_path.write_bytes(content)
                    record["saved_path"] = str(font_path.resolve())
                    usable_paths.append(font_path)
                font_records.append(record)
            except Exception as exc:
                font_records.append(
                    {
                        "xref": xref,
                        "base_name": descriptor[3],
                        "usable_full_vocabulary": False,
                        "error": str(exc),
                    }
                )
    return usable_paths


def add_pdf_sources(
    args,
    report_state,
    glyph_bank,
    size_profiles,
    validation_hashes,
):
    pdf_paths = collect_training_pdfs(args.pdf_source.resolve())
    if args.limit_pdfs is not None:
        pdf_paths = pdf_paths[: args.limit_pdfs]
    seen_fonts = set()
    font_records = []
    font_paths = []
    skipped_duplicate = 0
    skipped_without_boxes = 0
    errors = []

    for pdf_index, pdf_path in enumerate(pdf_paths, start=1):
        try:
            with fitz.open(pdf_path) as document:
                font_paths.extend(
                    extract_document_fonts(
                        document,
                        args.output,
                        seen_fonts,
                        font_records,
                    )
                )
                for page_index, page in enumerate(document):
                    image, annotations = render_pdf_page(page, args.dpi)
                    if not annotations:
                        skipped_without_boxes += 1
                        continue
                    if resembles_validation(image, validation_hashes):
                        skipped_duplicate += 1
                        continue
                    digest = hashlib.sha1(str(pdf_path.resolve()).encode("utf-8")).hexdigest()[:8]
                    stem = f"pdf_{pdf_index:04d}_{page_index + 1:02d}_{digest}"
                    write_sample(
                        args.output,
                        "train",
                        stem,
                        image,
                        annotations,
                        "pdf_exact",
                        report_state,
                    )
                    maximum = max(image.width, image.height)
                    for annotation in annotations:
                        # Old PDF subset encodings can report an uppercase
                        # Unicode value while the visible glyph is lowercase
                        # or otherwise remapped.  Keep exact PDF crops as a
                        # source for digits only; alphabet composites use
                        # human-reviewed glyphs or the Times-Roman fallback.
                        if not annotation.label.isdigit():
                            continue
                        add_glyph(
                            glyph_bank,
                            size_profiles,
                            annotation.label,
                            crop_to_mask(image, annotation),
                            (annotation.y2 - annotation.y1) / maximum,
                            "pdf_exact",
                        )
        except Exception as exc:
            errors.append({"path": str(pdf_path), "error": str(exc)})

    unique_font_paths = sorted(set(font_paths))
    return unique_font_paths, {
        "selected_pdf_count": len(pdf_paths),
        "skipped_validation_duplicates": skipped_duplicate,
        "skipped_without_text_boxes": skipped_without_boxes,
        "font_records": font_records,
        "usable_font_count": len(unique_font_paths),
        "errors": errors,
    }


def add_review_feedback(args, glyph_bank, size_profiles):
    feedback = Counter()
    hard_negative_masks = []
    if not args.crop_manifest.exists():
        return hard_negative_masks, dict(feedback)
    dataset_root = args.crop_manifest.parent
    with args.crop_manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("split") != "train":
                continue
            crop_path = dataset_root / row.get("crop_path", "")
            if not crop_path.exists():
                continue
            with Image.open(crop_path) as opened:
                mask = crop_to_mask(opened.convert("RGB"))
            if row.get("status") == "approved":
                label = normalize_label(row.get("label", ""))
                if label:
                    add_glyph(
                        glyph_bank,
                        size_profiles,
                        label,
                        mask,
                        0.012,
                        "approved_review_crop",
                    )
                    feedback["approved_positive_crops"] += 1
            elif row.get("status") == "skipped" and mask is not None:
                hard_negative_masks.append(mask)
                feedback["skipped_hard_negatives"] += 1
    return hard_negative_masks, dict(feedback)


def add_error_exports(args, glyph_bank, size_profiles, hard_negative_masks):
    summary = Counter()
    if not args.error_root.exists():
        return dict(summary)
    for zip_path in args.error_root.rglob("*.zip"):
        try:
            with ZipFile(zip_path) as archive:
                if "review.json" not in archive.namelist():
                    continue
                manifest = json.loads(archive.read("review.json"))
                summary["packages"] += 1
                for image_record in manifest.get("images", []):
                    for issue in image_record.get("issues", []):
                        crop_file = issue.get("crop_file")
                        if not crop_file or crop_file not in archive.namelist():
                            continue
                        with Image.open(io.BytesIO(archive.read(crop_file))) as opened:
                            mask = crop_to_mask(opened.convert("RGB"))
                        corrected = normalize_label(issue.get("corrected_label", ""))
                        issue_types = set(issue.get("issue_types", []))
                        if corrected and len(display_label(corrected)) == 1:
                            add_glyph(
                                glyph_bank,
                                size_profiles,
                                corrected,
                                mask,
                                0.012,
                                "coworker_error_export",
                            )
                            summary["corrected_positive_crops"] += 1
                        elif issue_types & {
                            "deleted_false_positive",
                            "auto_filtered_false_positive",
                        } and mask is not None:
                            hard_negative_masks.append(mask)
                            summary["false_positive_hard_negatives"] += 1
        except (BadZipFile, KeyError, OSError, ValueError):
            summary["ignored_invalid_packages"] += 1
    return dict(summary)


def mild_augment(image, rng):
    image = ImageEnhance.Contrast(image).enhance(rng.uniform(0.94, 1.06))
    image = ImageEnhance.Brightness(image).enhance(rng.uniform(0.97, 1.03))
    if rng.random() < 0.65:
        image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.15, 0.45)))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=rng.randint(88, 96), subsampling=0)
    buffer.seek(0)
    with Image.open(buffer) as decoded:
        return decoded.convert("RGB").copy()


def add_augmented_manual_pages(args, backgrounds, report_state, rng):
    for copy_index in range(args.augment_copies):
        for page in backgrounds:
            with Image.open(page.image_path) as opened:
                image = mild_augment(opened.convert("RGB"), rng)
            write_sample(
                args.output,
                "train",
                f"augment_{copy_index + 1:02d}_{page.page_id}",
                image,
                page.annotations,
                "mild_real_augmentation",
                report_state,
            )


def rendered_font_mask(font_path, character, target_height):
    font_size = max(12, int(target_height * 1.8))
    font = ImageFont.truetype(str(font_path), font_size)
    box = font.getbbox(character)
    canvas = Image.new(
        "L",
        (max(2, box[2] - box[0] + 8), max(2, box[3] - box[1] + 8)),
        0,
    )
    ImageDraw.Draw(canvas).text((4 - box[0], 4 - box[1]), character, font=font, fill=255)
    actual = canvas.getbbox()
    if actual is None:
        raise ValueError(f"Embedded PDF font cannot render {character!r}: {font_path}")
    mask = canvas.crop(actual)
    scale = target_height / max(1, mask.height)
    width = max(1, int(round(mask.width * scale)))
    return mask.resize((width, target_height), Image.Resampling.LANCZOS)


def sample_normalized_height(label, size_profiles, rng):
    values = size_profiles.get(label) or []
    if not values:
        values = [value for items in size_profiles.values() for value in items]
    if not values:
        return 0.012
    lower, upper = np.percentile(values, [10, 90])
    candidates = [value for value in values if lower <= value <= upper]
    return float(rng.choice(candidates or values))


def choose_glyph_mask(
    label,
    target_height,
    glyph_bank,
    font_paths,
    rng,
):
    character = display_label(label)
    use_font = bool(font_paths) and (not glyph_bank[label] or rng.random() < 0.12)
    if use_font:
        font_path = rng.choice(font_paths)
        source = (
            "pdf_embedded_font"
            if "provenance" in {part.casefold() for part in font_path.parts}
            else "matched_system_font"
        )
        return rendered_font_mask(font_path, character, target_height), source
    glyph = rng.choice(glyph_bank[label])
    mask = glyph.mask
    scale = target_height / max(1, mask.height)
    width = max(1, int(round(mask.width * scale)))
    return mask.resize((width, target_height), Image.Resampling.LANCZOS), glyph.source


def forced_text(label, rng):
    if label == "prime":
        return rng.choice(("7'", "8'", "9'", "55'", "10'"))
    if label in {"I", "V", "X"}:
        choices = [text for text in ("IV", "VII", "VIII", "IX", "XI", "XIV") if label in text]
        return rng.choice(choices)
    if label.isalpha():
        return label if rng.random() < 0.35 else f"{rng.randint(1, 99)}{label}"
    if rng.random() < 0.25:
        return label
    if rng.random() < 0.25:
        return f"{label}{rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
    return "".join(rng.choice("0123456789") for _ in range(rng.randint(2, 3)))


def build_group_masks(text, maximum, size_profiles, glyph_bank, font_paths, rng):
    items = []
    sources = Counter()
    for character in text:
        label = normalize_label(character)
        normalized_height = sample_normalized_height(label, size_profiles, rng)
        target_height = max(4, int(round(normalized_height * maximum)))
        mask, source = choose_glyph_mask(
            label,
            target_height,
            glyph_bank,
            font_paths,
            rng,
        )
        items.append((label, mask))
        sources[source] += 1
    group_height = max(mask.height for _, mask in items)
    spacing = max(1, int(round(group_height * rng.uniform(0.02, 0.09))))
    group_width = sum(mask.width for _, mask in items) + spacing * (len(items) - 1)
    return items, group_width, group_height, spacing, sources


def overlaps(box, occupied, margin=5):
    x1, y1, x2, y2 = box
    for other in occupied:
        if not (
            x2 + margin < other[0]
            or other[2] + margin < x1
            or y2 + margin < other[1]
            or other[3] + margin < y1
        ):
            return True
    return False


def add_composite_pages(
    args,
    backgrounds,
    glyph_bank,
    size_profiles,
    font_paths,
    report_state,
    rng,
):
    generated_counts = Counter()
    glyph_sources = Counter()
    class_cycle = list(CLASS_NAMES)
    for page_index in range(args.composite_count):
        background = backgrounds[page_index % len(backgrounds)]
        with Image.open(background.image_path) as opened:
            image = opened.convert("RGB").copy()
        occupied = [
            (item.x1, item.y1, item.x2, item.y2)
            for item in background.annotations
        ]
        annotations = list(background.annotations)
        maximum = max(image.width, image.height)
        group_count = rng.randint(10, 15)
        for group_index in range(group_count):
            if page_index == 0 and group_index < len(REQUIRED_PHRASES):
                text = REQUIRED_PHRASES[group_index]
            else:
                minimum = min(generated_counts[name] for name in class_cycle)
                candidates = [name for name in class_cycle if generated_counts[name] == minimum]
                forced_class = rng.choice(candidates)
                text = forced_text(forced_class, rng)
            items, width, height, spacing, sources = build_group_masks(
                text,
                maximum,
                size_profiles,
                glyph_bank,
                font_paths,
                rng,
            )
            position = None
            for _ in range(120):
                x = rng.randint(12, max(12, image.width - width - 12))
                y = rng.randint(12, max(12, image.height - height - 12))
                box = (x, y, x + width, y + height)
                if not overlaps(box, occupied, margin=max(4, height // 5)):
                    position = (x, y, box)
                    break
            if position is None:
                continue
            x, y, box = position
            occupied.append(box)
            cursor = x
            for label, mask in items:
                item_y = y if label == "prime" else y + height - mask.height
                image.paste((0, 0, 0), (cursor, item_y), mask)
                annotations.append(
                    Annotation(
                        label=label,
                        x1=cursor,
                        y1=item_y,
                        x2=cursor + mask.width,
                        y2=item_y + mask.height,
                        source="domain_matched_composite",
                    )
                )
                generated_counts[label] += 1
                cursor += mask.width + spacing
            glyph_sources.update(sources)
        image = mild_augment(image, rng)
        write_sample(
            args.output,
            "train",
            f"composite_{page_index + 1:04d}",
            image,
            annotations,
            "domain_matched_composite",
            report_state,
        )
    return dict(generated_counts), dict(glyph_sources)


def cleaned_background(image, annotations):
    array = np.asarray(image.convert("RGB"))[:, :, ::-1].copy()
    mask = np.zeros(array.shape[:2], dtype=np.uint8)
    for item in annotations:
        pad = max(3, int(round((item.y2 - item.y1) * 0.18)))
        x1 = max(0, int(item.x1) - pad)
        y1 = max(0, int(item.y1) - pad)
        x2 = min(array.shape[1] - 1, int(item.x2) + pad)
        y2 = min(array.shape[0] - 1, int(item.y2) + pad)
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, thickness=-1)
    result = cv2.inpaint(array, mask, 3, cv2.INPAINT_TELEA)
    return Image.fromarray(result[:, :, ::-1])


def add_hard_negative_pages(args, backgrounds, hard_negative_masks, report_state, rng):
    for page_index in range(args.hard_negative_count):
        background = backgrounds[page_index % len(backgrounds)]
        with Image.open(background.image_path) as opened:
            image = cleaned_background(opened.convert("RGB"), background.annotations)
        for _ in range(rng.randint(2, 7)):
            if not hard_negative_masks:
                break
            mask = rng.choice(hard_negative_masks)
            if mask.width >= image.width or mask.height >= image.height:
                continue
            x = rng.randint(0, image.width - mask.width)
            y = rng.randint(0, image.height - mask.height)
            image.paste((0, 0, 0), (x, y), mask)
        image = mild_augment(image, rng)
        write_sample(
            args.output,
            "train",
            f"hard_negative_{page_index + 1:03d}",
            image,
            [],
            "hard_negative",
            report_state,
        )


def percentile_summary(rows):
    if not rows:
        return {"instances": 0}
    values = np.asarray(rows, dtype=float)
    return {
        "instances": len(rows),
        "width_percentiles_10_50_90": [
            round(float(value), 2) for value in np.percentile(values[:, 0], [10, 50, 90])
        ],
        "height_percentiles_10_50_90": [
            round(float(value), 2) for value in np.percentile(values[:, 1], [10, 50, 90])
        ],
    }


def read_yolo_boxes(label_path, width, height):
    boxes = []
    for line in label_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        class_id = int(parts[0])
        center_x, center_y, box_width, box_height = map(float, parts[1:])
        pixel_width = box_width * width
        pixel_height = box_height * height
        boxes.append(
            (
                CLASS_NAMES[class_id],
                center_x * width - pixel_width / 2,
                center_y * height - pixel_height / 2,
                center_x * width + pixel_width / 2,
                center_y * height + pixel_height / 2,
            )
        )
    return boxes


def make_preview(args, report_state, split="train"):
    candidates = [item for item in report_state["preview_candidates"] if item[0] == split]
    selected = []
    for source_kind in (
        "manual_real",
        "pdf_exact",
        "mild_real_augmentation",
        "domain_matched_composite",
        "hard_negative",
    ):
        selected.extend(
            [item for item in candidates if item[2] == source_kind][:3]
        )
    selected = selected[:12]
    tile_size = 420
    sheet = Image.new("RGB", (tile_size * 3, tile_size * 4), "white")
    for index, (_split, stem, source_kind) in enumerate(selected):
        image_path = args.output / "images" / split / f"{stem}.png"
        label_path = args.output / "labels" / split / f"{stem}.txt"
        with Image.open(image_path) as opened:
            image = opened.convert("RGB").copy()
        draw = ImageDraw.Draw(image)
        for label, x1, y1, x2, y2 in read_yolo_boxes(
            label_path, image.width, image.height
        ):
            draw.rectangle((x1, y1, x2, y2), outline=(220, 20, 60), width=max(2, image.width // 700))
            draw.text((x1, max(0, y1 - 18)), label, fill=(20, 80, 210))
        image.thumbnail((tile_size - 12, tile_size - 42))
        tile = Image.new("RGB", (tile_size, tile_size), "white")
        tile.paste(image, ((tile_size - image.width) // 2, 36))
        ImageDraw.Draw(tile).text((6, 5), f"{source_kind}: {stem}", fill="black")
        sheet.paste(tile, ((index % 3) * tile_size, (index // 3) * tile_size))
    path = args.output / "previews" / f"{split}_audit.jpg"
    sheet.save(path, quality=92)
    return path


def write_metadata(args, report_state, additional):
    data_yaml = [
        f'path: "{args.output.resolve().as_posix()}"',
        "train: images/train",
        "val: images/val",
        "names:",
    ]
    for class_id, label in enumerate(CLASS_NAMES):
        data_yaml.append(f'  {class_id}: "{label}"')
    (args.output / "data.yaml").write_text("\n".join(data_yaml) + "\n", encoding="utf-8")
    class_map = {label: display_label(label) for label in CLASS_NAMES}
    class_map.update({"apostrophe": "'", "'": "'", "’": "'", "′": "'", "`": "'"})
    (args.output / "class_map.json").write_text(
        json.dumps(class_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    size_summary = {
        source: percentile_summary(rows)
        for source, rows in report_state["box_sizes"].items()
    }
    manual_median = size_summary.get("manual_real", {}).get(
        "height_percentiles_10_50_90", [0, 0, 0]
    )[1]
    generated_median = size_summary.get("domain_matched_composite", {}).get(
        "height_percentiles_10_50_90", [0, 0, 0]
    )[1]
    ratio = generated_median / manual_median if manual_median else None
    report = {
        "format_version": 2,
        "objective": "domain-matched 0-9/A-Z/prime detector",
        "validation_policy": {
            "real_only": True,
            "generated_or_augmented_validation_images": 0,
            "source": str(args.annotations.resolve()),
        },
        "rotation_augmentation": False,
        "source_counts": {
            split: dict(counts) for split, counts in report_state["source_counts"].items()
        },
        "class_counts": {
            split: dict(sorted(counts.items()))
            for split, counts in report_state["class_counts"].items()
        },
        "box_size_distribution_at_1536": size_summary,
        "generated_to_manual_median_height_ratio": (
            round(ratio, 4) if ratio is not None else None
        ),
        "required_phrase_coverage": list(REQUIRED_PHRASES),
        "augmentation": {
            "rotation": "disabled",
            "contrast": [0.94, 1.06],
            "brightness": [0.97, 1.03],
            "gaussian_blur_radius": [0.15, 0.45],
            "jpeg_quality": [88, 96],
        },
        **additional,
    }
    (args.output / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main():
    args = parse_args()
    args.pdf_source = args.pdf_source.resolve()
    args.annotations = args.annotations.resolve()
    args.crop_manifest = args.crop_manifest.resolve()
    args.error_root = args.error_root.resolve()
    args.output = args.output.resolve()
    if not args.pdf_source.exists():
        raise FileNotFoundError(f"PDF source not found: {args.pdf_source}")
    if not args.annotations.exists():
        raise FileNotFoundError(f"Manual annotations not found: {args.annotations}")
    ensure_empty_output(args.output)

    rng = random.Random(args.seed)
    report_state = {
        "source_counts": {"train": Counter(), "val": Counter()},
        "class_counts": {"train": Counter(), "val": Counter()},
        "box_sizes": defaultdict(list),
        "preview_candidates": [],
    }
    glyph_bank = defaultdict(list)
    size_profiles = defaultdict(list)

    backgrounds, validation_hashes = read_manual_pages(
        args,
        report_state,
        glyph_bank,
        size_profiles,
    )
    if not backgrounds:
        raise RuntimeError("No reviewed manual training pages were found.")
    font_paths, pdf_report = add_pdf_sources(
        args,
        report_state,
        glyph_bank,
        size_profiles,
        validation_hashes,
    )
    if not font_paths:
        # The PDF font programs are still audited and their exact visible
        # glyphs are harvested from rendered text-layer boxes above.  Some
        # embedded subset programs deliberately omit a usable Unicode cmap;
        # for classes absent from every real source, use only the closest
        # Times-Roman system counterpart at measured real heights.
        fallback_fonts = [
            Path("C:/Windows/Fonts/times.ttf"),
            Path("C:/Windows/Fonts/timesbd.ttf"),
        ]
        font_paths = [path for path in fallback_fonts if path.exists()]
        if not font_paths:
            raise RuntimeError(
                "Embedded PDF fonts have subset-only Unicode maps and no "
                "Times-Roman fallback font was found."
            )
        pdf_report["unicode_subset_fallback_fonts"] = [
            str(path.resolve()) for path in font_paths
        ]

    hard_negative_masks, review_feedback = add_review_feedback(
        args,
        glyph_bank,
        size_profiles,
    )
    error_feedback = add_error_exports(
        args,
        glyph_bank,
        size_profiles,
        hard_negative_masks,
    )
    add_augmented_manual_pages(args, backgrounds, report_state, rng)
    generated_counts, generated_sources = add_composite_pages(
        args,
        backgrounds,
        glyph_bank,
        size_profiles,
        font_paths,
        report_state,
        rng,
    )
    add_hard_negative_pages(
        args,
        backgrounds,
        hard_negative_masks,
        report_state,
        rng,
    )

    missing_train = [
        label for label in CLASS_NAMES if report_state["class_counts"]["train"][label] == 0
    ]
    if missing_train:
        raise RuntimeError(f"Training dataset still has missing classes: {missing_train}")

    preview_path = make_preview(args, report_state, "train")
    report = write_metadata(
        args,
        report_state,
        {
            "pdf_embedded_font_extraction": pdf_report,
            "glyph_bank_counts": {
                label: len(glyph_bank[label]) for label in CLASS_NAMES
            },
            "review_feedback": review_feedback,
            "coworker_error_exports": error_feedback,
            "hard_negative_mask_count": len(hard_negative_masks),
            "generated_class_counts": generated_counts,
            "generated_glyph_sources": generated_sources,
            "preview": str(preview_path),
            "settings": {
                "dpi": args.dpi,
                "composite_count": args.composite_count,
                "hard_negative_count": args.hard_negative_count,
                "augment_copies": args.augment_copies,
                "seed": args.seed,
            },
        },
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
