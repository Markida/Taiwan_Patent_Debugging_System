"""Visual themes and ranking unlock rules for Tetris battles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TetrisSkin:
    skin_id: str
    name: str
    board: str
    grid: str
    pieces: tuple[str, ...]
    obstacle: str
    bomb: str
    accent: str
    motif: str = "solid"

    def cell_color(self, value: int) -> str:
        if value == 8:
            return self.obstacle
        if value == 10:
            return self.bomb
        if 1 <= value <= len(self.pieces):
            return self.pieces[value - 1]
        return self.board


DEFAULT_TETRIS_SKIN_ID = "classic"

TETRIS_SKINS = (
    TetrisSkin(
        "classic", "經典方塊", "#0b1628", "#27415c",
        ("#22d3ee", "#facc15", "#a78bfa", "#60a5fa", "#fb923c", "#4ade80", "#fb7185"),
        "#64748b", "#f43f5e", "#e2e8f0", "solid",
    ),
    TetrisSkin(
        "neon", "霓虹夜城", "#09051c", "#44246b",
        ("#00f5ff", "#fff700", "#d946ef", "#3b82f6", "#ff7a00", "#39ff88", "#ff2e88"),
        "#6d5a8d", "#ff1744", "#67e8f9", "shine",
    ),
    TetrisSkin(
        "candy", "糖果樂園", "#29152d", "#71406f",
        ("#7dd3fc", "#fde68a", "#f0abfc", "#93c5fd", "#fdba74", "#86efac", "#fda4af"),
        "#a78aa8", "#fb7185", "#fff1f2", "dot",
    ),
    TetrisSkin(
        "ocean", "深海水晶", "#031b2d", "#155e75",
        ("#67e8f9", "#fef08a", "#c4b5fd", "#38bdf8", "#fbbf24", "#2dd4bf", "#818cf8"),
        "#477487", "#ff5d8f", "#a5f3fc", "ring",
    ),
    TetrisSkin(
        "sunset", "落日餘暉", "#2a1020", "#7c2d4d",
        ("#fb7185", "#fde047", "#e879f9", "#f97316", "#fdba74", "#a3e635", "#f43f5e"),
        "#8b6671", "#ef4444", "#ffedd5", "diagonal",
    ),
    TetrisSkin(
        "forest", "翡翠森林", "#071d16", "#285943",
        ("#2dd4bf", "#facc15", "#a7f3d0", "#34d399", "#fb923c", "#84cc16", "#f472b6"),
        "#657c70", "#ef4444", "#d1fae5", "leaf",
    ),
    TetrisSkin(
        "ice", "極地冰晶", "#071b2e", "#315b7b",
        ("#a5f3fc", "#fef9c3", "#ddd6fe", "#93c5fd", "#fed7aa", "#bbf7d0", "#fecdd3"),
        "#94a3b8", "#fb7185", "#e0f2fe", "diamond",
    ),
    TetrisSkin(
        "galaxy", "銀河星雲", "#100b2b", "#4c3b78",
        ("#22d3ee", "#fef08a", "#c084fc", "#818cf8", "#fb923c", "#34d399", "#f472b6"),
        "#70668c", "#ff3366", "#e9d5ff", "spark",
    ),
    TetrisSkin(
        "lava", "熔岩核心", "#240a05", "#7f1d1d",
        ("#fb923c", "#fde047", "#f97316", "#ef4444", "#fdba74", "#a3e635", "#fb7185"),
        "#78716c", "#ff2600", "#ffedd5", "split",
    ),
    TetrisSkin(
        "sakura", "櫻花祭典", "#291522", "#75435f",
        ("#67e8f9", "#fde68a", "#f9a8d4", "#93c5fd", "#fdba74", "#86efac", "#fb7185"),
        "#9f7f91", "#e11d48", "#fce7f3", "petal",
    ),
)

_SKINS_BY_ID = {skin.skin_id: skin for skin in TETRIS_SKINS}


def normalize_tetris_skin_id(value: object) -> str:
    skin_id = str(value or "").strip().casefold()
    return skin_id if skin_id in _SKINS_BY_ID else DEFAULT_TETRIS_SKIN_ID


def tetris_skin(value: object) -> TetrisSkin:
    return _SKINS_BY_ID[normalize_tetris_skin_id(value)]


def unlocked_tetris_skins(rank: int | None) -> tuple[TetrisSkin, ...]:
    """Return default plus 3/6/9 bonus skins for ranks 3/2/1."""

    bonus_count = {1: 9, 2: 6, 3: 3}.get(rank, 0)
    return TETRIS_SKINS[:1 + bonus_count]
