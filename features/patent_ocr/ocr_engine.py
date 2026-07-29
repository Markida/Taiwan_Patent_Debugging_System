from pathlib import Path
from statistics import median

import cv2
import torch

from app.config import (
    YOLO_CONF,
    V3_YOLO_CONF,
    YOLO_IOU,
    OCR_CONF,
    IMG_SIZE,
    PAD,
    OCR_ALLOWLIST,
    OCR_CHARACTER_MIN_CONFIDENCE,
    V3_CHARACTER_MIN_CONFIDENCE,
    OCR_LABEL_CHARACTER_MIN_CONFIDENCE,
    J_MIN_DIGIT_HEIGHT_RATIO,
    Y_TOLERANCE,
    MAX_X_GAP,
    MAX_LABEL_LENGTH,
    RECOGNITION_MODE
)

from features.patent_ocr.class_map import (
    get_default_class_map,
    map_yolo_class_to_char,
    is_yolo_char_model,
    is_yolo_label_model,
)
from features.patent_ocr.image_io import read_image

from features.patent_ocr.label_parser import normalize_label_text


def get_compute_device():
    """
    自動判斷目前電腦是否可使用 CUDA。
    """

    try:
        if torch.cuda.is_available() and torch.cuda.device_count() > 0:
            return 0
    except Exception:
        pass

    return "cpu"


def get_easyocr_gpu_flag():
    """
    EasyOCR 的 gpu 參數是 True / False。
    """

    try:
        return torch.cuda.is_available() and torch.cuda.device_count() > 0
    except Exception:
        return False


def get_device_status_text():
    """
    顯示目前使用 CPU 或 GPU。
    """

    device = get_compute_device()

    if device == "cpu":
        return "CPU 模式"

    try:
        gpu_name = torch.cuda.get_device_name(0)
        return f"GPU 模式：{gpu_name}"
    except Exception:
        return "GPU 模式"


def should_use_yolo_class_as_char(model, class_map):
    """
    依照 RECOGNITION_MODE 決定辨識模式。
    """

    if RECOGNITION_MODE == "yolo_char":
        return True

    if RECOGNITION_MODE == "easyocr":
        return False

    return is_yolo_char_model(model, class_map)


def clean_ocr_text(text):
    """
    將 OCR 結果清理成純數字。
    舊模型 fallback 模式使用。
    """

    text = str(text)

    text = text.replace("O", "0")
    text = text.replace("o", "0")
    text = text.replace("I", "1")
    text = text.replace("l", "1")
    text = text.replace("|", "1")

    return "".join(c for c in text if c.isdigit())


def clean_ocr_character(text, allowlist=OCR_ALLOWLIST):
    """Normalize one OCR character while preserving enabled letters/prime."""

    allowlist = str(allowlist)

    if allowlist and all(character.isdigit() for character in allowlist):
        return clean_ocr_text(text)

    normalized = str(text).strip()
    normalized = normalized.replace("’", "'").replace("′", "'").replace("`", "'")
    cleaned = []
    for character in normalized:
        if character in allowlist:
            cleaned.append(character)
        elif character.upper() in allowlist and character.lower() not in allowlist:
            cleaned.append(character.upper())
        elif character.lower() in allowlist and character.upper() not in allowlist:
            cleaned.append(character.lower())
    return "".join(cleaned)


def is_v3_case_sensitive_char_model(model, use_yolo_class_as_char):
    model_names = model.names
    class_names = (
        list(model_names.values())
        if isinstance(model_names, dict)
        else list(model_names)
    )
    return (
        use_yolo_class_as_char
        and len(class_names) == 63
        and any(
            len(str(name)) == 1 and str(name).islower()
            for name in class_names
        )
    )


def resolve_yolo_confidence(model, use_yolo_class_as_char, default=YOLO_CONF):
    """Use the calibrated threshold only for the 63-class v3 character model."""

    return (
        V3_YOLO_CONF
        if is_v3_case_sensitive_char_model(model, use_yolo_class_as_char)
        else float(default)
    )


