from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter


AGENT_NAMES = ("楊祺雄", "吳俊彥")
DRAWING_MARKERS = ("圖式簡單說明", "圖式之簡單說明")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and verify the latest approved Saint-Island patent PDFs."
    )
    parser.add_argument("manifest", type=Path, help="GPSS source manifest JSON")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=300)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def download_pdf(record: dict[str, Any], pdf_dir: Path) -> tuple[dict[str, Any], Path]:
    rank = int(record["rank"])
    filename = (
        f"{rank:04d}_{record['publication_number']}_{record['application_number']}.pdf"
    )
    target = pdf_dir / filename
    partial = target.with_suffix(".pdf.part")

    if target.exists() and target.stat().st_size > 1024:
        with target.open("rb") as handle:
            if handle.read(5) == b"%PDF-":
                return record, target

    request = urllib.request.Request(
        record["official_pdf_url"],
        headers={"User-Agent": "Saint-Island-Patent-MDS/collection-audit"},
    )
    last_error: Exception | None = None
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                with partial.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
            with partial.open("rb") as handle:
                if handle.read(5) != b"%PDF-":
                    raise RuntimeError("Downloaded file is not a PDF")
            os.replace(partial, target)
            return record, target
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            last_error = exc
            partial.unlink(missing_ok=True)
            if attempt < 4:
                time.sleep(attempt * 1.5)
    raise RuntimeError(f"download failed after 4 attempts: {last_error}")


def find_drawing_start(page_texts: list[str]) -> tuple[int | None, str]:
    normalized = [normalize_text(text) for text in page_texts]
    marker_pages = [
        index
        for index, text in enumerate(normalized)
        if any(marker in text for marker in DRAWING_MARKERS)
    ]
    if not marker_pages:
        return None, "drawing marker not found"

    start = marker_pages[-1] + 1
    if start >= len(page_texts):
        # Some short gazettes place the drawing below the marker on the same
        # final page. Keep that full page and flag it as mixed text/drawing so
        # no figure is lost and downstream dataset preparation can review it.
        return marker_pages[-1], "marker-on-mixed-final-page"
    return start, "marker-based"


def inspect_and_extract(
    record: dict[str, Any], source_pdf: Path, drawings_dir: Path
) -> dict[str, Any]:
    result = dict(record)
    result.update(
        {
            "status": "ok",
            "source_pdf": str(source_pdf),
            "source_bytes": source_pdf.stat().st_size,
            "source_sha256": sha256_file(source_pdf),
            "total_pages": None,
            "agent_yang_verified": False,
            "agent_wu_verified": False,
            "drawing_start_page_1based": None,
            "drawing_page_count": 0,
            "drawing_pdf": "",
            "drawing_extract_method": "",
            "notes": "",
        }
    )

    try:
        reader = PdfReader(str(source_pdf), strict=False)
        result["total_pages"] = len(reader.pages)
        page_texts: list[str] = []
        for page in reader.pages:
            try:
                page_texts.append(page.extract_text() or "")
            except Exception:
                page_texts.append("")

        full_text = normalize_text("\n".join(page_texts))
        result["agent_yang_verified"] = AGENT_NAMES[0] in full_text
        result["agent_wu_verified"] = AGENT_NAMES[1] in full_text

        drawing_start, method = find_drawing_start(page_texts)
        result["drawing_extract_method"] = method
        if drawing_start is None:
            result["status"] = "needs_review"
            result["notes"] = method
            return result

        writer = PdfWriter()
        for index in range(drawing_start, len(reader.pages)):
            writer.add_page(reader.pages[index])

        drawing_name = source_pdf.stem + "_complete_drawings.pdf"
        drawing_target = drawings_dir / drawing_name
        drawing_partial = drawing_target.with_suffix(".pdf.part")
        with drawing_partial.open("wb") as handle:
            writer.write(handle)
        os.replace(drawing_partial, drawing_target)

        result["drawing_start_page_1based"] = drawing_start + 1
        result["drawing_page_count"] = len(reader.pages) - drawing_start
        result["drawing_pdf"] = str(drawing_target)
        if method == "marker-on-mixed-final-page":
            result["status"] = "needs_review"
            result["notes"] = "drawing shares the final page with descriptive text"
        if not all(
            (result["agent_yang_verified"], result["agent_wu_verified"])
        ):
            result["status"] = "needs_review"
            result["notes"] = "agent names not both extractable from PDF text"
        return result
    except Exception as exc:
        result["status"] = "error"
        result["notes"] = f"PDF inspection failed: {type(exc).__name__}: {exc}"
        return result


