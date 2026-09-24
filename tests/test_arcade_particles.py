import os
from pathlib import Path
from unittest.mock import patch
import random
import unittest
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from PySide6.QtCore import QPointF,Qt
from PySide6.QtGui import QColor,QImage,QPainter,QPen
from PySide6.QtWidgets import QApplication,QWidget
from ui.arcade_particles import ParticleBudget,paint_burst,paint_glow,paint_light_trail
from ui.arcade_skin_art import ArcadeEffects
from ui.tetris_game_page import TetrisBattleBoard

class ParticleEffectsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def canvas(self):
        image=QImage(320,240,QImage.Format_ARGB32)
        image.fill(QColor("#07111f"))
        return image

    def test_many_bursts_share_one_budget(self):
        image=self.canvas();painter=QPainter(image);budget=ParticleBudget()
        counts=[paint_burst(painter,QPointF(160,120),.25,22,"#22d3ee","explosion",budget=budget) for _ in range(50)]
        painter.end()
        self.assertEqual(sum(counts),240)
        self.assertEqual(budget.remaining,0)
        self.assertEqual(counts[-1],0)

    def test_effects_are_deterministic_and_do_not_use_game_randomness(self):
        state=random.getstate()
        def render():
            image=self.canvas();painter=QPainter(image)
            paint_burst(painter,QPointF(160,120),.35,24,"#c084fc","victory")
            painter.end()
            return bytes(image.constBits())
        self.assertEqual(render(),render())
        self.assertEqual(state,random.getstate())

    def test_expired_particles_leave_no_residual_light(self):
        for progress in (-.1,1,5):
            image=self.canvas();before=bytes(image.constBits());painter=QPainter(image)
            count=paint_burst(painter,QPointF(100,100),progress,24,"#ffffff")
            painter.end()
            self.assertEqual(count,0)
            self.assertEqual(bytes(image.constBits()),before)

    def test_glow_trail_and_particles_restore_painter_state(self):
        image=self.canvas();painter=QPainter(image)
        painter.setOpacity(.3);painter.setPen(QPen(QColor("#123456"),7))
        painter.setClipRect(20,20,200,180)
        opacity=painter.opacity();pen=painter.pen();clip=painter.clipBoundingRect()
        for draw in (
            lambda:paint_glow(painter,QPointF(100,100),30,"#67e8f9"),
            lambda:paint_light_trail(painter,[QPointF(50,50),QPointF(150,100)],"#67e8f9"),
            lambda:paint_burst(painter,QPointF(100,100),.3,24,"#67e8f9"),
        ):
            draw()
            self.assertEqual(painter.pen(),pen)
            self.assertEqual(painter.opacity(),opacity)
            self.assertEqual(painter.clipBoundingRect(),clip)
            self.assertEqual(painter.compositionMode(),QPainter.CompositionMode_SourceOver)
        painter.end()

    def test_network_effect_storm_is_bounded_at_render_time(self):
        widget=QWidget();effects=ArcadeEffects(widget)
        image=self.canvas()
        try:
            with patch("ui.arcade_skin_art.time.monotonic",return_value=100):
                for index in range(100):effects.push("explosion",index%10,5)
            self.assertEqual(len(effects.events),48)
            with patch("ui.arcade_skin_art.time.monotonic",return_value=100.2):
                painter=QPainter(image)
                effects.paint(painter,lambda x,y:QPointF(x*20,y*20),20)
                painter.end()
            self.assertEqual(effects.last_particle_count,240)
            self.assertFalse(effects.timer.isActive())
        finally:widget.deleteLater()

    def test_two_tetris_boards_and_combos_share_budget(self):
        board=TetrisBattleBoard();board.resize(1100,650)
        state=dict(status="playing",match_id="particles",left_combo=9,right_combo=8)
        for side in ("left","right"):
            state[side+"_effects"]=[{"id":i+1,"kind":"obstacle","rows":[16,17,18,19],"bombs":[[4,19]]} for i in range(12)]
        try:
            with patch("ui.tetris_game_page.time.monotonic",return_value=100):
                board.set_state(state)
            with patch("ui.tetris_game_page.time.monotonic",return_value=100.2):
                image=QImage(board.size(),QImage.Format_ARGB32);board.render(image)
            self.assertLessEqual(board.last_particle_count,240)
            self.assertGreater(board.last_particle_count,0)
            self.assertEqual(image.pixelColor(5,5).name(),"#eef3f8")
            with patch("ui.tetris_game_page.time.monotonic",return_value=103):
                board._advance_combo_animation()
            self.assertFalse(board.combo_animation_timer.isActive())
        finally:board.deleteLater()

if __name__=="__main__":
    unittest.main()
