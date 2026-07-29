"""Create a human-readable Stage 1 report for a patent DOCX."""

from argparse import ArgumentParser
from html import escape
import json
from pathlib import Path
import re
import sys
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_review import PatentDocxError, parse_docx
from features.patent_review.models import PatentDocument, PatentParagraph


SYSTEM_NAME = "Saint-Island_Patent_MDS"
SYSTEM_FULL_NAME = "Saint-Island Patent Mistake Detection System"


def _safe_stem(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .") or "patent"


def _paragraph_cards(paragraphs: Iterable[PatentParagraph]) -> str:
    cards = []
    for paragraph in paragraphs:
        location = paragraph.source_path
        if paragraph.style_name:
            location += f" · style={paragraph.style_name}"
        classes = "paragraph heading" if paragraph.is_heading else "paragraph"
        original = escape(paragraph.text) or "<span class='empty'>(空白段落)</span>"
        normalized_note = ""
        if paragraph.text != paragraph.normalized_text:
            normalized_note = (
                "<details><summary>正規化後文字</summary><pre>"
                + escape(paragraph.normalized_text)
                + "</pre></details>"
            )
        cards.append(
            f"<article class='{classes}'>"
            f"<div class='meta'>段落 {paragraph.index} · {escape(location)}</div>"
            f"<pre>{original}</pre>{normalized_note}</article>"
        )
    return "".join(cards) or "<p class='empty'>此章節沒有內容。</p>"


def render_html(document: PatentDocument) -> str:
    paragraph_by_index = {paragraph.index: paragraph for paragraph in document.paragraphs}
    section_blocks = []
    for occurrence, section in enumerate(document.sections, start=1):
        paragraphs = [
            paragraph_by_index[index]
            for index in section.paragraph_indices
            if index in paragraph_by_index
        ]
        section_blocks.append(
            f"<section id='section-{occurrence}'>"
            f"<h2>{occurrence}. {escape(section.title)} "
            f"<code>{escape(section.key)}</code></h2>"
            f"<p class='heading-source'>辨識標題：{escape(section.heading_text)}</p>"
            f"{_paragraph_cards(paragraphs)}</section>"
        )

    unassigned = [
        paragraph_by_index[index]
        for index in document.unassigned_paragraph_indices
        if index in paragraph_by_index
    ]
    image_rows = "".join(
        "<tr>"
        f"<td>{escape(image.relationship_id)}</td>"
        f"<td>{escape(image.filename)}</td>"
        f"<td>{escape(image.content_type)}</td>"
        f"<td>{image.size_bytes:,}</td>"
        f"<td>{escape(', '.join(map(str, image.paragraph_indices)) or '-')}</td>"
        f"<td>{escape(' / '.join(image.alt_texts) or '-')}</td>"
        "</tr>"
        for image in document.images
    ) or "<tr><td colspan='6' class='empty'>未找到內嵌圖片。</td></tr>"
    warnings = "".join(f"<li>{escape(item)}</li>" for item in document.warnings)
    if not warnings:
        warnings = "<li>沒有解析警告。</li>"

    patent_type_labels = {
        "invention": "發明",
        "utility_model": "新型",
        "mixed": "混合／需人工確認",
        "unknown": "尚未判別",
    }
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{SYSTEM_NAME} 第一階段解析報告</title>
<style>
body {{ font-family: "Microsoft JhengHei", "Noto Sans TC", sans-serif; margin: 0; color: #1f2937; background: #f3f4f6; }}
header, main {{ max-width: 1180px; margin: auto; }}
header {{ padding: 32px 24px 18px; }}
main {{ padding: 0 24px 48px; }}
h1 {{ margin: 0 0 8px; color: #153e75; }}
h2 {{ margin-top: 0; color: #1e3a5f; }}
.subtitle, .meta, .heading-source {{ color: #64748b; }}
.checkpoint {{ background: #fff7d6; border: 1px solid #eab308; padding: 14px 18px; border-radius: 10px; margin: 18px 0; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; margin: 18px 0; }}
.summary div, section, .panel {{ background: white; border-radius: 10px; padding: 16px; box-shadow: 0 1px 3px #0002; }}
.summary strong {{ display: block; font-size: 1.25rem; margin-top: 5px; overflow-wrap: anywhere; }}
section, .panel {{ margin: 14px 0; }}
.paragraph {{ border-left: 4px solid #94a3b8; padding: 9px 12px; margin: 10px 0; background: #f8fafc; }}
.paragraph.heading {{ border-left-color: #2563eb; }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; margin: 6px 0; }}
code {{ font-size: .75em; background: #e2e8f0; padding: 2px 6px; border-radius: 5px; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
.empty {{ color: #94a3b8; }}
</style>
</head>
<body>
<header>
  <h1>{SYSTEM_NAME}</h1>
  <div class="subtitle">{SYSTEM_FULL_NAME} · 第一階段唯讀 DOCX 解析報告</div>
  <div class="checkpoint"><strong>人工斷點一：</strong>請確認章節標題、段落歸屬、專利名稱、表格文字與圖片數量是否正確。此階段沒有修改原始 Word，也還沒有產生法律或文字錯誤判斷。</div>
</header>
<main>
  <div class="summary">
    <div>檔案<strong>{escape(document.file_name)}</strong></div>
    <div>專利名稱<strong>{escape(document.patent_title or '尚未辨識')}</strong></div>
    <div>類型<strong>{patent_type_labels.get(document.patent_type, escape(document.patent_type))}</strong></div>
    <div>章節<strong>{len(document.sections)}</strong></div>
    <div>段落<strong>{len(document.paragraphs)}</strong></div>
    <div>內嵌圖片<strong>{len(document.images)}</strong></div>
  </div>
  <div class="panel"><h2>解析警告</h2><ul>{warnings}</ul></div>
  {''.join(section_blocks) or '<div class="panel"><h2>未辨識到章節</h2></div>'}
  <div class="panel"><h2>尚未歸類的段落</h2>{_paragraph_cards(unassigned)}</div>
  <div class="panel"><h2>內嵌圖片清單</h2>
    <table><thead><tr><th>關聯</th><th>檔名</th><th>格式</th><th>位元組</th><th>段落</th><th>替代文字</th></tr></thead><tbody>{image_rows}</tbody></table>
  </div>
  <div class="panel"><h2>文件識別</h2><p>SHA-256：<code>{escape(document.sha256)}</code></p><p>來源：{escape(document.source_path)}</p></div>
</main>
</body>
</html>"""


def build_reports(source: Path, output_dir: Path) -> tuple[Path, Path]:
    document = parse_docx(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(source.stem)
    json_path = output_dir / f"{stem}_stage1_parse.json"
    html_path = output_dir / f"{stem}_stage1_parse.html"
    json_path.write_text(
        json.dumps(document.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path.write_text(render_html(document), encoding="utf-8")
    return json_path, html_path


def main() -> int:
    parser = ArgumentParser(description=f"{SYSTEM_NAME} Stage 1 DOCX inspector")
    parser.add_argument("docx", type=Path, help="Patent .docx file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "output" / "patent_review_stage1",
    )
    args = parser.parse_args()
    try:
        json_path, html_path = build_reports(args.docx, args.output_dir)
    except PatentDocxError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    print(f"[OK] {SYSTEM_NAME} Stage 1 parse completed")
    print(f"JSON: {json_path.resolve()}")
    print(f"HTML: {html_path.resolve()}")
    print("Open the HTML report and complete manual checkpoint 1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
