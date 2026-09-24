import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import build_company_update as builder


class FigureHeadingTrialPackagingTests(unittest.TestCase):
    def test_default_does_not_include_trial(self):
        with patch.object(builder, "approved_figure_heading_release_files", return_value=()):
            self.assertEqual(builder.figure_heading_package_files(), ())

    def test_explicit_trial_requires_verified_artifacts(self):
        with patch.object(builder, "approved_figure_heading_release_files", return_value=()), patch(
            "features.patent_ocr.ocr_worker.is_verified_experimental_figure_heading_model", return_value=True
        ):
            self.assertEqual(builder.figure_heading_package_files(True), builder.FIGURE_HEADING_TRIAL_FILES)

    def test_missing_trial_is_rejected_before_creating_package(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            builder, "approved_figure_heading_release_files", return_value=()
        ), patch("features.patent_ocr.ocr_worker.is_verified_experimental_figure_heading_model", return_value=False):
            with self.assertRaises(RuntimeError):
                builder.build_package(Path(temporary), "missing", include_figure_heading_trial=True)
            self.assertFalse((Path(temporary) / "missing").exists())
