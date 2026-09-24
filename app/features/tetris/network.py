"""Server-authoritative two-player Tetris for the company LAN."""

from __future__ import annotations

from app.features.chat_room.game_ranking import local_computer_name, normalize_computer_name


from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import socket
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
from app.features.tetris.themes import (
    DEFAULT_TETRIS_SKIN_ID,
    normalize_tetris_skin_id,
)


TETRIS_PROTOCOL_VERSION = 4
DEFAULT_TETRIS_PORT = 28571
MAX_PACKET_BYTES = 32 * 1024
ROOM_HEARTBEAT_SECONDS = 1.0
ROOM_MAX_AGE_SECONDS = 6.0
TETRIS_BATTLE_DURATION_MS = 2 * 60 * 1000
TETRIS_BATTLE_MAX_KOS = 5


def _lan_no_proxy():
    return QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)


@dataclass(frozen=True)
class TetrisRoom:
    room_id: str
    room_code: str
    host_name: str
    computer_name: str
    player_count: int
    status: str
    updated_at_utc: str = ""

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict):
            raise TypeError("tetris room payload must be an object")
        room_id = str(payload.get("room_id", "")).strip()
        room_code = str(payload.get("room_code", "")).strip()
        host_name = str(payload.get("host_name", "")).strip()
        if not room_id or not room_code or not host_name:
            raise ValueError("tetris room identity is missing")
        return cls(
            room_id=room_id,
            room_code=room_code,
            host_name=host_name,
            computer_name=str(payload.get("computer_name", "")).strip(),
            player_count=max(0, min(2, int(payload.get("player_count", 1)))),
            status=str(payload.get("status", "waiting")).strip() or "waiting",
            updated_at_utc=str(payload.get("updated_at_utc", "")).strip(),
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
        }


class TetrisRoomRegistry:
    def __init__(self, root=None):
        self.root = Path(root or default_chat_room_root()) / "data" / "tetris_rooms"

    def publish(self, room):
        self.root.mkdir(parents=True, exist_ok=True)
        current = TetrisRoom(
            room_id=room.room_id,
            room_code=room.room_code,
            host_name=room.host_name,
            computer_name=room.computer_name,
            player_count=room.player_count,
            status=room.status,
            updated_at_utc=datetime.now(timezone.utc).isoformat(),
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
        room_id = str(room_id or "").strip()
        if not room_id:
            return
        try:
            (self.root / f"{room_id}.json").unlink(missing_ok=True)
        except OSError:
            pass

    def load_rooms(self, max_age_seconds=ROOM_MAX_AGE_SECONDS):
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
                rooms.append(
                    TetrisRoom.from_payload(
                        json.loads(path.read_text(encoding="utf-8-sig"))
                    )
                )
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


class TetrisRoomDirectory(QObject):
    rooms_received = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, registry=None, parent=None):
        super().__init__(parent)
        self.registry = registry or TetrisRoomRegistry()
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
            name="SaintIslandTetrisRoomDirectory",
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
                    with self._lock:
                        room = self._hosted_room
                    current_room_id = room.room_id if room else ""
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
                        f"無法更新俄羅斯方塊房間清單：{error}",
                        stop_event,
                    ):
                        break
                stop_event.wait(ROOM_HEARTBEAT_SECONDS)
        finally:
            if previous_room_id:
                self.registry.remove(previous_room_id)


