"""Detect 圖 + identifier headings and infer page orientation.

The production component-label model deliberately ignores figure captions.
This module consumes a dedicated locator for a normal 圖 prefix, its complete
identifier, and a sideways 圖 prefix that explicitly requests a clockwise
90-degree correction. Directed prefix-to-identifier geometry still resolves
the other right-angle orientations.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2

from app.config import IMG_SIZE, OCR_ALLOWLIST
from features.patent_ocr.figure_heading_classes import (
    FIGURE_HEADING_CLASS_NAMES,
    FIGURE_IDENTIFIER_CLASS,
    FIGURE_PREFIX_CLASS,
    FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
)
from features.patent_ocr.figure_identifiers import (
    normalize_figure_identifier,
    ordered_unique_figures,
)
from features.patent_ocr.image_io import read_image


PREFIX_CLASS_NAMES = {
    FIGURE_PREFIX_CLASS,
    FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
    "figure_word",
    "tu_word",
}
ROTATE_RIGHT_PREFIX_CLASS_NAMES = {FIGURE_PREFIX_ROTATE_RIGHT_CLASS}
IDENTIFIER_CLASS_NAMES = {
    FIGURE_IDENTIFIER_CLASS,
    "figure_number",
    "figure_id",
}
ROTATE_RIGHT_CLASS_AUTO_CONFIDENCE = 0.70
SINGLE_CAPTION_ORIENTATION_CONFIDENCE = 0.74
DIRECTION_CORRECTIONS = {
    "right": 0,
    "down": 270,
    "left": 180,
    "up": 90,
}


def _normalized_class_name(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _class_names(model) -> Dict[int, str]:
    names = getattr(model, "names", {})
    if isinstance(names, dict):
        return {int(index): _normalized_class_name(name) for index, name in names.items()}
    return {
        index: _normalized_class_name(name)
        for index, name in enumerate(list(names))
    }


def is_figure_heading_model(model) -> bool:
    """Return whether a detector exposes the exact three heading roles."""

    try:
        names = _class_names(model)
    except Exception:
        return False
    return (
        set(names) == {0, 1, 2}
        and names[0] == FIGURE_PREFIX_CLASS
        and names[1] in IDENTIFIER_CLASS_NAMES
        and names[2] == FIGURE_PREFIX_ROTATE_RIGHT_CLASS
    )


def empty_figure_heading_analysis(status: str = "no_evidence") -> Dict[str, object]:
    return {
        "status": status,
        "detected_figure_numbers": [],
        "auto_figure_numbers": [],
        "figure_caption_detections": [],
        "prefix_detection_count": 0,
        "identifier_detection_count": 0,
        "strong_prefix_count": 0,
        "rotate_right_prefix_detection_count": 0,
        "rotate_right_prefix_detections": [],
        "mapping": {
            "status": status,
            "confidence": 0.0,
            "complete": False,
        },
        "orientation": {
            "status": status,
            "correction_degrees": None,
            "confidence": 0.0,
            "votes": {"0": 0.0, "90": 0.0, "180": 0.0, "270": 0.0},
            "evidence_count": 0,
        },
    }


def _box_iou(first: Dict[str, object], second: Dict[str, object]) -> float:
    ax1, ay1, ax2, ay2 = _box_values(first)
    bx1, by1, bx2, by2 = _box_values(second)
    intersection_width = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    intersection_height = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = intersection_width * intersection_height
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - intersection
    return intersection / max(union, 1e-9)


def _deduplicate_prefix_detections(
    detections: Sequence[Dict[str, object]],
    *,
    overlap_threshold: float = 0.75,
) -> List[Dict[str, object]]:
    """Keep the strongest class when normal/sideways prefix boxes overlap."""

    kept: List[Dict[str, object]] = []
    for candidate in sorted(
        detections,
        key=lambda item: float(item.get("confidence", 0.0)),
        reverse=True,
    ):
        if any(
            _box_iou(candidate, existing) >= float(overlap_threshold)
            for existing in kept
        ):
            continue
        kept.append(candidate)
    return kept


def locate_figure_heading_boxes(
    image_path,
    model,
    *,
    confidence: float = 0.15,
    iou: float = 0.30,
    imgsz: int = IMG_SIZE,
    device="cpu",
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Run the dedicated locator and return prefix/identifier boxes."""

    if not is_figure_heading_model(model):
        raise ValueError(
            "圖題定位模型類別必須依序為："
            + "、".join(FIGURE_HEADING_CLASS_NAMES)
        )

    predict_kwargs = {
        "source": str(image_path),
        "imgsz": int(imgsz),
        "conf": float(confidence),
        "iou": float(iou),
        "device": device,
        "verbose": False,
    }
    if getattr(model, "supports_sliced_inference", False):
        predict_kwargs.update(
            {
                "sliced": True,
                "slice_conf": max(0.20, float(confidence)),
                "tile_fraction": 0.58,
                "edge_margin": 4.0,
            }
        )
    results = model.predict(**predict_kwargs)
    boxes = results[0].boxes
    class_names = _class_names(model)
    prefixes: List[Dict[str, object]] = []
    identifiers: List[Dict[str, object]] = []
    if boxes is None:
        return prefixes, identifiers

    for box in boxes:
        x1, y1, x2, y2 = (float(value) for value in box.xyxy[0])
        confidence_value = float(box.conf[0])
        class_name = class_names.get(int(box.cls[0]), "")
        item = {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "confidence": confidence_value,
            "class_name": class_name,
        }
        if class_name in PREFIX_CLASS_NAMES:
            prefixes.append(item)
        elif class_name in IDENTIFIER_CLASS_NAMES:
            identifiers.append(item)

    prefixes = _deduplicate_prefix_detections(prefixes)
    sort_key = lambda item: (float(item["y1"]), float(item["x1"]))
    return sorted(prefixes, key=sort_key), sorted(identifiers, key=sort_key)


