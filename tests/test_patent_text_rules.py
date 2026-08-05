import re
import unittest

from features.patent_review.models import PatentDocument, PatentParagraph, TextRunSpan
from features.patent_review.custom_rules import (
    CUSTOM_RULE_WHITELIST,
    CustomTextRule,
)
from features.patent_review.figure_ocr_checker import (
    parse_figure_number_mapping,
    parse_leading_figure_reference,
)
from features.patent_review.rule_engine import RULE_CATALOG, review_document
from features.patent_review.section_parser import assign_sections
from features.patent_review.symbol_transfer import extract_document_symbols
from features.patent_review.text_normalizer import normalize_patent_text
from tools.check_patent_text import render_html


NARRATIVE_SECTIONS = {
    "technical_field",
    "background_art",
    "disclosure",
    "brief_description_of_drawings",
    "embodiments",
    "drawing_symbol_description",
}


def build_document(lines, *, add_valid_numbering=True, table_cells=None):
    paragraphs = []
    table_cells = table_cells or {}
    for index, text in enumerate(lines):
        table_location = table_cells.get(index)
        paragraphs.append(
            PatentParagraph(
                index=index,
                text=text,
                normalized_text=normalize_patent_text(text),
                source_path=f"body/p[{index}]",
                source_kind="table" if table_location is not None else "body",
                table_index=table_location[0] if table_location is not None else None,
                row_index=table_location[1] if table_location is not None else None,
                cell_index=table_location[2] if table_location is not None else None,
                run_spans=[
                    TextRunSpan(run_index=0, start=0, end=len(text), text=text)
                ]
                if text
                else [],
            )
        )
    sections, patent_type, patent_title = assign_sections(paragraphs)
    formal = any(
        paragraph.section_key in {"major_abstract", "major_description"}
        for paragraph in paragraphs
    )
    if formal:
        for paragraph in paragraphs:
            if paragraph.section_key in {"major_description", "claims"} and paragraph.is_heading:
                if paragraph.index:
                    paragraphs[paragraph.index - 1].section_break_type = "nextPage"
            if paragraph.major_section_key == "claims" and not paragraph.is_heading:
                match = re.match(
                    r"^【請求項(?P<number>\d+)】", paragraph.text
                )
                if match:
                    paragraph.numbering_id = 20
                    paragraph.numbering_level = 0
                    paragraph.numbering_value = int(match.group("number"))
                    paragraph.numbering_text = match.group(0)
                    paragraph.text = paragraph.text[match.end():]
                    paragraph.normalized_text = normalize_patent_text(paragraph.text)
                    paragraph.content_text = paragraph.normalized_text
    if add_valid_numbering:
        number = 0
        for paragraph in paragraphs:
            if (
                paragraph.source_kind != "table"
                and
                not paragraph.is_heading
                and paragraph.section_key in NARRATIVE_SECTIONS
                and paragraph.content_text.strip()
                and not (
                    paragraph.section_key == "brief_description_of_drawings"
                    and re.match(r"^圖\s*\d+", paragraph.text)
                )
            ):
                if (
                    paragraph.section_key == "drawing_symbol_description"
                    and re.match(r"^[0-9A-Za-z]+(?:['′])?\s*[:：]", paragraph.text)
                ):
                    continue
                number += 1
                paragraph.numbering_id = 10
                paragraph.numbering_level = 0
                paragraph.numbering_value = number
                paragraph.numbering_text = f"【{number:04d}】" if formal else f"〖{number:04d}〗"
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
    "【新型摘要】",
    "【中文新型名稱】測試裝置",
    "【中文】",
    "摘要內容。",
    "【指定代表圖】圖1",
    "【代表圖之符號簡單說明】",
    "10:主箱體",
    "【新型說明書】",
    "【中文新型名稱】測試裝置",
    "【技術領域】",
    "本新型涉及測試技術。",
    "【先前技術】",
    "習知技術內容。",
    "【新型內容】",
    "本新型提供一種測試裝置。",
    "【圖式簡單說明】",
    "本新型之其他的特徵及功效，將於參照圖式的實施方式中清楚地呈現：",
    "圖1為測試裝置的立體圖。",
    "【實施方式】",
    "測試裝置包含主箱體10及分隔板20。",
    "【符號說明】",
    "符號說明如下。",
    "10:主箱體",
    "20:分隔板",
    "【新型申請專利範圍】",
    "【請求項1】一種測試裝置，包含一主箱體。",
    "【請求項2】如請求項1所述的測試裝置，其中該主箱體具有一分隔板。",
]