class TetrisBattleEngine:
    WIDTH = 10
    HEIGHT = 20
    SHAPES = {
        1: ((1, 1, 1, 1),),
        2: ((1, 1), (1, 1)),
        3: ((0, 1, 0), (1, 1, 1)),
        4: ((1, 0, 0), (1, 1, 1)),
        5: ((0, 0, 1), (1, 1, 1)),
        6: ((0, 1, 1), (1, 1, 0)),
        7: ((1, 1, 0), (0, 1, 1)),
    }
    PIECE_NAMES = {1: "I", 2: "O", 3: "T", 4: "J", 5: "L", 6: "S", 7: "Z"}

    def __init__(self, random_source=None):
        self.random = random_source or random.Random()
        self.reset()

    def reset(self):
        self.board = [[0] * self.WIDTH for _ in range(self.HEIGHT)]
        self.score = 0
        self.lines = 0
        self.sent_lines = 0
        self.ko_count = 0
        self.pending_garbage = 0
        self.effects = []
        self._effect_serial = 0
        self.combo = 0
        self.back_to_back = False
        self.last_attack = 0
        self.last_clear_name = ""
        self.game_over = False
        self.piece_id = 0
        self.matrix = ((1,),)
        self.x = 0
        self.y = 0
        self.hold_piece_id = 0
        self.hold_used = False
        self._queue = []
        self._last_move_was_rotation = False
        self.spawn_piece()

    def _record_effect(self, kind, **details):
        self._effect_serial += 1
        self.effects.append({"id": self._effect_serial, "kind": kind, **details})
        # Retain recent events across snapshots so multiple network updates
        # cannot overwrite a clear/hold before the UI has painted it.
        self.effects = self.effects[-16:]

    def reset_playfield(self):
        """Clear one KO while preserving the two-minute match statistics."""

        self.pending_garbage = 0

        self.board = [[0] * self.WIDTH for _ in range(self.HEIGHT)]
        self.game_over = False
        self.combo = 0
        self.back_to_back = False
        self.last_attack = 0
        self.last_clear_name = ""
        self.hold_piece_id = 0
        self.hold_used = False
        self._queue = []
        self.spawn_piece()

    def _fill_queue(self):
        while len(self._queue) < 7:
            bag = list(range(1, 8))
            self.random.shuffle(bag)
            self._queue.extend(bag)

    def spawn_piece(self, piece_id=None):
        self._fill_queue()
        self.piece_id = int(piece_id or self._queue.pop(0))
        self._fill_queue()
        self.matrix = self.SHAPES[self.piece_id]
        self.x = (self.WIDTH - len(self.matrix[0])) // 2
        self.y = 0
        self.hold_used = False
        self._last_move_was_rotation = False
        if not self._valid(self.matrix, self.x, self.y):
            self.game_over = True

    def next_piece_ids(self, count=5):
        self._fill_queue()
        return list(self._queue[:max(0, int(count))])

    def _valid(self, matrix, wanted_x, wanted_y):
        for row_index, row in enumerate(matrix):
            for column_index, occupied in enumerate(row):
                if not occupied:
                    continue
                x = wanted_x + column_index
                y = wanted_y + row_index
                if x < 0 or x >= self.WIDTH or y >= self.HEIGHT:
                    return False
                if y >= 0 and self.board[y][x]:
                    return False
        return True

    @staticmethod
    def _rotated(matrix):
        return tuple(tuple(row) for row in zip(*matrix[::-1]))

    def move(self, dx):
        if self.game_over or not self._valid(self.matrix, self.x + dx, self.y):
            return 0
        self.x += dx
        self._last_move_was_rotation = False
        return 0

    def rotate(self):
        if self.game_over:
            return 0
        rotated = self._rotated(self.matrix)
        for offset in (0, -1, 1, -2, 2):
            if self._valid(rotated, self.x + offset, self.y):
                self.matrix = rotated
                self.x += offset
                self._last_move_was_rotation = True
                break
        return 0

    def hold(self):
        if self.game_over or self.hold_used:
            return 0
        current = self.piece_id
        origin = [self.x + len(self.matrix[0]) / 2, self.y + len(self.matrix) / 2]
        if self.hold_piece_id:
            replacement = self.hold_piece_id
            self.hold_piece_id = current
            self.spawn_piece(replacement)
        else:
            self.hold_piece_id = current
            self.spawn_piece()
        self.hold_used = True
        self._record_effect("hold", piece=current, origin=origin)
        return 0

    def soft_drop(self):
        if self.game_over:
            return 0
        if self._valid(self.matrix, self.x, self.y + 1):
            self.y += 1
            self.score += 1
            self._last_move_was_rotation = False
            return 0
        return self._lock_piece()

    def hard_drop(self):
        if self.game_over:
            return 0
        distance = 0
        while self._valid(self.matrix, self.x, self.y + 1):
            self.y += 1
            distance += 1
        self.score += distance * 2
        return self._lock_piece()

    def tick(self):
        self.last_attack = 0
        if self.game_over:
            return 0
        if self._valid(self.matrix, self.x, self.y + 1):
            self.y += 1
            return 0
        return self._lock_piece()

    def action(self, name):
        self.last_attack = 0
        return {
            "left": lambda: self.move(-1),
            "right": lambda: self.move(1),
            "rotate": self.rotate,
            "soft": self.soft_drop,
            "hard": self.hard_drop,
            "hold": self.hold,
        }.get(str(name), lambda: 0)()

    def _is_t_spin(self):
        if self.piece_id != 3 or not self._last_move_was_rotation:
            return False
        pivot_x = self.x + 1
        pivot_y = self.y + 1
        occupied = 0
        for x, y in (
            (pivot_x - 1, pivot_y - 1),
            (pivot_x + 1, pivot_y - 1),
            (pivot_x - 1, pivot_y + 1),
            (pivot_x + 1, pivot_y + 1),
        ):
            if x < 0 or x >= self.WIDTH or y < 0 or y >= self.HEIGHT:
                occupied += 1
            elif self.board[y][x]:
                occupied += 1
        return occupied >= 3

    def _attack_for_clear(
        self,
        cleared,
        *,
        combo_clear=False,
        t_spin=False,
        perfect_clear=False,
    ):
        if t_spin:
            base = {0: 0, 1: 2, 2: 4, 3: 6}.get(cleared, 0)
        else:
            base = (0, 0, 1, 2, 4)[min(max(0, cleared), 4)]
        difficult = bool(t_spin and cleared) or cleared == 4
        if difficult and self.back_to_back:
            base = int(base * 1.5)
        if combo_clear:
            self.combo += 1
            if self.combo >= 2:
                # During a combo, the displayed count is also the exact number
                # of obstacle rows sent to the opponent.
                base = self.combo
        else:
            self.combo = 0
        if difficult:
            self.back_to_back = True
        elif cleared:
            self.back_to_back = False
        if perfect_clear and cleared:
            base = max(base, 10)
        return base

    def _lock_piece(self):
        t_spin = self._is_t_spin()
        locked_cells = []
        for row_index, row in enumerate(self.matrix):
            for column_index, occupied in enumerate(row):
                if occupied:
                    y = self.y + row_index
                    if y < 0:
                        self.game_over = True
                        return 0
                    x = self.x + column_index
                    self.board[y][x] = self.piece_id
                    locked_cells.append((x, y))
        full_rows = [
            index for index, row in enumerate(self.board)
            if all(value != 0 for value in row)
            # A newly received obstacle row has no gap. Keep it in place until
            # its red bomb is hit instead of clearing it as an ordinary line.
            and any(value in self.SHAPES for value in row)
        ]
        # Bomb garbage is represented by cell 10. It detonates when a falling
        # piece locks directly on top of it, as well as when its row is filled.
        # The blast removes only the bomb's own horizontal obstacle row.
        contacted_bomb_rows = {
            y + 1
            for x, y in locked_cells
            if y + 1 < self.HEIGHT and self.board[y + 1][x] == 10
        }
        blast_rows = set(full_rows)
        for index in full_rows:
            if 10 in self.board[index]:
                contacted_bomb_rows.add(index)
        blast_rows.update(contacted_bomb_rows)
        obstacle_rows = sorted(
            index for index in blast_rows
            if any(value in {8, 10} for value in self.board[index])
        )
        normal_rows = sorted(blast_rows.difference(obstacle_rows))
        if normal_rows:
            self._record_effect("clear", rows=normal_rows)
        if obstacle_rows:
            bombs = [
                [column, index]
                for index in obstacle_rows
                for column, value in enumerate(self.board[index])
                if value == 10
            ]
            self._record_effect("obstacle", rows=obstacle_rows, bombs=bombs)
        remaining = [
            row for index, row in enumerate(self.board)
            if index not in blast_rows
        ]
        cleared = len(full_rows)
        if blast_rows:
            self.board = (
                [[0] * self.WIDTH for _ in range(self.HEIGHT - len(remaining))]
                + remaining
            )
        if cleared:
            self.lines += cleared
            self.score += (0, 100, 300, 500, 800)[min(cleared, 4)]
        perfect_clear = not any(any(row) for row in self.board)
        self.last_attack = self._attack_for_clear(
            cleared,
            combo_clear=bool(blast_rows),
            t_spin=t_spin,
            perfect_clear=perfect_clear,
        )
        self.sent_lines += self.last_attack
        if self.last_attack:
            self._record_effect("attack", amount=self.last_attack, rows=sorted(blast_rows))
        clear_type = "T-Spin" if t_spin else ("Tetris" if cleared == 4 else "")
        if perfect_clear and cleared:
            clear_type = "Perfect Clear"
        self.last_clear_name = clear_type
        # Apply the queued attack between pieces: the active piece is never
        # pushed into an obstacle, and the next spawn detects top-out normally.
        pending = self.pending_garbage
        self.pending_garbage = 0
        if pending:
            self.add_garbage(pending)
        self.spawn_piece()
        return cleared

    def queue_garbage(self, rows):
        self.pending_garbage += max(0, int(rows))

    def add_garbage(self, rows):
        for _ in range(max(0, int(rows))):
            if any(self.board[0]):
                self.game_over = True
                return
            bomb = self.random.randrange(self.WIDTH)
            self.board.pop(0)
            self.board.append([
                10 if column == bomb else 8
                for column in range(self.WIDTH)
            ])
            self.y = max(0, self.y - 1)

    def stack_height(self):
        first = next(
            (index for index, row in enumerate(self.board) if any(row)),
            self.HEIGHT,
        )
        return self.HEIGHT - first

    def visible_board(self):
        visible = [row[:] for row in self.board]
        if not self.game_over:
            ghost_y = self.y
            while self._valid(self.matrix, self.x, ghost_y + 1):
                ghost_y += 1
            for row_index, row in enumerate(self.matrix):
                for column_index, occupied in enumerate(row):
                    y = ghost_y + row_index
                    x = self.x + column_index
                    if (
                        occupied
                        and 0 <= y < self.HEIGHT
                        and 0 <= x < self.WIDTH
                        and not visible[y][x]
                    ):
                        visible[y][x] = 9
            for row_index, row in enumerate(self.matrix):
                for column_index, occupied in enumerate(row):
                    y = self.y + row_index
                    x = self.x + column_index
                    if occupied and 0 <= y < self.HEIGHT and 0 <= x < self.WIDTH:
                        visible[y][x] = self.piece_id
        return visible