def _box_values(box: Dict[str, object]) -> Tuple[float, float, float, float]:
    x1 = float(box["x1"])
    y1 = float(box["y1"])
    x2 = float(box["x2"])
    y2 = float(box["y2"])
    if x2 <= x1 or y2 <= y1:
        raise ValueError("Detection boxes must have positive width and height.")
    return x1, y1, x2, y2


def _interval_overlap(first: Tuple[float, float], second: Tuple[float, float]) -> float:
    intersection = max(0.0, min(first[1], second[1]) - max(first[0], second[0]))
    denominator = max(1e-6, min(first[1] - first[0], second[1] - second[0]))
    return intersection / denominator


def score_heading_geometry(
    prefix_box: Dict[str, object],
    identifier_box: Dict[str, object],
) -> Optional[Dict[str, object]]:
    """Score one directed prefix/identifier pair using normalized geometry."""

    px1, py1, px2, py2 = _box_values(prefix_box)
    nx1, ny1, nx2, ny2 = _box_values(identifier_box)
    pcx, pcy = (px1 + px2) / 2.0, (py1 + py2) / 2.0
    ncx, ncy = (nx1 + nx2) / 2.0, (ny1 + ny2) / 2.0
    dx, dy = ncx - pcx, ncy - pcy
    if dx == 0 and dy == 0:
        return None

    if abs(dx) >= abs(dy):
        direction = "right" if dx > 0 else "left"
        major, minor = abs(dx), abs(dy)
        prefix_cross = py2 - py1
        identifier_cross = ny2 - ny1
        overlap = _interval_overlap((py1, py2), (ny1, ny2))
        gap = nx1 - px2 if direction == "right" else px1 - nx2
        union_long = max(px2, nx2) - min(px1, nx1)
        union_short = max(py2, ny2) - min(py1, ny1)
    else:
        direction = "down" if dy > 0 else "up"
        major, minor = abs(dy), abs(dx)
        prefix_cross = px2 - px1
        identifier_cross = nx2 - nx1
        overlap = _interval_overlap((px1, px2), (nx1, nx2))
        gap = ny1 - py2 if direction == "down" else py1 - ny2
        union_long = max(py2, ny2) - min(py1, ny1)
        union_short = max(px2, nx2) - min(px1, nx1)

    scale = max(1.0, prefix_cross)
    angle = math.degrees(math.atan2(minor, max(major, 1e-6)))
    size_ratio = identifier_cross / scale
    normalized_gap = gap / scale
    normalized_distance = major / scale
    axis_ratio = union_long / max(1.0, union_short)
    if (
        angle > 25.0
        or overlap < 0.50
        or not 0.45 <= size_ratio <= 2.20
        or not -0.25 <= normalized_gap <= 2.50
        or not 0.60 <= normalized_distance <= 7.00
        or axis_ratio < 1.15
    ):
        return None

    angle_score = max(0.0, 1.0 - angle / 25.0)
    overlap_score = min(1.0, 0.50 + max(0.0, overlap - 0.50) / 0.40)
    size_score = max(0.0, 1.0 - abs(math.log(size_ratio)) / math.log(2.20))
    if -0.10 <= normalized_gap <= 0.90:
        gap_score = 1.0
    elif normalized_gap < -0.10:
        gap_score = max(0.0, (normalized_gap + 0.25) / 0.15)
    else:
        gap_score = max(0.0, (2.50 - normalized_gap) / 1.60)
    if 0.80 <= normalized_distance <= 4.00:
        distance_score = 1.0
    elif normalized_distance < 0.80:
        distance_score = max(0.0, (normalized_distance - 0.60) / 0.20)
    else:
        distance_score = max(0.0, (7.00 - normalized_distance) / 3.00)
    axis_score = min(1.0, max(0.0, (axis_ratio - 1.15) / 0.85))
    geometry_score = (
        0.20 * angle_score
        + 0.25 * overlap_score
        + 0.15 * size_score
        + 0.20 * gap_score
        + 0.10 * distance_score
        + 0.10 * axis_score
    )
    return {
        "direction": direction,
        "correction_degrees": DIRECTION_CORRECTIONS[direction],
        "geometry_score": round(float(geometry_score), 6),
        "angle_degrees": round(float(angle), 3),
        "cross_axis_overlap": round(float(overlap), 6),
        "normalized_gap": round(float(normalized_gap), 6),
        "normalized_distance": round(float(normalized_distance), 6),
        "combined_axis_ratio": round(float(axis_ratio), 6),
    }


