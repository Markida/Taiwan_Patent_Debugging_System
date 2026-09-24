"""Hidden two-player LAN Tetris lobby and battle page."""

from __future__ import annotations

import math
import socket
import time
from threading import Thread
from uuid import uuid4

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QLinearGradient
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.features.chat_room.store import (
    ChatProfileStore,
    ChatRoomStore,
    format_ai_victory_announcement,
    publish_game_announcement,
    publish_game_invitation,
)
from app.features.tetris.network import (
    TetrisClient,
    TetrisHost,
    TetrisRoom,
    TetrisRoomDirectory,
    best_ai_placement,
)
from app.features.tetris.themes import (
    DEFAULT_TETRIS_SKIN_ID,
    tetris_skin,
    unlocked_tetris_skins,
)
from app.features.chat_room.game_ranking import (
    GameRankingStore,
    computer_rank,
    award_multiplayer_victory,
)
from ui.arcade_navigation import ArcadeNavigationBar
from ui.arcade_particles import ParticleBudget, paint_burst, paint_glow, paint_light_trail


class TetrisSkinPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.skin_id = DEFAULT_TETRIS_SKIN_ID
        self.setFixedSize(174, 48)

    def set_skin(self, skin_id):
        self.skin_id = tetris_skin(skin_id).skin_id
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#07111f"))
        skin = tetris_skin(self.skin_id)
        size = 20.0
        start_x = 4.0
        for index, value in enumerate((1, 2, 3, 4, 5, 6, 7, 8)):
            TetrisBattleBoard._paint_themed_cell(
                painter,
                QRectF(start_x + index * 20.5, 4, size, size),
                value,
                skin,
            )
        TetrisBattleBoard._paint_themed_cell(
            painter, QRectF(self.width() - 25, 26, 20, 20), 10, skin
        )
        painter.end()


