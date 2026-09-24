from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_COLLECTION = SCRIPT_DIR / "saint_island_latest_300_20260727"
DEFAULT_OUTPUT = DEFAULT_COLLECTION / "preprocessed_corpus"
DEFAULT_POPPLER = Path(
    os.environ.get("PDFTOPPM", shutil.which("pdftoppm") or "pdftoppm")
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the 300-case issued-patent drawing PDFs into a clean page corpus."
    )
    parser.add_argument("--collection", type=Path, default=DEFAULT_COLLECTION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pdftoppm", type=Path, default=DEFAULT_POPPLER)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--threshold", type=int, default=235)
    parser.add_argument("--top-mask-ratio", type=float, default=0.07)
    parser.add_argument("--bottom-mask-ratio", type=float, default=0.06)
    parser.add_argument("--validation-pages", type=int, default=175)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260727)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha256(image: Image.Image) -> str:
    digest = hashlib.sha256()
    digest.update(image.mode.encode("ascii"))
    digest.update(f"{image.width}x{image.height}".encode("ascii"))
    digest.update(image.tobytes())
    return digest.hexdigest()


def link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def safe_clear_output(output: Path, overwrite: bool) -> None:
    if not output.exists() or not any(output.iterdir()):
        return
    if not overwrite:
        raise FileExistsError(f"Output is not empty: {output}. Use --overwrite to rebuild it.")
    output = output.resolve()
    collection = DEFAULT_COLLECTION.resolve()
    if collection not in output.parents:
        raise RuntimeError(f"Refusing to remove output outside the collection folder: {output}")
    shutil.rmtree(output)


