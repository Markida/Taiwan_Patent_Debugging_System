"""Server-authoritative LAN networking and deterministic Pong physics."""

from __future__ import annotations

from app.features.chat_room.game_ranking import local_computer_name, normalize_computer_name


from app.features.arcade_cosmetics import ArcadeEventLog, arcade_skin, normalize_arcade_skin


import json
import math
from pathlib import Path
import random
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Lock, Thread
import time
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import (
    QAbstractSocket,
    QHostAddress,
    QNetworkProxy,
    QTcpServer,
    QTcpSocket,
)

from app.features.chat_room.store import default_chat_room_root


PONG_PROTOCOL_VERSION = 1
DEFAULT_PONG_PORT = 28570
MAX_PACKET_BYTES = 16 * 1024
PONG_ROOM_HEARTBEAT_SECONDS = 1.0
PONG_ROOM_MAX_AGE_SECONDS = 6.0
DEFAULT_PONG_WINNING_SCORE = 7
MIN_PONG_WINNING_SCORE = 1
MAX_PONG_WINNING_SCORE = 30


def normalize_winning_score(value):
    try:
        score = int(value)
    except (TypeError, ValueError):
        score = DEFAULT_PONG_WINNING_SCORE
    return max(MIN_PONG_WINNING_SCORE, min(MAX_PONG_WINNING_SCORE, score))


def _lan_no_proxy():
    """Never route an internal real-time game through the Windows web proxy."""

    return QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)


@dataclass(frozen=True)
class PongRoom:
    room_id: str
    room_code: str
    host_name: str
    computer_name: str
    player_count: int
    status: str
    updated_at_utc: str = ""
    winning_score: int = DEFAULT_PONG_WINNING_SCORE

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict):
            raise TypeError("pong room payload must be an object")
        room_id = str(payload.get("room_id", "")).strip()
        room_code = str(payload.get("room_code", "")).strip()
        host_name = str(payload.get("host_name", "")).strip()
        if not room_id or not room_code or not host_name:
            raise ValueError("pong room identity is missing")
        return cls(
            room_id=room_id,
            room_code=room_code,
            host_name=host_name,
            computer_name=str(payload.get("computer_name", "")).strip(),
            player_count=max(0, min(2, int(payload.get("player_count", 1)))),
            status=str(payload.get("status", "waiting")).strip() or "waiting",
            updated_at_utc=str(payload.get("updated_at_utc", "")).strip(),
            winning_score=normalize_winning_score(
                payload.get("winning_score", DEFAULT_PONG_WINNING_SCORE)
            ),
        )

    def to_payload(self):
        return {
            "schema_version": 1,
            "room_id": self.room_id,
            "room_code": self.room_code,
            "host_name": self.host_name,
            "computer_name": self.computer_name,
            "player_count": self.player_count,
            "status": self.status,
            "updated_at_utc": self.updated_at_utc,
            "winning_score": self.winning_score,
        }


