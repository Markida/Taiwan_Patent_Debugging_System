"""Alternative claim dependencies must preserve each antecedent path."""

import unittest

from features.patent_review.rule_engine import review_document
from test_patent_text_rules import VALID_LINES, build_document


ALIGNER_CLAIMS = [
    "一種整列機，適用於一輸送帶，該輸送帶用於沿一輸送方向輸送負數工件，該整列機包含："
    "一承載單元，沿一上下方向位於該輸送帶上方，並包括一適用以承接該等工件的基板，"
    "及二沿一左右方向設置於該基板相反兩側並用以導引該等工件的側板；及一分隔單元，"
    "設置於該基板，並與該基板及該等側板共同界定出負數沿該左右方向間隔設置的分隔道，"
    "該等分隔道在該左右方向上各自獨立，每一該工件通過其中一該分隔道後落至該輸送帶。",
    "如請求項1所述的整列機，其中，該基板具有沿該輸送方向相反設置的一入料端部及一出料端部，"
    "該入料端部在該上下方向與該輸送帶的距離大於該出料端部在該上下方向與該輸送帶的距離。",
    "如請求項2所述的整列機，其中，該分隔單元包括負數沿該左右方向間隔設置於該基板且位於該等側板間的第一隔板，"
    "每一該第一隔板具有一沿該輸送方向的兩相反端分別連接該入料端部及該出料端部的第一分隔部，"
    "即依自該第一分隔部的一端朝該輸送帶延伸的第一延伸部，該第一分隔部具有延伸方向成角度的第一前頂邊及一第一後頂邊，"
    "及一相反兩端分別連接於該第一前頂邊及該第一延伸部的轉折邊。",
    "如請求項3所述的整列機，其中，該分隔單元還包括複數沿該左右方向間隔設置於該基板的第二隔板，"
    "該等第二隔板穿插地設置於該等第一隔板與該等側板間，每一該第二隔板具有一連接該基板的第二分隔部，"
    "及一自該第二分隔部的一端朝該輸送帶延伸的第二延伸部，該第二延伸部具有延伸方向成角度的一第二前頂邊及一第二後頂邊。",
    "如請求項4所述的整列機，其中，該分隔單元還包括複數沿該左右方向間隔設置於該基板的第三隔板，"
    "每一該第三隔板位於相鄰的其中一該第一隔板與其中一該第二隔板間，且該第三隔板在該輸送方向上的長度小於"
    "每一該第一隔板及每一該第二隔板在該輸送方向上的長度，該基板、該等側板、該等第一隔板、該等第二隔板及該等第三隔板界定出該等分隔道。",
    "如請求項3至5中任一項所述的整列機，其中，該分隔單元還包括複數設置於該等第一隔板、該等第二隔板及該等第三隔板的圓棒，"
    "每一該圓棒覆蓋於該等第一分隔部、該等第二分隔部及該等第三隔板其中一者的頂側。",
]
ALIGNER_COMPONENTS = [
    "整列機", "輸送帶", "輸送方向", "工件", "承載單元", "上下方向", "基板", "左右方向", "側板",
    "分隔單元", "分隔道", "入料端部", "出料端部", "第一隔板", "第一分隔部", "第一延伸部",
    "第一前頂邊", "第一後頂邊", "轉折邊", "第二隔板", "第二分隔部", "第二延伸部", "第二前頂邊",
    "第二後頂邊", "第三隔板", "圓棒",
]


def claim_document(claims, components=(), title="測試裝置"):
    lines = [line.replace("測試裝置", title) for line in VALID_LINES[:-2]]
    symbol_insert = lines.index("20:分隔板") + 1
    lines[symbol_insert:symbol_insert] = [
        f"{number}:{name}" for number, name in enumerate(components, 30)
    ]
    lines.extend(f"【請求項{number}】{body}" for number, body in enumerate(claims, 1))
    return build_document(lines)


def component_issues(document, claim_number):
    paragraph = next(
        p for p in document.paragraphs
        if p.numbering_id == 20 and p.numbering_value == claim_number
    )
    return [
        issue for issue in review_document(document).issues
        if issue.rule_id in {"CLM012", "CLM016"}
        and issue.paragraph_index == paragraph.index
    ]