class PatentTextRuleTests(unittest.TestCase):
    def test_valid_document_has_no_stage2_issues(self):
        document = build_document(VALID_LINES)
        review = review_document(document)

        self.assertGreaterEqual(len(RULE_CATALOG), 48)
        self.assertNotIn("FMT003", {rule.rule_id for rule in RULE_CATALOG})
        self.assertNotIn("SYM002", {rule.rule_id for rule in RULE_CATALOG})
        self.assertEqual(review.issues, [])
        self.assertEqual(review.to_dict()["summary"]["total"], 0)

    def test_required_section_ending_punctuation_is_reported(self):
        lines = list(VALID_LINES)
        lines[lines.index("摘要內容。")] = "摘要內容"
        lines[lines.index("本新型涉及測試技術。")] = "本新型涉及測試技術，"
        introduction = (
            "本新型之其他的特徵及功效，將於參照圖式的實施方式中清楚地呈現："
        )
        lines[lines.index(introduction)] = introduction[:-1] + "。"
        lines[lines.index("圖1為測試裝置的立體圖。")] = "圖1為測試裝置的立體圖；"
        lines[lines.index("【請求項2】如請求項1所述的測試裝置，其中該主箱體具有一分隔板。")] = (
            "【請求項2】如請求項1所述的測試裝置，其中該主箱體具有一分隔板"
        )

        rule_ids = {
            issue.rule_id for issue in review_document(build_document(lines)).issues
        }

        self.assertTrue(
            {"PCT001", "PCT002", "PCT003", "PCT004", "CLM009"}
            <= rule_ids
        )

    def test_symbol_description_has_no_terminal_punctuation_rule(self):
        lines = list(VALID_LINES)
        lines[lines.index("符號說明如下。")] = "符號說明如下"

        punctuation_issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id.startswith("PCT")
            and issue.section_key == "drawing_symbol_description"
        ]

        self.assertEqual(punctuation_issues, [])

    def test_multiple_drawing_captions_use_ordered_endings(self):
        lines = list(VALID_LINES)
        figure_index = lines.index("圖1為測試裝置的立體圖。")
        lines[figure_index:figure_index + 1] = [
            "圖1為測試裝置的立體圖；",
            "圖2為測試裝置的前視圖；",
            "圖3為測試裝置的側視圖；及",
            "圖4為測試裝置的剖視圖。",
        ]

        valid_issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"PCT003", "PCT004"}
        ]
        self.assertEqual(valid_issues, [])

        invalid_lines = list(lines)
        invalid_lines[invalid_lines.index("圖1為測試裝置的立體圖；")] = (
            "圖1為測試裝置的立體圖。"
        )
        invalid_lines[invalid_lines.index("圖3為測試裝置的側視圖；及")] = (
            "圖3為測試裝置的側視圖；"
        )
        issues = [
            issue
            for issue in review_document(build_document(invalid_lines)).issues
            if issue.rule_id == "PCT004"
        ]

        self.assertEqual(len(issues), 2)
        self.assertEqual(
            [issue.details["expected_ending"] for issue in issues],
            ["；", "；及"],
        )

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
                "30:",
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

    def test_repeated_adjacent_chinese_characters_are_errors(self):
        lines = list(VALID_LINES)
        paragraph_index = lines.index("本新型涉及測試技術。")
        lines[paragraph_index] = "本新型涉及測試技術的的應用。"

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "TXT004"
        ]

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")
        self.assertEqual(issues[0].matched_text, "的的")
        self.assertEqual(issues[0].details["repeated_character"], "的")

    def test_repeated_non_chinese_or_separated_chinese_is_not_reported(self):
        lines = list(VALID_LINES)
        lines[lines.index("摘要內容。")] = "摘要內容包含AA、11、可 可及個，個。"

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "TXT004"
        ]

        self.assertEqual(issues, [])

    def test_table_cells_keep_text_checks_but_do_not_change_structure(self):
        lines = list(VALID_LINES)
        insertion = lines.index("本新型涉及測試技術。") + 1
        lines[insertion:insertion] = ["【先前技術】", "表格的的內容"]
        table_cells = {
            insertion: (0, 0, 0),
            insertion + 1: (0, 0, 1),
        }

        review = review_document(
            build_document(lines, table_cells=table_cells)
        )
        repeated = next(
            issue for issue in review.issues if issue.rule_id == "TXT004"
        )

        self.assertEqual(repeated.matched_text, "的的")
        self.assertEqual(repeated.details["source_kind"], "table")
        self.assertEqual(repeated.details["table_index"], 0)
        self.assertEqual(repeated.details["row_index"], 0)
        self.assertEqual(repeated.details["cell_index"], 1)
        self.assertFalse(
            any(issue.rule_id in {"STR003", "STR006", "PNO001"} for issue in review.issues)
        )

    def test_claim_table_cell_is_not_merged_into_claim_grammar(self):
        lines = list(VALID_LINES)
        insertion = len(lines) - 1
        lines.insert(
            insertion,
            "請求項99如請求項100所述的表格內容。",
        )

        review = review_document(
            build_document(lines, table_cells={insertion: (1, 0, 0)})
        )

        self.assertFalse(
            any(
                issue.rule_id in {"CLM002", "CLM003"}
                and ("99" in issue.message or "100" in issue.message)
                for issue in review.issues
            )
        )
        table_warning = next(
            issue for issue in review.issues if issue.rule_id == "TBL001"
        )
        self.assertEqual(table_warning.severity, "warning")
        self.assertEqual(table_warning.details["review_mode"], "cell_text_only")

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
                "【中文摘要】摘要內容。",
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
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "測試裝置包含主箱體10、分隔板10及未知元件99。"
        )
        review = review_document(build_document(lines))
        rule_ids = {issue.rule_id for issue in review.issues}
        wrong_label_issues = [
            issue for issue in review.issues if issue.rule_id == "REF002"
        ]

        self.assertIn("REF001", rule_ids)
        self.assertIn("REF002", rule_ids)
        self.assertTrue(wrong_label_issues)
        self.assertTrue(
            all(issue.severity == "error" for issue in wrong_label_issues)
        )

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

    def test_strict_mode_rejects_manual_description_numbering(self):
        document = build_document(VALID_LINES)
        paragraph = next(
            item
            for item in document.paragraphs
            if item.section_key == "technical_field" and not item.is_heading
        )
        paragraph.numbering_id = None
        paragraph.numbering_level = None
        paragraph.numbering_value = None
        paragraph.numbering_text = ""
        paragraph.text = "【0001】" + paragraph.text
        paragraph.normalized_text = normalize_patent_text(paragraph.text)
        paragraph.content_text = paragraph.normalized_text

        rule_ids = {issue.rule_id for issue in review_document(document).issues}

        self.assertIn("PNO004", rule_ids)

    def test_tipo_claim_wording_sentence_and_symbol_rules_are_integrated(self):
        lines = list(VALID_LINES)
        lines[lines.index("本新型提供一種測試裝置。")] = (
            "本發明提供一種測試裝置。"
        )
        lines[lines.index("【請求項2】如請求項1所述的測試裝置，其中該主箱體具有一分隔板。")] = (
            "【請求項2】如權利要求1所述的測試裝置，其中該主箱體10具有一分隔板。另一句。"
        )

        rule_ids = {issue.rule_id for issue in review_document(build_document(lines)).issues}

        self.assertIn("REF003", rule_ids)
        self.assertIn("CLM008", rule_ids)
        self.assertIn("CLM009", rule_ids)
        self.assertNotIn("CLM010", rule_ids)

    def test_missing_major_section_is_reported_as_strict_structure_error(self):
        lines = [line for line in VALID_LINES if line != "【新型說明書】"]

        review = review_document(build_document(lines))
        rule_ids = {issue.rule_id for issue in review.issues}

        self.assertIn("STR001", rule_ids)
        self.assertIn("STR005", rule_ids)

    def test_blank_word_paragraphs_are_not_reported(self):
        lines = list(VALID_LINES)
        lines.insert(lines.index("【先前技術】"), "")

        review = review_document(build_document(lines))

        self.assertNotIn("STR008", {issue.rule_id for issue in review.issues})

    def test_longest_component_name_wins_for_overlapping_symbol_names(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = ["1:齒輪", "11:第一齒輪"]
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "測試裝置包含第一齒輪11及第二齒輪1。"
        )

        review = review_document(build_document(lines))
        reference_issues = [
            issue
            for issue in review.issues
            if issue.rule_id in {"REF001", "REF002"}
        ]

        self.assertEqual(reference_issues, [])

    def test_heading_typography_and_bracket_style_are_not_checked(self):
        document = build_document(VALID_LINES)
        major = next(
            paragraph
            for paragraph in document.paragraphs
            if paragraph.section_key == "major_abstract"
        )
        medium = next(
            paragraph
            for paragraph in document.paragraphs
            if paragraph.section_key == "technical_field" and paragraph.is_heading
        )
        title = next(
            paragraph
            for paragraph in document.paragraphs
            if paragraph.section_key == "utility_model_title"
            and paragraph.major_section_key == "major_abstract"
        )
        major.text = "〖 新型摘要 〗"
        medium.text = "〖 技術領域 〗"
        title.text = "〖 中文新型名稱 〗測試裝置"
        for paragraph in (major, medium, title):
            paragraph.font_size_half_points = 96
            paragraph.paragraph_alignment = "right"
            paragraph.line_spacing = 123
            paragraph.line_spacing_rule = "exact"

        rule_ids = {issue.rule_id for issue in review_document(document).issues}

        self.assertNotIn("FMT001", rule_ids)
        self.assertNotIn("FMT002", rule_ids)
        self.assertNotIn("STR010", rule_ids)

    def test_body_font_size_and_alignment_are_not_checked(self):
        document = build_document(VALID_LINES)
        body = next(
            paragraph
            for paragraph in document.paragraphs
            if paragraph.section_key == "technical_field"
            and not paragraph.is_heading
        )
        body.font_size_half_points = 48
        body.complex_font_size_half_points = 32
        body.paragraph_alignment = "left"

        rule_ids = {issue.rule_id for issue in review_document(document).issues}

        self.assertNotIn("FMT003", rule_ids)

    def test_heading_layout_is_ignored_but_text_and_order_are_recognized(self):
        replacements = {
            "【新型摘要】": "新型摘要",
            "【中文新型名稱】測試裝置": "中文新型名稱：測試裝置",
            "【中文】": "一、中文",
            "【指定代表圖】圖1": "指定代表圖：圖1",
            "【代表圖之符號簡單說明】": "〖 代表圖之符號簡單說明 〗",
            "【新型說明書】": "新型說明書",
            "【技術領域】": "一、技術領域",
            "【先前技術】": "先前技術：",
            "【新型內容】": "〖 新型內容 〗",
            "【圖式簡單說明】": "圖式簡單說明",
            "【實施方式】": "實施方式：",
            "【符號說明】": "〖 符號說明 〗",
            "【新型申請專利範圍】": "新型申請專利範圍",
        }
        lines = [replacements.get(line, line) for line in VALID_LINES]

        review = review_document(build_document(lines))
        rule_ids = {issue.rule_id for issue in review.issues}

        self.assertFalse({"STR001", "STR005", "STR006", "STR010"} & rule_ids)

    def test_first_detected_patent_type_keeps_rule_sets_mutually_exclusive(self):
        invention_replacements = {
            "新型": "發明",
        }
        invention_lines = [
            line.replace(source, target)
            for line in VALID_LINES
            for source, target in invention_replacements.items()
        ]
        second_title = invention_lines.index("【中文發明名稱】測試裝置", 2)
        invention_lines[second_title] = "【中文新型名稱】測試裝置"
        invention = build_document(invention_lines)

        utility_lines = list(VALID_LINES)
        second_title = utility_lines.index("【中文新型名稱】測試裝置", 2)
        utility_lines[second_title] = "【中文發明名稱】測試裝置"
        utility = build_document(utility_lines)

        self.assertEqual(invention.patent_type, "invention")
        self.assertEqual(utility.patent_type, "utility_model")
        for document in (invention, utility):
            self.assertNotIn(
                "STR004",
                {issue.rule_id for issue in review_document(document).issues},
            )

    def test_heading_text_still_must_match_the_locked_patent_type(self):
        lines = list(VALID_LINES)
        lines[lines.index("【新型說明書】")] = "發明說明書"
        document = build_document(lines)
        review = review_document(document)

        self.assertEqual(document.patent_type, "utility_model")
        mismatch = next(issue for issue in review.issues if issue.rule_id == "STR010")
        self.assertIn("新型說明書", mismatch.message)

    def test_symbol_number_layout_is_ignored_while_content_is_extracted(self):
        document = build_document(VALID_LINES)
        replacements = {
            ("representative_drawing_symbols", "10:主箱體"): "（10） 主箱體",
            ("drawing_symbol_description", "10:主箱體"): "10．主箱體",
            ("drawing_symbol_description", "20:分隔板"): "20 分隔板",
        }
        for paragraph in document.paragraphs:
            replacement = replacements.get((paragraph.section_key, paragraph.text))
            if replacement is not None:
                paragraph.text = replacement
                paragraph.normalized_text = normalize_patent_text(replacement)
                paragraph.content_text = paragraph.normalized_text
            if (
                paragraph.section_key == "drawing_symbol_description"
                and paragraph.text == "符號說明如下。"
            ):
                paragraph.numbering_text = "〖15〗"
                paragraph.numbering_value = 15

        review = review_document(document)
        transfer = extract_document_symbols(document, review)
        symbol_rule_ids = {
            issue.rule_id
            for issue in review.issues
            if issue.section_key in {
                "drawing_symbol_description",
                "representative_drawing_symbols",
            }
        }

        self.assertFalse({"PNO001", "PNO002", "PNO003", "PNO004", "PNO005", "PNO006", "SYM001", "SYM002"} & symbol_rule_ids)
        self.assertEqual([entry.label for entry in transfer.full_entries], ["10", "20"])
        self.assertEqual([entry.label for entry in transfer.representative_entries], ["10"])

    def test_independent_claim_is_parsed_across_line_and_paragraph_breaks(self):
        inline_lines = list(VALID_LINES)
        inline_lines[inline_lines.index("【請求項1】一種測試裝置，包含一主箱體。")] = (
            "【請求項1】一種測試裝置，包含：\n一主箱體。"
        )
        paragraph_lines = list(VALID_LINES)
        claim_index = paragraph_lines.index("【請求項1】一種測試裝置，包含一主箱體。")
        paragraph_lines[claim_index:claim_index + 1] = [
            "【請求項1】一種測試裝置，包含：",
            "一主箱體。",
        ]

        for lines in (inline_lines, paragraph_lines):
            review = review_document(build_document(lines))
            first_claim_parse_errors = [
                issue
                for issue in review.issues
                if issue.rule_id in {"CLM001", "CLM014", "CLM015"}
                and issue.paragraph_index is not None
            ]
            self.assertEqual(first_claim_parse_errors, [])

    def test_independent_claim_line_endings_follow_constituent_order(self):
        lines = list(VALID_LINES)
        claim_index = lines.index("【請求項1】一種測試裝置，包含一主箱體。")
        lines[claim_index:claim_index + 1] = [
            "【請求項1】一種測試裝置，包含：",
            "一主箱體；",
            "一分隔板；及",
            "一控制模組。",
        ]

        valid_issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "CLM017"
        ]
        self.assertEqual(valid_issues, [])

        invalid_lines = list(lines)
        invalid_lines[claim_index:claim_index + 4] = [
            "【請求項1】一種測試裝置，包含，",
            "一主箱體，",
            "一分隔板；",
            "一控制模組。",
        ]
        issues = [
            issue
            for issue in review_document(build_document(invalid_lines)).issues
            if issue.rule_id == "CLM017"
        ]

        self.assertEqual(len(issues), 3)
        self.assertEqual(
            [issue.details["expected_ending"] for issue in issues],
            ["：", "；", "；及"],
        )
        self.assertEqual(
            [issue.paragraph_index for issue in issues],
            [claim_index, claim_index + 1, claim_index + 2],
        )

    def test_dependent_claim_line_breaks_do_not_use_independent_item_rule(self):
        lines = list(VALID_LINES)
        claim = "【請求項2】如請求項1所述的測試裝置，其中該主箱體具有一分隔板。"
        lines[lines.index(claim)] = (
            "【請求項2】如請求項1所述的測試裝置，\n"
            "其中該主箱體具有一分隔板。"
        )

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "CLM017"
        ]

        self.assertEqual(issues, [])

    def test_numbered_claim_absorbs_unnumbered_word_paragraphs_until_claim_two(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "30:通訊模組",
            "40:用戶裝置",
            "50:繳費裝置",
            "60:收款裝置",
            "70:帳單整合模組",
        ]
        lines[-2:] = [
            "【請求項1】一種電子帳單整合系統，包含：",
            (
                "一通訊模組，與一用戶裝置、一繳費裝置和一收款裝置通訊相接，"
                "其中該通訊模組自該用戶裝置接收複數個繳費帳單；及"
            ),
            (
                "一帳單整合模組，與該通訊模組資訊相接，以自該通訊模組接收"
                "並整合該些繳費帳單。"
            ),
            (
                "【請求項2】如請求項1所述之電子帳單整合系統，"
                "其中該帳單整合模組連接該通訊模組。"
            ),
        ]

        issues = review_document(build_document(lines)).issues
        claim_failures = [
            issue
            for issue in issues
            if issue.rule_id in {"CLM007", "CLM012", "CLM013", "CLM015"}
        ]

        self.assertEqual(claim_failures, [])

    def test_claim_boundaries_use_first_item_markers_not_body_numbers(self):
        lines = list(VALID_LINES[:-2]) + [
            (
                "【請求項1】一種測試裝置，包含一主箱體，數值為90且具有2個位置。"
                "請求項2 如請求項1所述的測試裝置，其中該主箱體具有一分隔板。"
            ),
            (
                "【請求項3】如請求項1或請求項2所述的測試裝置，"
                "其中該主箱體連接該分隔板。"
            ),
        ]

        rule_ids = {issue.rule_id for issue in review_document(build_document(lines)).issues}

        self.assertFalse({"CLM001", "CLM002", "CLM003", "CLM007", "CLM009", "CLM015"} & rule_ids)

    def test_plain_word_claim_numbering_is_accepted_without_brackets(self):
        document = build_document(VALID_LINES)
        claims = [
            paragraph
            for paragraph in document.paragraphs
            if paragraph.major_section_key == "claims" and not paragraph.is_heading
        ]
        for number, paragraph in enumerate(claims, start=1):
            paragraph.numbering_text = f"請求項{number}"

        rule_ids = {issue.rule_id for issue in review_document(document).issues}

        self.assertFalse({"CLM001", "CLM002", "CLM006", "CLM007"} & rule_ids)

    def test_component_labels_are_required_only_in_embodiments(self):
        lines = list(VALID_LINES)
        lines[lines.index("摘要內容。")] = "摘要包含主箱體。"
        lines[lines.index("本新型提供一種測試裝置。")] = "本新型提供主箱體。"
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "測試裝置包含該主箱體及分隔板20。"
        )

        document = build_document(lines)
        review = review_document(document)
        missing_labels = [issue for issue in review.issues if issue.rule_id == "REF004"]

        self.assertEqual(len(missing_labels), 1)
        self.assertEqual(missing_labels[0].severity, "error")
        self.assertEqual(missing_labels[0].matched_text, "主箱體")
        paragraph = document.paragraphs[missing_labels[0].paragraph_index]
        self.assertEqual(paragraph.section_key, "embodiments")

    def test_unqualified_embodiment_component_does_not_require_label(self):
        lines = list(VALID_LINES)
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "測試裝置包含主箱體及分隔板20。"
        )

        issues = review_document(build_document(lines)).issues

        self.assertFalse(
            any(
                issue.rule_id == "REF004"
                and issue.details.get("component_name") == "主箱體"
                for issue in issues
            )
        )

    def test_component_label_after_parenthetical_abbreviation_is_accepted(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines.insert(symbol_insert, "512:發光二極體")
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "該發光二極體(LED)512連接主箱體10及分隔板20。"
        )

        issues = review_document(build_document(lines)).issues
        reference_issues = [
            issue
            for issue in issues
            if issue.rule_id in {"REF001", "REF002", "REF004"}
            and issue.details.get("component_name") == "發光二極體"
        ]

        self.assertEqual(reference_issues, [])

    def test_implementation_accepts_exact_letter_and_literal_symbol_labels(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = ["X:字母元件", "△:特殊元件"]
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "測試裝置包含主箱體10、分隔板20、字母元件X及特殊元件△。"
        )

        issues = review_document(build_document(lines)).issues
        reference_issues = [
            issue
            for issue in issues
            if issue.rule_id in {"REF001", "REF002", "REF004"}
            and issue.details.get("component_name") in {"字母元件", "特殊元件"}
        ]

        self.assertEqual(reference_issues, [])

    def test_claim_quantity_and_antecedent_semantics_accept_valid_forms(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "30:基座",
            "40:外殼",
            "50:支架",
            "60:扣件",
            "70:端子",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含一基座、一設置於該基座的外殼、"
                "複數設置於一頂底方向的支架、二固定於該基座的扣件及"
                "至少一連接於該基座的端子，該外殼連接該等支架及該等扣件，"
                "且該端子設置於該基座。"
            ),
            (
                "【請求項2】如請求項1所述的測試裝置，"
                "其中該外殼連接該等支架。"
            ),
        ]

        issues = review_document(build_document(lines)).issues

        self.assertEqual([issue for issue in issues if issue.rule_id == "CLM012"], [])

    def test_claim_quantity_rule_errors_on_singular_plural_mismatch(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = ["40:外殼", "50:支架"]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含一外殼及複數支架，"
                "該等外殼連接該支架。"
            ),
            "【請求項2】如請求項1所述的測試裝置，其中該外殼連接該等支架。",
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "CLM012"
        ]

        self.assertTrue(any("單數" in issue.message for issue in issues))
        self.assertTrue(any("複數" in issue.message for issue in issues))
        self.assertTrue(all(issue.severity == "error" for issue in issues))

    def test_first_component_without_quantity_is_an_error(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines.insert(symbol_insert, "40:外殼")
        lines[-2:] = [
            "【請求項1】一種測試裝置，包含外殼。",
            "【請求項2】如請求項1所述的測試裝置，其中外殼可拆卸。",
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "CLM012"
            and issue.details.get("component_name") == "外殼"
            and "第一次提到" in issue.message
        ]

        self.assertTrue(issues)
        self.assertTrue(all(issue.severity == "error" for issue in issues))

    def test_claim_quantity_accepts_linking_phrase_and_plural_member_forms(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "80:第一按鍵面板單元",
            "81:第二按鍵面板單元",
            "82:第一按鍵部",
            "83:螺絲",
            "84:控制器",
            "85:連接部",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，適用於供一第一按鍵面板單元及"
                "一第二按鍵面板單元擇一設置，該第一按鍵面板單元包含"
                "複數第一按鍵部及複數螺絲，其中一該螺絲連接該等第一按鍵部，"
                "每一該螺絲具有一連接部，其中任一該螺絲可被選取，"
                "各該螺絲分別定位，"
                "各自該螺絲並包括一控制器。"
            ),
            (
                "【請求項2】如請求項1所述的測試裝置，其中該等控制器"
                "分別連接該等連接部。"
            ),
        ]

        issues = review_document(build_document(lines)).issues

        self.assertEqual([issue for issue in issues if issue.rule_id == "CLM012"], [])

    def test_similar_component_name_typo_is_a_warning(self):
        lines = list(VALID_LINES)
        lines.insert(lines.index("【新型申請專利範圍】"), "11:第一齒輪")
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "測試裝置包含主箱體10、分隔板20及第一尺輪11，"
            "且第一齒倫11可轉動。"
        )

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "REF005"
        ]

        self.assertEqual({issue.matched_text for issue in issues}, {"第一尺輪", "第一齒倫"})
        self.assertTrue(all(issue.severity == "warning" for issue in issues))

    def test_similarity_whitelist_suppresses_only_the_named_typo_warning(self):
        lines = list(VALID_LINES)
        lines.insert(lines.index("【新型申請專利範圍】"), "11:第一齒輪")
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "測試裝置包含主箱體10、分隔板20及第一尺輪11，"
            "且第一齒倫11可轉動。"
        )
        whitelist = CustomTextRule.from_text(
            "第一尺輪",
            CUSTOM_RULE_WHITELIST,
        )

        review = review_document(
            build_document(lines),
            custom_rules=[whitelist],
        )
        typo_candidates = {
            issue.details.get("candidate")
            for issue in review.issues
            if issue.rule_id == "REF005"
        }

        self.assertNotIn("第一尺輪", typo_candidates)
        self.assertIn("第一齒倫", typo_candidates)

    def test_reordered_component_name_and_one_substitution_are_warnings(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "30:固定座",
            "31:固定定座",
        ]
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "測試裝置包含主箱體10、分隔板20、固座定30、座固位30及固固座座31。"
        )

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "REF005"
        ]

        exact_reorder = next(
            issue for issue in issues if issue.details.get("candidate") == "固座定"
        )
        reordered_substitution = next(
            issue for issue in issues if issue.details.get("candidate") == "座固位"
        )
        self.assertIn(
            "reordered_exact",
            exact_reorder.details.get("similarity_modes", []),
        )
        self.assertIn(
            "reordered_one_character",
            reordered_substitution.details.get("similarity_modes", []),
        )
        self.assertFalse(
            any(
                issue.details.get("candidate") == "固固座座"
                and "固定定座" in issue.details.get(
                    "expected_component_names", []
                )
                for issue in issues
            )
        )

    def test_similar_component_name_typo_warning_is_enabled_in_claims(self):
        lines = list(VALID_LINES)
        lines.insert(lines.index("【新型申請專利範圍】"), "11:第一齒輪")
        lines[-2:] = [
            "【請求項1】一種測試裝置，包含一第一尺輪。",
            "【請求項2】如請求項1所述的測試裝置，其中該第一齒倫可轉動。",
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "REF005"
            and issue.section_key == "claims"
        ]

        self.assertEqual(
            {issue.details.get("candidate") for issue in issues},
            {"第一尺輪", "第一齒倫"},
        )
        self.assertTrue(all(issue.severity == "warning" for issue in issues))

    def test_legitimate_nested_and_primary_secondary_names_are_not_typos(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "10A:導引部",
            "11:第一主導引面",
            "12:第一副導引面",
        ]
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "第一主導引 面11與第一副導引面12分別鄰接導引部10A。"
        )

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "REF005"
        ]

        self.assertFalse(
            any(
                issue.details.get("candidate") in {
                    "導引面",
                    "第一主導引面",
                    "第一副導引面",
                }
                for issue in issues
            )
        )

    def test_component_inside_claim_subject_is_not_a_repeated_component(self):
        title = "具可拆式電池的輕量化滑鼠"
        lines = [
            line.replace("測試裝置", title)
            if line.startswith("【中文新型名稱】")
            else line
            for line in VALID_LINES
        ]
        symbol_insert = lines.index("20:分隔板") + 1
        lines.insert(symbol_insert, "30:電池")
        lines[-2:] = [f"【請求項1】一種{title}。"] + [
            f"【請求項{number}】如請求項1所述的{title}，其中外觀可調整。"
            for number in range(2, 11)
        ]

        issues = review_document(build_document(lines)).issues

        self.assertFalse(
            any(
                issue.rule_id == "CLM012"
                and issue.details.get("component_name") == "電池"
                for issue in issues
            )
        )

    def test_complete_patent_title_in_embodiments_does_not_need_component_labels(self):
        title = "具可拆式電池的輕量化滑鼠"
        lines = [
            line.replace("測試裝置", title)
            if line.startswith("【中文新型名稱】")
            else line
            for line in VALID_LINES
        ]
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = ["30:電池", "31:滑鼠"]
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            f"參閱圖14，為本新型{title}的立體圖。"
        )

        issues = review_document(build_document(lines)).issues

        self.assertFalse(
            any(
                issue.rule_id == "REF004"
                and issue.details.get("component_name") in {"電池", "滑鼠"}
                for issue in issues
            )
        )

    def test_embodiment_instance_subject_does_not_need_component_labels(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines.insert(symbol_insert, "30:前叉")
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "本發明前叉裝置的一第一實施例包含主箱體10及分隔板20。"
        )

        issues = review_document(build_document(lines)).issues

        self.assertFalse(
            any(
                issue.rule_id == "REF004"
                and issue.details.get("component_name") == "前叉"
                for issue in issues
            )
        )

    def test_separate_target_alias_in_embodiment_does_not_need_short_component_label(self):
        combined_title = "培林及前叉裝置"
        lines = [
            line.replace("測試裝置", combined_title)
            if line.startswith("【中文新型名稱】")
            else line
            for line in VALID_LINES
        ]
        symbol_insert = lines.index("20:分隔板") + 1
        lines.insert(symbol_insert, "30:前叉")
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "參閱圖2，本發明前叉裝置的一第一實施例。"
            "該前叉裝置包含主箱體10及分隔板20。"
        )

        issues = review_document(build_document(lines)).issues

        self.assertFalse(
            any(
                issue.rule_id == "REF004"
                and issue.details.get("component_name") == "前叉"
                for issue in issues
            )
        )

    def test_exact_component_name_has_priority_over_target_alias_protection(self):
        combined_title = "培林及前叉裝置"
        lines = [
            line.replace("測試裝置", combined_title)
            if line.startswith("【中文新型名稱】")
            else line
            for line in VALID_LINES
        ]
        symbol_insert = lines.index("20:分隔板") + 1
        lines.insert(symbol_insert, "30:前叉裝置")
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "參閱圖2，本發明前叉裝置的一第一實施例。"
            "該前叉裝置包含主箱體10及分隔板20。"
        )

        issues = review_document(build_document(lines)).issues

        self.assertTrue(
            any(
                issue.rule_id == "REF004"
                and issue.details.get("component_name") == "前叉裝置"
                for issue in issues
            )
        )

    def test_modifier_and_coordinated_target_aliases_protect_embedded_short_names(self):
        cases = (
            ("具有紫外線殺菌裝置的風箱", "紫外線殺菌裝置", "紫外線"),
            ("具有電池的輕量化滑鼠", "輕量化滑鼠", "滑鼠"),
            ("送料裝置及金爐", "送料裝置", "送料"),
        )
        for title, target_alias, short_component in cases:
            with self.subTest(title=title):
                lines = [
                    line.replace("測試裝置", title)
                    if line.startswith("【中文新型名稱】")
                    else line
                    for line in VALID_LINES
                ]
                symbol_insert = lines.index("20:分隔板") + 1
                lines.insert(symbol_insert, f"30:{short_component}")
                lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
                    f"該{target_alias}包含主箱體10及分隔板20。"
                )

                issues = review_document(build_document(lines)).issues

                self.assertFalse(
                    any(
                        issue.rule_id == "REF004"
                        and issue.details.get("component_name") == short_component
                        for issue in issues
                    )
                )

    def test_embodiment_local_terms_suppress_overlap_and_legal_variant_false_positives(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "30:定位部",
            "31:第一導引單元",
        ]
        lines[lines.index("測試裝置包含主箱體10及分隔板20。")] = (
            "本實施例另界定一局部定位部件及一第二導引單元，該局部定位部件"
            "可連接該第二導引單元、主箱體10及分隔板20。"
        )

        issues = review_document(build_document(lines)).issues

        self.assertFalse(
            any(
                issue.rule_id == "REF004"
                and issue.details.get("component_name") == "定位部"
                for issue in issues
            )
        )
        self.assertFalse(
            any(
                issue.rule_id == "REF005"
                and issue.details.get("candidate") == "第二導引單元"
                for issue in issues
            )
        )

    def test_missing_claim_antecedent_is_an_error(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines.insert(symbol_insert, "40:外殼")
        lines[-2:] = [
            "【請求項1】一種測試裝置，包含一主箱體。",
            "【請求項2】如請求項1所述的測試裝置，其中該外殼連接該主箱體。",
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "CLM016"
            and issue.details.get("component_name") == "外殼"
        ]

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")
        self.assertIn("沒有此構件的數量詞揭露", issues[0].message)

    def test_independent_control_method_inherits_cited_claim_components(self):
        for linking_phrase in ("適用於如", "運用於如", "包含如"):
            with self.subTest(linking_phrase=linking_phrase):
                lines = list(VALID_LINES)
                lines[-2:] = [
                    (
                        "【請求項1】一種測試裝置，包含一主箱體及一分隔板，"
                        "該主箱體連接該分隔板。"
                    ),
                    (
                        "【請求項2】一種測試裝置的控制方法，"
                        f"{linking_phrase}請求項1所述的測試裝置，"
                        "包含控制該主箱體及該分隔板。"
                    ),
                ]

                review = review_document(build_document(lines))
                failures = [
                    issue
                    for issue in review.issues
                    if issue.rule_id in {
                        "CLM003",
                        "CLM004",
                        "CLM005",
                        "CLM011",
                        "CLM012",
                        "CLM015",
                        "CLM016",
                    }
                ]

                self.assertEqual(failures, [])
                self.assertEqual(
                    review.claim_subjects,
                    ["測試裝置", "測試裝置的控制方法"],
                )

    def test_quantified_as_described_opening_is_an_independent_target(self):
        for opening in (
            "二如請求項1所述的培林裝置",
            "複數如請求項1所述的培林裝置",
        ):
            with self.subTest(opening=opening):
                title = "培林裝置"
                lines = [
                    line.replace("測試裝置", title)
                    if line.startswith("【中文新型名稱】")
                    else line
                    for line in VALID_LINES[:-2]
                ]
                lines.extend(
                    [
                        "【請求項1】一種培林裝置，包含一主箱體。",
                        f"【請求項2】{opening}，包含該主箱體。",
                    ]
                )

                review = review_document(build_document(lines))
                failures = [
                    issue
                    for issue in review.issues
                    if issue.rule_id in {
                        "CLM003",
                        "CLM004",
                        "CLM005",
                        "CLM011",
                        "CLM012",
                        "CLM015",
                        "CLM016",
                    }
                ]

                self.assertEqual(failures, [])
                self.assertEqual(review.claim_subjects, ["培林裝置"])

    def test_quantified_as_described_range_inherits_all_prior_claim_foundations(self):
        title = "培林裝置"
        lines = [
            line.replace("測試裝置", title)
            if line.startswith("【中文新型名稱】")
            else line
            for line in VALID_LINES[:-2]
        ]
        symbol_insert = lines.index("20:分隔板") + 1
        lines.insert(symbol_insert, "90:中間元件")
        lines.append("【請求項1】一種培林裝置，包含一主箱體。")
        lines.extend(
            (
                f"【請求項{number}】如請求項1所述的培林裝置，其中"
                f"該主箱體可轉動"
                + ("，並包含一中間元件。" if number == 4 else "。")
            )
            for number in range(2, 9)
        )
        lines.append(
            "【請求項9】一如請求項1至請求項8之中任一項所述的培林裝置，"
            "包含該主箱體及該中間元件。"
        )

        review = review_document(build_document(lines))
        failures = [
            issue
            for issue in review.issues
            if issue.rule_id in {
                "CLM003",
                "CLM004",
                "CLM005",
                "CLM011",
                "CLM012",
                "CLM015",
                "CLM016",
            }
        ]

        self.assertEqual(failures, [])
        self.assertEqual(review.claim_subjects, ["培林裝置"])

    def test_quantified_as_described_component_inside_second_independent_claim(self):
        combined_title = "培林裝置及前叉裝置"
        lines = [
            line.replace("測試裝置", combined_title)
            if line.startswith("【中文新型名稱】")
            else line
            for line in VALID_LINES[:-2]
        ]
        symbol_insert = lines.index("20:分隔板") + 1
        lines.insert(symbol_insert, "90:培林裝置")
        lines.extend(
            [
                "【請求項1】一種培林裝置，包含一主箱體。",
                (
                    "【請求項2】一種前叉裝置，包含二如請求項1所述的培林裝置，"
                    "且該等培林裝置連接一分隔板。"
                ),
            ]
        )

        review = review_document(build_document(lines))
        failures = [
            issue
            for issue in review.issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") == "培林裝置"
        ]

        self.assertEqual(failures, [])
        self.assertEqual(review.claim_subjects, ["培林裝置", "前叉裝置"])

    def test_as_described_range_is_primary_dependency_source(self):
        lines = list(VALID_LINES[:-2])
        lines.append(
            "【請求項1】一種測試裝置，包含一主箱體及一分隔板，"
            "該主箱體連接該分隔板。"
        )
        lines.extend(
            (
                f"【請求項{number}】如請求項1所述的測試裝置，其中"
                "該主箱體連接該分隔板。"
            )
            for number in range(2, 9)
        )
        lines.append(
            "【請求項9】一種測試裝置的控制方法，適用於如請求項1到8中"
            "任一項所述的測試裝置，包含控制該主箱體及該分隔板，並產生"
            "請求項99格式的控制資訊。"
        )

        review = review_document(build_document(lines))
        failures = [
            issue
            for issue in review.issues
            if issue.rule_id in {
                "CLM003",
                "CLM004",
                "CLM005",
                "CLM011",
                "CLM012",
                "CLM015",
                "CLM016",
            }
        ]

        self.assertEqual(failures, [])
        self.assertEqual(
            review.claim_subjects,
            ["測試裝置", "測試裝置的控制方法"],
        )

    def test_multiple_independent_claim_subjects_are_accumulated(self):
        combined_title = "測試裝置及測試方法"
        lines = [
            line.replace("測試裝置", combined_title)
            if line.startswith("【中文新型名稱】")
            else line
            for line in VALID_LINES[:-2]
        ]
        lines.extend(
            [
                "【請求項1】一種測試裝置，包含：",
                "一主箱體。",
                "【請求項2】如請求項1所述的測試裝置，其中該主箱體可移動。",
                "【請求項3】測試方法，包含：",
                "執行一檢測步驟。",
                "【請求項4】如請求項3所述的測試方法，其中該檢測步驟重複執行。",
            ]
        )

        review = review_document(build_document(lines))

        self.assertEqual(review.claim_subjects, ["測試裝置", "測試方法"])
        self.assertFalse(
            any(
                issue.rule_id in {"CLM011", "CLM014", "CLM015"}
                for issue in review.issues
            )
        )

    def test_adding_another_component_promotes_it_to_plural(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines.insert(symbol_insert, "40:開槽")
        lines[-2:] = [
            "【請求項1】一種測試裝置，包含一開槽。",
            (
                "【請求項2】如請求項1所述的測試裝置，還包括另一該開槽，"
                "並且該等開槽彼此間隔。"
            ),
            (
                "【請求項3】如請求項2所述的測試裝置，其中該等開槽"
                "分別沿一方向延伸。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") == "開槽"
        ]

        self.assertEqual(issues, [])

    def test_quantity_before_long_component_modifier_establishes_antecedent(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "40:第一按鍵部",
            "41:第一訊息攜帶區",
            "42:第一快拆螺孔",
            "43:快拆螺絲",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含複數第一按鍵部及"
                "一第一訊息攜帶區，還包含一與該等第一按鍵部及"
                "該第一訊息攜帶區相間隔的第一快拆螺孔及一快拆螺絲，"
                "該快拆螺絲鎖入該第一快拆螺孔。"
            ),
            "【請求項2】如請求項1所述的測試裝置，其中該第一快拆螺孔可定位。",
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") in {
                "第一快拆螺孔",
                "快拆螺絲",
            }
        ]

        self.assertEqual(issues, [])

    def test_multiple_premodified_components_in_one_clause_are_introduced(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "50:底座",
            "51:承接按鈕部",
            "52:頂面",
            "53:底面",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含一底座及一承接按鈕部，"
                "其中該底座還具有一與該承接按鈕部相鄰的頂面及"
                "一位於該頂面下方的底面，該頂面與該底面相間隔。"
            ),
            "【請求項2】如請求項1所述的測試裝置，其中該頂面可承載物件。",
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") in {"頂面", "底面"}
        ]

        self.assertEqual(issues, [])

    def test_relational_component_modifiers_establish_singular_quantity(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "60:底部開口",
            "61:安裝方向",
            "62:橫軸項",
            "63:移動軸線",
            "64:電池空間",
            "65:退出方向",
            "66:第一側方向",
            "67:橫軸向",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含一底部開口、一安裝方向、"
                "一橫軸項及一移動軸線，該裝置界定一連通該底部開口的"
                "電池空間，並定義一相反該安裝方向的退出方向、"
                "一垂直該橫軸項與該移動軸線的第一側方向及"
                "一垂直該移動軸線的橫軸向，該電池空間沿該退出方向延伸，"
                "且該第一側方向垂直該橫軸向。"
            ),
            "【請求項2】如請求項1所述的測試裝置，其中該電池空間可容納電池。",
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") in {
                "電池空間",
                "退出方向",
                "第一側方向",
                "橫軸向",
            }
        ]

        self.assertEqual(issues, [])

    def test_quantity_within_sixteen_characters_before_de_introduces_component(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = ["100:開口", "101:導流件"]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含一開口，並界定複數經由該開口"
                "向外延伸的導流件，該等導流件環繞該開口。"
            ),
            (
                "【請求項2】如請求項1所述的測試裝置，其中該等導流件"
                "分別朝向該開口。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") == "導流件"
        ]

        self.assertEqual(issues, [])

    def test_nested_quantity_within_sixteen_characters_is_not_borrowed(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = ["100:開口", "101:導流件"]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含一開口，並具有沿一頂底方向"
                "延伸的導流件，該等導流件環繞該開口。"
            ),
            "【請求項2】如請求項1所述的測試裝置，其中該開口可供流體通過。",
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "CLM012"
            and issue.details.get("component_name") == "導流件"
        ]

        self.assertTrue(any("沒有辨識到數量詞" in issue.message for issue in issues))
        self.assertTrue(all(issue.severity == "error" for issue in issues))

    def test_extended_component_determiners_are_recognized(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines.insert(symbol_insert, "120:支架")
        lines[-2:] = [
            "【請求項1】一種測試裝置，包含複數支架。",
            (
                "【請求項2】如請求項1所述的測試裝置，其中任意該支架"
                "可被選取，且其中複數該支架可同時移動。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") == "支架"
        ]

        self.assertEqual(issues, [])

    def test_de_anchored_modifier_references_bind_outer_determiners(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = ["120:外殼", "121:支架"]
        lines[-2:] = [
            "【請求項1】一種測試裝置，包含一外殼及複數支架。",
            (
                "【請求項2】如請求項1所述的測試裝置，其中該沿一方向"
                "延伸的外殼連接其中複數該沿一軸向排列的支架。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") in {"外殼", "支架"}
        ]

        self.assertEqual(issues, [])

    def test_de_anchor_does_not_borrow_inner_component_article(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = ["120:基座", "121:外殼"]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含一基座及位於該基座上的"
                "外殼。"
            ),
            "【請求項2】如請求項1所述的測試裝置，其中該基座可移動。",
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "CLM012"
            and issue.details.get("component_name") == "外殼"
        ]

        self.assertTrue(any("沒有辨識到數量詞" in issue.message for issue in issues))

    def test_possessive_component_hierarchy_inherits_root_quantity(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "90:安裝座",
            "91:底壁",
            "92:開槽",
            "93:缺槽",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含一安裝座，該安裝座的底壁"
                "鄰接該安裝座的開槽的缺槽，該底壁連接該開槽，且該缺槽"
                "朝向該安裝座。"
            ),
            (
                "【請求項2】如請求項1所述的測試裝置，其中該底壁、"
                "該開槽及該缺槽彼此相鄰。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") in {
                "安裝座",
                "底壁",
                "開槽",
                "缺槽",
            }
        ]

        self.assertEqual(issues, [])

    def test_plural_possessive_component_hierarchy_pluralizes_lower_levels(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "90:安裝座",
            "91:底壁",
            "92:開槽",
            "93:缺槽",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含複數安裝座，每一該安裝座"
                "的底壁鄰接該等安裝座的開槽的缺槽，該等底壁連接該等"
                "開槽，且該等缺槽分別朝向該等安裝座。"
            ),
            (
                "【請求項2】如請求項1所述的測試裝置，其中該等底壁、"
                "該等開槽及該等缺槽彼此相鄰。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") in {
                "安裝座",
                "底壁",
                "開槽",
                "缺槽",
            }
        ]

        self.assertEqual(issues, [])

    def test_selected_parent_member_keeps_possessive_child_singular(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = ["90:安裝座", "91:底壁"]
        lines[-2:] = [
            "【請求項1】一種測試裝置，包含複數安裝座。",
            (
                "【請求項2】如請求項1所述的測試裝置，其中一該安裝座"
                "的底壁連接該等底壁。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "CLM012"
            and issue.details.get("component_name") == "底壁"
        ]

        self.assertTrue(any("只以單數揭露" in issue.message for issue in issues))
        self.assertTrue(all(issue.severity == "error" for issue in issues))

    def test_one_child_per_plural_parent_establishes_plural_children(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "80:按鍵部",
            "81:接點",
            "82:彈性件",
            "83:標記",
            "84:導電片",
        ]
        lines[-2:] = [
            "【請求項1】一種測試裝置，包含複數按鍵部。",
            (
                "【請求項2】如請求項1所述的測試裝置，其中每一該按鍵部"
                "具有一接點及一彈性件，各自該按鍵部設有一標記，且該等"
                "按鍵部分別包括一導電片，該等接點連接該等彈性件，該等"
                "標記分別對應該等導電片。"
            ),
            (
                "【請求項3】如請求項2所述的測試裝置，其中該等接點、"
                "該等彈性件、該等標記及該等導電片彼此間隔。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") in {
                "按鍵部",
                "接點",
                "彈性件",
                "標記",
                "導電片",
            }
        ]

        self.assertEqual(issues, [])

    def test_plural_parent_each_establishes_plural_arc_children(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "80:密封線段",
            "81:水平軸線",
            "82:密封線段弧度",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含複數密封線段及一水平軸線，"
                "其中，該等密封線段各自與該水平軸線夾有一密封線段弧度，"
                "每一該密封線段弧度決定了密封效果。"
            ),
            (
                "【請求項2】如請求項1所述的測試裝置，其中該等密封線段"
                "弧度彼此相同。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") == "密封線段弧度"
        ]

        self.assertEqual(issues, [])

    def test_plural_parent_each_establishes_unquantified_following_segments(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "90:公轉子齒",
            "91:公齒型段",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含複數公轉子齒，該等公轉子齒"
                "的齒型相同並各自具有以下公齒型段，該等公齒型段彼此相連。"
            ),
            (
                "【請求項2】如請求項1所述的測試裝置，其中每一該公齒型段"
                "沿一方向延伸。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") == "公齒型段"
        ]

        self.assertEqual(issues, [])

    def test_distributive_hierarchy_allows_temporary_singular_child_reference(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "110:按鍵部",
            "111:模組",
            "112:接點",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含複數按鍵部、複數模組及"
                "複數接點，該等按鍵部連接該等模組及該等接點。"
            ),
            (
                "【請求項2】如請求項1所述的測試裝置，其中每一該按鍵部"
                "的該模組的該接點可分別導通。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") in {
                "按鍵部",
                "模組",
                "接點",
            }
        ]

        self.assertEqual(issues, [])

    def test_distributive_clause_keeps_children_locally_singular(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "120:送料輥",
            "121:輥本體",
            "122:防滑層",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含複數送料輥，其中，每一該"
                "送料輥具有一輥本體，及緊貼環繞該輥本體的一防滑層。"
            ),
            (
                "【請求項2】如請求項1所述的測試裝置，其中該等輥本體"
                "分別支撐該等防滑層。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") in {
                "送料輥",
                "輥本體",
                "防滑層",
            }
        ]

        self.assertEqual(issues, [])

    def test_singular_reference_after_plural_is_temporarily_allowed(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "120:送料輥",
            "121:輥本體",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含複數送料輥，其中，每一該"
                "送料輥具有一輥本體。"
            ),
            (
                "【請求項2】如請求項1所述的測試裝置，其中該輥本體"
                "沿一方向延伸。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "CLM012"
            and issue.details.get("component_name") == "輥本體"
        ]

        self.assertEqual(issues, [])

    def test_distributive_hierarchy_allows_sibling_singular_children(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "130:送料孔道",
            "131:第一孔段",
            "132:第二孔段",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含複數送料孔道、複數第一孔段"
                "及複數第二孔段。"
            ),
            (
                "【請求項2】如請求項1所述的測試裝置，其中每一該送料孔道"
                "的該第一孔段及該第二孔段彼此連通。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") in {
                "送料孔道",
                "第一孔段",
                "第二孔段",
            }
        ]

        self.assertEqual(issues, [])

    def test_one_selected_plural_member_does_not_pluralize_its_child(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = ["80:按鍵部", "81:測試接點"]
        lines[-2:] = [
            "【請求項1】一種測試裝置，包含複數按鍵部。",
            (
                "【請求項2】如請求項1所述的測試裝置，其中一該按鍵部"
                "具有一測試接點，該等測試接點彼此間隔。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id == "CLM012"
            and issue.details.get("component_name") == "測試接點"
        ]

        self.assertTrue(any("只以單數揭露" in issue.message for issue in issues))
        self.assertTrue(all(issue.severity == "error" for issue in issues))

    def test_plural_word_quantifiers_establish_plural_components(self):
        for quantifier in ("二", "兩", "數個", "多個", "複數"):
            with self.subTest(quantifier=quantifier):
                lines = list(VALID_LINES)
                symbol_insert = lines.index("20:分隔板") + 1
                lines.insert(symbol_insert, "40:開槽")
                lines[-2:] = [
                    (
                        f"【請求項1】一種測試裝置，包含{quantifier}開槽，"
                        "該等開槽彼此間隔。"
                    ),
                    (
                        "【請求項2】如請求項1所述的測試裝置，其中"
                        "每一該開槽沿一方向延伸。"
                    ),
                ]

                issues = [
                    issue
                    for issue in review_document(build_document(lines)).issues
                    if issue.rule_id in {"CLM012", "CLM016"}
                    and issue.details.get("component_name") == "開槽"
                ]

                self.assertEqual(issues, [])

    def test_two_and_liang_establish_the_same_plural_component_state(self):
        for quantifier in ("二", "兩"):
            with self.subTest(quantifier=quantifier):
                lines = list(VALID_LINES)
                symbol_insert = lines.index("20:分隔板") + 1
                lines.insert(symbol_insert, "90:電池盒")
                lines[-2:] = [
                    (
                        f"【請求項1】一種測試裝置，包含{quantifier}電池盒，"
                        "該等電池盒彼此間隔。"
                    ),
                    (
                        "【請求項2】如請求項1所述的測試裝置，其中"
                        "該等電池盒分別可拆卸。"
                    ),
                ]

                issues = [
                    issue
                    for issue in review_document(build_document(lines)).issues
                    if issue.rule_id in {"CLM012", "CLM016"}
                    and issue.details.get("component_name") == "電池盒"
                ]

                self.assertEqual(issues, [])

    def test_nested_modifier_quantities_bind_to_the_correct_component_level(self):
        lines = list(VALID_LINES)
        symbol_insert = lines.index("20:分隔板") + 1
        lines[symbol_insert:symbol_insert] = [
            "70:基座",
            "71:開孔",
            "72:螺絲",
            "73:固定板",
            "74:外殼",
        ]
        lines[-2:] = [
            (
                "【請求項1】一種測試裝置，包含一沿一頂底方向設置的基座、"
                "複數沿一軸向排列的開孔、一供二螺絲穿過的固定板及"
                "一位於一第一方向與一第二方向之間的外殼，該基座連接"
                "該固定板，該等開孔環繞該外殼，且該等螺絲穿過該固定板。"
            ),
            (
                "【請求項2】如請求項1所述的測試裝置，其中該基座承載"
                "該外殼，該等開孔供該等螺絲穿過。"
            ),
        ]

        issues = [
            issue
            for issue in review_document(build_document(lines)).issues
            if issue.rule_id in {"CLM012", "CLM016"}
            and issue.details.get("component_name") in {
                "基座",
                "開孔",
                "螺絲",
                "固定板",
                "外殼",
            }
        ]

        self.assertEqual(issues, [])

    def test_embodiment_ocr_rule_expands_ranges_and_discontinuous_figures(self):
        lines = list(VALID_LINES)
        embodiment_index = lines.index("【實施方式】") + 1
        lines[embodiment_index] = (
            "參閱圖1到圖4及圖6，主箱體10、分隔板20、元件30、"
            "元件40及元件60共同組成測試裝置。"
        )
        symbol_index = lines.index("20:分隔板") + 1
        lines[symbol_index:symbol_index] = [
            "30:元件",
            "40:元件",
            "60:元件",
        ]
        ocr_results = [
            {"source_image_name": f"圖{number}.png", "numbers": [str(number * 10)]}
            for number in (1, 2, 3, 4, 6)
        ]

        issues = [
            issue
            for issue in review_document(
                build_document(lines),
                ocr_results=ocr_results,
            ).issues
            if issue.rule_id == "OCR001"
        ]

        self.assertEqual(issues, [])

    def test_leading_figure_parser_stops_before_component_labels(self):
        reference = parse_leading_figure_reference(
            "參閱圖1到圖4以及圖6所示的主箱體10及分隔板20。"
        )

        self.assertIsNotNone(reference)
        self.assertEqual(reference.figures, (1, 2, 3, 4, 6))
        self.assertNotIn("10", reference.text)

    def test_editable_figure_mapping_accepts_lists_and_ranges(self):
        self.assertEqual(parse_figure_number_mapping("圖1,2及4-6"), [1, 2, 4, 5, 6])
        with self.assertRaises(ValueError):
            parse_figure_number_mapping("4-2")

    def test_partial_reference_to_multi_figure_page_uses_subset_check(self):
        lines = list(VALID_LINES)
        embodiment_index = lines.index("【實施方式】") + 1
        lines[embodiment_index] = "參閱圖1，主箱體10形成測試裝置。"

        issues = [
            issue
            for issue in review_document(
                build_document(lines),
                ocr_results=[
                    {
                        "image_name": "Pic_01",
                        "figure_numbers": [1, 2],
                        "numbers": ["10", "20"],
                    }
                ],
            ).issues
            if issue.rule_id == "OCR001"
        ]

        self.assertEqual(issues, [])

    def test_labels_only_in_drawing_are_not_reported(self):
        lines = list(VALID_LINES)
        embodiment_index = lines.index("【實施方式】") + 1
        lines[embodiment_index] = "參閱圖1及圖2，主箱體10形成測試裝置。"

        issues = [
            issue
            for issue in review_document(
                build_document(lines),
                ocr_results=[
                    {
                        "image_name": "Pic_01",
                        "figure_numbers": [1, 2],
                        "numbers": ["10", "20"],
                    }
                ],
            ).issues
            if issue.rule_id == "OCR001"
        ]

        self.assertEqual(issues, [])

    def test_later_page_can_be_mapped_to_figure_three(self):
        lines = list(VALID_LINES)
        embodiment_index = lines.index("【實施方式】") + 1
        lines[embodiment_index] = "參閱圖3，主箱體10形成測試裝置。"

        issues = [
            issue
            for issue in review_document(
                build_document(lines),
                ocr_results=[
                    {
                        "image_name": "Pic_01",
                        "figure_numbers": [1, 2],
                        "numbers": ["20"],
                    },
                    {
                        "image_name": "Pic_02",
                        "figure_numbers": [3],
                        "numbers": ["10"],
                    },
                ],
            ).issues
            if issue.rule_id == "OCR001"
        ]

        self.assertEqual(issues, [])

    def test_embodiment_ocr_rule_inherits_previous_paragraph_figures(self):
        lines = list(VALID_LINES)
        embodiment_index = lines.index("【實施方式】") + 1
        lines[embodiment_index:embodiment_index + 1] = [
            "參閱圖1，主箱體10及分隔板20共同組成測試裝置。",
            "主箱體10連接分隔板20。",
        ]

        issues = [
            issue
            for issue in review_document(
                build_document(lines),
                ocr_results=[{"image_name": "Pic_01", "numbers": ["10", "20"]}],
            ).issues
            if issue.rule_id == "OCR001"
        ]

        self.assertEqual(issues, [])

    def test_embodiment_ocr_rule_reports_exact_set_differences_as_error(self):
        lines = list(VALID_LINES)
        embodiment_index = lines.index("【實施方式】") + 1
        lines[embodiment_index] = "參閱圖1，主箱體10及分隔板20共同組成測試裝置。"

        issues = [
            issue
            for issue in review_document(
                build_document(lines),
                ocr_results=[{"image_name": "Pic_01", "numbers": ["10", "30"]}],
            ).issues
            if issue.rule_id == "OCR001"
        ]

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")
        self.assertEqual(issues[0].details["labels_not_in_drawings"], ["20"])
        self.assertEqual(issues[0].details["labels_not_in_paragraph"], [])
        self.assertIn("段落有、圖式沒有：20", issues[0].message)
        self.assertNotIn("圖式有、段落沒有", issues[0].message)

    def test_see_figure_parenthesis_exempts_label_from_active_figure_check(self):
        lines = list(VALID_LINES)
        embodiment_index = lines.index("【實施方式】") + 1
        lines[embodiment_index] = (
            "參閱圖2，分隔板20鄰近廟宇3（見圖1）。"
        )
        symbol_index = lines.index("20:分隔板") + 1
        lines.insert(symbol_index, "3:廟宇")

        issues = [
            issue
            for issue in review_document(
                build_document(lines),
                ocr_results=[
                    {"source_image_name": "圖2.png", "numbers": ["20"]},
                ],
            ).issues
            if issue.rule_id == "OCR001"
        ]

        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