class PongRoomRegistry:
    """File-per-room discovery on the same SMB share used by chat."""

    def __init__(self, root=None):
        self.root = Path(root or default_chat_room_root()) / "data" / "pong_rooms"

    def publish(self, room):
        self.root.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        current = PongRoom(
            room_id=room.room_id,
            room_code=room.room_code,
            host_name=room.host_name,
            computer_name=room.computer_name,
            player_count=room.player_count,
            status=room.status,
            updated_at_utc=now,
            winning_score=normalize_winning_score(room.winning_score),
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
        normalized = str(room_id or "").strip()
        if not normalized:
            return
        try:
            (self.root / f"{normalized}.json").unlink(missing_ok=True)
        except OSError:
            return

    def load_rooms(self, max_age_seconds=PONG_ROOM_MAX_AGE_SECONDS):
        try:
            paths = list(self.root.glob("*.json")) if self.root.is_dir() else []
        except OSError:
            return []
        cutoff = time.time() - max(2.0, float(max_age_seconds))
        rooms = []
        for path in paths:
            try:
                if path.stat().st_mtime < cutoff:
                    continue
                room = PongRoom.from_payload(
                    json.loads(path.read_text(encoding="utf-8-sig"))
                )
                rooms.append(room)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                continue
        return sorted(
            rooms,
            key=lambda room: (
                room.player_count >= 2,
                room.host_name.casefold(),
                room.computer_name.casefold(),
            ),
        )


class PongRoomDirectory(QObject):
    """Poll and heartbeat rooms off the GUI thread so SMB cannot freeze Qt."""

    rooms_received = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, registry=None, parent=None):
        super().__init__(parent)
        self.registry = registry or PongRoomRegistry()
        self._lock = Lock()
        self._hosted_room = None
        self._stop_event = Event()
        self._disposed = Event()
        self._thread = None
        self.destroyed.connect(
            lambda _object=None, disposed=self._disposed, stop=self._stop_event: (
                disposed.set(),
                stop.set(),
            )
        )

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        if self._disposed.is_set():
            return
        self._stop_event.clear()
        self._thread = Thread(
            target=self._run,
            args=(self._stop_event,),
            name="SaintIslandPongRoomDirectory",
            daemon=True,
        )
        self._thread.start()

    def set_hosted_room(self, room):
        with self._lock:
            self._hosted_room = room

    def clear_hosted_room(self):
        with self._lock:
            self._hosted_room = None

    def stop(self):
        self._stop_event.set()

    def _snapshot(self):
        with self._lock:
            return self._hosted_room

    def _emit_if_active(self, signal_name, payload, stop_event):
        if stop_event.is_set() or self._disposed.is_set():
            return False
        try:
            getattr(self, signal_name).emit(payload)
        except RuntimeError:
            stop_event.set()
            return False
        return True

    def _run(self, stop_event):
        previous_room_id = ""
        try:
            while not stop_event.is_set() and not self._disposed.is_set():
                try:
                    room = self._snapshot()
                    current_room_id = room.room_id if room is not None else ""
                    if previous_room_id and previous_room_id != current_room_id:
                        self.registry.remove(previous_room_id)
                    if room is not None:
                        self.registry.publish(room)
                    previous_room_id = current_room_id
                    if not self._emit_if_active(
                        "rooms_received", self.registry.load_rooms(), stop_event
                    ):
                        break
                except Exception as error:
                    if not self._emit_if_active(
                        "error_occurred",
                        f"無法更新雙人彈球房間清單：{error}",
                        stop_event,
                    ):
                        break
                stop_event.wait(PONG_ROOM_HEARTBEAT_SECONDS)
        finally:
            if previous_room_id:
                self.registry.remove(previous_room_id)


