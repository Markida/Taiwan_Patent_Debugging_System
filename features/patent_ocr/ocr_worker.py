import hashlib
import json
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Signal

from features.patent_ocr.class_map import load_class_map
from features.patent_ocr.figure_heading_classes import FIGURE_HEADING_CLASS_NAMES
from features.patent_ocr.model_loader import load_detection_model


FIGURE_HEADING_MODEL_FILENAMES = (
    "figure_heading_locator_v1.onnx",
    "figure_heading_locator_v1.pt",
)
FIGURE_HEADING_RELEASE_MANIFEST = "figure_heading_locator_v1.release.json"
FIGURE_HEADING_EXPERIMENTAL_MANIFEST_SUFFIX = ".experimental.json"


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_matches_figure_heading_model(model_path, manifest_path):
    model_path = Path(model_path)
    if not model_path.is_file() or not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("classes") != list(FIGURE_HEADING_CLASS_NAMES):
            return False
        artifacts = manifest.get("artifacts", {})
        artifact = artifacts.get(model_path.name, {})
        expected_digest = str(artifact.get("sha256") or "").lower()
        if not expected_digest or _sha256_file(model_path) != expected_digest:
            return False
        names_path = model_path.with_suffix(".names.json")
        names_artifact = artifacts.get(names_path.name, {})
        expected_names_digest = str(
            names_artifact.get("sha256") or ""
        ).lower()
        if (
            not names_path.is_file()
            or not expected_names_digest
            or _sha256_file(names_path) != expected_names_digest
        ):
            return False
        names_payload = json.loads(names_path.read_text(encoding="utf-8"))
        return names_payload.get("names") == list(FIGURE_HEADING_CLASS_NAMES)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def is_approved_figure_heading_model(model_path):
    """Verify the production release manifest and artifact digest."""

    model_path = Path(model_path)
    manifest_path = model_path.parent / FIGURE_HEADING_RELEASE_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        manifest.get("approved_for_production") is True
        and _manifest_matches_figure_heading_model(model_path, manifest_path)
    )


def is_verified_experimental_figure_heading_model(model_path):
    """Verify an explicitly enabled pilot without treating it as production."""

    model_path = Path(model_path)
    manifest_path = model_path.with_suffix(
        FIGURE_HEADING_EXPERIMENTAL_MANIFEST_SUFFIX
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        manifest.get("experimental") is True
        and manifest.get("experimental_enabled") is True
        and manifest.get("approved_for_production") is False
        and _manifest_matches_figure_heading_model(model_path, manifest_path)
    )


def resolve_figure_heading_model_path(
    main_model_path,
    configured_path=None,
    *,
    allow_experimental=False,
):
    """Resolve the optional sibling model without making it a UI setting."""

    if configured_path:
        candidate = Path(configured_path)
        if is_approved_figure_heading_model(candidate):
            return candidate
        if (
            allow_experimental
            and is_verified_experimental_figure_heading_model(candidate)
        ):
            return candidate
        return None
    model_directory = Path(main_model_path).parent
    return next(
        (
            model_directory / filename
            for filename in FIGURE_HEADING_MODEL_FILENAMES
            if is_approved_figure_heading_model(model_directory / filename)
        ),
        None,
    )


