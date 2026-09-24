"""Code-drawn game cosmetics and finite, non-blocking arcade effects."""
import math
import time

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from app.features.arcade_cosmetics import arcade_skin
from ui.arcade_particles import ParticleBudget, paint_burst, paint_glow, paint_light_trail

def paint_surface(p, rect, skin, color=None, rounded=True):
    r = QRectF(rect)
    base = QColor(color or skin.color)
    p.save()
    gradient = QLinearGradient(r.topLeft(), r.bottomRight())
    gradient.setColorAt(0, base.lighter(130))
    gradient.setColorAt(1, base.darker(135))
    p.setBrush(gradient)
    p.setPen(QPen(QColor(skin.accent), 1))
    radius = min(r.width(), r.height()) * .22 if rounded else 0
    p.drawRoundedRect(r, radius, radius)
    p.setClipRect(r.adjusted(1, 1, -1, -1))
    p.setPen(QPen(QColor(skin.accent), max(1, min(r.width(), r.height()) * .055)))
    p.setBrush(Qt.NoBrush)
    c, w, h = r.center(), r.width(), r.height()
    motif = skin.motif
    if motif == "shine":
        p.drawRoundedRect(r.adjusted(3, 3, -3, -3), radius, radius)
        p.drawLine(QPointF(r.left()+w*.2, r.top()+h*.2), QPointF(r.right()-w*.2, r.top()+h*.2))
    elif motif == "dot":
        for x, y in ((.25,.3),(.7,.65),(.3,.8)):
            p.drawEllipse(QPointF(r.left()+w*x, r.top()+h*y), w*.06, min(w,h)*.06)
    elif motif == "ring":
        p.drawEllipse(r.adjusted(w*.18,h*.18,-w*.18,-h*.18))
    elif motif == "diagonal":
        for f in (-.5,0,.5):
            p.drawLine(QPointF(r.left()+f*w,r.bottom()), QPointF(r.left()+(f+1)*w,r.top()))
    elif motif == "leaf":
        p.drawLine(QPointF(c.x(),r.top()+h*.15),QPointF(c.x(),r.bottom()-h*.15))
        for f in (.3,.6):
            p.drawLine(QPointF(c.x(),r.top()+h*f),QPointF(r.left()+w*.2,r.top()+h*(f-.15)))
            p.drawLine(QPointF(c.x(),r.top()+h*f),QPointF(r.right()-w*.2,r.top()+h*(f+.15)))
    elif motif == "diamond":
        p.drawPolygon(QPolygonF([QPointF(c.x(),r.top()+h*.12),QPointF(r.right()-w*.12,c.y()),
                                QPointF(c.x(),r.bottom()-h*.12),QPointF(r.left()+w*.12,c.y())]))
    elif motif == "spark":
        p.drawLine(QPointF(c.x()-w*.25,c.y()),QPointF(c.x()+w*.25,c.y()))
        p.drawLine(QPointF(c.x(),c.y()-h*.3),QPointF(c.x(),c.y()+h*.3))
        p.drawEllipse(QPointF(r.left()+w*.2,r.top()+h*.2),1.5,1.5)
    elif motif == "split":
        p.drawPolyline(QPolygonF([QPointF(r.left()+w*.65,r.top()),QPointF(r.left()+w*.3,c.y()),
                                 QPointF(r.left()+w*.65,c.y()),QPointF(r.left()+w*.35,r.bottom())]))
    elif motif == "petal":
        for i in range(5):
            angle = i*math.tau/5
            p.drawEllipse(QPointF(c.x()+math.cos(angle)*w*.18,c.y()+math.sin(angle)*h*.18),w*.12,h*.12)
    p.restore()

def paint_tank(p, rect, skin, direction="up", player_color="#38bdf8", local=False, slot=0):
    r = QRectF(rect)
    p.save()
    p.translate(r.center())
    p.rotate({"up":0,"right":90,"down":180,"left":270}.get(direction,0))
    w,h = r.width(),r.height()
    hull = QRectF(-w*.32,-h*.32,w*.64,h*.72)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#475569"))
    for side in (-1,1):
        track = QRectF(side*w*.36-w*.1,-h*.36,w*.2,h*.84)
        p.drawRoundedRect(track,2,2)
        p.setPen(QPen(QColor("#cbd5e1"),1))
        for n in range(4):
            yy = -h*.25+n*h*.18
            p.drawLine(QPointF(track.left(),yy),QPointF(track.right(),yy))
        p.setPen(Qt.NoPen)
    paint_surface(p,hull,skin,player_color if skin.skin_id=="classic" else None)
    p.setBrush(QColor(skin.secondary if skin.skin_id!="classic" else player_color))
    p.setPen(QPen(QColor("#e2e8f0"),1))
    if skin.motif in ("diamond","split","spark"):
        p.drawPolygon(QPolygonF([QPointF(0,-h*.24),QPointF(w*.22,0),QPointF(0,h*.22),QPointF(-w*.22,0)]))
    else:
        p.drawEllipse(QRectF(-w*.2,-h*.2,w*.4,h*.4))
    p.setPen(QPen(QColor(skin.accent),max(2,w*.11),Qt.SolidLine,Qt.RoundCap))
    p.drawLine(QPointF(0,0),QPointF(0,-h*.49))
    p.restore()
    p.save()
    p.setBrush(QColor(player_color))
    p.setPen(QPen(QColor("#ffffff" if local else player_color),1.5))
    badge = QRectF(r.right()-10,r.bottom()-10,12,12)
    p.drawEllipse(badge)
    font=QFont(p.font());font.setPixelSize(9);font.setBold(True);p.setFont(font)
    p.setPen(QColor("#07111f"));p.drawText(badge,Qt.AlignCenter,str(slot+1))
    p.restore()