class PongEngine:
    WIDTH = 1000.0
    HEIGHT = 600.0
    PADDLE_WIDTH = 18.0
    PADDLE_HEIGHT = 112.0
    LEFT_X = 34.0
    RIGHT_X = WIDTH - LEFT_X - PADDLE_WIDTH
    BALL_SIZE = 18.0
    PADDLE_SPEED = 470.0
    BALL_SPEED = 390.0
    MAX_BALL_SPEED = 960.0
    POWERUP_INTERVAL = 5.0
    POWERUP_SIZE = 44.0
    GIANT_DURATION = 4.0
    GHOST_DURATION = 4.0
    SPIN_DURATION = 5.0
    SPIN_RADIUS = 34.0
    SPIN_ANGULAR_SPEED = math.tau * 1.35
    SWAP_DELAY = 0.5
    POWERUP_TYPES = ("speed", "swap", "giant", "ghost", "spin")

    def __init__(self, *, winning_score=DEFAULT_PONG_WINNING_SCORE, random_source=None):
        self.random = random_source or random.Random()
        self.winning_score = normalize_winning_score(winning_score)
        self.events = ArcadeEventLog()
        self.left_skin = self.right_skin = self.ball_skin = "classic"
        self.left_name = "房主"
        self.right_name = "訪客"
        self.left_score = 0
        self.right_score = 0
        self.left_y = (self.HEIGHT - self.PADDLE_HEIGHT) / 2
        self.right_y = self.left_y
        self.left_input = 0
        self.right_input = 0
        self.status = "waiting"
        self.winner = ""
        self.ball_x = 0.0
        self.ball_y = 0.0
        self.ball_vx = 0.0
        self.ball_vy = 0.0
        self._serve_direction = 1
        self.powerups = []
        self.powerup_spawn_remaining = self.POWERUP_INTERVAL
        self.ball_scale = 1.0
        self.giant_remaining = 0.0
        self.ghost_remaining = 0.0
        self.spin_remaining = 0.0
        self.spin_angle = 0.0
        self.swap_countdown = 0.0
        self.controls_swapped = False
        self._reset_ball(1)

    def reset_match(self, left_name="房主", right_name="訪客", left_skin="classic", right_skin="classic"):
        self.events = ArcadeEventLog()
        self.left_skin = normalize_arcade_skin(left_skin)
        self.right_skin = normalize_arcade_skin(right_skin)
        self.ball_skin = "classic"
        self.left_name = str(left_name or "房主")[:24]
        self.right_name = str(right_name or "訪客")[:24]
        self.left_score = 0
        self.right_score = 0
        self.left_y = (self.HEIGHT - self.PADDLE_HEIGHT) / 2
        self.right_y = self.left_y
        self.left_input = 0
        self.right_input = 0
        self.status = "playing"
        self.winner = ""
        self.powerups = []
        self.powerup_spawn_remaining = self.POWERUP_INTERVAL
        self.ball_scale = 1.0
        self.giant_remaining = 0.0
        self.ghost_remaining = 0.0
        self.spin_remaining = 0.0
        self.spin_angle = 0.0
        self.swap_countdown = 0.0
        self.controls_swapped = False
        self._serve_direction = self.random.choice((-1, 1))
        self._reset_ball(self._serve_direction)

    @property
    def current_ball_size(self):
        return self.BALL_SIZE * self.ball_scale

    # Compatibility accessors keep older tests and clients working while the
    # authoritative state now supports up to two simultaneous items.
    @property
    def powerup_type(self):
        return self.powerups[0]["type"] if self.powerups else ""

    @powerup_type.setter
    def powerup_type(self, value):
        normalized = str(value or "")
        if not normalized:
            self.powerups = []
        elif self.powerups:
            self.powerups[0]["type"] = normalized
        else:
            self.powerups = [{"type": normalized, "x": 0.0, "y": 0.0}]

    @property
    def powerup_x(self):
        return self.powerups[0]["x"] if self.powerups else 0.0

    @powerup_x.setter
    def powerup_x(self, value):
        if self.powerups:
            self.powerups[0]["x"] = float(value)

    @property
    def powerup_y(self):
        return self.powerups[0]["y"] if self.powerups else 0.0

    @powerup_y.setter
    def powerup_y(self, value):
        if self.powerups:
            self.powerups[0]["y"] = float(value)

    def _reset_ball(self, direction):
        ball_size = self.current_ball_size
        self.ball_x = (self.WIDTH - ball_size) / 2
        self.ball_y = (self.HEIGHT - ball_size) / 2
        direction = -1 if direction < 0 else 1
        angle = self.random.uniform(-0.42, 0.42)
        self.ball_vx = direction * self.BALL_SPEED * math.cos(angle)
        self.ball_vy = self.BALL_SPEED * math.sin(angle)

    @staticmethod
    def _clamp(value, minimum, maximum):
        return max(minimum, min(maximum, value))

    def set_input(self, side, direction):
        normalized = -1 if direction < 0 else (1 if direction > 0 else 0)
        if side == "left":
            self.left_input = normalized
        elif side == "right":
            self.right_input = normalized

    def step(self, dt=1 / 60):
        if self.status != "playing":
            return
        dt = self._clamp(float(dt), 0.0, 0.05)
        if self.giant_remaining > 0:
            old_size = self.current_ball_size
            self.giant_remaining = max(0.0, self.giant_remaining - dt)
            if self.giant_remaining <= 0:
                center_x = self.ball_x + old_size / 2
                center_y = self.ball_y + old_size / 2
                self.ball_scale = 1.0
                self.ball_x = center_x - self.current_ball_size / 2
                self.ball_y = center_y - self.current_ball_size / 2
        if self.ghost_remaining > 0:
            self.ghost_remaining = max(0.0, self.ghost_remaining - dt)
        spin_delta_x = 0.0
        spin_delta_y = 0.0
        if self.spin_remaining > 0:
            old_angle = self.spin_angle
            self.spin_remaining = max(0.0, self.spin_remaining - dt)
            if self.spin_remaining > 0:
                self.spin_angle = (
                    self.spin_angle + self.SPIN_ANGULAR_SPEED * dt
                ) % math.tau
                # The velocity still advances the trajectory's moving centre.
                # This delta adds a circular orbit around that centre without
                # replacing or slowing the original travel direction.
                spin_delta_x = self.SPIN_RADIUS * (
                    math.cos(self.spin_angle) - math.cos(old_angle)
                )
                spin_delta_y = self.SPIN_RADIUS * (
                    math.sin(self.spin_angle) - math.sin(old_angle)
                )
            else:
                self.spin_angle = 0.0
        if self.swap_countdown > 0:
            self.swap_countdown = max(0.0, self.swap_countdown - dt)
            if self.swap_countdown <= 0:
                self._apply_side_swap()
        self.powerup_spawn_remaining -= dt
        if self.powerup_spawn_remaining <= 0:
            self._spawn_powerup()
            self.powerup_spawn_remaining = self.POWERUP_INTERVAL

        maximum_y = self.HEIGHT - self.PADDLE_HEIGHT
        self.left_y = self._clamp(
            self.left_y + self.left_input * self.PADDLE_SPEED * dt,
            0.0,
            maximum_y,
        )
        self.right_y = self._clamp(
            self.right_y + self.right_input * self.PADDLE_SPEED * dt,
            0.0,
            maximum_y,
        )
        self.ball_x += self.ball_vx * dt + spin_delta_x
        self.ball_y += self.ball_vy * dt + spin_delta_y

        ball_size = self.current_ball_size
        if self.ball_y <= 0 and self.ball_vy < 0:
            self.ball_y = 0
            self.ball_vy = abs(self.ball_vy)
            self.events.emit("bounce", self.ball_x, 0)
        elif (
            self.ball_y + ball_size >= self.HEIGHT
            and self.ball_vy > 0
        ):
            self.ball_y = self.HEIGHT - ball_size
            self.ball_vy = -abs(self.ball_vy)
            self.events.emit("bounce", self.ball_x, self.HEIGHT)

        self._paddle_collision("left")
        self._paddle_collision("right")
        self._collect_powerup_if_hit()

        if self.ball_x + ball_size < 0:
            self._score("right")
        elif self.ball_x > self.WIDTH:
            self._score("left")

    def _paddle_collision(self, side):
        if side == "left":
            paddle_x = self.LEFT_X
            paddle_y = self.left_y
            moving_toward = self.ball_vx < 0
        else:
            paddle_x = self.RIGHT_X
            paddle_y = self.right_y
            moving_toward = self.ball_vx > 0
        if not moving_toward:
            return
        ball_size = self.current_ball_size
        overlaps_x = (
            self.ball_x < paddle_x + self.PADDLE_WIDTH
            and self.ball_x + ball_size > paddle_x
        )
        overlaps_y = (
            self.ball_y < paddle_y + self.PADDLE_HEIGHT
            and self.ball_y + ball_size > paddle_y
        )
        if not overlaps_x or not overlaps_y:
            return
        self.ball_skin = self.left_skin if side == "left" else self.right_skin
        self.events.emit("hit", self.ball_x + ball_size/2, self.ball_y + ball_size/2,
                         color=arcade_skin("pong", self.ball_skin).accent)
        ball_center = self.ball_y + ball_size / 2
        paddle_center = paddle_y + self.PADDLE_HEIGHT / 2
        relative = self._clamp(
            (ball_center - paddle_center) / (self.PADDLE_HEIGHT / 2),
            -1.0,
            1.0,
        )
        speed = min(
            self.MAX_BALL_SPEED,
            math.hypot(self.ball_vx, self.ball_vy) * 1.045,
        )
        horizontal = max(speed * 0.68, speed * math.cos(relative * 0.82))
        self.ball_vx = horizontal if side == "left" else -horizontal
        self.ball_vy = speed * math.sin(relative * 0.82)
        self.ball_x = (
            paddle_x + self.PADDLE_WIDTH
            if side == "left"
            else paddle_x - ball_size
        )

    def _spawn_powerup(self):
        if len(self.powerups) >= 2:
            return
        for _attempt in range(8):
            item = {
                "type": self.random.choice(self.POWERUP_TYPES),
                "x": (self.WIDTH - self.POWERUP_SIZE) / 2,
                "y": self.random.uniform(
                    55.0,
                    self.HEIGHT - self.POWERUP_SIZE - 55.0,
                ),
            }
            if all(
                math.hypot(item["x"] - other["x"], item["y"] - other["y"])
                > self.POWERUP_SIZE * 1.2
                for other in self.powerups
            ):
                self.powerups.append(item)
                return
        self.powerups.append(item)

    def _collect_powerup_if_hit(self):
        if not self.powerups:
            return
        ball_size = self.current_ball_size
        ball_center_x = self.ball_x + ball_size / 2
        ball_center_y = self.ball_y + ball_size / 2
        hit_index = None
        for index, item in enumerate(self.powerups):
            item_center_x = item["x"] + self.POWERUP_SIZE / 2
            item_center_y = item["y"] + self.POWERUP_SIZE / 2
            maximum_distance = (ball_size + self.POWERUP_SIZE) / 2
            if math.hypot(
                ball_center_x - item_center_x,
                ball_center_y - item_center_y,
            ) <= maximum_distance:
                hit_index = index
                break
        if hit_index is None:
            return
        collected = self.powerups.pop(hit_index)["type"]
        self.events.emit("bonus", ball_center_x, ball_center_y, text={"speed":"加速！","swap":"換位！","giant":"巨大化！","ghost":"虛化！","spin":"旋轉！"}.get(collected,"道具！"))
        if collected == "speed":
            speed = min(
                self.MAX_BALL_SPEED,
                max(self.BALL_SPEED, math.hypot(self.ball_vx, self.ball_vy)) * 1.45,
            )
            angle = math.atan2(self.ball_vy, self.ball_vx)
            self.ball_vx = math.cos(angle) * speed
            self.ball_vy = math.sin(angle) * speed
        elif collected == "swap":
            self.swap_countdown = self.SWAP_DELAY
        elif collected == "giant":
            center_x = self.ball_x + ball_size / 2
            center_y = self.ball_y + ball_size / 2
            self.ball_scale = 5.0
            self.giant_remaining = self.GIANT_DURATION
            self.ball_x = center_x - self.current_ball_size / 2
            self.ball_y = center_y - self.current_ball_size / 2
        elif collected == "ghost":
            self.ghost_remaining = self.GHOST_DURATION
        elif collected == "spin":
            self.spin_remaining = self.SPIN_DURATION
            self.spin_angle = 0.0

    def _apply_side_swap(self):
        self.left_skin, self.right_skin = self.right_skin, self.left_skin
        self.left_name, self.right_name = self.right_name, self.left_name
        self.left_score, self.right_score = self.right_score, self.left_score
        self.left_y, self.right_y = self.right_y, self.left_y
        self.left_input = 0
        self.right_input = 0
        self.controls_swapped = not self.controls_swapped

    def _score(self, side):
        skin = self.left_skin if side == "left" else self.right_skin
        self.events.emit("score", self.WIDTH * (.25 if side == "left" else .75), self.HEIGHT*.45,
                         color=arcade_skin("pong",skin).accent, text="+1 得分")
        if side == "left":
            self.left_score += 1
            score = self.left_score
            self._serve_direction = -1
        else:
            self.right_score += 1
            score = self.right_score
            self._serve_direction = 1
        if score >= self.winning_score:
            self.status = "finished"
            self.winner = side
            self.events.emit("victory", self.WIDTH/2, self.HEIGHT*.68, color=arcade_skin("pong",skin).accent, text="勝利！")
            self.ball_vx = 0.0
            self.ball_vy = 0.0
            return
        self._reset_ball(self._serve_direction)

    def to_state(self):
        return {
            "type": "state",
            "protocol": PONG_PROTOCOL_VERSION,
            **self.events.state(),
            "left_skin": self.left_skin,
            "right_skin": self.right_skin,
            "ball_skin": self.ball_skin,
            "width": self.WIDTH,
            "height": self.HEIGHT,
            "paddle_width": self.PADDLE_WIDTH,
            "paddle_height": self.PADDLE_HEIGHT,
            "left_x": self.LEFT_X,
            "right_x": self.RIGHT_X,
            "left_y": self.left_y,
            "right_y": self.right_y,
            "ball_x": self.ball_x,
            "ball_y": self.ball_y,
            "ball_size": self.current_ball_size,
            "left_score": self.left_score,
            "right_score": self.right_score,
            "left_name": self.left_name,
            "right_name": self.right_name,
            "winning_score": self.winning_score,
            "status": self.status,
            "winner": self.winner,
            "powerup_type": self.powerup_type,
            "powerup_x": self.powerup_x,
            "powerup_y": self.powerup_y,
            "powerup_size": self.POWERUP_SIZE,
            "powerup_spawn_remaining": self.powerup_spawn_remaining,
            "powerups": [dict(item) for item in self.powerups],
            "giant_remaining": self.giant_remaining,
            "ghost_remaining": self.ghost_remaining,
            "spin_remaining": self.spin_remaining,
            "spin_angle": self.spin_angle,
            "spin_radius": self.SPIN_RADIUS,
            "swap_countdown": self.swap_countdown,
            "controls_swapped": self.controls_swapped,
        }