def pair_figure_heading_boxes(
    prefix_boxes: Sequence[Dict[str, object]],
    identifier_boxes: Sequence[Dict[str, object]],
) -> List[Dict[str, object]]:
    """Select maximum-cardinality pairs, then maximize their total score."""

    candidates = []
    for prefix_index, prefix_box in enumerate(prefix_boxes):
        for identifier_index, identifier_box in enumerate(identifier_boxes):
            geometry = score_heading_geometry(prefix_box, identifier_box)
            if geometry is None:
                continue
            if (
                _normalized_class_name(prefix_box.get("class_name"))
                in ROTATE_RIGHT_PREFIX_CLASS_NAMES
            ):
                geometry = {
                    **geometry,
                    "geometry_correction_degrees": geometry[
                        "correction_degrees"
                    ],
                    "correction_degrees": 90,
                    "orientation_source": FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
                }
            detector_score = math.sqrt(
                max(0.0, float(prefix_box.get("confidence", 0.0)))
                * max(0.0, float(identifier_box.get("confidence", 0.0)))
            )
            candidates.append(
                (
                    detector_score * float(geometry["geometry_score"]),
                    prefix_index,
                    identifier_index,
                    geometry,
                )
            )

    if not candidates:
        return []
    active_prefixes = sorted({item[1] for item in candidates})
    active_identifiers = sorted({item[2] for item in candidates})
    prefix_to_local = {
        original_index: local_index
        for local_index, original_index in enumerate(active_prefixes)
    }
    identifier_to_local = {
        original_index: local_index
        for local_index, original_index in enumerate(active_identifiers)
    }
    prefix_count = len(active_prefixes)
    identifier_count = len(active_identifiers)

    # The square matrix includes one dummy column per prefix and one dummy row
    # per identifier, so every real box may remain unmatched.  Giving every
    # valid edge a bonus greater than the largest possible aggregate score
    # makes cardinality the primary objective and detector/geometry quality
    # the secondary objective.
    matrix_size = prefix_count + identifier_count
    cardinality_bonus = min(prefix_count, identifier_count) + 1.0
    impossible_weight = -(cardinality_bonus * (matrix_size + 1.0))
    weights = [
        [0.0 for _column in range(matrix_size)]
        for _row in range(matrix_size)
    ]
    for prefix_index in range(prefix_count):
        for identifier_index in range(identifier_count):
            weights[prefix_index][identifier_index] = impossible_weight

    candidate_lookup = {}
    for score, prefix_index, identifier_index, geometry in candidates:
        local_prefix = prefix_to_local[prefix_index]
        local_identifier = identifier_to_local[identifier_index]
        weights[local_prefix][local_identifier] = cardinality_bonus + score
        candidate_lookup[(prefix_index, identifier_index)] = geometry

    # Hungarian assignment (minimum-cost form) on the negated weights.  This
    # avoids an optional SciPy dependency in the packaged offline application.
    costs = [[-value for value in row] for row in weights]
    potentials_rows = [0.0] * (matrix_size + 1)
    potentials_columns = [0.0] * (matrix_size + 1)
    matched_row = [0] * (matrix_size + 1)
    previous_column = [0] * (matrix_size + 1)
    for row in range(1, matrix_size + 1):
        matched_row[0] = row
        column = 0
        minimums = [float("inf")] * (matrix_size + 1)
        used = [False] * (matrix_size + 1)
        while True:
            used[column] = True
            current_row = matched_row[column]
            delta = float("inf")
            next_column = 0
            for candidate_column in range(1, matrix_size + 1):
                if used[candidate_column]:
                    continue
                reduced_cost = (
                    costs[current_row - 1][candidate_column - 1]
                    - potentials_rows[current_row]
                    - potentials_columns[candidate_column]
                )
                if reduced_cost < minimums[candidate_column]:
                    minimums[candidate_column] = reduced_cost
                    previous_column[candidate_column] = column
                if minimums[candidate_column] < delta:
                    delta = minimums[candidate_column]
                    next_column = candidate_column
            for candidate_column in range(matrix_size + 1):
                if used[candidate_column]:
                    potentials_rows[matched_row[candidate_column]] += delta
                    potentials_columns[candidate_column] -= delta
                else:
                    minimums[candidate_column] -= delta
            column = next_column
            if matched_row[column] == 0:
                break
        while True:
            next_column = previous_column[column]
            matched_row[column] = matched_row[next_column]
            column = next_column
            if column == 0:
                break

    assigned_column = [-1] * matrix_size
    for column in range(1, matrix_size + 1):
        if matched_row[column]:
            assigned_column[matched_row[column] - 1] = column - 1

    pairs: List[Dict[str, object]] = []
    for local_prefix, prefix_index in enumerate(active_prefixes):
        local_identifier = assigned_column[local_prefix]
        identifier_index = (
            active_identifiers[local_identifier]
            if 0 <= local_identifier < identifier_count
            else -1
        )
        geometry = candidate_lookup.get((prefix_index, identifier_index))
        if geometry is None:
            continue
        pairs.append(
            {
                "prefix_index": prefix_index,
                "identifier_index": identifier_index,
                "prefix_box": dict(prefix_boxes[prefix_index]),
                "identifier_box": dict(identifier_boxes[identifier_index]),
                **geometry,
            }
        )
    return sorted(
        pairs,
        key=lambda pair: (
            float(pair["prefix_box"]["y1"]),
            float(pair["prefix_box"]["x1"]),
        ),
    )


