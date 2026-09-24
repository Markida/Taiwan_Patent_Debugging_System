"""Build Stage 3 reports for document-symbol extraction and OCR comparison."""

from argparse import ArgumentParser
from html import escape
import json
from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_review import (
    FULL_SYMBOL_SOURCE,
    REPRESENTATIVE_SYMBOL_SOURCE,
    SOURCE_TITLES,
    compare_document_symbols_with_ocr,
    extract_document_symbols,
    parse_docx,
    review_document,
)


SYMBOL_SOURCES = (FULL_SYMBOL_SOURCE, REPRESENTATIVE_SYMBOL_SOURCE)


def _safe_stem(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .") or "patent"


def _labels(values) -> str:
    return escape(", ".join(values) if values else "無")


def _load_ocr_results(path: Path | None):
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("all_results"), list):
        return payload["all_results"]
    raise ValueError("OCR JSON 必須是結果陣列，或含有 all_results 陣列。")


def _source_panel(transfer, symbol_source, cross_check) -> str:
    entries = transfer.entries_for(symbol_source)
    ready = transfer.is_source_ready(symbol_source)
    rows = "".join(
        "<tr>"
        f"<td><strong>{escape(entry.label)}</strong></td>"
        f"<td>{escape(entry.name)}</td>"
        f"<td>{entry.paragraph_index}</td>"
        "</tr>"
        for entry in entries
    ) or "<tr><td colspan='3'>沒有可用標號</td></tr>"
    comparison = "<p class='muted'>本次沒有提供 OCR 結果，只驗證清單擷取。</p>"
    if cross_check is not None:
        comparison = (
            "<div class='comparison'>"
            f"<p><strong>全部圖片偵測：</strong>{_labels(cross_check.detected_labels_all_images)}</p>"
            f"<p><strong>文件有、全部圖片都沒有：</strong>{_labels(cross_check.document_labels_missing_from_all_images)}</p>"
            f"<p><strong>圖片有、此文件清單沒有：</strong>{_labels(cross_check.detected_labels_missing_from_document)}</p>"
            f"<p><strong>低信心待確認：</strong>{_labels(cross_check.labels_needing_confirmation)}</p>"
            "</div>"
        )
        image_rows = "".join(
            "<tr>"
            f"<td>{escape(image.image_name)}</td>"
            f"<td>{_labels(image.detected_labels)}</td>"
            f"<td>{_labels(image.document_labels_not_on_image)}</td>"
            f"<td>{_labels(image.image_labels_not_in_document)}</td>"
            f"<td>{_labels(image.labels_needing_confirmation)}</td>"
            "</tr>"
            for image in cross_check.images
        )
        comparison += (
            "<div class='table-wrap'><table><thead><tr>"
            "<th>圖片</th><th>偵測標號</th><th>此張未出現</th>"
            "<th>清單未列</th><th>待確認</th></tr></thead>"
            f"<tbody>{image_rows}</tbody></table></div>"
        )
    return (
        "<section class='panel'>"
        f"<h2>{escape(SOURCE_TITLES[symbol_source])}</h2>"
        f"<p class='status {'ready' if ready else 'blocked'}'>"
        f"{'可交接 OCR' if ready else '需要人工確認'} · {len(entries)} 個標號</p>"
        "<table><thead><tr><th>標號</th><th>元件名稱</th><th>來源段落</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>{comparison}</section>"
    )


def render_html(transfer, cross_checks) -> str:
    warnings = "".join(
        "<li>"
        f"<strong>{escape(SOURCE_TITLES.get(warning.symbol_source, '文件'))}</strong>："
        f"{escape(warning.message)}"
        "</li>"
        for warning in transfer.warnings
    ) or "<li>兩套清單皆無擷取警告。</li>"
    panels = "".join(
        _source_panel(transfer, source, cross_checks.get(source))
        for source in SYMBOL_SOURCES
    )
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Saint-Island_Patent_MDS 人工斷點三</title>
<style>
body {{ margin:0; background:#eef2f7; color:#172033; font-family:"Microsoft JhengHei",sans-serif; }}
header,main {{ max-width:1180px; margin:auto; padding:24px; }}
h1,h2 {{ color:#153e75; }} .checkpoint {{ background:#fff7d6; border:1px solid #eab308; padding:14px; border-radius:10px; }}
.panel {{ background:white; margin:16px 0; padding:18px; border-radius:10px; box-shadow:0 1px 3px #0002; }}
.status {{ display:inline-block; padding:5px 10px; border-radius:999px; }} .ready {{ background:#dcfce7; color:#166534; }} .blocked {{ background:#fee2e2; color:#991b1b; }}
table {{ width:100%; border-collapse:collapse; }} th,td {{ padding:8px; text-align:left; border-bottom:1px solid #e2e8f0; vertical-align:top; }}
.table-wrap {{ overflow:auto; margin-top:12px; }} .muted {{ color:#64748b; }} .comparison {{ background:#f8fafc; padding:10px 14px; margin-top:14px; border-radius:8px; }}
</style></head><body><header><h1>Saint-Island_Patent_MDS</h1>
<p>階段三：文件符號清單與 OCR 圖式標號交叉比對</p>
<div class="checkpoint"><strong>人工斷點三：</strong>請確認完整符號說明與代表圖符號說明是否分開擷取、標號與名稱是否正確，以及兩套清單的差異結果是否符合預期。本階段不修改 Word。</div>
</header><main><section class="panel"><h2>文件</h2>
<p><strong>{escape(transfer.file_name)}</strong> · {escape(transfer.patent_title or '未辨識名稱')}</p>
<p>OCR 頁預設採用「完整符號說明」，使用者可用清單切換按鍵改採「代表圖符號說明」。兩套人工修改分別保存。</p>
<h3>擷取提示</h3><ul>{warnings}</ul></section>{panels}</main></body></html>"""


def build_reports(docx_path: Path, ocr_json: Path | None, output_dir: Path, confidence: float):
    document = parse_docx(docx_path)
    review = review_document(document)
    transfer = extract_document_symbols(document, review)
    ocr_results = _load_ocr_results(ocr_json)
    cross_checks = {}
    if ocr_results is not None:
        for source in SYMBOL_SOURCES:
            cross_checks[source] = compare_document_symbols_with_ocr(
                transfer,
                ocr_results,
                confidence_threshold=confidence,
                symbol_source=source,
            )

    payload = {
        "transfer": transfer.to_dict(),
        "cross_checks": {
            source: result.to_dict() for source, result in cross_checks.items()
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(docx_path.stem)
    json_path = output_dir / f"{stem}_stage3_cross_review.json"
    html_path = output_dir / f"{stem}_stage3_cross_review.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_html(transfer, cross_checks), encoding="utf-8")
    return json_path, html_path, transfer, cross_checks


def main() -> int:
    parser = ArgumentParser(description="Saint-Island_Patent_MDS Stage 3 checker")
    parser.add_argument("docx", type=Path)
    parser.add_argument("--ocr-json", type=Path)
    parser.add_argument("--confidence", type=float, default=0.60)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "output" / "patent_review_stage3",
    )
    args = parser.parse_args()
    try:
        json_path, html_path, transfer, checks = build_reports(
            args.docx, args.ocr_json, args.output_dir, args.confidence
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    print("[OK] Stage 3 cross-reference report completed")
    print(
        f"Full={len(transfer.full_entries)}, "
        f"Representative={len(transfer.representative_entries)}, "
        f"OCR comparison={'yes' if checks else 'no'}"
    )
    print(f"JSON: {json_path.resolve()}")
    print(f"HTML: {html_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
