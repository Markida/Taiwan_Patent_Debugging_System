from training.v3.build_pseudo_dataset import (
    Prediction,
    merge_teachers,
    predictions_in_crop,
)
from training.v3.common import BASE_CLASS_NAMES, CLASS_NAMES, CLASS_TO_ID


CFG = {
    "agreement_iou": 0.35,
    "consensus_min_v1": 0.12,
    "consensus_min_v2": 0.10,
    "v2_only_minimum": 0.72,
    "v1_only_minimum": 0.92,
    "zero_minimum": 0.94,
    "j_minimum": 0.95,
    "prime_minimum": 0.82,
    "zero_min_height_ratio": 0.65,
    "j_min_height_ratio": 0.90,
}


def prediction(label, confidence, box=(10, 10, 30, 50), teacher="v1"):
    return Prediction(label, confidence, *box, teacher)


def test_lowercase_classes_are_appended_without_shifting_legacy_ids():
    assert len(BASE_CLASS_NAMES) == 37
    assert len(CLASS_NAMES) == 63
    assert CLASS_NAMES[:37] == BASE_CLASS_NAMES
    assert CLASS_TO_ID["prime"] == 36
    assert CLASS_TO_ID["a"] == 37
    assert CLASS_TO_ID["z"] == 62


def test_same_class_teacher_consensus_is_accepted():
    accepted, rejected, _height = merge_teachers(
        [prediction("A", 0.60, teacher="v1")],
        [prediction("A", 0.55, box=(11, 10, 31, 50), teacher="v2")],
        CFG,
    )
    assert [item.label for item in accepted] == ["A"]
    assert rejected == []


def test_v1_only_zero_is_never_auto_labelled():
    accepted, rejected, _height = merge_teachers(
        [prediction("0", 0.99, teacher="v1")],
        [],
        CFG,
    )
    assert accepted == []
    assert {item["reason"] for item in rejected} == {"v1_only_high_risk_0"}


def test_consensus_zero_requires_manual_review():
    accepted, rejected, _height = merge_teachers(
        [prediction("0", 0.97, teacher="v1")],
        [prediction("0", 0.96, box=(11, 10, 31, 50), teacher="v2")],
        CFG,
    )
    assert [item.label for item in accepted] == ["0"]
    assert "high_risk_class_requires_manual_review" in {item["reason"] for item in rejected}


def test_page_predictions_translate_into_group_crop():
    source = [
        prediction("7", 0.8, box=(110, 210, 130, 250), teacher="v2"),
        prediction("8", 0.8, box=(10, 10, 30, 50), teacher="v2"),
    ]
    selected = predictions_in_crop(source, 100, 200, 150, 270)
    assert len(selected) == 1
    assert selected[0].label == "7"
    assert (selected[0].x1, selected[0].y1, selected[0].x2, selected[0].y2) == (10, 10, 30, 50)
