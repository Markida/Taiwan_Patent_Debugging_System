import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

import cv2
import numpy as np

from features.patent_ocr.figure_heading import (
    _deduplicate_prefix_detections,
    _identifier_crop,
    apply_rotate_right_class_orientation,
    exclude_caption_identifiers_from_components,
    infer_page_orientation,
    is_figure_heading_model,
    pair_figure_heading_boxes,
    score_heading_geometry,
    transform_box_clockwise,
)
from features.patent_ocr.figure_identifiers import (
    figure_sort_key,
    normalize_figure_identifier,
    parse_figure_number_mapping,
)
from features.patent_ocr.image_io import read_image, write_image
from features.patent_ocr.image_tools import create_auto_oriented_image


def box(x1, y1, x2, y2, confidence=0.9, class_name=None):
    payload = {
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "confidence": confidence,
    }
    if class_name is not None:
        payload["class_name"] = class_name
    return payload


def vote(correction, quality=0.9, ocr=0.9, geometry=0.9):
    return {
        "accepted": True,
        "figure_number": 1,
        "correction_degrees": correction,
        "pair_confidence": quality,
        "ocr_confidence": ocr,
        "geometry_score": geometry,
    }


class FigureIdentifierTests(unittest.TestCase):
    def test_supports_numeric_alphabetic_and_prime_identifiers(self):
        self.assertEqual(normalize_figure_identifier("003a"), "3A")
        self.assertEqual(normalize_figure_identifier("1′"), "1'")
        self.assertEqual(normalize_figure_identifier("a’"), "A'")
        self.assertEqual(parse_figure_number_mapping("圖1、圖3A、圖1'、圖B"), [
            1,
            "3A",
            "1'",
            "B",
        ])

    def test_rejects_zero_bare_prime_and_repeated_prime(self):
        for value in ("0", "'", "1''", "A''", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_figure_identifier(value)

    def test_sort_orders_numeric_variants_before_letters(self):
        values = ["A", "2'", "2A", 2, 1, "B"]
        self.assertEqual(
            sorted(values, key=figure_sort_key),
            [1, 2, "2'", "2A", "A", "B"],
        )


class FigureHeadingGeometryTests(unittest.TestCase):
    def setUp(self):
        self.prefix = box(100, 100, 120, 120)

    def test_directed_pair_resolves_all_four_corrections(self):
        cases = [
            (box(124, 102, 144, 118), "right", 0),
            (box(102, 124, 118, 144), "down", 270),
            (box(76, 102, 96, 118), "left", 180),
            (box(102, 76, 118, 96), "up", 90),
        ]
        for identifier, direction, correction in cases:
            with self.subTest(direction=direction):
                result = score_heading_geometry(self.prefix, identifier)
                self.assertIsNotNone(result)
                self.assertEqual(result["direction"], direction)
                self.assertEqual(result["correction_degrees"], correction)
                self.assertGreaterEqual(result["geometry_score"], 0.80)

    def test_diagonal_and_distant_candidates_are_rejected(self):
        self.assertIsNone(
            score_heading_geometry(self.prefix, box(130, 130, 150, 150))
        )

    def test_overlapping_normal_and_sideways_prefix_keep_stronger_class(self):
        normal = box(
            100,
            100,
            120,
            120,
            0.91,
            "figure_prefix",
        )
        sideways = box(
            101,
            100,
            121,
            120,
            0.90,
            "figure_prefix_rotate_right",
        )
        self.assertEqual(
            _deduplicate_prefix_detections([sideways, normal]),
            [normal],
        )
        sideways["confidence"] = 0.95
        self.assertEqual(
            _deduplicate_prefix_detections([normal, sideways]),
            [sideways],
        )
        self.assertIsNone(
            score_heading_geometry(self.prefix, box(300, 102, 320, 118))
        )

    def test_pairing_uses_each_box_only_once(self):
        prefixes = [self.prefix, box(100, 200, 120, 220)]
        identifiers = [
            box(124, 102, 144, 118, 0.95),
            box(124, 202, 144, 218, 0.90),
            box(150, 102, 170, 118, 0.20),
        ]
        pairs = pair_figure_heading_boxes(prefixes, identifiers)
        self.assertEqual(len(pairs), 2)
        self.assertEqual({pair["prefix_index"] for pair in pairs}, {0, 1})
        self.assertEqual({pair["identifier_index"] for pair in pairs}, {0, 1})

    def test_pairing_maximizes_complete_pairs_before_individual_score(self):
        prefixes = [
            box(100, 100, 120, 120),
            box(124, 126, 144, 146),
        ]
        identifiers = [
            box(124, 102, 144, 118),
            box(102, 76, 118, 96),
        ]
        pairs = pair_figure_heading_boxes(prefixes, identifiers)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(
            {
                (pair["prefix_index"], pair["identifier_index"])
                for pair in pairs
            },
            {(0, 1), (1, 0)},
        )

    def test_sideways_prefix_class_forces_clockwise_90_correction(self):
        special_prefix = box(
            100,
            100,
            120,
            120,
            class_name="figure_prefix_rotate_right",
        )
        pair = pair_figure_heading_boxes(
            [special_prefix],
            [box(124, 102, 144, 118)],
        )[0]
        self.assertEqual(pair["geometry_correction_degrees"], 0)
        self.assertEqual(pair["correction_degrees"], 90)

    def test_sideways_prefix_rotates_identifier_crop_clockwise_before_ocr(self):
        special_prefix = box(
            1,
            1,
            3,
            3,
            class_name="figure_prefix_rotate_right",
        )
        identifier = box(4, 1, 6, 3)
        pair = pair_figure_heading_boxes(
            [special_prefix],
            [identifier],
        )[0]
        image = np.arange(32, dtype=np.uint8).reshape(4, 8)

        actual = _identifier_crop(
            image,
            pair["identifier_box"],
            pair["correction_degrees"],
        )
        padded_identifier = image[0:4, 2:8]

        np.testing.assert_array_equal(
            actual,
            cv2.rotate(padded_identifier, cv2.ROTATE_90_CLOCKWISE),
        )

    def test_model_contract_requires_all_three_dedicated_classes(self):
        valid = type(
            "Model",
            (),
            {
                "names": {
                    0: "figure_prefix",
                    1: "figure_identifier",
                    2: "figure_prefix_rotate_right",
                }
            },
        )()
        legacy_two_class = type(
            "Model",
            (),
            {"names": {0: "figure_prefix", 1: "figure_identifier"}},
        )()
        old_group = type("Model", (), {"names": {0: "patent_label"}})()
        reversed_roles = type(
            "Model",
            (),
            {
                "names": {
                    0: "figure_identifier",
                    1: "figure_prefix",
                    2: "figure_prefix_rotate_right",
                }
            },
        )()
        self.assertTrue(is_figure_heading_model(valid))
        self.assertFalse(is_figure_heading_model(legacy_two_class))
        self.assertFalse(is_figure_heading_model(old_group))
        self.assertFalse(is_figure_heading_model(reversed_roles))


class FigureOrientationTests(unittest.TestCase):
    def test_high_confidence_sideways_prefix_requests_clockwise_rotation(self):
        result = apply_rotate_right_class_orientation(
            infer_page_orientation([]),
            [box(1, 1, 10, 10, 0.90, "figure_prefix_rotate_right")],
        )
        self.assertEqual(result["status"], "needs_rotation")
        self.assertEqual(result["correction_degrees"], 90)

    def test_low_confidence_sideways_prefix_does_not_rotate(self):
        result = apply_rotate_right_class_orientation(
            infer_page_orientation([]),
            [box(1, 1, 10, 10, 0.69, "figure_prefix_rotate_right")],
        )
        self.assertEqual(result["status"], "no_evidence")
        self.assertIsNone(result["correction_degrees"])

    def test_sideways_prefix_conflicting_with_upright_evidence_is_safe(self):
        result = apply_rotate_right_class_orientation(
            infer_page_orientation([vote(0)]),
            [box(1, 1, 10, 10, 0.90, "figure_prefix_rotate_right")],
        )
        self.assertEqual(result["status"], "mixed_orientation")
        self.assertIsNone(result["correction_degrees"])

    def test_one_strong_caption_can_confirm_upright(self):
        result = infer_page_orientation([vote(0)])
        self.assertEqual(result["status"], "upright")
        self.assertEqual(result["correction_degrees"], 0)

    def test_one_weak_caption_remains_ambiguous(self):
        result = infer_page_orientation([vote(90, quality=0.70)])
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["correction_degrees"])

    def test_single_caption_orientation_uses_calibrated_safe_boundary(self):
        accepted = infer_page_orientation([vote(270, quality=0.74)])
        rejected = infer_page_orientation([vote(270, quality=0.739)])

        self.assertEqual(accepted["status"], "needs_rotation")
        self.assertEqual(accepted["correction_degrees"], 270)
        self.assertEqual(rejected["status"], "ambiguous")
        self.assertIsNone(rejected["correction_degrees"])

    def test_unrecognized_identifier_cannot_rotate_page(self):
        invalid = vote(90)
        invalid.update({"accepted": False, "figure_number": None})
        result = infer_page_orientation([invalid])
        self.assertEqual(result["status"], "no_evidence")
        self.assertIsNone(result["correction_degrees"])

    def test_two_consistent_captions_request_rotation(self):
        result = infer_page_orientation([
            vote(270, quality=0.70),
            vote(270, quality=0.72),
        ])
        self.assertEqual(result["status"], "needs_rotation")
        self.assertEqual(result["correction_degrees"], 270)

    def test_conflicting_strong_captions_do_not_rotate_page(self):
        result = infer_page_orientation([vote(90), vote(270)])
        self.assertEqual(result["status"], "mixed_orientation")
        self.assertIsNone(result["correction_degrees"])

    def test_weighted_majority_accepts_only_clear_winner(self):
        result = infer_page_orientation([
            vote(180, quality=0.85),
            vote(180, quality=0.82),
            vote(0, quality=0.50),
        ])
        self.assertEqual(result["status"], "needs_rotation")
        self.assertEqual(result["correction_degrees"], 180)


