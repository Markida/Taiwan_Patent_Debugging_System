import json
import unittest
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECOND_STAGE = PROJECT_ROOT / "training" / "second_stage"


class SecondStageTrainingPrepTests(unittest.TestCase):
    @unittest.skipUnless((SECOND_STAGE / "supervised_locator_v2_dataset/split_manifest.json").is_file(), "Private supervised dataset is not included in the public repository")
    def test_supervised_split_is_gold_only_patent_grouped_and_disjoint(self):
        root = SECOND_STAGE / "supervised_locator_v2_dataset"
        manifest = json.loads(
            (root / "split_manifest.json").read_text(encoding="utf-8")
        )

        self.assertTrue(manifest["complete"])
        self.assertTrue(manifest["ready_for_training"])
        self.assertEqual(manifest["train"]["pages"], 138)
        self.assertEqual(manifest["train"]["publications"], 32)
        self.assertEqual(manifest["holdout"]["pages"], 34)
        self.assertEqual(manifest["holdout"]["publications"], 8)
        self.assertEqual(manifest["holdout"]["complete_label_groups"], 502)
        self.assertFalse(manifest["leakage_checks"]["publication_cross_split"])
        self.assertEqual(manifest["leakage_checks"]["exact_image_duplicates"], 0)
        self.assertFalse(manifest["leakage_checks"]["pseudo_labels_used"])

        pages = manifest["pages"]
        self.assertEqual(len(pages), 172)
        self.assertEqual(len({page["image_sha256"] for page in pages}), 172)
        splits_by_publication = defaultdict(set)
        for page in pages:
            splits_by_publication[page["publication_number"]].add(page["split"])
        self.assertTrue(
            all(len(splits) == 1 for splits in splits_by_publication.values())
        )

    @unittest.skipUnless((SECOND_STAGE / "sealed_holdout_gold_v2/qa_report.json").is_file(), "Private sealed holdout is not included in the public repository")
    def test_sealed_holdout_is_complete_and_has_no_training_overlap(self):
        root = SECOND_STAGE / "sealed_holdout_gold_v2"
        qa = json.loads((root / "qa_report.json").read_text(encoding="utf-8"))

        self.assertTrue(qa["evaluation_only"])
        self.assertTrue(qa["complete"])
        self.assertTrue(qa["ready_for_threshold_sweep"])
        self.assertEqual(qa["frozen_pages"], 34)
        self.assertEqual(qa["publications"], 8)
        self.assertEqual(qa["reviewed_pages"], 34)
        self.assertEqual(qa["remaining_pages"], 0)
        self.assertEqual(qa["exact_training_image_overlaps"], [])

    @unittest.skipUnless((SECOND_STAGE / "rare_character_holdout_v2/manifest.json").is_file(), "Private rare-character holdout is not included in the public repository")
    def test_rare_challenge_uses_sealed_holdout_only(self):
        root = SECOND_STAGE / "rare_character_holdout_v2"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        split = json.loads(
            (SECOND_STAGE / "supervised_locator_v2_dataset" / "split_manifest.json")
            .read_text(encoding="utf-8")
        )

        self.assertTrue(manifest["evaluation_only"])
        self.assertTrue(manifest["training_prohibited"])
        self.assertEqual(manifest["holdout_pages"], 34)
        self.assertEqual(
            manifest["challenge_crops"],
            len(list((root / "crops").glob("*.png"))),
        )
        self.assertGreaterEqual(manifest["category_counts"]["uppercase"], 1)
        self.assertGreaterEqual(manifest["category_counts"]["lowercase"], 1)
        self.assertGreaterEqual(manifest["category_counts"]["prime"], 1)
        self.assertTrue(
            {item["page_id"] for item in manifest["items"]}
            <= set(split["holdout_page_ids"])
        )

    @unittest.skipUnless((SECOND_STAGE / "ocr_recognizer_v2_dataset/manifest.json").is_file(), "Private recognizer dataset is not included in the public repository")
    def test_ocr_recognizer_dataset_is_gold_only_and_patent_disjoint(self):
        root = SECOND_STAGE / "ocr_recognizer_v2_dataset"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

        self.assertTrue(manifest["complete"])
        self.assertTrue(manifest["gold_only"])
        self.assertFalse(manifest["pseudo_labels_used"])
        self.assertFalse(manifest["additional_manual_review_required"])
        self.assertEqual(manifest["train"]["crops"], 2106)
        self.assertEqual(manifest["val"]["crops"], 499)
        self.assertEqual(manifest["holdout"]["crops"], 502)
        self.assertFalse(manifest["leakage_checks"]["publication_cross_split"])
        self.assertFalse(
            manifest["leakage_checks"]["sealed_holdout_used_for_training"]
        )

        publications_by_split = defaultdict(set)
        for item in manifest["entries"]:
            publications_by_split[item["split"]].add(item["publication_number"])
        self.assertFalse(
            publications_by_split["train"] & publications_by_split["val"]
        )
        self.assertFalse(
            publications_by_split["train"] & publications_by_split["holdout"]
        )
        self.assertFalse(
            publications_by_split["val"] & publications_by_split["holdout"]
        )


if __name__ == "__main__":
    unittest.main()
