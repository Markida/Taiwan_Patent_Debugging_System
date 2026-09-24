"""Hidden LAN chat room used by the Easter-egg pages."""

from .store import (
    CHAT_POLL_INTERVAL_SECONDS,
    ChatMessage,
    ChatPresence,
    ChatProfile,
    ChatProfileStore,
    ChatRoomError,
    ChatRoomStore,
    default_chat_profile_path,
    default_chat_room_root,
    normalize_nickname,
    publish_game_announcement,
    format_ai_victory_announcement,
)

__all__ = [
    "CHAT_POLL_INTERVAL_SECONDS",
    "ChatMessage",
    "ChatPresence",
    "ChatProfile",
    "ChatProfileStore",
    "ChatRoomError",
    "ChatRoomStore",
    "default_chat_profile_path",
    "default_chat_room_root",
    "normalize_nickname",
    "publish_game_announcement",
    "format_ai_victory_announcement",
]
