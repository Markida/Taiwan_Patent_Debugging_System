"""Rank rewards shared by the four arcade games; cosmetics never change physics."""
from dataclasses import dataclass
from functools import lru_cache
from uuid import uuid4

from app.features.tetris.themes import TETRIS_SKINS
from app.features.chat_room.game_ranking import computer_rank

THEME_COLORS = (
    ("#38bdf8","#a78bfa"), ("#00f5ff","#d946ef"), ("#f9a8d4","#fde68a"),
    ("#38bdf8","#2dd4bf"), ("#fb923c","#fde047"), ("#34d399","#a3e635"),
    ("#cffafe","#93c5fd"), ("#c084fc","#818cf8"), ("#f97316","#ef4444"),
    ("#f9a8d4","#fb7185"),
)

GAME_SKIN_NAMES = {
    "tank": ("經典戰車", "霓虹突擊", "糖果裝甲", "深海潛航", "落日遊騎", "翡翠叢林", "極地守衛", "銀河巡航", "熔岩重裝", "櫻花武士"),
    "pong": ("經典球拍", "霓虹脈衝", "糖果球場", "深海波紋", "落日弧光", "翡翠球拍", "極地冰刃", "銀河軌道", "熔岩烈焰", "櫻花飛舞"),
    "bulls_and_cows": ("經典數字", "霓虹密碼", "糖果數字", "深海謎題", "落日密語", "翡翠符文", "極地晶碼", "銀河解碼", "熔岩密鑰", "櫻花字札"),
    "snake": ("經典小蛇", "霓虹電蛇", "糖果軟糖", "深海靈蛇", "落日赤蛇", "翡翠藤蛇", "極地冰蛇", "銀河星蛇", "熔岩火蛇", "櫻花花蛇"),
}

@dataclass(frozen=True)
class ArcadeSkin:
    skin_id: str
    name: str
    color: str
    secondary: str
    accent: str
    motif: str
    rank: int

@lru_cache(maxsize=4)
def game_skins(game):
    names = GAME_SKIN_NAMES[game]
    return tuple(
        ArcadeSkin(theme.skin_id, names[i], THEME_COLORS[i][0],
                   THEME_COLORS[i][1], theme.accent, theme.motif,
                   0 if i == 0 else 3 if i <= 3 else 2 if i <= 6 else 1)
        for i, theme in enumerate(TETRIS_SKINS)
    )

def normalize_arcade_skin(value):
    value = str(value or "")
    return value if any(s.skin_id == value for s in TETRIS_SKINS) else "classic"

def arcade_skin(game, value="classic"):
    value = normalize_arcade_skin(value)
    return next(s for s in game_skins(game) if s.skin_id == value)

def unlocked_game_skins(game, rank):
    return game_skins(game)[:{1: 10, 2: 7, 3: 4}.get(rank, 1)]

def profile_rank(entries, profile, computer_name=None):
    return computer_rank(entries, computer_name)

class ArcadeEventLog:
    """Repeat a short event window in snapshots so network batching loses no effects."""
    def __init__(self):
        self.match_id = uuid4().hex
        self.events = []
        self.sequence = 0

    def emit(self, kind, x, y, **details):
        self.sequence += 1
        self.events.append(dict(id=self.sequence, kind=kind, x=x, y=y, **details))
        self.events = self.events[-24:]

    def state(self):
        return {"match_id": self.match_id, "effects": [dict(e) for e in self.events]}
