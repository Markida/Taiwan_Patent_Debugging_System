"""Build a resumable raw patent-drawing corpus from official TIPO open data.

This script deliberately does not create labels or train a model.  It downloads
official specification XML and multi-page TIFF files, uses the XML figure count
to select the drawing pages at the end of each TIFF, removes exact duplicates,
and creates patent-grouped train/validation splits.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont


Image.MAX_IMAGE_PIXELS = None

FTP_HOST = "ftps://ftp.tipo.gov.tw"
FTP_USER = "anonymous:codex-corpus@openai.com"
SOURCE_PAGE = "https://cloud.tipo.gov.tw/S220/opdata/detail/PatentPub"
LICENSE_PAGE = "https://data.gov.tw/license"


@dataclass(frozen=True)
class PatentRecord:
    publication_number: str
    application_number: str
    specification_xml_path: str
    specification_tiff_path: str


@dataclass(frozen=True)
class DrawingMeta:
    record: PatentRecord
    figure_count: int
    xml_file: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def parse_batch_index(path: Path) -> list[PatentRecord]:
    text = path.read_text(encoding="utf-8", errors="replace")
    records: list[PatentRecord] = []
    block_re = re.compile(
        r'<tw-patent-application\b[^>]*publication-number="(?P<pub>\d+)"[^>]*>'
        r'(?P<body>.*?)</tw-patent-application>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in block_re.finditer(text):
        body = match.group("body")
        spec = re.search(
            r'<specification-files\b[^>]*xml-file="(?P<xml>[^"]+)"[^>]*>'
            r'.*?<tif\b[^>]*file="(?P<tif>[^"]+)"',
            body,
            re.IGNORECASE | re.DOTALL,
        )
        if not spec:
            continue
        xml_path = spec.group("xml")
        tif_path = spec.group("tif")
        app_match = re.search(r'/([A-Za-z0-9]+)-A\d+\.xml$', xml_path)
        if not app_match:
            app_match = re.search(
                r'<application-reference\b[^>]*>.*?<doc-number>([^<]+)</doc-number>',
                body,
                re.IGNORECASE | re.DOTALL,
            )
        if not app_match:
            continue
        records.append(
            PatentRecord(
                publication_number=match.group("pub"),
                application_number=app_match.group(1).strip(),
                specification_xml_path=xml_path,
                specification_tiff_path=tif_path,
            )
        )
    return records


def xml_ftp_url(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    if normalized.startswith("/InventionPubXML_"):
        return f"{FTP_HOST}/InventionPubXML/115{normalized}"
    return f"{FTP_HOST}{normalized if normalized.startswith('/') else '/' + normalized}"


def ftp_url(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    return f"{FTP_HOST}{normalized if normalized.startswith('/') else '/' + normalized}"


def download_is_complete(path: Path) -> bool:
    try:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                return bool(archive.namelist()) and archive.testzip() is None
        if path.suffix.lower() == ".xml":
            text = read_text_retry(path, attempts=4)
            return text.rstrip().endswith(">") and "</" in text[-500:]
        return path.stat().st_size > 0
    except Exception:
        return False


def curl_download(url: str, destination: Path, attempts: int = 4) -> tuple[bool, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        if download_is_complete(destination):
            return True, "cached"
        unlink_retry(destination, attempts=4)
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        part = destination.with_suffix(
            destination.suffix + f".part.{os.getpid()}.{threading.get_ident()}"
        )
        try:
            part.unlink(missing_ok=True)
            result = subprocess.run(
                [
                    "curl.exe",
                    "--ssl-reqd",
                    "--ftp-pasv",
                    "--user",
                    FTP_USER,
                    "--connect-timeout",
                    "20",
                    "--max-time",
                    "300",
                    "--retry",
                    "2",
                    "--retry-all-errors",
                    "--retry-delay",
                    "2",
                    "--silent",
                    "--show-error",
                    "--output",
                    str(part),
                    url,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=330,
                check=False,
            )
            if part.exists() and (result.returncode == 0 or download_is_complete(part)):
                os.replace(part, destination)
                # Antivirus/indexing software on Windows can briefly retain a
                # handle immediately after curl closes the file.
                time.sleep(0.15)
                return True, result.stderr.strip() or f"curl_exit_{result.returncode}"
            unlink_retry(part, attempts=4)
            errors.append(result.stderr.strip() or f"curl exit {result.returncode}")
            # TIPO's FTPS endpoint temporarily refuses port 990 when clients
            # reconnect too aggressively.  A longer backoff for connection
            # failures lets the server-side throttle clear without discarding
            # an otherwise valid patent record.
            if result.returncode == 7:
                time.sleep(15 * attempt)
        except Exception as exc:  # network/process errors are recorded and retried
            errors.append(f"{type(exc).__name__}: {exc}")
        time.sleep(min(3 * attempt, 12))
    return False, " | ".join(errors[-3:])


def read_text_retry(path: Path, attempts: int = 12) -> str:
    for attempt in range(1, attempts + 1):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(min(0.2 * attempt, 1.5))
    raise RuntimeError(f"Could not read {path}")


def unlink_retry(path: Path, attempts: int = 12) -> None:
    for attempt in range(1, attempts + 1):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == attempts:
                return
            time.sleep(min(0.2 * attempt, 1.5))


def count_official_figures(xml_path: Path) -> int:
    text = read_text_retry(xml_path)
    # The actual full-page figure assets live in <drawings>.  The similarly
    # named <figure-drawings> section is descriptive text and can contain
    # inline formula images, so it must not be used as the primary count.
    sections = re.findall(
        r'<drawings\b[^>]*>(.*?)</drawings>',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not sections:
        sections = re.findall(
            r'<figure-drawings\b[^>]*>(.*?)</figure-drawings>',
            text,
            re.IGNORECASE | re.DOTALL,
        )
    if not sections:
        return 0
    section = sections[-1]
    figure_count = len(re.findall(r'<figure\b', section, re.IGNORECASE))
    image_count = len(re.findall(r'<img\b', section, re.IGNORECASE))
    return max(figure_count, image_count)


def fetch_drawing_meta(record: PatentRecord, xml_cache: Path) -> tuple[DrawingMeta | None, str | None]:
    local_xml = xml_cache / f"{record.publication_number}_{record.application_number}.xml"
    ok, detail = curl_download(xml_ftp_url(record.specification_xml_path), local_xml)
    if not ok:
        return None, f"xml_download_failed: {detail}"
    try:
        figure_count = count_official_figures(local_xml)
    except Exception as exc:
        unlink_retry(local_xml)
        return None, f"xml_parse_failed: {type(exc).__name__}: {exc}"
    if figure_count <= 0:
        return None, "no_official_figures"
    return DrawingMeta(record=record, figure_count=figure_count, xml_file=str(local_xml)), None


def evenly_spaced_indices(start: int, stop: int, limit: int) -> list[int]:
    values = list(range(start, stop))
    if len(values) <= limit:
        return values
    raw = np.linspace(0, len(values) - 1, num=limit)
    selected: list[int] = []
    for position in raw:
        value = values[int(round(float(position)))]
        if value not in selected:
            selected.append(value)
    for value in values:
        if len(selected) >= limit:
            break
        if value not in selected:
            selected.append(value)
    return sorted(selected)


def image_metrics(image: Image.Image) -> dict[str, float]:
    thumb = image.convert("L")
    thumb.thumbnail((320, 320), Image.Resampling.BILINEAR)
    array = np.asarray(thumb)
    ink = array < 210
    return {
        "ink_fraction": round(float(ink.mean()), 6),
        "dark_fraction": round(float((array < 80).mean()), 6),
    }


def process_specification(
    meta: DrawingMeta,
    zip_cache: Path,
    staging: Path,
    max_pages_per_patent: int,
) -> tuple[list[dict], str | None]:
    record = meta.record
    zip_path = zip_cache / f"{record.publication_number}.zip"
    ok, detail = curl_download(ftp_url(record.specification_tiff_path), zip_path)
    if not ok:
        return [], f"tiff_download_failed: {detail}"
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith((".tif", ".tiff"))]
            if not names:
                return [], "zip_has_no_tiff"
            with archive.open(names[0]) as source:
                with tempfile.SpooledTemporaryFile(max_size=96 * 1024 * 1024) as temp_tiff:
                    shutil.copyfileobj(source, temp_tiff)
                    temp_tiff.seek(0)
                    image = Image.open(temp_tiff)
                    frame_count = int(getattr(image, "n_frames", 1))
                    if meta.figure_count > frame_count:
                        return [], f"figure_count_exceeds_frames:{meta.figure_count}>{frame_count}"
                    start = frame_count - meta.figure_count
                    frame_indices = evenly_spaced_indices(start, frame_count, max_pages_per_patent)
                    rows: list[dict] = []
                    for frame_index in frame_indices:
                        image.seek(frame_index)
                        frame = image.convert("1")
                        metrics = image_metrics(frame)
                        if metrics["ink_fraction"] < 0.001 or metrics["ink_fraction"] > 0.42:
                            continue
                        pixel_hash = hashlib.sha256(
                            f"{frame.width}x{frame.height}:1:".encode("ascii") + frame.tobytes()
                        ).hexdigest()
                        filename = (
                            f"TIPO_{record.publication_number}_{record.application_number}_"
                            f"page_{frame_index + 1:04d}.png"
                        )
                        output_path = staging / filename
                        frame.save(output_path, format="PNG", compress_level=6)
                        file_hash = hashlib.sha256(output_path.read_bytes()).hexdigest()
                        rows.append(
                            {
                                "publication_number": record.publication_number,
                                "application_number": record.application_number,
                                "source_xml_path": record.specification_xml_path,
                                "source_tiff_path": record.specification_tiff_path,
                                "source_tiff_frame": frame_index + 1,
                                "source_tiff_frames": frame_count,
                                "official_figure_count": meta.figure_count,
                                "selection_rule": "last_n_tiff_frames_from_official_xml_figure_count",
                                "original_orientation_preserved": True,
                                "width": frame.width,
                                "height": frame.height,
                                "pixel_sha256": pixel_hash,
                                "file_sha256": file_hash,
                                "filename": filename,
                                "staging_path": str(output_path),
                                **metrics,
                            }
                        )
        zip_path.unlink(missing_ok=True)
        return rows, None
    except Exception as exc:
        zip_path.unlink(missing_ok=True)
        return [], f"tiff_process_failed: {type(exc).__name__}: {exc}"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def clear_files(directory: Path) -> None:
    resolved = directory.resolve()
    if not resolved.name in {"train", "val"} or resolved.parent.name != "images":
        raise RuntimeError(f"Refusing to clear unexpected directory: {resolved}")
    directory.mkdir(parents=True, exist_ok=True)
    for child in directory.iterdir():
        if child.is_file():
            child.unlink()


def choose_grouped_split(rows: list[dict], train_count: int, val_count: int, seed: int) -> tuple[list[dict], list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["publication_number"]].append(row)
    patents = sorted(groups, key=lambda value: hashlib.sha256(f"{seed}:{value}".encode()).hexdigest())

    def take(patent_ids: list[str], target: int) -> tuple[list[dict], list[str]]:
        selected: list[dict] = []
        used: list[str] = []
        for patent_id in patent_ids:
            if len(selected) >= target:
                break
            group = sorted(groups[patent_id], key=lambda row: row["source_tiff_frame"])
            remaining = target - len(selected)
            selected.extend(group[:remaining])
            used.append(patent_id)
        return selected, used

    val, val_patents = take(patents, val_count)
    train, _ = take([patent for patent in patents if patent not in set(val_patents)], train_count)
    if len(train) != train_count or len(val) != val_count:
        raise RuntimeError(
            f"Not enough grouped images for requested split: train={len(train)}/{train_count}, "
            f"val={len(val)}/{val_count}"
        )
    if {row["publication_number"] for row in train} & {row["publication_number"] for row in val}:
        raise RuntimeError("Patent leakage detected between train and validation sets")
    return train, val


def link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def build_contact_sheet(rows: list[dict], staging: Path, output_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    sample = rng.sample(rows, min(30, len(rows)))
    cell_w, cell_h = 360, 480
    columns = 5
    rows_count = (len(sample) + columns - 1) // columns
    canvas = Image.new("RGB", (cell_w * columns, cell_h * rows_count), "#d9dde3")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for index, row in enumerate(sample):
        source = staging / row["filename"]
        with Image.open(source) as image:
            preview = image.convert("RGB")
            preview.thumbnail((cell_w - 18, cell_h - 48), Image.Resampling.LANCZOS)
        x = (index % columns) * cell_w
        y = (index // columns) * cell_h
        px = x + (cell_w - preview.width) // 2
        py = y + 8
        canvas.paste(preview, (px, py))
        draw.text((x + 8, y + cell_h - 34), row["filename"], fill="black", font=font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", compress_level=6)


def finalize_corpus(output: Path, train_count: int, val_count: int, seed: int) -> dict:
    staging = output / "cache" / "staging"
    raw_rows = read_jsonl(output / "manifest_raw.jsonl")
    existing_rows = [row for row in raw_rows if (staging / row["filename"]).exists()]
    unique_by_pixels: dict[str, dict] = {}
    duplicates = 0
    for row in existing_rows:
        key = row["pixel_sha256"]
        if key in unique_by_pixels:
            duplicates += 1
            continue
        unique_by_pixels[key] = row
    unique_rows = list(unique_by_pixels.values())
    train_rows, val_rows = choose_grouped_split(unique_rows, train_count, val_count, seed)

    train_dir = output / "images" / "train"
    val_dir = output / "images" / "val"
    clear_files(train_dir)
    clear_files(val_dir)
    final_rows: list[dict] = []
    for split, rows, destination in (("train", train_rows, train_dir), ("val", val_rows, val_dir)):
        for row in rows:
            source = staging / row["filename"]
            target = destination / row["filename"]
            link_or_copy(source, target)
            portable_row = {key: value for key, value in row.items() if key != "staging_path"}
            final_rows.append({**portable_row, "split": split, "path": str(target.relative_to(output))})

    manifest_path = output / "manifest.jsonl"
    manifest_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in final_rows),
        encoding="utf-8",
    )
    csv_path = output / "manifest.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = list(final_rows[0])
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(final_rows)

    integrity_path = output / "integrity_sha256.txt"
    integrity_path.write_text(
        "".join(f"{row['file_sha256']}  {row['path'].replace(os.sep, '/')}\n" for row in final_rows),
        encoding="utf-8",
    )
    build_contact_sheet(train_rows, staging, output / "qa" / "train_contact_sheet.png", seed + 1)
    build_contact_sheet(val_rows, staging, output / "qa" / "val_contact_sheet.png", seed + 2)

    train_patents = {row["publication_number"] for row in train_rows}
    val_patents = {row["publication_number"] for row in val_rows}
    stats = {
        "created_at_utc": utc_now(),
        "source": "Taiwan Intellectual Property Office (TIPO) Patent Open Data",
        "source_page": SOURCE_PAGE,
        "license_page": LICENSE_PAGE,
        "training_started": False,
        "labels_created": False,
        "orientation_policy": "official original orientation preserved",
        "split_policy": "grouped by publication_number; no patent appears in both splits",
        "train_images": len(train_rows),
        "validation_images": len(val_rows),
        "total_images": len(final_rows),
        "train_patents": len(train_patents),
        "validation_patents": len(val_patents),
        "patent_overlap": len(train_patents & val_patents),
        "raw_manifest_rows": len(raw_rows),
        "unique_staging_images": len(unique_rows),
        "exact_duplicates_excluded": duplicates,
        "extensions": dict(Counter(Path(row["filename"]).suffix.lower() for row in final_rows)),
        "ink_fraction": {
            "min": min(row["ink_fraction"] for row in final_rows),
            "max": max(row["ink_fraction"] for row in final_rows),
            "mean": round(float(np.mean([row["ink_fraction"] for row in final_rows])), 6),
        },
    }
    (output / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return stats


def build_corpus(args: argparse.Namespace) -> int:
    index_path = Path(args.batch_index).resolve()
    output = Path(args.output).resolve()
    if not index_path.is_file():
        raise FileNotFoundError(index_path)
    output.mkdir(parents=True, exist_ok=True)
    staging = output / "cache" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    xml_cache = output / "cache" / "xml"
    zip_cache = output / "cache" / "zip"
    xml_cache.mkdir(parents=True, exist_ok=True)
    zip_cache.mkdir(parents=True, exist_ok=True)

    records = parse_batch_index(index_path)
    if not records:
        raise RuntimeError("No specification records found in batch index")
    rng = random.Random(args.seed)
    rng.shuffle(records)
    if args.max_records:
        records = records[: args.max_records]
    log(f"Parsed {len(records)} official patent records")

    raw_manifest = output / "manifest_raw.jsonl"
    rejection_log = output / "rejected.jsonl"
    existing = read_jsonl(raw_manifest)
    processed_patents = {row["publication_number"] for row in existing}
    existing_pixels = {row["pixel_sha256"] for row in existing}
    unique_existing = len(existing_pixels)
    needed = max(0, args.train_count + args.val_count + args.reserve - unique_existing)
    if needed == 0:
        log(f"Staging already contains {unique_existing} unique images; skipping downloads")
    else:
        available = [record for record in records if record.publication_number not in processed_patents]
        log(f"Need about {needed} more images; scanning official XML metadata with {args.workers} workers")
        metas: list[DrawingMeta] = []
        expected_pages = 0
        scanned = 0
        metadata_buffer = min(100, max(10, args.reserve // 2))
        metadata_target = needed + metadata_buffer
        batch_size = max(args.workers * 3, 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            for offset in range(0, len(available), batch_size):
                batch = available[offset : offset + batch_size]
                results = list(executor.map(lambda record: fetch_drawing_meta(record, xml_cache), batch))
                for record, (meta, error) in zip(batch, results):
                    scanned += 1
                    if meta is not None:
                        xml_kb = Path(meta.xml_file).stat().st_size / 1024
                        if xml_kb > args.max_spec_xml_kb:
                            append_jsonl(
                                rejection_log,
                                [{
                                    "time_utc": utc_now(),
                                    "phase": "metadata",
                                    "record": asdict(record),
                                    "reason": f"specification_xml_above_collection_cap_kb:{xml_kb:.1f}",
                                }],
                            )
                        elif meta.figure_count <= args.max_official_figures:
                            metas.append(meta)
                            expected_pages += min(meta.figure_count, args.max_pages_per_patent)
                        else:
                            append_jsonl(
                                rejection_log,
                                [{
                                    "time_utc": utc_now(),
                                    "phase": "metadata",
                                    "record": asdict(record),
                                    "reason": f"official_figure_count_above_collection_cap:{meta.figure_count}",
                                }],
                            )
                    elif error and error != "no_official_figures":
                        append_jsonl(
                            rejection_log,
                            [{"time_utc": utc_now(), "phase": "metadata", "record": asdict(record), "reason": error}],
                        )
                    if scanned % 25 == 0:
                        log(f"XML scanned={scanned}, usable_patents={len(metas)}, expected_pages={expected_pages}")
                if expected_pages >= metadata_target:
                    break

        metas.sort(key=lambda item: Path(item.xml_file).stat().st_size)
        log(f"Downloading multi-page TIFFs for {len(metas)} patents (expected up to {expected_pages} pages)")
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {
                executor.submit(
                    process_specification,
                    meta,
                    zip_cache,
                    staging,
                    args.max_pages_per_patent,
                ): meta
                for meta in metas
            }
            completed = 0
            for future in concurrent.futures.as_completed(future_map):
                meta = future_map[future]
                completed += 1
                try:
                    rows, error = future.result()
                except Exception as exc:
                    rows, error = [], f"worker_failed: {type(exc).__name__}: {exc}"
                accepted: list[dict] = []
                for row in rows:
                    if row["pixel_sha256"] in existing_pixels:
                        Path(row["staging_path"]).unlink(missing_ok=True)
                        append_jsonl(
                            rejection_log,
                            [{"time_utc": utc_now(), "phase": "dedup", "record": asdict(meta.record), "reason": "exact_pixel_duplicate", "filename": row["filename"]}],
                        )
                        continue
                    existing_pixels.add(row["pixel_sha256"])
                    accepted.append(row)
                if accepted:
                    append_jsonl(raw_manifest, accepted)
                if error:
                    append_jsonl(
                        rejection_log,
                        [{"time_utc": utc_now(), "phase": "tiff", "record": asdict(meta.record), "reason": error}],
                    )
                if completed % 10 == 0 or completed == len(future_map):
                    log(
                        f"TIFF patents={completed}/{len(future_map)}, "
                        f"unique_staging={len(existing_pixels)}"
                    )

    log("Creating the exact patent-grouped train/validation split")
    stats = finalize_corpus(output, args.train_count, args.val_count, args.seed)
    log(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-index", required=True, help="TIPO InventionPubXML index_all.xml")
    parser.add_argument("--output", required=True, help="Corpus output directory")
    parser.add_argument("--train-count", type=int, default=5000)
    parser.add_argument("--val-count", type=int, default=1000)
    parser.add_argument("--reserve", type=int, default=250, help="Extra staging images collected for QA/dedup")
    parser.add_argument("--max-pages-per-patent", type=int, default=12)
    parser.add_argument(
        "--max-official-figures",
        type=int,
        default=40,
        help="Skip unusually huge specifications to improve corpus diversity and download time",
    )
    parser.add_argument(
        "--max-spec-xml-kb",
        type=float,
        default=200.0,
        help="Skip unusually text-heavy specifications; smaller XMLs are downloaded first",
    )
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260722)
    parser.add_argument("--max-records", type=int, default=0, help="Nonzero value limits records for a smoke test")
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(build_corpus(make_parser().parse_args()))
    except KeyboardInterrupt:
        log("Interrupted. Existing XML, images, and manifests are safe to resume.")
        raise SystemExit(130)
    except Exception as exc:
        log(f"ERROR: {type(exc).__name__}: {exc}")
        raise
