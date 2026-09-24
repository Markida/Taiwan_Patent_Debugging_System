"""Build an intentionally faulty Stage 2 report for manual checkpoint review."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_review.models import PatentDocument, PatentParagraph, TextRunSpan
from features.patent_review.rule_engine import review_document
from features.patent_review.section_parser import assign_sections
from features.patent_review.text_normalizer import normalize_patent_text
from tools.check_patent_text import render_html


DEMO_LINES = [
    "【中文新型名稱】測試Ａ裝置",
    "【技術領域】",
    "本新型涉及7′測試技術。",
    "【先前技術】",
    "習知技術內容。",
    "【新型內容】",
    "本新型提供一種測試裝置。",
    "【符號說明】",
    "10：主箱體",
    "10:外殼",
    "這行不是有效的符號格式",
    "【代表圖之符號簡單說明】",
    "10:殼體",
    "99:未知元件",
    "【申請專利範圍】",
    "【請求項1】一種測試裝置。",
    "【請求項3】如請求項4所述的測試裝置。",
]


def build_demo_document() -> PatentDocument:
    paragraphs = []
    for index, source_text in enumerate(DEMO_LINES):
        # Split one paragraph into two runs so the report also proves that its
        # character location can be mapped back to the corresponding Word run.
        split_at = max(1, len(source_text) // 2)
        run_spans = []
        if source_text:
            for run_index, (start, end) in enumerate(
                ((0, split_at), (split_at, len(source_text)))
            ):
                if start < end:
                    run_spans.append(
                        TextRunSpan(
                            run_index=run_index,
                            start=start,
                            end=end,
                            text=source_text[start:end],
                        )
                    )
        paragraphs.append(
            PatentParagraph(
                index=index,
                text=source_text,
                normalized_text=normalize_patent_text(source_text),
                source_path=f"demo/body/p[{index}]",
                run_spans=run_spans,
            )
        )

    sections, patent_type, patent_title = assign_sections(paragraphs)
    return PatentDocument(
        source_path="Stage2_規則故障示範（非真實案件）.docx",
        file_name="Stage2_規則故障示範（非真實案件）.docx",
        file_size_bytes=0,
        sha256="demo-" + datetime.now(timezone.utc).strftime("%Y%m%d"),
        patent_type=patent_type,
        patent_title=patent_title,
        paragraphs=paragraphs,
        sections=sections,
        warnings=["這是人工斷點二的刻意含錯示範，不是來源 Word 的檢核結果。"],
    )


def main() -> int:
    output_dir = PROJECT_ROOT / "output" / "patent_review_stage2" / "demo"
    output_dir.mkdir(parents=True, exist_ok=True)
    document = build_demo_document()
    review = review_document(document)
    stem = "Stage2_規則故障示範"
    json_path = output_dir / f"{stem}_stage2_review.json"
    html_path = output_dir / f"{stem}_stage2_review.html"
    json_path.write_text(
        json.dumps(review.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path.write_text(render_html(document, review), encoding="utf-8")
    summary = review.to_dict()["summary"]
    print(
        f"[OK] Demo issues: {summary['total']} "
        f"(error={summary['by_severity']['error']}, "
        f"warning={summary['by_severity']['warning']}, "
        f"info={summary['by_severity']['info']})"
    )
    print(f"JSON: {json_path.resolve()}")
    print(f"HTML: {html_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
