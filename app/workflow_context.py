"""Shared in-memory state used for handoff between independent feature pages."""

from __future__ import annotations

from copy import deepcopy
from typing import Callable, Dict, List, Optional, Sequence

from features.patent_review.models import PatentDocument
from features.patent_review.symbol_transfer import DocumentSymbolTransfer


SymbolTransferCallback = Callable[[DocumentSymbolTransfer], None]
OcrResultsCallback = Callable[[List[Dict[str, object]]], None]
DocumentCallback = Callable[[Optional[PatentDocument]], None]


class PatentWorkflowContext:
    """Publish a reviewed document symbol list to current and future OCR pages."""

    def __init__(self):
        self.symbol_transfer: Optional[DocumentSymbolTransfer] = None
        self._symbol_transfer_callbacks: List[SymbolTransferCallback] = []
        self.document: Optional[PatentDocument] = None
        self._document_callbacks: List[DocumentCallback] = []
        self.ocr_results: List[Dict[str, object]] = []
        self._ocr_results_callbacks: List[OcrResultsCallback] = []

    def subscribe_symbol_transfer(self, callback: SymbolTransferCallback) -> None:
        if callback not in self._symbol_transfer_callbacks:
            self._symbol_transfer_callbacks.append(callback)
        if self.symbol_transfer is not None:
            callback(self.symbol_transfer)

    def publish_symbol_transfer(self, transfer: DocumentSymbolTransfer) -> None:
        self.symbol_transfer = transfer
        for callback in tuple(self._symbol_transfer_callbacks):
            callback(transfer)

    def subscribe_document(self, callback: DocumentCallback) -> None:
        if callback not in self._document_callbacks:
            self._document_callbacks.append(callback)
        if self.document is not None:
            callback(self.document)

    def publish_document(self, document: PatentDocument) -> None:
        self.document = document
        for callback in tuple(self._document_callbacks):
            callback(document)

    def subscribe_ocr_results(self, callback: OcrResultsCallback) -> None:
        if callback not in self._ocr_results_callbacks:
            self._ocr_results_callbacks.append(callback)
        if self.ocr_results:
            callback(deepcopy(self.ocr_results))

    def publish_ocr_results(
        self,
        results: Sequence[Dict[str, object]],
    ) -> None:
        self.ocr_results = deepcopy(list(results))
        for callback in tuple(self._ocr_results_callbacks):
            callback(deepcopy(self.ocr_results))

    def clear_ocr_results(self) -> None:
        self.ocr_results = []
        for callback in tuple(self._ocr_results_callbacks):
            callback([])

    def clear_document(self) -> None:
        self.document = None
        self.symbol_transfer = None
        for callback in tuple(self._document_callbacks):
            callback(None)
