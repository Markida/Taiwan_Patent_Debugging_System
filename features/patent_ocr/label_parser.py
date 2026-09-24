import re
import unicodedata


_STANDARD_LABEL = re.compile(r"^[0-9A-Za-z]+(?:')?$")
_REFERENCE_SPECIAL_LABEL = re.compile(
    r"^[^\s:：.．,，、~～\-()（）]+$"
)


def normalize_label_text(label_text):
    """
    將標號正規化。

    支援：
    1
    01
    A
    7'
    10A
    """

    text = str(label_text).strip()

    text = text.replace("’", "'")
    text = text.replace("′", "'")
    text = text.replace("`", "'")

    text = re.sub(r"[^0-9A-Za-z']", "", text)

    # Prime is a suffix of any supported alphanumeric patent label. Real
    # symbol lists can contain 3', A', S1' and lowercase equivalents.
    # A leading or embedded mark is still treated as drawing noise.
    has_alphanumeric_prime_suffix = (
        text.endswith("'")
        and len(text) >= 2
        and text[:-1].isalnum()
    )
    text = text.replace("'", "")
    if has_alphanumeric_prime_suffix:
        text += "'"

    if text.isdigit():
        text = text.lstrip("0") or "0"

    return text


def normalize_reference_label_text(label_text):
    """Normalize document-list labels while preserving literal symbols."""

    text = re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", str(label_text).strip()),
    )
    text = text.replace("’", "'").replace("′", "'").replace("＇", "'")
    if _STANDARD_LABEL.fullmatch(text):
        return normalize_label_text(text)
    if (
        _REFERENCE_SPECIAL_LABEL.fullmatch(text)
        and re.search(r"[\u3400-\u9fff]", text) is None
    ):
        return text
    return ""


def parse_reference_items(input_text):
    """
    從使用者輸入的標號清單中擷取標號。

    支援格式：
    1:蓋子
    A:蓋子
    7':第一凸部
    10A:連接件
    """

    items = []
    seen_labels = set()

    for raw_line in input_text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        match = re.match(
            r"^\s*([^\s:：.．,，、~～\-()（）]+)\s*[:：]\s*(.*?)\s*$",
            line
        )

        if not match:
            match = re.match(
                r"^\s*([0-9A-Za-z]+(?:['’′])?)\s*(?:[、.．\)\）\-]|\s+)\s*(.*?)\s*$",
                line
            )

        if not match:
            match = re.match(
                r"^\s*([0-9A-Za-z]+(?:['’′])?)\s*$",
                line
            )

        if not match:
            continue

        label = normalize_reference_label_text(match.group(1))

        if not label:
            continue

        if label in seen_labels:
            continue

        name = ""

        if len(match.groups()) >= 2:
            name = match.group(2).strip()

        items.append({
            "number": label,
            "name": name
        })

        seen_labels.add(label)

    return items


def format_reference_label(number, reference_map):
    """
    將標號格式化成：
    1（蓋子）
    A（蓋子）
    7'（凸部）
    """

    name = reference_map.get(number, "")

    if name:
        return f"{number}（{name}）"

    return number