def preprocess_roi(roi):
    """
    將單一 ROI 做灰階、二值化、放大。
    舊模型 fallback 模式使用。
    """

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    thresh = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        11,
        2
    )

    resized = cv2.resize(
        thresh,
        None,
        fx=4,
        fy=4,
        interpolation=cv2.INTER_CUBIC
    )

    return resized


def preprocess_roi_variants(roi):
    """Build complementary single-angle OCR inputs for a YOLO character crop.

    Raw grayscale with a white border preserves thin strokes.  Adaptive
    thresholding remains the fallback for crops affected by drawing lines.
    """

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    raw_resized = cv2.resize(
        gray,
        None,
        fx=4,
        fy=4,
        interpolation=cv2.INTER_CUBIC,
    )
    raw_with_border = cv2.copyMakeBorder(
        raw_resized,
        30,
        30,
        30,
        30,
        cv2.BORDER_CONSTANT,
        value=255,
    )
    return (raw_with_border, preprocess_roi(roi))


def _best_easyocr_character(results, allowlist="0123456789"):
    """Return the best single digit from EasyOCR's standard result tuples."""

    best_text = ""
    best_conf = 0.0

    for detection in results or []:
        if len(detection) < 3:
            continue

        text = clean_ocr_character(detection[1], allowlist=allowlist)
        confidence = float(detection[2])

        if len(text) == 1 and confidence > best_conf:
            best_text = text
            best_conf = confidence

    return best_text, best_conf


def _best_easyocr_label(
    results,
    allowlist=OCR_ALLOWLIST,
    max_label_length=MAX_LABEL_LENGTH,
):
    """Return the best complete patent label from EasyOCR result tuples."""

    best_text = ""
    best_conf = 0.0
    for detection in results or []:
        if len(detection) < 3:
            continue
        text = normalize_label_text(
            clean_ocr_character(detection[1], allowlist=allowlist)
        )
        confidence = float(detection[2])
        if 1 <= len(text) <= int(max_label_length) and confidence > best_conf:
            best_text = text
            best_conf = confidence
    return best_text, best_conf


def select_easyocr_label_candidate(candidates):
    """Choose between raw and thresholded group OCR candidates.

    Roman numerals benefit from preserving every thin ``I`` stroke, so a
    slightly longer pure Roman candidate receives a small score bonus.
    Other label types continue to use OCR confidence directly.
    """

    candidates = [
        (text, float(confidence))
        for text, confidence in candidates
        if text
    ]
    if not candidates:
        return "", 0.0

    roman_like = all(
        len(text) >= 2 and set(text) <= set("IVXTL")
        for text, _confidence in candidates
    )

    def score(candidate):
        text, confidence = candidate
        if roman_like:
            return confidence + 0.10 * len(text) + (
                0.05 if set(text) <= set("IVX") else 0.0
            )
        return confidence

    return max(candidates, key=score)


def count_roman_i_strokes(roi, minimum_height_ratio=0.65):
    """Count full-height vertical ``I`` strokes in a tightly detected group.

    The diagonal sides of ``V`` do not span most rows in any single column,
    while each adjacent ``I`` does. This makes the projection conservative and
    independent of the absolute character size.
    """

    if roi is None or roi.size == 0:
        return 0
    if len(roi.shape) == 3:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    else:
        gray = roi
    _threshold, ink = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )
    height, _width = ink.shape[:2]
    minimum_ink = max(3, int(round(height * float(minimum_height_ratio))))
    tall_columns = (ink > 0).sum(axis=0) >= minimum_ink

    runs = []
    start = None
    for index, is_tall in enumerate(list(tall_columns) + [False]):
        if is_tall and start is None:
            start = index
        elif not is_tall and start is not None:
            runs.append((start, index - 1))
            start = None

    maximum_stroke_width = max(2, int(round(height * 0.25)))
    return sum(
        1
        for start, end in runs
        if 1 <= end - start + 1 <= maximum_stroke_width
    )