def paint_paddle(p, rect, skin, default_color):
    if skin.skin_id=="classic":
        p.fillRect(rect,QColor(default_color))
    else:
        paint_surface(p,rect,skin)

def paint_snake_cell(p, rect, skin, head=False, default_color="#22d3a7", direction=(1,0)):
    r=QRectF(rect)
    if skin.skin_id=="classic":
        p.fillRect(r,QColor(default_color))
        return
    paint_surface(p,r,skin,skin.secondary if head else None)
    if head:
        p.save()
        p.translate(r.center())
        p.rotate({(1,0):0,(0,1):90,(-1,0):180,(0,-1):270}.get(tuple(direction),0))
        p.setPen(Qt.NoPen);p.setBrush(QColor("#ffffff"))
        for y in (-.22,.22):
            eye=QPointF(r.width()*.15,r.height()*y)
            p.drawEllipse(eye,r.width()*.13,r.height()*.13)
            p.setBrush(QColor("#0f172a"))
            p.drawEllipse(eye+QPointF(r.width()*.04,0),r.width()*.065,r.height()*.065)
            p.setBrush(QColor("#ffffff"))
        p.restore()

class ArcadeEffects(QObject):
    """Only animate visible widgets; bounded events expire by monotonic wall time."""
    def __init__(self, widget):
        super().__init__(widget)
        self.widget=widget
        self.events=[]
        self.match_id=None
        self.last_id=0
        self.timer=QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self._tick)
        widget.installEventFilter(self)

    def clear(self):
        self.events.clear()
        self.last_id=0
        self.match_id=None
        self.timer.stop()

    def push(self,kind,x,y,**details):
        duration = {"victory": 1.6, "score": 1.25, "eat": .9, "bonus": 1.2,
                    "explosion": 1.05, "shot": .45}.get(kind, .75)
        self.events.append(dict(kind=kind,x=x,y=y,born=time.monotonic(),duration=duration,**details))
        self.events=self.events[-48:]
        self._wake()
        self.widget.update()

    def consume(self,state):
        match_id=state.get("match_id")
        if match_id!=self.match_id:
            self.clear()
            self.match_id=match_id
        for event in state.get("effects",[]):
            event_id=int(event.get("id",0))
            if event_id<=self.last_id:
                continue
            self.last_id=event_id
            payload={k:v for k,v in event.items() if k!="id"}
            self.push(**payload)

    def eventFilter(self,obj,event):
        if event.type()==QEvent.Hide:
            self.timer.stop()
        elif event.type()==QEvent.Show:
            self._tick()
            self._wake()
        return False

    def _wake(self):
        if self.events and self.widget.isVisible():
            self.timer.start()

    def _tick(self):
        now=time.monotonic()
        self.events=[e for e in self.events if now-e["born"]<e["duration"]]
        if not self.events or not self.widget.isVisible():
            self.timer.stop()
        self.widget.update()

    def paint(self, p, mapped, unit=24, budget=None):
        now = time.monotonic()
        budget = budget or ParticleBudget()
        initial_budget = budget.remaining
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        active = [event for event in self.events
                  if 0 <= now-event["born"] < event["duration"]][-16:]
        for event in reversed(active):
            progress = (now-event["born"])/event["duration"]
            point = mapped(event["x"], event["y"])
            color = event.get("color", "#67e8f9")
            paint_burst(p, point, progress, unit, color, event["kind"],
                        budget=budget, direction=event.get("direction"))
            if event["kind"] == "victory" and progress > .18:
                for offset in (-2.2, 2.2):
                    paint_burst(p, point+QPointF(unit*offset, -unit*.6),
                                (progress-.18)/.82, unit*.8, color, "victory",
                                budget=budget, count=20)
            label = event.get("text", "")
            if label:
                font = QFont(p.font())
                font.setPixelSize(max(13, min(27, int(unit*.9))))
                font.setBold(True)
                p.setFont(font)
                p.setCompositionMode(QPainter.CompositionMode_SourceOver)
                p.setOpacity(max(0, min(1, (1-progress)*2)))
                box = QRectF(point.x()-120, point.y()-unit*(1.4+progress)-30, 240, 36)
                p.setPen(QColor("#07111f"))
                p.drawText(box.translated(1, 1), Qt.AlignCenter, label)
                p.setPen(QColor(color))
                p.drawText(box, Qt.AlignCenter, label)
                p.setOpacity(1)
        p.restore()
        self.last_particle_count = initial_budget-budget.remaining

