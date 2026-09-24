"""Offline claim-to-disclosure coverage examples and source-integrity contracts.

These examples deliberately distinguish a supported rewrite from a merely
similar description.  ``covered`` is not a legal support or validity opinion.
"""

import copy
import unittest

from features.patent_review.claim_coverage import analyze_claim_disclosure
from features.patent_review.models import PatentParagraph, TextRunSpan
from features.patent_review.text_normalizer import normalize_patent_text


COMPONENT_NAMES = (
    "基座", "外殼", "卡槽", "卡榫", "控制器", "感測器", "馬達", "轉軸",
    "支架", "導線", "第一構件", "第二構件", "電極A", "電極a", "電極A′",
    "電極A²", "電極A2", "接點",
)
SUBJECT_NAMES = ("固定裝置", "控制裝置", "導電裝置")


# Each tuple is (claim 1 body, separate original Word paragraphs).
# The documentation can reuse these compact, user-readable Chinese examples.
COVERED_CASES = {
    "exact_components_and_relation": (
        "一種固定裝置，包含一基座及一外殼，該基座連接該外殼。",
        ["一種固定裝置，包含一基座及一外殼，該基座連接該外殼。"],
    ),
    "includes_has_rewrite": (
        "該固定裝置包含一基座及一外殼。",
        ["該固定裝置具有一基座及一外殼。"],
    ),
    "includes_comprises_rewrite": (
        "該固定裝置包含一基座及一外殼。",
        ["該固定裝置包括一基座及一外殼。"],
    ),
    "said_demonstrative_rewrite": (
        "該基座連接該外殼。",
        ["所述基座連接所述外殼。"],
    ),
    "independent_clauses_reordered": (
        "該基座連接該外殼，該控制器連接該感測器。",
        ["該控制器連接該感測器，該基座連接該外殼。"],
    ),
    "clauses_split_into_sentences": (
        "該基座連接該外殼，該控制器連接該感測器。",
        ["該基座連接該外殼。該控制器連接該感測器。"],
    ),
    "clauses_split_across_word_paragraphs": (
        "該基座連接該外殼，該控制器連接該感測器。",
        ["該基座連接該外殼。", "該控制器連接該感測器。"],
    ),
    "passive_connection_preserves_roles": (
        "該基座被該外殼連接。",
        ["該外殼連接該基座。"],
    ),
    "chinese_two_variants": (
        "該基座具有二卡槽。",
        ["該基座具有兩卡槽。"],
    ),
    "added_explanation_keeps_relation": (
        "該基座連接該外殼。",
        ["該基座連接該外殼，該外殼為金屬製成。"],
    ),
    "additional_independent_component": (
        "該固定裝置包含一基座。",
        ["該固定裝置包含一基座，該固定裝置包含一控制器。"],
    ),
    "verbatim_material_limitation": (
        "該外殼由聚醚醚酮製成。",
        ["該外殼由聚醚醚酮製成。"],
    ),
    "nested_detachable_connection_expanded": (
        "一種固定裝置，包含一基座及一可拆卸地連接於該基座的外殼。",
        [
            "一種固定裝置，包括一基座及一外殼。"
            "該外殼以可拆卸方式與該基座連接。"
        ],
    ),
    "nested_direction_limitation_expanded": (
        "一種固定裝置，包含一沿一頂底方向設置的基座。",
        ["一種固定裝置，包含一基座。該基座沿一頂底方向設置。"],
    ),
    "detachable_source_covers_base_connection": (
        "該基座連接該外殼。",
        ["該基座可拆卸地連接該外殼。"],
    ),
    "passive_support_preserves_roles": (
        "該基座由該外殼支撐。",
        ["該外殼支撐該基座。"],
    ),
    "discourse_opening_of_verbatim_unknown_clause": (
        "其中該控制器自該感測器接收複數筆訊號。",
        ["該控制器自該感測器接收複數筆訊號。"],
    ),
}


