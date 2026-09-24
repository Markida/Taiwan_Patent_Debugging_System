import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from features.patent_ocr.image_io import write_image
from features.patent_ocr.figure_heading_classes import (
    FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
)
from training.figure_heading.build_text_layer_seed import _extract_line_pairs
from training.figure_heading.build_training_dataset import (
    _normalized_annotations,
    _validate_page,
    build_dataset,
)
from training.manual_annotation.common import (
    Annotation,
    make_page_record,
    normalize_label,
    write_page_record,
)


NO_RARE_IDENTIFIER_MINIMUMS = {
    "minimum_letter_pairs": 0,
    "minimum_prime_pairs": 0,
    "minimum_pure_alpha_pairs": 0,
    "minimum_validation_letter_pairs": 0,
    "minimum_test_letter_pairs": 0,
    "minimum_validation_prime_pairs": 0,
    "minimum_test_prime_pairs": 0,
    "minimum_validation_pure_alpha_pairs": 0,
    "minimum_test_pure_alpha_pairs": 0,
}


class FigureHeadingSeedTests(unittest.TestCase):
    def test_text_layer_seed_keeps_complete_identifier_in_one_box(self):
        line = {
            "spans": [{
                "chars": [
                    {"c": "圖", "bbox": [10, 20, 20, 30]},
                    {"c": " ", "bbox": [20, 20, 23, 30]},
                    {"c": "3", "bbox": [23, 20, 30, 30]},
                    {"c": "A", "bbox": [30, 20, 38, 30]},
                    {"c": "′", "bbox": [38, 20, 41, 30]},
                ]
            }]
        }
        pairs = _extract_line_pairs(line)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["figure_number"], "3A'")
        self.assertEqual(pairs[0]["raw_identifier_text"], "3A′")
        self.assertEqual(
            pairs[0]["identifier_pdf_box"],
            {"x1": 23.0, "y1": 20.0, "x2": 41.0, "y2": 30.0},
        )

    def test_general_figure_words_without_identifier_are_not_seeded(self):
        line = {
            "spans": [{
                "chars": [
                    {"c": "立", "bbox": [0, 0, 10, 10]},
                    {"c": "體", "bbox": [10, 0, 20, 10]},
                    {"c": "圖", "bbox": [20, 0, 30, 10]},
                ]
            }]
        }
        self.assertEqual(_extract_line_pairs(line), [])
        self.assertEqual(normalize_label("figure_prefix"), "figure_prefix")
        self.assertEqual(
            normalize_label("figure_identifier"),
            "figure_identifier",
        )
        self.assertEqual(
            normalize_label(FIGURE_PREFIX_ROTATE_RIGHT_CLASS),
            FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
        )

    def test_only_a_standalone_figure_heading_line_is_seeded(self):
        def make_line(text):
            return {
                "spans": [{
                    "chars": [
                        {
                            "c": character,
                            "bbox": [index * 10, 0, index * 10 + 9, 10],
                        }
                        for index, character in enumerate(text)
                    ]
                }]
            }

        self.assertEqual(
            _extract_line_pairs(make_line("  圖 3A′  "))[0]["figure_number"],
            "3A'",
        )
        for text in ("立體圖1", "前視圖2", "如圖3所示", "圖FIG.1"):
            with self.subTest(text=text):
                self.assertEqual(_extract_line_pairs(make_line(text)), [])

        normalized = _extract_line_pairs(make_line("圖００３ａ"))[0]
        self.assertEqual(normalized["figure_number"], "3A")
        self.assertEqual(normalized["raw_identifier_text"], "００３ａ")