def best_ai_placement(engine: TetrisBattleEngine):
    """Return a stable landing plan using a conventional board heuristic."""

    best = None
    seen = set()
    matrix = engine.matrix
    for rotations in range(4):
        signature = tuple(tuple(row) for row in matrix)
        if signature in seen:
            matrix = engine._rotated(matrix)
            continue
        seen.add(signature)
        width = len(matrix[0])
        for wanted_x in range(0, engine.WIDTH - width + 1):
            wanted_y = 0
            if not engine._valid(matrix, wanted_x, wanted_y):
                continue
            while engine._valid(matrix, wanted_x, wanted_y + 1):
                wanted_y += 1
            board = [row[:] for row in engine.board]
            for row_index, row in enumerate(matrix):
                for column_index, occupied in enumerate(row):
                    if occupied:
                        board[wanted_y + row_index][wanted_x + column_index] = 1
            remaining = [row for row in board if any(value == 0 for value in row)]
            cleared = engine.HEIGHT - len(remaining)
            board = [[0] * engine.WIDTH for _ in range(cleared)] + remaining
            heights = []
            holes = 0
            for column in range(engine.WIDTH):
                first = next(
                    (row for row in range(engine.HEIGHT) if board[row][column]),
                    engine.HEIGHT,
                )
                heights.append(engine.HEIGHT - first)
                if first < engine.HEIGHT:
                    holes += sum(
                        1 for row in range(first + 1, engine.HEIGHT)
                        if not board[row][column]
                    )
            bumpiness = sum(
                abs(heights[index] - heights[index + 1])
                for index in range(engine.WIDTH - 1)
            )
            aggregate_height = sum(heights)
            score = (
                cleared * 7.5
                - holes * 8.0
                - aggregate_height * 0.45
                - bumpiness * 0.7
            )
            candidate = (score, -rotations, -wanted_x, rotations, wanted_x)
            if best is None or candidate > best:
                best = candidate
        matrix = engine._rotated(matrix)
    return (best[3], best[4]) if best is not None else (0, engine.x)