def recognize_easyocr_character(
    reader,
    processed_roi,
    min_confidence=OCR_CONF,
    allowlist=OCR_ALLOWLIST,
):
    """Recognize a YOLO-cropped character without detecting text a second time.

    The YOLO box already describes the text region.  EasyOCR ``recognize`` can
    therefore consume the full ROI directly.  The older ``readtext`` path is
    retained as a fallback for difficult crops and older EasyOCR versions.
    No rotation candidates are used here because page orientation is controlled
    by the UI before recognition.
    """

    best_text = ""
    best_conf = 0.0

    if hasattr(reader, "recognize"):
        height, width = processed_roi.shape[:2]

        try:
            results = reader.recognize(
                processed_roi,
                horizontal_list=[[0, width, 0, height]],
                free_list=[],
                allowlist=allowlist,
                detail=1,
                rotation_info=None,
                paragraph=False,
            )
            best_text, best_conf = _best_easyocr_character(
                results,
                allowlist=allowlist,
            )
        except (AttributeError, TypeError, ValueError):
            # Keep compatibility with older EasyOCR releases/readers.
            best_text = ""
            best_conf = 0.0

    if best_text and best_conf >= min_confidence:
        return best_text, best_conf

    if not getattr(reader, "patent_detector_fallback_enabled", True):
        return best_text, best_conf

    fallback_results = reader.readtext(
        processed_roi,
        allowlist=allowlist,
        paragraph=False,
    )
    fallback_text, fallback_conf = _best_easyocr_character(
        fallback_results,
        allowlist=allowlist,
    )

    if fallback_conf > best_conf:
        return fallback_text, fallback_conf

    return best_text, best_conf


def recognize_easyocr_label(
    reader,
    processed_roi,
    min_confidence=OCR_CONF,
    allowlist=OCR_ALLOWLIST,
    max_label_length=MAX_LABEL_LENGTH,
):
    """Recognize a complete label inside a group-level YOLO crop."""

    best_text = ""
    best_conf = 0.0
    if hasattr(reader, "recognize"):
        height, width = processed_roi.shape[:2]
        try:
            results = reader.recognize(
                processed_roi,
                horizontal_list=[[0, width, 0, height]],
                free_list=[],
                allowlist=allowlist,
                detail=1,
                rotation_info=None,
                paragraph=False,
            )
            best_text, best_conf = _best_easyocr_label(
                results,
                allowlist=allowlist,
                max_label_length=max_label_length,
            )
        except (AttributeError, TypeError, ValueError):
            best_text = ""
            best_conf = 0.0

    if best_text and best_conf >= min_confidence:
        return best_text, best_conf

    if not getattr(reader, "patent_detector_fallback_enabled", True):
        return best_text, best_conf

    fallback_results = reader.readtext(
        processed_roi,
        allowlist=allowlist,
        paragraph=False,
    )
    fallback_text, fallback_conf = _best_easyocr_label(
        fallback_results,
        allowlist=allowlist,
        max_label_length=max_label_length,
    )
    if fallback_conf > best_conf:
        return fallback_text, fallback_conf
    return best_text, best_conf


def meets_character_confidence(
    text,
    confidence,
    min_confidence=OCR_CONF,
    character_minimums=OCR_CHARACTER_MIN_CONFIDENCE,
):
    """Apply a stricter threshold to known high-risk character confusions."""

    required_confidence = max(
        float(min_confidence),
        float(
            character_minimums.get(
                text,
                character_minimums.get(str(text).upper(), min_confidence),
            )
        ),
    )
    return bool(text) and float(confidence) >= required_confidence


