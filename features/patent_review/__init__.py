"""Read-only patent parsing and Stage 2 deterministic text review."""

from .docx_reader import PatentDocxError, parse_docx
from .custom_rules import (
    CUSTOM_TEXT_RULES_FILENAME,
    DOCUMENT_SIMILARITY_WHITELISTS_FILENAME,
    CustomRuleError,
    CustomRuleStorageError,
    CustomRuleValidationError,
    CustomTextRule,
    CustomTextRuleStore,
    DocumentSimilarityWhitelistStore,
)
from .models import PatentDocument, PatentIssue, PatentTextReview, RuleDefinition
from .rule_engine import RULE_CATALOG, review_document
from .cross_checker import (
    ImageCrossCheck,
    PatentDrawingCrossCheck,
    compare_document_symbols_with_ocr,
)
from .symbol_transfer import (
    DocumentSymbolTransfer,
    FULL_SYMBOL_SOURCE,
    PatentSymbolEntry,
    REPRESENTATIVE_SYMBOL_SOURCE,
    SOURCE_TITLES,
    SymbolTransferWarning,
    extract_document_symbols,
    rebuild_transfer_from_reference_texts,
)

__all__ = [
    "PatentDocument",
    "PatentDocxError",
    "PatentIssue",
    "PatentTextReview",
    "RULE_CATALOG",
    "RuleDefinition",
    "CUSTOM_TEXT_RULES_FILENAME",
    "DOCUMENT_SIMILARITY_WHITELISTS_FILENAME",
    "CustomRuleError",
    "CustomRuleStorageError",
    "CustomRuleValidationError",
    "CustomTextRule",
    "CustomTextRuleStore",
    "DocumentSimilarityWhitelistStore",
    "parse_docx",
    "review_document",
    "DocumentSymbolTransfer",
    "FULL_SYMBOL_SOURCE",
    "PatentSymbolEntry",
    "REPRESENTATIVE_SYMBOL_SOURCE",
    "SOURCE_TITLES",
    "SymbolTransferWarning",
    "extract_document_symbols",
    "rebuild_transfer_from_reference_texts",
    "ImageCrossCheck",
    "PatentDrawingCrossCheck",
    "compare_document_symbols_with_ocr",
]
