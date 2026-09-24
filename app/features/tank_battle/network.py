"""Server-authoritative Battle-City-inspired deathmatch for the company LAN."""

from __future__ import annotations

from app.features.chat_room.game_ranking import local_computer_name, normalize_computer_name


from app.features.arcade_cosmetics import ArcadeEventLog, arcade_skin, normalize_arcade_skin


from dataclasses import dataclass
from datetime import datetime, timezone
import json
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


TANK_PROTOCOL_VERSION = 1
DEFAULT_TANK_PORT = 28572
MAX_PACKET_BYTES = 32 * 1024
ROOM_HEARTBEAT_SECONDS = 1.0
ROOM_MAX_AGE_SECONDS = 6.0
TANK_MAP_CHOICES = (
    ("crossroads", "十字要塞"),
    ("river_bend", "河灣伏擊"),
    ("twin_forts", "雙堡對峙"),
    ("steel_maze", "鋼鐵迷宮"),
    ("island_ring", "環島決戰"),
)
TANK_MAP_IDS = frozenset(map_id for map_id, _name in TANK_MAP_CHOICES)
DEFAULT_TANK_MAP = TANK_MAP_CHOICES[0][0]


def normalize_tank_map_id(value):
    candidate = str(value or "").strip()
    return candidate if candidate in TANK_MAP_IDS else DEFAULT_TANK_MAP


def _lan_no_proxy():
    return QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)


def _encoded_packet(payload):
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


@dataclass(frozen=True)
class TankRoom:
    room_id: str
    room_code: str
    host_name: str
    computer_name: str
    player_count: int
    max_players: int
    team_mode: str
    status: str
    map_id: str = DEFAULT_TANK_MAP
    updated_at_utc: str = ""

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict):
            raise TypeError("tank room payload must be an object")
        room_id = str(payload.get("room_id", "")).strip()
        room_code = str(payload.get("room_code", "")).strip()
        host_name = str(payload.get("host_name", "")).strip()
        if not room_id or not room_code or not host_name:
            raise ValueError("tank room identity is missing")
        maximum = max(2, min(4, int(payload.get("max_players", 2))))
        team_mode = str(payload.get("team_mode", "ffa"))
        if maximum != 4:
            team_mode = "ffa"
        return cls(
            room_id=room_id,
            room_code=room_code,
            host_name=host_name,
            computer_name=str(payload.get("computer_name", "")).strip(),
            player_count=max(
                0, min(maximum, int(payload.get("player_count", 1)))
            ),
            max_players=maximum,
            team_mode="teams" if team_mode == "teams" else "ffa",
            status=str(payload.get("status", "waiting")).strip() or "waiting",
            map_id=normalize_tank_map_id(payload.get("map_id")),
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
            "max_players": self.max_players,
            "team_mode": self.team_mode,
            "status": self.status,
            "map_id": self.map_id,
            "updated_at_utc": self.updated_at_utc,
        }


class TankRoomRegistry:
    def __init__(self, root=None):
        self.root = Path(root or default_chat_room_root()) / "data" / "tank_rooms"

    def publish(self, room):
        self.root.mkdir(parents=True, exist_ok=True)
        current = TankRoom(
            room_id=room.room_id,
            room_code=room.room_code,
            host_name=room.host_name,
            computer_name=room.computer_name,
            player_count=room.player_count,
            max_players=room.max_players,
            team_mode=room.team_mode,
            status=room.status,
            map_id=normalize_tank_map_id(room.map_id),
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
        try:
            (self.root / f"{str(room_id).strip()}.json").unlink(missing_ok=True)
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
                    TankRoom.from_payload(
                        json.loads(path.read_text(encoding="utf-8-sig"))
                    )
                )
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                continue
        return sorted(
            rooms,
            key=lambda room: (
                room.player_count >= room.max_players,
                room.host_name.casefold(),
            ),
        )


class TankRoomDirectory(QObject):
    rooms_received = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, registry=None, parent=None):
        super().__init__(parent)
        self.registry = registry or TankRoomRegistry()
        self._hosted_room = None
        self._lock = Lock()
        self._stop_event = Event()
        self._disposed = Event()
        self._thread = None
        self.destroyed.connect(
            lambda _object=None, disposed=self._disposed, stop=self._stop_event: (
                disposed.set(),
                stop.set(),
            )
        )

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        if self._disposed.is_set():
            return
        self._stop_event.clear()
        self._thread = Thread(
            target=self._run,
            args=(self._stop_event,),
            name="SaintIslandTankRoomDirectory",
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
                        f"無法更新坦克大戰房間清單：{error}",
                        stop_event,
                    ):
                        break
                stop_event.wait(ROOM_HEARTBEAT_SECONDS)
        finally:
            if previous_room_id:
                self.registry.remove(previous_room_id)


