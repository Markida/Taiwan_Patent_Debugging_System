"""Run Saint-Island_Patent_MDS Stage 2 text rules on a patent DOCX."""

from argparse import ArgumentParser
from html import escape
import json
from pathlib import Path
import re
import sys
from typing import Dict, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_review import (
    PatentDocxError,
    PatentTextReview,
    parse_docx,
    review_document,
)
from features.patent_review.models import PatentDocument, PatentIssue


SYSTEM_NAME = "Saint-Island_Patent_MDS"
SYSTEM_FULL_NAME = "Saint-Island Patent Mistake Detection System"


def _safe_stem(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .") or "patent"


def _highlight_text(text: str, start: int | None, end: int | None) -> str:
    if start is None or end is None or not (0 <= start <= end <= len(text)):
        return f"<pre>{escape(text)}</pre>"
    before = escape(text[:start])
    target = escape(text[start:end]) or "&#8203;"
    after = escape(text[end:])
    return f"<pre>{before}<mark>{target}</mark>{after}</pre>"


def _issue_card(
    issue: PatentIssue,
    paragraphs: Dict[int, object],
) -> str:
    severity_labels = {"error": "錯誤", "warning": "警告", "info": "提示"}
    paragraph = (
        paragraphs.get(issue.paragraph_index)
        if issue.paragraph_index is not None
        else None
    )
    if paragraph is None:
        location = "文件層級"
        source = "<p class='muted'>此問題不限定於單一段落。</p>"
    else:
        location = f"段落 {paragraph.index} · {paragraph.source_path}"
        if issue.char_start is not None and issue.char_end is not None:
            location += f" · 字元 {issue.char_start}:{issue.char_end}"
        if issue.run_indices:
            location += " · Word run " + ", ".join(map(str, issue.run_indices))
        source = _highlight_text(
            paragraph.text,
            issue.char_start,
            issue.char_end,
        )
    auto_fix = ""
    if issue.safe_auto_fix:
        auto_fix = (
            "<div class='autofix'><strong>安全替換候選：</strong><code>"
            + escape(issue.replacement or "")
            + "</code>（本階段不會寫回 Word）</div>"
        )
    section = issue.section_title or issue.section_key or "整份文件"
    return (
        f"<article class='issue {escape(issue.severity)}'>"
        f"<div class='issue-head'><span class='badge'>{severity_labels.get(issue.severity, escape(issue.severity))}</span>"
        f"<code>{escape(issue.rule_id)}</code><strong>{escape(section)}</strong></div>"
        f"<h3>{escape(issue.message)}</h3>"
        f"<div class='location'>{escape(location)}</div>{source}"
        f"<p><strong>建議：</strong>{escape(issue.suggestion)}</p>{auto_fix}"
        f"<div class='issue-id'>Issue ID: {escape(issue.issue_id)}</div>"
        "</article>"
    )


def render_html(document: PatentDocument, review: PatentTextReview) -> str:
    payload = review.to_dict()
    counts = payload["summary"]["by_severity"]
    paragraph_map = {paragraph.index: paragraph for paragraph in document.paragraphs}
    issue_cards = "".join(
        _issue_card(issue, paragraph_map) for issue in review.issues
    )
    if not issue_cards:
        issue_cards = (
            "<div class='no-issues'><strong>目前規則沒有找到問題。</strong>"
            "<p>這只代表本階段已啟用的確定性規則通過，不代表完成法律審查。</p></div>"
        )
    rule_rows = "".join(
        "<tr>"
        f"<td><code>{escape(rule.rule_id)}</code></td>"
        f"<td>{escape(rule.title)}</td>"
        f"<td>{escape(rule.default_severity)}</td>"
        f"<td>{escape(rule.description)}</td>"
        "</tr>"
        for rule in review.rule_catalog
    )
    warnings = "".join(
        f"<li>{escape(warning)}</li>" for warning in review.parse_warnings
    ) or "<li>沒有第一階段解析警告。</li>"
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{SYSTEM_NAME} 階段二文字檢核報告</title>
<style>
:root {{ color-scheme: light; }}
body {{ font-family: "Microsoft JhengHei", "Noto Sans TC", sans-serif; margin: 0; color: #172033; background: #eef2f7; }}
header, main {{ max-width: 1180px; margin: auto; }}
header {{ padding: 32px 24px 18px; }}
main {{ padding: 0 24px 56px; }}
h1 {{ color: #153e75; margin: 0 0 8px; }}
h2 {{ color: #1e3a5f; }}
.subtitle, .muted, .location, .issue-id {{ color: #64748b; }}
.checkpoint {{ margin-top: 18px; padding: 15px 18px; border: 1px solid #eab308; border-radius: 10px; background: #fff7d6; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(155px,1fr)); gap: 10px; margin: 18px 0; }}
.summary div, .panel, .issue, .no-issues {{ background: #fff; border-radius: 10px; padding: 16px; box-shadow: 0 1px 3px #0002; }}
.summary strong {{ display: block; margin-top: 5px; font-size: 1.35rem; overflow-wrap: anywhere; }}
.panel {{ margin: 14px 0; overflow-x: auto; }}
.issue {{ margin: 12px 0; border-left: 6px solid #64748b; }}
.issue.error {{ border-left-color: #c62828; }}
.issue.warning {{ border-left-color: #d97706; }}
.issue.info {{ border-left-color: #2563eb; }}
.issue-head {{ display: flex; gap: 9px; align-items: center; flex-wrap: wrap; }}
.issue h3 {{ margin: 12px 0 6px; }}
.badge {{ color: white; background: #64748b; padding: 2px 8px; border-radius: 999px; font-size: .82rem; }}
.error .badge {{ background: #c62828; }} .warning .badge {{ background: #d97706; }} .info .badge {{ background: #2563eb; }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; background: #f8fafc; padding: 11px; border-radius: 7px; font: inherit; line-height: 1.65; }}
mark {{ background: #fecaca; color: #7f1d1d; padding: 1px 2px; }}
code {{ background: #e2e8f0; padding: 2px 6px; border-radius: 5px; }}
.autofix {{ background: #ecfdf5; border: 1px solid #86efac; padding: 9px 11px; border-radius: 7px; }}
.issue-id {{ font-size: .78rem; margin-top: 12px; }}
.no-issues {{ border-left: 6px solid #16a34a; margin: 14px 0; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ text-align: left; padding: 9px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
</style>
</head>
<body>
<header>
  <h1>{SYSTEM_NAME}</h1>
  <div class="subtitle">{SYSTEM_FULL_NAME} · 階段二文字規則檢核</div>
  <div class="checkpoint"><strong>人工斷點二：</strong>請確認問題內容、嚴重度、段落與紅色字元範圍是否正確。本報告不會修改原始 Word，也不提供法律結論。</div>
</header>
<main>
  <div class="summary">
    <div>文件<strong>{escape(review.file_name)}</strong></div>
    <div>專利名稱<strong>{escape(review.patent_title or '尚未辨識')}</strong></div>
    <div>總問題<strong>{len(review.issues)}</strong></div>
    <div>錯誤<strong>{counts.get('error', 0)}</strong></div>
    <div>警告<strong>{counts.get('warning', 0)}</strong></div>
    <div>提示<strong>{counts.get('info', 0)}</strong></div>
    <div>已執行規則<strong>{len(review.rule_catalog)}</strong></div>
  </div>
  <div class="panel"><h2>第一階段解析警告</h2><ul>{warnings}</ul></div>
  <section><h2>問題清單</h2>{issue_cards}</section>
  <div class="panel"><h2>本階段規則目錄</h2>
    <table><thead><tr><th>規則</th><th>名稱</th><th>預設等級</th><th>說明</th></tr></thead><tbody>{rule_rows}</tbody></table>
  </div>
  <div class="panel"><h2>文件識別</h2><p>SHA-256：<code>{escape(review.sha256)}</code></p><p>來源：{escape(review.source_path)}</p><p>報告時間（UTC）：{escape(review.generated_at_utc)}</p></div>
</main>
</body>
</html>"""


def build_reports(source: Path, output_dir: Path) -> Tuple[Path, Path]:
    document = parse_docx(source)
    review = review_document(document)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(source.stem)
    json_path = output_dir / f"{stem}_stage2_review.json"
    html_path = output_dir / f"{stem}_stage2_review.html"
    json_path.write_text(
        json.dumps(review.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path.write_text(render_html(document, review), encoding="utf-8")
    return json_path, html_path


def main() -> int:
    parser = ArgumentParser(description=f"{SYSTEM_NAME} Stage 2 text checker")
    parser.add_argument("docx", type=Path, help="Patent .docx file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "output" / "patent_review_stage2",
    )
    args = parser.parse_args()
    try:
        json_path, html_path = build_reports(args.docx, args.output_dir)
    except PatentDocxError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    summary = payload["summary"]
    print(f"[OK] {SYSTEM_NAME} Stage 2 review completed")
    print(
        "Issues: "
        f"{summary['total']} "
        f"(error={summary['by_severity'].get('error', 0)}, "
        f"warning={summary['by_severity'].get('warning', 0)}, "
        f"info={summary['by_severity'].get('info', 0)})"
    )
    print(f"JSON: {json_path.resolve()}")
    print(f"HTML: {html_path.resolve()}")
    print("Open the HTML report and complete manual checkpoint 2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
