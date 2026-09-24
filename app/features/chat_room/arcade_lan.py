"""Reusable two-player LAN transport for hidden arcade pages."""

from __future__ import annotations

from app.features.chat_room.game_ranking import local_computer_name, normalize_computer_name


from app.features.arcade_cosmetics import normalize_arcade_skin

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
from threading import Event, Lock, Thread
import time
from uuid import uuid4

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import (
    QAbstractSocket,
    QHostAddress,
    QNetworkProxy,
    QTcpServer,
    QTcpSocket,
)

from app.features.chat_room.store import default_chat_room_root


MAX_PACKET_BYTES = 64 * 1024


def _no_proxy():
    return QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)


def encoded_packet(payload):
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


@dataclass(frozen=True)
class ArcadeRoom:
    game_id: str
    room_id: str
    room_code: str
    host_name: str
    computer_name: str
    player_count: int
    status: str
    updated_at_utc: str = ""
    metadata: dict = field(default_factory=dict)

    def to_payload(self):
        return {
            "schema_version": 1,
            "game_id": self.game_id,
            "room_id": self.room_id,
            "room_code": self.room_code,
            "host_name": self.host_name,
            "computer_name": self.computer_name,
            "player_count": max(0, min(2, int(self.player_count))),
            "status": self.status,
            "updated_at_utc": self.updated_at_utc,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict):
            raise TypeError("arcade room payload must be an object")
        required = [str(payload.get(key, "")).strip() for key in ("game_id", "room_id", "room_code", "host_name")]
        if not all(required):
            raise ValueError("arcade room identity is missing")
        return cls(
            game_id=required[0],
            room_id=required[1],
            room_code=required[2],
            host_name=required[3],
            computer_name=str(payload.get("computer_name", "")).strip(),
            player_count=max(0, min(2, int(payload.get("player_count", 1)))),
            status=str(payload.get("status", "waiting")).strip() or "waiting",
            updated_at_utc=str(payload.get("updated_at_utc", "")).strip(),
            metadata=dict(payload.get("metadata") or {}),
        )


class ArcadeRoomRegistry:
    def __init__(self, game_id, root=None):
        self.game_id = str(game_id).strip()
        self.root = Path(root or default_chat_room_root()) / "data" / f"{self.game_id}_rooms"

    def publish(self, room):
        self.root.mkdir(parents=True, exist_ok=True)
        current = ArcadeRoom(
            **{
                **room.__dict__,
                "game_id": self.game_id,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        destination = self.root / f"{current.room_id}.json"
        temporary = self.root / f".{current.room_id}.{uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(current.to_payload(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
        return current

    def remove(self, room_id):
        try:
            (self.root / f"{str(room_id or '').strip()}.json").unlink(missing_ok=True)
        except OSError:
            pass

    def load_rooms(self, max_age_seconds=6.0):
        cutoff = time.time() - max(2.0, float(max_age_seconds))
        try:
            paths = list(self.root.glob("*.json")) if self.root.is_dir() else []
        except OSError:
            return []
        rooms = []
        for path in paths:
            try:
                if path.stat().st_mtime < cutoff:
                    continue
                room = ArcadeRoom.from_payload(json.loads(path.read_text(encoding="utf-8-sig")))
                if room.game_id == self.game_id:
                    rooms.append(room)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                continue
        return sorted(rooms, key=lambda room: (room.player_count >= 2, room.host_name.casefold()))


class ArcadeRoomDirectory(QObject):
    rooms_received = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, game_id, registry=None, parent=None):
        super().__init__(parent)
        self.game_id = str(game_id)
        self.registry = registry or ArcadeRoomRegistry(self.game_id)
        self._lock = Lock()
        self._room = None
        self._stop = Event()
        self._disposed = Event()
        self._thread = None
        # A slow shared-folder read may finish after Qt has deleted this
        # QObject.  Keep native-thread lifetime flags outside the Qt wrapper so
        # destruction can stop the worker without touching an invalid signal.
        self.destroyed.connect(
            lambda _object=None, disposed=self._disposed, stop=self._stop: (
                disposed.set(),
                stop.set(),
            )
        )

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        if self._disposed.is_set():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._run,
            args=(self._stop,),
            name=f"SaintIsland{self.game_id}Rooms",
            daemon=True,
        )
        self._thread.start()

    def set_hosted_room(self, room):
        with self._lock:
            self._room = room

    def clear_hosted_room(self):
        with self._lock:
            self._room = None

    def stop(self):
        self._stop.set()

    def _emit_if_active(self, signal_name, payload, stop_event):
        if stop_event.is_set() or self._disposed.is_set():
            return False
        try:
            getattr(self, signal_name).emit(payload)
        except RuntimeError:
            # Deletion can occur between the check and emit.  Treat this as a
            # normal shutdown condition instead of trying a second Qt signal.
            stop_event.set()
            return False
        return True

    def _run(self, stop_event):
        previous = ""
        try:
            while not stop_event.is_set() and not self._disposed.is_set():
                try:
                    with self._lock:
                        room = self._room
                    current = room.room_id if room else ""
                    if previous and previous != current:
                        self.registry.remove(previous)
                    if room is not None:
                        self.registry.publish(room)
                    previous = current
                    if not self._emit_if_active(
                        "rooms_received", self.registry.load_rooms(), stop_event
                    ):
                        break
                except Exception as error:
                    if not self._emit_if_active(
                        "error_occurred",
                        f"無法更新區網房間清單：{error}",
                        stop_event,
                    ):
                        break
                stop_event.wait(1.0)
        finally:
            if previous:
                self.registry.remove(previous)