class TankBattleEngine:
    WIDTH = 24
    HEIGHT = 18
    DIRECTIONS = {
        "up": (0, -1),
        "down": (0, 1),
        "left": (-1, 0),
        "right": (1, 0),
    }
    SPAWNS = ((1, 16), (22, 1), (22, 16), (1, 1))

    def __init__(self, random_source=None):
        self.random = random_source or random.Random()
        self.map_id = DEFAULT_TANK_MAP
        self.reset(["玩家1", "玩家2"], "ffa", self.map_id)

    def _build_map(self, map_id=None):
        map_id = normalize_tank_map_id(map_id or self.map_id)
        grid = [[0] * self.WIDTH for _ in range(self.HEIGHT)]
        # These five arenas only borrow the classic terrain vocabulary
        # (destructible brick, steel and water); their tile layouts are new.
        def put(value, coordinates):
            for x, y in coordinates:
                if 0 <= x < self.WIDTH and 0 <= y < self.HEIGHT:
                    grid[y][x] = value

        if map_id == "crossroads":
            put(1, (
                (x, y)
                for y in range(2, self.HEIGHT - 2)
                for x in range(3, self.WIDTH - 3)
                if (x + y) % 7 == 0 and x not in (11, 12)
            ))
            put(2, ((6, 4), (17, 4), (6, 13), (17, 13), (11, 7), (12, 10)))
            put(3, ((x, y) for y in (8, 9) for x in range(9, 15)))
        elif map_id == "river_bend":
            put(3, (
                [(x, 5) for x in range(3, 15)]
                + [(14, y) for y in range(5, 13)]
                + [(x, 12) for x in range(9, 21)]
            ))
            put(2, ((5, 8), (8, 8), (16, 9), (19, 9), (10, 3), (13, 14)))
            put(1, (
                (x, y)
                for y in range(2, 16)
                for x in range(2, 22)
                if (x * 3 + y * 5) % 13 == 0
            ))
        elif map_id == "twin_forts":
            for center_x in (7, 16):
                put(1, (
                    (center_x + dx, 9 + dy)
                    for dx, dy in (
                        (-3, -3), (-2, -3), (-1, -3), (0, -3), (1, -3), (2, -3), (3, -3),
                        (-3, -2), (3, -2), (-3, -1), (3, -1), (-3, 0), (3, 0),
                        (-3, 1), (3, 1), (-3, 2), (3, 2),
                        (-3, 3), (-2, 3), (-1, 3), (0, 3), (1, 3), (2, 3), (3, 3),
                    )
                ))
                put(2, ((center_x - 1, 8), (center_x + 1, 8), (center_x, 10)))
            put(3, ((11, y) for y in range(3, 8)))
            put(3, ((12, y) for y in range(10, 15)))
        elif map_id == "steel_maze":
            put(2, (
                (x, y)
                for x in (4, 8, 15, 19)
                for y in range(2, 16)
                if (y + x) % 5 not in (0, 1)
            ))
            put(1, (
                (x, y)
                for y in (4, 7, 10, 13)
                for x in range(2, 22)
                if x not in (5, 9, 11, 12, 14, 18)
            ))
            put(3, ((x, y) for x in (10, 13) for y in (5, 6, 11, 12)))
        else:  # island_ring
            put(3, (
                [(x, 4) for x in range(6, 18)]
                + [(x, 13) for x in range(6, 18)]
                + [(6, y) for y in range(4, 14)]
                + [(17, y) for y in range(4, 14)]
            ))
            for opening in ((11, 4), (12, 4), (11, 13), (12, 13), (6, 8), (6, 9), (17, 8), (17, 9)):
                grid[opening[1]][opening[0]] = 0
            put(2, ((9, 7), (14, 7), (9, 10), (14, 10)))
            put(1, (
                (x, y)
                for y in range(6, 12)
                for x in range(8, 16)
                if (x + y) % 3 == 0
            ))

        # Every original arena guarantees a walkable route from every spawn to
        # the central crossing. This also prevents an unlucky layout revision
        # from trapping a player behind indestructible terrain.
        center_x, center_y = self.WIDTH // 2, self.HEIGHT // 2
        for spawn_x, spawn_y in self.SPAWNS:
            for x in range(min(spawn_x, center_x), max(spawn_x, center_x) + 1):
                grid[spawn_y][x] = 0
            for y in range(min(spawn_y, center_y), max(spawn_y, center_y) + 1):
                grid[y][spawn_x] = 0
            for y in range(max(0, spawn_y - 1), min(self.HEIGHT, spawn_y + 2)):
                for x in range(max(0, spawn_x - 1), min(self.WIDTH, spawn_x + 2)):
                    grid[y][x] = 0
        return grid

    def reset(self, names, team_mode="ffa", map_id=None, skins=None):
        self.events = ArcadeEventLog()
        skins = list(skins or [])
        normalized_names = [str(name or "玩家")[:24] for name in list(names)[:4]]
        if len(normalized_names) < 2:
            normalized_names.append("玩家2")
        self.team_mode = "teams" if team_mode == "teams" and len(normalized_names) == 4 else "ffa"
        self.map_id = normalize_tank_map_id(map_id or self.map_id)
        self.map = self._build_map(self.map_id)
        self.players = {}
        for slot, name in enumerate(normalized_names):
            x, y = self.SPAWNS[slot]
            self.players[slot] = {
                "slot": slot,
                "skin_id": normalize_arcade_skin(skins[slot] if slot < len(skins) else "classic"),
                "name": name,
                "x": x,
                "y": y,
                "direction": "up" if slot >= 2 else "down",
                "lives": 3,
                "alive": True,
                "team": slot % 2 if self.team_mode == "teams" else slot,
                "shield": 10,
                "cooldown": 0,
            }
        self.bullets = []
        self.status = "playing"
        self.winner = ""

    def waiting_state(self, names, map_id=None, skins=None):
        self.reset(names, "ffa", map_id, skins)
        self.status = "waiting"

    def action(self, slot, action):
        player = self.players.get(int(slot))
        if self.status != "playing" or not player or not player["alive"]:
            return
        action = str(action)
        if action in self.DIRECTIONS:
            player["direction"] = action
            dx, dy = self.DIRECTIONS[action]
            wanted_x = player["x"] + dx
            wanted_y = player["y"] + dy
            if self._tank_can_move(slot, wanted_x, wanted_y):
                player["x"] = wanted_x
                player["y"] = wanted_y
        elif action == "shoot" and player["cooldown"] <= 0:
            dx, dy = self.DIRECTIONS[player["direction"]]
            self.bullets.append({
                "owner": int(slot),
                "x": player["x"],
                "y": player["y"],
                "dx": dx,
                "dy": dy,
            })
            player["cooldown"] = 4
            self.events.emit("shot", player["x"]+dx*.6, player["y"]+dy*.6,
                             color=arcade_skin("tank",player["skin_id"]).accent, direction=[dx, dy])

    def _tank_can_move(self, slot, x, y):
        if not (0 <= x < self.WIDTH and 0 <= y < self.HEIGHT):
            return False
        if self.map[y][x] != 0:
            return False
        return not any(
            other_slot != slot
            and player["alive"]
            and player["x"] == x
            and player["y"] == y
            for other_slot, player in self.players.items()
        )

    def tick(self):
        if self.status != "playing":
            return
        for player in self.players.values():
            player["cooldown"] = max(0, player["cooldown"] - 1)
            player["shield"] = max(0, player["shield"] - 1)
        remaining = []
        for bullet in self.bullets:
            x = bullet["x"] + bullet["dx"]
            y = bullet["y"] + bullet["dy"]
            if not (0 <= x < self.WIDTH and 0 <= y < self.HEIGHT):
                continue
            tile = self.map[y][x]
            if tile == 1:
                self.map[y][x] = 0
                self.events.emit("explosion", x, y, color="#fb923c")
                continue
            if tile == 2:
                self.events.emit("hit", x, y, color="#cbd5e1")
                continue
            target = next(
                (
                    player
                    for slot, player in self.players.items()
                    if slot != bullet["owner"]
                    and player["alive"]
                    and player["x"] == x
                    and player["y"] == y
                ),
                None,
            )
            if target is not None:
                owner = self.players.get(bullet["owner"])
                friendly = (
                    self.team_mode == "teams"
                    and owner is not None
                    and owner["team"] == target["team"]
                )
                if not friendly and target["shield"] <= 0:
                    self._damage(target)
                else:
                    self.events.emit("hit", x, y, color="#7dd3fc")
                continue
            bullet["x"], bullet["y"] = x, y
            remaining.append(bullet)
        self.bullets = remaining
        self._finish_if_needed()

    def _damage(self, player):
        self.events.emit("explosion", player["x"], player["y"], color="#fb7185",
                         text="擊破！" if player["lives"] <= 1 else "命中！")
        player["lives"] -= 1
        if player["lives"] <= 0:
            player["alive"] = False
            return
        spawn_x, spawn_y = self.SPAWNS[player["slot"]]
        player["x"], player["y"] = spawn_x, spawn_y
        player["shield"] = 12

    def _finish_if_needed(self):
        alive = [player for player in self.players.values() if player["alive"]]
        if self.team_mode == "teams":
            teams = {player["team"] for player in alive}
            if len(teams) > 1:
                return
            self.winner = f"team:{next(iter(teams))}" if teams else "draw"
        else:
            if len(alive) > 1:
                return
            self.winner = str(alive[0]["slot"]) if alive else "draw"
        self.status = "finished"
        self.events.emit("victory", self.WIDTH/2, self.HEIGHT*.7, text="戰鬥結束", color="#facc15")

    def winner_name(self):
        if self.winner == "draw":
            return "平手"
        if self.winner.startswith("team:"):
            team = int(self.winner.split(":", 1)[1])
            return "＋".join(
                player["name"] for player in self.players.values()
                if player["team"] == team
            )
        return self.players.get(int(self.winner), {}).get("name", "未知玩家")

    def ai_action(self, slot, difficulty="normal"):
        player = self.players.get(int(slot))
        enemies = [
            other for other in self.players.values()
            if other["alive"]
            and other["slot"] != int(slot)
            and (self.team_mode != "teams" or other["team"] != player["team"])
        ] if player else []
        if not player or not player["alive"] or not enemies:
            return
        target = min(
            enemies,
            key=lambda item: abs(item["x"] - player["x"]) + abs(item["y"] - player["y"]),
        )
        difficulty = str(difficulty or "normal")
        shoot_chance = {"easy": 0.12, "normal": 0.22, "hard": 0.42}.get(
            difficulty, 0.22
        )
        random_turn_chance = {"easy": 0.48, "normal": 0.28, "hard": 0.12}.get(
            difficulty, 0.28
        )
        if (
            target["x"] == player["x"]
            or target["y"] == player["y"]
            or self.random.random() < shoot_chance
        ):
            self.action(slot, "shoot")
        if self.random.random() < random_turn_chance:
            direction = self.random.choice(tuple(self.DIRECTIONS))
        elif abs(target["x"] - player["x"]) > abs(target["y"] - player["y"]):
            direction = "right" if target["x"] > player["x"] else "left"
        else:
            direction = "down" if target["y"] > player["y"] else "up"
        self.action(slot, direction)

    def to_state(self):
        return {
            "type": "state",
            "protocol": TANK_PROTOCOL_VERSION,
            **self.events.state(),
            "width": self.WIDTH,
            "height": self.HEIGHT,
            "map": [row[:] for row in self.map],
            "players": [dict(player) for player in self.players.values()],
            "bullets": [dict(bullet) for bullet in self.bullets],
            "status": self.status,
            "winner": self.winner,
            "winner_name": self.winner_name() if self.status == "finished" else "",
            "team_mode": self.team_mode,
            "map_id": self.map_id,
        }


