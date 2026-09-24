"""Conservative OCR-label reconciliation against a trusted symbol list."""

from __future__ import annotations

from features.patent_ocr.label_parser import normalize_label_text


CONFUSION_GROUPS = (
    frozenset("1Il"),
    frozenset("0Oo"),
    frozenset("2Zz"),
    frozenset("5Ss"),
    frozenset("8B"),
)


def levenshtein_distance(first: str, second: str) -> int:
    if len(first) < len(second):
        first, second = second, first
    previous = list(range(len(second) + 1))
    for first_index, first_character in enumerate(first, start=1):
        current = [first_index]
        for second_index, second_character in enumerate(second, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[second_index] + 1,
                    previous[second_index - 1]
                    + int(first_character != second_character),
                )
            )
        previous = current
    return previous[-1]


def _known_substitution(first: str, second: str) -> bool:
    return any({first, second} <= group for group in CONFUSION_GROUPS)


def _safe_single_edit(predicted: str, reference: str) -> bool:
    # Production correction is deliberately limited to one-for-one OCR glyph
    # confusions.  Gold auditing showed that insertion/deletion rules improved
    # two true labels but also rewrote ten unmatched extra boxes into apparently
    # valid reference labels.  That trade-off is unsafe for document checking.
    if len(predicted) != len(reference):
        return False

    if len(predicted) == len(reference):
        differences = [
            (first, second)
            for first, second in zip(predicted, reference, strict=True)
            if first != second
        ]
        if len(differences) != 1:
            return False
        first, second = differences[0]
        if _known_substitution(first, second):
            return True
        if {first, second} == {"1", "'"} and (
            predicted.endswith(("1", "'")) and reference.endswith(("1", "'"))
        ):
            return True
        return False
    return False


def reconcile_label_to_reference(predicted, references):
    """Return a unique, safe one-glyph symbol-list correction if available."""

    predicted = normalize_label_text(str(predicted or "").strip())
    normalized_references = sorted(
        {
            normalize_label_text(str(reference or "").strip())
            for reference in references or []
            if normalize_label_text(str(reference or "").strip())
        }
    )
    if not predicted or predicted in normalized_references:
        return predicted, False
    candidates = [
        reference
        for reference in normalized_references
        if levenshtein_distance(predicted, reference) == 1
        and _safe_single_edit(predicted, reference)
    ]
    if len(candidates) != 1:
        return predicted, False
    return candidates[0], True
