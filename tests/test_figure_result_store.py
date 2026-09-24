import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from features.patent_ocr.result_store import FigureResultStore, FigureResultStoreError


class FigureResultStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = FigureResultStore(self.root / "saved")
        self.pdf = self.root / "圖式Ａ.pdf"
        self.pdf.write_bytes(b"PDF version one")
        self.image = self.root / "page.png"
        self.image.write_bytes(b"durable image bytes")
        self.identity = self.store.identify([self.pdf])
        self.state = {"image_paths": [str(self.image)], "source_image_paths": [str(self.image)],
                      "all_results": [{"image_path": str(self.image), "original_image_path": str(self.image),
                                       "numbers": ["12A'"], "rotation_degrees": 90}],
                      "manual": {"text": "修改中文", "removed": ["9"], "added": ["10"]}}

    def test_filename_case_unicode_and_folder_independent_but_content_checked(self):
        renamed = self.root / "folder" / "圖式a.PDF"
        renamed.parent.mkdir()
        renamed.write_bytes(b"new version")
        other = self.store.identify([renamed])
        self.assertEqual(self.identity["key"], other["key"])
        self.assertNotEqual(self.identity["sha256s"], other["sha256s"])
        self.store.save(self.identity, self.state)
        self.assertEqual(self.store.load(other)["_saved_source_identity"], self.identity)

    def test_roundtrip_images_survive_original_deletion_and_store_restart(self):
        self.store.save(self.identity, self.state)
        self.image.unlink()
        restored = FigureResultStore(self.store.root).load(self.identity)
        self.assertEqual(restored["manual"], self.state["manual"])
        self.assertEqual(Path(restored["image_paths"][0]).read_bytes(), b"durable image bytes")
        self.assertEqual(restored["all_results"][0]["image_path"], restored["image_paths"][0])
        self.assertEqual(len(list((self.store.root / "assets").iterdir())), 1)

    def test_ordered_image_batch_does_not_collide_with_pdf_or_other_order(self):
        a = self.store.identify([self.pdf, self.image], kind="images")
        b = self.store.identify([self.image, self.pdf], kind="images")
        self.assertNotEqual(a["key"], b["key"])
        self.assertNotEqual(self.identity["key"], self.store.identify([self.pdf], kind="images")["key"])

    def test_missing_record_does_not_create_folders(self):
        self.assertIsNone(self.store.load(self.identity))
        self.assertFalse(self.store.root.exists())

    def test_atomic_record_failure_keeps_previous_revision(self):
        self.store.save(self.identity, self.state)
        self.state["manual"]["text"] = "new revision"
        with patch("features.patent_ocr.result_store.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(FigureResultStoreError):
                self.store.save(self.identity, self.state)
        self.assertEqual(self.store.load(self.identity)["manual"]["text"], "修改中文")
        self.assertEqual(list((self.store.root / "records").glob("*.tmp")), [])

    def test_corrupt_record_and_missing_image_report_errors(self):
        self.store.save(self.identity, self.state)
        record = self.store._record_path(self.identity)
        original = record.read_bytes()
        record.write_text("{broken", encoding="utf-8")
        with self.assertRaises(FigureResultStoreError):
            self.store.load(self.identity)
        record.write_bytes(original)
        next((self.store.root / "assets").iterdir()).unlink()
        with self.assertRaises(FigureResultStoreError):
            self.store.load(self.identity)

    def test_asset_path_escape_rejected(self):
        self.store.save(self.identity, self.state)
        record = self.store._record_path(self.identity)
        envelope = json.loads(record.read_text(encoding="utf-8"))
        envelope["state"]["image_paths"] = [{self.store.ASSET_FIELD: "../../private.png"}]
        record.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaises(FigureResultStoreError):
            self.store.load(self.identity)


if __name__ == "__main__":
    unittest.main()
