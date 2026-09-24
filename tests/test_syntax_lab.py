"""Safety and grammar-variation regressions for an opt-in retrieval preview."""
import json
import os
from threading import Event
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from features.patent_review.models import PatentDocument, PatentParagraph
from features.patent_review.syntax_lab import analyze_syntax_lab, render_syntax_lab
from features.patent_review.rule_engine import review_document
from test_claim_coverage_integration import coverage_document


def report(disclosure, claims=None, embodiments=None):
    return analyze_syntax_lab(coverage_document(disclosure, claims, embodiments))


class SyntaxLabTests(unittest.TestCase):
    def test_exact_is_candidate_never_covered(self):
        result = report(["一種測試裝置，包含一基座及一外殼。"])
        self.assertEqual(result["status"], "candidates_only")
        self.assertTrue(any(r["status"] == "literal_candidate" for r in result["rows"]))
        self.assertNotIn("covered", json.dumps(result))

    def test_all_independent_targets_but_not_dependents_go_to_contents(self):
        result = report(["一種測試裝置，包含一基座。"], [
            "【請求項1】一種測試裝置，包含一基座。",
            "【請求項2】如請求項1所述的測試裝置，其中該基座具有二開口。",
            "【請求項3】一種測試系統，包含二如請求項1或2所述的測試裝置。",
        ])
        self.assertEqual({r["claim_number"] for r in result["rows"] if r["section"] == "disclosure"}, {1, 3})
        self.assertEqual({r["claim_number"] for r in result["rows"] if r["section"] == "embodiments"}, {1, 2, 3})
        self.assertIn("如請求項1或2", "".join(r["claim"]["text"] for r in result["rows"] if r["claim_number"] == 3))

    def test_dependent_header_not_treated_as_feature(self):
        result = report(["一種測試裝置。"], ["【請求項2】如請求項1所述的測試裝置，其中該基座具有二開口。"])
        self.assertTrue(result["rows"])
        self.assertTrue(all("如請求項" not in r["claim"]["text"] for r in result["rows"]))

    def test_multiline_and_inline_numbers(self):
        result = report(["一種測試裝置。"], [
            "【請求項1】一種測試裝置，包含：", "一基座；及一外殼。",
            "【請求項2】如請求項1所述的測試裝置，該基座具有二開口。請求項3：一種測試系統，包含一基座。",
        ])
        self.assertEqual([c["number"] for c in result["claims"]], [1, 2, 3])
        self.assertIn("一外殼", "".join(r["claim"]["text"] for r in result["rows"]))
        self.assertNotIn("包含", [r["claim"]["text"] for r in result["rows"]])

    def test_unknown_and_duplicate_not_silent(self):
        result = report(["一種測試裝置。"], [
            "【請求項1】未知格式，基座連接外殼。",
            "【請求項1】一種測試裝置，包含一基座。",
        ])
        self.assertTrue(any("項首類型不明" in w for w in result["warnings"]))
        self.assertTrue(any("重複" in w for w in result["warnings"]))
        self.assertEqual(len({c["claim_id"] for c in result["claims"]}), 2)

    def test_numeric_word_auto_number(self):
        doc = coverage_document(["一種測試裝置。"])
        p = next(p for p in doc.paragraphs if p.numbering_text.startswith("【請求項"))
        p.numbering_text = "１."
        self.assertEqual(analyze_syntax_lab(doc)["claims"][0]["number"], 1)

    def test_no_number_no_claim_one_inference(self):
        doc = coverage_document(["一種測試裝置。"])
        for p in doc.paragraphs:
            if p.section_key == "claims" and not p.is_heading:
                p.numbering_text = ""
        result = analyze_syntax_lab(doc)
        self.assertEqual(result["claims"], [])
        self.assertTrue(result["warnings"])

    def test_exact_offsets_and_section_boundaries(self):
        doc = coverage_document(["該基座具有二開口。"], embodiments=["該外殼連接該基座。"])
        before = doc.to_dict()
        result = analyze_syntax_lab(doc)
        paragraph_map = {p.index: p for p in doc.paragraphs}
        for r in result["rows"]:
            for a in [r["claim"]] + r["evidence"]:
                p = paragraph_map[a["paragraph_index"]]
                self.assertEqual(p.text[a["char_start"]:a["char_end"]], a["text"])
            self.assertTrue(all(paragraph_map[e["paragraph_index"]].section_key == r["section"] for e in r["evidence"]))
        self.assertEqual(doc.to_dict(), before)

    def test_missing_section_not_passed(self):
        result = report([])
        self.assertTrue(any("未擷取" in w for w in result["warnings"]))
        self.assertTrue(all(not r["evidence"] for r in result["rows"] if r["section"] == "disclosure"))

    def test_relative_clause_and_numbered_component_are_candidates(self):
        result = report(["一種測試裝置，包含一設置於該基座1的外殼2。"], [
            "【請求項1】一種測試裝置，包含一外殼，該外殼設置於該基座。",
        ])
        rows = [r for r in result["rows"] if "設置於" in r["claim"]["text"] and r["section"] == "disclosure"]
        self.assertTrue(rows[0]["evidence"])
        self.assertEqual(rows[0]["status"], "rewrite_candidate")
        self.assertIn("基座1", rows[0]["evidence"][0]["text"])

    def test_workflow_variant_keeps_conditions(self):
        result = report(["在步驟S01中，當開關啟動時，處理單元接收影像。"], [
            "【請求項1】一種方法，當開關啟動時處理單元接收影像。",
        ])
        rows = [r for r in result["rows"] if "當" in r["claim"]["text"]]
        self.assertTrue(any("condition" in r["checks"] and r["evidence"] for r in rows))

    def test_protected_changes_are_not_literal_matches(self):
        variants = [
            ("該基座連接該外殼", "該基座未連接該外殼", "logic"),
            ("該基座具有二開口", "該基座具有三開口", "quantity"),
            ("該基座位於該外殼上方", "該基座位於該外殼下方", "direction"),
            ("該長度D大於10mm", "該長度d大於10mm", "numeric"),
            ("該長度D大於10mm", "該長度D等於10mm", "numeric"),
            ("該第一隔板連接該基座", "該第二隔板連接該基座", "direction"),
            ("該基座具有C6撥水層", "該基座具有C8撥水層", "numeric"),
        ]
        for left, right, flag in variants:
            with self.subTest(left=left):
                result = report([right + "。"], ["【請求項1】一種測試裝置，" + left + "。"])
                row = next(r for r in result["rows"] if r["section"] == "disclosure" and r["claim"]["text"] == left)
                self.assertNotEqual(row["status"], "literal_candidate")
                self.assertIn(flag, row["checks"])

    def test_multiple_variants_not_aggregated(self):
        result = report(["第一實施例，該基座具有二開口。", "另一實施態樣，該基座具有三開口。"], [
            "【請求項1】一種測試裝置，該基座具有二開口，該基座具有三開口。",
        ])
        groups = {e["group"] for r in result["rows"] for e in r["evidence"]}
        self.assertGreater(len(groups), 1)
        self.assertNotIn("coverage", result)
        self.assertEqual(result["status"], "candidates_only")

    def test_prior_art_inside_embodiments_is_guarded(self):
        result = report(["一種測試裝置。"], ["【請求項1】一種測試裝置，該基座具有二開口。"],
                        ["習知裝置中，該基座具有二開口。"])
        row = next(r for r in result["rows"] if r["section"] == "embodiments" and "二開口" in r["claim"]["text"])
        self.assertIn("prior_art", row["checks"])

    def test_image_or_table_source_requires_review(self):
        for mode in ("image", "table"):
            with self.subTest(mode=mode):
                doc = coverage_document(["一種測試裝置，包含一基座及一外殼。"])
                p = next(p for p in doc.paragraphs if p.section_key == "disclosure" and not p.is_heading)
                if mode == "image":
                    p.image_relationship_ids = ["rId9"]
                else:
                    p.source_kind = "table"
                self.assertTrue(any(r["status"] == "source_review" for r in analyze_syntax_lab(doc)["rows"]))

    def test_formula_commas_remain_inside_parentheses(self):
        result = report(["該座標P(x,y)=(u*cos(t),u*sin(t))+Q(x,y)。"],
                        ["【請求項1】一種方法，該座標P(x,y)=(u*cos(t),u*sin(t))+Q(x,y)。"])
        row = next(r for r in result["rows"] if "P(" in r["claim"]["text"])
        self.assertIn("Q(x,y)", row["claim"]["text"])
        self.assertIn("formula", row["checks"])

    def test_local_corresponding_singular_has_no_quantity_verdict(self):
        result = report(["各該鏡腳具有相應的該側壁。"], [
            "【請求項1】一種裝置，包含二鏡腳，各該鏡腳具有相應的該側壁。",
        ])
        self.assertTrue(any("quantity" in r["checks"] for r in result["rows"]))
        self.assertNotIn("error", result)

    def test_cancellation_is_not_completion(self):
        stop = Event()
        stop.set()
        result = analyze_syntax_lab(coverage_document(["一種測試裝置。"]), cancel=stop)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["rows"], [])

    def test_large_input_explicitly_limited(self):
        doc = coverage_document(["一種測試裝置。"], ["【請求項1】一種測試裝置，" + "該基座具有開口，" * 1501])
        result = analyze_syntax_lab(doc)
        self.assertEqual(result["status"], "limited")
        self.assertTrue(result["warnings"])

    def test_html_is_escaped(self):
        result = report(["<img src='http://bad'>該基座具有二開口。"],
                        ["【請求項1】一種測試裝置，<b>該基座具有二開口</b>。"])
        html = render_syntax_lab(result)
        self.assertNotIn("<img", html)
        self.assertNotIn("<b>該基座", html)
        self.assertIn("&lt;", html)

    def test_json_roundtrip_and_filters(self):
        result = json.loads(json.dumps(report(["一種測試裝置。"]), ensure_ascii=False))
        html = render_syntax_lab(result, claim_id=0, section="embodiments")
        self.assertIn("→ 實施方式", html)
        self.assertNotIn("→ 內容", html)

    def test_production_review_unchanged(self):
        doc = coverage_document(["一種測試裝置，包含一基座及一外殼。"])
        first = review_document(doc).to_dict()
        analyze_syntax_lab(doc)
        second = review_document(doc).to_dict()
        first.pop("generated_at_utc")
        second.pop("generated_at_utc")
        self.assertEqual(first, second)


class SyntaxLabDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_background_result_and_filter(self):
        from ui.syntax_lab_dialog import SyntaxLabDialog
        dialog = SyntaxLabDialog(coverage_document(["一種測試裝置，包含一基座及一外殼。"]))
        deadline = time.monotonic() + 10
        while dialog.report is None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIsNotNone(dialog.report)
        self.assertIn("→ 內容", dialog.viewer.toPlainText())
        dialog.section.setCurrentIndex(1)
        self.assertIn("→ 實施方式", dialog.viewer.toPlainText())
        dialog.reject()
        self.assertTrue(dialog.task._closed)
        dialog.deleteLater()

    def test_internal_page_entry_is_available_without_special_launch_or_auto_analysis(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from features.patent_review.custom_rules import CustomTextRuleStore, DocumentSimilarityWhitelistStore
        from ui.patent_review_page import PatentReviewPage
        with TemporaryDirectory() as folder:
            for enabled in ("", "1"):
                with patch.dict(os.environ, {"PATENT_MDS_SYNTAX_LAB": enabled}):
                    with patch("features.patent_review.syntax_lab.analyze_syntax_lab") as analyze:
                        page = PatentReviewPage(lambda: None,
                            custom_rule_store=CustomTextRuleStore(Path(folder) / "rules.json"),
                            document_whitelist_store=DocumentSimilarityWhitelistStore(Path(folder) / "whitelist.json"))
                        self.assertFalse(page.syntax_lab_button.isHidden())
                        analyze.assert_not_called()
                        page.shutdown()
                        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