def _encoded_packet(payload):
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


class PongHost(QObject):
    state_changed = Signal(object)
    room_created = Signal(str)
    guest_joined = Signal(str)
    guest_left = Signal()
    status_changed = Signal(str)
    match_finished = Signal(str)

    def __init__(self, parent=None, *, engine=None):
        super().__init__(parent)
        self.engine = engine or PongEngine()
        self.server = QTcpServer(self)
        self.server.setProxy(_lan_no_proxy())
        self.server.newConnection.connect(self._accept_connection)
        self.client = None
        self._buffer = bytearray()
        self.host_name = "房主"
        self.host_user_id = ""
        self.host_computer_name = local_computer_name()
        self.guest_user_id = ""
        self.guest_computer_name = ""
        self.room_code = ""
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self._broadcast_tick = 0

    def create_room(
        self,
        host_name,
        port=DEFAULT_PONG_PORT,
        winning_score=DEFAULT_PONG_WINNING_SCORE,
        user_id="",
        skin_id="classic",
    ):
        self.close()
        self.host_name = str(host_name or socket.gethostname())[:24]
        self.host_user_id = str(user_id or "").strip()
        self.host_computer_name = local_computer_name()
        self.host_skin_id = normalize_arcade_skin(skin_id)
        self.engine.reset_match(self.host_name, "訪客", self.host_skin_id)
        self.engine.winning_score = normalize_winning_score(winning_score)
        wanted_port = max(0, int(port))
        if not self.server.listen(QHostAddress.AnyIPv4, wanted_port):
            if wanted_port and not self.server.listen(QHostAddress.AnyIPv4, 0):
                raise RuntimeError(self.server.errorString())
        actual_port = self.server.serverPort()
        self.room_code = f"{socket.gethostname()}:{actual_port}"
        self.engine.status = "waiting"
        self.state_changed.emit(self.engine.to_state())
        self.room_created.emit(self.room_code)
        self.status_changed.emit("等待另一位玩家加入")
        return self.room_code

    def _accept_connection(self):
        while self.server.hasPendingConnections():
            candidate = self.server.nextPendingConnection()
            candidate.setProxy(_lan_no_proxy())
            if self.client is not None:
                candidate.write(_encoded_packet({"type": "error", "message": "房間已滿"}))
                candidate.disconnectFromHost()
                continue
            self.client = candidate
            self._buffer.clear()
            candidate.readyRead.connect(self._read_client)
            candidate.disconnected.connect(self._client_disconnected)
            candidate.write(
                _encoded_packet(
                    {
                        "type": "welcome",
                        "protocol": PONG_PROTOCOL_VERSION,
                        "side": "right",
                    }
                )
            )

    def _read_client(self):
        if self.client is None:
            return
        self._buffer.extend(bytes(self.client.readAll()))
        if len(self._buffer) > MAX_PACKET_BYTES * 4:
            self.client.abort()
            return
        while b"\n" in self._buffer:
            raw, _, remainder = self._buffer.partition(b"\n")
            self._buffer = bytearray(remainder)
            if len(raw) > MAX_PACKET_BYTES:
                continue
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            message_type = payload.get("type")
            if message_type == "join":
                guest_name = str(payload.get("name", "訪客")).strip()[:24] or "訪客"
                self.guest_user_id = str(payload.get("user_id", "")).strip()
                self.guest_computer_name = normalize_computer_name(payload.get("computer_name"))
                self.engine.reset_match(self.host_name, guest_name, self.host_skin_id, normalize_arcade_skin(payload.get("skin_id")))
                self.guest_joined.emit(guest_name)
                self.status_changed.emit(f"已連線：{guest_name}")
                self.timer.start()
                self._broadcast_state()
            elif message_type == "input":
                guest_side = "left" if self.engine.controls_swapped else "right"
                self.engine.set_input(
                    guest_side,
                    int(payload.get("direction", 0)),
                )

    def set_host_direction(self, direction):
        host_side = "right" if self.engine.controls_swapped else "left"
        self.engine.set_input(host_side, direction)

    def _tick(self):
        was_playing = self.engine.status == "playing"
        self.engine.step(self.timer.interval() / 1000.0)
        state = self.engine.to_state()
        self.state_changed.emit(state)
        self._broadcast_tick += 1
        if self._broadcast_tick % 2 == 0 or state["status"] != "playing":
            self._broadcast_state()
        if was_playing and self.engine.status == "finished":
            winner = (
                self.engine.left_name
                if self.engine.winner == "left"
                else self.engine.right_name
            )
            self.match_finished.emit(winner)
            self.status_changed.emit(f"比賽結束：{winner} 勝利")
            self.timer.stop()

    def winner_user_id(self):
        if self.engine.winner not in {"left", "right"}:
            return ""
        host_side = "right" if self.engine.controls_swapped else "left"
        return self.host_user_id if self.engine.winner == host_side else self.guest_user_id

    def winner_computer_name(self):
        if self.engine.winner not in {"left", "right"}:
            return ""
        host_side = "right" if self.engine.controls_swapped else "left"
        return self.host_computer_name if self.engine.winner == host_side else self.guest_computer_name

    def _broadcast_state(self):
        if self.client is not None and self.client.state() == QAbstractSocket.ConnectedState:
            self.client.write(_encoded_packet(self.engine.to_state()))

    def _client_disconnected(self):
        if self.client is not None:
            self.client.deleteLater()
        self.client = None
        self._buffer.clear()
        self.timer.stop()
        if self.server.isListening():
            self.engine.status = "waiting"
            self.status_changed.emit("對手已離線；等待另一位玩家加入")
            self.state_changed.emit(self.engine.to_state())
            self.guest_left.emit()

    def close(self):
        self.timer.stop()
        if self.client is not None:
            client = self.client
            self.client = None
            try:
                client.disconnected.disconnect(self._client_disconnected)
            except RuntimeError:
                pass
            client.disconnectFromHost()
            client.deleteLater()
        self._buffer.clear()
        self.server.close()
        self.room_code = ""