class BatchRecognitionWorker(QObject):
    """Qt signal bridge over a daemon OCR thread.

    Native inference may not return immediately after cancellation. The thread
    owns a strong reference to this parentless bridge until ``run`` returns, so
    closing a page cannot destroy a still-running QThread. No widgets are used
    here; signals to QObject UI receivers remain queued by Qt.
    """

    progress_signal = Signal(str)
    finished_signal = Signal(object)
    error_signal = Signal(str)
    cancelled_signal = Signal()
    started = Signal()
    finished = Signal()

    def __init__(
        self,
        image_paths,
        model_path,
        figure_heading_model_path=None,
        allow_experimental_figure_heading_model=False,
        auto_orient=True,
    ):
        super().__init__()
        self.image_paths = image_paths
        self.model_path = model_path
        self.figure_heading_model_path = figure_heading_model_path
        self.allow_experimental_figure_heading_model = bool(
            allow_experimental_figure_heading_model
        )
        self.auto_orient = bool(auto_orient)
        self._state_lock = threading.Lock()
        self._interruption = threading.Event()
        self._running = False
        self._thread = None

    def start(self):
        with self._state_lock:
            if self._running:
                return
            self._interruption.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._thread_main,
                name="PatentOCR",
                daemon=True,
            )
            try:
                self._thread.start()
            except BaseException:
                self._running = False
                raise

    def _thread_main(self):
        try:
            self.started.emit()
            self.run()
        finally:
            with self._state_lock:
                self._running = False
            self.finished.emit()

    def isRunning(self):
        with self._state_lock:
            return self._running

    def requestInterruption(self):
        self._interruption.set()

    def isInterruptionRequested(self):
        return self._interruption.is_set()

    def _stop_if_interrupted(self):
        if not self.isInterruptionRequested():
            return False
        self.cancelled_signal.emit()
        return True

    def run(self):
        try:
            if self._stop_if_interrupted():
                return
            # Torch, OpenCV and the recognizer network are intentionally
            # imported inside the worker.  Opening the OCR page therefore
            # remains immediate, and first-time engine initialization no
            # longer blocks the Qt GUI thread.
            from features.patent_ocr.easyocr_loader import create_easyocr_reader
            from features.patent_ocr.figure_heading import (
                analyze_figure_headings,
                empty_figure_heading_analysis,
                exclude_caption_identifiers_from_components,
                is_figure_heading_model,
            )
            from features.patent_ocr.image_tools import create_auto_oriented_image
            from features.patent_ocr.ocr_engine import (
                get_compute_device,
                get_device_status_text,
                get_easyocr_gpu_flag,
                recognize_one_image,
                should_use_yolo_class_as_char,
            )

            model_path_obj = Path(self.model_path)
            model_name = model_path_obj.name

            device_status = get_device_status_text()

            self.progress_signal.emit(
                f"正在載入 YOLO 模型...\n目前運算模式：{device_status}"
            )

            model = load_detection_model(model_path_obj)
            if self._stop_if_interrupted():
                return

            class_map = load_class_map(model_path_obj)
            use_yolo_class_as_char = should_use_yolo_class_as_char(model, class_map)

            heading_model_path = resolve_figure_heading_model_path(
                model_path_obj,
                self.figure_heading_model_path,
                allow_experimental=self.allow_experimental_figure_heading_model,
            )
            heading_model = None
            if heading_model_path is not None:
                self.progress_signal.emit(
                    f"正在載入圖題方向模型...\n目前運算模式：{device_status}"
                )
                heading_model = load_detection_model(heading_model_path)
                if not is_figure_heading_model(heading_model):
                    raise RuntimeError(
                        "圖題方向模型類別錯誤：必須依序包含 "
                        + "、".join(FIGURE_HEADING_CLASS_NAMES)
                    )
                if self._stop_if_interrupted():
                    return

            reader = None

            if use_yolo_class_as_char and heading_model is None:
                self.progress_signal.emit(
                    f"已啟用新版 YOLO 字元模型模式。\n"
                    f"目前運算模式：{device_status}"
                )
            else:
                ocr_gpu = get_easyocr_gpu_flag()

                if ocr_gpu:
                    self.progress_signal.emit(
                        "正在載入 EasyOCR...\n目前運算模式：GPU 模式"
                    )
                else:
                    self.progress_signal.emit(
                        "正在載入 EasyOCR...\n目前運算模式：CPU 模式"
                    )

                reader = create_easyocr_reader(ocr_gpu)

            all_results = []
            total = len(self.image_paths)

            for index, image_path in enumerate(self.image_paths, start=1):
                if self._stop_if_interrupted():
                    return
                image_path_obj = Path(image_path)

                mode_text = "YOLO 字元模型" if use_yolo_class_as_char else "YOLO + EasyOCR"
                if heading_model is not None:
                    mode_text += " + 圖題方向辨識"

                self.progress_signal.emit(
                    f"[{index}/{total}] 正在辨識：{image_path_obj.name}\n"
                    f"辨識模式：{mode_text}\n"
                    f"目前運算模式：{device_status}"
                )

                heading_analysis = empty_figure_heading_analysis(
                    "model_unavailable" if heading_model is None else "no_evidence"
                )
                effective_image_path = image_path_obj
                correction_degrees = 0
                if heading_model is not None:
                    try:
                        heading_analysis = analyze_figure_headings(
                            image_path=image_path_obj,
                            model=heading_model,
                            reader=reader,
                            device=get_compute_device(),
                        )
                        orientation = heading_analysis.get("orientation", {})
                        requested_correction = orientation.get("correction_degrees")
                        if (
                            self.auto_orient
                            and orientation.get("status") == "needs_rotation"
                            and requested_correction in {90, 180, 270}
                        ):
                            correction_degrees = int(requested_correction)
                            effective_image_path = Path(
                                create_auto_oriented_image(
                                    image_path_obj,
                                    correction_degrees=correction_degrees,
                                )
                            )
                    except Exception as heading_error:
                        heading_analysis = empty_figure_heading_analysis(
                            "analysis_error"
                        )
                        heading_analysis["error"] = str(heading_error)
                if self._stop_if_interrupted():
                    return

                result = recognize_one_image(
                    image_path=effective_image_path,
                    model=model,
                    reader=reader,
                    model_name=model_name,
                    class_map=class_map,
                    use_yolo_class_as_char=use_yolo_class_as_char
                )
                if self._stop_if_interrupted():
                    return

                result["original_image_path"] = str(image_path_obj)
                result["rotation_degrees"] = correction_degrees
                result["figure_heading"] = heading_analysis
                result["detected_figure_numbers"] = list(
                    heading_analysis.get("detected_figure_numbers", [])
                )
                result["auto_figure_numbers"] = list(
                    heading_analysis.get("auto_figure_numbers", [])
                )
                result["figure_caption_detections"] = list(
                    heading_analysis.get("figure_caption_detections", [])
                )
                result["orientation"] = dict(
                    heading_analysis.get("orientation", {})
                )
                if heading_analysis.get("status") == "ready":
                    exclude_caption_identifiers_from_components(
                        result,
                        heading_analysis,
                        original_width=int(heading_analysis["image_width"]),
                        original_height=int(heading_analysis["image_height"]),
                        correction_degrees=correction_degrees,
                    )

                all_results.append(result)

            self.finished_signal.emit(all_results)

        except Exception as e:
            self.error_signal.emit(str(e))