class SkinPreview(QWidget):
    def __init__(self,game,parent=None):
        super().__init__(parent)
        self.game=game
        self.skin_id="classic"
        self.setFixedSize(140,54)
    def set_skin(self,skin_id):
        self.skin_id=skin_id;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(),QColor("#0b1628"))
        skin=arcade_skin(self.game,self.skin_id)
        if self.game=="tank":
            paint_tank(p,QRectF(48,6,42,42),skin)
        elif self.game=="pong":
            paint_paddle(p,QRectF(16,8,12,38),skin,"#38bdf8")
            paint_paddle(p,QRectF(112,8,12,38),skin,"#fb7185")
            p.setPen(Qt.NoPen);p.setBrush(QColor(skin.accent));p.drawEllipse(QPointF(72,26),7,7)
        elif self.game=="snake":
            for n,(x,y) in enumerate(((90,10),(65,10),(40,10),(40,33))):
                paint_snake_cell(p,QRectF(x,y,22,20),skin,n==0,"#67e8c4" if n==0 else "#22d3a7")
        else:
            for n,char in enumerate("1A2B"):
                r=QRectF(8+n*32,9,27,36)
                paint_surface(p,r,skin)
                p.setPen(QColor("#07111f"));font=QFont(p.font());font.setPixelSize(19);font.setBold(True);p.setFont(font)
                p.drawText(r,Qt.AlignCenter,char)
        p.end()

class GuessReveal(QWidget):
    """Display only submitted guesses; the secret never enters this widget."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setMinimumHeight(106)
        self.setMaximumHeight(126)
        self.skin_id="classic"
        self.guess=""
        self.result=""
        self.player=""
        self.started=0
        self.effects=ArcadeEffects(self)
        self.setAccessibleName("猜測結果動畫")

    def reset(self,skin_id="classic"):
        self.skin_id=skin_id;self.guess="";self.result="";self.player=""
        self.effects.clear();self.update()

    def reveal(self,guess,result,player,skin_id):
        self.skin_id=skin_id;self.guess=str(guess)[:4];self.result=str(result);self.player=str(player)
        self.started=time.monotonic()
        self.effects.clear()
        won=self.result=="4A0B"
        self.effects.push("victory" if won else "score",.77,.60,
                          color=arcade_skin("bulls_and_cows",skin_id).accent,text="破解成功！" if won else "")
        self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(),QColor("#0b1628"))
        skin=arcade_skin("bulls_and_cows",self.skin_id)
        budget = ParticleBudget()
        self.effects.paint(p, lambda x,y: QPointF(x*self.width(), y*self.height()), 23, budget)
        p.setPen(QColor("#e2e8f0"))
        font=QFont(self.font());font.setPixelSize(13);p.setFont(font)
        p.drawText(QRectF(16,5,self.width()-32,23),Qt.AlignLeft|Qt.AlignVCenter,
                   self.player+" 的猜測" if self.guess else "輸入四個不重複的數字，開始破解")
        elapsed=time.monotonic()-self.started
        for n in range(4):
            r=QRectF(16+n*48,34,40,55)
            age = elapsed-n*.11
            if self.guess and 0 <= age < .7:
                paint_glow(p, r.center(), 40, skin.accent, (1-age/.7)*.5)
                paint_burst(p, r.center(), age/.7, 14, skin.accent, "hold", budget=budget, count=10)
            paint_surface(p,r,skin)
            p.setPen(QColor("#07111f"));font.setPixelSize(28);font.setBold(True);p.setFont(font)
            char=self.guess[n] if len(self.guess)>n and elapsed>=n*.11 else "?"
            p.drawText(r,Qt.AlignCenter,char)
        p.setPen(QColor("#f8fafc"));font.setPixelSize(26);p.setFont(font)
        p.drawText(QRectF(228,36,max(100,self.width()-244),50),Qt.AlignCenter,
                   self.result if elapsed>.44 else "…")
        p.end()
