"""Shared in-memory state used for handoff between independent feature pages."""

from __future__ import annotations

from typing import Callable, List, Optional

from features.patent_review.symbol_transfer import DocumentSymbolTransfer


SymbolTransferCallback = Callable[[DocumentSymbolTransfer], None]


class PatentWorkflowContext:
    """Publish a reviewed document symbol list to current and future OCR pages."""

    def __init__(self):
        self.symbol_transfer: Optional[DocumentSymbolTransfer] = None
        self._symbol_transfer_callbacks: List[SymbolTransferCallback] = []

    def subscribe_symbol_transfer(self, callback: SymbolTransferCallback) -> None:
        if callback not in self._symbol_transfer_callbacks:
            self._symbol_transfer_callbacks.append(callback)
        if self.symbol_transfer is not None:
            callback(self.symbol_transfer)

    def publish_symbol_transfer(self, transfer: DocumentSymbolTransfer) -> None:
        self.symbol_transfer = transfer
        for callback in tuple(self._symbol_transfer_callbacks):
            callback(transfer)

    def clear_document(self) -> None:
        self.symbol_transfer = None
