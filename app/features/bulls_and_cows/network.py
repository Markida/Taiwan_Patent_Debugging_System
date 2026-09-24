"""Server-authoritative LAN mode for the hidden 1A2B game."""

from __future__ import annotations

from app.features.chat_room.game_ranking import local_computer_name, normalize_computer_name


from uuid import uuid4
from app.features.arcade_cosmetics import normalize_arcade_skin


from PySide6.QtCore import QObject, Signal

from app.features.bulls_and_cows.engine import score_guess, validate_number
from app.features.chat_room.arcade_lan import TwoPlayerLanClient, TwoPlayerLanHost


class BullsAndCowsHost(QObject):
    state_changed = Signal(object)
    guest_joined = Signal(str)
    guest_left = Signal()
    match_finished = Signal(str)

    def __init__(self, parent=None, transport=None):
        super().__init__(parent)
        self.transport = transport or TwoPlayerLanHost(self)
        self.transport.guest_joined.connect(self._guest_joined)
        self.transport.guest_left.connect(self.guest_left)
        self.transport.message_received.connect(self._message)
        self.host_name = "房主"
        self.host_user_id = ""
        self.host_computer_name = local_computer_name()
        self.guest_name = "訪客"
        self.host_skin_id = "classic"
        self.guest_skin_id = "classic"
        self.match_id = uuid4().hex
        self.secrets = {"left": "", "right": ""}
        self.history = []
        self.active_side = "left"
        self.status = "waiting"
        self.winner = ""

    @property
    def room_code(self):
        return self.transport.room_code

    def create_room(self, host_name, port=0, user_id="", skin_id="classic"):
        self.host_name = str(host_name or "房主")[:24]
        self.host_user_id = str(user_id or "").strip()
        self.host_computer_name = local_computer_name()
        self.host_skin_id = normalize_arcade_skin(skin_id)
        self.guest_skin_id = "classic"
        self.reset()
        return self.transport.create_room(port)

    def reset(self):
        self.match_id = uuid4().hex
        self.secrets = {"left": "", "right": ""}
        self.history = []
        self.active_side = "left"
        self.status = "waiting"
        self.winner = ""
        self._emit()

    def _guest_joined(self, name):
        self.guest_name = str(name or "訪客")[:24]
        self.guest_skin_id = normalize_arcade_skin(getattr(self.transport,"guest_skin_id","classic"))
        self.guest_joined.emit(self.guest_name)
        self._emit()

    def set_secret(self, side, value):
        self.secrets[str(side)] = validate_number(value)
        if self.secrets["left"] and self.secrets["right"]:
            self.status = "playing"
        else:
            self.status = "setup"
        self._emit()

    def submit_guess(self, side, value):
        side = str(side)
        if self.status != "playing" or side != self.active_side:
            return False
        guess = validate_number(value)
        target = "right" if side == "left" else "left"
        result = score_guess(self.secrets[target], guess)
        entry = {
            "turn": len(self.history) + 1,
            "side": side,
            "name": self.host_name if side == "left" else self.guest_name,
            "guess": guess,
            "result": result.notation,
        }
        self.history.append(entry)
        if result.won:
            self.status = "finished"
            self.winner = side
            self.match_finished.emit(entry["name"])
        else:
            self.active_side = target
        self._emit()
        return True

    def _message(self, payload):
        if payload.get("type") == "secret":
            try:
                self.set_secret("right", payload.get("value", ""))
            except ValueError as error:
                self.transport.send({"type": "error", "message": str(error)})
        elif payload.get("type") == "guess":
            try:
                self.submit_guess("right", payload.get("value", ""))
            except ValueError as error:
                self.transport.send({"type": "error", "message": str(error)})

    def state(self):
        return {
            "type": "state",
            "match_id": self.match_id,
            "left_skin": self.host_skin_id,
            "right_skin": self.guest_skin_id,
            "status": self.status,
            "winner": self.winner,
            "left_name": self.host_name,
            "right_name": self.guest_name,
            "left_ready": bool(self.secrets["left"]),
            "right_ready": bool(self.secrets["right"]),
            "active_side": self.active_side,
            "history": list(self.history),
        }

    def _emit(self):
        state = self.state()
        self.state_changed.emit(state)
        self.transport.send(state)

    def close(self):
        self.transport.close()

    def winner_computer_name(self):
        if self.winner == "left":
            return self.host_computer_name
        if self.winner == "right":
            return getattr(self.transport, "guest_computer_name", "")
        return ""

    def winner_user_id(self):
        return (
            self.host_user_id
            if self.winner == "left"
            else self.transport.guest_user_id
        )


class BullsAndCowsClient(QObject):
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

    def set_secret(self, value):
        self.transport.send({"type": "secret", "value": validate_number(value)})

    def submit_guess(self, value):
        self.transport.send({"type": "guess", "value": validate_number(value)})

    def _message(self, payload):
        if payload.get("type") != "state":
            return
        self.state_changed.emit(payload)
        if payload.get("status") == "finished":
            winner = payload.get("left_name") if payload.get("winner") == "left" else payload.get("right_name")
            if winner and winner != self._winner:
                self._winner = str(winner)
                self.match_finished.emit(self._winner)

    def disconnect(self):
        self.transport.disconnect()