class ClaimDependencyPathTests(unittest.TestCase):
    def test_user_example_reports_the_four_missing_antecedent_paths(self):
        expected = {
            ("第二隔板", (3, 2, 1)),
            ("第三隔板", (3, 2, 1)),
            ("第三隔板", (4, 3, 2, 1)),
            ("第二分隔部", (3, 2, 1)),
        }
        for normalize_typo in (False, True):
            with self.subTest(normalize_typo=normalize_typo):
                claims = [
                    claim.replace("負數", "複數") if normalize_typo else claim
                    for claim in ALIGNER_CLAIMS
                ]
                document = claim_document(claims, ALIGNER_COMPONENTS, "整列機")
                issues = [
                    issue for issue in component_issues(document, 6)
                    if issue.rule_id == "CLM016"
                    and issue.details["component_name"] in {"第二隔板", "第三隔板", "第二分隔部"}
                ]
                self.assertEqual(len(issues), 4)
                self.assertEqual({
                    (issue.details["component_name"], tuple(issue.details["dependency_path"]))
                    for issue in issues
                }, expected)
                self.assertEqual(len({issue.issue_id for issue in issues}), 4)
                for issue in issues:
                    self.assertEqual(issue.severity, "error")
                    self.assertIn("依附路徑", issue.message)
                    self.assertTrue(all(
                        f"請求項{number}" in issue.message
                        for number in (6, *issue.details["dependency_path"])
                    ))
                    self.assertIn(issue.details["highlight_text"], claims[5])

    def test_common_ancestor_components_are_available_in_every_alternative(self):
        document = claim_document([
            "一種測試裝置，包含一主箱體。",
            "如請求項1所述的測試裝置，其中該主箱體可轉動。",
            "如請求項1或2所述的測試裝置，其中該主箱體可移動。",
        ])
        self.assertEqual(component_issues(document, 3), [])

    def test_user_example_depending_only_on_claim_five_has_all_three_components(self):
        claims = [claim.replace("負數", "複數") for claim in ALIGNER_CLAIMS]
        claims[5] = claims[5].replace("3至5中任一項", "5")
        issues = component_issues(claim_document(claims, ALIGNER_COMPONENTS, "整列機"), 6)
        self.assertEqual([
            issue for issue in issues
            if issue.details["component_name"] in {"第二隔板", "第三隔板", "第二分隔部"}
        ], [])

    def test_local_introduction_fixes_all_paths_only_after_it_occurs(self):
        for declaration_first in (True, False):
            with self.subTest(declaration_first=declaration_first):
                body = (
                    "包含一分隔板，該分隔板連接該主箱體。" if declaration_first
                    else "該分隔板連接該主箱體，並包含一分隔板。"
                )
                document = claim_document([
                    "一種測試裝置，包含一主箱體。",
                    "如請求項1所述的測試裝置，其中該主箱體可轉動。",
                    f"如請求項1或2所述的測試裝置，其中{body}",
                ])
                issues = component_issues(document, 3)
                self.assertEqual(len(issues), 0 if declaration_first else 2)

    def test_single_dependency_on_a_multiple_claim_preserves_the_branches(self):
        document = claim_document([
            "一種測試裝置，包含一主箱體。",
            "如請求項1所述的測試裝置，其中包含一分隔板。",
            "如請求項1或2所述的測試裝置，其中該主箱體可移動。",
            "如請求項3所述的測試裝置，其中該分隔板連接該主箱體。",
        ])
        issues = component_issues(document, 4)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "CLM016")
        self.assertEqual(issues[0].details["dependency_path"], [3, 1])

    def test_quantity_mismatch_is_not_masked_by_another_branch(self):
        document = claim_document([
            "一種測試裝置，包含一主箱體。",
            "如請求項1所述的測試裝置，其中包含一分隔板。",
            "如請求項1所述的測試裝置，其中包含複數分隔板。",
            "如請求項2或3所述的測試裝置，其中該等分隔板連接該主箱體。",
        ])
        issues = component_issues(document, 4)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "CLM012")
        self.assertEqual(issues[0].details["dependency_path"], [2, 1])

    def test_additive_member_is_inherited_only_on_the_path_with_an_antecedent(self):
        document = claim_document([
            "一種測試裝置，包含一主箱體。",
            "如請求項1所述的測試裝置，其中包含一分隔板。",
            "如請求項1所述的測試裝置，其中該主箱體可轉動。",
            "如請求項2或3所述的測試裝置，其中另一該分隔板連接該主箱體。",
            "如請求項4所述的測試裝置，其中該等分隔板設置於該主箱體。",
        ])
        for number, expected_path in ((4, [3, 1]), (5, [4, 3, 1])):
            with self.subTest(claim=number):
                issues = component_issues(document, number)
                self.assertEqual(len(issues), 1)
                self.assertEqual(issues[0].rule_id, "CLM016")
                self.assertEqual(issues[0].details["dependency_path"], expected_path)

    def test_invalid_dependencies_do_not_inherit_from_self_or_future_claims(self):
        document = claim_document([
            "一種測試裝置，包含一主箱體。",
            "如請求項2或3所述的測試裝置，其中該分隔板可轉動。",
            "如請求項1所述的測試裝置，其中包含一分隔板。",
        ])
        self.assertTrue(any(
            issue.rule_id == "CLM003" for issue in review_document(document).issues
        ))
        issues = component_issues(document, 2)
        self.assertEqual(len(issues), 2)
        self.assertTrue(all(issue.rule_id == "CLM016" for issue in issues))
        self.assertEqual(
            {tuple(issue.details["dependency_path"]) for issue in issues},
            {(2,), (3,)},
        )


if __name__ == "__main__":
    unittest.main()
