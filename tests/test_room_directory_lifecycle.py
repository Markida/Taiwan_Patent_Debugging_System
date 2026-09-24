import os
from threading import Event
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from shiboken6 import delete, isValid

from app.features.chat_room.arcade_lan import ArcadeRoomDirectory
from app.features.pong.network import PongRoomDirectory
from app.features.tank_battle.network import TankRoomDirectory
from app.features.tetris.network import TetrisRoomDirectory


class _BlockingRegistry:
    """Hold a directory worker inside the slow shared-folder read."""

    def __init__(self):
        self.load_started = Event()
        self.release_load = Event()

    def load_rooms(self):
        self.load_started.set()
        if not self.release_load.wait(2.0):
            raise TimeoutError("test did not release the room registry")
        return []

    def publish(self, _room):
        return None

    def remove(self, _room_id):
        return None


class RoomDirectoryLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_deleting_qobject_during_slow_room_read_stops_without_thread_error(self):
        factories = {
            "arcade": lambda registry: ArcadeRoomDirectory("snake", registry),
            "pong": PongRoomDirectory,
            "tetris": TetrisRoomDirectory,
            "tank": TankRoomDirectory,
        }

        for name, factory in factories.items():
            with self.subTest(directory=name):
                registry = _BlockingRegistry()
                directory = factory(registry)
                unhandled = []
                original_excepthook = threading.excepthook
                threading.excepthook = unhandled.append
                worker = None
                try:
                    directory.start()
                    worker = directory._thread
                    self.assertTrue(registry.load_started.wait(1.0))

                    # This mirrors a page being destroyed while the SMB room
                    # directory read is still in progress.
                    delete(directory)
                    self.assertFalse(isValid(directory))
                    registry.release_load.set()
                    worker.join(2.0)

                    self.assertFalse(worker.is_alive())
                    self.assertEqual(unhandled, [])
                finally:
                    registry.release_load.set()
                    if worker is not None:
                        worker.join(2.0)
                    if isValid(directory):
                        directory.stop()
                        delete(directory)
                    threading.excepthook = original_excepthook


if __name__ == "__main__":
    unittest.main()
