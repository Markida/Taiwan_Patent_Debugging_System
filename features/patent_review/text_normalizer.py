"""Conservative normalization used for analysis without changing source text."""

import html
import unicodedata


_CHARACTER_REPLACEMENTS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "′": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "\u00a0": " ",
        "\u3000": " ",
    }
)


def normalize_patent_text(text: str) -> str:
    """Normalize analysis text while preserving the original in the model."""

    decoded = html.unescape(text or "")
    normalized = unicodedata.normalize("NFKC", decoded)
    return normalized.translate(_CHARACTER_REPLACEMENTS).replace("\r\n", "\n")