def _rotate_crop_clockwise(image, degrees: int):
    normalized = int(degrees) % 360
    if normalized == 0:
        return image
    if normalized == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if normalized == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if normalized == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError("Only right-angle rotations are supported.")


def _identifier_crop(image, box: Dict[str, object], correction_degrees: int):
    height, width = image.shape[:2]
    x1, y1, x2, y2 = _box_values(box)
    padding = max(2, int(round(max(x2 - x1, y2 - y1) * 0.08)))
    ix1 = max(0, int(math.floor(x1)) - padding)
    iy1 = max(0, int(math.floor(y1)) - padding)
    ix2 = min(width, int(math.ceil(x2)) + padding)
    iy2 = min(height, int(math.ceil(y2)) + padding)
    crop = image[iy1:iy2, ix1:ix2]
    if not crop.size:
        return None
    return _rotate_crop_clockwise(crop, correction_degrees)


def _recognize_identifier(reader, crop) -> Tuple[Optional[object], str, float]:
    from features.patent_ocr.ocr_engine import (
        preprocess_roi_variants,
        recognize_easyocr_label,
        select_easyocr_label_candidate,
    )

    candidates = []
    for variant in preprocess_roi_variants(crop):
        candidates.append(
            recognize_easyocr_label(
                reader,
                variant,
                min_confidence=0.0,
                allowlist=OCR_ALLOWLIST,
            )
        )
    text, confidence = select_easyocr_label_candidate(candidates)
    try:
        normalized = normalize_figure_identifier(text)
    except ValueError:
        return None, text, float(confidence)
    return normalized, text, float(confidence)


