"""Conservative abstract-length and whole-claim duplicate reminders."""

import re
import unittest

from features.patent_review.models import PatentDocument, PatentParagraph
from features.patent_review.rule_engine import RULE_CATALOG, review_document
from features.patent_review.section_parser import assign_sections
from features.patent_review.text_normalizer import normalize_patent_text


def reminder_document(
    abstract=None, claims=None, *, english=None, auto_numbered=True,
    inline_abstract=False, utility_model=False,
):
    """Keep fixtures independent of the large legacy text-rule test module."""
    lines = ["【發明摘要】", "【中文發明名稱】測試裝置", "【中文】"]
    lines.extend(abstract if abstract is not None else ["本發明涉及測試裝置。"])
    if english is not None:
        lines.extend(["【英文發明名稱】Testing device", "【英文】", *english])
    lines.extend([
        "【指定代表圖】圖1",
        "【代表圖之符號簡單說明】",
        "10:基座",
        "【發明說明書】",
        "【中文發明名稱】測試裝置",
        "【技術領域】",
        "本發明涉及測試裝置。",
        "【先前技術】",
        "習知技術內容。",
        "【發明內容】",
        "本發明提供測試裝置。",
        "【圖式簡單說明】",
        "圖1是測試裝置的示意圖。",
        "【實施方式】",
        "該基座10構成測試裝置。",
        "【符號說明】",
        "10:基座",
        "【發明申請專利範圍】",
    ])
    lines.extend(claims if claims is not None else ["【請求項1】一種測試裝置，包含一基座。"])
    if inline_abstract:
        lines[2] += lines.pop(3)
    if utility_model:
        lines = [line.replace("發明", "新型") for line in lines]
    paragraphs = [
        PatentParagraph(
            index=index,
            text=text,
            normalized_text=normalize_patent_text(text),
            source_path=f"body/p[{index}]",
        )
        for index, text in enumerate(lines)
    ]
    sections, patent_type, patent_title = assign_sections(paragraphs)
    if auto_numbered:
        for paragraph in paragraphs:
            match = re.match(r"^【請求項(?P<number>\d+)】", paragraph.text)
            if match and paragraph.major_section_key == "claims":
                paragraph.numbering_id = 20
                paragraph.numbering_level = 0
                paragraph.numbering_value = int(match.group("number"))
                paragraph.numbering_text = match.group(0)
                paragraph.text = paragraph.text[match.end():]
                paragraph.normalized_text = normalize_patent_text(paragraph.text)
                paragraph.content_text = paragraph.normalized_text
    return PatentDocument(
        source_path="C:/synthetic/reminders.docx",
        file_name="reminders.docx",
        file_size_bytes=0,
        sha256="a" * 64,
        patent_type=patent_type,
        patent_title=patent_title,
        paragraphs=paragraphs,
        sections=sections,
    )


def reminder_issues(document, rule_id):
    return [issue for issue in review_document(document).issues if issue.rule_id == rule_id]