REVIEW_CASES = {
    "negated_relation": (
        "該基座連接該外殼。",
        ["該基座未連接該外殼。"],
    ),
    "negation_scopes_over_literal_positive_clause": (
        "該基座連接該外殼。",
        ["並非該基座連接該外殼。"],
    ),
    "two_is_not_three": (
        "該基座具有二卡槽。",
        ["該基座具有三卡槽。"],
    ),
    "at_least_is_not_exact_quantity": (
        "該基座具有至少二卡槽。",
        ["該基座具有二卡槽。"],
    ),
    "exact_is_not_at_least_quantity": (
        "該基座具有二卡槽。",
        ["該基座具有至少二卡槽。"],
    ),
    "opposite_direction": (
        "該基座位於該外殼上方。",
        ["該基座位於該外殼下方。"],
    ),
    "reversed_owner": (
        "該基座具有一卡槽。",
        ["該卡槽具有一基座。"],
    ),
    "same_nouns_missing_relation": (
        "該基座連接該外殼。",
        ["該固定裝置包含一基座及一外殼。"],
    ),
    "or_does_not_cover_and": (
        "該固定裝置包含一基座及一外殼。",
        ["該固定裝置包含一基座或一外殼。"],
    ),
    "and_does_not_silently_cover_or": (
        "該固定裝置包含一基座或一外殼。",
        ["該固定裝置包含一基座及一外殼。"],
    ),
    "mutually_exclusive_embodiments_not_combined": (
        "該基座連接該外殼，該控制器連接該感測器。",
        [
            "在第一實施例中，該基座連接該外殼。",
            "在另一互斥的實施例中，該控制器連接該感測器。",
        ],
    ),
    "unlisted_material_must_not_disappear": (
        "該基座連接該外殼，該外殼由聚醚醚酮製成。",
        ["該基座連接該外殼。"],
    ),
    "unlisted_numeric_value_must_not_disappear": (
        "該基座連接該外殼，該外殼的厚度為0.8毫米。",
        ["該基座連接該外殼。"],
    ),
    "unlisted_function_must_not_disappear": (
        "該控制器連接該感測器，該控制器用於偵測過熱並切斷電源。",
        ["該控制器連接該感測器。"],
    ),
    "technical_identifier_case_is_significant": (
        "該電極A連接該接點。",
        ["該電極a連接該接點。"],
    ),
    "technical_identifier_prime_is_significant": (
        "該電極A′連接該接點。",
        ["該電極A連接該接點。"],
    ),
    "technical_identifier_superscript_is_significant": (
        "該電極A²連接該接點。",
        ["該電極A2連接該接點。"],
    ),
    "unparsed_residual_must_not_disappear": (
        "該基座連接該外殼，且滿足未定義的Ω準則。",
        ["該基座連接該外殼。"],
    ),
    "condition_must_not_disappear": (
        "僅當溫度超過80℃時，該控制器連接該感測器。",
        ["該控制器連接該感測器。"],
    ),
    "conditional_disclosure_does_not_cover_unconditional_claim": (
        "該控制器連接該感測器。",
        ["僅當溫度超過80℃時，該控制器連接該感測器。"],
    ),
    "nested_material_modifier_must_not_disappear": (
        "該基座連接由聚醚醚酮製成的該外殼。",
        ["該基座連接該外殼。"],
    ),
    "respectively_wrong_pairing": (
        "該第一構件及該第二構件分別連接該基座及該外殼。",
        ["該第一構件連接該外殼，該第二構件連接該基座。"],
    ),
    "detachable_modifier_must_not_disappear": (
        "一種固定裝置，包含一基座及一可拆卸地連接於該基座的外殼。",
        ["一種固定裝置，包括一基座及一外殼。該外殼連接該基座。"],
    ),
    "nested_direction_must_not_disappear": (
        "一種固定裝置，包含一沿一頂底方向設置的基座。",
        ["一種固定裝置，包含一基座。"],
    ),
    "nested_direction_quantity_must_not_change": (
        "一種固定裝置，包含一沿一頂底方向設置的基座。",
        ["一種固定裝置，包含一基座。該基座沿二頂底方向設置。"],
    ),
    "same_group_exact_and_conflicting_quantity": (
        "該基座具有一卡槽。",
        ["該基座具有一卡槽。該基座具有三卡槽。"],
    ),
    "same_group_exact_and_conflicting_direction": (
        "該基座位於該外殼上方。",
        ["該基座位於該外殼上方。該基座位於該外殼下方。"],
    ),
    "same_group_exact_and_negated_connection": (
        "該基座連接該外殼。",
        ["該基座連接該外殼。該基座未連接該外殼。"],
    ),
    "same_group_negated_claim_and_positive_connection": (
        "該基座未連接該外殼。",
        ["該基座連接該外殼。該基座未連接該外殼。"],
    ),
    "base_connection_does_not_cover_detachable_claim": (
        "該基座可拆卸地連接該外殼。",
        ["該基座連接該外殼。"],
    ),
}


def make_paragraph(text, index):
    return PatentParagraph(
        index=index,
        text=text,
        normalized_text=normalize_patent_text(text),
        content_text=normalize_patent_text(text),
        source_path=f"body/p[{index}]",
        section_key="disclosure",
        run_spans=[TextRunSpan(run_index=0, start=0, end=len(text), text=text)],
    )


def analyze_example(claim, disclosure):
    paragraphs = [make_paragraph(text, 10 + index * 3) for index, text in enumerate(disclosure)]
    result = analyze_claim_disclosure(
        claim,
        paragraphs,
        component_names=COMPONENT_NAMES,
        subject_names=SUBJECT_NAMES,
    )
    return result, paragraphs