def infer_page_orientation(
    caption_detections: Sequence[Dict[str, object]],
) -> Dict[str, object]:
    """Conservatively infer a page correction from recognized caption pairs."""

    votes = {0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0}
    evidence = [
        item
        for item in caption_detections
        if item.get("accepted") is True
        and item.get("figure_number") is not None
        and float(item.get("pair_confidence", 0.0)) >= 0.50
        and float(item.get("ocr_confidence", 0.0)) >= 0.65
    ]
    for item in evidence:
        correction = int(item["correction_degrees"]) % 360
        votes[correction] += float(item["pair_confidence"]) ** 2

    payload = {
        "status": "no_evidence",
        "correction_degrees": None,
        "confidence": 0.0,
        "votes": {str(key): round(value, 6) for key, value in votes.items()},
        "evidence_count": len(evidence),
    }
    if not evidence:
        return payload

    ranked = sorted(votes.items(), key=lambda item: item[1], reverse=True)
    winner, winning_weight = ranked[0]
    runner_up_weight = ranked[1][1]
    total_weight = sum(votes.values())
    winner_items = [
        item
        for item in evidence
        if int(item["correction_degrees"]) % 360 == winner
    ]
    strong_opposition = any(
        int(item["correction_degrees"]) % 360 != winner
        and float(item.get("pair_confidence", 0.0)) >= 0.80
        for item in evidence
    )

    accepted = False
    if len(evidence) == 1:
        only = evidence[0]
        accepted = (
            float(only.get("pair_confidence", 0.0))
            >= SINGLE_CAPTION_ORIENTATION_CONFIDENCE
            and float(only.get("ocr_confidence", 0.0)) >= 0.75
            and float(only.get("geometry_score", 0.0)) >= 0.80
        )
    elif len(evidence) == 2:
        accepted = (
            len(winner_items) == 2
            and all(float(item.get("pair_confidence", 0.0)) >= 0.55 for item in winner_items)
        )
    else:
        accepted = (
            len(winner_items) >= 2
            and len(winner_items) / len(evidence) >= 2.0 / 3.0
            and winning_weight / max(total_weight, 1e-9) >= 0.72
            and winning_weight / max(runner_up_weight, 1e-9) >= 1.80
            and not strong_opposition
        )

    payload["confidence"] = round(winning_weight / max(total_weight, 1e-9), 6)
    if accepted:
        payload["status"] = "upright" if winner == 0 else "needs_rotation"
        payload["correction_degrees"] = winner
    elif len({int(item["correction_degrees"]) % 360 for item in evidence}) > 1:
        payload["status"] = "mixed_orientation"
    else:
        payload["status"] = "ambiguous"
    return payload