def required_label_confidence(
    text,
    min_confidence=OCR_CONF,
    character_minimums=OCR_LABEL_CHARACTER_MIN_CONFIDENCE,
):
    """Return the strictest OCR threshold required by a complete label."""

    normalized_text = normalize_label_text(text)
    return max(
        [float(min_confidence)]
        + [
            float(character_minimums[character.upper()])
            for character in normalized_text
            if character.upper() in character_minimums
        ]
    )


def meets_label_confidence(
    text,
    confidence,
    min_confidence=OCR_CONF,
    character_minimums=OCR_LABEL_CHARACTER_MIN_CONFIDENCE,
):
    """Reject high-risk letters such as false J strokes unless confident."""

    return bool(text) and float(confidence) >= required_label_confidence(
        text,
        min_confidence=min_confidence,
        character_minimums=character_minimums,
    )


def filter_small_j_detections(
    detections,
    min_digit_height_ratio=J_MIN_DIGIT_HEIGHT_RATIO,
):
    """Remove J labels that are materially shorter than numbers on the page."""

    detections = list(detections or [])
    digit_heights = [
        max(0, float(item.get("y2", 0)) - float(item.get("y1", 0)))
        for item in detections
        if "J" not in str(item.get("label", "")).upper()
        and any(character.isdigit() for character in str(item.get("label", "")))
    ]
    digit_heights = [height for height in digit_heights if height > 0]
    if not digit_heights:
        return detections, []

    reference_height = float(median(digit_heights))
    minimum_j_height = reference_height * float(min_digit_height_ratio)
    accepted = []
    rejected = []
    for item in detections:
        label = normalize_label_text(item.get("label", item.get("number", "")))
        height = max(
            0.0,
            float(item.get("y2", 0)) - float(item.get("y1", 0)),
        )
        if "J" in label.upper() and height < minimum_j_height:
            rejected_item = dict(item)
            rejected_item["original_label"] = label
            rejected_item["auto_filtered"] = True
            rejected_item["rejection_reason"] = "j_smaller_than_numeric_labels"
            rejected_item["j_height"] = round(height, 3)
            rejected_item["reference_digit_height"] = round(reference_height, 3)
            rejected_item["minimum_height_ratio"] = float(min_digit_height_ratio)
            rejected.append(rejected_item)
        else:
            accepted.append(item)
    return accepted, rejected


