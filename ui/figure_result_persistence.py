"""Local figure-review snapshots; disk work never runs on the GUI thread."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from features.patent_ocr.result_store import FigureResultStore


class FigureResultPersistence:
    def _init_result_persistence(self, result_store=None):
        self.result_store = result_store or FigureResultStore()
        self._result_io = ThreadPoolExecutor(max_workers=1, thread_name_prefix="figure-save")
        self._result_identity = None
        self._result_input_paths = []
        self._result_has_recognized = False
        self._restoring_result = False
        self._result_source_changed = False
        self._result_save_revision = 0
        self._result_rerun_snapshot = None
        self._result_saved.connect(self._on_result_saved)

    def _commit_result_editor(self):
        from PySide6.QtWidgets import QApplication, QLineEdit

        editor = QApplication.focusWidget()
        if isinstance(editor, QLineEdit) and self.review_table.isAncestorOf(editor):
            self.review_table.commitData(editor)

    def _capture_result_state(self):
        rows = []
        for row in range(self.review_table.rowCount()):
            item = self.review_table.item(row, 1)
            if item is not None:
                # Preserve the review baseline as well as the edited text.
                # Rebuilding from OCR alone loses manual edits and deletions.
                from PySide6.QtCore import Qt
                rows.append({"text": item.text(), "metadata": deepcopy(item.data(Qt.UserRole) or {})})
        return {
            "ui_schema_version": 1,
            "source_image_paths": list(self.source_image_paths),
            "image_paths": list(self.image_paths),
            "all_results": deepcopy(self.all_results),
            "reviewed_results": self.collect_reviewed_results(),
            "review_rows": rows,
            "deleted_review_entries": deepcopy(self.deleted_review_entries),
            "figure_number_mappings": deepcopy(self.figure_number_mappings),
            "figure_mapping_sources": dict(self.figure_mapping_sources),
            "pending_figure_mappings": sorted(self._pending_manual_figure_mappings),
            "figure_mapping_text": self.figure_mapping_line.text(),
            "current_preview_index": self.current_preview_index,
            "reference_items": deepcopy(self.reference_items),
            "reference_text": self.reference_text.toPlainText(),
            "reference_source": self._reference_symbol_source,
            "reference_drafts": dict(self._document_reference_drafts),
            "confidence_threshold": self.confidence_threshold.value(),
            "result_view_current_only": self._result_view_current_only,
            "sort_result_numbers": self._sort_result_numbers,
            "result_title": self.result_title.text(),
            "has_recognized": self._result_has_recognized,
            "heading_stage_enabled": self._heading_stage_enabled,
            "orientation_stage_pages": deepcopy(self._orientation_stage_pages),
        }

    def _queue_result_save(self):
        if (
            self._restoring_result or self._closing or not self._result_identity
            or not self._result_has_recognized or self._ocr_job_active
            or not self.image_paths
        ):
            return None
        state = self._capture_result_state()
        identity = deepcopy(self._result_identity)
        self._result_save_revision += 1
        revision = self._result_save_revision
        self.result_save_status.setText("正在保存圖式結果…")
        store = self.result_store
        signal = self._result_saved

        def save():
            error = ""
            try:
                store.save(identity, state)
            except Exception as exc:
                error = str(exc)
            try:
                signal.emit((identity["key"], revision, error))
            except RuntimeError:
                pass  # The atomic save still completes after the window closes.
            return error

        self._result_save_future = self._result_io.submit(save)
        return self._result_save_future

    def _on_result_saved(self, payload):
        key, revision, error = payload
        if self._closing or not self._result_identity or key != self._result_identity["key"]:
            return
        if revision != self._result_save_revision:
            return
        if error:
            self.result_save_status.setText("圖式結果尚未保存，請保留此視窗")
            self.result_save_status.setToolTip(error)
            self.result_save_report.emit("圖式結果尚未保存在本機，請先保留此視窗並檢查儲存空間或權限。")
        else:
            self.result_save_status.setText(self._saved_result_status())
            self.result_save_status.setToolTip(str(self.result_store.root))
            if not self._save_success_reported:
                self.result_save_report.emit(self._saved_result_status())
                self._save_success_reported = True

    def _saved_result_status(self):
        if self._result_source_changed:
            return "已載入同名檔案的舊結果；內容已更新，可從第一步自動轉正後再按第二步辨識"
        return "圖式結果與人工修改已保存在本機"

    def _load_result_for_import(self, paths, kind, *, use_saved=True):
        # Called from the import worker. Use the same queue as writes so a
        # rapid re-import cannot race the last edit's pending save.
        store = self.result_store
        identity = store.identify(paths, kind=kind)

        def load():
            state, error = None, ""
            if use_saved:
                try:
                    state = store.load(identity)
                    if state is not None:
                        self._validate_result_state(state)
                except Exception as exc:
                    state, error = None, str(exc)
            return identity, state, error

        return self._result_io.submit(load).result()

    @staticmethod
    def _validate_result_state(state):
        if (
            not isinstance(state, dict) or state.get("ui_schema_version") != 1
            or not isinstance(state.get("image_paths"), list) or not state["image_paths"]
            or not isinstance(state.get("source_image_paths"), list)
            or len(state["source_image_paths"]) != len(state["image_paths"])
            or not isinstance(state.get("all_results"), list)
            or (state["all_results"] and len(state["all_results"]) != len(state["image_paths"]))
            or not isinstance(state.get("review_rows"), list)
        ):
            raise ValueError("本機圖式結果格式不完整，請重新辨識。")
        for field in ("figure_number_mappings", "figure_mapping_sources", "deleted_review_entries", "reference_drafts"):
            if not isinstance(state.get(field), dict):
                raise ValueError("本機圖式設定格式不完整，請重新辨識。")
        for field in ("figure_number_mappings", "figure_mapping_sources", "deleted_review_entries"):
            if any(not 0 <= int(index) < len(state["image_paths"]) for index in state[field]):
                raise ValueError("本機圖式設定包含無效的圖片編號。")
        if any(not isinstance(result, dict) for result in state["all_results"]):
            raise ValueError("本機圖式結果格式不正確。")
        for result, path in zip(state["all_results"], state["image_paths"]):
            if result.get("image_path") != path:
                raise ValueError("本機圖式結果與圖片順序不一致。")
        orientation_pages = state.get("orientation_stage_pages", [])
        if not isinstance(orientation_pages, list) or (
            orientation_pages and len(orientation_pages) != len(state["image_paths"])
        ):
            raise ValueError("本機圖式方向紀錄不完整。")
        for page, path in zip(orientation_pages, state["image_paths"]):
            if not isinstance(page, dict) or page.get("image_path") != path:
                raise ValueError("本機圖式方向紀錄與圖片順序不一致。")
        if not isinstance(state.get("reference_items", []), list):
            raise ValueError("本機圖式比對清單格式不正確。")
        int(state.get("current_preview_index", 0))
        float(state.get("confidence_threshold", 0.6))
        from features.patent_review.symbol_transfer import SOURCE_TITLES
        if state.get("reference_source") not in SOURCE_TITLES:
            raise ValueError("本機圖式比對來源無效。")
        for row in state["review_rows"]:
            if not isinstance(row, dict) or not isinstance(row.get("metadata"), dict) or not isinstance(row.get("text"), str):
                raise ValueError("本機人工修改紀錄格式不完整。")
            metadata = row.get("metadata", {})
            if not 0 <= int(metadata.get("image_index", -1)) < len(state["all_results"]):
                raise ValueError("本機圖式結果包含無效的圖片編號。")
            if not isinstance(metadata.get("detection"), dict):
                raise ValueError("本機標號框紀錄格式不完整。")
            int(metadata.get("review_sequence", 0))

    def _restore_result_state(self, state):
        from PySide6.QtCore import Qt

        self._validate_result_state(state)
        self._restoring_result = True
        self._updating_review_table = True
        self.review_table.blockSignals(True)
        try:
            self.source_image_paths = list(state["source_image_paths"])
            self.image_paths = list(state["image_paths"])
            self.all_results = deepcopy(state["all_results"])
            self.figure_number_mappings = {int(k): v for k, v in state["figure_number_mappings"].items()}
            self.figure_mapping_sources = {int(k): v for k, v in state["figure_mapping_sources"].items()}
            self.deleted_review_entries = {int(k): v for k, v in state["deleted_review_entries"].items()}
            self._pending_manual_figure_mappings = set(state.get("pending_figure_mappings", []))
            self.reference_items = deepcopy(state.get("reference_items", []))
            self.reference_text.setPlainText(state.get("reference_text", ""))
            self._document_reference_drafts = dict(state.get("reference_drafts", {}))
            self._reference_symbol_source = state.get("reference_source", self._reference_symbol_source)
            self._sync_reference_source_button(self._reference_symbol_source)
            self.confidence_threshold.blockSignals(True)
            self.confidence_threshold.setValue(state.get("confidence_threshold", 0.7))
            self.confidence_threshold.blockSignals(False)
            self.review_table.setRowCount(0)
            self._review_sequence = 0
            for saved_row in state["review_rows"]:
                metadata = deepcopy(saved_row["metadata"])
                row = self.insert_review_row(
                    int(metadata["image_index"]), metadata.get("detection", {}),
                    manual_added=bool(metadata.get("manual_added")),
                )
                item = self.review_table.item(row, 1)
                item.setText(saved_row["text"])
                item.setData(Qt.UserRole, metadata)
                self._review_sequence = max(self._review_sequence, int(metadata.get("review_sequence", 0)))
            self._result_view_current_only = bool(state.get("result_view_current_only"))
            self._sort_result_numbers = bool(state.get("sort_result_numbers", True))
            self.result_view_button.blockSignals(True)
            self.result_view_button.setChecked(self._result_view_current_only)
            self.result_view_button.setText("顯示完整結果" if self._result_view_current_only else "僅顯示當前圖片")
            self.result_view_button.blockSignals(False)
            self.current_preview_index = max(0, min(int(state.get("current_preview_index", 0)), len(self.image_paths) - 1))
            self.current_pixmap = None
            self._preview_image_key = None
            self._result_has_recognized = bool(state.get("has_recognized", True))
            self._heading_stage_enabled = bool(state.get("heading_stage_enabled", False))
            self._orientation_stage_pages = deepcopy(state.get("orientation_stage_pages", []))
        finally:
            self.review_table.blockSignals(False)
            self._updating_review_table = False
            self._restoring_result = False
        for row in range(self.review_table.rowCount()):
            self.apply_review_row_style(row)
        self.sort_review_rows_by_picture_priority()
        self.show_original_image(self.current_preview_index)
        if self.current_preview_index in self._pending_manual_figure_mappings:
            self.figure_mapping_line.setText(state.get("figure_mapping_text", ""))
        self.review_search_line.clear()
        self.refresh_result_display()
        self.result_title.setText(state.get("result_title", "已還原圖式辨識結果"))
        has_results = bool(self.all_results)
        self.run_button.setEnabled(True)
        self.review_table.setEnabled(True)
        self.compare_button.setEnabled(has_results and bool(self.reference_text.toPlainText().strip()))
        for button in (self.result_view_button, self.add_label_button, self.delete_label_button):
            button.setEnabled(has_results)
        self.prev_button.setEnabled(len(self.image_paths) > 1)
        self.next_button.setEnabled(len(self.image_paths) > 1)
        self._set_rotation_buttons_enabled(True)
        if self.workflow_context is not None:
            self.workflow_context.publish_ocr_results(self.collect_reviewed_results())

    def _finish_result_import(self, payload):
        self._result_identity = payload["identity"]
        self._result_input_paths = list(payload["input_paths"])
        self._result_has_recognized = False
        self._heading_stage_enabled = False
        self._orientation_stage_pages = []
        self._save_success_reported = False
        self._result_source_changed = False
        self._result_save_revision += 1
        state = payload.get("state")
        if state is not None:
            saved = state.get("_saved_source_identity", {})
            self._result_source_changed = saved.get("sha256s") != self._result_identity.get("sha256s")
            # Keep the saved content identity until the user explicitly runs
            # recognition on the new source, so the changed-content notice
            # survives edits and later restarts.
            if self._result_source_changed and saved:
                self._result_identity = saved
            self._restore_result_state(state)
            self.image_line.setText("已還原：" + "、".join(payload["identity"]["names"]))
            self.result_save_status.setText(self._saved_result_status())
            self.result_save_report.emit(self._saved_result_status())
            self._save_success_reported = True
            return True
        self.result_save_status.setText(
            "本機紀錄無法還原，完成辨識後會重新保存" if payload.get("cache_error")
            else "完成辨識後會自動保存結果與人工修改"
        )
        self.result_save_status.setToolTip(payload.get("cache_error", ""))
        return False

    def _shutdown_result_persistence(self):
        self._commit_result_editor()
        self._queue_result_save()
        # Queued writes finish before Python exits, without blocking Qt or
        # discarding the most recent manual edit on a normal window close.
        self._result_io.shutdown(wait=False)
