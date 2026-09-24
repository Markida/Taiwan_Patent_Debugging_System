"""One bounded background job per page, with non-blocking cancellation.

Jobs receive a cancellation event and must never access widgets.  A daemon
thread is used so an unavailable Windows file share cannot hold application
shutdown hostage; cancelled/stale results are never applied to the GUI.
"""

from threading import Event, Thread

from PySide6.QtCore import QObject, Signal, Slot


class BackgroundTaskRunner(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()
    _settled = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self._closed = False
        self._cancel = Event()
        self._settled.connect(self._deliver)

    def start(self, work):
        if self.busy or self._closed:
            return False
        self.busy = True
        self._cancel = Event()
        cancel = self._cancel

        def run():
            result = error = None
            try:
                result = work(cancel)
            except Exception as exc:
                error = str(exc)
            try:
                self._settled.emit(result, error)
            except RuntimeError:
                # The page/application may already have been destroyed.
                pass

        Thread(target=run, name="MDS-background-job", daemon=True).start()
        return True

    @Slot(object, object)
    def _deliver(self, result, error):
        self.busy = False
        if self._closed:
            return
        cancelled = self._cancel.is_set()
        self.finished.emit()
        if not cancelled:
            if error is not None:
                self.failed.emit(error)
            else:
                self.succeeded.emit(result)

    def cancel(self):
        self._cancel.set()

    def shutdown(self):
        self._closed = True
        self.cancel()