class FigureCaptionExclusionTests(unittest.TestCase):
    def test_box_transform_handles_all_right_angle_rotations(self):
        source = box(10, 20, 30, 40)
        self.assertEqual(
            transform_box_clockwise(source, 100, 200, 90),
            {"x1": 160.0, "y1": 10.0, "x2": 180.0, "y2": 30.0},
        )
        self.assertEqual(
            transform_box_clockwise(source, 100, 200, 180),
            {"x1": 70.0, "y1": 160.0, "x2": 90.0, "y2": 180.0},
        )
        self.assertEqual(
            transform_box_clockwise(source, 100, 200, 270),
            {"x1": 20.0, "y1": 70.0, "x2": 40.0, "y2": 90.0},
        )

    def test_caption_number_is_not_kept_as_component_label(self):
        result = {
            "detections": [
                {"label": "1", "x1": 124, "y1": 102, "x2": 144, "y2": 118},
                {"label": "10", "x1": 300, "y1": 300, "x2": 330, "y2": 320},
            ],
            "numbers": ["1", "10"],
            "labels": ["1", "10"],
            "number_count": 2,
            "result_text": "偵測到標號數量：2\n組合後標號列表：3A, 10",
        }
        analysis = {
            "figure_caption_detections": [{
                "accepted": True,
                "figure_number": 1,
                "identifier_box": box(124, 102, 144, 118),
            }]
        }
        removed = exclude_caption_identifiers_from_components(
            result,
            analysis,
            original_width=500,
            original_height=500,
            correction_degrees=0,
        )
        self.assertEqual(removed, 1)
        self.assertEqual(result["numbers"], ["10"])
        self.assertIn("偵測到標號數量：1", result["result_text"])
        self.assertIn("組合後標號列表：10", result["result_text"])
        self.assertEqual(
            result["figure_caption_component_detections"][0]["label"],
            "1",
        )

    def test_non_destructive_auto_orientation_supports_three_corrections(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            image = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
            self.assertTrue(write_image(source, image))
            codes = {
                90: cv2.ROTATE_90_CLOCKWISE,
                180: cv2.ROTATE_180,
                270: cv2.ROTATE_90_COUNTERCLOCKWISE,
            }
            with patch(
                "features.patent_ocr.image_tools.get_output_base_dir",
                return_value=root,
            ):
                for degrees, code in codes.items():
                    with self.subTest(degrees=degrees):
                        output = create_auto_oriented_image(
                            source,
                            correction_degrees=degrees,
                        )
                        np.testing.assert_array_equal(
                            read_image(output),
                            cv2.rotate(image, code),
                        )
            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