def apply_rotate_right_class_orientation(
    orientation: Dict[str, object],
    prefix_detections: Sequence[Dict[str, object]],
) -> Dict[str, object]:
    """Merge high-confidence sideways-prefix evidence into page orientation."""

    evidence = [
        item
        for item in prefix_detections
        if _normalized_class_name(item.get("class_name"))
        in ROTATE_RIGHT_PREFIX_CLASS_NAMES
        and float(item.get("confidence", 0.0))
        >= ROTATE_RIGHT_CLASS_AUTO_CONFIDENCE
    ]
    if not evidence:
        return orientation

    payload = {
        **orientation,
        "votes": dict(orientation.get("votes", {})),
        "rotate_right_class_evidence_count": len(evidence),
    }
    added_weight = sum(
        float(item.get("confidence", 0.0)) ** 2 for item in evidence
    )
    payload["votes"]["90"] = round(
        float(payload["votes"].get("90", 0.0)) + added_weight,
        6,
    )
    payload["evidence_count"] = int(payload.get("evidence_count", 0)) + len(
        evidence
    )
    class_confidence = max(float(item["confidence"]) for item in evidence)
    current_correction = payload.get("correction_degrees")
    current_status = payload.get("status")
    conflicting_votes = any(
        correction != "90" and float(weight) > 0.0
        for correction, weight in payload["votes"].items()
    )
    if (
        current_status == "mixed_orientation"
        or current_correction not in {None, 90}
        or conflicting_votes
    ):
        payload["status"] = "mixed_orientation"
        payload["correction_degrees"] = None
        payload["confidence"] = round(class_confidence, 6)
        payload["orientation_source"] = "conflicting_evidence"
        return payload

    payload["status"] = "needs_rotation"
    payload["correction_degrees"] = 90
    payload["confidence"] = round(
        max(float(payload.get("confidence", 0.0)), class_confidence),
        6,
    )
    payload["orientation_source"] = FIGURE_PREFIX_ROTATE_RIGHT_CLASS
    return payload


def analyze_figure_headings(
    image_path,
    model,
    reader,
    *,
    device="cpu",
    imgsz: int = IMG_SIZE,
) -> Dict[str, object]:
    """Locate, pair and recognize figure captions on one drawing page."""

    image = read_image(Path(image_path))
    if image is None:
        raise ValueError(f"圖片讀取失敗：{image_path}")
    prefixes, identifiers = locate_figure_heading_boxes(
        image_path,
        model,
        device=device,
        imgsz=imgsz,
    )
    pairs = pair_figure_heading_boxes(prefixes, identifiers)
    recognized = []
    for pair in pairs:
        crop = _identifier_crop(
            image,
            pair["identifier_box"],
            int(pair["correction_degrees"]),
        )
        if crop is None:
            continue
        figure_number, raw_text, ocr_confidence = _recognize_identifier(reader, crop)
        prefix_confidence = float(pair["prefix_box"].get("confidence", 0.0))
        identifier_confidence = float(pair["identifier_box"].get("confidence", 0.0))
        pair_confidence = (
            math.sqrt(max(0.0, prefix_confidence * identifier_confidence))
            * float(pair["geometry_score"])
            * math.sqrt(max(0.0, ocr_confidence))
        )
        recognized.append(
            {
                **pair,
                "figure_number": figure_number,
                "raw_ocr_text": raw_text,
                "ocr_confidence": round(ocr_confidence, 6),
                "pair_confidence": round(pair_confidence, 6),
                "accepted": bool(
                    figure_number is not None
                    and ocr_confidence >= 0.70
                    and pair_confidence >= 0.65
                ),
            }
        )

    accepted = [item for item in recognized if item["accepted"]]
    detected_numbers = (
        ordered_unique_figures(item["figure_number"] for item in accepted)
        if accepted
        else []
    )
    strong_prefix_count = sum(
        float(item.get("confidence", 0.0)) >= 0.50 for item in prefixes
    )
    accepted_prefixes = {item["prefix_index"] for item in accepted}
    complete = bool(accepted) and all(
        index in accepted_prefixes
        for index, item in enumerate(prefixes)
        if float(item.get("confidence", 0.0)) >= 0.50
    )
    minimum_confidence = min(
        (float(item["pair_confidence"]) for item in accepted),
        default=0.0,
    )
    mapping_accepted = (
        complete
        and bool(detected_numbers)
        and (len(accepted) >= 2 or minimum_confidence >= 0.78)
    )
    if mapping_accepted:
        mapping_status = "accepted"
    elif recognized:
        mapping_status = "review"
    else:
        mapping_status = "no_evidence"

    orientation = apply_rotate_right_class_orientation(
        infer_page_orientation(recognized),
        prefixes,
    )
    rotate_right_prefixes = [
        item
        for item in prefixes
        if _normalized_class_name(item.get("class_name"))
        in ROTATE_RIGHT_PREFIX_CLASS_NAMES
    ]
    return {
        "status": "ready",
        "image_width": int(image.shape[1]),
        "image_height": int(image.shape[0]),
        "detected_figure_numbers": detected_numbers,
        "auto_figure_numbers": detected_numbers if mapping_accepted else [],
        "figure_caption_detections": recognized,
        "prefix_detection_count": len(prefixes),
        "identifier_detection_count": len(identifiers),
        "strong_prefix_count": strong_prefix_count,
        "rotate_right_prefix_detection_count": len(rotate_right_prefixes),
        "rotate_right_prefix_detections": rotate_right_prefixes,
        "mapping": {
            "status": mapping_status,
            "confidence": round(minimum_confidence, 6),
            "complete": complete,
        },
        "orientation": orientation,
    }


