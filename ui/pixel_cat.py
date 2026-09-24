"""Tiny code-drawn pixel sprites; no image files, fonts, or heavy imports."""

import math
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPainter, QPolygon


ACTIONS = (
    ("groom", "舔毛"), ("eat", "吃飯"), ("drink", "喝水"),
    ("walk", "向右走路"), ("walk_left", "向左走路"),
    ("run", "向右跑步"), ("run_left", "向左跑步"), ("jump", "跳躍"),
    ("roll", "打滾"), ("blink", "眨眼"), ("yarn", "玩毛線球"),
    ("meow", "喵喵叫"), ("angry", "生氣"), ("sleep", "趴著睡覺"),
    ("stretch", "伸懶腰"), ("yawn", "打哈欠"), ("knead", "踩奶"),
    ("pounce", "蹲低撲一下"), ("chase_tail", "轉圈追尾巴"),
)
MENU_ACTIONS = tuple(item for item in ACTIONS if item[0] in ("eat", "drink", "yarn"))
# Dizziness is a drag interaction, never a random action or a menu command.
ACTION_NAMES = dict((*ACTIONS, ("dizzy", "暈眩吐毛線球")))


def paint_cat(painter, rect, action="idle", frame=0, facing=1):
    """Paint on a 48 x 40 pixel grid, using integer scale for crisp edges."""
    if action in ("walk_left", "run_left"):
        action = action.removesuffix("_left")
        facing = -1
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, False)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
    scale = max(1, min(rect.width() // 48, rect.height() // 40))
    painter.translate(rect.x() + (rect.width() - 48 * scale) // 2,
                      rect.y() + (rect.height() - 40 * scale) // 2)
    painter.scale(scale, scale)

    def box(x, y, w, h, color):
        painter.fillRect(int(x), int(y), int(w), int(h), QColor(color))

    def poly(points, color):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))

    def yarn_ball(x, y):
        box(x+1, y, 4, 6, "#bf83d9")
        box(x, y+1, 6, 4, "#bf83d9")
        box(x+1, y+1, 1, 4, "#ead5f2")
        box(x+2, y+3, 3, 1, "#84579c")
        box(x+3, y, 1, 3, "#ead5f2")

    dark, fur, light = "#0e1420", "#202a3a", "#344157"
    eye, pink = "#d9f67b", "#f0a1b4"
    phase = frame % 8
    scratch = (action in ("hover", "groom", "eat", "drink",
                          "jump", "blink", "yarn", "meow", "angry")
               and phase in (2, 3, 4, 5))
    box(10, 36, 29, 2, "#a3bed2")
    if action == "yarn":
        bx = 36 + round(4 * math.sin(frame * 0.45))
        box(bx, 30, 6, 6, "#bf83d9")
        box(bx-1, 32, 8, 3, "#bf83d9")
        box(bx+1, 30, 1, 6, "#ead5f2")
        box(bx+3, 31, 1, 4, "#84579c")
        box(27, 36, bx-26, 1, "#a566bf")
        box(26, 34, 1, 3, "#a566bf")
    if action in ("eat", "drink"):
        box(33, 33, 12, 2, "#577caf")
        box(34, 35, 10, 3, "#789cd0")
        box(35, 34, 8, 1, "#8fd9f0" if action == "drink" else "#d0a775")
        if action == "eat":
            box(35, 32, 3, 2, "#b98351")
            box(39, 32, 3, 2, "#e0b476")
        elif phase < 4:
            box(42, 29 - phase//2, 1, 2, "#77cfea")
    if action == "knead":
        # A soft little cushion beneath the alternating front paws.
        box(11, 34, 25, 4, "#a08dd2")
        box(13, 33, 21, 2, "#cfbfec")
        box(14, 37, 20, 1, "#7964ac")
    painter.save()
    if facing < 0 and action not in ("eat", "drink", "yarn"):
        painter.translate(48, 0)
        painter.scale(-1, 1)
    if action == "stretch":
        # Rump up, chest down, paws stretched forwards and tail waving.
        sink = round(2 * (1-math.cos(frame * 0.22))/2)
        sway = round(2 * math.sin(frame * 0.5))
        box(9, 13, 3, 17, dark)
        box(7+sway, 10, 4, 5, fur)
        poly([(11, 21), (17, 18), (23, 23), (31, 28+sink),
              (32, 33), (15, 31)], dark)
        poly([(13, 22), (17, 20), (23, 25), (30, 29+sink),
              (29, 32), (16, 29)], fur)
        box(13, 29, 4, 7, dark)
        box(12, 35, 6, 1, light)
        box(20, 29, 4, 6, fur)
        box(22, 33, 16, 3, dark)
        box(25, 35, 18, 2, fur)
        box(38, 35, 5, 1, light)
        box(27, 22+sink, 14, 10, dark)
        box(29, 24+sink, 11, 7, fur)
        poly([(28, 25+sink), (27, 18+sink), (33, 23+sink)], dark)
        poly([(35, 23+sink), (41, 18+sink), (41, 27+sink)], dark)
        box(29, 21+sink, 1, 3, pink)
        box(39, 21+sink, 1, 3, pink)
        box(30, 27+sink, 3, 1, eye)
        box(36, 27+sink, 3, 1, eye)
        box(34, 29+sink, 2, 1, pink)
        painter.restore()
        painter.restore()
        return
    if action == "chase_tail":
        # Compact top-down chase: a curved tail leads the turning nose.
        painter.translate(24, 23)
        painter.rotate((frame % 32) * 11.25)
        painter.translate(-24, -23)
        box(12, 17, 19, 12, dark)
        box(14, 15, 14, 15, fur)
        box(15, 17, 6, 3, light)
        for x, y in ((14, 14), (17, 29), (27, 15), (28, 28)):
            box(x, y+(phase % 2), 4, 3, light)
        box(8, 22, 6, 3, dark)
        box(7, 23, 3, 10, dark)
        box(9, 31, 13, 3, fur)
        box(20, 29, 9, 3, fur)
        box(27, 27, 4, 4, light)
        box(27, 17, 12, 11, dark)
        box(29, 19, 9, 7, fur)
        poly([(28, 20), (27, 14), (33, 18)], dark)
        poly([(34, 18), (40, 14), (39, 22)], dark)
        box(30, 22, 2, 2, eye)
        box(35, 22, 2, 2, eye)
        box(33, 25, 2, 1, pink)
        painter.restore()
        painter.restore()
        return
    if action == "sleep":
        # A tucked silhouette: no protruding tail or standing legs.
        breath = (frame // 6) % 2
        box(10, 29-breath, 22, 7+breath, dark)
        box(12, 26-breath, 17, 9+breath, fur)
        box(15, 25-breath, 10, 1, light)
        box(11, 35, 24, 1, light)
        box(28, 26, 13, 9, dark)
        box(30, 27, 10, 7, fur)
        box(29, 23, 3, 5, dark)
        box(38, 23, 3, 5, dark)
        box(30, 25, 1, 2, pink)
        box(39, 25, 1, 2, pink)
        box(31, 30, 3, 1, eye)
        box(36, 30, 3, 1, eye)
        box(34, 32, 2, 1, pink)
        box(28, 34, 6, 2, fur)
        box(37, 34, 5, 2, fur)
        painter.restore()
        # Draw the letters outside the mirrored pose so they remain readable.
        for index in range(3):
            x = 28 + index*5
            y = 17 - index*5 - (frame // 3) % 3
            box(x, y, 4, 1, "#6d91b3")
            box(x+2, y+1, 1, 1, "#6d91b3")
            box(x+1, y+2, 1, 1, "#6d91b3")
            box(x, y+3, 4, 1, "#6d91b3")
        painter.restore()
        return
    if action == "roll":
        # A separate belly-up pose, not a rotated copy of the standing sprite.
        painter.translate(24, 22)
        painter.rotate((frame % 16) * 22.5)
        painter.translate(-24, -22)
        sway = (0, -1, -2, -1, 0, 2, 1, 0)[phase]
        box(7, 23, 9, 3, dark)
        box(6, 19+sway, 3, 6-sway, fur)
        box(13, 16, 18, 14, dark)
        box(15, 15, 14, 16, fur)
        box(18, 17, 10, 12, "#647185")
        box(20, 19, 6, 9, "#8792a2")
        box(22, 23, 2, 2, light)
        # All four paws are visible around the soft belly.
        for x, y in ((14, 12), (26, 12), (14, 29), (27, 29)):
            wiggle = phase % 2
            box(x, y+wiggle, 5, 5, light)
            box(x+1, y+1+wiggle, 3, 2, pink)
        box(30, 15, 11, 12, dark)
        box(31, 17, 10, 8, fur)
        box(30, 12, 3, 5, dark)
        box(38, 12, 3, 5, dark)
        box(31, 14, 1, 3, pink)
        box(39, 14, 1, 3, pink)
        box(32, 20, 3, 1, eye)
        box(37, 20, 3, 1, eye)
        box(35, 22, 2, 1, pink)
        box(30, 26, 9, 2, "#799acd")
        painter.restore()
        painter.restore()
        return
    bob = (phase % 2) if action in ("walk", "run", "yarn") else 0
    if action == "jump":
        bob = -2 if phase < 4 else 0
    elif action == "pounce":
        stage = frame % 36
        bob = (2 if stage < 10 else
               -round(5*math.sin(math.pi*(stage-10)/14)) if stage < 24 else 0)
    elif action == "knead":
        bob = (frame // 3) % 2
    painter.translate(0, bob)
    # Articulated tail, hind legs and body.
    gesture = action in ("idle_fidget", "hover")
    sway = (0, -2, -4, -2, 1, 3, 1, 0)[phase] if action != "idle" else 0
    if sway:
        box(8, 27, 3, 5, dark)
        box(8+sway//2, 24, 3, 5, dark)
        box(8+sway, 21, 3, 5, dark)
        box(6+sway, 20, 4, 3, fur)
    else:
        tail = 0 if gesture else (phase // 2) % 3
        box(8, 22-tail, 3, 10+tail, dark)
        box(6, 20-tail, 4, 3, fur)
    box(10, 29, 9, 4, fur)
    if action in ("walk", "run"):
        # Side profile with four independently stepping legs. Far legs are
        # darker and slightly higher; near legs remain distinct in front.
        step = (0, 1, 2, 1, 0, -1, -2, -1)[phase]
        lift = (0, 0, 1, 2, 1, 0, 0, 0)[phase]
        amplitude = 2 if action == "run" else 1
        for x, stride, raise_by in (
                (18, -step, lift), (26, step, 0 if lift else 1)):
            box(x+stride, 28, 3, 6-raise_by, "#131b28")
            box(x+stride, 33-raise_by, 4, 1, "#5b687c")
        box(12, 23, 20, 9, dark)
        box(14, 21, 15, 9, fur)
        box(14, 23, 7, 2, light)
        for x, stride, raise_by in (
                (13, step*amplitude, 0 if lift else 1),
                (29, -step*amplitude, lift)):
            box(x+stride, 28, 3, 8-raise_by, fur)
            box(x+stride, 35-raise_by, 4, 1, light)
        # A short feline muzzle: the tiny nose sits close to the cheek.
        poly([(28, 24), (27, 16), (29, 10), (32, 11), (34, 15),
              (38, 16), (39, 20), (40, 21), (39, 23),
              (37, 25), (36, 28), (30, 27)], dark)
        box(29, 17, 9, 8, fur)
        box(36, 21, 3, 2, fur)
        box(29, 13, 2, 4, "#785566")
        box(35, 18, 3, 3, eye)
        box(37, 18, 1, 3, dark)
        box(35, 18, 1, 1, "#ffffff")
        box(39, 21, 1, 1, pink)
        box(37, 24, 4, 1, "#758698")
        box(30, 26, 6, 2, "#799acd")
        box(34, 28, 2, 2, "#efc970")
        if action == "run":
            box(1, 28, 5, 1, "#94b2d2")
            box(0, 31, 4, 1, "#94b2d2")
        painter.restore()
        painter.restore()
        return
    box(15, 22, 17, 12, dark)
    box(17, 21, 13, 11, fur)
    box(17, 23, 4, 4, light)
    stride = ((phase % 4)-2) if action in ("walk", "run") else 0
    if action == "knead":
        for x, lift in ((15, (frame//3) % 2), (26, 1-(frame//3) % 2)):
            box(x, 29, 5, 6-2*lift, dark)
            box(x-1, 34-2*lift, 6, 2, light)
            box(x+1, 34-2*lift, 2, 1, pink)
    else:
        box(15+stride, 31, 5, 5, dark)
        box(14+stride, 35, 6, 1, light)
    if not scratch and action != "knead":
        box(26-stride, 30, 5, 6, dark)
        box(26-stride, 35, 6, 1, light)
    if action == "yarn" and not scratch:
        box(28, 28, 5+phase%3, 3, fur)
        box(32+phase%3, 29, 3, 2, light)
    # Head is lowered toward the bowl during food/water actions.
    hx, hy = (26, 20+phase%2) if action in ("eat", "drink") else (22, 12)
    if action == "hover":
        hy -= 1
    elif action == "dizzy":
        hx += round(math.sin(frame*0.65))
    elif action == "yawn":
        hy -= (frame//5) % 2
    poly([(hx-2, hy+2), (hx-2, hy-5), (hx+1, hy-5),
          (hx+5, hy), (hx+9, hy), (hx+13, hy-5),
          (hx+16, hy-5), (hx+16, hy+9), (hx+13, hy+13),
          (hx+1, hy+13), (hx-3, hy+9), (hx-3, hy+3)], dark)
    box(hx-1, hy+2, 15, 8, fur)
    box(hx+1, hy+10, 10, 3, fur)
    box(hx-1, hy-3, 2, 4, "#785566")
    box(hx+12, hy-3, 2, 4, "#785566")
    closed = (action in ("groom", "eat", "drink", "yawn", "knead")) or (
        action == "blink" and phase in (1, 2, 3, 5)) or (
        action == "idle_fidget" and phase in (2, 3))
    if action == "dizzy" and frame < 48:
        # Blocky spiral pupils, turning through four orientations.
        for x in (hx, hx+8):
            box(x, hy+3, 5, 5, eye)
            spiral = ((1, 0), (2, 0), (3, 0), (4, 0), (4, 1),
                      (4, 2), (4, 3), (4, 4), (3, 4), (2, 4),
                      (1, 4), (0, 4), (0, 3), (0, 2), (1, 2),
                      (2, 2), (2, 3))
            for sx, sy in spiral:
                for _ in range((frame//3) % 4):
                    sx, sy = 4-sy, sx
                box(x+sx, hy+3+sy, 1, 1, dark)
    elif action == "angry":
        # Slanted brows and narrow eyes, rather than the usual round gaze.
        box(hx, hy+5, 4, 2, eye)
        box(hx+8, hy+5, 4, 2, eye)
        box(hx+2, hy+5, 1, 2, dark)
        box(hx+9, hy+5, 1, 2, dark)
        box(hx-1, hy+3, 2, 1, light)
        box(hx+1, hy+4, 2, 1, light)
        box(hx+3, hy+5, 2, 1, light)
        box(hx+7, hy+5, 2, 1, light)
        box(hx+9, hy+4, 2, 1, light)
        box(hx+11, hy+3, 2, 1, light)
    elif closed:
        box(hx, hy+6, 4, 1, eye)
        box(hx+8, hy+6, 4, 1, eye)
    else:
        box(hx, hy+4, 4, 3, eye)
        box(hx+8, hy+4, 4, 3, eye)
        box(hx+2, hy+4, 1, 3, dark)
        box(hx+9, hy+4, 1, 3, dark)
        box(hx, hy+4, 1, 1, "#ffffff")
        box(hx+8, hy+4, 1, 1, "#ffffff")
    box(hx+5, hy+8, 2, 1, pink)
    box(hx-5, hy+8, 5, 1, "#758698")
    box(hx+12, hy+8, 5, 1, "#758698")
    if action in ("groom", "drink") and phase%2:
        box(hx+6, hy+10, 2, 3, pink)
    elif action == "yawn":
        opened = 2 + (0, 1, 2, 3, 3, 2, 1, 0)[phase]
        box(hx+4, hy+9, 5, opened, dark)
        box(hx+5, hy+10, 3, max(1, opened-1), pink)
    elif action == "dizzy" and 22 <= frame < 40:
        box(hx+4, hy+10, 5, 3, pink)
    elif action == "meow":
        box(hx+5, hy+10, 3, 2+phase%2, pink)
    else:
        box(hx+6, hy+10, 1, 1, light)
    box(hx, hy+13, 11, 2, "#799acd")
    box(hx+7, hy+15, 2, 2, "#efc970")
    if scratch:
        # Lift the existing front paw (never add a fifth leg) and scratch the
        # cheek twice. Draw last so the paw remains visible beside the face.
        paw_y = hy+8-2*(phase % 2)
        box(hx+9, hy+13, 3, 5, fur)
        box(hx+11, hy+10, 3, 6, fur)
        box(hx+12, paw_y+2, 3, hy+12-paw_y, fur)
        box(hx+11, paw_y, 5, 4, light)
        box(hx+12, paw_y+1, 2, 2, pink)
    if action == "run":
        box(2, 28, 4, 1, "#94b2d2")
        box(0, 31, 5, 1, "#94b2d2")
    if action == "pounce":
        # Watch a tiny fluttering leaf, then put both paws forwards to catch it.
        box(40, 28-(frame//3) % 3, 4, 2, "#88b997")
        box(42, 27-(frame//3) % 3, 2, 4, "#badc9e")
        if 10 <= frame % 36 < 24:
            box(27, 27, 10, 3, fur)
            box(32, 30, 9, 3, light)
            box(37, 28, 3, 2, pink)
    if action == "dizzy":
        if frame < 42:
            for x, y in ((13, 7+phase % 3), (40, 6+(phase+1) % 3)):
                box(x-1, y, 3, 1, "#eebc54")
                box(x, y-1, 1, 3, "#eebc54")
        if frame >= 24:
            t = min(1.0, (frame-24)/28)
            bx = round(28+12*t)
            by = round(22+10*t-4*math.sin(math.pi*t))
            yarn_ball(bx, by)
            box(bx-3, by+5, 4, 1, "#a566bf")
            box(bx-4, by+4, 1, 2, "#a566bf")
    painter.restore()
    if action == "hover":
        heart_y = 3 - phase//4
        box(8, heart_y, 2, 2, pink)
        box(12, heart_y, 2, 2, pink)
        box(7, heart_y+2, 8, 2, pink)
        box(8, heart_y+4, 6, 1, pink)
        box(9, heart_y+5, 4, 1, pink)
        box(10, heart_y+6, 2, 1, pink)
    if action == "angry":
        # Four red corners form the familiar anger mark above the head.
        y = 3 + phase//4
        red = "#e64b55"
        for x, dy, mirror_x, mirror_y in (
                (8, 0, False, False), (16, 0, True, False),
                (8, 8, False, True), (16, 8, True, True)):
            box(x, y+dy, 2, 4, red)
            box(x-2 if mirror_x else x, y+dy if mirror_y else y+dy+2,
                4, 2, red)
    if action == "meow" and phase < 6:
        # Pixel music notes.
        box(41, 5+phase%2, 1, 6, "#5a86b0")
        box(38, 10+phase%2, 3, 2, "#5a86b0")
        box(42, 5+phase%2, 3, 1, "#5a86b0")
    painter.restore()
