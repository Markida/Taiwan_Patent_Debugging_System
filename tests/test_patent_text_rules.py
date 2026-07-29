import unittest

from features.patent_review.models import PatentDocument, PatentParagraph, TextRunSpan
from features.patent_review.rule_engine import RULE_CATALOG, review_document
from features.patent_review.section_parser import assign_sections
from features.patent_review.text_normalizer import normalize_patent_text
from tools.check_patent_text import render_html


NARRATIVE_SECTIONS = {
    "technical_field",
    "background_art",
    "disclosure",
    "brief_description_of_drawings",
    "embodiments",
}


def build_document(lines, *, add_valid_numbering=True):
    paragraphs = []
    for index, text in enumerate(lines):
        paragraphs.append(
            PatentParagraph(
                index=index,
                text=text,
                normalized_text=normalize_patent_text(text),
                source_path=f"body/p[{index}]",
                run_spans=[
                    TextRunSpan(run_index=0, start=0, end=len(text), text=text)
                ]
                if text
                else [],
            )
        )
    sections, patent_type, patent_title = assign_sections(paragraphs)
    if add_valid_numbering:
        number = 0
        for paragraph in paragraphs:
            if (
                not paragraph.is_heading
                and paragraph.section_key in NARRATIVE_SECTIONS
                and paragraph.content_text.strip()
            ):
                number += 1
                paragraph.numbering_id = 10
                paragraph.numbering_level = 0
                paragraph.numbering_value = number
                paragraph.numbering_text = f"〖{number:04d}〗"
                paragraph.numbering_format = "decimalZero"
    return PatentDocument(
        source_path="C:/synthetic/review.docx",
        file_name="review.docx",
        file_size_bytes=0,
        sha256="a" * 64,
        patent_type=patent_type,
        patent_title=patent_title,
        paragraphs=paragraphs,
        sections=sections,
    )


VALID_LINES = [
    "【中文新型名稱】測試裝置",
    "【中文摘要】摘要內容",
    "【技術領域】",
    "本新型涉及測試技術。",
    "【先前技術】",
    "習知技術內容。",
    "【新型內容】",
    "本新型提供一種測試裝置。",
    "【實施方式】",
    "測試裝置包含主箱體10及分隔板20。",
    "【符號說明】",
    "10:主箱體",
    "20:分隔板",
    "【代表圖之符號簡單說明】",
    "10:主箱體",
    "【申請專利範圍】",
    "【請求項1】一種測試裝置，包含一主箱體。",
    "【請求項2】如請求項1所述的測試裝置，其中該主箱體具有一分隔板。",
]


class PatentTextRuleTests(unittest.TestCase):
    def test_valid_document_has_no_stage2_issues(self):
        document = build_document(VALID_LINES)
        review = review_document(document)

        self.assertEqual(len(RULE_CATALOG), 22)
        self.assertEqual(review.issues, [])
        self.assertEqual(review.to_dict()["summary"]["total"], 0)

    def test_detects_structure_symbol_claim_and_typography_problems(self):
        document = build_document(
            [
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
                "格式錯誤",
                "【代表圖之符號簡單說明】",
                "10:殼體",
                "99:未知元件",
                "【申請專利範圍】",
                "【請求項1】一種測試裝置。",
                "【請求項3】如請求項4所述的測試裝置。",
            ]
        )
        review = review_document(document)
        rule_ids = [issue.rule_id for issue in review.issues]

        for expected in (
            "STR001",
            "SYM001",
            "SYM003",
            "SYM004",
            "SYM005",
            "CLM002",
            "CLM003",
            "TXT001",
            "TXT002",
        ):
            self.assertIn(expected, rule_ids)

        self.assertNotIn("SYM002", rule_ids)
        self.assertFalse(
            any(issue.matched_text == "：" for issue in review.issues),
            "全形冒號應視為合法符號分隔符號",
        )

        claim = next(issue for issue in review.issues if issue.rule_id == "CLM003")
        self.assertEqual(claim.details["invalid_dependencies"], [4])
        self.assertIn("請求項4", claim.matched_text)

        fullwidth = next(issue for issue in review.issues if issue.rule_id == "TXT001")
        self.assertEqual(fullwidth.matched_text, "Ａ")
        self.assertEqual(fullwidth.replacement, "A")

    def test_html_report_marks_source_range_and_states_checkpoint(self):
        document = build_document(["【中文新型名稱】測試Ａ裝置"] + VALID_LINES[1:])
        review = review_document(document)
        html = render_html(document, review)

        self.assertIn("人工斷點二", html)
        self.assertIn("<mark>Ａ</mark>", html)
        self.assertIn("本階段不會寫回 Word", html)
        self.assertIn("TXT001", html)

    def test_detects_paragraph_section_and_figure_sequence_problems(self):
        document = build_document(
            [
                "【中文新型名稱】測試裝置",
                "【中文摘要】摘要內容",
                "【新型內容】",
                "第一段內容。",
                "【技術領域】",
                "第二段內容。",
                "【先前技術】",
                "第三段內容。",
                "【圖式簡單說明】",
                "圖1為立體圖。\n圖3為剖面圖。",
                "【指定代表圖】圖2",
                "【實施方式】",
                "第四段內容。",
                "【符號說明】",
                "1:底座",
                "【申請專利範圍】",
                "【請求項1】一種測試裝置。",
            ],
            add_valid_numbering=False,
        )
        narrative = [
            paragraph
            for paragraph in document.paragraphs
            if not paragraph.is_heading
            and paragraph.section_key in NARRATIVE_SECTIONS
            and paragraph.content_text.strip()
        ]
        for position, paragraph in enumerate(narrative, start=1):
            paragraph.numbering_id = 10
            paragraph.numbering_level = 0
            paragraph.numbering_value = position
            paragraph.numbering_text = f"〖{position:04d}〗"
            paragraph.numbering_format = "decimalZero"
        narrative[1].numbering_value = 3
        narrative[1].numbering_text = "〖003〗"
        narrative[2].numbering_id = None
        narrative[2].numbering_value = None
        narrative[2].numbering_text = ""

        rule_ids = {issue.rule_id for issue in review_document(document).issues}

        self.assertTrue({"PNO001", "PNO002", "PNO003"}.issubset(rule_ids))
        self.assertIn("ORD001", rule_ids)
        self.assertIn("FIG001", rule_ids)
        self.assertIn("FIG002", rule_ids)

    def test_detects_body_symbol_mismatch_and_missing_symbol(self):
        lines = list(VALID_LINES)
        lines[9] = "測試裝置包含主箱體10、分隔板10及未知元件99。"
        review = review_document(build_document(lines))
        rule_ids = {issue.rule_id for issue in review.issues}

        self.assertIn("REF001", rule_ids)
        self.assertIn("REF002", rule_ids)

    def test_detects_multiple_dependent_claim_defects(self):
        lines = list(VALID_LINES[:-2]) + [
            "【請求項1】一種測試裝置。",
            "【請求項2】如請求項1所述的測試裝置。",
            "【請求項3】如請求項1及請求項2所述的測試裝置。",
            "【請求項4】如請求項3所述的測試裝置。",
            "【請求項5】如請求項1或請求項4所述的測試裝置。",
        ]
        review = review_document(build_document(lines))
        rule_ids = {issue.rule_id for issue in review.issues}

        self.assertIn("CLM004", rule_ids)
        self.assertIn("CLM005", rule_ids)


if __name__ == "__main__":
    unittest.main()
