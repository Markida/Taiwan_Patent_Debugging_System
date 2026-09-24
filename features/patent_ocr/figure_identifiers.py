"""Shared parsing rules for patent drawing figure identifiers."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Tuple, Union


FigureIdentifier = Union[int, str]

# Taiwanese patent drawings normally use a positive number, sometimes with an
# alphabetic suffix or a prime.  Pure alphabetic identifiers also occur in
# supplied drawings, so the same grammar is shared by OCR, the editable page
# mapping, and the downstream embodiment comparison.
FIGURE_IDENTIFIER_EXPRESSION = (
    r"(?:\d+[A-Za-z]*|[A-Za-z]+)(?:['\u2032\u2019])?"
)
MAX_FIGURE_RANGE_SIZE = 100


def normalize_figure_identifier(value: object) -> FigureIdentifier:
    """Normalize one identifier while preserving letters and a final prime."""

    token = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
    token = token.replace("\u2032", "'").replace("\u2019", "'").replace("`", "'")
    match = re.fullmatch(
        r"(?:(?P<number>\d+)(?P<suffix>[A-Z]*)(?P<prime>'?)|"
        r"(?P<letters>[A-Z]+)(?P<letter_prime>'?))",
        token,
    )
    if match is None:
        raise ValueError(f"\u300c{value}\u300d\u4e0d\u662f\u6709\u6548\u5716\u865f\u3002")

    number_text = match.group("number")
    if number_text is not None:
        number = int(number_text)
        if number <= 0:
            raise ValueError(f"\u300c{value}\u300d\u4e0d\u662f\u6709\u6548\u5716\u865f\u3002")
        suffix = match.group("suffix") or ""
        prime = match.group("prime") or ""
        return f"{number}{suffix}{prime}" if suffix or prime else number

    return f"{match.group('letters')}{match.group('letter_prime') or ''}"


def ordered_unique_figures(
    values: Iterable[FigureIdentifier],
) -> List[FigureIdentifier]:
    output: List[FigureIdentifier] = []
    seen = set()
    for value in values:
        normalized = normalize_figure_identifier(value)
        key = str(normalized).upper()
        if key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return output


def figure_sort_key(value: FigureIdentifier) -> Tuple[int, int, str, int]:
    text = str(normalize_figure_identifier(value)).upper()
    match = re.fullmatch(r"(?P<number>\d+)(?P<suffix>[A-Z]*)(?P<prime>'?)", text)
    if match is not None:
        return (
            0,
            int(match.group("number")),
            match.group("suffix"),
            1 if match.group("prime") else 0,
        )
    return (1, 0, text.rstrip("'"), 1 if text.endswith("'") else 0)


def expand_figure_identifier_range(
    start_value: object,
    end_value: object,
) -> List[FigureIdentifier]:
    """Expand a numeric range or one same-base single-letter suffix range."""

    start = normalize_figure_identifier(start_value)
    end = normalize_figure_identifier(end_value)
    if isinstance(start, int) and isinstance(end, int):
        if end < start:
            raise ValueError(
                f"圖號區間「{start_value}-{end_value}」不是有效的遞增範圍。"
            )
        if end - start + 1 > MAX_FIGURE_RANGE_SIZE:
            raise ValueError(
                f"圖號區間一次最多 {MAX_FIGURE_RANGE_SIZE} 張。"
            )
        return list(range(start, end + 1))

    start_match = re.fullmatch(r"(?P<number>\d+)(?P<suffix>[A-Z])", str(start))
    end_match = re.fullmatch(r"(?P<number>\d+)(?P<suffix>[A-Z])", str(end))
    if (
        start_match is None
        or end_match is None
        or start_match.group("number") != end_match.group("number")
    ):
        raise ValueError(
            "英文字尾圖號區間必須使用相同數字及單一字母，例如 8A-8E。"
        )

    start_suffix = ord(start_match.group("suffix"))
    end_suffix = ord(end_match.group("suffix"))
    if end_suffix < start_suffix:
        raise ValueError(
            f"圖號區間「{start_value}-{end_value}」不是有效的遞增範圍。"
        )
    if end_suffix - start_suffix + 1 > MAX_FIGURE_RANGE_SIZE:
        raise ValueError(
            f"圖號區間一次最多 {MAX_FIGURE_RANGE_SIZE} 張。"
        )
    number = start_match.group("number")
    return [
        f"{number}{chr(suffix)}"
        for suffix in range(start_suffix, end_suffix + 1)
    ]


def parse_figure_number_mapping(text: object) -> List[FigureIdentifier]:
    """Parse mappings such as ``1,2``, ``1-4``, ``8A-8E`` or ``1'``."""

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = normalized.replace("\u2032", "'").replace("\u2019", "'")
    normalized = re.sub(r"\u5716\s*", "", normalized)
    normalized = re.sub(r"(?:\u4ee5\u53ca|\u53ca|\u8207|\u548c)", ",", normalized)
    tokens = [token.strip() for token in re.split(r"[,\uff0c\u3001]", normalized)]
    if not tokens or any(not token for token in tokens):
        raise ValueError(
            "\u8acb\u8f38\u5165\u5716\u865f\uff0c\u4f8b\u5982 1\u30011,2\u30011-4\u30018A-8E \u6216 1'\u3002"
        )

    figures: List[FigureIdentifier] = []
    for token in tokens:
        alpha_range = re.fullmatch(
            r"(?:(?P<start_number>\d+)(?P<start_suffix>[A-Za-z])|"
            r"(?P<start_letter>[A-Za-z]))\s*"
            r"(?:到|至|~|～|-)\s*"
            r"(?:(?P<end_number>\d+)(?P<end_suffix>[A-Za-z])|"
            r"(?P<end_letter>[A-Za-z]))",
            token,
        )
        if alpha_range is not None:
            start_number = alpha_range.group("start_number")
            end_number = alpha_range.group("end_number")
            start_letter = (
                alpha_range.group("start_suffix")
                or alpha_range.group("start_letter")
            ).upper()
            end_letter = (
                alpha_range.group("end_suffix")
                or alpha_range.group("end_letter")
            ).upper()
            if start_number != end_number:
                raise ValueError(f"圖號區間「{token}」必須使用相同數字主號。")
            start_code = ord(start_letter)
            end_code = ord(end_letter)
            if end_code < start_code:
                raise ValueError(f"圖號區間「{token}」不是有效的遞增範圍。")
            if end_code - start_code + 1 > MAX_FIGURE_RANGE_SIZE:
                raise ValueError(
                    f"圖號區間一次最多 {MAX_FIGURE_RANGE_SIZE} 張。"
                )
            prefix = str(int(start_number)) if start_number is not None else ""
            figures.extend(
                f"{prefix}{chr(code)}"
                for code in range(start_code, end_code + 1)
            )
            continue
        range_match = re.fullmatch(
            r"(?P<start>\d+[A-Za-z]*)\s*"
            r"(?:\u5230|\u81f3|~|\uff5e|-)\s*"
            r"(?P<end>\d+[A-Za-z]*)",
            token,
        )
        if range_match is not None:
            figures.extend(
                expand_figure_identifier_range(
                    range_match.group("start"),
                    range_match.group("end"),
                )
            )
            continue
        figures.append(normalize_figure_identifier(token))
    return ordered_unique_figures(figures)