class TankHost(QObject):
    state_changed = Signal(object)
    status_changed = Signal(str)
    player_count_changed = Signal(int)
    match_finished = Signal(str)

    def __init__(self, parent=None, *, engine=None):
        super().__init__(parent)
        self.engine = engine or TankBattleEngine()
        self.server = QTcpServer(self)
        self.server.setProxy(_lan_no_proxy())
        self.server.newConnection.connect(self._accept_connections)
        self.clients = {}
        self.buffers = {}
        self.host_name = "房主"
        self.host_user_id = ""
        self.host_computer_name = local_computer_name()
        self.room_code = ""
        self.max_players = 2
        self.team_mode = "ffa"
        self.map_id = DEFAULT_TANK_MAP
        self.timer = QTimer(self)
        self.timer.setInterval(90)
        self.timer.timeout.connect(self._tick)
        self._finished_winner = ""

    def create_room(
        self,
        host_name,
        max_players=2,
        team_mode="ffa",
        port=DEFAULT_TANK_PORT,
        map_id=DEFAULT_TANK_MAP,
        user_id="",
        skin_id="classic",
    ):
        self.close()
        self.host_name = str(host_name or socket.gethostname())[:24]
        self.host_user_id = str(user_id or "").strip()
        self.host_computer_name = local_computer_name()
        self.host_skin_id = normalize_arcade_skin(skin_id)
        self.max_players = max(2, min(4, int(max_players)))
        self.team_mode = "teams" if self.max_players == 4 and team_mode == "teams" else "ffa"
        self.map_id = normalize_tank_map_id(map_id)
        wanted_port = max(0, int(port))
        if not self.server.listen(QHostAddress.AnyIPv4, wanted_port):
            if wanted_port and not self.server.listen(QHostAddress.AnyIPv4, 0):
                raise RuntimeError(self.server.errorString())
        self.room_code = f"{socket.gethostname()}:{self.server.serverPort()}"
        self.engine.waiting_state([self.host_name, "等待加入"], self.map_id, [self.host_skin_id])
        self.state_changed.emit(self.engine.to_state())
        self.status_changed.emit(f"等待玩家加入（1/{self.max_players}）")
        return self.room_code

    def _accept_connections(self):
        while self.server.hasPendingConnections():
            socket_item = self.server.nextPendingConnection()
            socket_item.setProxy(_lan_no_proxy())
            if len(self.clients) >= self.max_players - 1 or self.engine.status == "playing":
                socket_item.write(_encoded_packet({"type": "error", "message": "房間已滿"}))
                socket_item.disconnectFromHost()
                continue
            slot = next(slot for slot in range(1, self.max_players) if slot not in self.clients)
            self.clients[slot] = socket_item
            self.buffers[slot] = bytearray()
            socket_item.readyRead.connect(lambda wanted=slot: self._read_client(wanted))
            socket_item.disconnected.connect(lambda wanted=slot: self._client_disconnected(wanted))
            socket_item.write(_encoded_packet({"type": "welcome", "slot": slot}))

    def _read_client(self, slot):
        client = self.clients.get(slot)
        if client is None:
            return
        buffer = self.buffers[slot]
        buffer.extend(bytes(client.readAll()))
        if len(buffer) > MAX_PACKET_BYTES * 4:
            client.abort()
            return
        while b"\n" in buffer:
            raw, _, remainder = buffer.partition(b"\n")
            buffer[:] = remainder
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if payload.get("type") == "join":
                if client.property("player_name"):
                    continue
                client.setProperty("skin_id", normalize_arcade_skin(payload.get("skin_id")))
                client.setProperty("player_name", str(payload.get("name", "玩家"))[:24])
                client.setProperty("user_id", str(payload.get("user_id", "")).strip())
                client.setProperty("computer_name", normalize_computer_name(payload.get("computer_name")))
                self._update_lobby()
            elif payload.get("type") == "action":
                self.engine.action(slot, payload.get("action", ""))
                self._emit_state()

    def _update_lobby(self):
        ready = [
            slot for slot, client in self.clients.items()
            if str(client.property("player_name") or "").strip()
        ]
        count = 1 + len(ready)
        self.player_count_changed.emit(count)
        if count < self.max_players:
            self.status_changed.emit(f"等待玩家加入（{count}/{self.max_players}）")
            return
        names = [self.host_name] + [
            str(self.clients[slot].property("player_name")) for slot in sorted(ready)
        ]
        skins = [self.host_skin_id] + [normalize_arcade_skin(self.clients[slot].property("skin_id")) for slot in sorted(ready)]
        self.engine.reset(names, self.team_mode, self.map_id, skins)
        self._finished_winner = ""
        self.timer.start()
        self.status_changed.emit("坦克死鬥開始")
        self._emit_state()

    def action(self, action):
        self.engine.action(0, action)
        self._emit_state()

    def winner_identities(self):
        return [(name, user_id) for name, user_id, _ in self.winner_ranking_identities()]

    def winner_ranking_identities(self):
        if self.engine.winner == "draw":
            return []
        if self.engine.winner.startswith("team:"):
            team = int(self.engine.winner.split(":", 1)[1])
            slots = [
                slot for slot, player in self.engine.players.items()
                if player.get("team") == team
            ]
        else:
            slots = [int(self.engine.winner)]
        identities = []
        for slot in slots:
            name = self.engine.players.get(slot, {}).get("name", "玩家")
            if slot == 0:
                user_id = self.host_user_id
                computer = self.host_computer_name
            else:
                client = self.clients.get(slot)
                user_id = str(client.property("user_id") or "") if client else ""
                computer = str(client.property("computer_name") or "") if client else ""
            identities.append((name, user_id, computer))
        return identities

    def _tick(self):
        self.engine.tick()
        self._emit_state()
        if self.engine.status == "finished" and self._finished_winner != self.engine.winner:
            self._finished_winner = self.engine.winner
            self.timer.stop()
            self.match_finished.emit(self.engine.winner_name())

    def _emit_state(self):
        state = self.engine.to_state()
        self.state_changed.emit(state)
        packet = _encoded_packet(state)
        for client in self.clients.values():
            if client.state() == QAbstractSocket.ConnectedState:
                client.write(packet)

    def _client_disconnected(self, slot):
        client = self.clients.pop(slot, None)
        self.buffers.pop(slot, None)
        if client is not None:
            client.deleteLater()
        self.timer.stop()
        if self.server.isListening():
            self.engine.waiting_state([self.host_name, "等待加入"], self.map_id, [self.host_skin_id])
            self.player_count_changed.emit(1 + len(self.clients))
            self.status_changed.emit("有玩家離線；等待房間重新補滿")
            self._emit_state()

    def close(self):
        self.timer.stop()
        clients = list(self.clients.values())
        self.clients.clear()
        self.buffers.clear()
        for client in clients:
            client.abort()
            client.deleteLater()
        self.server.close()
        self.room_code = ""


