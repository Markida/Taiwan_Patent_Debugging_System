import unittest

from features.patent_ocr.reference_reconciliation import (
    levenshtein_distance,
    reconcile_label_to_reference,
)


class ReferenceReconciliationTests(unittest.TestCase):
    def test_distance_handles_insert_delete_and_substitution(self):
        self.assertEqual(levenshtein_distance("Ll", "L1"), 1)
        self.assertEqual(levenshtein_distance("1122", "122"), 1)
        self.assertEqual(levenshtein_distance("3L21", "321"), 1)

    def test_known_ocr_confusions_are_corrected_when_unique(self):
        self.assertEqual(reconcile_label_to_reference("Ll", ["L1"]), ("L1", True))
        self.assertEqual(reconcile_label_to_reference("Xl", ["X1"]), ("X1", True))
        self.assertEqual(reconcile_label_to_reference("61", ["6'"]), ("6'", True))

    def test_insertions_and_deletions_are_not_rewritten(self):
        self.assertEqual(reconcile_label_to_reference("1122", ["122"]), ("1122", False))
        self.assertEqual(reconcile_label_to_reference("311", ["3311"]), ("311", False))
        self.assertEqual(reconcile_label_to_reference("L", ["L1"]), ("L", False))

    def test_ambiguous_or_unknown_substitutions_are_not_changed(self):
        self.assertEqual(
            reconcile_label_to_reference("31", ["311", "331"]),
            ("31", False),
        )
        self.assertEqual(reconcile_label_to_reference("54", ["24"]), ("54", False))

    def test_existing_reference_and_case_sensitive_lowercase_are_preserved(self):
        self.assertEqual(reconcile_label_to_reference("a", ["a", "A"]), ("a", False))
        self.assertEqual(reconcile_label_to_reference("L1", ["L1"]), ("L1", False))


if __name__ == "__main__":
    unittest.main()
