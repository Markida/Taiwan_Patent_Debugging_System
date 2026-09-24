"""Append-only company LAN ranking for human-versus-human games."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import socket
from pathlib import Path
from threading import Thread
from typing import Optional
from uuid import uuid4

from app.features.chat_room.store import default_chat_room_root


GAME_WIN_POINTS = {
    "tetris": 3,
    "pong": 2,
    "tank": 2,
    "bulls_and_cows": 1,
    "snake": 1,
}


def normalize_computer_name(value):
    name = str(value or "").strip().upper()
    return name if re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{0,252}", name) else ""


def local_computer_name():
    return normalize_computer_name(socket.gethostname())


def computer_rank(entries, computer_name=None):
    computer = normalize_computer_name(computer_name) if computer_name is not None else local_computer_name()
    if not computer:
        return 0
    for rank, entry in enumerate(entries[:3], 1):
        if normalize_computer_name(getattr(entry, "computer_name", "")) == computer:
            return rank
    return 0


class GameRankingError(ValueError):
    pass


@dataclass(frozen=True)
class GameRankingEntry:
    user_id: str
    nickname: str
    points: int
    wins: int
    by_game: dict
    avatar_relative_path: str = ""
    computer_name: str = ""

    @property
    def display_name(self):
        return self.computer_name or "未識別電腦（舊紀錄）"


class GameRankingStore:
    """Use one immutable event per result so simultaneous PCs cannot overwrite."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or default_chat_room_root()) / "data" / "game_ranking"
        self.events_root = self.root / "events"
        # Results are immutable in normal use.  Cache parsed events between
        # polls; scanning directory metadata is much cheaper than reopening
        # every historical JSON over SMB.  Signatures still detect deliberate
        # corrections, and entries removed from the share leave the cache.
        self._event_cache = {}
        self._profile_cache = {}

    def _load_events(self):
        current = {}
        try:
            with os.scandir(self.events_root) as entries:
                for entry in entries:
                    if not entry.name.lower().endswith(".json"):
                        continue
                    try:
                        if not entry.is_file():
                            continue
                        info = entry.stat()
                        signature = (info.st_mtime_ns, info.st_size)
                        cached = self._event_cache.get(entry.name)
                        if cached is not None and cached[0] == signature:
                            current[entry.name] = cached
                            continue
                        payload = json.loads(
                            Path(entry.path).read_text(encoding="utf-8-sig")
                        )
                        if isinstance(payload, dict):
                            current[entry.name] = (signature, payload)
                    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                        # Retry incomplete or temporarily inaccessible files
                        # next time, without taking the whole ranking offline.
                        continue
        except FileNotFoundError:
            pass
        except OSError as error:
            raise GameRankingError(f"遊戲排行榜無法讀取：{error}") from error
        self._event_cache = current
        return [(Path(name), value[1]) for name, value in current.items()]

    @staticmethod
    def points_for(game_id):
        return max(1, int(GAME_WIN_POINTS.get(str(game_id or "").strip(), 1)))

    def _profile(self, user_id):
        # IDs received over LAN must not become arbitrary filesystem paths.
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", user_id):
            return {}
        try:
            path = self.root.parent / "profiles" / f"{user_id}.json"
            profile = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(profile, dict) or profile.get("user_id", user_id) != user_id:
                return {}
            if not normalize_computer_name(profile.get("identity_computer_name")):
                return {}
            self._profile_cache[user_id] = profile
            return profile
        except FileNotFoundError:
            self._profile_cache.pop(user_id, None)
            return {}
        except OSError:
            return self._profile_cache.get(user_id, {})
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return {}

    def record_win(self, nickname, game_id, *, match_id="", user_id="", computer_name=""):
        normalized_name = str(nickname or "").strip()[:24]
        normalized_game = str(game_id or "").strip() or "other"
        points = self.points_for(normalized_game)
        normalized_match = str(match_id or "").strip()
        normalized_user_id = str(user_id or "").strip()
        computer = normalize_computer_name(computer_name)
        if not computer:
            computer = normalize_computer_name(self._profile(normalized_user_id).get("identity_computer_name"))
        player_key = ("computer:" + computer) if computer else (
            normalized_user_id
            if normalized_user_id
            else "unidentified:" + uuid4().hex
        )
        event_id = (
            hashlib.sha256(f"{normalized_game}:{normalized_match}".encode("utf-8")).hexdigest()
            if normalized_match
            else uuid4().hex
        )
        payload = {
            "schema_version": 2,
            "computer_name": computer,
            "event_id": event_id,
            "match_id": normalized_match,
            "nickname": normalized_name,
            "user_id": normalized_user_id,
            "player_key": player_key,
            "game_id": normalized_game,
            "points": points,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        destination = self.events_root / f"{event_id}.json"
        temporary = self.events_root / f".{event_id}.{uuid4().hex}.tmp"
        try:
            self.events_root.mkdir(parents=True, exist_ok=True)
            if destination.is_file():
                return payload
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                temporary.replace(destination)
            except OSError:
                if not destination.is_file():
                    raise
                temporary.unlink(missing_ok=True)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise GameRankingError(f"遊戲積分無法寫入公司排行榜：{error}") from error
        return payload

    def leaderboard(self, limit=3):
        events = self._load_events()
        user_ids = {str(payload.get("user_id", "")).strip() for _, payload in events}
        profiles = {user_id: self._profile(user_id) for user_id in user_ids if user_id}
        known_computers = {}
        for _, payload in events:
            user_id = str(payload.get("user_id", "")).strip()
            computer = normalize_computer_name(payload.get("computer_name"))
            if user_id and computer:
                known_computers.setdefault(user_id, set()).add(computer)
        for user_id, profile in profiles.items():
            computer = normalize_computer_name(profile.get("identity_computer_name"))
            if computer:
                known_computers.setdefault(user_id, set()).add(computer)

        totals = {}
        for path, payload in events:
            try:
                user_id = str(payload.get("user_id", "")).strip()
                computer = normalize_computer_name(payload.get("computer_name"))
                candidates = known_computers.get(user_id, set())
                # Older events can be attributed only when identity evidence
                # points to exactly one machine. Never merge by nickname.
                if not computer and len(candidates) == 1:
                    computer = next(iter(candidates))
                legacy_key = user_id or str(payload.get("player_key", "")).strip() or path.stem
                key = ("computer:" + computer) if computer else ("legacy:" + legacy_key)
                game_id = str(payload.get("game_id", "other")).strip() or "other"
                points = max(0, int(payload.get("points", self.points_for(game_id))))
                current = totals.setdefault(key, {
                    "user_id": user_id, "nickname": "", "computer_name": computer,
                    "points": 0, "wins": 0, "by_game": {}, "avatar_relative_path": "",
                    "_latest_event": ("", ""),
                })
                event_order = (str(payload.get("created_at_utc", "")), str(payload.get("event_id", path.stem)))
                if event_order >= current["_latest_event"]:
                    current["user_id"] = user_id
                    # Retain nickname metadata for old files; UI and rewards
                    # exclusively use the computer identity.
                    current["nickname"] = str(payload.get("nickname", "")).strip()
                    profile = profiles.get(user_id, {})
                    profile_computer = normalize_computer_name(profile.get("identity_computer_name"))
                    if computer and profile_computer == computer:
                        current["nickname"] = str(profile.get("nickname") or current["nickname"]).strip()
                        current["avatar_relative_path"] = str(profile.get("avatar_relative_path", "")).strip()
                    else:
                        current["avatar_relative_path"] = ""
                    current["_latest_event"] = event_order
                current["points"] += points
                current["wins"] += 1
                current["by_game"][game_id] = current["by_game"].get(game_id, 0)+1
            except (TypeError, ValueError):
                continue
        entries = [
            GameRankingEntry(**{key: value for key, value in row.items() if not key.startswith("_")})
            for row in totals.values()
        ]
        entries.sort(key=lambda entry: (-entry.points, -entry.wins, entry.computer_name or "~"+entry.user_id))
        return entries[:max(0, int(limit))]


def award_multiplayer_victory(
    nickname,
    game_id,
    *,
    match_id="",
    user_id="",
    computer_name="",
    store=None,
):
    """Persist one human-versus-human win without blocking the GUI thread."""

    target = store or GameRankingStore()

    def write():
        try:
            target.record_win(
                nickname,
                game_id,
                match_id=match_id,
                user_id=user_id,
                computer_name=computer_name,
            )
        except GameRankingError:
            return

    thread = Thread(target=write, name="SaintIslandGameRanking", daemon=True)
    thread.start()
    return thread
