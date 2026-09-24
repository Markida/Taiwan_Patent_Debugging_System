"""Small cached light sprites and deterministic, bounded particle bursts."""
from functools import lru_cache
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF, QRadialGradient


class ParticleBudget:
    """One budget per board paint, shared by every effect in that frame."""
    MAX_PARTICLES = 240

    def __init__(self, limit=MAX_PARTICLES):
        self.remaining = min(self.MAX_PARTICLES, max(0, int(limit)))

    def take(self, count):
        amount = min(self.remaining, max(0, int(count)))
        self.remaining -= amount
        return amount


@lru_cache(maxsize=64)
def _light_sprite(color_name):
    image = QImage(64, 64, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    color = QColor(color_name)
    gradient = QRadialGradient(QPointF(32, 32), 32)
    for position, opacity in ((0, 210), (.12, 150), (.35, 65), (.7, 12), (1, 0)):
        shade = QColor(color)
        shade.setAlpha(opacity)
        gradient.setColorAt(position, shade)
    painter = QPainter(image)
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(QRectF(0, 0, 64, 64))
    painter.end()
    return image


def paint_glow(painter, center, radius, color, opacity=1.0):
    if radius <= 0 or opacity <= 0:
        return
    painter.save()
    painter.setCompositionMode(QPainter.CompositionMode_Plus)
    painter.setOpacity(painter.opacity() * min(1.0, opacity))
    painter.drawImage(QRectF(center.x()-radius, center.y()-radius, radius*2, radius*2),
                      _light_sprite(QColor(color).name()))
    painter.restore()


def paint_light_trail(painter, points, color, width=3, opacity=1.0):
    """A soft outside edge, saturated middle and narrow bright core."""
    if len(points) < 2 or opacity <= 0:
        return
    painter.save()
    painter.setCompositionMode(QPainter.CompositionMode_Plus)
    for multiplier, alpha, tint in ((3.8, .07, color), (1.8, .22, color), (.5, .85, "#ffffff")):
        shade = QColor(tint)
        shade.setAlphaF(min(1.0, alpha*opacity))
        painter.setPen(QPen(shade, max(.5, width*multiplier), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPolyline(QPolygonF(points))
    painter.restore()


def paint_burst(painter, center, progress, unit, color, kind="hit", *, budget=None, count=None, direction=None):
    """Analytic particles: no random game state, physics timers or network packets."""
    if not 0 <= progress < 1:
        return 0
    styles = {
        "shot": (22, 1.4, .05), "bounce": (14, 1.1, .15),
        "hit": (28, 1.9, .3), "explosion": (44, 3.5, 1.1),
        "score": (34, 2.8, .35), "victory": (64, 4.5, 1.2),
        "eat": (24, 1.9, -.35), "bonus": (38, 2.8, .3),
        "hold": (26, 2.0, -.2), "clear": (22, 2.1, .5),
    }
    requested, spread, gravity = styles.get(kind, styles["hit"])
    count = min(64, requested if count is None else max(0, int(count)))
    count = (budget or ParticleBudget()).take(count)
    if count == 0:
        return 0
    unit = max(5.0, float(unit))
    fade = (1-progress)**1.4
    ring_radius = unit*(.2+spread*.7*progress)
    paint_glow(painter, center, unit*(1.4+progress), color, fade*.72)
    painter.save()
    painter.setCompositionMode(QPainter.CompositionMode_Plus)
    painter.setBrush(Qt.NoBrush)
    for radius, alpha in ((ring_radius, .75), (ring_radius*.72, .3)):
        shade = QColor(color)
        shade.setAlphaF(alpha*fade)
        painter.setPen(QPen(shade, max(.7, unit*.08*(1-progress))))
        painter.drawEllipse(center, radius, radius*.72 if kind=="shot" else radius)
    if progress < .28:
        paint_light_trail(painter,
                          [center-QPointF(unit*1.2, 0), center+QPointF(unit*1.2, 0)],
                          color, max(1, unit*.09), (1-progress/.28)*.65)
    for index in range(count):
        variation = (index*.61803398875+.17) % 1
        phase = (index*.41421356237+.31) % 1
        age = progress*(.78+phase*.48)
        if age >= 1:
            continue
        angle = index*2.39996323+.4
        if direction is not None:
            angle = math.atan2(direction[1], direction[0])+(variation-.5)*1.5
        speed = spread*(.45+variation*.8)
        distance = unit*speed*(1-math.exp(-2.8*age))
        vector = QPointF(math.cos(angle), math.sin(angle))
        position = center+vector*distance+QPointF(0, unit*gravity*age*age)
        particle_fade = (1-age)**1.4
        size = max(.65, unit*(.055+phase*.075)*(1-age*.55))
        tint = QColor("#fff1c2" if kind=="explosion" and index%3==0 else color)
        tint.setAlphaF(min(.98, particle_fade))
        if index%3==0:
            paint_glow(painter, position, size*4, tint, particle_fade*.75)
        tail = position-vector*(unit*.36*(1-age))
        painter.setPen(QPen(tint, max(.65, size*.6), Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(tail, position)
        painter.setBrush(tint)
        painter.setPen(Qt.NoPen)
        if kind in {"victory", "score", "eat", "bonus", "hold"}:
            points = []
            for vertex in range(8):
                a = vertex*math.pi/4+age*2+index
                radius = size*(1.9 if vertex%2==0 else .5)
                points.append(position+QPointF(math.cos(a)*radius, math.sin(a)*radius))
            painter.drawPolygon(QPolygonF(points))
        elif kind in {"explosion", "clear"}:
            angle = age*5+index
            points = [position+QPointF(math.cos(angle+n*math.pi/2)*size*1.5,
                                      math.sin(angle+n*math.pi/2)*size*1.5) for n in range(4)]
            painter.drawPolygon(QPolygonF(points))
        else:
            painter.drawEllipse(position, size, size)
        core = QColor("#ffffff")
        core.setAlphaF(particle_fade*.8)
        painter.setBrush(core)
        painter.drawEllipse(position, max(.4, size*.35), max(.4, size*.35))
    painter.restore()
    return count
