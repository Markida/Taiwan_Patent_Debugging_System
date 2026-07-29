import json
from pathlib import Path

from app.paths import get_app_base_dir


def get_default_class_map():
    """
    預設 class map。
    如果 models/class_map.json 不存在，會使用這份。
    """

    class_map = {}

    for i in range(10):
        class_map[str(i)] = str(i)

    for code in range(ord("A"), ord("Z") + 1):
        letter = chr(code)
        class_map[letter] = letter
        class_map[letter.lower()] = letter.lower()

    class_map.update({
        "prime": "'",
        "Prime": "'",
        "PRIME": "'",
        "apostrophe": "'",
        "Apostrophe": "'",
        "APOSTROPHE": "'",
        "'": "'",
        "’": "'",
        "′": "'",
        "`": "'"
    })

    return class_map


def load_class_map(model_path=None):
    """
    載入 class_map.json。

    優先順序：
    1. 模型所在資料夾的 class_map.json
    2. App 資料夾 / models / class_map.json
    3. App 資料夾 / class_map.json
    4. 預設 class map
    """

    class_map = get_default_class_map()
    candidate_paths = []

    if model_path:
        model_path = Path(model_path)
        candidate_paths.append(model_path.parent / "class_map.json")

    app_base = get_app_base_dir()
    candidate_paths.append(app_base / "models" / "class_map.json")
    candidate_paths.append(app_base / "class_map.json")

    for path in candidate_paths:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    user_map = json.load(f)

                for key, value in user_map.items():
                    class_map[str(key)] = str(value)

                break

            except Exception:
                pass

    return class_map


def normalize_detected_char(text):
    """
    將模型輸出的 class name 轉成標準字元。
    """

    text = str(text).strip()

    if text in ["'", "’", "′", "`"]:
        return "'"

    if len(text) == 1 and text.isdigit():
        return text

    if len(text) == 1 and text.isalpha():
        return text

    if text.lower() in ["prime", "apostrophe"]:
        return "'"

    return ""


def map_yolo_class_to_char(class_name, class_map):
    """
    將 YOLO class name 透過 class_map 轉成實際字元。
    """

    class_name = str(class_name).strip()

    candidates = [
        class_name,
        class_name.upper(),
        class_name.lower()
    ]

    for candidate in candidates:
        if candidate in class_map:
            return normalize_detected_char(class_map[candidate])

    return normalize_detected_char(class_name)


def is_yolo_char_model(model, class_map):
    """
    判斷目前 YOLO 模型是否像是新版字元模型。
    """

    try:
        names = model.names

        if isinstance(names, dict):
            class_names = list(names.values())
        else:
            class_names = list(names)

        if not class_names:
            return False

        valid_count = 0

        for name in class_names:
            mapped = map_yolo_class_to_char(name, class_map)

            if mapped:
                valid_count += 1

        return valid_count == len(class_names) and valid_count >= 2

    except Exception:
        return False


def is_yolo_label_model(model):
    """Return whether a one-class model locates complete patent labels."""

    try:
        names = model.names
        class_names = list(names.values()) if isinstance(names, dict) else list(names)
        if len(class_names) != 1:
            return False
        normalized = str(class_names[0]).strip().lower().replace("-", "_")
        return normalized in {
            "patent_label",
            "patent_label_group",
            "label_group",
        }
    except Exception:
        return False
