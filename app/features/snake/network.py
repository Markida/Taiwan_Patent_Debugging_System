"""Server-authoritative two-player LAN snake battle."""

from __future__ import annotations

from app.features.chat_room.game_ranking import local_computer_name, normalize_computer_name


from app.features.arcade_cosmetics import ArcadeEventLog, arcade_skin, normalize_arcade_skin


import random

from PySide6.QtCore import QObject, QTimer, Signal

from app.features.chat_room.arcade_lan import TwoPlayerLanClient, TwoPlayerLanHost


class SnakeBattleEngine:
    COLUMNS = 30
    ROWS = 22
    VECTORS = {
        "left": (-1, 0),
        "right": (1, 0),
        "up": (0, -1),
        "down": (0, 1),
    }
    OPPOSITE = {"left": "right", "right": "left", "up": "down", "down": "up"}

    def __init__(self, random_source=None):
        self.random = random_source or random.Random()
        self.reset()

    def reset(self):
        self.events = ArcadeEventLog()
        self.skins = {"left":"classic","right":"classic"}
        middle = self.ROWS // 2
        self.snakes = {
            "left": [(7, middle), (6, middle), (5, middle)],
            "right": [(22, middle), (23, middle), (24, middle)],
        }
        self.directions = {"left": "right", "right": "left"}
        self.pending = dict(self.directions)
        self.scores = {"left": 0, "right": 0}
        self.status = "playing"
        self.winner = ""
        self.food = self._empty_cell()

    def _empty_cell(self):
        occupied = set(self.snakes["left"]) | set(self.snakes["right"])
        cells = [
            (x, y)
            for y in range(self.ROWS)
            for x in range(self.COLUMNS)
            if (x, y) not in occupied
        ]
        return self.random.choice(cells) if cells else None

    def action(self, side, direction):
        side = str(side)
        direction = str(direction)
        if side not in self.snakes or direction not in self.VECTORS:
            return
        if direction != self.OPPOSITE[self.directions[side]]:
            self.pending[side] = direction

    def tick(self):
        if self.status != "playing":
            return
        heads = {}
        growing = {}
        for side in ("left", "right"):
            self.directions[side] = self.pending[side]
            dx, dy = self.VECTORS[self.directions[side]]
            x, y = self.snakes[side][0]
            heads[side] = (x + dx, y + dy)
            growing[side] = heads[side] == self.food

        dead = set()
        if heads["left"] == heads["right"]:
            dead.update(("left", "right"))
        for side, opponent in (("left", "right"), ("right", "left")):
            x, y = heads[side]
            own_body = self.snakes[side] if growing[side] else self.snakes[side][:-1]
            other_body = self.snakes[opponent] if growing[opponent] else self.snakes[opponent][:-1]
            if (
                x < 0 or x >= self.COLUMNS or y < 0 or y >= self.ROWS
                or heads[side] in own_body
                or heads[side] in other_body
            ):
                dead.add(side)
        if dead:
            for side in sorted(dead):
                x,y = self.snakes[side][0]
                self.events.emit("explosion", x, y, color="#fb7185", text="碰撞！")
            self.status = "finished"
            if len(dead) == 2:
                self.winner = "draw"
            else:
                self.winner = "right" if "left" in dead else "left"
            self.events.emit("victory", self.COLUMNS/2, self.ROWS*.7, text="對戰結束")
            return

        ate = False
        for side in ("left", "right"):
            self.snakes[side].insert(0, heads[side])
            if growing[side]:
                self.scores[side] += 1
                self.events.emit("eat", heads[side][0], heads[side][1], text="+1",
                                 color=arcade_skin("snake",self.skins[side]).accent)
                ate = True
            else:
                self.snakes[side].pop()
        if ate:
            self.food = self._empty_cell()

    def to_state(self, left_name="房主", right_name="訪客"):
        return {
            "type": "state",
            **self.events.state(),
            "left_skin": self.skins["left"],
            "right_skin": self.skins["right"],
            "status": self.status,
            "winner": self.winner,
            "left_name": left_name,
            "right_name": right_name,
            "left_snake": [list(cell) for cell in self.snakes["left"]],
            "right_snake": [list(cell) for cell in self.snakes["right"]],
            "left_score": self.scores["left"],
            "right_score": self.scores["right"],
            "food": list(self.food) if self.food else None,
        }


