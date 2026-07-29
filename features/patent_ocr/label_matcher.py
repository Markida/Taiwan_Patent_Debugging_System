from html import escape

from features.patent_ocr.label_parser import normalize_label_text


def _label_character_sort_key(label):
    """Sort labels one character at a time, preserving letter case."""

    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'"
    character_order = {
        character: index
        for index, character in enumerate(alphabet)
    }
    return tuple(
        character_order.get(character, len(alphabet) + ord(character))
        for character in normalize_label_text(label)
    )


def _unique_normalized_numbers(numbers, sort_numbers=False):
    normalized_numbers = []
    seen = set()

    for number in numbers:
        normalized = normalize_label_text(number)
        if not normalized or normalized in seen:
            continue
        normalized_numbers.append(normalized)
        seen.add(normalized)

    if sort_numbers:
        normalized_numbers.sort(key=_label_character_sort_key)
    return normalized_numbers


def _format_numbers(numbers):
    if not numbers:
        return "無"
    return escape(", ".join(numbers))


def _format_image_section(image_name, lines):
    content = "<br>".join(escape(line_name) + _format_numbers(numbers) for line_name, numbers in lines)
    return f"<div><strong>{escape(str(image_name))}</strong><br>{content}</div>"


def build_result_summary_html(
    all_results,
    reference_items,
    include_global_summary=True,
    sort_numbers=False,
):
    """Build the compact rich-text summary shown after batch recognition."""

    image_results = [
        (
            result.get("image_name", "未知圖片"),
            _unique_normalized_numbers(
                result.get("numbers", []),
                sort_numbers=sort_numbers,
            ),
        )
        for result in all_results
    ]

    separator = '<hr style="border: 0; border-top: 1px solid #9ca3af;">'

    if not reference_items:
        return separator.join(
            _format_image_section(
                image_name,
                [("偵測到的標號：", detected_numbers)],
            )
            for image_name, detected_numbers in image_results
        )

    expected_numbers = _unique_normalized_numbers(
        (item.get("number", "") for item in reference_items),
        sort_numbers=sort_numbers,
    )
    expected_set = set(expected_numbers)

    all_detected_numbers = []
    all_detected_set = set()
    for _, detected_numbers in image_results:
        for number in detected_numbers:
            if number not in all_detected_set:
                all_detected_numbers.append(number)
                all_detected_set.add(number)

    missing_from_all_images = [
        number for number in expected_numbers
        if number not in all_detected_set
    ]
    absent_from_reference = [
        number for number in all_detected_numbers
        if number not in expected_set
    ]

    sections = []
    if include_global_summary:
        sections.append(
            "<div><strong>All Pictures</strong><br>"
            "標號清單有，但全部圖片都沒有出現："
            f"{_format_numbers(missing_from_all_images)}<br>"
            "有任意一張圖片出現，但標號清單沒有輸入："
            f"{_format_numbers(absent_from_reference)}</div>"
        )

    for image_name, detected_numbers in image_results:
        detected_set = set(detected_numbers)
        missing_from_image = [
            number for number in expected_numbers
            if number not in detected_set
        ]
        absent_from_reference_for_image = [
            number for number in detected_numbers
            if number not in expected_set
        ]
        sections.append(
            _format_image_section(
                image_name,
                [
                    ("標號清單有，圖片沒有：", missing_from_image),
                    ("圖片有，標號清單沒有：", absent_from_reference_for_image),
                ],
            )
        )

    return separator.join(sections)
