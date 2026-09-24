import unittest

from features.patent_review.rule_engine import review_document
from test_patent_text_rules import VALID_LINES, build_document


class ClaimSubjectAntecedentTests(unittest.TestCase):
    TARGET = "螺旋鑽導引裝置"

    def review_claims(self, claims, *, title=None, symbols=()):
        title = self.TARGET if title is None else title
        lines = [
            line.replace("測試裝置", title)
            if line.startswith("【中文新型名稱】")
            else line
            for line in VALID_LINES
        ]
        symbol_start = lines.index("【符號說明】") + 1
        claims_start = lines.index("【新型申請專利範圍】")
        lines[symbol_start:claims_start] = [
            "符號說明如下。",
            "10:主箱體",
            "20:分隔板",
            *[f"{number}:{name}" for number, name in enumerate(symbols, 30)],
        ]
        claims_start = lines.index("【新型申請專利範圍】")
        lines[claims_start + 1:] = claims
        return review_document(build_document(lines))

    def quantity_issues(self, review):
        return [
            issue for issue in review.issues
            if issue.rule_id in {"CLM012", "CLM016"}
        ]

    def test_exact_application_preamble_and_word_paragraph_continuations(self):
        preamble = (
            "一種螺旋鑽導引裝置應用於一紅酒開瓶器，"
            "該紅酒開瓶器包含一螺旋鑽，該螺旋鑽導引裝置包括："
        )
        for register_target in (False, True):
            for split_paragraph in (False, True):
                with self.subTest(
                    register_target=register_target,
                    split_paragraph=split_paragraph,
                ):
                    symbols = ["紅酒開瓶器", "螺旋鑽"]
                    if register_target:
                        symbols.append(self.TARGET)
                    claims = [f"【請求項1】{preamble}"]
                    if split_paragraph:
                        claims.append("一主箱體。")
                    else:
                        claims[0] += "一主箱體。"
                    review = self.review_claims(claims, symbols=symbols)
                    self.assertEqual(review.claim_subjects, [self.TARGET])
                    self.assertEqual(self.quantity_issues(review), [])

    def test_punctuated_target_reference_does_not_refer_to_nested_component(self):
        for register_target in (False, True):
            with self.subTest(register_target=register_target):
                symbols = ["螺旋鑽"]
                if register_target:
                    symbols.append(self.TARGET)
                review = self.review_claims(
                    [
                        f"【請求項1】一種{self.TARGET}，"
                        f"該{self.TARGET}包括一主箱體。"
                    ],
                    symbols=symbols,
                )
                self.assertEqual(self.quantity_issues(review), [])

    def test_application_predicates_do_not_become_part_of_the_target(self):
        for predicate in ("應用於", "適用於", "用於", "係用於"):
            with self.subTest(predicate=predicate):
                review = self.review_claims(
                    [
                        f"【請求項1】一種{self.TARGET}{predicate}一紅酒開瓶器，"
                        f"該{self.TARGET}包含一主箱體，該紅酒開瓶器連接該主箱體。"
                    ],
                    symbols=[self.TARGET, "紅酒開瓶器", "螺旋鑽"],
                )
                self.assertEqual(review.claim_subjects, [self.TARGET])
                self.assertEqual(self.quantity_issues(review), [])

    def test_purpose_prefixed_title_remains_a_complete_target(self):
        target = "用於紅酒開瓶器的螺旋鑽導引裝置"
        for register_target in (False, True):
            with self.subTest(register_target=register_target):
                symbols = ["紅酒開瓶器", "螺旋鑽"]
                if register_target:
                    symbols.append(target)
                review = self.review_claims(
                    [f"【請求項1】一種{target}，該{target}包含一主箱體。"],
                    title=target,
                    symbols=symbols,
                )
                self.assertEqual(review.claim_subjects, [target])
                self.assertEqual(self.quantity_issues(review), [])

    def test_title_does_not_disclose_a_standalone_inner_component(self):
        review = self.review_claims(
            [
                f"【請求項1】一種{self.TARGET}，該{self.TARGET}包括一主箱體，"
                "該螺旋鑽設置於該主箱體。"
            ],
            symbols=["螺旋鑽"],
        )
        issues = self.quantity_issues(review)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "CLM016")
        self.assertEqual(issues[0].details.get("highlight_text"), "該螺旋鑽")

    def test_quantified_word_inside_title_does_not_disclose_child_to_dependents(self):
        target = "具一螺旋鑽的導引裝置"
        for dependent in (False, True):
            with self.subTest(dependent=dependent):
                if dependent:
                    claims = [
                        f"【請求項1】一種{target}，包含一主箱體。",
                        f"【請求項2】如請求項1所述的{target}，"
                        "其中該螺旋鑽設置於該主箱體。",
                    ]
                else:
                    claims = [
                        f"【請求項1】一種{target}，包含一主箱體，"
                        "該螺旋鑽設置於該主箱體。"
                    ]
                review = self.review_claims(
                    claims, title=target, symbols=["螺旋鑽"]
                )
                issues = self.quantity_issues(review)
                self.assertEqual(len(issues), 1)
                self.assertEqual(issues[0].rule_id, "CLM016")
                self.assertEqual(issues[0].details.get("component_name"), "螺旋鑽")

    def test_target_antecedent_is_inherited_through_a_dependency_chain(self):
        review = self.review_claims(
            [
                f"【請求項1】一種{self.TARGET}，包含一主箱體。",
                f"【請求項2】如請求項1所述的{self.TARGET}，"
                f"其中該{self.TARGET}具有一分隔板。",
                f"【請求項3】如請求項2所述的{self.TARGET}，"
                f"其中該{self.TARGET}的該分隔板連接該主箱體。",
            ],
            symbols=[self.TARGET, "螺旋鑽"],
        )
        self.assertEqual(self.quantity_issues(review), [])

    def test_other_independent_claim_cannot_inherit_target_antecedent(self):
        review = self.review_claims(
            [
                f"【請求項1】一種{self.TARGET}，包含一主箱體。",
                "【請求項2】一種定位裝置，包含一分隔板，"
                f"該{self.TARGET}連接該分隔板。",
            ],
            symbols=[self.TARGET, "定位裝置", "螺旋鑽"],
        )
        issues = self.quantity_issues(review)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "CLM016")
        self.assertEqual(issues[0].details.get("component_name"), self.TARGET)

    def test_actual_claim_target_can_differ_from_document_title(self):
        actual_target = "定位裝置"
        review = self.review_claims(
            [
                f"【請求項1】一種{actual_target}，該{actual_target}包含一主箱體，"
                f"該{self.TARGET}設置於該主箱體。"
            ],
            symbols=[self.TARGET, actual_target, "螺旋鑽"],
        )
        self.assertEqual(review.claim_subjects, [actual_target])
        issues = self.quantity_issues(review)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "CLM016")
        self.assertEqual(issues[0].details.get("component_name"), self.TARGET)

    def test_singular_target_still_rejects_plural_reference(self):
        review = self.review_claims(
            [
                f"【請求項1】一種{self.TARGET}，包含一主箱體，"
                f"該等{self.TARGET}連接該主箱體。"
            ],
            symbols=[self.TARGET, "螺旋鑽"],
        )
        issues = self.quantity_issues(review)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "CLM012")
        self.assertEqual(issues[0].details.get("available_kinds"), ["singular"])


if __name__ == "__main__":
    unittest.main()