def render_pdf(
    record: dict[str, Any],
    output: Path,
    pdftoppm: Path,
    dpi: int,
    threshold: int,
    top_mask_ratio: float,
    bottom_mask_ratio: float,
) -> list[dict[str, Any]]:
    drawing_pdf = Path(record["drawing_pdf"])
    expected = int(record["drawing_page_count"])
    status = str(record["status"])
    rank = int(record["rank"])
    publication = str(record["publication_number"])
    application = str(record["application_number"])
    page_status = "clean" if status == "ok" else "mixed_text_review"
    rendered_dir = output / "rendered_pages" / page_status
    rendered_dir.mkdir(parents=True, exist_ok=True)

    expected_paths = [
        rendered_dir
        / f"SAINT_{rank:04d}_{publication}_{application}_page_{page_number:04d}.png"
        for page_number in range(1, expected + 1)
    ]
    if all(path.exists() for path in expected_paths):
        source_paths = expected_paths
    else:
        with tempfile.TemporaryDirectory(prefix=f"saint_{rank:04d}_") as temp_name:
            prefix = Path(temp_name) / "page"
            command = [
                str(pdftoppm),
                "-r",
                str(dpi),
                "-gray",
                "-png",
                str(drawing_pdf),
                str(prefix),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"pdftoppm failed for rank {rank}: {completed.stderr.strip()}"
                )
            temporary_pages = sorted(
                Path(temp_name).glob("page-*.png"),
                key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
            )
            if len(temporary_pages) != expected:
                raise RuntimeError(
                    f"rank {rank}: expected {expected} pages, rendered {len(temporary_pages)}"
                )
            for temporary, target in zip(temporary_pages, expected_paths):
                with Image.open(temporary) as source:
                    gray = ImageOps.grayscale(source)
                    width, height = gray.size
                    draw = ImageDraw.Draw(gray)
                    top = round(height * top_mask_ratio)
                    bottom = round(height * (1.0 - bottom_mask_ratio))
                    draw.rectangle((0, 0, width, max(0, top - 1)), fill=255)
                    draw.rectangle((0, min(height, bottom), width, height), fill=255)
                    monochrome = gray.point(
                        lambda value: 255 if value > threshold else 0,
                        mode="1",
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary_target = target.with_suffix(".png.part")
                    monochrome.save(temporary_target, format="PNG", optimize=True)
                    os.replace(temporary_target, target)
        source_paths = expected_paths

    rows: list[dict[str, Any]] = []
    for page_number, target in enumerate(source_paths, start=1):
        with Image.open(target) as image:
            image.load()
            width, height = image.size
            mode = image.mode
            pixel_hash = pixel_sha256(image)
            histogram = image.histogram()
            black_fraction = histogram[0] / max(1, width * height)
        rows.append(
            {
                "rank": rank,
                "announcement_date": record["announcement_date"],
                "publication_number": publication,
                "application_number": application,
                "title": record["title"],
                "source_pdf": str(drawing_pdf),
                "source_pdf_page": page_number,
                "source_extract_status": status,
                "preprocess_status": page_status,
                "filename": target.name,
                "rendered_path": str(target.resolve()),
                "width": width,
                "height": height,
                "mode": mode,
                "dpi": dpi,
                "threshold": threshold,
                "top_mask_ratio": top_mask_ratio,
                "bottom_mask_ratio": bottom_mask_ratio,
                "ink_fraction": round(black_fraction, 6),
                "file_sha256": file_sha256(target),
                "pixel_sha256": pixel_hash,
                "split": "manual_page_cleanup" if page_status != "clean" else "pending",
                "path": "",
            }
        )
    return rows


def exact_group_split(
    rows: list[dict[str, Any]], target_pages: int, seed: int
) -> set[str]:
    groups: dict[str, int] = Counter(row["publication_number"] for row in rows)
    items = list(groups.items())
    random.Random(seed).shuffle(items)
    reachable: dict[int, tuple[str, ...]] = {0: ()}
    for publication, count in items:
        additions: dict[int, tuple[str, ...]] = {}
        for total, selected in list(reachable.items()):
            candidate = total + count
            if candidate <= target_pages and candidate not in reachable:
                additions[candidate] = selected + (publication,)
        reachable.update(additions)
        if target_pages in reachable:
            return set(reachable[target_pages])
    closest = max(reachable)
    return set(reachable[closest])


def make_contact_sheet(
    rows: list[dict[str, Any]], output: Path, seed: int, title: str
) -> None:
    if not rows:
        return
    selected = random.Random(seed).sample(rows, min(20, len(rows)))
    columns, cell_w, cell_h = 4, 360, 500
    rows_count = (len(selected) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_w, rows_count * cell_h + 50), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    draw.text((12, 12), title, fill="black", font=font)
    for index, row in enumerate(selected):
        with Image.open(row["rendered_path"]) as image:
            thumb = ImageOps.contain(image.convert("RGB"), (cell_w - 20, cell_h - 55))
        x = (index % columns) * cell_w + (cell_w - thumb.width) // 2
        y = (index // columns) * cell_h + 42
        sheet.paste(thumb, (x, y))
        draw.text(
            ((index % columns) * cell_w + 8, (index // columns + 1) * cell_h + 26),
            f"#{row['rank']} p{row['source_pdf_page']} {row['publication_number']}",
            fill="black",
            font=font,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)


def write_manifest(output: Path, rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda row: (int(row["rank"]), int(row["source_pdf_page"])))
    with (output / "manifest.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    collection = args.collection.resolve()
    output = args.output.resolve()
    report_path = collection / "collection_report.json"
    if not report_path.exists():
        raise FileNotFoundError(report_path)
    if not args.pdftoppm.exists():
        raise FileNotFoundError(args.pdftoppm)
    safe_clear_output(output, args.overwrite)
    output.mkdir(parents=True, exist_ok=True)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    records = [row for row in report["records"] if row.get("drawing_pdf")]
    expected_total = sum(int(row["drawing_page_count"]) for row in records)
    if expected_total != 1355:
        raise RuntimeError(f"Expected 1355 drawing pages, found {expected_total}")

    pages: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    workers = max(1, min(int(args.workers), 4))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                render_pdf,
                record,
                output,
                args.pdftoppm.resolve(),
                args.dpi,
                args.threshold,
                args.top_mask_ratio,
                args.bottom_mask_ratio,
            ): record
            for record in records
        }
        for completed_count, future in enumerate(as_completed(futures), start=1):
            record = futures[future]
            try:
                pages.extend(future.result())
            except Exception as exc:
                failures.append(
                    {
                        "rank": record["rank"],
                        "publication_number": record["publication_number"],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            if completed_count % 25 == 0 or completed_count == len(records):
                print(f"rendered patents {completed_count}/{len(records)}", flush=True)

    if failures:
        (output / "render_errors.json").write_text(
            json.dumps(failures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        raise RuntimeError(f"Rendering failed for {len(failures)} patents")
    if len(pages) != expected_total:
        raise RuntimeError(f"Expected {expected_total} pages, created {len(pages)}")

    clean = [row for row in pages if row["preprocess_status"] == "clean"]
    mixed = [row for row in pages if row["preprocess_status"] != "clean"]
    seen_pixels: dict[str, dict[str, Any]] = {}
    unique_clean: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for row in clean:
        existing = seen_pixels.get(row["pixel_sha256"])
        if existing is None:
            seen_pixels[row["pixel_sha256"]] = row
            unique_clean.append(row)
        else:
            row["split"] = "duplicate_excluded"
            row["duplicate_of"] = existing["filename"]
            duplicates.append(row)

    validation_publications = exact_group_split(
        unique_clean, args.validation_pages, args.seed
    )
    for row in unique_clean:
        split = "val" if row["publication_number"] in validation_publications else "train"
        source = Path(row["rendered_path"])
        relative = Path("images") / split / row["filename"]
        link_or_copy(source, output / relative)
        row["split"] = split
        row["path"] = relative.as_posix()
    for row in mixed:
        source = Path(row["rendered_path"])
        relative = Path("manual_page_cleanup") / row["filename"]
        link_or_copy(source, output / relative)
        row["path"] = relative.as_posix()

    write_manifest(output, pages)
    make_contact_sheet(
        [row for row in pages if row["split"] == "train"],
        output / "contact_sheet_train.png",
        args.seed,
        "Saint-Island approved cases - train sample",
    )
    make_contact_sheet(
        [row for row in pages if row["split"] == "val"],
        output / "contact_sheet_val.png",
        args.seed + 1,
        "Saint-Island approved cases - untouched holdout sample",
    )
    make_contact_sheet(
        mixed,
        output / "contact_sheet_manual_page_cleanup.png",
        args.seed + 2,
        "Mixed text/drawing pages - excluded pending cleanup",
    )

    split_counts = Counter(row["split"] for row in pages)
    patent_counts = {
        split: len({row["publication_number"] for row in pages if row["split"] == split})
        for split in ("train", "val", "manual_page_cleanup")
    }
    size_counts = Counter(f"{row['width']}x{row['height']}" for row in pages)
    mode_counts = Counter(row["mode"] for row in pages)
    stats = {
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "training_started": False,
        "source_collection": str(collection),
        "expected_pdf_pages": expected_total,
        "rendered_pages": len(pages),
        "clean_pages": len(clean),
        "mixed_text_drawing_pages": len(mixed),
        "exact_pixel_duplicates_excluded": len(duplicates),
        "split_counts": dict(sorted(split_counts.items())),
        "patent_counts": patent_counts,
        "publication_overlap_train_val": sorted(
            {row["publication_number"] for row in pages if row["split"] == "train"}
            & {row["publication_number"] for row in pages if row["split"] == "val"}
        ),
        "image_sizes": dict(size_counts),
        "image_modes": dict(mode_counts),
        "render_dpi": args.dpi,
        "binarization_threshold": args.threshold,
        "page_margin_policy": {
            "top_mask_ratio": args.top_mask_ratio,
            "bottom_mask_ratio": args.bottom_mask_ratio,
            "method": "masked white without resizing or rotation",
        },
        "validation_policy": (
            "exact page target by whole publication-number groups; no patent leakage; "
            "unlabelled qualitative holdout only"
        ),
        "manual_page_cleanup_policy": (
            "11 mixed descriptive-text/drawing pages rendered but excluded from train and holdout"
        ),
        "manifest": str(output / "manifest.jsonl"),
    }
    (output / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
