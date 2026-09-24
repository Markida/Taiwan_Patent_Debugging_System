"""File-per-message storage for the hidden company LAN chat room."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
from threading import Lock, Thread
from time import monotonic
from typing import Iterable, Optional
import unicodedata
from uuid import uuid4

from app.paths import get_app_install_dir


CHAT_SCHEMA_VERSION = 2
CHAT_PROFILE_SCHEMA_VERSION = 3
CHAT_POLL_INTERVAL_SECONDS = 1.0
MAX_NICKNAME_LENGTH = 24
MAX_MESSAGE_LENGTH = 3000
MAX_IMAGE_BYTES = 20 * 1024 * 1024
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
ALLOWED_REACTION_EMOJIS = (
    "👍", "❤️", "😂", "😮", "😢", "😡", "👏", "🎉", "🔥", "🙏",
)
CHAT_ROOT_ENVIRONMENT_VARIABLE = "SAINT_ISLAND_CHAT_ROOM_ROOT"
DEFAULT_COMPANY_CHAT_ROOT = Path(
    r"\\CPC2856\Documents\features\chat_room"
)
GAME_REPORT_AVATAR_PATH = Path(__file__).with_name("game_report_avatar.jpg")
AI_DIFFICULTY_REPORTS = {
    "easy": ("簡單", "恭喜"),
    "normal": ("普通", "厲害厲害"),
    "hard": ("困難", "太神啦!!!"),
}


_message_clock_lock = Lock()
_last_message_created_at = None


def _next_message_created_at() -> datetime:
    """Return a process-wide increasing timestamp for rapid local sends."""

    global _last_message_created_at
    now = datetime.now(timezone.utc)
    with _message_clock_lock:
        if _last_message_created_at is not None and now <= _last_message_created_at:
            now = _last_message_created_at + timedelta(microseconds=1)
        _last_message_created_at = now
    return now


def format_ai_victory_announcement(player, game_title, difficulty):
    """Return one consistent AI victory report for every hidden game."""

    label, praise = AI_DIFFICULTY_REPORTS.get(
        str(difficulty or "normal"),
        AI_DIFFICULTY_REPORTS["normal"],
    )
    return f'"{player}"在{game_title}中戰勝了{label}難度的 AI，{praise}'


def publish_game_announcement(text: object, *, store=None) -> Thread:
    """Publish a game notice without ever blocking the GUI on the SMB share."""

    normalized = normalize_message_text(text)
    target_store = store or ChatRoomStore()

    def publish():
        try:
            target_store.send_message(
                "遊戲戰報",
                normalized,
                user_id="game_notice",
                avatar_path=(
                    GAME_REPORT_AVATAR_PATH
                    if GAME_REPORT_AVATAR_PATH.is_file()
                    else None
                ),
            )
        except ChatRoomError:
            # A temporary share outage must not interrupt or crash a game.
            return

    thread = Thread(
        target=publish,
        name="SaintIslandGameAnnouncement",
        daemon=True,
    )
    thread.start()
    return thread


def publish_game_invitation(
    game_id: object,
    game_title: object,
    room_payload: object,
    host_name: object,
    *,
    store=None,
) -> Thread:
    """Publish a durable, clickable room invitation without blocking Qt."""

    target_store = store or ChatRoomStore()
    normalized_host = normalize_nickname(host_name)
    title = str(game_title or "對戰遊戲").strip() or "對戰遊戲"
    metadata = {
        "game_id": str(game_id or "").strip(),
        "game_title": title,
        "room": dict(room_payload) if isinstance(room_payload, dict) else {},
    }

    def publish():
        try:
            target_store.send_message(
                "對戰邀請",
                f"{normalized_host} 建立了「{title}」房間，點此直接加入。",
                user_id="game_invitation",
                message_type="game_invitation",
                metadata=metadata,
            )
        except ChatRoomError:
            return

    thread = Thread(
        target=publish,
        name="SaintIslandGameInvitation",
        daemon=True,
    )
    thread.start()
    return thread


class ChatRoomError(ValueError):
    """Raised when chat content or the shared storage cannot be used."""


class ChatRoomStorageError(ChatRoomError):
    """Raised when a local profile or shared chat file cannot be accessed."""


def default_chat_room_root() -> Path:
    override = os.environ.get(CHAT_ROOT_ENVIRONMENT_VARIABLE, "").strip()
    return Path(override) if override else DEFAULT_COMPANY_CHAT_ROOT


def default_chat_profile_path() -> Path:
    # This is intentionally nested below app/features/chat_room. The update
    # builder and installer copy program files without deleting this profile.
    return (
        get_app_install_dir()
        / "app"
        / "features"
        / "chat_room"
        / "data"
        / "settings"
        / "chat_profile.json"
    )


def normalize_nickname(value: object) -> str:
    nickname = unicodedata.normalize("NFC", str(value or "")).strip()
    if not nickname:
        raise ChatRoomError("請先輸入聊天室暱稱。")
    if len(nickname) > MAX_NICKNAME_LENGTH:
        raise ChatRoomError(
            f"聊天室暱稱不可超過 {MAX_NICKNAME_LENGTH} 個字元。"
        )
    if any(character in "\r\n" or ord(character) < 32 for character in nickname):
        raise ChatRoomError("聊天室暱稱只能使用單行可見文字。")
    return nickname


def normalize_message_text(value: object) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    if len(text) > MAX_MESSAGE_LENGTH:
        raise ChatRoomError(
            f"單則訊息不可超過 {MAX_MESSAGE_LENGTH} 個字元。"
        )
    return text


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    sender: str
    computer_name: str
    text: str
    created_at_utc: str
    user_id: str = ""
    avatar_relative_path: str = ""
    attachment_relative_path: str = ""
    attachment_name: str = ""
    reply_to_message_id: str = ""
    reply_sender: str = ""
    reply_text: str = ""
    reply_avatar_relative_path: str = ""
    message_type: str = "message"
    metadata: Optional[dict] = None

    @classmethod
    def from_payload(cls, payload: object) -> "ChatMessage":
        if not isinstance(payload, dict):
            raise TypeError("chat message root must be an object")
        message_id = str(payload.get("id", "")).strip()
        created_at = str(payload.get("created_at_utc", "")).strip()
        if not message_id or not created_at:
            raise ValueError("chat message id or timestamp is missing")
        return cls(
            message_id=message_id,
            sender=normalize_nickname(payload.get("sender", "")),
            computer_name=str(payload.get("computer_name", "")).strip(),
            text=normalize_message_text(payload.get("text", "")),
            created_at_utc=created_at,
            user_id=str(payload.get("user_id", "")).strip(),
            avatar_relative_path=str(
                payload.get("avatar_relative_path", "")
            ).strip(),
            attachment_relative_path=str(
                payload.get("attachment_relative_path", "")
            ).strip(),
            attachment_name=str(payload.get("attachment_name", "")).strip(),
            reply_to_message_id=str(
                payload.get("reply_to_message_id", "")
            ).strip(),
            reply_sender=str(payload.get("reply_sender", "")).strip(),
            reply_text=normalize_message_text(payload.get("reply_text", "")),
            reply_avatar_relative_path=str(
                payload.get("reply_avatar_relative_path", "")
            ).strip(),
            message_type=(
                str(payload.get("message_type", "message")).strip()
                or "message"
            ),
            metadata=(
                dict(payload.get("metadata", {}))
                if isinstance(payload.get("metadata"), dict)
                else {}
            ),
        )

    def to_payload(self) -> dict:
        return {
            "schema_version": CHAT_SCHEMA_VERSION,
            "id": self.message_id,
            "sender": self.sender,
            "computer_name": self.computer_name,
            "text": self.text,
            "created_at_utc": self.created_at_utc,
            "user_id": self.user_id,
            "avatar_relative_path": self.avatar_relative_path,
            "attachment_relative_path": self.attachment_relative_path,
            "attachment_name": self.attachment_name,
            "reply_to_message_id": self.reply_to_message_id,
            "reply_sender": self.reply_sender,
            "reply_text": self.reply_text,
            "reply_avatar_relative_path": self.reply_avatar_relative_path,
            "message_type": self.message_type,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class ChatReaction:
    message_id: str
    user_id: str
    computer_name: str
    nickname: str
    emoji: str
    updated_at_utc: str

    @classmethod
    def from_payload(cls, payload: object) -> "ChatReaction":
        if not isinstance(payload, dict):
            raise TypeError("chat reaction root must be an object")
        reaction = cls(
            message_id=str(payload.get("message_id", "")).strip(),
            user_id=str(payload.get("user_id", "")).strip(),
            computer_name=str(payload.get("computer_name", "")).strip(),
            nickname=str(payload.get("nickname", "")).strip(),
            emoji=str(payload.get("emoji", "")).strip(),
            updated_at_utc=str(payload.get("updated_at_utc", "")).strip(),
        )
        if (
            not reaction.message_id
            or not reaction.user_id
            or reaction.emoji not in ALLOWED_REACTION_EMOJIS
        ):
            raise ValueError("invalid reaction")
        return reaction

    def to_payload(self) -> dict:
        return {
            "schema_version": CHAT_SCHEMA_VERSION,
            "message_id": self.message_id,
            "user_id": self.user_id,
            "computer_name": self.computer_name,
            "nickname": self.nickname,
            "emoji": self.emoji,
            "updated_at_utc": self.updated_at_utc,
        }


@dataclass(frozen=True)
class ChatProfile:
    user_id: str
    nickname: str = ""
    avatar_path: str = ""
    notifications_enabled: bool = True
    last_read_message_id: str = ""


@dataclass(frozen=True)
class ChatPresence:
    user_id: str
    session_id: str
    nickname: str
    computer_name: str
    last_seen_utc: str
    avatar_relative_path: str = ""

    @classmethod
    def from_payload(cls, payload: object) -> "ChatPresence":
        if not isinstance(payload, dict):
            raise TypeError("presence root must be an object")
        user_id = str(payload.get("user_id", "")).strip()
        session_id = str(payload.get("session_id", "")).strip()
        last_seen = str(payload.get("last_seen_utc", "")).strip()
        if not user_id or not session_id or not last_seen:
            raise ValueError("presence identity or timestamp is missing")
        return cls(
            user_id=user_id,
            session_id=session_id,
            nickname=normalize_nickname(payload.get("nickname", "")),
            computer_name=str(payload.get("computer_name", "")).strip(),
            last_seen_utc=last_seen,
            avatar_relative_path=str(
                payload.get("avatar_relative_path", "")
            ).strip(),
        )

    def to_payload(self) -> dict:
        return {
            "schema_version": CHAT_SCHEMA_VERSION,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "nickname": self.nickname,
            "computer_name": self.computer_name,
            "last_seen_utc": self.last_seen_utc,
            "avatar_relative_path": self.avatar_relative_path,
        }


@dataclass(frozen=True)
class ChatReadReceipt:
    user_id: str
    nickname: str
    computer_name: str
    last_read_message_id: str
    last_read_created_at_utc: str
    updated_at_utc: str

    @classmethod
    def from_payload(cls, payload: object) -> "ChatReadReceipt":
        if not isinstance(payload, dict):
            raise TypeError("read receipt root must be an object")
        user_id = str(payload.get("user_id", "")).strip()
        message_id = str(payload.get("last_read_message_id", "")).strip()
        message_time = str(payload.get("last_read_created_at_utc", "")).strip()
        updated_at = str(payload.get("updated_at_utc", "")).strip()
        if not user_id or not message_id or not message_time or not updated_at:
            raise ValueError("read receipt identity or cursor is missing")
        return cls(
            user_id=user_id,
            nickname=str(payload.get("nickname", "")).strip(),
            computer_name=str(payload.get("computer_name", "")).strip(),
            last_read_message_id=message_id,
            last_read_created_at_utc=message_time,
            updated_at_utc=updated_at,
        )

    def to_payload(self) -> dict:
        return {
            "schema_version": CHAT_SCHEMA_VERSION,
            "user_id": self.user_id,
            "nickname": self.nickname,
            "computer_name": self.computer_name,
            "last_read_message_id": self.last_read_message_id,
            "last_read_created_at_utc": self.last_read_created_at_utc,
            "updated_at_utc": self.updated_at_utc,
        }


class ChatProfileStore:
    """Persist the last nickname on each individual company computer."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or default_chat_profile_path())

    def _load_payload(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise TypeError("profile root must be an object")
            return payload
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise ChatRoomStorageError(
                f"無法讀取上次使用的聊天室暱稱：{self.path}\n{error}"
            ) from error

    def load_profile(self) -> ChatProfile:
        payload = self._load_payload()
        nickname_value = payload.get("nickname", "")
        nickname = (
            normalize_nickname(nickname_value)
            if str(nickname_value).strip()
            else ""
        )
        user_id = str(payload.get("user_id", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", user_id):
            user_id = uuid4().hex
        else:
            # Some early company builds were copied together with their local
            # profile.  That gave several computers the same ``user_id`` and
            # made shared data (most visibly the game ranking nickname) flip
            # between those users.  Bind legacy/copied identities to the
            # current computer without changing the on-disk profile merely by
            # reading it.  The derived id is deterministic and is persisted on
            # the next normal profile save.
            current_computer = socket.gethostname().strip().casefold()
            bound_computer = str(
                payload.get("identity_computer_name", "")
            ).strip().casefold()
            if current_computer and bound_computer != current_computer:
                user_id = hashlib.sha256(
                    f"{user_id}\0{current_computer}".encode("utf-8")
                ).hexdigest()
        avatar_file = Path(str(payload.get("avatar_file", ""))).name
        avatar_path = self.path.parent / avatar_file if avatar_file else None
        return ChatProfile(
            user_id=user_id,
            nickname=nickname,
            avatar_path=(
                str(avatar_path)
                if avatar_path is not None and avatar_path.is_file()
                else ""
            ),
            notifications_enabled=bool(
                payload.get("notifications_enabled", True)
            ),
            last_read_message_id=str(
                payload.get("last_read_message_id", "")
            ).strip(),
        )

    def load_nickname(self) -> str:
        return self.load_profile().nickname

    def _save_profile(self, profile: ChatProfile) -> ChatProfile:
        avatar_file = Path(profile.avatar_path).name if profile.avatar_path else ""
        payload = {
            "schema_version": CHAT_PROFILE_SCHEMA_VERSION,
            "user_id": profile.user_id,
            "identity_computer_name": socket.gethostname().strip(),
            "nickname": profile.nickname,
            "avatar_file": avatar_file,
            "notifications_enabled": profile.notifications_enabled,
            "last_read_message_id": profile.last_read_message_id,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
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
            raise ChatRoomStorageError(
                f"無法保存聊天室個人設定：{self.path}\n{error}"
            ) from error
        return profile

    def save_nickname(self, nickname: object) -> str:
        normalized = normalize_nickname(nickname)
        current = self.load_profile()
        self._save_profile(
            ChatProfile(
                user_id=current.user_id,
                nickname=normalized,
                avatar_path=current.avatar_path,
                notifications_enabled=current.notifications_enabled,
                last_read_message_id=current.last_read_message_id,
            )
        )
        return normalized

    def save_notifications_enabled(self, enabled: object) -> bool:
        current = self.load_profile()
        normalized = bool(enabled)
        self._save_profile(
            ChatProfile(
                user_id=current.user_id,
                nickname=current.nickname,
                avatar_path=current.avatar_path,
                notifications_enabled=normalized,
                last_read_message_id=current.last_read_message_id,
            )
        )
        return normalized

    def save_last_read_message_id(self, message_id: object) -> str:
        current = self.load_profile()
        normalized = str(message_id or "").strip()[:180]
        self._save_profile(
            ChatProfile(
                user_id=current.user_id,
                nickname=current.nickname,
                avatar_path=current.avatar_path,
                notifications_enabled=current.notifications_enabled,
                last_read_message_id=normalized,
            )
        )
        return normalized

    def save_avatar(self, source_path: Path) -> str:
        source = Path(source_path)
        if not source.is_file():
            raise ChatRoomError(f"找不到頭貼圖片：{source}")
        suffix = source.suffix.lower()
        if suffix not in ALLOWED_IMAGE_SUFFIXES:
            raise ChatRoomError("頭貼只接受 PNG、JPG、JPEG 或 BMP 圖片。")
        temporary_path = None
        try:
            if source.stat().st_size > MAX_IMAGE_BYTES:
                raise ChatRoomError("頭貼圖片不可超過 20 MB。")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            avatar_path = self.path.parent / f"chat_avatar_{digest}{suffix}"
            temporary_path = avatar_path.with_suffix(avatar_path.suffix + ".tmp")
            shutil.copyfile(source, temporary_path)
            temporary_path.replace(avatar_path)
        except OSError as error:
            try:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ChatRoomStorageError(
                f"無法保存聊天室頭貼：{self.path.parent}\n{error}"
            ) from error
        current = self.load_profile()
        self._save_profile(
            ChatProfile(
                user_id=current.user_id,
                nickname=current.nickname,
                avatar_path=str(avatar_path),
                notifications_enabled=current.notifications_enabled,
                last_read_message_id=current.last_read_message_id,
            )
        )
        return str(avatar_path)


class ChatRoomStore:
    """Coordinate multiple clients without a shared mutable database file."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or default_chat_room_root())
        self.data_root = self.root / "data"
        self.messages_root = self.data_root / "messages"
        self.images_root = self.data_root / "images"
        self.avatars_root = self.data_root / "avatars"
        self.presence_root = self.data_root / "presence"
        self.read_receipts_root = self.data_root / "read_receipts"
        self.reactions_root = self.data_root / "reactions"
        self.shared_profiles_root = self.data_root / "profiles"
        self._structure_ready = False
        self._avatar_upload_cache = {}
        self._shared_profile_cache = {}
        self._message_cache = OrderedDict()

    def ensure_available(self) -> None:
        if self._structure_ready:
            return
        probe_path = self.data_root / f".chat_probe_{uuid4().hex}.tmp"
        try:
            self.messages_root.mkdir(parents=True, exist_ok=True)
            self.images_root.mkdir(parents=True, exist_ok=True)
            self.avatars_root.mkdir(parents=True, exist_ok=True)
            self.presence_root.mkdir(parents=True, exist_ok=True)
            self.read_receipts_root.mkdir(parents=True, exist_ok=True)
            self.reactions_root.mkdir(parents=True, exist_ok=True)
            self.shared_profiles_root.mkdir(parents=True, exist_ok=True)
            probe_path.write_text("ok", encoding="ascii")
            probe_path.unlink()
        except OSError as error:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ChatRoomStorageError(
                "無法連線至公司聊天室。\n"
                f"共享路徑：{self.root}\n{error}"
            ) from error
        self._structure_ready = True

    @staticmethod
    def _minute_bucket(root: Path, timestamp: datetime) -> Path:
        return (
            root
            / f"{timestamp:%Y}"
            / f"{timestamp:%m}"
            / f"{timestamp:%d}"
            / f"{timestamp:%H%M}"
        )

    def send_message(
        self,
        nickname: object,
        text: object = "",
        image_path: Optional[Path] = None,
        user_id: str = "",
        avatar_path: Optional[Path] = None,
        reply_to: Optional[ChatMessage] = None,
        message_type: str = "message",
        metadata: Optional[dict] = None,
    ) -> ChatMessage:
        sender = normalize_nickname(nickname)
        normalized_text = normalize_message_text(text)
        source_image = Path(image_path) if image_path else None
        normalized_type = str(message_type or "message").strip() or "message"
        normalized_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if not normalized_text and source_image is None and normalized_type == "message":
            raise ChatRoomError("請輸入訊息或選擇一張圖片。")

        self.ensure_available()
        now = _next_message_created_at()
        message_id = f"{now:%Y%m%dT%H%M%S_%fZ}_{uuid4().hex}"
        attachment_relative_path = ""
        attachment_name = ""
        avatar_relative_path = ""
        copied_image_path = None

        normalized_user_id = self._normalize_user_id(user_id)
        avatar_relative_path = self._upload_avatar(
            normalized_user_id,
            Path(avatar_path) if avatar_path else None,
        )

        if source_image is not None:
            if not source_image.is_file():
                raise ChatRoomError(f"找不到準備上傳的圖片：{source_image}")
            suffix = source_image.suffix.lower()
            if suffix not in ALLOWED_IMAGE_SUFFIXES:
                raise ChatRoomError("聊天室只接受 PNG、JPG、JPEG 或 BMP 圖片。")
            try:
                image_size = source_image.stat().st_size
            except OSError as error:
                raise ChatRoomStorageError(
                    f"無法讀取準備上傳的圖片：{source_image}\n{error}"
                ) from error
            if image_size > MAX_IMAGE_BYTES:
                raise ChatRoomError("聊天室圖片不可超過 20 MB。")

            image_directory = (
                self.images_root
                / f"{now:%Y}"
                / f"{now:%m}"
                / f"{now:%d}"
            )
            copied_image_path = image_directory / f"{message_id}{suffix}"
            temporary_image = copied_image_path.with_suffix(
                copied_image_path.suffix + ".part"
            )
            try:
                image_directory.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_image, temporary_image)
                temporary_image.replace(copied_image_path)
            except OSError as error:
                try:
                    temporary_image.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ChatRoomStorageError(
                    f"圖片無法上傳到公司聊天室：\n{error}"
                ) from error
            attachment_relative_path = copied_image_path.relative_to(
                self.root
            ).as_posix()
            attachment_name = source_image.name

        message = ChatMessage(
            message_id=message_id,
            sender=sender,
            computer_name=socket.gethostname(),
            text=normalized_text,
            created_at_utc=now.isoformat(),
            user_id=normalized_user_id,
            avatar_relative_path=avatar_relative_path,
            attachment_relative_path=attachment_relative_path,
            attachment_name=attachment_name,
            reply_to_message_id=(reply_to.message_id if reply_to else ""),
            reply_sender=(reply_to.sender if reply_to else ""),
            reply_text=(
                (reply_to.text or ("[圖片]" if reply_to.attachment_name else ""))[:500]
                if reply_to
                else ""
            ),
            reply_avatar_relative_path=(
                reply_to.avatar_relative_path if reply_to else ""
            ),
            message_type=normalized_type,
            metadata=normalized_metadata,
        )
        message_directory = self._minute_bucket(self.messages_root, now)
        message_path = message_directory / f"{message_id}.json"
        temporary_message = message_path.with_suffix(".json.tmp")
        try:
            message_directory.mkdir(parents=True, exist_ok=True)
            temporary_message.write_text(
                json.dumps(message.to_payload(), ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            temporary_message.replace(message_path)
        except OSError as error:
            try:
                temporary_message.unlink(missing_ok=True)
                if copied_image_path is not None:
                    copied_image_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ChatRoomStorageError(
                f"訊息無法寫入公司聊天室：\n{error}"
            ) from error
        return message

    @staticmethod
    def _normalize_user_id(value: object) -> str:
        user_id = str(value or "").strip()
        return (
            user_id
            if re.fullmatch(r"[A-Za-z0-9_-]{8,80}", user_id)
            else uuid4().hex
        )

    def _upload_avatar(self, user_id: str, avatar_path: Optional[Path]) -> str:
        if avatar_path is None:
            return ""
        source_avatar = Path(avatar_path)
        if not source_avatar.is_file():
            raise ChatRoomError(f"找不到聊天室頭貼：{source_avatar}")
        avatar_suffix = source_avatar.suffix.lower()
        if avatar_suffix not in ALLOWED_IMAGE_SUFFIXES:
            raise ChatRoomError("頭貼只接受 PNG、JPG、JPEG 或 BMP 圖片。")
        try:
            avatar_stat = source_avatar.stat()
            if avatar_stat.st_size > MAX_IMAGE_BYTES:
                raise ChatRoomError("頭貼圖片不可超過 20 MB。")
            cache_key = (
                user_id,
                str(source_avatar),
                avatar_stat.st_mtime_ns,
                avatar_stat.st_size,
            )
            cached = self._avatar_upload_cache.get(cache_key)
            if cached:
                return cached
            digest = hashlib.sha256(source_avatar.read_bytes()).hexdigest()[:16]
            avatar_directory = self.avatars_root / user_id
            shared_avatar_path = avatar_directory / f"{digest}{avatar_suffix}"
            if not shared_avatar_path.is_file():
                temporary_avatar = shared_avatar_path.with_suffix(
                    shared_avatar_path.suffix + f".{uuid4().hex}.part"
                )
                avatar_directory.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_avatar, temporary_avatar)
                temporary_avatar.replace(shared_avatar_path)
        except OSError as error:
            try:
                temporary_avatar.unlink(missing_ok=True)
            except (NameError, OSError):
                pass
            raise ChatRoomStorageError(
                f"頭貼無法上傳到公司聊天室：\n{error}"
            ) from error
        relative_path = shared_avatar_path.relative_to(self.root).as_posix()
        self._avatar_upload_cache = {cache_key: relative_path}
        return relative_path

    def update_presence(
        self,
        nickname: object,
        user_id: object,
        session_id: object,
        avatar_path: Optional[Path] = None,
    ) -> ChatPresence:
        self.ensure_available()
        normalized_user_id = self._normalize_user_id(user_id)
        normalized_session_id = str(session_id or "").strip() or uuid4().hex
        shared_avatar = self._upload_avatar(
            normalized_user_id,
            Path(avatar_path) if avatar_path else None,
        )
        presence = ChatPresence(
            user_id=normalized_user_id,
            session_id=normalized_session_id,
            nickname=normalize_nickname(nickname),
            computer_name=socket.gethostname(),
            last_seen_utc=datetime.now(timezone.utc).isoformat(),
            avatar_relative_path=shared_avatar,
        )
        # A profile can be copied together with the application to multiple
        # computers, so ``user_id`` alone is not a unique presence key.  Keep
        # one heartbeat file per running chat session instead of letting those
        # clients overwrite each other.
        presence_path = self.presence_root / (
            f"{normalized_user_id}_{normalized_session_id}.json"
        )
        temporary_path = self.presence_root / (
            f".{normalized_user_id}.{normalized_session_id}.tmp"
        )
        try:
            temporary_path.write_text(
                json.dumps(presence.to_payload(), ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(presence_path)
        except OSError as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ChatRoomStorageError(
                f"無法更新聊天室在線狀態：\n{error}"
            ) from error
        # This persistent profile is intentionally separate from presence.
        # It lets the all-time ranking show a user's latest nickname/avatar
        # even after that person goes offline.
        shared_profile = {
            "schema_version": CHAT_PROFILE_SCHEMA_VERSION,
            "user_id": normalized_user_id,
            "identity_computer_name": presence.computer_name,
            "nickname": presence.nickname,
            "avatar_relative_path": shared_avatar,
            "updated_at_utc": presence.last_seen_utc,
        }
        shared_profile_path = self.shared_profiles_root / f"{normalized_user_id}.json"
        profile_signature = (
            presence.computer_name,
            presence.nickname,
            shared_avatar,
        )
        cached_profile = self._shared_profile_cache.get(normalized_user_id)
        if (
            cached_profile is not None
            and cached_profile[0] == profile_signature
            and monotonic() - cached_profile[1] < 60.0
        ):
            # Presence remains a one-second heartbeat.  Persistent profile
            # data only changes with the nickname/avatar, not with the clock.
            return presence
        temporary_profile = self.shared_profiles_root / f".{normalized_user_id}.{uuid4().hex}.tmp"
        try:
            temporary_profile.write_text(
                json.dumps(shared_profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_profile.replace(shared_profile_path)
            self._shared_profile_cache[normalized_user_id] = (profile_signature, monotonic())
        except OSError as error:
            try:
                temporary_profile.unlink(missing_ok=True)
            except OSError:
                pass
            raise ChatRoomStorageError(
                f"無法更新聊天室共享個人資料：\n{error}"
            ) from error
        return presence

    def load_active_presence(self, max_age_seconds: float = 30.0) -> list[ChatPresence]:
        self.ensure_available()
        try:
            paths_with_mtime = [
                (path, path.stat().st_mtime)
                for path in self.presence_root.glob("*.json")
            ]
        except OSError as error:
            raise ChatRoomStorageError(
                f"無法讀取聊天室在線名單：\n{error}"
            ) from error
        if not paths_with_mtime:
            return []

        # The JSON timestamp comes from each client computer and those clocks
        # may differ. SMB modification times come from the shared filesystem,
        # so compare every heartbeat against the newest file on that server.
        shared_now = max(mtime for _path, mtime in paths_with_mtime)
        cutoff_mtime = shared_now - max(3.0, float(max_age_seconds))
        # A worker that is restarted while an SMB read is still unwinding can
        # briefly leave more than one live session file for the same computer.
        # Treat one user on one computer as the presence identity and keep only
        # its newest heartbeat so one person never appears multiple times in
        # the roster. Different users sharing a computer remain distinguishable.
        newest_by_identity = {}
        for path, modified_at in paths_with_mtime:
            if modified_at < cutoff_mtime:
                continue
            try:
                presence = ChatPresence.from_payload(
                    json.loads(path.read_text(encoding="utf-8-sig"))
                )
                identity_key = (
                    presence.user_id.casefold(),
                    presence.computer_name.strip().casefold(),
                )
                current = newest_by_identity.get(identity_key)
                if current is None or modified_at > current[0]:
                    newest_by_identity[identity_key] = (modified_at, presence)
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ):
                continue
        return sorted(
            (presence for _mtime, presence in newest_by_identity.values()),
            key=lambda item: (item.nickname.casefold(), item.computer_name.casefold()),
        )

    def update_read_receipt(
        self,
        nickname: object,
        user_id: object,
        message: ChatMessage,
    ) -> ChatReadReceipt:
        self.ensure_available()
        normalized_user_id = self._normalize_user_id(user_id)
        computer_name = socket.gethostname()
        receipt = ChatReadReceipt(
            user_id=normalized_user_id,
            nickname=normalize_nickname(nickname),
            computer_name=computer_name,
            last_read_message_id=message.message_id,
            last_read_created_at_utc=message.created_at_utc,
            updated_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        identity = hashlib.sha256(
            f"{normalized_user_id}\0{computer_name.casefold()}".encode("utf-8")
        ).hexdigest()[:32]
        receipt_path = self.read_receipts_root / f"{identity}.json"
        temporary_path = self.read_receipts_root / f".{identity}.{uuid4().hex}.tmp"
        try:
            temporary_path.write_text(
                json.dumps(receipt.to_payload(), ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(receipt_path)
        except OSError as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ChatRoomStorageError(
                f"無法更新聊天室已讀狀態：\n{error}"
            ) from error
        return receipt

    def load_read_receipts(self) -> list[ChatReadReceipt]:
        self.ensure_available()
        receipts = []
        try:
            paths = list(self.read_receipts_root.glob("*.json"))
        except OSError as error:
            raise ChatRoomStorageError(
                f"無法讀取聊天室已讀狀態：\n{error}"
            ) from error
        for path in paths:
            try:
                receipts.append(
                    ChatReadReceipt.from_payload(
                        json.loads(path.read_text(encoding="utf-8-sig"))
                    )
                )
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ):
                continue
        return receipts

    def set_reaction(
        self,
        message_id: object,
        user_id: object,
        nickname: object,
        emoji: object,
    ) -> ChatReaction:
        """Add/update one user's durable reaction to a chat message."""

        self.ensure_available()
        normalized_message_id = str(message_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,120}", normalized_message_id):
            raise ChatRoomError("無法識別要回應的聊天室訊息。")
        normalized_emoji = str(emoji or "").strip()
        if normalized_emoji not in ALLOWED_REACTION_EMOJIS:
            raise ChatRoomError("不支援這個表情符號。")
        normalized_user_id = self._normalize_user_id(user_id)
        computer_name = socket.gethostname()
        reaction = ChatReaction(
            message_id=normalized_message_id,
            user_id=normalized_user_id,
            computer_name=computer_name,
            nickname=normalize_nickname(nickname),
            emoji=normalized_emoji,
            updated_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        message_root = self.reactions_root / normalized_message_id
        identity = hashlib.sha256(
            f"{normalized_user_id}\0{computer_name.casefold()}".encode("utf-8")
        ).hexdigest()[:32]
        reaction_path = message_root / f"{identity}.json"
        temporary_path = message_root / f".{identity}.{uuid4().hex}.tmp"
        try:
            message_root.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(reaction.to_payload(), ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(reaction_path)
        except OSError as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ChatRoomStorageError(f"無法保存表情回應：\n{error}") from error
        return reaction

    def load_reactions(
        self,
        message_ids: Optional[Iterable[str]] = None,
    ) -> list[ChatReaction]:
        """Load permanent reactions, optionally only for visible messages."""

        self.ensure_available()
        allowed = (
            {str(value) for value in message_ids}
            if message_ids is not None
            else None
        )
        if allowed == set():
            return []
        reactions = []
        try:
            roots = [
                root for root in self.reactions_root.iterdir()
                if allowed is None or root.name in allowed
            ]
            paths = [
                path
                for root in roots
                if root.is_dir()
                for path in root.glob("*.json")
            ]
        except OSError as error:
            raise ChatRoomStorageError(f"無法讀取表情回應：\n{error}") from error
        for path in paths:
            try:
                reaction = ChatReaction.from_payload(
                    json.loads(path.read_text(encoding="utf-8-sig"))
                )
                if allowed is None or reaction.message_id in allowed:
                    reactions.append(reaction)
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ):
                continue
        return reactions

    def remove_presence(self, user_id: object, session_id: object) -> None:
        normalized_user_id = str(user_id or "").strip()
        normalized_session_id = str(session_id or "").strip()
        if not normalized_user_id or not normalized_session_id:
            return
        path = self.presence_root / (
            f"{normalized_user_id}_{normalized_session_id}.json"
        )
        try:
            if not path.is_file():
                # Compatibility cleanup for the v2.0.5 first-release format.
                legacy_path = self.presence_root / f"{normalized_user_id}.json"
                if not legacy_path.is_file():
                    return
                path = legacy_path
            presence = ChatPresence.from_payload(
                json.loads(path.read_text(encoding="utf-8-sig"))
            )
            if presence.session_id == normalized_session_id:
                path.unlink(missing_ok=True)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            return

    @staticmethod
    def _read_message(path: Path) -> Optional[ChatMessage]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            return ChatMessage.from_payload(payload)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            # One interrupted or manually damaged message must not take the
            # whole room offline. Temporary files never use the .json suffix.
            return None

    def _read_cached_message(self, path: Path, *, refresh=False) -> Optional[ChatMessage]:
        # Message filenames are unique and published atomically; active polls
        # must not reopen the same ten minutes of messages every second.
        # Re-entering the room performs a full reread, including corrections
        # made directly on the share.  Bound the cache for long-running clients.
        if not refresh and path in self._message_cache:
            self._message_cache.move_to_end(path)
            return self._message_cache[path]
        message = self._read_message(path)
        if message is None:
            self._message_cache.pop(path, None)
        else:
            self._message_cache[path] = message
            self._message_cache.move_to_end(path)
            while len(self._message_cache) > 10000:
                self._message_cache.popitem(last=False)
        return message

    @staticmethod
    def _deduplicate(messages: Iterable[ChatMessage]) -> list[ChatMessage]:
        by_id = {message.message_id: message for message in messages}
        return sorted(
            by_id.values(),
            key=lambda message: (message.created_at_utc, message.message_id),
        )

    def load_recent_messages(
        self,
        limit: Optional[int] = None,
        lookback_days: int = 2,
    ) -> list[ChatMessage]:
        self.ensure_available()
        now = datetime.now(timezone.utc)
        paths = []
        try:
            for day_offset in range(max(1, int(lookback_days))):
                day = now - timedelta(days=day_offset)
                day_root = (
                    self.messages_root
                    / f"{day:%Y}"
                    / f"{day:%m}"
                    / f"{day:%d}"
                )
                if day_root.is_dir():
                    paths.extend(day_root.rglob("*.json"))
        except OSError as error:
            self._structure_ready = False
            raise ChatRoomStorageError(
                f"無法讀取公司聊天室訊息：\n{error}"
            ) from error
        messages = self._deduplicate(
            message
            for message in (self._read_cached_message(path, refresh=True) for path in paths)
            if message is not None
        )
        if limit is None:
            return messages
        normalized_limit = max(0, int(limit))
        return messages[-normalized_limit:] if normalized_limit else []

    def load_active_messages(self, lookback_minutes: int = 10) -> list[ChatMessage]:
        self.ensure_available()
        now = datetime.now(timezone.utc)
        paths = []
        try:
            for minute_offset in range(max(1, int(lookback_minutes))):
                bucket = self._minute_bucket(
                    self.messages_root,
                    now - timedelta(minutes=minute_offset),
                )
                if bucket.is_dir():
                    paths.extend(bucket.glob("*.json"))
        except OSError as error:
            self._structure_ready = False
            raise ChatRoomStorageError(
                f"無法更新公司聊天室訊息：\n{error}"
            ) from error
        return self._deduplicate(
            message
            for message in (self._read_cached_message(path) for path in paths)
            if message is not None
        )

    def resolve_attachment(self, message: ChatMessage) -> Optional[Path]:
        return self._resolve_relative_path(message.attachment_relative_path)

    def resolve_avatar(self, message: ChatMessage) -> Optional[Path]:
        return self._resolve_relative_path(message.avatar_relative_path)

    def resolve_presence_avatar(self, presence: ChatPresence) -> Optional[Path]:
        return self._resolve_relative_path(presence.avatar_relative_path)

    def resolve_relative_media(self, relative_path: str) -> Optional[Path]:
        return self._resolve_relative_path(relative_path)

    def _resolve_relative_path(self, value: str) -> Optional[Path]:
        relative = PurePosixPath(value)
        if (
            not value
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            return None
        return self.root.joinpath(*relative.parts)