class ClaimDisclosureCoverageTests(unittest.TestCase):
    def assert_valid_source_spans(self, claim, paragraphs, result):
        sources = {paragraph.index: paragraph.text for paragraph in paragraphs}
        self.assertIn(result["status"], {"covered", "needs_review", "unavailable"})
        self.assertIsInstance(result["limitations"], list)
        self.assertIsInstance(result["counts"], dict)
        self.assertEqual(
            result["counts"],
            {
                status: sum(item["status"] == status for item in result["items"])
                for status in ("covered", "possible_gap", "conflict", "uncertain")
            },
        )
        for item in result["items"]:
            self.assertIn(item["status"], {"covered", "possible_gap", "conflict", "uncertain"})
            start, end = item["claim_start"], item["claim_end"]
            self.assertIsInstance(start, int)
            self.assertIsInstance(end, int)
            self.assertTrue(0 <= start < end <= len(claim))
            self.assertEqual(claim[start:end], item["claim_text"])
            self.assertTrue(item["reason"])
            for evidence in item["evidence"]:
                source = sources[evidence["paragraph_index"]]
                start, end = evidence["char_start"], evidence["char_end"]
                self.assertTrue(0 <= start < end <= len(source))
                self.assertEqual(source[start:end], evidence["text"])
            if item["status"] == "covered":
                self.assertTrue(item["evidence"], "A covered item must cite actual disclosure text.")

    def test_empty_claim_is_unavailable(self):
        result, paragraphs = analyze_example("", ["該基座連接該外殼。"])
        self.assertEqual(result["status"], "unavailable")
        self.assert_valid_source_spans("", paragraphs, result)

    def test_empty_disclosure_is_unavailable(self):
        claim = "該基座連接該外殼。"
        result, paragraphs = analyze_example(claim, [])
        self.assertEqual(result["status"], "unavailable")
        self.assert_valid_source_spans(claim, paragraphs, result)

    def test_whitespace_only_disclosure_is_unavailable(self):
        claim = "該基座連接該外殼。"
        result, paragraphs = analyze_example(claim, [" \t\n　"])
        self.assertEqual(result["status"], "unavailable")
        self.assert_valid_source_spans(claim, paragraphs, result)

    def test_analysis_is_deterministic_and_never_mutates_source(self):
        claim = "  該電極A′連接該接點，該電極A²位於該基座上方。  "
        paragraphs = [
            make_paragraph("【0007】  該電極A′連接該接點。", 23),
            make_paragraph("\t該電極A²位於該基座上方。  ", 71),
        ]
        before = copy.deepcopy(paragraphs)
        first = analyze_claim_disclosure(
            claim, paragraphs, component_names=COMPONENT_NAMES, subject_names=SUBJECT_NAMES
        )
        second = analyze_claim_disclosure(
            claim, paragraphs, component_names=COMPONENT_NAMES, subject_names=SUBJECT_NAMES
        )
        self.assertEqual(first, second)
        self.assertEqual(paragraphs, before)
        self.assert_valid_source_spans(claim, paragraphs, first)

    def test_duplicate_disclosure_does_not_duplicate_claim_items(self):
        claim = "該基座連接該外殼。"
        single, _ = analyze_example(claim, [claim])
        repeated, paragraphs = analyze_example(claim, [claim, claim, claim])
        self.assertEqual(repeated["status"], "covered")
        self.assertEqual(len(repeated["items"]), len(single["items"]))
        self.assertEqual(repeated["counts"], single["counts"])
        self.assert_valid_source_spans(claim, paragraphs, repeated)

    def test_cross_paragraph_evidence_uses_original_noncontiguous_indices(self):
        claim = "該基座連接該外殼，該控制器連接該感測器。"
        result, paragraphs = analyze_example(
            claim, ["該基座連接該外殼。", "該控制器連接該感測器。"]
        )
        self.assertEqual(result["status"], "covered")
        self.assert_valid_source_spans(claim, paragraphs, result)
        evidence_indices = {
            evidence["paragraph_index"]
            for item in result["items"]
            for evidence in item["evidence"]
        }
        self.assertEqual(evidence_indices, {10, 13})

    def test_distinct_embodiments_inside_one_paragraph_are_not_combined(self):
        claim = "該基座連接該外殼，該控制器連接該感測器。"
        disclosure = [
            "在第一實施例中，該基座連接該外殼。"
            "在另一實施例中，該控制器連接該感測器。"
        ]
        result, paragraphs = analyze_example(claim, disclosure)
        self.assertEqual(result["status"], "needs_review", result)
        self.assert_valid_source_spans(claim, paragraphs, result)

    def test_excluded_or_erroneous_context_cannot_supply_positive_evidence(self):
        for claim in ("該基座連接該外殼。", "若開關閉合，該基座連接該外殼。"):
            for context in (
                "下述敘述不適用於本發明。",
                "以下是錯誤配置。",
                "當溫度超過80度時，",
            ):
                for disclosure in ([context + claim], [context, claim]):
                    with self.subTest(claim=claim, disclosure=disclosure):
                        result, paragraphs = analyze_example(claim, disclosure)
                        self.assertEqual(result["status"], "needs_review", result)
                        self.assert_valid_source_spans(claim, paragraphs, result)

    def test_manual_paragraph_numbers_preserve_original_evidence_offsets(self):
        claim = "該基座連接該外殼。"
        for prefix in ("【1】", "【01】"):
            with self.subTest(prefix=prefix):
                result, paragraphs = analyze_example(claim, [prefix + claim])
                self.assertEqual(result["status"], "covered", result)
                self.assert_valid_source_spans(claim, paragraphs, result)

    def test_later_or_clause_does_not_retroactively_block_covered_relation(self):
        claim = "該基座連接該外殼，該控制器連接該感測器或該馬達。"
        result, paragraphs = analyze_example(claim, ["該基座連接該外殼。"])
        self.assertEqual(result["status"], "needs_review", result)
        first_relation = next(
            item for item in result["items"] if item["claim_text"] == "該基座連接該外殼"
        )
        self.assertEqual(first_relation["status"], "covered", result)
        self.assert_valid_source_spans(claim, paragraphs, result)

    def test_large_input_is_unavailable_instead_of_an_implicit_pass(self):
        ordinary_claim = "該基座連接該外殼。"
        cases = (
            ("甲" * 100_001, [ordinary_claim], COMPONENT_NAMES),
            (ordinary_claim, ["甲" * 1_000_001], COMPONENT_NAMES),
            (ordinary_claim, [ordinary_claim], tuple(f"元件{index}" for index in range(4_001))),
        )
        for claim, texts, names in cases:
            with self.subTest(claim_chars=len(claim), source_chars=sum(map(len, texts)), names=len(names)):
                paragraphs = [make_paragraph(text, index) for index, text in enumerate(texts)]
                result = analyze_claim_disclosure(
                    claim, paragraphs, component_names=names, subject_names=SUBJECT_NAMES
                )
                self.assertEqual(result["status"], "unavailable", result)
                self.assertEqual(result["items"], [])
                self.assert_valid_source_spans(claim, paragraphs, result)

    def test_candidate_evidence_never_upgrades_missing_or_unknown_limitations(self):
        for claim, texts in (
            ("該基座連接該外殼。", ["該固定裝置包含一基座及一外殼。"]),
            ("該外殼由聚醚醚酮製成。", ["該外殼具有一卡槽。"]),
        ):
            with self.subTest(claim=claim):
                result, paragraphs = analyze_example(claim, texts)
                self.assertEqual(result["status"], "needs_review", result)
                candidates = [
                    (item, evidence)
                    for item in result["items"]
                    for evidence in item["evidence"]
                    if evidence.get("evidence_kind") == "candidate"
                ]
                self.assertTrue(candidates, result)
                for item, evidence in candidates:
                    self.assertIn(item["status"], {"possible_gap", "uncertain"})
                    self.assertIn("候選", item["reason"])
                    self.assertIsInstance(evidence["disclosure_group"], int)
                self.assert_valid_source_spans(claim, paragraphs, result)


def _covered_case_test(claim, disclosure):
    def test(self):
        result, paragraphs = analyze_example(claim, disclosure)
        self.assertEqual(result["status"], "covered", result)
        self.assertTrue(result["items"])
        self.assertTrue(all(item["status"] == "covered" for item in result["items"]))
        self.assert_valid_source_spans(claim, paragraphs, result)
    return test


def _review_case_test(claim, disclosure):
    def test(self):
        result, paragraphs = analyze_example(claim, disclosure)
        self.assertEqual(result["status"], "needs_review", result)
        self.assertTrue(result["items"])
        self.assertTrue(any(item["status"] != "covered" for item in result["items"]))
        self.assert_valid_source_spans(claim, paragraphs, result)
    return test


for _name, (_claim, _disclosure) in COVERED_CASES.items():
    setattr(ClaimDisclosureCoverageTests, f"test_covered_{_name}", _covered_case_test(_claim, _disclosure))

for _name, (_claim, _disclosure) in REVIEW_CASES.items():
    setattr(ClaimDisclosureCoverageTests, f"test_review_{_name}", _review_case_test(_claim, _disclosure))


if __name__ == "__main__":
    unittest.main()
