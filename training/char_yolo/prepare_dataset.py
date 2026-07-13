"""從專利 PDF 與合成字元建立 0-9 / A-Z / prime YOLO 資料集。"""

import argparse
import hashlib
import json
import math
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

from char_classes import (
    CLASS_NAMES,
    CLASS_TO_ID,
    DIGITS,
    LETTERS,
    PRIME_CLASS_NAME,
    display_character,
    normalize_character,
)


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent

DEFAULT_SOURCE = WORKSPACE_ROOT / "pdf-100"
DEFAULT_OUTPUT = SCRIPT_DIR / "dataset"


@dataclass(frozen=True)
class Annotation:
    class_name: str
    x1: float
    y1: float
    x2: float
    y2: float

    def to_yolo(self, image_width, image_height):
        x1 = max(0.0, min(float(image_width), self.x1))
        y1 = max(0.0, min(float(image_height), self.y1))
        x2 = max(0.0, min(float(image_width), self.x2))
        y2 = max(0.0, min(float(image_height), self.y2))

        width = x2 - x1
        height = y2 - y1

        if width < 1.0 or height < 1.0:
            return ""

        x_center = ((x1 + x2) / 2.0) / image_width
        y_center = ((y1 + y2) / 2.0) / image_height
        width_norm = width / image_width
        height_norm = height / image_height

        return (
            f"{CLASS_TO_ID[self.class_name]} "
            f"{x_center:.6f} {y_center:.6f} "
            f"{width_norm:.6f} {height_norm:.6f}"
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="建立專利圖式字元級 YOLO 訓練資料集。"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--image-size", type=int, default=1280)
    parser.add_argument("--synthetic-count", type=int, default=740)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument(
        "--limit-pdfs",
        type=int,
        default=None,
        help="只供快速測試使用；正式產生資料時不要設定。",
    )
    return parser.parse_args()


def validate_args(args):
    if not args.source.exists():
        raise FileNotFoundError(f"找不到素材資料夾：{args.source}")

    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError(
            f"輸出資料夾不是空的：{args.output}\n"
            "為避免覆寫既有標註，請改用新的 --output 路徑。"
        )

    if args.dpi < 72:
        raise ValueError("--dpi 不可小於 72。")

    if args.image_size < 320:
        raise ValueError("--image-size 不可小於 320。")

    if args.synthetic_count < 0:
        raise ValueError("--synthetic-count 不可為負數。")

    if not 0.05 <= args.val_ratio <= 0.5:
        raise ValueError("--val-ratio 必須介於 0.05 與 0.5。")


def create_output_dirs(output_root):
    for split in ("train", "val"):
        (output_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (output_root / "labels" / split).mkdir(parents=True, exist_ok=True)

    (output_root / "previews").mkdir(parents=True, exist_ok=True)


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
                                if normalize_character(character.get("c", "")):
                                    count += 1
    except Exception:
        return 0

    return count


def collect_training_pdfs(source_root):
    """每個案件優先單頁 PDF，無文字層時改用一份完整圖式 PDF。"""

    selected = []

    for project_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
        pdf_paths = sorted(project_dir.glob("*.pdf"))
        single_page_pdfs = [path for path in pdf_paths if path.stem.isdigit()]
        single_page_count = sum(
            count_extractable_characters(path) for path in single_page_pdfs
        )

        if single_page_count > 0:
            selected.extend(single_page_pdfs)
            continue

        complete_candidates = [
            (count_extractable_characters(path), path)
            for path in pdf_paths
            if path not in single_page_pdfs
        ]
        complete_candidates = [item for item in complete_candidates if item[0] > 0]

        if complete_candidates:
            complete_candidates.sort(key=lambda item: (item[0], item[1].stat().st_size))
            selected.append(complete_candidates[-1][1])

    if selected:
        return selected

    return sorted(source_root.rglob("*.pdf"))


def stable_split(key, val_ratio):
    digest = hashlib.sha1(str(key).encode("utf-8")).hexdigest()
    fraction = int(digest[:8], 16) / 0xFFFFFFFF
    return "val" if fraction < val_ratio else "train"


def page_to_image(page, dpi):
    scale = dpi / 72.0
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        colorspace=fitz.csRGB,
        alpha=False,
    )
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    return image, scale


def extract_pdf_character_boxes(page, scale):
    annotations = []
    raw = page.get_text("rawdict")

    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for character in span.get("chars", []):
                    class_name = normalize_character(character.get("c", ""))

                    if not class_name:
                        continue

                    x1, y1, x2, y2 = character["bbox"]
                    annotation = Annotation(
                        class_name=class_name,
                        x1=x1 * scale,
                        y1=y1 * scale,
                        x2=x2 * scale,
                        y2=y2 * scale,
                    )

                    if annotation.x2 - annotation.x1 >= 1.0 and annotation.y2 - annotation.y1 >= 1.0:
                        annotations.append(annotation)

    return annotations


def write_sample(output_root, split, stem, image, annotations):
    image_path = output_root / "images" / split / f"{stem}.png"
    label_path = output_root / "labels" / split / f"{stem}.txt"

    image.save(image_path, format="PNG", optimize=True)

    lines = []
    for annotation in annotations:
        line = annotation.to_yolo(image.width, image.height)
        if line:
            lines.append(line)

    label_path.write_text(
        "\n".join(lines) + ("\n" if lines else ""),
        encoding="utf-8",
    )


def prepare_real_pdf_samples(pdf_paths, output_root, dpi, val_ratio):
    counts = {"train": Counter(), "val": Counter()}
    image_counts = Counter()
    skipped_without_text_boxes = 0
    errors = []

    for index, pdf_path in enumerate(pdf_paths, start=1):
        try:
            with fitz.open(pdf_path) as document:
                for page_index, page in enumerate(document):
                    image, scale = page_to_image(page, dpi)
                    annotations = extract_pdf_character_boxes(page, scale)

                    if not annotations:
                        skipped_without_text_boxes += 1
                        continue

                    source_key = pdf_path.parent.relative_to(pdf_path.parents[1])
                    split = stable_split(source_key, val_ratio)
                    digest = hashlib.sha1(
                        str(pdf_path.resolve()).encode("utf-8")
                    ).hexdigest()[:10]
                    stem = f"real_{index:04d}_{page_index + 1:02d}_{digest}"

                    write_sample(output_root, split, stem, image, annotations)
                    image_counts[split] += 1
                    counts[split].update(a.class_name for a in annotations)

        except Exception as exc:
            errors.append({"path": str(pdf_path), "error": str(exc)})

    return {
        "image_counts": dict(image_counts),
        "class_counts": {key: dict(value) for key, value in counts.items()},
        "skipped_without_text_boxes": skipped_without_text_boxes,
        "errors": errors,
    }


def find_fonts():
    candidates = [
        Path("C:/Windows/Fonts/times.ttf"),
        Path("C:/Windows/Fonts/timesbd.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/cambria.ttc"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("C:/Windows/Fonts/cour.ttf"),
    ]
    fonts = [path for path in candidates if path.exists()]

    if not fonts:
        raise FileNotFoundError("找不到可用的 Windows 字型。")

    return fonts


def draw_patent_style_background(image, rng):
    draw = ImageDraw.Draw(image)
    width, height = image.size

    for _ in range(rng.randint(18, 45)):
        ink = rng.randint(90, 210)
        line_width = rng.randint(1, 3)
        x1 = rng.randint(20, width - 80)
        y1 = rng.randint(20, height - 80)
        x2 = max(20, min(width - 20, x1 + rng.randint(-260, 360)))
        y2 = max(20, min(height - 20, y1 + rng.randint(-260, 360)))
        draw.line((x1, y1, x2, y2), fill=(ink, ink, ink), width=line_width)

    for _ in range(rng.randint(5, 14)):
        ink = rng.randint(120, 225)
        x1 = rng.randint(30, width - 180)
        y1 = rng.randint(30, height - 180)
        x2 = min(width - 30, x1 + rng.randint(60, 260))
        y2 = min(height - 30, y1 + rng.randint(40, 220))

        if rng.random() < 0.5:
            draw.rectangle((x1, y1, x2, y2), outline=(ink, ink, ink), width=2)
        else:
            draw.ellipse((x1, y1, x2, y2), outline=(ink, ink, ink), width=2)


def render_glyph(character, font_path, font_size):
    font = ImageFont.truetype(str(font_path), font_size)
    bbox = font.getbbox(character)
    width = max(1, bbox[2] - bbox[0])
    height = max(1, bbox[3] - bbox[1])
    padding = max(3, font_size // 12)
    mask = Image.new("L", (width + padding * 2, height + padding * 2), 0)
    draw = ImageDraw.Draw(mask)
    draw.text(
        (padding - bbox[0], padding - bbox[1]),
        character,
        font=font,
        fill=255,
    )
    actual_bbox = mask.getbbox()

    if actual_bbox is None:
        raise ValueError(f"字型無法繪製字元：{character}")

    return mask.crop(actual_bbox)


def build_group_glyphs(text, font_path, font_size, rng):
    glyphs = []
    base_top = max(4, int(font_size * 0.22))

    for character in text:
        is_prime = character == "'"
        glyph_size = max(14, int(font_size * 0.62)) if is_prime else font_size
        mask = render_glyph(character, font_path, glyph_size)
        top = 0 if is_prime else base_top
        glyphs.append(
            {
                "class_name": normalize_character(character),
                "mask": mask,
                "top": top,
                "spacing": rng.randint(0, max(2, font_size // 14)),
            }
        )

    group_width = sum(item["mask"].width + item["spacing"] for item in glyphs)
    group_height = max(item["top"] + item["mask"].height for item in glyphs)
    return glyphs, group_width, group_height


def rectangles_overlap(first, second, margin=8):
    return not (
        first[2] + margin < second[0]
        or second[2] + margin < first[0]
        or first[3] + margin < second[1]
        or second[3] + margin < first[1]
    )


def choose_synthetic_text(rng, forced_class=None):
    if forced_class == PRIME_CLASS_NAME:
        return rng.choice(DIGITS) + "'"

    if forced_class:
        return display_character(forced_class)

    choice = rng.random()

    if choice < 0.16:
        return rng.choice(DIGITS)
    if choice < 0.34:
        return rng.choice(LETTERS)
    if choice < 0.58:
        return "".join(rng.choice(DIGITS) for _ in range(rng.randint(2, 3)))
    if choice < 0.80:
        return (
            "".join(rng.choice(DIGITS) for _ in range(rng.randint(1, 3)))
            + rng.choice(LETTERS)
        )

    return (
        "".join(rng.choice(DIGITS) for _ in range(rng.randint(1, 2)))
        + "'"
    )


def create_synthetic_sample(image_size, font_paths, rng, forced_class):
    paper = rng.randint(246, 255)
    image = Image.new("RGB", (image_size, image_size), (paper, paper, paper))
    draw_patent_style_background(image, rng)

    annotations = []
    occupied = []
    group_count = rng.randint(10, 20)

    for group_index in range(group_count):
        text = choose_synthetic_text(
            rng,
            forced_class=forced_class if group_index == 0 else None,
        )
        font_path = rng.choice(font_paths)
        font_size = rng.randint(30, 76)
        glyphs, group_width, group_height = build_group_glyphs(
            text, font_path, font_size, rng
        )

        position = None
        for _ in range(80):
            x = rng.randint(20, max(20, image_size - group_width - 20))
            y = rng.randint(20, max(20, image_size - group_height - 20))
            rectangle = (x, y, x + group_width, y + group_height)

            if not any(rectangles_overlap(rectangle, used) for used in occupied):
                position = (x, y, rectangle)
                break

        if position is None:
            continue

        x, y, rectangle = position
        occupied.append(rectangle)
        cursor_x = x
        ink = rng.randint(0, 45)

        for glyph in glyphs:
            mask = glyph["mask"]
            glyph_y = y + glyph["top"]
            image.paste(
                (ink, ink, ink),
                (cursor_x, glyph_y),
                mask,
            )
            annotations.append(
                Annotation(
                    class_name=glyph["class_name"],
                    x1=cursor_x,
                    y1=glyph_y,
                    x2=cursor_x + mask.width,
                    y2=glyph_y + mask.height,
                )
            )
            cursor_x += mask.width + glyph["spacing"]

    return image, annotations


def prepare_synthetic_samples(
    count,
    output_root,
    image_size,
    val_ratio,
    seed,
):
    fonts = find_fonts()
    counts = {"train": Counter(), "val": Counter()}
    image_counts = Counter()
    val_interval = max(2, round(1.0 / val_ratio))

    for index in range(count):
        rng = random.Random(seed + index * 104729)
        forced_class = CLASS_NAMES[index % len(CLASS_NAMES)]
        image, annotations = create_synthetic_sample(
            image_size=image_size,
            font_paths=fonts,
            rng=rng,
            forced_class=forced_class,
        )
        split = "val" if index % val_interval == 0 else "train"
        stem = f"synthetic_{index + 1:05d}"
        write_sample(output_root, split, stem, image, annotations)
        image_counts[split] += 1
        counts[split].update(a.class_name for a in annotations)

    return {
        "image_counts": dict(image_counts),
        "class_counts": {key: dict(value) for key, value in counts.items()},
        "fonts": [str(path) for path in fonts],
    }


def read_yolo_annotations(label_path, image_width, image_height):
    annotations = []

    for line in label_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue

        class_id = int(parts[0])
        x_center, y_center, width, height = map(float, parts[1:])
        box_width = width * image_width
        box_height = height * image_height
        center_x = x_center * image_width
        center_y = y_center * image_height
        annotations.append(
            Annotation(
                class_name=CLASS_NAMES[class_id],
                x1=center_x - box_width / 2,
                y1=center_y - box_height / 2,
                x2=center_x + box_width / 2,
                y2=center_y + box_height / 2,
            )
        )

    return annotations


def make_preview_sheet(output_root, split, max_images=12):
    image_paths = sorted((output_root / "images" / split).glob("*.png"))
    if not image_paths:
        return None

    rng = random.Random(20260713 + (1 if split == "val" else 0))
    selected = image_paths if len(image_paths) <= max_images else rng.sample(image_paths, max_images)
    tile_size = 420
    columns = 3
    rows = math.ceil(len(selected) / columns)
    sheet = Image.new("RGB", (columns * tile_size, rows * tile_size), "white")

    for index, image_path in enumerate(selected):
        image = Image.open(image_path).convert("RGB")
        annotations = read_yolo_annotations(
            output_root / "labels" / split / f"{image_path.stem}.txt",
            image.width,
            image.height,
        )
        draw = ImageDraw.Draw(image)

        for annotation in annotations:
            draw.rectangle(
                (annotation.x1, annotation.y1, annotation.x2, annotation.y2),
                outline=(220, 20, 60),
                width=max(2, image.width // 500),
            )
            draw.text(
                (annotation.x1, max(0, annotation.y1 - 14)),
                annotation.class_name,
                fill=(20, 90, 200),
            )

        image.thumbnail((tile_size - 12, tile_size - 30))
        tile = Image.new("RGB", (tile_size, tile_size), "white")
        tile.paste(image, ((tile_size - image.width) // 2, 22))
        ImageDraw.Draw(tile).text((6, 4), image_path.name, fill="black")
        x = (index % columns) * tile_size
        y = (index // columns) * tile_size
        sheet.paste(tile, (x, y))

    preview_path = output_root / "previews" / f"{split}_samples.jpg"
    sheet.save(preview_path, quality=90)
    return preview_path


def merge_class_counts(*sections):
    merged = {"train": Counter(), "val": Counter()}

    for section in sections:
        for split in ("train", "val"):
            merged[split].update(section.get("class_counts", {}).get(split, {}))

    return {split: dict(merged[split]) for split in merged}


def write_dataset_metadata(output_root, source_root, args, real, synthetic):
    yaml_lines = [
        f'path: "{output_root.resolve().as_posix()}"',
        "train: images/train",
        "val: images/val",
        "names:",
    ]

    for index, class_name in enumerate(CLASS_NAMES):
        yaml_lines.append(f'  {index}: "{class_name}"')

    (output_root / "data.yaml").write_text(
        "\n".join(yaml_lines) + "\n",
        encoding="utf-8",
    )

    class_map = {
        class_name: display_character(class_name)
        for class_name in CLASS_NAMES
    }
    class_map.update({
        "apostrophe": "'",
        "'": "'",
        "’": "'",
        "′": "'",
        "`": "'",
    })
    (output_root / "class_map.json").write_text(
        json.dumps(class_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    real_counts = Counter()
    for split_counts in real["class_counts"].values():
        real_counts.update(split_counts)

    manifest = {
        "source": str(source_root.resolve()),
        "classes": list(CLASS_NAMES),
        "settings": {
            "dpi": args.dpi,
            "image_size": args.image_size,
            "synthetic_count": args.synthetic_count,
            "val_ratio": args.val_ratio,
            "seed": args.seed,
        },
        "real": real,
        "synthetic": synthetic,
        "combined_class_counts": merge_class_counts(real, synthetic),
        "real_classes_without_samples": [
            name for name in CLASS_NAMES if real_counts[name] == 0
        ],
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main():
    args = parse_args()
    args.source = args.source.resolve()
    args.output = args.output.resolve()
    validate_args(args)
    create_output_dirs(args.output)

    pdf_paths = collect_training_pdfs(args.source)
    if args.limit_pdfs is not None:
        pdf_paths = pdf_paths[:args.limit_pdfs]

    print(f"來源 PDF：{len(pdf_paths)}")
    real = prepare_real_pdf_samples(
        pdf_paths=pdf_paths,
        output_root=args.output,
        dpi=args.dpi,
        val_ratio=args.val_ratio,
    )
    print(f"真實頁面：{sum(real['image_counts'].values())}")

    synthetic = prepare_synthetic_samples(
        count=args.synthetic_count,
        output_root=args.output,
        image_size=args.image_size,
        val_ratio=args.val_ratio,
        seed=args.seed,
    )
    print(f"合成頁面：{sum(synthetic['image_counts'].values())}")

    write_dataset_metadata(
        output_root=args.output,
        source_root=args.source,
        args=args,
        real=real,
        synthetic=synthetic,
    )

    train_preview = make_preview_sheet(args.output, "train")
    val_preview = make_preview_sheet(args.output, "val")

    print(f"data.yaml：{args.output / 'data.yaml'}")
    print(f"manifest：{args.output / 'manifest.json'}")
    print(f"train 預覽：{train_preview}")
    print(f"val 預覽：{val_preview}")


if __name__ == "__main__":
    main()
