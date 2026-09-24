import json
from pathlib import Path

from training.first_stage.prepare_gold_validation import (
    box_iou,
    build_freeze_payload,
    filter_suggestions_to_groups,
    merge_model_suggestions,
    write_or_verify_freeze,
)


def test_merge_model_suggestions_votes_without_merging_adjacent_boxes():
    items = [
        {"label": "1", "x1": 10, "y1": 10, "x2": 20, "y2": 30, "confidence": 0.8, "model": "a"},
        {"label": "I", "x1": 10, "y1": 10, "x2": 20, "y2": 30, "confidence": 0.9, "model": "b"},
        {"label": "1", "x1": 10.2, "y1": 10, "x2": 20.2, "y2": 30, "confidence": 0.7, "model": "c"},
        {"label": "1", "x1": 22, "y1": 10, "x2": 30, "y2": 30, "confidence": 0.95, "model": "a"},
    ]
    selected, diagnostics = merge_model_suggestions(items, 0.65)
    assert [item["label"] for item in selected] == ["1", "1"]
    assert len(diagnostics) == 2
    assert box_iou(items[0], items[3]) < 0.65


def test_frozen_manifest_refuses_mutation(tmp_path):
    path = tmp_path / "freeze.json"
    payload = {"format_version": 1, "pages": [{"page_id": "a"}]}
    write_or_verify_freeze(path, payload)
    write_or_verify_freeze(path, payload)
    changed = {"format_version": 1, "pages": [{"page_id": "b"}]}
    try:
        write_or_verify_freeze(path, changed)
    except RuntimeError as error:
        assert "new version" in str(error)
    else:
        raise AssertionError("Frozen manifest mutation was accepted")


def test_build_freeze_payload_hashes_source(tmp_path):
    source = tmp_path / "images" / "val" / "page.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"gold")
    rows = [
        {
            "filename": source.name,
            "path": "images/val/page.png",
            "publication_number": "TW1",
            "application_number": "APP1",
            "source_pdf_page": 1,
            "title": "demo",
            "width": 10,
            "height": 20,
            "dpi": 300,
        }
    ]
    payload = build_freeze_payload(tmp_path, rows)
    assert payload["page_count"] == 1
    assert payload["publication_count"] == 1
    assert payload["pages"][0]["file_sha256"]


def test_filter_suggestions_to_complete_label_regions():
    suggestions = {
        "page.png": [
            {"label": "1", "x1": 10, "y1": 10, "x2": 20, "y2": 30},
            {"label": "0", "x1": 200, "y1": 200, "x2": 220, "y2": 220},
        ]
    }
    regions = {
        "page.png": [
            {"x1": 8, "y1": 8, "x2": 24, "y2": 34},
        ]
    }
    filtered, rejected = filter_suggestions_to_groups(suggestions, regions, 0.15)
    assert [item["label"] for item in filtered["page.png"]] == ["1"]
    assert rejected == 1
