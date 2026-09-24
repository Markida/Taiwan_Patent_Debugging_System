"""A compact, asynchronous rank reward picker shared by arcade lobbies."""
from threading import Thread
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QWidget, QComboBox, QLabel, QGridLayout, QScrollArea, QFrame
from app.features.arcade_cosmetics import game_skins, profile_rank, unlocked_game_skins
from app.features.chat_room.game_ranking import GameRankingStore
from ui.arcade_skin_art import SkinPreview

class ArcadeSkinPicker(QWidget):
    loaded=Signal(object)
    changed=Signal(str)

    def __init__(self,game,profile_store,root,parent=None,ranking_store=None):
        super().__init__(parent)
        self.game=game
        self.profile_store=profile_store
        self.ranking_store=ranking_store or GameRankingStore(root)
        self.rank=0
        self.preferred="classic"
        self._loading=False
        self._generation=0
        self.selector=QComboBox()
        self.selector.setAccessibleName("排行榜專屬造型")
        self.preview=SkinPreview(game)
        self.access_label=QLabel("第三名 +3 款｜第二名 +6 款｜第一名 +9 款")
        self.access_label.setWordWrap(True)
        self.access_label.setStyleSheet("color:#475569; font-size:12px;")
        grid=QGridLayout(self)
        grid.setContentsMargins(0,4,0,4)
        grid.setHorizontalSpacing(12)
        grid.addWidget(self.preview,0,0,2,1)
        grid.addWidget(self.selector,0,1)
        grid.addWidget(self.access_label,1,1)
        grid.setColumnStretch(1,1)
        self.loaded.connect(self._loaded)
        self.selector.currentIndexChanged.connect(self._selected)
        self.apply_rank(0)

    def skin_id(self):
        selected=str(self.selector.currentData() or "classic")
        return selected if selected in {s.skin_id for s in unlocked_game_skins(self.game,self.rank)} else "classic"

    def apply_rank(self,rank,error=False,loading=False):
        self.rank=rank if rank in (1,2,3) else 0
        allowed={s.skin_id for s in unlocked_game_skins(self.game,self.rank)}
        wanted=self.preferred if self.preferred in allowed else "classic"
        self.selector.blockSignals(True)
        self.selector.clear()
        for skin in game_skins(self.game):
            enabled=skin.skin_id in allowed
            suffix="" if enabled else f" 🔒 前 {skin.rank} 名限定"
            self.selector.addItem(skin.name+suffix,skin.skin_id)
            self.selector.model().item(self.selector.count()-1).setEnabled(enabled)
        self.selector.setCurrentIndex(max(0,self.selector.findData(wanted)))
        self.selector.blockSignals(False)
        self.preview.set_skin(wanted)
        self.changed.emit(wanted)
        if loading:
            text="讀取排行榜中；目前可使用經典造型"
        elif error:
            text="排行榜暫時無法讀取；目前可使用經典造型，重回大廳可重試"
        elif self.rank:
            text=f"排行榜第 {self.rank} 名｜已解鎖 {len(allowed)} / 10 款（含經典）"
        else:
            text="第三名 +3 款｜第二名 +6 款｜第一名 +9 款"
        self.access_label.setText(text)

    def _selected(self,_index):
        self.preferred=self.skin_id()
        self.preview.set_skin(self.preferred)
        self.changed.emit(self.preferred)

    def showEvent(self,event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        if self._loading:
            return
        self._loading=True
        self._generation+=1
        generation=self._generation
        self.apply_rank(0,loading=True)
        def read():
            result={"generation":generation,"rank":0}
            try:
                profile=self.profile_store.load_profile()
                result["rank"]=profile_rank(self.ranking_store.leaderboard(3),profile)
            except Exception:
                result["error"]=True
            try:
                self.loaded.emit(result)
            except RuntimeError:
                pass
        Thread(target=read,name="ArcadeSkinRank",daemon=True).start()

    def _loaded(self,result):
        if result["generation"]!=self._generation:
            return
        self._loading=False
        self.apply_rank(result["rank"],error=result.get("error",False))

def scroll_lobby(page):
    scroll=QScrollArea()
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    palette=page.palette()
    palette.setColor(QPalette.Window,QColor("#eef3f8"))
    page.setPalette(palette)
    page.setAutoFillBackground(True)
    scroll.setWidget(page)
    return scroll