def group_chars_to_labels(
    char_items,
    y_tolerance=15,
    max_x_gap=35,
    max_label_length=8
):
    """
    將單一字元依照 y 軸高度與 x 軸距離組合成完整標號。
    """

    if not char_items:
        return []

    prime_items = [
        item for item in char_items
        if item.get("char") == "'"
    ]

    base_items = [
        item for item in char_items
        if item.get("char") != "'"
    ]

    for item in base_items:
        item["suffix"] = ""

    for prime in prime_items:
        candidates = []

        for base in base_items:
            dx = prime["xc"] - base["xc"]
            dy = abs(prime["yc"] - base["yc"])

            if -5 <= dx <= max_x_gap * 2.2 and dy <= y_tolerance * 2.5:
                distance = (dx ** 2 + dy ** 2) ** 0.5
                candidates.append((distance, base))

        if candidates:
            candidates.sort(key=lambda x: x[0])
            nearest_base = candidates[0][1]

            if "'" not in nearest_base["suffix"]:
                nearest_base["suffix"] += "'"
                nearest_base.setdefault("suffix_items", []).append(prime)

    if not base_items:
        return []

    base_items = sorted(base_items, key=lambda d: d["yc"])

    rows = []

    for item in base_items:
        item_height = max(1.0, float(item["y2"] - item["y1"]))
        matching_rows = []
        for row in rows:
            row_center = median(float(d["yc"]) for d in row)
            row_height = median(
                max(1.0, float(d["y2"] - d["y1"]))
                for d in row
            )
            center_distance = abs(float(item["yc"]) - row_center)
            adaptive_y_tolerance = min(
                float(y_tolerance) * 2.0,
                max(
                    float(y_tolerance),
                    0.35 * max(item_height, row_height),
                ),
            )
            row_top = median(float(d["y1"]) for d in row)
            row_bottom = median(float(d["y2"]) for d in row)
            overlap = max(
                0.0,
                min(float(item["y2"]), row_bottom)
                - max(float(item["y1"]), row_top),
            )
            overlap_ratio = overlap / max(
                1.0,
                min(item_height, row_bottom - row_top),
            )

            if (
                center_distance <= adaptive_y_tolerance
                and overlap_ratio >= 0.35
            ):
                matching_rows.append((center_distance, row))

        if matching_rows:
            min(matching_rows, key=lambda candidate: candidate[0])[1].append(item)
        else:
            rows.append([item])

    grouped_char_lists = []

    for row in rows:
        row = sorted(row, key=lambda d: d["x1"])

        current_group = []

        for item in row:
            if not current_group:
                current_group.append(item)
                continue

            prev = current_group[-1]

            gap = item["x1"] - prev["x2"]
            pair_height = median([
                max(1.0, float(prev["y2"] - prev["y1"])),
                max(1.0, float(item["y2"] - item["y1"])),
            ])
            adaptive_x_gap = min(
                float(max_x_gap) * 1.5,
                max(float(max_x_gap), pair_height * 0.30),
            )

            if gap <= adaptive_x_gap:
                current_group.append(item)
            else:
                grouped_char_lists.append(current_group)
                current_group = [item]

        if current_group:
            grouped_char_lists.append(current_group)

    results = []

    for group in grouped_char_lists:
        group = sorted(group, key=lambda d: d["x1"])

        label_text = ""

        for d in group:
            label_text += d.get("char", "")
            label_text += d.get("suffix", "")

        label_text = normalize_label_text(label_text)

        if not label_text:
            continue

        if label_text == "'":
            continue

        if len(label_text) > max_label_length:
            continue

        confidence_items = list(group)
        for character in group:
            confidence_items.extend(character.get("suffix_items", []))
        confidence_values = [
            min(
                float(character.get("ocr_conf", 0.0)),
                float(character.get("yolo_conf", character.get("ocr_conf", 0.0))),
            )
            for character in confidence_items
        ]

        results.append({
            "label": label_text,
            "number": label_text,
            "x1": min(d["x1"] for d in confidence_items),
            "y1": min(d["y1"] for d in confidence_items),
            "x2": max(d["x2"] for d in confidence_items),
            "y2": max(d["y2"] for d in confidence_items),
            "chars": confidence_items,
            "confidence": min(confidence_values) if confidence_values else 0.0,
        })

    # Rows and their groups are already built top-to-bottom and left-to-right.
    # Re-sorting by the exact y1 coordinate can reverse items on the same row
    # when their boxes differ vertically by only one or two pixels.
    return results