class TetrisBattleBoard(QWidget):
    action_requested = Signal(str)
    COMBO_ANIMATION_SECONDS = 1.0
    COLORS = {
        0: QColor("#0b1628"),
        1: QColor("#22d3ee"),
        2: QColor("#facc15"),
        3: QColor("#a78bfa"),
        4: QColor("#60a5fa"),
        5: QColor("#fb923c"),
        6: QColor("#4ade80"),
        7: QColor("#fb7185"),
        8: QColor("#64748b"),
        10: QColor("#f43f5e"),
    }
    PIECE_NAMES = {1: "I", 2: "O", 3: "T", 4: "J", 5: "L", 6: "S", 7: "Z"}
    PIECE_SHAPES = {
        1: ((1, 1, 1, 1),),
        2: ((1, 1), (1, 1)),
        3: ((0, 1, 0), (1, 1, 1)),
        4: ((1, 0, 0), (1, 1, 1)),
        5: ((0, 0, 1), (1, 1, 1)),
        6: ((0, 1, 1), (1, 1, 0)),
        7: ((1, 1, 0), (0, 1, 1)),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(560, 400)
        self.setFocusPolicy(Qt.StrongFocus)
        self.local_side = "left"
        self._combo_animations = {}
        self._effects = []
        self._particle_budget = ParticleBudget()
        self._last_effect_ids = {"left": 0, "right": 0}
        self._go_started_at = None
        self._countdown_started_at = time.monotonic()
        self.combo_animation_timer = QTimer(self)
        self.combo_animation_timer.setInterval(33)
        self.combo_animation_timer.timeout.connect(self._advance_combo_animation)
        empty = [[0] * 10 for _ in range(20)]
        self.state = {
            "status": "waiting",
            "left_name": "房主",
            "right_name": "訪客",
            "left_board": empty,
            "right_board": empty,
            "left_score": 0,
            "right_score": 0,
            "left_lines": 0,
            "right_lines": 0,
        }

    def set_state(self, state):
        next_state = dict(state or {})
        now = time.monotonic()
        new_match = next_state.get("match_id") != self.state.get("match_id")
        if new_match or next_state.get("status") == "waiting":
            self._combo_animations.clear()
            self._effects.clear()
            self._last_effect_ids = {"left": 0, "right": 0}
            self._go_started_at = None
        if next_state.get("countdown") != self.state.get("countdown"):
            self._countdown_started_at = now
        if not new_match and self.state.get("status") == "countdown" and next_state.get("status") == "playing":
            self._go_started_at = now
            self.setFocus(Qt.OtherFocusReason)
        for side in ("left", "right"):
            previous_combo = 0 if new_match else int(self.state.get(f"{side}_combo", 0) or 0)
            current_combo = int(next_state.get(f"{side}_combo", 0) or 0)
            if current_combo >= 2 and current_combo > previous_combo:
                self._combo_animations[side] = (current_combo, now)
            for event in next_state.get(f"{side}_effects", []):
                event_id = int(event.get("id", 0))
                if event_id <= self._last_effect_ids[side]:
                    continue
                self._last_effect_ids[side] = event_id
                if next_state.get("status") in {"playing", "finished"}:
                    duration = {"hold": 0.7, "clear": 0.85, "obstacle": 1.0, "attack": 1.05, "ko": 1.5, "knocked_out": 1.3}.get(event.get("kind"))
                    if duration:
                        self._effects.append({
                            **event, "side": side, "started_at": now, "duration": duration,
                        })
        self._effects = self._effects[-48:]
        self.state = next_state
        if self._animation_needed() and self.isVisible():
            self.combo_animation_timer.start()
        self.update()

    def _animation_needed(self):
        return bool(
            self._combo_animations or self._effects or self._go_started_at is not None
            or self.state.get("status") == "countdown"
        )

    def _advance_combo_animation(self):
        now = time.monotonic()
        self._combo_animations = {
            side: animation
            for side, animation in self._combo_animations.items()
            if now - animation[1] < self.COMBO_ANIMATION_SECONDS
        }
        self._effects = [
            effect for effect in self._effects
            if now - effect["started_at"] < effect["duration"]
        ]
        if self._go_started_at is not None and now - self._go_started_at > 0.6:
            self._go_started_at = None
        if not self._animation_needed():
            self.combo_animation_timer.stop()
        self.update()

    def hideEvent(self, event):
        self.combo_animation_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        self._advance_combo_animation()
        if self._animation_needed():
            self.combo_animation_timer.start()
        super().showEvent(event)

    def _draw_combo_animation(self, painter, side, board_x, start_y, cell):
        animation = self._combo_animations.get(side)
        if animation is None:
            return
        combo, started_at = animation
        progress = min(
            1.0,
            max(0.0, (time.monotonic() - started_at) / self.COMBO_ANIMATION_SECONDS),
        )
        opacity = 1.0 if progress < 0.55 else (1.0 - progress) / 0.45
        rise = progress * cell * 2.0
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSize(max(18, int(22 + 8 * (1.0 - progress))))
        painter.save()
        painter.setOpacity(max(0.0, opacity))
        painter.setFont(font)
        target = QRectF(
            board_x,
            start_y + cell * 7.5 - rise,
            cell * 10,
            cell * 3,
        )
        painter.setClipRect(QRectF(board_x, start_y, cell*10, cell*20))
        paint_burst(painter, target.center(), progress, cell*1.25, "#fbbf24", "bonus",
                    budget=self._particle_budget, count=min(42, 14+combo*4))
        painter.setPen(QPen(QColor("#451a03"), 5))
        painter.drawText(target.translated(2, 2), Qt.AlignCenter, f"{combo} COMBO!")
        painter.setPen(QColor("#fbbf24"))
        painter.drawText(target, Qt.AlignCenter, f"{combo} COMBO!")
        painter.restore()

    def keyPressEvent(self, event):
        actions = {
            Qt.Key_Left: "left",
            Qt.Key_A: "left",
            Qt.Key_Right: "right",
            Qt.Key_D: "right",
            Qt.Key_Down: "soft",
            Qt.Key_S: "soft",
            Qt.Key_Up: "rotate",
            Qt.Key_W: "rotate",
            Qt.Key_Z: "rotate",
            Qt.Key_Space: "hard",
            Qt.Key_C: "hold",
            Qt.Key_Shift: "hold",
        }
        action = actions.get(event.key())
        if action:
            if self.state.get("status") == "playing":
                if not (event.isAutoRepeat() and action in {"hold", "hard"}):
                    self.action_requested.emit(action)
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _paint_themed_cell(painter, rectangle, value, skin):
        base = QColor(skin.cell_color(int(value)))
        inner = rectangle.adjusted(1, 1, -1, -1)
        painter.fillRect(inner, base)
        painter.setPen(QPen(QColor(skin.grid), 1))
        painter.drawRect(rectangle)
        if not value:
            return
        accent = QColor(skin.accent)
        painter.setPen(QPen(accent, max(1.0, rectangle.width() / 13.0)))
        inset = max(3.0, rectangle.width() * 0.23)
        motif_rect = rectangle.adjusted(inset, inset, -inset, -inset)
        motif = skin.motif
        if motif == "shine":
            painter.fillRect(
                QRectF(inner.x(), inner.y(), inner.width(), max(2.0, inner.height() * 0.22)),
                base.lighter(145),
            )
        elif motif == "dot":
            painter.setBrush(accent)
            painter.drawEllipse(motif_rect)
            painter.setBrush(Qt.NoBrush)
        elif motif == "ring":
            painter.drawEllipse(motif_rect)
        elif motif == "diagonal":
            painter.drawLine(QLineF(inner.bottomLeft(), inner.topRight()))
        elif motif == "leaf":
            painter.drawEllipse(motif_rect)
            painter.drawLine(QLineF(motif_rect.bottomLeft(), motif_rect.topRight()))
        elif motif == "diamond":
            center = rectangle.center()
            painter.drawLine(QLineF(center.x(), motif_rect.top(), motif_rect.right(), center.y()))
            painter.drawLine(QLineF(motif_rect.right(), center.y(), center.x(), motif_rect.bottom()))
            painter.drawLine(QLineF(center.x(), motif_rect.bottom(), motif_rect.left(), center.y()))
            painter.drawLine(QLineF(motif_rect.left(), center.y(), center.x(), motif_rect.top()))
        elif motif == "spark":
            center = rectangle.center()
            painter.drawLine(QLineF(center.x(), motif_rect.top(), center.x(), motif_rect.bottom()))
            painter.drawLine(QLineF(motif_rect.left(), center.y(), motif_rect.right(), center.y()))
        elif motif == "split":
            painter.fillRect(
                QRectF(
                    inner.center().x(), inner.y(), inner.width() / 2.0, inner.height()
                ),
                base.darker(135),
            )
        elif motif == "petal":
            half = motif_rect.width() / 2.0
            painter.drawEllipse(
                QRectF(motif_rect.x(), motif_rect.y(), half + 2, motif_rect.height())
            )
            painter.drawEllipse(
                QRectF(motif_rect.center().x() - 1, motif_rect.y(), half + 2, motif_rect.height())
            )
        if value == 8:
            painter.setPen(QPen(base.lighter(155), max(1.0, rectangle.width() / 12.0)))
            painter.drawLine(QLineF(inner.left(), inner.center().y(), inner.right(), inner.center().y()))
        elif value == 10:
            painter.setPen(QPen(QColor("#ffffff"), max(1.0, rectangle.width() / 10.0)))
            painter.drawEllipse(motif_rect)
            center = rectangle.center()
            painter.drawLine(QLineF(center.x(), motif_rect.top(), center.x(), motif_rect.bottom()))
            painter.drawLine(QLineF(motif_rect.left(), center.y(), motif_rect.right(), center.y()))

    def player_score_text(self, side):
        return (
            f"KO 對手 {self.state.get(f'{side}_kos', 0)}"
            f"   攻擊 {self.state.get(f'{side}_sent', 0)}"
        )

    def battle_layout(self):
        """Keep HOLD, the well, incoming meter and five NEXT slots in bounds."""
        cell = max(1.0, min((self.height() - 112.0) / 20.0, (self.width() - 24.0) / 38.8))
        group_width = cell * 18.8
        start = (self.width() - cell * 38.8) / 2.0
        result = {}
        for index, side in enumerate(("left", "right")):
            x = start + index * cell * 20.0
            well = QRectF(x + cell * 3.6, 76, cell * 10, cell * 20)
            result[side] = {
                "cell": cell,
                "well": well,
                "hold": QRectF(x, 76, cell * 3.2, cell * 3.2 + 20),
                "meter": QRectF(well.right() + cell * 0.3, 98, cell * 0.65, cell * 20 - 22),
                "next": QRectF(well.right() + cell * 1.3, 76, cell * 3.6, cell * 15.5 + 22),
                "group": QRectF(x, 38, group_width, cell * 20 + 72),
            }
        return result

    def _font(self, pixels, bold=False):
        font = QFont(self.font())
        font.setPixelSize(max(8, int(pixels)))
        font.setBold(bold)
        return font

    def _draw_piece(self, painter, target, piece_id, skin):
        shape = self.PIECE_SHAPES.get(int(piece_id or 0))
        if not shape:
            return
        cell = min(target.width() / 4.4, target.height() / 2.5)
        x = target.center().x() - len(shape[0]) * cell / 2
        y = target.center().y() - len(shape) * cell / 2
        for row_index, row in enumerate(shape):
            for column, occupied in enumerate(row):
                if occupied:
                    self._paint_themed_cell(
                        painter, QRectF(x + column * cell, y + row_index * cell, cell, cell),
                        piece_id, skin,
                    )

    def _draw_previews(self, painter, side, geometry, skin):
        hold = geometry["hold"]
        painter.fillRect(hold, QColor("#101e31"))
        painter.setPen(QPen(QColor(skin.grid), 1))
        painter.drawRect(hold)
        painter.setFont(self._font(11, True))
        painter.setPen(QColor("#94a3b8") if self.state.get(f"{side}_hold_used") else QColor("#c4b5fd"))
        painter.drawText(QRectF(hold.x(), hold.y(), hold.width(), 20), Qt.AlignCenter, "HOLD")
        self._draw_piece(painter, hold.adjusted(2, 20, -2, -2), self.state.get(f"{side}_hold", 0), skin)
        painter.setFont(self._font(9))
        painter.setPen(QColor("#475569"))
        painter.drawText(
            QRectF(hold.x() - 2, hold.bottom() + 4, hold.width() + 4, 28),
            Qt.AlignCenter,
            "本次已儲存" if self.state.get(f"{side}_hold_used") else "Shift / C",
        )
        panel = geometry["next"]
        painter.fillRect(panel, QColor("#101e31"))
        painter.setPen(QPen(QColor(skin.grid), 1))
        painter.drawRect(panel)
        painter.setFont(self._font(11, True))
        painter.setPen(QColor("#bae6fd"))
        painter.drawText(QRectF(panel.x(), panel.y(), panel.width(), 22), Qt.AlignCenter, "NEXT")
        slot_height = (panel.height() - 22) / 5
        next_ids = self.state.get(f"{side}_next") or []
        for index in range(5):
            slot = QRectF(panel.x() + 2, panel.y() + 22 + index * slot_height, panel.width() - 4, slot_height)
            painter.fillRect(slot, QColor("#172d45" if index == 0 else "#0c1829"))
            painter.setPen(QPen(QColor("#26384d"), 1))
            painter.drawLine(QLineF(slot.bottomLeft(), slot.bottomRight()))
            self._draw_piece(painter, slot.adjusted(2, 2, -2, -2), next_ids[index] if index < len(next_ids) else 0, skin)

    def _draw_incoming(self, painter, side, geometry):
        bar = geometry["meter"]
        pending = max(0, int(self.state.get(f"{side}_incoming", 0)))
        painter.fillRect(bar, QColor("#1a2535"))
        painter.setPen(QPen(QColor("#6b3546" if pending else "#344256"), 1))
        painter.drawRect(bar)
        unit = bar.height() / 20
        for index in range(min(20, pending)):
            color = QColor("#fb7185" if index >= 8 else "#fbbf24" if index >= 4 else "#fb923c")
            painter.fillRect(
                QRectF(bar.left() + 1, bar.bottom() - (index + 1) * unit + 1, bar.width() - 2, max(1, unit - 2)),
                color,
            )
        painter.setPen(QColor("#be123c" if pending else "#475569"))
        painter.setFont(self._font(12, True))
        painter.drawText(
            QRectF(bar.center().x() - 16, bar.top() - 22, 32, 20),
            Qt.AlignCenter, str(pending),
        )

    def _draw_effects(self, painter, layouts):
        now = time.monotonic()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        active = [effect for effect in self._effects
                  if 0 <= now-effect["started_at"] < effect["duration"]][-16:]
        for effect in reversed(active):
            progress = (now-effect["started_at"])/effect["duration"]
            side = effect["side"]
            geometry = layouts[side]
            well, cell = geometry["well"], geometry["cell"]
            kind = effect["kind"]
            skin = tetris_skin(self.state.get(f"{side}_skin"))
            painter.save()
            if kind in {"clear", "obstacle"}:
                painter.setClipRect(well.adjusted(-2, -2, 2, 2))
                color = "#67e8f9" if kind == "clear" else "#fb923c"
                for row in effect.get("rows", [])[-4:]:
                    y = well.top()+(row+.5)*cell
                    # A narrow sweep leaves nearby falling blocks unobscured.
                    sweep = min(1.0, progress*2)
                    left = QPointF(well.center().x()-well.width()*sweep/2, y)
                    right = QPointF(well.center().x()+well.width()*sweep/2, y)
                    paint_light_trail(painter, [left, right], color, cell*.28, (1-progress)**1.3)
                    for fraction in (.2, .5, .8):
                        paint_burst(painter, QPointF(well.left()+well.width()*fraction, y),
                                    progress, cell, color, "clear" if kind=="clear" else "explosion",
                                    budget=self._particle_budget, count=14)
                if kind == "obstacle":
                    for column, row in effect.get("bombs", [])[-4:]:
                        center = QPointF(well.left()+(column+.5)*cell, well.top()+(row+.5)*cell)
                        paint_burst(painter, center, progress, cell*1.25, "#fbbf24", "explosion",
                                    budget=self._particle_budget, count=24)
            elif kind == "hold":
                origin = effect.get("origin", [5, 1])
                start = QPointF(well.left()+float(origin[0])*cell, well.top()+float(origin[1])*cell)
                target = geometry["hold"].center()
                control = QPointF(target.x(), start.y()-cell*2)
                eased = 1-(1-progress)**3
                def point_at(t):
                    return start*((1-t)**2)+control*(2*(1-t)*t)+target*(t*t)
                trail = [point_at(max(0, eased-index*.035)) for index in range(12)]
                center = trail[0]
                paint_light_trail(painter, trail, "#c4b5fd", cell*.23, 1-progress)
                paint_glow(painter, center, cell*1.5, "#c4b5fd", .7*(1-progress))
                painter.setOpacity(1-progress)
                size = cell*2.2
                self._draw_piece(painter, QRectF(center.x()-size/2, center.y()-size/2, size, size),
                                 effect.get("piece"), skin)
                painter.setOpacity(1)
                if progress > .32:
                    paint_burst(painter, target, (progress-.32)/.68, cell*.8, skin.accent, "hold",
                                budget=self._particle_budget)
            elif kind == "attack":
                rows = effect.get("rows") or [15]
                start = QPointF(well.center().x(), well.top()+(sum(rows)/len(rows)+.5)*cell)
                target = layouts["right" if side=="left" else "left"]["meter"].center()
                control = QPointF((start.x()+target.x())/2, min(start.y(),target.y())-cell*5)
                color = "#67e8f9" if side==self.local_side else "#fb923c"
                def point_at(t):
                    return start*((1-t)**2)+control*(2*(1-t)*t)+target*(t*t)
                travel = min(1.0, progress*1.4)
                trail = [point_at(max(0, travel-index*.035)) for index in range(14)]
                paint_light_trail(painter, trail, color, cell*.23, 1-progress*.75)
                for index in range(min(8, max(3, int(effect.get("amount",1))))):
                    point = point_at(max(0, min(1, progress*1.4-index*.032)))
                    paint_glow(painter, point, cell*(.9-index*.055), color, (1-progress)*.85)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor("#ffffff"))
                    painter.drawEllipse(point, max(1.5,cell*.11), max(1.5,cell*.11))
                if progress > .68:
                    paint_burst(painter, target, (progress-.68)/.32, cell, color, "hit",
                                budget=self._particle_budget, count=28)
                painter.setFont(self._font(max(15,cell), True))
                box = QRectF(start.x()-65,start.y()-cell*(2+progress*2),130,cell*2)
                painter.setPen(QColor("#172033"))
                painter.drawText(box.translated(1,1),Qt.AlignCenter,f"+{effect.get('amount',0)} 攻擊")
                painter.setPen(QColor(color))
                painter.drawText(box,Qt.AlignCenter,f"+{effect.get('amount',0)} 攻擊")
            elif kind in {"ko", "knocked_out"}:
                painter.setClipRect(well)
                label = "KO +1" if kind=="ko" else "被 KO"
                color = "#a7f3d0" if kind=="ko" else "#fecdd3"
                target = QRectF(well.left()+cell*.4,
                               well.top()+cell*(6.0 if kind=="ko" else 9.2)-progress*cell,
                               well.width()-cell*.8,cell*2.5)
                paint_burst(painter,target.center(),progress,cell*1.5,color,
                            "victory" if kind=="ko" else "explosion",budget=self._particle_budget)
                painter.setOpacity(min(1,(1-progress)*2))
                painter.setBrush(QColor("#14332b" if kind=="ko" else "#4c1525"))
                painter.setPen(QPen(QColor(color),1.5))
                painter.drawRoundedRect(target,8,8)
                painter.setFont(self._font(max(16,cell),True))
                painter.drawText(target,Qt.AlignCenter,label)
            painter.restore()
        painter.restore()

    def paintEvent(self, event):
        self._particle_budget = ParticleBudget()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#eef3f8"))
        layouts = self.battle_layout()
        remaining = int(self.state.get("remaining_seconds", 120))
        painter.setFont(self._font(24, True))
        painter.setPen(QColor("#172033"))
        painter.drawText(QRectF(0, 3, self.width(), 32), Qt.AlignCenter, f"{remaining // 60}:{remaining % 60:02d}")
        for side, geometry in layouts.items():
            well, cell = geometry["well"], geometry["cell"]
            skin = tetris_skin(self.state.get(f"{side}_skin"))
            board = self.state.get(f"{side}_board") or [[0] * 10 for _ in range(20)]
            painter.fillRect(well, QColor(skin.board))
            for row in range(20):
                for column in range(10):
                    value = int(board[row][column]) if row < len(board) and column < len(board[row]) else 0
                    rectangle = QRectF(well.x() + column * cell, well.y() + row * cell, cell, cell)
                    if value == 9:
                        painter.fillRect(rectangle, QColor(skin.board))
                        painter.setPen(QPen(QColor(skin.accent), 1, Qt.DashLine))
                        painter.drawRect(rectangle.adjusted(3, 3, -3, -3))
                    else:
                        self._paint_themed_cell(painter, rectangle, value, skin)
            painter.setPen(QPen(QColor("#0284c7" if side == self.local_side else "#52677e"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(well)
            self._draw_previews(painter, side, geometry, skin)
            self._draw_incoming(painter, side, geometry)
            painter.setFont(self._font(min(16, max(11, cell * 0.6)), True))
            painter.setPen(QColor("#075985" if side == self.local_side else "#334155"))
            name = str(self.state.get(f"{side}_name", "玩家"))
            name = painter.fontMetrics().elidedText(name, Qt.ElideRight, int(well.width() - 26))
            painter.drawText(QRectF(well.left(), 37, well.width(), 20), Qt.AlignCenter,
                             ("你 · " if side == self.local_side else "") + name)
            painter.setFont(self._font(11))
            painter.setPen(QColor("#334155"))
            painter.drawText(QRectF(well.left(), 56, well.width(), 17), Qt.AlignCenter,
                             self.player_score_text(side))
            combo = int(self.state.get(f"{side}_combo", 0))
            detail = f"消行 {self.state.get(f'{side}_lines', 0)}"
            if combo >= 2:
                detail += f"   {combo} COMBO"
            if self.state.get(f"{side}_b2b"):
                detail += "   B2B"
            painter.drawText(QRectF(well.left() - 8, well.bottom() + 5, well.width() + 16, 22), Qt.AlignCenter, detail)
        self._draw_effects(painter, layouts)
        for side, geometry in layouts.items():
            self._draw_combo_animation(painter, side, geometry["well"].x(), geometry["well"].y(), geometry["cell"])
        self.last_particle_count = ParticleBudget.MAX_PARTICLES-self._particle_budget.remaining
        status = self.state.get("status")
        if status != "playing":
            card_width = min(self.width() - 32, 220 if status == "countdown" else 460)
            card = QRectF((self.width() - card_width) / 2, (self.height() - 208) / 2, card_width, 208)
            painter.setPen(QPen(QColor("#94a3b8"), 1.5))
            painter.setBrush(QColor("#ffffff"))
            painter.drawRoundedRect(card, 14, 14)
            painter.setPen(QColor("#172033"))
            if status == "countdown":
                elapsed = min(1.0, time.monotonic() - self._countdown_started_at)
                painter.setFont(self._font(80 + 24 * (1 - elapsed), True))
                message = str(self.state.get("countdown", 3))
            elif status == "ready":
                painter.setFont(self._font(27, True))
                message = "READY\n雙方已就緒\n" + ("請按上方 START 開始" if self.local_side == "left" else "等待房主按 START")
            elif status == "finished":
                painter.setFont(self._font(27, True))
                winner = self.state.get("winner")
                message = "平手" if winner == "draw" else str(self.state.get(f"{winner}_name", "玩家")) + " 勝利！"
                message += f"\n判定：{self.state.get('winner_reason', 'KO')}"
            else:
                painter.setFont(self._font(27, True))
                message = "等待對手加入…"
            painter.drawText(card.adjusted(10, 8, -10, -8), Qt.AlignCenter, message)
        elif self._go_started_at is not None:
            painter.save()
            painter.setOpacity(max(0.0, 1 - (time.monotonic() - self._go_started_at) / 0.6))
            card = QRectF(self.width() / 2 - 110, self.height() / 2 - 65, 220, 130)
            painter.setBrush(QColor("#ffffff"))
            painter.setPen(QPen(QColor("#86b8aa"), 1.5))
            painter.drawRoundedRect(card, 14, 14)
            painter.setFont(self._font(66, True))
            painter.setPen(QColor("#047857"))
            painter.drawText(card, Qt.AlignCenter, "GO!")
            painter.restore()
        painter.end()


class TetrisGamePage(QWidget):
    """Unregistered Easter egg; room discovery uses the chat SMB share."""

    skin_access_loaded = Signal(object)

    def __init__(
        self,
        go_home_callback,
        open_chat_callback=None,
        open_pong_callback=None,
        open_snake_callback=None,
        open_tank_callback=None,
        open_bulls_cows_callback=None,
        return_work_callback=None,
        *,
        host=None,
        client=None,
        room_directory=None,
        chat_store=None,
        profile_store=None,
        ranking_store=None,
    ):
        super().__init__()
        self.go_home_callback = go_home_callback
        self.open_chat_callback = open_chat_callback
        self.open_pong_callback = open_pong_callback
        self.open_snake_callback = open_snake_callback
        self.open_tank_callback = open_tank_callback
        self.open_bulls_cows_callback = open_bulls_cows_callback
        self.return_work_callback = return_work_callback
        self.host = host or TetrisHost(self)
        self.client = client or TetrisClient(self)
        self.room_directory = room_directory or TetrisRoomDirectory(parent=self)
        self.chat_store = chat_store or ChatRoomStore()
        self.profile_store = profile_store or ChatProfileStore()
        self.ranking_store = ranking_store or GameRankingStore(self.chat_store.root)
        self.mode = ""
        self._hosted_room_id = ""
        self._announcement_sent = False
        self._known_rooms = {}
        self._skin_access_thread = None
        self._preferred_skin_id = DEFAULT_TETRIS_SKIN_ID
        self.cpu_timer = QTimer(self)
        self.cpu_timer.setInterval(230)
        self.cpu_timer.timeout.connect(self._cpu_move)
        self._ai_difficulty = "normal"
        self._build_ui()
        self.skin_access_loaded.connect(self._apply_skin_access)
        self._connect_network()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 14)
        root.setSpacing(8)
        self.arcade_navigation = ArcadeNavigationBar(
            current_id="tetris",
            title="TETRIS",
            go_home_callback=self.go_home_callback,
            return_work_callback=self.return_work_callback,
            callbacks={
                "chat": self.open_chat_callback,
                "pong": self.open_pong_callback,
                "tank": self.open_tank_callback,
                "bulls_and_cows": self.open_bulls_cows_callback,
                "snake": self.open_snake_callback,
            },
        )
        self.return_work_button = self.arcade_navigation.return_work_button
        root.addWidget(self.arcade_navigation)

        self.pages = QStackedWidget()
        self.lobby_page = self._build_lobby()
        self.game_page = self._build_game()
        self.pages.addWidget(self.lobby_page)
        self.pages.addWidget(self.game_page)
        root.addWidget(self.pages, 1)

    def _build_lobby(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()
        panel = QFrame()
        panel.setObjectName("Panel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(30, 24, 30, 24)
        grid.setSpacing(12)
        heading = QLabel("雙人俄羅斯方塊對戰大廳")
        heading.setObjectName("PanelTitle")
        heading.setAlignment(Qt.AlignCenter)
        grid.addWidget(heading, 0, 0, 1, 3)
        grid.addWidget(QLabel("玩家名稱："), 1, 0)
        self.player_name = QLineEdit()
        self.player_name.setObjectName("InputLine")
        self.player_name.setReadOnly(True)
        self.player_name.setPlaceholderText("請先在聊天室設定暱稱")
        grid.addWidget(self.player_name, 1, 1, 1, 2)
        grid.addWidget(QLabel("AI 難度："), 2, 0)
        self.ai_difficulty = QComboBox()
        self.ai_difficulty.setObjectName("InputLine")
        self.ai_difficulty.addItem("簡單", "easy")
        self.ai_difficulty.addItem("普通", "normal")
        self.ai_difficulty.addItem("困難", "hard")
        self.ai_difficulty.setCurrentIndex(1)
        grid.addWidget(self.ai_difficulty, 2, 1, 1, 2)
        grid.addWidget(QLabel("本場造型："), 3, 0)
        self.skin_selector = QComboBox()
        self.skin_selector.setObjectName("InputLine")
        self.skin_selector.currentIndexChanged.connect(self._skin_selection_changed)
        grid.addWidget(self.skin_selector, 3, 1)
        self.skin_preview = TetrisSkinPreview()
        grid.addWidget(self.skin_preview, 3, 2, alignment=Qt.AlignCenter)
        self.skin_access_label = QLabel("預設造型可使用；正在確認排行榜獎勵…")
        self.skin_access_label.setWordWrap(True)
        grid.addWidget(self.skin_access_label, 4, 1, 1, 2)
        self._apply_skin_access({"rank": 0, "loading": True})
        self.create_button = QPushButton("建立房間")
        self.create_button.setObjectName("PrimaryButton")
        self.create_button.clicked.connect(self.create_room)
        grid.addWidget(self.create_button, 5, 0, 1, 3)
        self.cpu_button = QPushButton("▶  與電腦遊玩")
        self.cpu_button.setObjectName("SecondaryButton")
        self.cpu_button.clicked.connect(self.start_cpu_match)
        grid.addWidget(self.cpu_button, 6, 0, 1, 3)
        room_heading = QLabel("房間清單｜點選加入")
        room_heading.setObjectName("PanelTitle")
        grid.addWidget(room_heading, 7, 0, 1, 3)
        self.room_list = QListWidget()
        self.room_list.setObjectName("PongRoomList")
        self.room_list.setMinimumHeight(190)
        self.room_list.itemClicked.connect(self._join_room_item)
        grid.addWidget(self.room_list, 8, 0, 1, 3)
        instructions = QLabel(
            "⌨  ←→ 移動　↑/Z 旋轉　↓ 加速　Space 落下　C/Shift Hold"
        )
        instructions.setAlignment(Qt.AlignCenter)
        instructions.setWordWrap(True)
        grid.addWidget(instructions, 9, 0, 1, 3)
        self.lobby_status = QLabel("正在讀取目前房間清單…")
        self.lobby_status.setObjectName("ArcadeStatus")
        self.lobby_status.setAlignment(Qt.AlignCenter)
        self.lobby_status.setWordWrap(True)
        grid.addWidget(self.lobby_status, 10, 0, 1, 3)
        panel.setMaximumWidth(820)
        layout.addWidget(panel, alignment=Qt.AlignCenter)
        layout.addStretch()
        return page

    def _build_game(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.mode_label = QLabel("尚未開始")
        self.mode_label.setObjectName("PanelTitle")
        row.addWidget(self.mode_label)
        self.game_status = QLabel("")
        self.game_status.setObjectName("ArcadeStatus")
        self.game_status.setAlignment(Qt.AlignCenter)
        self.game_status.setWordWrap(True)
        row.addWidget(self.game_status, 1)
        self.start_button = QPushButton("START")
        self.start_button.setObjectName("GreenActionButton")
        self.start_button.setToolTip("雙方到齊後，由房主開始 3、2、1 倒數")
        self.start_button.clicked.connect(self._start_lan_match)
        self.start_button.setVisible(False)
        self.start_button.setEnabled(False)
        row.addWidget(self.start_button)
        leave = QPushButton("離開比賽並返回大廳")
        leave.setObjectName("SecondaryButton")
        leave.clicked.connect(self.leave_match)
        row.addWidget(leave)
        layout.addLayout(row)
        self.board = TetrisBattleBoard()
        self.board.action_requested.connect(self._send_action)
        layout.addWidget(self.board, 1)
        hint = QLabel(
            "兩分鐘對戰｜KO → 攻擊行數 → 盤面高度判定｜"
            "←→ 移動　↑/Z 旋轉　↓ 加速　Space 落下　C/Shift Hold"
        )
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return page

    def _connect_network(self):
        self.host.state_changed.connect(self._apply_battle_state)
        self.host.status_changed.connect(self.game_status.setText)
        self.host.guest_joined.connect(lambda _name: self._publish_host_room(2, "ready"))
        self.host.guest_left.connect(lambda: self._publish_host_room(1, "waiting"))
        self.host.match_finished.connect(self._match_finished)
        self.client.state_changed.connect(self._apply_battle_state)
        self.client.status_changed.connect(self.game_status.setText)
        self.client.error_occurred.connect(self._network_error)
        self.client.match_finished.connect(self._match_finished)
        self.room_directory.rooms_received.connect(self._update_room_list)
        self.room_directory.error_occurred.connect(self.lobby_status.setText)

    def _apply_battle_state(self, state):
        self.board.set_state(state)
        status = state.get("status", "waiting")
        self.start_button.setVisible(self.mode == "host" and status in {"waiting", "ready", "countdown"})
        self.start_button.setEnabled(self.mode == "host" and status == "ready")
        if status == "ready":
            self.game_status.setText("雙方已就緒｜請房主按 START")
        elif status == "countdown":
            self.game_status.setText(f"倒數 {state.get('countdown', 3)}｜準備開始")
        elif status == "playing":
            self.game_status.setText("對戰中｜橘紅直條為待接收障礙，下次落定後升起")
        elif status == "waiting":
            self.game_status.setText("等待對手加入")
        if self.mode == "host":
            self._publish_host_room(1 if status == "waiting" else 2, status)

    def _start_lan_match(self):
        if self.mode == "host" and self.host.start_countdown():
            self.board.setFocus(Qt.OtherFocusReason)

    def set_return_work_available(self, available):
        self.return_work_button.setEnabled(bool(available))

    def prepare_lobby(self):
        self.room_directory.start()
        try:
            profile = self.profile_store.load_profile()
            nickname = profile.nickname
        except Exception as error:
            self.lobby_status.setText(f"無法讀取聊天室暱稱：{error}")
            nickname = ""
            profile = None
        self.player_name.setText(nickname)
        ready = bool(nickname)
        self.create_button.setEnabled(ready)
        self.cpu_button.setEnabled(ready)
        self.room_list.setEnabled(ready)
        if not ready:
            self.lobby_status.setText(
                "請先回聊天室設定暱稱，再建立、加入或進行單機對戰。"
            )
        if profile is not None:
            self._start_skin_access_load(profile)
        if self.mode in {"host", "client", "cpu"}:
            self.pages.setCurrentWidget(self.game_page)
            self.board.setFocus(Qt.OtherFocusReason)
        else:
            self.pages.setCurrentWidget(self.lobby_page)
            self.room_list.setFocus(Qt.OtherFocusReason)

    def _selected_skin_id(self):
        return tetris_skin(self.skin_selector.currentData()).skin_id

    def _skin_selection_changed(self, _index):
        self._preferred_skin_id = self._selected_skin_id()
        self.skin_preview.set_skin(self._preferred_skin_id)

    def _apply_skin_access(self, result):
        source = result if isinstance(result, dict) else {}
        rank = int(source.get("rank", 0) or 0)
        previous = self._preferred_skin_id
        skins = unlocked_tetris_skins(rank if rank in {1, 2, 3} else None)
        self.skin_selector.blockSignals(True)
        self.skin_selector.clear()
        selected_index = 0
        for index, skin in enumerate(skins):
            self.skin_selector.addItem(skin.name, skin.skin_id)
            if skin.skin_id == previous:
                selected_index = index
        self.skin_selector.setCurrentIndex(selected_index)
        self.skin_selector.blockSignals(False)
        self.skin_preview.set_skin(self._selected_skin_id())
        if not source.get("loading") and previous not in {
            skin.skin_id for skin in skins
        }:
            self._preferred_skin_id = DEFAULT_TETRIS_SKIN_ID
        if source.get("loading"):
            self.skin_access_label.setText("預設造型可使用；正在確認排行榜獎勵…")
        elif rank == 1:
            self.skin_access_label.setText("排行榜第 1 名：九套獎勵造型全部解鎖")
        elif rank == 2:
            self.skin_access_label.setText("排行榜第 2 名：前六套獎勵造型已解鎖")
        elif rank == 3:
            self.skin_access_label.setText("排行榜第 3 名：前三套獎勵造型已解鎖")
        else:
            suffix = f"（排行榜讀取失敗：{source['error']}）" if source.get("error") else ""
            self.skin_access_label.setText(f"目前可使用預設造型{suffix}")

    def _start_skin_access_load(self, profile):
        if self._skin_access_thread is not None and self._skin_access_thread.is_alive():
            return
        self._apply_skin_access({"rank": 0, "loading": True})

        def load():
            result = {"rank": 0}
            try:
                entries = self.ranking_store.leaderboard(3)
                result["rank"] = computer_rank(entries)
            except Exception as error:
                result["error"] = str(error)
            try:
                self.skin_access_loaded.emit(result)
            except RuntimeError:
                pass

        self._skin_access_thread = Thread(
            target=load,
            name="SaintIslandTetrisSkinAccess",
            daemon=True,
        )
        self._skin_access_thread.start()

    def _player_name(self):
        name = self.player_name.text().strip()
        if not name:
            QMessageBox.warning(self, "尚未設定暱稱", "請先回聊天室設定暱稱。")
        return name

    def _update_room_list(self, rooms):
        selected = self.room_list.currentItem()
        selected_id = str(selected.data(Qt.UserRole) or "") if selected else ""
        self._known_rooms = {room.room_id: room for room in rooms}
        self.room_list.clear()
        if not rooms:
            item = QListWidgetItem("目前沒有房間；你可以建立一個新房間。")
            item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.room_list.addItem(item)
            return
        for room in rooms:
            full = room.player_count >= 2
            suffix = {
                "ready": "已到齊，等待房主開始",
                "countdown": "倒數準備中",
                "playing": "對戰進行中",
                "finished": "比賽已結束",
            }.get(room.status, "已滿，禁止加入" if full else "點一下加入")
            item = QListWidgetItem(f"{room.host_name} 的房間｜{room.player_count}/2｜{suffix}")
            item.setData(Qt.UserRole, room.room_id)
            if full or room.status != "waiting":
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.room_list.addItem(item)
            if room.room_id == selected_id:
                self.room_list.setCurrentItem(item)

    def _join_room_item(self, item):
        room = self._known_rooms.get(str(item.data(Qt.UserRole) or ""))
        self.join_room(room)

    def join_room(self, room):
        if room is None or room.player_count >= 2 or room.status != "waiting":
            QMessageBox.information(self, "無法加入", "這個房間目前已滿或已開始。")
            return
        name = self._player_name()
        if not name:
            return
        try:
            self.client.connect_to_room(
                room.room_code,
                name,
                self.profile_store.load_profile().user_id,
                self._selected_skin_id(),
            )
        except ValueError as error:
            QMessageBox.warning(self, "房間資料錯誤", str(error))
            return
        self.mode = "client"
        self.start_button.hide()
        self.board.local_side = "right"
        self.board.set_state({"status": "waiting", "match_id": ""})
        self.mode_label.setText("區網對戰｜你是右側加入者")
        self.pages.setCurrentWidget(self.game_page)
        self.board.setFocus(Qt.OtherFocusReason)

    def start_cpu_match(self):
        name = self._player_name()
        if not name:
            return
        self.shutdown_match()
        self.mode = "cpu"
        self.host.host_name = name
        self.host.host_user_id = self.profile_store.load_profile().user_id
        self.host.host_skin_id = self._selected_skin_id()
        self.host.guest_skin_id = DEFAULT_TETRIS_SKIN_ID
        self.host.guest_name = "電腦"
        self.host.start_match()
        self.host.timer.start()
        self._ai_difficulty = str(self.ai_difficulty.currentData() or "normal")
        self.cpu_timer.setInterval({
            "easy": 480,
            "normal": 260,
            "hard": 145,
        }.get(self._ai_difficulty, 260))
        self.cpu_timer.start()
        self.board.local_side = "left"
        self._apply_battle_state(self.host.to_state())
        self.mode_label.setText("單機對戰｜你是左側玩家")
        self.game_status.setText(f"電腦對手｜{self.ai_difficulty.currentText()}")
        self.pages.setCurrentWidget(self.game_page)
        self.board.setFocus(Qt.OtherFocusReason)

    def _cpu_move(self):
        if self.mode != "cpu" or self.host.status != "playing":
            self.cpu_timer.stop()
            return
        engine = self.host.right
        rotations, target_x = best_ai_placement(engine)
        if self._ai_difficulty == "easy":
            rotations = engine.random.randrange(4)
            matrix = engine.matrix
            for _ in range(rotations):
                matrix = engine._rotated(matrix)
            width = max(len(row) for row in matrix)
            target_x = engine.random.randint(0, max(0, engine.WIDTH - width))
        elif self._ai_difficulty == "normal" and engine.random.random() < 0.24:
            target_x = max(
                0,
                min(engine.WIDTH - 1, target_x + engine.random.choice((-2, -1, 1, 2))),
            )
        for _ in range(rotations):
            self.host.apply_action("right", "rotate")
        while engine.x < target_x:
            previous = engine.x
            self.host.apply_action("right", "right")
            if engine.x == previous:
                break
        while engine.x > target_x:
            previous = engine.x
            self.host.apply_action("right", "left")
            if engine.x == previous:
                break
        self.host.apply_action("right", "hard")

    def create_room(self):
        name = self._player_name()
        if not name:
            return
        self.room_directory.start()
        try:
            self.host.create_room(
                name,
                user_id=self.profile_store.load_profile().user_id,
                skin_id=self._selected_skin_id(),
            )
        except RuntimeError as error:
            QMessageBox.warning(self, "房間建立失敗", str(error))
            return
        self.mode = "host"
        self.board.local_side = "left"
        self._apply_battle_state(self.host.to_state())
        self._hosted_room_id = uuid4().hex
        self._announcement_sent = False
        self._publish_host_room(1, "waiting")
        publish_game_invitation(
            "tetris",
            "俄羅斯方塊",
            {
                "room_id": self._hosted_room_id,
                "room_code": self.host.room_code,
                "host_name": name,
                "computer_name": socket.gethostname(),
                "player_count": 1,
                "status": "waiting",
                "skin_id": self._selected_skin_id(),
            },
            name,
            store=self.chat_store,
        )
        self.mode_label.setText("區網對戰｜你是左側房主")
        self.game_status.setText("房間已建立｜等待對手從清單加入")
        self.pages.setCurrentWidget(self.game_page)
        self.board.setFocus(Qt.OtherFocusReason)

    def _publish_host_room(self, player_count, status):
        if not self._hosted_room_id or not self.host.room_code:
            return
        self.room_directory.set_hosted_room(
            TetrisRoom(
                room_id=self._hosted_room_id,
                room_code=self.host.room_code,
                host_name=self.player_name.text().strip(),
                computer_name=socket.gethostname(),
                player_count=player_count,
                status=status,
            )
        )

    def _send_action(self, action):
        if self.mode in {"host", "cpu"}:
            self.host.apply_action("left", action)
        elif self.mode == "client":
            self.client.send_action(action)

    def _match_finished(self, _winner):
        if self.mode not in {"host", "cpu"}:
            return
        self._publish_host_room(2, "finished")
        if self._announcement_sent or self.host.winner == "draw":
            return
        self._announcement_sent = True
        winner = self.host.winner_name()
        loser = self.host.guest_name if self.host.winner == "left" else self.host.host_name
        if self.mode == "cpu" and self.host.winner == "left":
            report = format_ai_victory_announcement(
                winner,
                "俄羅斯方塊",
                self._ai_difficulty,
            )
        else:
            report = f'"{winner}"剛剛在俄羅斯方塊對戰中擊敗了"{loser}"'
        publish_game_announcement(
            report,
            store=self.chat_store,
        )
        if self.mode == "host":
            award_multiplayer_victory(
                winner,
                "tetris",
                match_id=self._hosted_room_id,
                user_id=self.host.winner_user_id(),
                computer_name=self.host.winner_computer_name(),
                store=GameRankingStore(self.chat_store.root),
            )

    def _network_error(self, message):
        self.game_status.setText(f"連線失敗：{message}")
        if self.mode == "client":
            self.client.disconnect()
            self.mode = ""
            self.pages.setCurrentWidget(self.lobby_page)
            self.lobby_status.setText(f"無法加入房間：{message}")

    def leave_match(self):
        self.shutdown_match()
        self.pages.setCurrentWidget(self.lobby_page)
        self.lobby_status.setText("已離開上一場比賽。")

    def shutdown_match(self):
        self.cpu_timer.stop()
        self.room_directory.clear_hosted_room()
        self.host.close()
        self.client.disconnect()
        self._hosted_room_id = ""
        self.mode = ""
        self.start_button.hide()
        self.start_button.setEnabled(False)
        self.board.set_state({"status": "waiting", "match_id": ""})

    def abort_current_workflow(self):
        self.shutdown_match()
        self.pages.setCurrentWidget(self.lobby_page)

    def shutdown(self):
        self.shutdown_match()
        self.room_directory.stop()
