from pathlib import Path

import cv2
import torch

from app.config import (
    YOLO_CONF,
    OCR_CONF,
    IMG_SIZE,
    PAD,
    Y_TOLERANCE,
    MAX_X_GAP,
    MAX_LABEL_LENGTH,
    RECOGNITION_MODE
)

from features.patent_ocr.class_map import (
    get_default_class_map,
    map_yolo_class_to_char,
    is_yolo_char_model
)

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

    if not base_items:
        return []

    base_items = sorted(base_items, key=lambda d: d["yc"])

    rows = []

    for item in base_items:
        placed = False

        for row in rows:
            row_y_avg = sum(d["yc"] for d in row) / len(row)

            if abs(item["yc"] - row_y_avg) <= y_tolerance:
                row.append(item)
                placed = True
                break

        if not placed:
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

            prev_width = prev["x2"] - prev["x1"]
            item_width = item["x2"] - item["x1"]
            avg_width = (prev_width + item_width) / 2

            dynamic_gap = max(max_x_gap, avg_width * 1.2)

            if gap <= dynamic_gap:
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

        results.append({
            "label": label_text,
            "number": label_text,
            "x1": min(d["x1"] for d in group),
            "y1": min(d["y1"] for d in group),
            "x2": max(d["x2"] for d in group),
            "y2": max(d["y2"] for d in group),
            "chars": group
        })

    results = sorted(results, key=lambda r: (r["y1"], r["x1"]))

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

    img = cv2.imread(str(image_path))

    if img is None:
        raise ValueError(f"圖片讀取失敗：{image_path}")

    img_h, img_w = img.shape[:2]

    compute_device = get_compute_device()

    results = model.predict(
        source=str(image_path),
        imgsz=imgsz,
        conf=yolo_conf,
        device=compute_device,
        verbose=False
    )

    boxes = results[0].boxes

    char_items = []

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

            else:
                if reader is None:
                    raise RuntimeError("EasyOCR reader 尚未載入，無法辨識 digit。")

                processed_roi = preprocess_roi(roi)

                ocr_result = reader.readtext(
                    processed_roi,
                    allowlist="0123456789",
                    paragraph=False
                )

                for det in ocr_result:
                    text = det[1]
                    confidence = float(det[2])
                    clean_num = clean_ocr_text(text)

                    if len(clean_num) == 1 and confidence > best_conf:
                        best_text = clean_num
                        best_conf = confidence

            if best_text and best_conf >= ocr_conf:
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

    grouped_results = group_chars_to_labels(
        char_items,
        y_tolerance=y_tolerance,
        max_x_gap=max_x_gap,
        max_label_length=MAX_LABEL_LENGTH
    )

    final_labels = [r["label"] for r in grouped_results]

    recognition_mode_text = "YOLO 字元模型" if use_yolo_class_as_char else "YOLO + EasyOCR"

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
        "result_text": result_text
    }