class AbstractLengthReminderTests(unittest.TestCase):
    def test_catalog_registers_both_reminders_as_non_errors(self):
        definitions = {rule.rule_id: rule for rule in RULE_CATALOG}
        for rule_id in ("ABS001", "CLM018"):
            self.assertIn(rule_id, definitions)
            self.assertEqual(definitions[rule_id].default_severity, "warning")

    def test_250_chinese_characters_do_not_warn(self):
        self.assertEqual(reminder_issues(reminder_document(["摘" * 250]), "ABS001"), [])

    def test_251_chinese_characters_warn_once_with_both_counts(self):
        issues = reminder_issues(reminder_document(["摘" * 251]), "ABS001")
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue.severity, "warning")
        self.assertEqual(issue.section_key, "abstract_zh")
        self.assertEqual(issue.details["estimated_word_count"], 251)
        self.assertEqual(issue.details["non_whitespace_count"], 251)
        self.assertFalse(issue.safe_auto_fix)

    def test_inline_chinese_abstract_heading_is_not_counted_as_content(self):
        for length in (250, 251):
            with self.subTest(length=length):
                document = reminder_document(["摘" * length], inline_abstract=True)
                issues = reminder_issues(document, "ABS001")
                self.assertEqual(len(issues), int(length > 250))
                if issues:
                    self.assertEqual(issues[0].details["estimated_word_count"], length)
                    self.assertEqual(issues[0].details["non_whitespace_count"], length)

    def test_utility_model_abstract_uses_the_same_250_word_reminder(self):
        document = reminder_document(["摘" * 251], utility_model=True)
        self.assertEqual(document.patent_type, "utility_model")
        issues = reminder_issues(document, "ABS001")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warning")
        self.assertEqual(issues[0].details["estimated_word_count"], 251)

    def test_multiple_word_paragraphs_are_counted_as_one_abstract(self):
        document = reminder_document(["摘" * 125, "要" * 126])
        issues = reminder_issues(document, "ABS001")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].details["estimated_word_count"], 251)
        first_body = next(p for p in document.paragraphs if p.text == "摘" * 125)
        self.assertEqual(issues[0].paragraph_index, first_body.index)

    def test_english_abstract_and_name_are_excluded(self):
        document = reminder_document(["摘要。"], english=["English abstract " * 300])
        name = next(p for p in document.paragraphs if p.section_key == "english_title")
        name.text += " LongName" * 300
        name.normalized_text = normalize_patent_text(name.text)
        name.content_text += " LongName" * 300
        self.assertEqual(reminder_issues(document, "ABS001"), [])

    def test_representative_figure_symbols_are_excluded(self):
        document = reminder_document(["摘要。"])
        symbol = next(p for p in document.paragraphs if p.text == "10:基座")
        symbol.text += "元件" * 300
        symbol.normalized_text = normalize_patent_text(symbol.text)
        symbol.content_text = symbol.normalized_text
        self.assertEqual(reminder_issues(document, "ABS001"), [])

    def test_whitespace_is_not_counted(self):
        issues = reminder_issues(reminder_document(["摘 \t\n" * 250]), "ABS001")
        self.assertEqual(issues, [])

    def test_punctuation_only_boundary_is_information_not_warning(self):
        issues = reminder_issues(reminder_document(["摘" * 250 + "。"]), "ABS001")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "info")
        self.assertEqual(issues[0].details["estimated_word_count"], 250)
        self.assertEqual(issues[0].details["non_whitespace_count"], 251)

    def test_long_english_and_numeric_tokens_each_count_as_one(self):
        text = "摘" * 248 + " abcdefghijklmnopqrstuvwxyz 1234567890"
        issues = reminder_issues(reminder_document([text]), "ABS001")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "info")
        self.assertEqual(issues[0].details["estimated_word_count"], 250)
        self.assertEqual(issues[0].details["non_whitespace_count"], 284)

    def test_english_words_can_push_estimate_above_threshold(self):
        text = "摘" * 249 + " sensor 1234"
        issues = reminder_issues(reminder_document([text]), "ABS001")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warning")
        self.assertEqual(issues[0].details["estimated_word_count"], 251)


