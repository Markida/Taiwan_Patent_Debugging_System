import unittest

from app.workflow_context import PatentWorkflowContext
from features.patent_review.cross_checker import compare_document_symbols_with_ocr
from features.patent_review.models import PatentDocument, PatentParagraph, TextRunSpan
from features.patent_review.rule_engine import review_document
from features.patent_review.section_parser import assign_sections
from features.patent_review.symbol_transfer import (
    REPRESENTATIVE_SYMBOL_SOURCE,
    extract_document_symbols,
    rebuild_transfer_from_reference_texts,
)
from features.patent_review.text_normalizer import normalize_patent_text


def build_document(lines):
    paragraphs = [
        PatentParagraph(
            index=index,
            text=text,
            normalized_text=normalize_patent_text(text),
            source_path=f"body/p[{index}]",
            run_spans=[TextRunSpan(0, 0, len(text), text)] if text else [],
        )
        for index, text in enumerate(lines)
    ]
    sections, patent_type, patent_title = assign_sections(paragraphs)
    return PatentDocument(
        source_path="C:/synthetic/transfer.docx",
        file_name="transfer.docx",
        file_size_bytes=0,
        sha256="b" * 64,
        patent_type=patent_type,
        patent_title=patent_title,
        paragraphs=paragraphs,
        sections=sections,
    )


BASE_LINES = [
    "【中文新型名稱】測試裝置",
    "【符號說明】",
    "1:底座",
    "2, 3：上蓋",
    "7′:定位銷",
    "10A:連接件",
    "20-22:支架",
    "【代表圖之符號簡單說明】",
    "1:底座",
    "新型專利說明書",
]


class PatentSymbolTransferTests(unittest.TestCase):
    def test_extracts_full_symbol_list_in_ocr_reference_format(self):
        document = build_document(BASE_LINES)
        transfer = extract_document_symbols(document, review_document(document))

        self.assertTrue(transfer.ready_for_ocr)
        self.assertEqual(
            [entry.label for entry in transfer.entries],
            ["1", "2", "3", "7'", "10A", "20", "21", "22"],
        )
        self.assertIn("2:上蓋", transfer.reference_text)
        self.assertIn("7':定位銷", transfer.reference_text)
        self.assertNotIn("代表圖", transfer.reference_text)
        self.assertTrue(transfer.is_source_ready(REPRESENTATIVE_SYMBOL_SOURCE))
        self.assertEqual(
            transfer.reference_items_for(REPRESENTATIVE_SYMBOL_SOURCE),
            [{"number": "1", "name": "底座"}],
        )

    def test_fullwidth_colon_is_accepted_without_warning(self):
        document = build_document([
            "【中文新型名稱】測試裝置",
            "【符號說明】",
            "10：主箱體",
        ])
        transfer = extract_document_symbols(document)

        self.assertTrue(transfer.ready_for_ocr)
        self.assertEqual(transfer.reference_items, [{"number": "10", "name": "主箱體"}])
        self.assertFalse(
            any(warning.symbol_source == "full" for warning in transfer.warnings)
        )

    def test_ambiguous_or_conflicting_symbols_block_complete_handoff(self):
        document = build_document([
            "【中文新型名稱】測試裝置",
            "【符號說明】",
            "10:主箱體",
            "10:外殼",
            "A-C:英文字母範圍",
        ])
        transfer = extract_document_symbols(document)

        self.assertFalse(transfer.ready_for_ocr)
        self.assertEqual([entry.label for entry in transfer.entries], ["10"])
        self.assertEqual(
            {
                warning.code
                for warning in transfer.warnings
                if warning.symbol_source == "full"
            },
            {"SYMBOL_NAME_CONFLICT", "SYMBOL_EXPRESSION_AMBIGUOUS"},
        )

    def test_cross_check_keeps_low_confidence_label_but_requests_confirmation(self):
        transfer = extract_document_symbols(build_document(BASE_LINES))
        result = compare_document_symbols_with_ocr(
            transfer,
            [
                {
                    "image_name": "Pic_01",
                    "detections": [
                        {"label": "1", "confidence": 0.95},
                        {"label": "2", "confidence": 0.55},
                        {"label": "X", "confidence": 0.91},
                    ],
                },
                {
                    "image_name": "Pic_02",
                    "detections": [
                        {"label": "3", "confidence": 0.70},
                        {"label": "7′", "confidence": 0.40, "manually_corrected": True},
                    ],
                },
            ],
            confidence_threshold=0.60,
        )

        self.assertIn("2", result.detected_labels_all_images)
        self.assertNotIn("2", result.document_labels_missing_from_all_images)
        self.assertEqual(result.labels_needing_confirmation, ["2"])
        self.assertEqual(result.detected_labels_missing_from_document, ["X"])
        self.assertEqual(
            result.document_labels_missing_from_all_images,
            ["10A", "20", "21", "22"],
        )
        self.assertFalse(result.ready_for_final_comparison)

        representative_result = compare_document_symbols_with_ocr(
            transfer,
            [{"image_name": "代表圖", "numbers": ["1", "X"]}],
            symbol_source=REPRESENTATIVE_SYMBOL_SOURCE,
        )
        self.assertEqual(representative_result.document_labels, ["1"])
        self.assertEqual(representative_result.detected_labels_missing_from_document, ["X"])
        self.assertEqual(representative_result.symbol_source, REPRESENTATIVE_SYMBOL_SOURCE)

    def test_workflow_context_delivers_current_transfer_to_feature_pages(self):
        context = PatentWorkflowContext()
        transfer = extract_document_symbols(build_document(BASE_LINES))
        received = []

        context.publish_symbol_transfer(transfer)
        context.subscribe_symbol_transfer(received.append)

        self.assertIs(context.symbol_transfer, transfer)
        self.assertEqual(received, [transfer])
        context.clear_document()
        self.assertIsNone(context.symbol_transfer)

    def test_confirmed_editor_text_rebuilds_both_lists(self):
        original = extract_document_symbols(build_document(BASE_LINES))
        rebuilt = rebuild_transfer_from_reference_texts(
            original,
            "1:底座\n2:人工修正上蓋\n99:人工新增",
            "1:底座\n7':代表圖新增",
        )

        self.assertTrue(rebuilt.ready_for_ocr)
        self.assertTrue(rebuilt.is_source_ready(REPRESENTATIVE_SYMBOL_SOURCE))
        self.assertEqual(
            [entry.label for entry in rebuilt.full_entries],
            ["1", "2", "99"],
        )
        self.assertEqual(
            [entry.label for entry in rebuilt.representative_entries],
            ["1", "7'"],
        )
        self.assertEqual(
            rebuilt.manual_override_sources,
            ["full", "representative"],
        )


if __name__ == "__main__":
    unittest.main()