class TankClient(QObject):
    state_changed = Signal(object)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    match_finished = Signal(str)
    slot_changed = Signal(int)

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
        self.skin_id = "classic"
        self.slot = -1
        self._finished_winner = ""

    def connect_to_room(self, room_code, player_name, user_id="", skin_id="classic"):
        host, separator, port_text = str(room_code or "").strip().rpartition(":")
        if not separator or not host:
            raise ValueError("房間資料格式錯誤。")
        try:
            port = int(port_text)
        except ValueError as error:
            raise ValueError("房間連接埠不正確。") from error
        self.disconnect()
        self.player_name = str(player_name or socket.gethostname())[:24]
        self.user_id = str(user_id or "").strip()
        self.computer_name = local_computer_name()
        self.skin_id = normalize_arcade_skin(skin_id)
        self.status_changed.emit(f"正在連線至 {host}:{port}")
        self.socket.connectToHost(host, port)

    def _connected(self):
        self.socket.write(_encoded_packet({
            "type": "join",
            "name": self.player_name,
            "user_id": self.user_id,
            "computer_name": self.computer_name,
            "skin_id": self.skin_id,
        }))
        self.status_changed.emit("已加入房間，等待其他玩家")

    def send_action(self, action):
        if self.socket.state() == QAbstractSocket.ConnectedState:
            self.socket.write(_encoded_packet({"type": "action", "action": str(action)}))

    def _read_server(self):
        self._buffer.extend(bytes(self.socket.readAll()))
        while b"\n" in self._buffer:
            raw, _, remainder = self._buffer.partition(b"\n")
            self._buffer = bytearray(remainder)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if payload.get("type") == "welcome":
                self.slot = int(payload.get("slot", -1))
                self.slot_changed.emit(self.slot)
            elif payload.get("type") == "state":
                self.state_changed.emit(payload)
                if payload.get("status") == "finished" and payload.get("winner") != self._finished_winner:
                    self._finished_winner = str(payload.get("winner", ""))
                    self.match_finished.emit(str(payload.get("winner_name", "")))
            elif payload.get("type") == "error":
                self.error_occurred.emit(str(payload.get("message", "連線失敗")))

    def disconnect(self):
        self.socket.abort()
        self._buffer.clear()
        self.slot = -1