def write_reports(root: Path, source: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda row: int(row["rank"]))
    summary = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": source.get("source"),
        "query": source.get("query"),
        "filter": source.get("filter"),
        "requested_count": len(rows),
        "downloaded_count": sum(Path(row["source_pdf"]).exists() for row in rows if row.get("source_pdf")),
        "verified_both_agents_count": sum(
            bool(row.get("agent_yang_verified") and row.get("agent_wu_verified"))
            for row in rows
        ),
        "drawing_pdf_count": sum(bool(row.get("drawing_pdf")) for row in rows),
        "drawing_page_total": sum(int(row.get("drawing_page_count") or 0) for row in rows),
        "ok_count": sum(row.get("status") == "ok" for row in rows),
        "needs_review_count": sum(row.get("status") == "needs_review" for row in rows),
        "error_count": sum(row.get("status") == "error" for row in rows),
        "source_bytes_total": sum(int(row.get("source_bytes") or 0) for row in rows),
        "records": rows,
    }
    (root / "collection_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    columns = [
        "rank",
        "announcement_date",
        "application_number",
        "publication_number",
        "title",
        "applicant",
        "official_pdf_url",
        "status",
        "total_pages",
        "drawing_start_page_1based",
        "drawing_page_count",
        "agent_yang_verified",
        "agent_wu_verified",
        "source_bytes",
        "source_sha256",
        "source_pdf",
        "drawing_pdf",
        "drawing_extract_method",
        "notes",
    ]
    with (root / "collection_report.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    source = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = list(source.get("records", []))[: args.limit]
    if not records:
        raise SystemExit("Manifest contains no records")
    if args.limit > 300 or len(records) > 300:
        raise SystemExit("Safety stop: this collector is limited to the latest 300 records")

    expected_ranks = list(range(1, len(records) + 1))
    actual_ranks = [int(record["rank"]) for record in records]
    if actual_ranks != expected_ranks:
        raise SystemExit("Manifest ranks are not a contiguous 1..N sequence")
    if len({record["application_number"] for record in records}) != len(records):
        raise SystemExit("Manifest contains duplicate application numbers")

    root = args.manifest.resolve().parent
    source_dir = root / "source_pdfs"
    drawings_dir = root / "complete_drawings"
    source_dir.mkdir(parents=True, exist_ok=True)
    drawings_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
        futures = {pool.submit(download_pdf, record, source_dir): record for record in records}
        for completed, future in enumerate(as_completed(futures), start=1):
            record = futures[future]
            try:
                returned_record, source_pdf = future.result()
                results.append(inspect_and_extract(returned_record, source_pdf, drawings_dir))
            except Exception as exc:
                failed = dict(record)
                failed.update(
                    {
                        "status": "error",
                        "source_pdf": "",
                        "source_bytes": 0,
                        "source_sha256": "",
                        "total_pages": None,
                        "agent_yang_verified": False,
                        "agent_wu_verified": False,
                        "drawing_start_page_1based": None,
                        "drawing_page_count": 0,
                        "drawing_pdf": "",
                        "drawing_extract_method": "",
                        "notes": f"{type(exc).__name__}: {exc}",
                    }
                )
                results.append(failed)
                failures.append(failed)
            if completed % 25 == 0 or completed == len(records):
                print(f"Completed {completed}/{len(records)}", flush=True)

    write_reports(root, source, results)
    print(
        json.dumps(
            {
                "records": len(results),
                "download_failures": len(failures),
                "report": str(root / "collection_report.json"),
            },
            ensure_ascii=False,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
