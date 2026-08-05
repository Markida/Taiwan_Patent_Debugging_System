"""Persistent score table for the hidden snake game."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import unicodedata
from typing import Iterable, List, Optional

from app.paths import get_app_install_dir


SNAKE_SCORE_FILENAME = "snake_scores.json"
SNAKE_SCORE_SCHEMA_VERSION = 1
MAX_PLAYER_NAME_LENGTH = 20
MAX_STORED_SCORES = 100


class SnakeScoreError(ValueError):
    """Raised when a score or the persistent score file is invalid."""


class SnakeScoreStorageError(SnakeScoreError):
    """Raised when scores cannot be read or written."""


def default_snake_score_path() -> Path:
    # Deliberately kept out of ordinary output/config locations.  This exact
    # nested path is also copied unchanged into portable company installations.
    return (
        get_app_install_dir()
        / "app"
        / "features"
        / "snake"
        / SNAKE_SCORE_FILENAME
    )


def normalize_player_name(value: object) -> str:
    name = unicodedata.normalize("NFC", str(value or "")).strip()
    if not name:
        raise SnakeScoreError("請輸入排行榜名稱。")
    if any(character in name for character in "\r\n"):
        raise SnakeScoreError("排行榜名稱只能使用單行文字。")
    if len(name) > MAX_PLAYER_NAME_LENGTH:
        raise SnakeScoreError(
            f"排行榜名稱不可超過 {MAX_PLAYER_NAME_LENGTH} 個字元。"
        )
    return name


@dataclass(frozen=True)
class SnakeScoreEntry:
    name: str
    score: int
    recorded_at_utc: str


class SnakeScoreStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or default_snake_score_path())

    def load(self) -> List[SnakeScoreEntry]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise TypeError("score file root must be an object")
            raw_scores = payload.get("scores", [])
            if not isinstance(raw_scores, list):
                raise TypeError("scores must be a list")
            entries = []
            for raw in raw_scores:
                if not isinstance(raw, dict):
                    raise TypeError("each score must be an object")
                name = normalize_player_name(raw.get("name", ""))
                score = max(0, int(raw.get("score", 0)))
                recorded_at = str(raw.get("recorded_at_utc", ""))
                entries.append(SnakeScoreEntry(name, score, recorded_at))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise SnakeScoreStorageError(
                f"無法讀取貪食蛇排行榜：{self.path}\n{error}"
            ) from error
        return self._sorted(entries)

    def save(self, entries: Iterable[SnakeScoreEntry]) -> None:
        normalized = self._sorted(entries)[:MAX_STORED_SCORES]
        payload = {
            "schema_version": SNAKE_SCORE_SCHEMA_VERSION,
            "scores": [asdict(entry) for entry in normalized],
        }
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        except OSError as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise SnakeScoreStorageError(
                f"無法儲存貪食蛇排行榜：{self.path}\n{error}"
            ) from error

    def record(self, name: object, score: object) -> SnakeScoreEntry:
        normalized_name = normalize_player_name(name)
        try:
            normalized_score = max(0, int(score))
        except (TypeError, ValueError) as error:
            raise SnakeScoreError("分數格式不正確。") from error
        entry = SnakeScoreEntry(
            name=normalized_name,
            score=normalized_score,
            recorded_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        entries = self.load()
        entries.append(entry)
        self.save(entries)
        return entry

    def top(self, limit: int = 3) -> List[SnakeScoreEntry]:
        return self.load()[:max(0, int(limit))]

    @staticmethod
    def _sorted(entries: Iterable[SnakeScoreEntry]) -> List[SnakeScoreEntry]:
        return sorted(
            entries,
            key=lambda entry: (-entry.score, entry.recorded_at_utc, entry.name),
        )