class SnakeHost(QObject):
    state_changed = Signal(object)
    guest_joined = Signal(str)
    guest_left = Signal()
    match_finished = Signal(str)

    def __init__(self, parent=None, *, random_source=None, transport=None):
        super().__init__(parent)
        self.transport = transport or TwoPlayerLanHost(self)
        self.transport.guest_joined.connect(self._guest_joined)
        self.transport.guest_left.connect(self._guest_left)
        self.transport.message_received.connect(self._message)
        self.engine = SnakeBattleEngine(random_source)
        self.host_name = "房主"
        self.host_user_id = ""
        self.host_computer_name = local_computer_name()
        self.host_skin_id = "classic"
        self.guest_name = "訪客"
        self.timer = QTimer(self)
        self.timer.setInterval(115)
        self.timer.timeout.connect(self._tick)
        self._announced_winner = ""

    @property
    def room_code(self):
        return self.transport.room_code

    def create_room(self, host_name, port=0, user_id="", skin_id="classic"):
        self.host_name = str(host_name or "房主")[:24]
        self.host_user_id = str(user_id or "").strip()
        self.host_computer_name = local_computer_name()
        self.host_skin_id = normalize_arcade_skin(skin_id)
        self.engine.reset()
        self.engine.skins["left"] = self.host_skin_id
        self.engine.status = "waiting"
        return self.transport.create_room(port)

    def _guest_joined(self, name):
        self.guest_name = str(name or "訪客")[:24]
        self.engine.reset()
        self.engine.skins = {"left": self.host_skin_id, "right": normalize_arcade_skin(getattr(self.transport,"guest_skin_id","classic"))}
        self._announced_winner = ""
        self.guest_joined.emit(self.guest_name)
        self.timer.start()
        self._emit()

    def _guest_left(self):
        self.timer.stop()
        self.engine.status = "waiting"
        self.guest_left.emit()
        self._emit()

    def _message(self, payload):
        if payload.get("type") == "action":
            self.action("right", payload.get("direction", ""))

    def action(self, side, direction):
        self.engine.action(side, direction)

    def _tick(self):
        was_playing = self.engine.status == "playing"
        self.engine.tick()
        self._emit()
        if was_playing and self.engine.status == "finished":
            self.timer.stop()
            winner = self.winner_name()
            if winner != self._announced_winner:
                self._announced_winner = winner
                self.match_finished.emit(winner)

    def winner_name(self):
        if self.engine.winner == "left":
            return self.host_name
        if self.engine.winner == "right":
            return self.guest_name
        return "平手"

    def state(self):
        return self.engine.to_state(self.host_name, self.guest_name)

    def _emit(self):
        state = self.state()
        self.state_changed.emit(state)
        self.transport.send(state)

    def close(self):
        self.timer.stop()
        self.transport.close()

    def winner_computer_name(self):
        if self.engine.winner == "left":
            return self.host_computer_name
        if self.engine.winner == "right":
            return getattr(self.transport, "guest_computer_name", "")
        return ""

    def winner_user_id(self):
        return (
            self.host_user_id
            if self.engine.winner == "left"
            else self.transport.guest_user_id
        )


class SnakeClient(QObject):
    state_changed = Signal(object)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    match_finished = Signal(str)

    def __init__(self, parent=None, transport=None):
        super().__init__(parent)
        self.transport = transport or TwoPlayerLanClient(self)
        self.transport.message_received.connect(self._message)
        self.transport.status_changed.connect(self.status_changed)
        self.transport.error_occurred.connect(self.error_occurred)
        self._winner = ""

    def connect_to_room(self, room_code, player_name, user_id="", skin_id="classic"):
        self._winner = ""
        self.transport.connect_to_room(room_code, player_name, user_id, skin_id=normalize_arcade_skin(skin_id))

    def action(self, direction):
        self.transport.send({"type": "action", "direction": str(direction)})

    def _message(self, payload):
        if payload.get("type") != "state":
            return
        self.state_changed.emit(payload)
        if payload.get("status") == "finished":
            winner = "平手" if payload.get("winner") == "draw" else (
                payload.get("left_name") if payload.get("winner") == "left" else payload.get("right_name")
            )
            if winner != self._winner:
                self._winner = str(winner)
                self.match_finished.emit(self._winner)

    def disconnect(self):
        self.transport.disconnect()
