from features.patent_ocr.label_parser import (
    normalize_label_text,
    format_reference_label
)


IMAGE_RESULT_SEPARATOR = "----------------------------------------"


def build_reference_comparison_text(all_results, reference_items):
    """
    將辨識結果與使用者輸入的標號清單進行比對。
    """

    if not reference_items:
        return ""

    expected_numbers = [item["number"] for item in reference_items]

    reference_map = {
        item["number"]: item.get("name", "")
        for item in reference_items
    }

    number_to_images = {
        number: []
        for number in expected_numbers
    }

    lines = []

    lines.append("標號清單比對結果")
    lines.append("")
    lines.append(f"輸入標號數量：{len(expected_numbers)}")
    lines.append(
        "輸入標號列表：" +
        ", ".join(format_reference_label(number, reference_map) for number in expected_numbers)
    )

    lines.append("")
    lines.append("【依圖片比對】")

    for result_index, result in enumerate(all_results):
        if result_index > 0:
            lines.append("")
            lines.append(IMAGE_RESULT_SEPARATOR)

        image_name = result.get("image_name", "未知圖片")
        recognized_numbers = result.get("numbers", [])

        normalized_recognized_numbers = []
        recognized_set = set()

        for number in recognized_numbers:
            normalized = normalize_label_text(number)
            if not normalized or normalized in recognized_set:
                continue
            normalized_recognized_numbers.append(normalized)
            recognized_set.add(normalized)

        present_numbers = [
            number for number in expected_numbers
            if number in recognized_set
        ]

        missing_numbers = [
            number for number in expected_numbers
            if number not in recognized_set
        ]

        unexpected_numbers = [
            number for number in normalized_recognized_numbers
            if number not in reference_map
        ]

        for number in present_numbers:
            number_to_images[number].append(image_name)

        lines.append("")
        lines.append(f"圖片名稱：{image_name}")

        if present_numbers:
            lines.append(
                "有出現的標號：" +
                ", ".join(format_reference_label(number, reference_map) for number in present_numbers)
            )
        else:
            lines.append("有出現的標號：無")

        if missing_numbers:
            lines.append(
                "未出現的標號：" +
                ", ".join(format_reference_label(number, reference_map) for number in missing_numbers)
            )
        else:
            lines.append("未出現的標號：無")

        if unexpected_numbers:
            lines.append(
                "圖片中有出現，但是清單裡沒有出現的標號：" +
                ", ".join(unexpected_numbers)
            )
        else:
            lines.append("圖片中有出現，但是清單裡沒有出現的標號：無")

    lines.append("")
    lines.append("【依標號彙整】")

    for number in expected_numbers:
        label = format_reference_label(number, reference_map)
        appeared_images = number_to_images.get(number, [])

        if appeared_images:
            lines.append(f"{label}：出現在 {', '.join(appeared_images)}")
        else:
            lines.append(f"{label}：未在任何圖片出現")

    return "\n".join(lines)
