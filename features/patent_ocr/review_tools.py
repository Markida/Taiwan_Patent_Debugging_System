"""Shared confidence and review helpers for the two-stage OCR workflow."""

from pathlib import Path


def detection_confidence(detection):
    """Return the conservative confidence for one complete reference label."""

    if detection.get("confidence") is not None:
        return max(0.0, min(1.0, float(detection["confidence"])))

    characters = detection.get("chars") or []
    if characters:
        values = []
        for character in characters:
            ocr_conf = float(character.get("ocr_conf", 0.0))
            yolo_conf = float(character.get("yolo_conf", ocr_conf))
            values.append(min(ocr_conf, yolo_conf))
        if values:
            return max(0.0, min(1.0, min(values)))

    available = [
        float(detection[key])
        for key in ("ocr_conf", "yolo_conf")
        if detection.get(key) is not None
    ]
    if not available:
        return 0.0
    return max(0.0, min(1.0, min(available)))


def recognition_quality_score(result):
    """Score one orientation by rewarding multiple high-confidence labels."""

    return sum(
        detection_confidence(detection) ** 2
        for detection in result.get("detections", [])
        if detection.get("label") or detection.get("number")
    )


def should_use_rotated_result(original_result, rotated_result, margin=0.03):
    """Prefer rotation only when it is materially better than the original."""

    original_score = recognition_quality_score(original_result)
    rotated_score = recognition_quality_score(rotated_result)
    return rotated_score > original_score + float(margin)


def orientation_path_key(image_path):
    """Normalize an image path for stable manual-orientation comparisons."""

    return str(Path(image_path).resolve()).casefold()


def is_manual_rotation_output(image_path):
    """Recognize images created by the app's manual rotation command."""

    image_path = Path(image_path)
    return (
        image_path.parent.name.casefold() == "rotated_images"
        and "_rot90" in image_path.stem.casefold()
    )


def is_manually_oriented(image_path, manual_orientation_paths=None):
    """Return whether the user has explicitly chosen this image orientation."""

    manual_keys = {
        orientation_path_key(path)
        for path in (manual_orientation_paths or [])
    }
    return (
        orientation_path_key(image_path) in manual_keys
        or is_manual_rotation_output(image_path)
    )