def transform_box_clockwise(
    box: Dict[str, object],
    image_width: int,
    image_height: int,
    correction_degrees: int,
) -> Dict[str, float]:
    """Transform an original-image xyxy box into a rotated image."""

    x1, y1, x2, y2 = _box_values(box)
    degrees = int(correction_degrees) % 360
    if degrees == 0:
        values = (x1, y1, x2, y2)
    elif degrees == 90:
        values = (image_height - y2, x1, image_height - y1, x2)
    elif degrees == 180:
        values = (
            image_width - x2,
            image_height - y2,
            image_width - x1,
            image_height - y1,
        )
    elif degrees == 270:
        values = (y1, image_width - x2, y2, image_width - x1)
    else:
        raise ValueError("Only right-angle rotations are supported.")
    return dict(
        zip(
            ("x1", "y1", "x2", "y2"),
            (float(value) for value in values),
        )
    )


def exclude_caption_identifiers_from_components(
    result: Dict[str, object],
    heading_analysis: Dict[str, object],
    *,
    original_width: int,
    original_height: int,
    correction_degrees: int,
) -> int:
    """Remove accepted caption IDs from the component-label result only."""

    caption_boxes = []
    for caption in heading_analysis.get("figure_caption_detections", []):
        if not caption.get("accepted"):
            continue
        transformed = transform_box_clockwise(
            caption["identifier_box"],
            original_width,
            original_height,
            correction_degrees,
        )
        width = transformed["x2"] - transformed["x1"]
        height = transformed["y2"] - transformed["y1"]
        caption_boxes.append(
            {
                "x1": transformed["x1"] - 0.15 * width,
                "y1": transformed["y1"] - 0.15 * height,
                "x2": transformed["x2"] + 0.15 * width,
                "y2": transformed["y2"] + 0.15 * height,
                "figure_number": caption.get("figure_number"),
            }
        )

    kept = []
    removed = []
    for detection in result.get("detections", []):
        try:
            center_x = (float(detection["x1"]) + float(detection["x2"])) / 2.0
            center_y = (float(detection["y1"]) + float(detection["y2"])) / 2.0
        except (KeyError, TypeError, ValueError):
            kept.append(detection)
            continue
        matching_caption = next(
            (
                caption
                for caption in caption_boxes
                if caption["x1"] <= center_x <= caption["x2"]
                and caption["y1"] <= center_y <= caption["y2"]
            ),
            None,
        )
        if matching_caption is None:
            kept.append(detection)
        else:
            moved = dict(detection)
            moved["figure_number"] = matching_caption["figure_number"]
            moved["excluded_reason"] = "figure_caption_identifier"
            removed.append(moved)

    if not removed:
        return 0
    result["detections"] = kept
    result["figure_caption_component_detections"] = removed
    labels = [item.get("label", item.get("number", "")) for item in kept]
    result["numbers"] = labels
    result["labels"] = labels
    result["number_count"] = len(labels)
    if result.get("result_text"):
        updated_lines = []
        for line in str(result["result_text"]).splitlines():
            if line.startswith("偵測到標號數量："):
                line = f"偵測到標號數量：{len(labels)}"
            elif line.startswith("組合後標號列表："):
                line = f"組合後標號列表：{', '.join(labels) if labels else '無'}"
            updated_lines.append(line)
        result["result_text"] = "\n".join(updated_lines)
    return len(removed)