class PongClient(QObject):
    connected = Signal()
    state_changed = Signal(object)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    match_finished = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.socket = QTcpSocket(self)
        self.socket.setProxy(_lan_no_proxy())
        self.socket.connected.connect(self._connected)
        self.socket.readyRead.connect(self._read_server)
        self.socket.disconnected.connect(
            lambda: self.status_changed.emit("已與房主中斷連線")
        )
        self.socket.errorOccurred.connect(
            lambda _error: self.error_occurred.emit(self.socket.errorString())
        )
        self._buffer = bytearray()
        self.player_name = "訪客"
        self.user_id = ""
        self.computer_name = ""
        self.skin_id = "classic"
        self._finished_winner = ""

    def connect_to_room(self, room_code, player_name, user_id="", skin_id="classic"):
        code = str(room_code or "").strip()
        host, separator, port_text = code.rpartition(":")
        if not separator or not host:
            raise ValueError("房間代碼格式應為「電腦編號:連接埠」。")
        try:
            port = int(port_text)
        except ValueError as error:
            raise ValueError("房間代碼的連接埠不正確。") from error
        if not 1 <= port <= 65535:
            raise ValueError("房間代碼的連接埠超出範圍。")
        self.disconnect()
        self.player_name = str(player_name or socket.gethostname())[:24]
        self.user_id = str(user_id or "").strip()
        self.computer_name = local_computer_name()
        self.skin_id = normalize_arcade_skin(skin_id)
        self._finished_winner = ""
        self.status_changed.emit(f"正在連線至 {host}:{port}")
        self.socket.connectToHost(host, port)

    def _connected(self):
        self.socket.write(
            _encoded_packet({
                "type": "join",
                "name": self.player_name,
                "user_id": self.user_id,
                "computer_name": self.computer_name,
                "skin_id": self.skin_id,
            })
        )
        self.connected.emit()
        self.status_changed.emit("已連線，等待房主開始比賽")

    def set_direction(self, direction):
        if self.socket.state() == QAbstractSocket.ConnectedState:
            normalized = -1 if direction < 0 else (1 if direction > 0 else 0)
            self.socket.write(
                _encoded_packet({"type": "input", "direction": normalized})
            )

    def _read_server(self):
        self._buffer.extend(bytes(self.socket.readAll()))
        if len(self._buffer) > MAX_PACKET_BYTES * 4:
            self.socket.abort()
            return
        while b"\n" in self._buffer:
            raw, _, remainder = self._buffer.partition(b"\n")
            self._buffer = bytearray(remainder)
            if len(raw) > MAX_PACKET_BYTES:
                continue
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if payload.get("type") == "state":
                self.state_changed.emit(payload)
                if payload.get("status") == "finished":
                    winner = (
                        payload.get("left_name", "房主")
                        if payload.get("winner") == "left"
                        else payload.get("right_name", "訪客")
                    )
                    if winner != self._finished_winner:
                        self._finished_winner = winner
                        self.match_finished.emit(winner)
            elif payload.get("type") == "error":
                self.error_occurred.emit(str(payload.get("message", "連線失敗")))

    def disconnect(self):
        self.socket.abort()
        self._buffer.clear()