def _encoded_packet(payload):
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class TetrisHost(QObject):
    state_changed = Signal(object)
    room_created = Signal(str)
    guest_joined = Signal(str)
    guest_left = Signal()
    status_changed = Signal(str)
    match_finished = Signal(str)

    def __init__(self, parent=None, *, random_source=None):
        super().__init__(parent)
        random_source = random_source or random.Random()
        self.left = TetrisBattleEngine(random.Random(random_source.randrange(2**31)))
        self.right = TetrisBattleEngine(random.Random(random_source.randrange(2**31)))
        self.server = QTcpServer(self)
        self.server.setProxy(_lan_no_proxy())
        self.server.newConnection.connect(self._accept_connection)
        self.client = None
        self._buffer = bytearray()
        self.host_name = "房主"
        self.guest_name = "訪客"
        self.host_user_id = ""
        self.host_computer_name = local_computer_name()
        self.guest_user_id = ""
        self.guest_computer_name = ""
        self.host_skin_id = DEFAULT_TETRIS_SKIN_ID
        self.guest_skin_id = DEFAULT_TETRIS_SKIN_ID
        self.room_code = ""
        self.status = "waiting"
        self.winner = ""
        self.winner_reason = ""
        self.remaining_ms = TETRIS_BATTLE_DURATION_MS
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self._tick)
        self.match_id = uuid4().hex
        self.countdown = 0
        self._countdown_deadline = 0.0
        self._guest_joined = False
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(50)
        self.countdown_timer.timeout.connect(self._advance_countdown)

    def create_room(
        self,
        host_name,
        port=DEFAULT_TETRIS_PORT,
        user_id="",
        skin_id=DEFAULT_TETRIS_SKIN_ID,
    ):
        self.close()
        self.host_name = str(host_name or socket.gethostname())[:24]
        self.host_user_id = str(user_id or "").strip()
        self.host_computer_name = local_computer_name()
        self.host_skin_id = normalize_tetris_skin_id(skin_id)
        self.guest_skin_id = DEFAULT_TETRIS_SKIN_ID
        wanted_port = max(0, int(port))
        if not self.server.listen(QHostAddress.AnyIPv4, wanted_port):
            if wanted_port and not self.server.listen(QHostAddress.AnyIPv4, 0):
                raise RuntimeError(self.server.errorString())
        self.room_code = f"{socket.gethostname()}:{self.server.serverPort()}"
        self._prepare_match()
        self.status = "waiting"
        self.guest_name = "訪客"
        self.guest_user_id = ""
        self.guest_computer_name = ""
        self.state_changed.emit(self.to_state())
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
            candidate.write(_encoded_packet({"type": "welcome", "side": "right"}))

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
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "join":
                if self._guest_joined:
                    continue
                if payload.get("protocol") != TETRIS_PROTOCOL_VERSION:
                    self.client.write(_encoded_packet({
                        "type": "error",
                        "message": "俄羅斯方塊版本不同，請雙方更新至相同版本後再加入。",
                    }))
                    self.client.disconnectFromHost()
                    return
                self.guest_name = str(payload.get("name", "訪客")).strip()[:24] or "訪客"
                self.guest_user_id = str(payload.get("user_id", "")).strip()
                self.guest_computer_name = normalize_computer_name(payload.get("computer_name"))
                self.guest_skin_id = normalize_tetris_skin_id(
                    payload.get("skin_id", DEFAULT_TETRIS_SKIN_ID)
                )
                self._guest_joined = True
                self.status = "ready"
                self.guest_joined.emit(self.guest_name)
                self.status_changed.emit("雙方已就緒｜等待房主按 START")
                self._emit_state()
            elif payload.get("type") == "action":
                self.apply_action("right", payload.get("action", ""))

    def _prepare_match(self):
        self.timer.stop()
        self.countdown_timer.stop()
        self.left.reset()
        self.right.reset()
        self.match_id = uuid4().hex
        self.countdown = 0
        self._countdown_deadline = 0.0
        self.winner = ""
        self.winner_reason = ""
        self.remaining_ms = TETRIS_BATTLE_DURATION_MS

    def start_match(self):
        """Start a local CPU match; LAN rooms use the host-only countdown."""
        self._prepare_match()
        self.status = "playing"

    def start_countdown(self):
        if (
            self.status != "ready"
            or not self._guest_joined
            or self.client is None
            or self.client.state() != QAbstractSocket.ConnectedState
        ):
            return False
        self.status = "countdown"
        self.countdown = 3
        self._countdown_deadline = time.monotonic() + 3.0
        self.status_changed.emit("倒數 3")
        self._emit_state()
        self.countdown_timer.start()
        return True

    def _advance_countdown(self):
        if self.status != "countdown":
            self.countdown_timer.stop()
            return
        remaining = max(0, math.ceil(self._countdown_deadline - time.monotonic()))
        if remaining == self.countdown:
            return
        self.countdown = remaining
        if remaining:
            self.status_changed.emit(f"倒數 {remaining}")
        else:
            self.countdown_timer.stop()
            self.status = "playing"
            self.status_changed.emit("開始！")
            self.timer.start()
        self._emit_state()

    def _apply_attack(self, engine, opponent):
        attack = max(0, int(engine.last_attack))
        if attack:
            opponent.queue_garbage(attack)

    def apply_action(self, side, action):
        if self.status != "playing":
            return
        engine = self.left if side == "left" else self.right
        opponent = self.right if side == "left" else self.left
        engine.action(action)
        self._apply_attack(engine, opponent)
        self._finish_if_needed()
        self._emit_state()

    def _tick(self):
        if self.status != "playing":
            return
        self.remaining_ms = max(0, self.remaining_ms - self.timer.interval())
        self.left.tick()
        self._apply_attack(self.left, self.right)
        self.right.tick()
        self._apply_attack(self.right, self.left)
        self._finish_if_needed()
        self._emit_state()

    def _finish_if_needed(self):
        if self.status != "playing":
            return
        # Snapshot both losses before resetting either field. Every KO is a
        # point earned by the opponent, including simultaneous top-outs.
        defeated_players = [
            (defeated, opponent)
            for defeated, opponent in ((self.left, self.right), (self.right, self.left))
            if defeated.game_over
        ]
        for defeated, opponent in defeated_players:
            opponent.ko_count += 1
            opponent._record_effect("ko")
            defeated._record_effect("knocked_out")
        for defeated, _opponent in defeated_players:
            defeated.reset_playfield()

        if (
            self.left.ko_count < TETRIS_BATTLE_MAX_KOS
            and self.right.ko_count < TETRIS_BATTLE_MAX_KOS
            and self.remaining_ms > 0
        ):
            return

        self.status = "finished"
        left_rank = (
            self.left.ko_count,
            self.left.sent_lines,
            -self.left.stack_height(),
        )
        right_rank = (
            self.right.ko_count,
            self.right.sent_lines,
            -self.right.stack_height(),
        )
        if left_rank == right_rank:
            self.winner = "draw"
        else:
            self.winner = "left" if left_rank > right_rank else "right"
        if self.left.ko_count != self.right.ko_count:
            self.winner_reason = "KO"
        elif self.left.sent_lines != self.right.sent_lines:
            self.winner_reason = "攻擊行數"
        else:
            self.winner_reason = "盤面高度"
        self.timer.stop()
        winner_name = self.winner_name()
        self.match_finished.emit(winner_name)
        self.status_changed.emit(f"比賽結束：{winner_name}")

    def winner_name(self):
        if self.winner == "left":
            return self.host_name
        if self.winner == "right":
            return self.guest_name
        return "平手"

    def winner_computer_name(self):
        if self.winner == "left":
            return self.host_computer_name
        if self.winner == "right":
            return self.guest_computer_name
        return ""

    def winner_user_id(self):
        if self.winner == "left":
            return self.host_user_id
        if self.winner == "right":
            return self.guest_user_id
        return ""

    def to_state(self):
        return {
            "type": "state",
            "protocol": TETRIS_PROTOCOL_VERSION,
            "match_id": self.match_id,
            "countdown": self.countdown,
            "status": self.status,
            "winner": self.winner,
            "left_name": self.host_name,
            "right_name": self.guest_name,
            "left_skin": self.host_skin_id,
            "right_skin": self.guest_skin_id,
            "left_board": self.left.visible_board(),
            "right_board": self.right.visible_board(),
            "left_score": self.left.score,
            "right_score": self.right.score,
            "left_lines": self.left.lines,
            "right_lines": self.right.lines,
            "left_sent": self.left.sent_lines,
            "right_sent": self.right.sent_lines,
            "left_kos": self.left.ko_count,
            "right_kos": self.right.ko_count,
            "left_combo": self.left.combo,
            "right_combo": self.right.combo,
            "left_b2b": self.left.back_to_back,
            "right_b2b": self.right.back_to_back,
            "left_hold": self.left.hold_piece_id,
            "right_hold": self.right.hold_piece_id,
            "left_next": self.left.next_piece_ids(),
            "right_next": self.right.next_piece_ids(),
            "left_incoming": self.left.pending_garbage,
            "right_incoming": self.right.pending_garbage,
            "left_hold_used": self.left.hold_used,
            "right_hold_used": self.right.hold_used,
            "left_effects": list(self.left.effects),
            "right_effects": list(self.right.effects),
            "remaining_seconds": max(0, (self.remaining_ms + 999) // 1000),
            "winner_reason": self.winner_reason,
        }

    def _emit_state(self):
        state = self.to_state()
        self.state_changed.emit(state)
        self._broadcast_state(state)

    def _broadcast_state(self, state=None):
        if self.client is not None and self.client.state() == QAbstractSocket.ConnectedState:
            self.client.write(_encoded_packet(state or self.to_state()))

    def _client_disconnected(self):
        if self.client is not None:
            self.client.deleteLater()
        self.client = None
        self._buffer.clear()
        self.timer.stop()
        self.countdown_timer.stop()
        self._guest_joined = False
        self.countdown = 0
        if self.server.isListening():
            self._prepare_match()
            self.status = "waiting"
            self.status_changed.emit("對手已離線；等待另一位玩家加入")
            self.state_changed.emit(self.to_state())
            self.guest_left.emit()

    def close(self):
        self.timer.stop()
        self.countdown_timer.stop()
        self.countdown = 0
        self._guest_joined = False
        self.status = "waiting"
        if self.client is not None:
            client = self.client
            self.client = None
            try:
                client.disconnected.disconnect(self._client_disconnected)
            except RuntimeError:
                pass
            client.abort()
            client.deleteLater()
        self._buffer.clear()
        self.server.close()
        self.room_code = ""


class TetrisClient(QObject):
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
        self.socket.disconnected.connect(lambda: self.status_changed.emit("已與房主中斷連線"))
        self.socket.errorOccurred.connect(lambda _error: self.error_occurred.emit(self.socket.errorString()))
        self._buffer = bytearray()
        self.player_name = "訪客"
        self.user_id = ""
        self.computer_name = ""
        self.skin_id = DEFAULT_TETRIS_SKIN_ID
        self._finished_winner = ""

    def connect_to_room(
        self,
        room_code,
        player_name,
        user_id="",
        skin_id=DEFAULT_TETRIS_SKIN_ID,
    ):
        code = str(room_code or "").strip()
        host, separator, port_text = code.rpartition(":")
        if not separator or not host:
            raise ValueError("房間資料格式錯誤。")
        try:
            port = int(port_text)
        except ValueError as error:
            raise ValueError("房間連接埠不正確。") from error
        if not 1 <= port <= 65535:
            raise ValueError("房間連接埠超出範圍。")
        self.disconnect()
        self.player_name = str(player_name or socket.gethostname())[:24]
        self.user_id = str(user_id or "").strip()
        self.computer_name = local_computer_name()
        self.skin_id = normalize_tetris_skin_id(skin_id)
        self._finished_winner = ""
        self.status_changed.emit(f"正在連線至 {host}:{port}")
        self.socket.connectToHost(host, port)

    def _connected(self):
        self.socket.write(_encoded_packet({
            "type": "join",
            "protocol": TETRIS_PROTOCOL_VERSION,
            "name": self.player_name,
            "user_id": self.user_id,
            "computer_name": self.computer_name,
            "skin_id": self.skin_id,
        }))
        self.status_changed.emit("已連線，等待房主開始比賽")

    def send_action(self, action):
        if self.socket.state() == QAbstractSocket.ConnectedState:
            self.socket.write(_encoded_packet({"type": "action", "action": str(action)}))

    def _read_server(self):
        self._buffer.extend(bytes(self.socket.readAll()))
        if len(self._buffer) > MAX_PACKET_BYTES * 4:
            self.socket.abort()
            return
        while b"\n" in self._buffer:
            raw, _, remainder = self._buffer.partition(b"\n")
            self._buffer = bytearray(remainder)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "state":
                if payload.get("protocol") != TETRIS_PROTOCOL_VERSION:
                    self.error_occurred.emit("俄羅斯方塊版本不同，請雙方更新至相同版本。")
                    self.disconnect()
                    return
                self.state_changed.emit(payload)
                if payload.get("status") == "finished":
                    if payload.get("winner") == "draw":
                        winner = "平手"
                    else:
                        winner = (
                            payload.get("left_name", "房主")
                            if payload.get("winner") == "left"
                            else payload.get("right_name", "訪客")
                        )
                    if winner != self._finished_winner:
                        self._finished_winner = winner
                        self.match_finished.emit(str(winner))
            elif payload.get("type") == "error":
                self.error_occurred.emit(str(payload.get("message", "連線失敗")))

    def disconnect(self):
        self.socket.abort()
        self._buffer.clear()
