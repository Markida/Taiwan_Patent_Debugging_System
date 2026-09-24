"""Coverage rule integration without changing the existing claim grammar."""

import json
import re
import unittest

from features.patent_review.models import PatentDocument, PatentParagraph
from features.patent_review.rule_engine import review_document
from features.patent_review.section_parser import assign_sections
from features.patent_review.text_normalizer import normalize_patent_text


def coverage_document(disclosure, claims=None, embodiments=None):
    lines = [
        "【發明說明書】", "【中文發明名稱】測試裝置", "【發明內容】",
        *disclosure, "【實施方式】", *(embodiments or ["本發明包含基座及外殼。"]),
        "【符號說明】", "1:基座", "2:外殼", "3:開口",
        "【發明申請專利範圍】",
        *(claims or ["【請求項1】一種測試裝置，包含一基座及一外殼。"]),
    ]
    paragraphs = [
        PatentParagraph(
            index=index, text=text, normalized_text=normalize_patent_text(text),
            source_path=f"body/p[{index}]",
        )
        for index, text in enumerate(lines)
    ]
    sections, patent_type, title = assign_sections(paragraphs)
    for paragraph in paragraphs:
        match = re.match(r"^【請求項(\d+)】", paragraph.text)
        if match:
            paragraph.numbering_text = match.group(0)
            paragraph.numbering_value = int(match.group(1))
            paragraph.text = paragraph.text[match.end():]
            paragraph.normalized_text = normalize_patent_text(paragraph.text)
            paragraph.content_text = paragraph.normalized_text
        if paragraph.section_key == "disclosure" and not paragraph.is_heading:
            paragraph.numbering_text = "【0004】"
    return PatentDocument(
        source_path="coverage.docx", file_name="coverage.docx",
        file_size_bytes=0, sha256="c" * 64, patent_type=patent_type,
        patent_title=title, paragraphs=paragraphs, sections=sections,
    )


class ClaimCoverageIntegrationTests(unittest.TestCase):
    def test_exact_claim_is_covered_without_coverage_issues(self):
        review = review_document(coverage_document([
            "一種測試裝置，包含一基座及一外殼。",
        ]))
        self.assertEqual(review.claim_disclosure_coverage["status"], "covered")
        self.assertFalse([i for i in review.issues if i.rule_id == "CLM019"])
        payload = json.loads(json.dumps(review.to_dict(), ensure_ascii=False))
        self.assertEqual(payload["claim_disclosure_coverage"]["claim_number"], 1)

    def test_other_claims_are_not_added_to_claim_one_coverage(self):
        review = review_document(coverage_document(
            ["一種測試裝置，包含一基座及一外殼。"], claims=[
                "【請求項1】一種測試裝置，包含一基座及一外殼。",
                "【請求項2】如請求項1所述的測試裝置，其中該外殼具有二開口。",
            ],
        ))
        self.assertEqual(review.claim_disclosure_coverage["status"], "covered")
        items = review.claim_disclosure_coverage["items"]
        self.assertNotIn("二開口", "".join(item["claim_text"] for item in items))

    def test_embodiments_do_not_fill_disclosure_gap(self):
        claim = "一種測試裝置，包含一可拆卸地連接於該基座的外殼。"
        review = review_document(coverage_document(
            ["本發明提供測試裝置。"], claims=["【請求項1】" + claim],
            embodiments=[claim],
        ))
        self.assertNotEqual(review.claim_disclosure_coverage["status"], "covered")
        issues = [i for i in review.issues if i.rule_id == "CLM019"]
        self.assertTrue(issues)
        self.assertTrue(all(i.severity in {"warning", "info"} for i in issues))
        self.assertTrue(all(not i.safe_auto_fix for i in issues))

    def test_multiline_claim_reuses_existing_parser_and_original_offsets(self):
        document = coverage_document(
            ["一種測試裝置，包含一基座及一外殼。"], claims=[
                "【請求項1】一種測試裝置，包含",
                "一基座及一外殼。",
            ],
        )
        review = review_document(document)
        self.assertEqual(review.claim_disclosure_coverage["status"], "covered")
        body = "一種測試裝置，包含\n一基座及一外殼。"
        for item in review.claim_disclosure_coverage["items"]:
            self.assertEqual(body[item["claim_start"]:item["claim_end"]], item["claim_text"])

    def test_no_disclosure_is_unavailable_not_passed(self):
        review = review_document(coverage_document([]))
        self.assertEqual(review.claim_disclosure_coverage["status"], "unavailable")
        issues = [i for i in review.issues if i.rule_id == "CLM019"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "info")

    def test_missing_claim_one_is_unavailable_not_inferred_from_claim_two(self):
        review = review_document(coverage_document(
            ["一種測試裝置，包含一基座。"], claims=[
                "【請求項2】一種測試裝置，包含一基座。",
            ],
        ))
        self.assertEqual(review.claim_disclosure_coverage["status"], "unavailable")
        self.assertFalse([i for i in review.issues if i.rule_id == "CLM019"])

    def test_evidence_is_restricted_to_original_disclosure_paragraphs(self):
        document = coverage_document(["一種測試裝置，包含一基座及一外殼。"])
        before = document.to_dict()
        report = review_document(document).claim_disclosure_coverage
        paragraph_map = {p.index: p for p in document.paragraphs}
        for item in report["items"]:
            for evidence in item["evidence"]:
                paragraph = paragraph_map[evidence["paragraph_index"]]
                self.assertEqual(paragraph.section_key, "disclosure")
                self.assertEqual(
                    paragraph.text[evidence["char_start"]:evidence["char_end"]],
                    evidence["text"],
                )
        self.assertEqual(document.to_dict(), before)

    def test_issue_source_span_points_to_later_word_paragraph_not_claim_heading(self):
        document = coverage_document(
            ["一種測試裝置，該基座具有三開口。"], claims=[
                "【請求項1】一種測試裝置，", "該基座具有二開口。",
            ],
        )
        issues = [
            issue for issue in review_document(document).issues
            if issue.rule_id == "CLM019" and issue.details["coverage_status"] == "conflict"
        ]
        self.assertEqual(len(issues), 1)
        source = next(p for p in document.paragraphs if p.text == "該基座具有二開口。")
        self.assertEqual(issues[0].paragraph_index, source.index)
        self.assertEqual(source.text[issues[0].char_start:issues[0].char_end], "該基座具有二開口")

    def test_unparsed_fragments_are_grouped_without_discarding_report_items(self):
        document = coverage_document(
            ["一種測試裝置。"], claims=[
                "【請求項1】一種測試裝置，該外殼由未知材料製成，該基座滿足Ω準則。",
            ],
        )
        review = review_document(document)
        infos = [i for i in review.issues if i.rule_id == "CLM019" and i.severity == "info"]
        self.assertEqual(len(infos), 1)
        fragments = infos[0].details["coverage_items"]
        self.assertEqual(len(fragments), 2)
        self.assertEqual(review.claim_disclosure_coverage["counts"]["uncertain"], 2)
        self.assertEqual([item["claim_text"] for item in fragments], ["該外殼由未知材料製成", "該基座滿足Ω準則"])


if __name__ == "__main__":
    unittest.main()
