"""Branching advice must stay read-only and never imply human approval."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace as NS
import unittest
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QTableWidget
from features.workflow_pet.guidance import GuidanceMemory, conversion_scope, guidance_for


def issue(term="第一位置", section="embodiments", rule="REF005"):
    return NS(rule_id=rule, section_key=section, details={"candidate": term})


class BranchingGuidanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_repeated_suggestion_only_same_term_in_implementation_fuzzy_findings(self):
        for findings, repeated in (([issue()] * 3, True), ([issue()] * 2, False),
                ([issue(section="claims")] * 5, False), ([issue(rule="REF003")] * 5, False),
                ([issue("第一位置"), issue("第二位置"), issue("第三位置")], False)):
            page = NS(document=object(), review=NS(issues=findings))
            hint = guidance_for("patent_review", page)
            self.assertEqual(hint.key == "review-repeated", repeated)
            if repeated:
                self.assertIn("若你確認", hint.text)
                self.assertIn("有標號的元件勿", hint.text)
                self.assertEqual(hint.alternative.effect, "skip_repeated")

    def test_memory_resets_on_new_check_but_not_page_switch_or_poll(self):
        memory = GuidanceMemory()
        page = NS(document=object(), review=NS(issues=[issue()] * 4))
        guidance_for("patent_review", page, memory=memory)
        memory.skip_repeated = True
        memory.visit_issue(page, "one")
        memory.visit_issue(page, "one")
        for _ in range(3):
            hint = guidance_for("patent_review", page, memory=memory)
            self.assertEqual(hint.key, "review-progress")
        self.assertEqual(len(memory.visited), 1)
        self.assertIn("點閱不代表已修正", hint.text)
        page.review = NS(issues=[issue()] * 4)
        self.assertEqual(guidance_for("patent_review", page, memory=memory).key, "review-repeated")
        self.assertFalse(memory.visited)

    def test_findings_only_scanned_once_per_review(self):
        class CountingList(list):
            scans = 0
            def __iter__(self):
                self.scans += 1
                return super().__iter__()
        findings = CountingList([issue()] * 5000)
        page, memory = NS(document=object(), review=NS(issues=findings)), GuidanceMemory()
        for _ in range(100):
            guidance_for("patent_review", page, memory=memory)
        self.assertEqual(findings.scans, 1)

    def test_whitelist_edits_require_recheck_instead_of_recommending_duplicate_add(self):
        page = NS(document=object(), review=NS(issues=[issue()] * 4),
                  document_whitelist_terms=["第一位置"], _guidance_review_whitelist=())
        hint = guidance_for("patent_review", page)
        self.assertEqual(hint.key, "review-whitelist-pending")
        self.assertEqual(hint.target, "reload_button")
        self.assertEqual(hint.alternative.target, "issue_table")
        page._guidance_review_whitelist = tuple(page.document_whitelist_terms)
        page.review = NS(issues=[])
        self.assertEqual(guidance_for("patent_review", page).key, "review-ready")

    def test_unfinished_or_incomplete_review_never_reports_no_issues(self):
        page = NS(document=object(), review=None)
        self.assertEqual(guidance_for("patent_review", page).key, "review-pending")
        page.review = NS(issues=[])
        page.custom_rule_load_error = "denied"
        self.assertEqual(guidance_for("patent_review", page).key, "review-rules-unavailable")

    def test_loaded_reference_is_not_completed_comparison_and_edit_invalidates(self):
        editor = QPlainTextEdit("1:基座")
        table = QTableWidget(1, 1)
        page = NS(image_paths=["one.png"], all_results=[{}], reference_items=["1"],
                  reference_text=editor, review_table=table)
        self.assertEqual(guidance_for("patent_ocr", page).key, "ocr-compare-ready")
        page._guidance_comparison_snapshot = (page.all_results, editor.document().revision())
        hint = guidance_for("patent_ocr", page)
        self.assertEqual(hint.key, "ocr-compared")
        self.assertIn("不代表人工確認已完成", hint.text)
        editor.setPlainText("2:外殼")
        self.assertEqual(guidance_for("patent_ocr", page).key, "ocr-compare-ready")

    def test_changed_source_keeps_both_review_and_rerun_choices(self):
        page = NS(image_paths=["one.png"], all_results=[{}], _result_source_changed=True)
        hint = guidance_for("patent_ocr", page)
        self.assertEqual(hint.target, "review_table")
        self.assertEqual(hint.alternative.target, "auto_rotate_button")
        self.assertIn("舊結果", hint.text)

    def test_busy_states_do_not_offer_conflicting_actions(self):
        for feature, kwargs in (("patent_ocr", {"_ocr_job_active": True}),
                                ("patent_review", {"_review_tasks": NS(busy=True)}),
                                ("taiwan_china_spec", {"_conversion_running": True})):
            hint = guidance_for(feature, NS(**kwargs))
            self.assertFalse(hint.button)
            self.assertFalse(hint.alternative.button)

    def test_compare_distinguishes_wrong_reference_missing_page_and_ocr_omission(self):
        comparison = NS(referenced_figures=[], missing_figures=[], labels_not_in_drawings=[])
        page = NS(document=object(), ocr_results=[{}], _selected_comparison=lambda: comparison)
        self.assertEqual(guidance_for("embodiment_figure_compare", page).key, "compare-reference")
        comparison.referenced_figures = ["1"]
        comparison.missing_figures = ["1"]
        self.assertEqual(guidance_for("embodiment_figure_compare", page).key, "compare-missing-figure")
        comparison.missing_figures = []
        comparison.labels_not_in_drawings = ["12"]
        hint = guidance_for("embodiment_figure_compare", page)
        self.assertEqual(hint.key, "compare-missing-label")
        self.assertIn("不一定是原圖真的缺字", hint.text)
        self.assertEqual(hint.alternative.target, "paragraph_table")

    def test_conversion_prompts_preservation_or_review_not_blanket_deletion(self):
        hint = guidance_for("taiwan_china_spec", NS(_preview_source="sample.docx"))
        self.assertIn("不是全部都要刪除", hint.text)
        self.assertEqual(hint.target, "article_review")
        self.assertEqual(hint.alternative.target, "convert_button")

    def test_conversion_export_is_current_only_until_inputs_change(self):
        editor = QPlainTextEdit("人工修訂後的內文")
        page = NS(_preview_source="sample.docx", preview_output=editor, _dictionary_revision=1)
        page._guidance_conversion_snapshot = conversion_scope(page)
        self.assertEqual(guidance_for("taiwan_china_spec", page).key, "convert-exported")
        editor.setPlainText("再修訂")
        self.assertEqual(guidance_for("taiwan_china_spec", page).key, "convert-review")
        page._guidance_conversion_snapshot = conversion_scope(page)
        page._dictionary_revision += 1
        self.assertEqual(guidance_for("taiwan_china_spec", page).key, "convert-review")


if __name__ == "__main__":
    unittest.main()
