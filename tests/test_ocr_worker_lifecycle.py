import gc
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import weakref
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtWidgets import QApplication

from features.patent_ocr.figure_heading_classes import FIGURE_HEADING_CLASS_NAMES
from features.patent_ocr.ocr_worker import (
    BatchRecognitionWorker,
    resolve_figure_heading_model_path,
)


class ResultReceiver(QObject):
    def __init__(self):
        super().__init__()
        self.results = []
        self.threads = []

    @Slot(object)
    def receive(self, results):
        self.results.append(results)
        self.threads.append(threading.get_ident())


class OcrWorkerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pump_until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if predicate():
                return
            time.sleep(0.002)
        self.fail("OCR worker did not finish within the test deadline")

    def test_single_running_job_uses_daemon_and_stays_running_through_run(self):
        worker = BatchRecognitionWorker([], "test.pt")
        entered, release = threading.Event(), threading.Event()
        calls, running_at_end = [], []

        def run():
            calls.append(threading.get_ident())
            entered.set()
            release.wait(2)
            running_at_end.append(worker.isRunning())

        worker.run = run
        worker.start()
        try:
            self.assertTrue(entered.wait(0.5))
            self.assertTrue(worker.isRunning())
            self.assertTrue(worker._thread.daemon)
            worker.start()
            self.assertEqual(len(calls), 1)
            self.assertNotEqual(calls[0], threading.get_ident())
            worker.requestInterruption()
            self.assertTrue(worker.isInterruptionRequested())
            self.assertTrue(worker.isRunning())
        finally:
            release.set()
            self.pump_until(lambda: not worker.isRunning())
        self.assertEqual(running_at_end, [True])

    def test_completed_worker_can_restart_with_fresh_interruption_state(self):
        worker = BatchRecognitionWorker([], "test.pt")
        requests = []
        worker.run = lambda: requests.append(worker.isInterruptionRequested())
        worker.requestInterruption()
        worker.start()
        self.pump_until(lambda: not worker.isRunning())
        worker.requestInterruption()
        worker.start()
        self.pump_until(lambda: not worker.isRunning())
        self.assertEqual(requests, [False, False])

    def test_results_to_qobject_receiver_are_delivered_on_gui_thread(self):
        worker = BatchRecognitionWorker([], "test.pt")
        receiver = ResultReceiver()
        worker.finished_signal.connect(receiver.receive)
        worker.run = lambda: worker.finished_signal.emit([{"label": "1"}])
        worker.start()
        self.pump_until(lambda: bool(receiver.results) and not worker.isRunning())
        self.assertEqual(receiver.results, [[{"label": "1"}]])
        self.assertEqual(receiver.threads, [threading.get_ident()])

    def test_dropping_ui_reference_does_not_destroy_running_worker(self):
        entered, release = threading.Event(), threading.Event()
        worker = BatchRecognitionWorker([], "test.pt")

        def run():
            entered.set()
            release.wait(2)

        worker.run = run
        reference = weakref.ref(worker)
        worker.start()
        try:
            self.assertTrue(entered.wait(0.5))
            del worker
            gc.collect()
            self.assertIsNotNone(reference())
            self.assertTrue(reference().isRunning())
        finally:
            release.set()
            self.pump_until(lambda: reference() is None or not reference().isRunning())

    def test_direct_run_still_honors_cancellation_before_loading_engine(self):
        worker = BatchRecognitionWorker([], "test.pt")
        cancelled = []
        worker.cancelled_signal.connect(lambda: cancelled.append(True))
        worker.requestInterruption()
        with patch("features.patent_ocr.ocr_worker.load_detection_model") as load:
            worker.run()
        load.assert_not_called()
        self.assertEqual(cancelled, [True])

    def test_real_run_keeps_result_fields_and_cancels_between_images(self):
        import features.patent_ocr.ocr_engine as engine

        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                worker = BatchRecognitionWorker(["one.png", "two.png"], "test.pt")
                results, cancelled = [], []
                worker.finished_signal.connect(results.append)
                worker.cancelled_signal.connect(lambda: cancelled.append(True))

                def recognize(**kwargs):
                    if cancel:
                        worker.requestInterruption()
                    return {"image_path": str(kwargs["image_path"]), "detections": []}

                with (
                    patch("features.patent_ocr.ocr_worker.load_detection_model", return_value=Mock()),
                    patch("features.patent_ocr.ocr_worker.load_class_map", return_value={}),
                    patch.object(engine, "should_use_yolo_class_as_char", return_value=True),
                    patch.object(engine, "get_device_status_text", return_value="CPU"),
                    patch.object(engine, "recognize_one_image", side_effect=recognize) as recognize_mock,
                ):
                    worker.run()
                if cancel:
                    self.assertEqual(cancelled, [True])
                    self.assertEqual(results, [])
                    self.assertEqual(recognize_mock.call_count, 1)
                else:
                    self.assertEqual(cancelled, [])
                    self.assertEqual(len(results[0]), 2)
                    self.assertEqual(results[0][0]["original_image_path"], "one.png")
                    self.assertEqual(results[0][1]["rotation_degrees"], 0)

    def test_optional_heading_model_can_auto_orient_before_component_ocr(self):
        import features.patent_ocr.ocr_engine as engine

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main_path = root / "patent_label_group_v2_gold_ft.pt"
            heading_path = root / "figure_heading_locator_v1.pt"
            main_path.write_bytes(b"main")
            heading_path.write_bytes(b"heading")
            names_path = heading_path.with_suffix(".names.json")
            names_path.write_text(
                json.dumps({"names": list(FIGURE_HEADING_CLASS_NAMES)}),
                encoding="utf-8",
            )
            (root / "figure_heading_locator_v1.release.json").write_text(
                json.dumps(
                    {
                        "approved_for_production": True,
                        "classes": list(FIGURE_HEADING_CLASS_NAMES),
                        "artifacts": {
                            heading_path.name: {
                                "sha256": hashlib.sha256(b"heading").hexdigest()
                            },
                            names_path.name: {
                                "sha256": hashlib.sha256(
                                    names_path.read_bytes()
                                ).hexdigest()
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            source = root / "source.png"
            oriented = root / "oriented.png"
            main_model = Mock()
            heading_model = Mock()
            heading_model.names = dict(enumerate(FIGURE_HEADING_CLASS_NAMES))
            analysis = {
                "status": "ready",
                "image_width": 100,
                "image_height": 200,
                "detected_figure_numbers": ["3A"],
                "auto_figure_numbers": ["3A"],
                "figure_caption_detections": [],
                "mapping": {
                    "status": "accepted",
                    "confidence": 0.9,
                    "complete": True,
                },
                "orientation": {
                    "status": "needs_rotation",
                    "correction_degrees": 90,
                    "confidence": 1.0,
                    "orientation_source": "figure_prefix_rotate_right",
                },
            }
            worker = BatchRecognitionWorker([str(source)], str(main_path))
            emitted = []
            worker.finished_signal.connect(emitted.append)

            with (
                patch(
                    "features.patent_ocr.ocr_worker.load_detection_model",
                    side_effect=[main_model, heading_model],
                ),
                patch(
                    "features.patent_ocr.ocr_worker.load_class_map",
                    return_value={},
                ),
                patch.object(
                    engine,
                    "should_use_yolo_class_as_char",
                    return_value=False,
                ),
                patch.object(engine, "get_device_status_text", return_value="CPU"),
                patch.object(engine, "get_easyocr_gpu_flag", return_value=False),
                patch.object(engine, "get_compute_device", return_value="cpu"),
                patch.object(
                    engine,
                    "recognize_one_image",
                    return_value={
                        "image_path": str(oriented),
                        "detections": [],
                        "numbers": [],
                        "labels": [],
                    },
                ) as recognize,
                patch(
                    "features.patent_ocr.easyocr_loader.create_easyocr_reader",
                    return_value=Mock(),
                ),
                patch(
                    "features.patent_ocr.figure_heading.analyze_figure_headings",
                    return_value=analysis,
                ),
                patch(
                    "features.patent_ocr.image_tools.create_auto_oriented_image",
                    return_value=str(oriented),
                ) as create_oriented,
            ):
                worker.run()

            self.assertEqual(
                resolve_figure_heading_model_path(main_path),
                heading_path,
            )
            recognize.assert_called_once()
            self.assertEqual(
                recognize.call_args.kwargs["image_path"],
                oriented,
            )
            create_oriented.assert_called_once_with(
                source,
                correction_degrees=90,
            )
            result = emitted[0][0]
            self.assertEqual(result["original_image_path"], str(source))
            self.assertEqual(result["rotation_degrees"], 90)
            self.assertEqual(result["auto_figure_numbers"], ["3A"])
            self.assertEqual(
                result["orientation"]["status"],
                "needs_rotation",
            )

    def test_unapproved_or_changed_heading_model_is_not_auto_loaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main_path = root / "main.pt"
            heading_path = root / "figure_heading_locator_v1.pt"
            main_path.write_bytes(b"main")
            heading_path.write_bytes(b"candidate")
            self.assertIsNone(resolve_figure_heading_model_path(main_path))

            manifest = {
                "approved_for_production": True,
                "classes": list(FIGURE_HEADING_CLASS_NAMES),
                "artifacts": {
                    heading_path.name: {
                        "sha256": hashlib.sha256(b"candidate").hexdigest()
                    },
                },
            }
            names_path = heading_path.with_suffix(".names.json")
            names_path.write_text(
                json.dumps({"names": list(FIGURE_HEADING_CLASS_NAMES)}),
                encoding="utf-8",
            )
            manifest["artifacts"][names_path.name] = {
                "sha256": hashlib.sha256(names_path.read_bytes()).hexdigest()
            }
            (root / "figure_heading_locator_v1.release.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            self.assertEqual(
                resolve_figure_heading_model_path(main_path),
                heading_path,
            )
            heading_path.write_bytes(b"changed")
            self.assertIsNone(resolve_figure_heading_model_path(main_path))
            self.assertIsNone(
                resolve_figure_heading_model_path(
                    main_path,
                    configured_path=heading_path,
                )
            )

    def test_verified_experimental_heading_model_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main_path = root / "main.pt"
            heading_path = root / "figure_heading_pilot_v1.onnx"
            main_path.write_bytes(b"main")
            heading_path.write_bytes(b"pilot")
            names_path = heading_path.with_suffix(".names.json")
            names_path.write_text(
                json.dumps({"names": list(FIGURE_HEADING_CLASS_NAMES)}),
                encoding="utf-8",
            )
            manifest = {
                "experimental": True,
                "experimental_enabled": True,
                "approved_for_production": False,
                "classes": list(FIGURE_HEADING_CLASS_NAMES),
                "artifacts": {
                    heading_path.name: {
                        "sha256": hashlib.sha256(b"pilot").hexdigest()
                    },
                    names_path.name: {
                        "sha256": hashlib.sha256(
                            names_path.read_bytes()
                        ).hexdigest()
                    },
                },
            }
            heading_path.with_suffix(".experimental.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            self.assertIsNone(
                resolve_figure_heading_model_path(
                    main_path,
                    configured_path=heading_path,
                )
            )
            self.assertEqual(
                resolve_figure_heading_model_path(
                    main_path,
                    configured_path=heading_path,
                    allow_experimental=True,
                ),
                heading_path,
            )
            heading_path.write_bytes(b"tampered")
            self.assertIsNone(
                resolve_figure_heading_model_path(
                    main_path,
                    configured_path=heading_path,
                    allow_experimental=True,
                )
            )


if __name__ == "__main__":
    unittest.main()