class DuplicateClaimReminderTests(unittest.TestCase):
    def duplicate_issues(self, bodies, **kwargs):
        claims = [f"【請求項{number}】{body}" for number, body in enumerate(bodies, 1)]
        return reminder_issues(reminder_document(claims=claims, **kwargs), "CLM018")

    def test_identical_claim_bodies_ignore_their_own_item_numbers(self):
        issues = self.duplicate_issues(["一種裝置，包含一基座。"] * 2)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warning")
        self.assertEqual(issues[0].details["duplicate_claim_numbers"], [1, 2])
        self.assertFalse(issues[0].safe_auto_fix)

    def test_empty_claims_do_not_trigger_duplicate_reminders(self):
        for auto_numbered in (True, False):
            with self.subTest(auto_numbered=auto_numbered):
                self.assertEqual(self.duplicate_issues(
                    ["", " \t", "\n"], auto_numbered=auto_numbered
                ), [])

    def test_three_identical_claims_form_one_group_not_three_pairs(self):
        issues = self.duplicate_issues(["一種裝置，包含一基座。"] * 3)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].details["duplicate_claim_numbers"], [1, 2, 3])

    def test_distinct_duplicate_groups_are_reported_separately(self):
        first = "一種裝置，包含一基座。"
        second = "一種方法，包含一處理步驟。"
        issues = self.duplicate_issues([first, second, first, second])
        groups = sorted(issue.details["duplicate_claim_numbers"] for issue in issues)
        self.assertEqual(groups, [[1, 3], [2, 4]])

    def test_layout_whitespace_and_fullwidth_forms_are_equivalent(self):
        issues = self.duplicate_issues([
            "一種裝置，包含一基座(A1)。",
            "一種裝置,\n包含\t一基座（Ａ１）。",
        ])
        self.assertEqual(len(issues), 1)

    def test_split_word_paragraphs_are_combined_before_comparison(self):
        document = reminder_document(claims=[
            "【請求項1】一種装置，包含一基座及一外殼。",
            "【請求項2】一種装置，包含",
            "一基座及",
            "一外殼。",
        ])
        issues = reminder_issues(document, "CLM018")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].details["duplicate_claim_numbers"], [1, 2])

    def test_literal_claim_markers_work_without_word_auto_numbering(self):
        issues = self.duplicate_issues(
            ["一種裝置，包含一基座。"] * 2, auto_numbered=False
        )
        self.assertEqual(len(issues), 1)

    def test_plain_word_numbering_without_brackets_is_supported(self):
        document = reminder_document(claims=[
            "【請求項1】一種裝置，包含一基座。",
            "【請求項2】一種裝置，包含一基座。",
        ])
        for paragraph in document.paragraphs:
            if paragraph.numbering_value:
                paragraph.numbering_text = f"請求項{paragraph.numbering_value}"
        self.assertEqual(len(reminder_issues(document, "CLM018")), 1)

    def test_dependency_numbers_are_not_removed(self):
        bodies = [
            "一種裝置，包含一基座。",
            "如請求項1所述的裝置，其中該基座為金屬。",
            "如請求項2所述的裝置，其中該基座為金屬。",
        ]
        self.assertEqual(self.duplicate_issues(bodies), [])

    def test_quantities_negation_case_and_prime_remain_meaningful(self):
        pairs = [
            ("一基座", "複數基座"),
            ("一基座", "二基座"),
            ("長度10毫米", "長度11毫米"),
            ("具有開口", "不具有開口"),
            ("基座A", "基座a"),
            ("基座A", "基座A'"),
            ("基座A'", "基座A''"),
            ("面積1m²", "面積1m2"),
            ("型號a b", "型號ab"),
            ("數值1 0", "數值10"),
        ]
        for first, second in pairs:
            with self.subTest(first=first, second=second):
                self.assertEqual(self.duplicate_issues([
                    f"一種裝置，包含{first}。",
                    f"一種裝置，包含{second}。",
                ]), [])

    def test_equivalent_prime_glyphs_preserve_the_same_label(self):
        issues = self.duplicate_issues([
            "一種裝置，包含一基座A'。",
            "一種裝置，包含一基座A′。",
        ])
        self.assertEqual(len(issues), 1)

    def test_reflowed_english_words_preserve_word_boundaries(self):
        issues = self.duplicate_issues([
            "一種裝置，包含一sensor array。",
            "一種裝置，包含一sensor\n\tarray。",
        ])
        self.assertEqual(len(issues), 1)

    def test_repeated_description_text_is_not_a_duplicate_claim(self):
        document = reminder_document(claims=["【請求項1】一種裝置，包含一基座。"])
        for paragraph in document.paragraphs:
            if paragraph.section_key in {"technical_field", "background_art", "embodiments"} and not paragraph.is_heading:
                paragraph.text = "一種裝置，包含一基座。"
                paragraph.normalized_text = paragraph.text
                paragraph.content_text = paragraph.text
        self.assertEqual(reminder_issues(document, "CLM018"), [])

    def test_partial_overlap_is_not_a_whole_claim_duplicate(self):
        self.assertEqual(self.duplicate_issues([
            "一種裝置，包含一基座及一外殼。",
            "一種裝置，包含一基座及一外殼，該外殼具有一開口。",
        ]), [])


if __name__ == "__main__":
    unittest.main()