class FigureHeadingDatasetTests(unittest.TestCase):
    def _make_annotation_set(
        self,
        root,
        reviewed=True,
        prefix_label="figure_prefix",
    ):
        image_path = root / "images" / "train" / "page.png"
        record_path = root / "labels" / "train" / "page.json"
        image_path.parent.mkdir(parents=True)
        record_path.parent.mkdir(parents=True)
        image = np.full((100, 160, 3), 255, dtype=np.uint8)
        cv2.putText(
            image,
            "1",
            (55, 65),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 0, 0),
            2,
        )
        self.assertTrue(write_image(image_path, image))
        record = make_page_record(
            page_id="page",
            split="train",
            image_path=image_path.relative_to(root),
            source_path="sample.pdf#page=1",
            image_width=160,
            image_height=100,
            annotations=[
                Annotation(
                    prefix_label,
                    20,
                    40,
                    45,
                    70,
                    source="manual",
                    pair_id="caption-001",
                ),
                Annotation(
                    "figure_identifier",
                    50,
                    43,
                    75,
                    68,
                    source="manual",
                    text="1",
                    pair_id="caption-001",
                ),
            ],
            reviewed=reviewed,
        )
        record["annotation_mode"] = "figure-heading"
        record["document_id"] = "document-1"
        write_page_record(record_path, record)

    def test_reviewed_pair_builds_all_four_orientations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "annotations"
            output = Path(temporary) / "yolo"
            self._make_annotation_set(root)
            report = build_dataset(
                root,
                output,
                minimum_pairs=1,
                minimum_negative_pages=0,
                minimum_validation_pairs=0,
                minimum_test_pairs=0,
                minimum_validation_negative_pages=0,
                minimum_test_negative_pages=0,
                minimum_validation_documents=0,
                minimum_test_documents=0,
                **NO_RARE_IDENTIFIER_MINIMUMS,
            )
            self.assertTrue(report["ready_for_training"])
            self.assertEqual(report["generated_rotated_images"], 4)
            self.assertEqual(
                len(list((output / "images" / "train").glob("*.png"))),
                4,
            )
            for label_path in (output / "labels" / "train").glob("*.txt"):
                labels = label_path.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(labels), 2)
                degrees = int(label_path.stem.rsplit("__r", 1)[1])
                expected_prefix = "2" if degrees == 270 else "0"
                self.assertEqual(
                    {line.split()[0] for line in labels},
                    {expected_prefix, "1"},
                )
            data_yaml = (output / "data.yaml").read_text(encoding="utf-8")
            self.assertIn("0: figure_prefix", data_yaml)
            self.assertIn("1: figure_identifier", data_yaml)
            self.assertIn("2: figure_prefix_rotate_right", data_yaml)
            evaluation = json.loads(
                (output / "evaluation_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(evaluation), 4)
            self.assertEqual(
                {
                    item["rotation_applied_clockwise"]: item[
                        "expected_correction_degrees"
                    ]
                    for item in evaluation
                },
                {0: 0, 90: 270, 180: 180, 270: 90},
            )
            self.assertTrue(
                all(item["expected_figure_numbers"] == ["1"] for item in evaluation)
            )

    def test_sideways_source_keeps_special_class_only_when_right_90_is_needed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "annotations"
            output = Path(temporary) / "yolo"
            self._make_annotation_set(
                root,
                prefix_label=FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
            )
            build_dataset(
                root,
                output,
                minimum_pairs=1,
                minimum_negative_pages=0,
                minimum_validation_pairs=0,
                minimum_test_pairs=0,
                minimum_validation_negative_pages=0,
                minimum_test_negative_pages=0,
                minimum_validation_documents=0,
                minimum_test_documents=0,
                **NO_RARE_IDENTIFIER_MINIMUMS,
            )
            expected_prefix_by_rotation = {0: "2", 90: "0", 180: "0", 270: "0"}
            for label_path in (output / "labels" / "train").glob("*.txt"):
                degrees = int(label_path.stem.rsplit("__r", 1)[1])
                classes = {
                    line.split()[0]
                    for line in label_path.read_text(encoding="utf-8").splitlines()
                }
                self.assertEqual(
                    classes,
                    {expected_prefix_by_rotation[degrees], "1"},
                )
            evaluation = json.loads(
                (output / "evaluation_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                {
                    item["rotation_applied_clockwise"]: item[
                        "expected_correction_degrees"
                    ]
                    for item in evaluation
                },
                {0: 90, 90: 0, 180: 270, 270: 180},
            )

    def test_unfinished_page_is_not_silently_used_as_negative(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "annotations"
            self._make_annotation_set(root, reviewed=False)
            with self.assertRaisesRegex(RuntimeError, "尚未人工確認"):
                build_dataset(
                    root,
                    Path(temporary) / "yolo",
                    minimum_pairs=1,
                    minimum_negative_pages=0,
                    minimum_validation_pairs=0,
                    minimum_test_pairs=0,
                    minimum_validation_negative_pages=0,
                    minimum_test_negative_pages=0,
                    minimum_validation_documents=0,
                    minimum_test_documents=0,
                    **NO_RARE_IDENTIFIER_MINIMUMS,
                )

    def test_reviewed_identifier_requires_text_ground_truth(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "annotations"
            self._make_annotation_set(root)
            record_path = root / "labels" / "train" / "page.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            for annotation in record["annotations"]:
                if annotation["label"] == "figure_identifier":
                    annotation["text"] = ""
            record_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "未輸入實際圖號文字"):
                build_dataset(
                    root,
                    Path(temporary) / "yolo",
                    minimum_pairs=1,
                    minimum_negative_pages=0,
                    minimum_validation_pairs=0,
                    minimum_test_pairs=0,
                    minimum_validation_negative_pages=0,
                    minimum_test_negative_pages=0,
                    minimum_validation_documents=0,
                    minimum_test_documents=0,
                    **NO_RARE_IDENTIFIER_MINIMUMS,
                )

    def test_numeric_only_dataset_cannot_claim_full_identifier_support(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "annotations"
            self._make_annotation_set(root)
            with self.assertRaisesRegex(RuntimeError, "含英文字母圖號"):
                build_dataset(
                    root,
                    Path(temporary) / "yolo",
                    minimum_pairs=1,
                    minimum_negative_pages=0,
                    minimum_validation_pairs=0,
                    minimum_test_pairs=0,
                    minimum_validation_negative_pages=0,
                    minimum_test_negative_pages=0,
                    minimum_validation_documents=0,
                    minimum_test_documents=0,
                )

    def test_invalid_or_tiny_reviewed_boxes_fail_closed(self):
        base = {
            "page_id": "broken-page",
            "width": 100,
            "height": 100,
            "annotations": [
                {
                    "label": "typo_label",
                    "x1": 10,
                    "y1": 10,
                    "x2": 20,
                    "y2": 20,
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "不屬於圖題模式"):
            _normalized_annotations(base)

        base["annotations"][0].update(
            label="figure_prefix",
            x2=11,
        )
        with self.assertRaisesRegex(ValueError, "框尺寸過小"):
            _normalized_annotations(base)

    def test_pair_id_cannot_be_shared_by_multiple_captions(self):
        annotations = [
            Annotation("figure_prefix", 10, 10, 20, 20, pair_id="caption-001"),
            Annotation(
                "figure_identifier",
                24,
                10,
                34,
                20,
                text="1",
                pair_id="caption-001",
            ),
            Annotation("figure_prefix", 10, 40, 20, 50, pair_id="caption-001"),
            Annotation(
                "figure_identifier",
                24,
                40,
                34,
                50,
                text="2",
                pair_id="caption-001",
            ),
        ]
        with self.assertRaisesRegex(ValueError, "不得重複共用"):
            _validate_page({"page_id": "duplicate-pair"}, annotations)


if __name__ == "__main__":
    unittest.main()