class TwoPlayerLanHost(QObject):
    guest_joined = Signal(str)
    guest_left = Signal()
    message_received = Signal(object)
    status_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QTcpServer(self)
        self.server.setProxy(_no_proxy())
        self.server.newConnection.connect(self._accept)
        self.client = None
        self.buffer = bytearray()
        self.room_code = ""
        self.guest_name = "訪客"
        self.guest_user_id = ""
        self.guest_computer_name = ""
        self.guest_skin_id = "classic"

    def create_room(self, port=0):
        self.close()
        if not self.server.listen(QHostAddress.AnyIPv4, max(0, int(port))):
            raise RuntimeError(self.server.errorString())
        self.room_code = f"{socket.gethostname()}:{self.server.serverPort()}"
        return self.room_code

    def _accept(self):
        while self.server.hasPendingConnections():
            candidate = self.server.nextPendingConnection()
            candidate.setProxy(_no_proxy())
            if self.client is not None:
                candidate.write(encoded_packet({"type": "error", "message": "房間已滿"}))
                candidate.disconnectFromHost()
                continue
            self.client = candidate
            self.buffer.clear()
            candidate.readyRead.connect(self._read)
            candidate.disconnected.connect(self._disconnected)
            candidate.write(encoded_packet({"type": "welcome", "side": "right"}))

    def _read(self):
        if self.client is None:
            return
        self.buffer.extend(bytes(self.client.readAll()))
        if len(self.buffer) > MAX_PACKET_BYTES * 4:
            self.client.abort()
            return
        while b"\n" in self.buffer:
            raw, _, remainder = self.buffer.partition(b"\n")
            self.buffer = bytearray(remainder)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if payload.get("type") == "join":
                self.guest_name = str(payload.get("name", "訪客")).strip()[:24] or "訪客"
                self.guest_user_id = str(payload.get("user_id", "")).strip()
                self.guest_computer_name = normalize_computer_name(payload.get("computer_name"))
                self.guest_skin_id = normalize_arcade_skin(payload.get("skin_id"))
                self.guest_joined.emit(self.guest_name)
            else:
                self.message_received.emit(payload)

    def send(self, payload):
        if self.client is not None and self.client.state() == QAbstractSocket.ConnectedState:
            self.client.write(encoded_packet(payload))

    def _disconnected(self):
        if self.client is not None:
            self.client.deleteLater()
        self.client = None
        self.buffer.clear()
        self.guest_left.emit()

    def close(self):
        if self.client is not None:
            client = self.client
            self.client = None
            try:
                client.disconnected.disconnect(self._disconnected)
            except RuntimeError:
                pass
            client.abort()
            client.deleteLater()
        self.buffer.clear()
        self.server.close()
        self.room_code = ""


class TwoPlayerLanClient(QObject):
    message_received = Signal(object)
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.socket = QTcpSocket(self)
        self.socket.setProxy(_no_proxy())
        self.socket.connected.connect(self._connected)
        self.socket.readyRead.connect(self._read)
        self.socket.errorOccurred.connect(lambda _error: self.error_occurred.emit(self.socket.errorString()))
        self.buffer = bytearray()
        self.player_name = "訪客"
        self.user_id = ""
        self.computer_name = ""
        self.skin_id = "classic"

    def connect_to_room(self, room_code, player_name, user_id="", skin_id="classic"):
        host, separator, port_text = str(room_code or "").strip().rpartition(":")
        if not separator or not host:
            raise ValueError("房間資料格式錯誤。")
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError("房間連接埠超出範圍。")
        self.disconnect()
        self.player_name = str(player_name or socket.gethostname())[:24]
        self.user_id = str(user_id or "").strip()
        self.computer_name = local_computer_name()
        self.skin_id = normalize_arcade_skin(skin_id)
        self.socket.connectToHost(host, port)

    def _connected(self):
        self.send({"type": "join", "name": self.player_name, "user_id": self.user_id, "computer_name": self.computer_name, "skin_id": self.skin_id})
        self.status_changed.emit("已連線")

    def send(self, payload):
        if self.socket.state() == QAbstractSocket.ConnectedState:
            self.socket.write(encoded_packet(payload))

    def _read(self):
        self.buffer.extend(bytes(self.socket.readAll()))
        if len(self.buffer) > MAX_PACKET_BYTES * 4:
            self.socket.abort()
            return
        while b"\n" in self.buffer:
            raw, _, remainder = self.buffer.partition(b"\n")
            self.buffer = bytearray(remainder)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if payload.get("type") == "error":
                self.error_occurred.emit(str(payload.get("message", "連線失敗")))
            else:
                self.message_received.emit(payload)

    def disconnect(self):
        self.socket.abort()
        self.buffer.clear()