def recognize_one_image(
    image_path,
    model,
    reader,
    model_name,
    class_map=None,
    use_yolo_class_as_char=False,
    yolo_conf=YOLO_CONF,
    ocr_conf=OCR_CONF,
    imgsz=IMG_SIZE,
    pad=PAD,
    y_tolerance=Y_TOLERANCE,
    max_x_gap=MAX_X_GAP,
):
    """
    單張圖片辨識核心。

    新版模型：
    直接讀 YOLO class name。

    舊版模型：
    YOLO 抓框 + EasyOCR 讀數字。
    """

    image_path = Path(image_path)

    img = read_image(image_path)

    if img is None:
        raise ValueError(f"圖片讀取失敗：{image_path}")

    img_h, img_w = img.shape[:2]

    compute_device = get_compute_device()

    effective_yolo_conf = resolve_yolo_confidence(
        model,
        use_yolo_class_as_char,
        default=yolo_conf,
    )
    is_v3_case_sensitive_model = is_v3_case_sensitive_char_model(
        model,
        use_yolo_class_as_char,
    )
    character_minimums = (
        V3_CHARACTER_MIN_CONFIDENCE
        if is_v3_case_sensitive_model
        else OCR_CHARACTER_MIN_CONFIDENCE
    )
    character_acceptance_confidence = (
        effective_yolo_conf
        if is_v3_case_sensitive_model
        else ocr_conf
    )

    results = model.predict(
        source=str(image_path),
        imgsz=imgsz,
        conf=effective_yolo_conf,
        iou=YOLO_IOU,
        device=compute_device,
        verbose=False
    )

    boxes = results[0].boxes

    char_items = []
    label_items = []
    rejected_items = []
    use_yolo_label_groups = is_yolo_label_model(model)

    if boxes is not None and len(boxes) > 0:

        detected_boxes = []

        for box in boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            box_conf = float(box.conf[0])
            cls_id = int(box.cls[0])

            detected_boxes.append((x1, y1, x2, y2, box_conf, cls_id))

        detected_boxes = sorted(detected_boxes, key=lambda b: (b[1], b[0]))

        for x1, y1, x2, y2, box_conf, cls_id in detected_boxes:

            px1 = max(0, x1 - pad)
            py1 = max(0, y1 - pad)
            px2 = min(img_w, x2 + pad)
            py2 = min(img_h, y2 + pad)

            roi = img[py1:py2, px1:px2]

            if roi.size == 0:
                continue

            best_text = ""
            best_conf = 0.0

            if use_yolo_class_as_char:
                class_name = str(model.names[cls_id])
                mapped_char = map_yolo_class_to_char(
                    class_name,
                    class_map or get_default_class_map()
                )

                if mapped_char:
                    best_text = mapped_char
                    best_conf = box_conf

            elif use_yolo_label_groups:
                if reader is None:
                    raise RuntimeError(
                        "EasyOCR reader is required for label-group models."
                    )

                processed_rois = preprocess_roi_variants(roi)
                label_candidates = []
                for processed_roi in processed_rois:
                    candidate_text, candidate_conf = recognize_easyocr_label(
                        reader,
                        processed_roi,
                        min_confidence=ocr_conf,
                        allowlist=OCR_ALLOWLIST,
                    )
                    label_candidates.append((candidate_text, candidate_conf))

                selected_text, selected_conf = select_easyocr_label_candidate(
                    label_candidates
                )
                selected_required_confidence = required_label_confidence(
                    selected_text,
                    min_confidence=ocr_conf,
                )
                if (
                    selected_text
                    and selected_required_confidence > float(ocr_conf)
                    and selected_conf < selected_required_confidence
                ):
                    rejected_items.append({
                        "label": selected_text,
                        "original_label": selected_text,
                        "x1": px1,
                        "y1": py1,
                        "x2": px2,
                        "y2": py2,
                        "ocr_conf": selected_conf,
                        "yolo_conf": box_conf,
                        "confidence": min(selected_conf, box_conf),
                        "auto_filtered": True,
                        "rejection_reason": "high_risk_character_confidence",
                    })
                    label_candidates = [
                        candidate
                        for candidate in label_candidates
                        if meets_label_confidence(
                            candidate[0],
                            candidate[1],
                            min_confidence=ocr_conf,
                        )
                    ]
                    best_text, best_conf = select_easyocr_label_candidate(
                        label_candidates
                    )
                else:
                    best_text, best_conf = selected_text, selected_conf

                # EasyOCR occasionally reads the first stroke of III as T or
                # L. Only for this narrow ambiguous pattern, retry with the
                # Roman alphabet so normal letter labels remain untouched.
                roman_strokes = sum(character in "IVX" for character in best_text)
                if (
                    roman_strokes >= 2
                    and any(character in "TL" for character in best_text)
                ):
                    roman_candidates = []
                    for processed_roi in processed_rois:
                        roman_candidates.append(
                            recognize_easyocr_label(
                                reader,
                                processed_roi,
                                min_confidence=0.0,
                                allowlist="IVX",
                            )
                        )
                    roman_text, roman_conf = select_easyocr_label_candidate(
                        roman_candidates
                    )
                    if (
                        len(roman_text) >= 2
                        and set(roman_text) <= set("IVX")
                        and roman_conf >= max(0.70, best_conf)
                    ):
                        best_text = roman_text
                        best_conf = roman_conf

                if best_text in {"V", "VI", "VII", "VIII"}:
                    i_strokes = count_roman_i_strokes(roi)
                    if 1 <= i_strokes <= 3 and i_strokes > best_text.count("I"):
                        best_text = "V" + "I" * i_strokes

            else:
                if reader is None:
                    raise RuntimeError("EasyOCR reader 尚未載入，無法辨識 digit。")

                for processed_roi in preprocess_roi_variants(roi):
                    candidate_text, candidate_conf = recognize_easyocr_character(
                        reader,
                        processed_roi,
                        min_confidence=ocr_conf,
                        allowlist=OCR_ALLOWLIST,
                    )

                    if (
                        meets_character_confidence(
                            candidate_text,
                            candidate_conf,
                            min_confidence=ocr_conf,
                        )
                        and candidate_conf > best_conf
                    ):
                        best_text = candidate_text
                        best_conf = candidate_conf

                    if best_text and best_conf >= ocr_conf:
                        break

            if use_yolo_label_groups and best_text and best_conf >= ocr_conf:
                normalized_text = normalize_label_text(best_text)
                if normalized_text:
                    label_items.append({
                        "label": normalized_text,
                        "number": normalized_text,
                        "x1": px1,
                        "y1": py1,
                        "x2": px2,
                        "y2": py2,
                        "ocr_conf": best_conf,
                        "yolo_conf": box_conf,
                        "confidence": min(best_conf, box_conf),
                    })
            elif (
                best_text
                and best_conf >= character_acceptance_confidence
                and meets_character_confidence(
                    best_text,
                    best_conf,
                    min_confidence=character_acceptance_confidence,
                    character_minimums=character_minimums,
                )
            ):
                char_items.append({
                    "char": best_text,
                    "x1": px1,
                    "y1": py1,
                    "x2": px2,
                    "y2": py2,
                    "xc": (px1 + px2) / 2,
                    "yc": (py1 + py2) / 2,
                    "ocr_conf": best_conf,
                    "yolo_conf": box_conf
                })

    if use_yolo_label_groups:
        grouped_results = sorted(
            label_items,
            key=lambda item: (item["y1"], item["x1"]),
        )
    else:
        grouped_results = group_chars_to_labels(
            char_items,
            y_tolerance=y_tolerance,
            max_x_gap=max_x_gap,
            max_label_length=MAX_LABEL_LENGTH
        )

    grouped_results, small_j_items = filter_small_j_detections(grouped_results)
    rejected_items.extend(small_j_items)

    final_labels = [r["label"] for r in grouped_results]

    if use_yolo_class_as_char:
        recognition_mode_text = "YOLO 字元模型"
    elif use_yolo_label_groups:
        recognition_mode_text = "YOLO 整組標號 + EasyOCR"
    else:
        recognition_mode_text = "YOLO + EasyOCR"

    result_text = (
        f"圖片名稱：{image_path.name}\n"
        f"使用模型：{model_name}\n"
        f"辨識模式：{recognition_mode_text}\n"
        f"運算模式：{get_device_status_text()}\n"
        f"偵測到標號數量：{len(final_labels)}\n"
        f"組合後標號列表：{', '.join(final_labels) if final_labels else '無'}"
    )

    return {
        "image_path": str(image_path),
        "image_name": image_path.name,
        "model_name": model_name,
        "number_count": len(final_labels),
        "numbers": final_labels,
        "labels": final_labels,
        "detections": grouped_results,
        "rejected_detections": rejected_items,
        "result_text": result_text
    }
