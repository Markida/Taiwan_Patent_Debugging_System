import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from features.patent_ocr.figure_heading_classes import (
    FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
)
from features.patent_ocr.image_io import write_image
from training.figure_heading.prepare_pilot_dataset import (
    PilotDatasetError,
    prepare_pilot_dataset,
)
from training.manual_annotation.common import (
    Annotation,
    make_page_record,
    read_page_record,
    write_page_record,
)


class FigureHeadingPilotDatasetTests(unittest.TestCase):
    def _add_page(
        self,
        root,
        page_id,
        document_id,
        split,
        *,
        image_value,
        figure_number="1",
        prefix_label="figure_prefix",
        reviewed=True,
        negative=False,
    ):
        image_path = root / "images" / split / f"{page_id}.png"
        record_path = root / "labels" / split / f"{page_id}.json"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.parent.mkdir(parents=True, exist_ok=True)
        image = np.full((100, 160, 3), int(image_value), dtype=np.uint8)
        self.assertTrue(write_image(image_path, image))
        annotations = []
        if not negative:
            annotations = [
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
                    text=figure_number,
                    pair_id="caption-001",
                ),
            ]
        record = make_page_record(
            page_id=page_id,
            split=split,
            image_path=image_path.relative_to(root),
            source_path=f"{document_id}.pdf#page=1",
            image_width=160,
            image_height=100,
            annotations=annotations,
            reviewed=reviewed,
        )
        record.update(
            {
                "annotation_mode": "figure-heading",
                "document_id": document_id,
                "relative_pdf": f"A-pdf/{document_id}.pdf",
                "operator_note": f"keep-{page_id}",
                "deleted_components": {"legacy": [1, 2]},
            }
        )
        write_page_record(record_path, record)
        return record_path, image_path

    def _make_source(self, root):
        source = root / "annotations"
        self._add_page(source, "val-1", "val-doc", "validation", image_value=10)
        self._add_page(
            source,
            "test-1",
            "test-doc",
            "test",
            image_value=20,
            prefix_label=FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
        )
        self._add_page(source, "a-1", "train-a", "train", image_value=31)
        self._add_page(source, "a-2", "train-a", "train", image_value=32)
        self._add_page(
            source,
            "b-1",
            "train-b",
            "train",
            image_value=40,
            prefix_label=FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
        )
        self._add_page(
            source,
            "c-1",
            "train-c",
            "train",
            image_value=50,
            figure_number="3A",
        )
        self._add_page(
            source,
            "d-1",
            "train-d",
            "train",
            image_value=60,
            negative=True,
        )
        # Identical image content connects these two documents into one split group.
        self._add_page(source, "dup-1", "dup-a", "train", image_value=70)
        self._add_page(source, "dup-2", "dup-b", "train", image_value=70)
        self._add_page(
            source,
            "unfinished",
            "unfinished-doc",
            "train",
            image_value=80,
            reviewed=False,
        )
        return source

    def test_copies_reviewed_snapshot_and_builds_four_rotations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._make_source(root)
            output = root / "pilot"
            source_record = source / "labels" / "train" / "c-1.json"
            source_bytes = source_record.read_bytes()

            result = prepare_pilot_dataset(
                source,
                output,
                seed=20260917,
                target_validation_pages=5,
                minimum_validation_pages=4,
                maximum_validation_pages=5,
                minimum_validation_documents=3,
                selection_attempts=1000,
            )

            manifest = result["manifest"]
            self.assertEqual(manifest["reviewed_pages"], 9)
            self.assertEqual(manifest["excluded_unreviewed_pages"], 1)
            self.assertEqual(manifest["distributions"]["validation"]["pages"], 5)
            self.assertEqual(manifest["distributions"]["test"]["pages"], 1)
            self.assertGreater(manifest["distributions"]["train"]["pages"], 0)
            by_page = {item["page_id"]: item for item in manifest["pages"]}
            self.assertEqual(by_page["val-1"]["new_split"], "validation")
            self.assertEqual(by_page["test-1"]["new_split"], "test")
            self.assertEqual(
                by_page["dup-1"]["new_split"],
                by_page["dup-2"]["new_split"],
            )
            self.assertNotIn("unfinished", by_page)

            snapshot_record_path = (
                Path(result["snapshot_root"])
                / by_page["c-1"]["snapshot_record_path"]
            )
            snapshot_record = read_page_record(snapshot_record_path)
            self.assertEqual(snapshot_record["operator_note"], "keep-c-1")
            self.assertEqual(snapshot_record["deleted_components"], {"legacy": [1, 2]})
            self.assertEqual(snapshot_record["annotations"][1]["text"], "3A")
            self.assertEqual(source_record.read_bytes(), source_bytes)

            copied_image = (
                Path(result["snapshot_root"])
                / by_page["c-1"]["snapshot_image_path"]
            )
            source_image = source / by_page["c-1"]["source_image_path"]
            self.assertFalse(os.path.samefile(source_image, copied_image))
            self.assertEqual(
                by_page["c-1"]["content_sha256"],
                hashlib.sha256(copied_image.read_bytes()).hexdigest(),
            )

            dataset_root = Path(result["dataset_root"])
            self.assertTrue((dataset_root / "data.yaml").is_file())
            self.assertTrue((output / "split_manifest.json").is_file())
            self.assertEqual(
                result["dataset_report"]["generated_rotated_images"],
                9 * 4,
            )

    def test_seeded_document_selection_is_reproducible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._make_source(root)
            kwargs = dict(
                seed=20260917,
                target_validation_pages=5,
                minimum_validation_pages=4,
                maximum_validation_pages=5,
                minimum_validation_documents=3,
                selection_attempts=500,
                build_yolo=False,
            )
            first = prepare_pilot_dataset(source, root / "pilot-a", **kwargs)
            second = prepare_pilot_dataset(source, root / "pilot-b", **kwargs)
            self.assertEqual(
                first["manifest"]["moved_documents"],
                second["manifest"]["moved_documents"],
            )
            self.assertEqual(
                first["manifest"]["distributions"],
                second["manifest"]["distributions"],
            )

    def test_invalid_reviewed_page_fails_before_creating_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._make_source(root)
            record_path = source / "labels" / "train" / "c-1.json"
            record = read_page_record(record_path)
            record["annotations"][1]["text"] = ""
            write_page_record(record_path, record)
            output = root / "pilot"

            with self.assertRaisesRegex(PilotDatasetError, "不會默默排除"):
                prepare_pilot_dataset(
                    source,
                    output,
                    target_validation_pages=5,
                    minimum_validation_pages=4,
                    maximum_validation_pages=5,
                    minimum_validation_documents=3,
                    build_yolo=False,
                )
            self.assertFalse(output.exists())

    def test_failed_yolo_build_keeps_snapshot_and_writes_failure_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._make_source(root)
            output = root / "pilot"

            with patch(
                "training.figure_heading.prepare_pilot_dataset.build_dataset",
                side_effect=RuntimeError("synthetic build failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "synthetic build failure"):
                    prepare_pilot_dataset(
                        source,
                        output,
                        target_validation_pages=5,
                        minimum_validation_pages=4,
                        maximum_validation_pages=5,
                        minimum_validation_documents=3,
                    )

            self.assertTrue((output / "snapshot").is_dir())
            failure = json.loads(
                (output / "failure.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failure["error_type"], "RuntimeError")
            self.assertIn("synthetic build failure", failure["error"])


if __name__ == "__main__":
    unittest.main()
