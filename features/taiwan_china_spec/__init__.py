"""Taiwan-to-China patent specification conversion."""

from .converter import (
    ConversionError,
    ConversionPreview,
    ConversionPreviewParagraph,
    ConversionReport,
    EditedClaimsContentReplacement,
    available_templates,
    build_conversion_preview,
    convert_document,
    default_terminology_path,
    normalize_terminology_pairs,
    parse_edited_preview_text,
    parse_terminology_text,
    replace_content_from_edited_claims,
)
from .terminology_store import (
    TerminologyDictionaryStore,
    TerminologySnapshot,
    default_cloud_terminology_path,
)

__all__ = [
    "ConversionError",
    "ConversionPreview",
    "ConversionPreviewParagraph",
    "ConversionReport",
    "EditedClaimsContentReplacement",
    "available_templates",
    "build_conversion_preview",
    "convert_document",
    "default_cloud_terminology_path",
    "default_terminology_path",
    "normalize_terminology_pairs",
    "parse_edited_preview_text",
    "parse_terminology_text",
    "replace_content_from_edited_claims",
    "TerminologyDictionaryStore",
    "TerminologySnapshot",
]
