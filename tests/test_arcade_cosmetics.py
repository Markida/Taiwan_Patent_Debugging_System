"""Regression coverage for rank cosmetics, LAN identity and finite visual events."""
import json
import os
from pathlib import Path
import random
import socket
from tempfile import TemporaryDirectory
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

from PySide6.QtWidgets import QApplication,QWidget
from app.features.arcade_cosmetics import (
    ArcadeEventLog,game_skins,normalize_arcade_skin,profile_rank,unlocked_game_skins,
)
from app.features.chat_room.store import ChatProfileStore,ChatRoomStore
from app.features.pong.network import PongEngine,PongHost,PongClient
from app.features.tank_battle.network import TankBattleEngine,TankHost,TankClient
from app.features.snake.network import SnakeBattleEngine,SnakeHost,SnakeClient
from app.features.bulls_and_cows.network import BullsAndCowsHost,BullsAndCowsClient
from app.features.snake.score_store import SnakeScoreStore
from ui.arcade_skin_art import ArcadeEffects,GuessReveal
from ui.arcade_skin_picker import ArcadeSkinPicker
from ui.pong_game_page import PongGamePage
from ui.tank_battle_page import TankBattlePage
from ui.snake_game_page import SnakeBoard,SnakeGamePage
from ui.bulls_and_cows_page import BullsAndCowsPage

class ArcadeCosmeticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def wait_for(self,predicate):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            self.app.processEvents()
            if predicate():return True
            time.sleep(.005)
        return False

    def test_rewards_are_cumulative_for_all_four_games(self):
        for game in ("tank","pong","bulls_and_cows","snake"):
            with self.subTest(game=game):
                self.assertEqual(len({s.skin_id for s in game_skins(game)}),10)
                for rank,count in ((0,1),(4,1),(3,4),(2,7),(1,10)):
                    self.assertEqual(len(unlocked_game_skins(game,rank)),count)
                third={s.skin_id for s in unlocked_game_skins(game,3)}
                second={s.skin_id for s in unlocked_game_skins(game,2)}
                self.assertLess(third,second)

    def test_unknown_skin_falls_back(self):
        for value in (None,"wrong",{},["sakura"]):
            self.assertEqual(normalize_arcade_skin(value),"classic")

    def test_rank_identity_does_not_award_a_namesake(self):
        profile=SimpleNamespace(user_id="me",nickname="同名")
        entry=lambda uid,name,computer="":SimpleNamespace(user_id=uid,nickname=name,computer_name=computer)
        self.assertEqual(profile_rank([entry("other","同名","CP1111")],profile,"CP2856"),0)
        self.assertEqual(profile_rank([entry("other","同名","CP1111"),entry("new-id","新名稱","cp2856")],profile,"CP2856"),2)
        self.assertEqual(profile_rank([entry("me","同名")],profile,"CP2856"),0)

    def test_picker_downgrade_and_error_revoke_locked_selection(self):
        with TemporaryDirectory() as tmp:
            picker=ArcadeSkinPicker("tank",ChatProfileStore(Path(tmp)/"profile.json"),Path(tmp))
            picker.apply_rank(1)
            picker.selector.setCurrentIndex(picker.selector.findData("sakura"))
            self.assertEqual(picker.skin_id(),"sakura")
            picker.apply_rank(3)
            self.assertEqual(picker.skin_id(),"classic")
            self.assertFalse(picker.selector.model().item(9).isEnabled())
            picker.apply_rank(0,error=True)
            self.assertEqual(picker.skin_id(),"classic")
            self.assertIn("無法讀取",picker.access_label.text())
            picker.deleteLater()

    def test_rank_loading_runs_off_gui_thread(self):
        started,release=Event(),Event()
        class Store:
            def leaderboard(self,count):
                started.set();release.wait(1)
                return [SimpleNamespace(user_id="me",nickname="甲",computer_name=socket.gethostname())]
        profile=SimpleNamespace(load_profile=lambda:SimpleNamespace(user_id="me",nickname="甲"))
        picker=ArcadeSkinPicker("pong",profile,Path("."),ranking_store=Store())
        try:
            picker.refresh()
            self.assertTrue(started.wait(.5))
            self.assertEqual(picker.skin_id(),"classic")
            release.set()
            self.assertTrue(self.wait_for(lambda:picker.rank==1))
        finally:
            release.set();picker.deleteLater()

    def test_event_window_bounded_and_match_reset_distinct(self):
        log=ArcadeEventLog()
        for i in range(80):log.emit("shot",i,0)
        self.assertEqual(len(log.state()["effects"]),24)
        self.assertEqual(log.events[-1]["id"],80)
        self.assertNotEqual(log.match_id,ArcadeEventLog().match_id)

    def test_effects_deduplicate_expire_and_stop_when_hidden(self):
        widget=QWidget();effects=ArcadeEffects(widget)
        log=ArcadeEventLog();log.emit("hit",1,2)
        try:
            widget.show();self.app.processEvents()
            effects.consume(log.state());effects.consume(log.state())
            self.assertEqual(len(effects.events),1)
            self.assertTrue(effects.timer.isActive())
            widget.hide();self.app.processEvents()
            self.assertFalse(effects.timer.isActive())
            effects.events[0]["born"]-=10
            widget.show();self.app.processEvents()
            self.assertFalse(effects.events)
            self.assertFalse(effects.timer.isActive())
            log2=ArcadeEventLog();log2.emit("hit",4,5)
            effects.consume(log2.state())
            self.assertEqual(effects.last_id,1)
        finally:widget.close();widget.deleteLater()

    def test_pong_skin_moves_with_player_during_swap(self):
        engine=PongEngine();engine.reset_match("甲","乙","neon","sakura")
        engine._apply_side_swap()
        state=engine.to_state()
        self.assertEqual((state["left_name"],state["left_skin"]),("乙","sakura"))
        self.assertEqual((state["right_name"],state["right_skin"]),("甲","neon"))

    def test_pong_hit_powerup_and_score_emit_distinct_events(self):
        engine=PongEngine(winning_score=1);engine.reset_match("甲","乙","galaxy","candy")
        engine.ball_x=engine.LEFT_X+engine.PADDLE_WIDTH-2
        engine.ball_y=engine.left_y+30;engine.ball_vx=-400;engine.ball_vy=0
        engine._paddle_collision("left")
        self.assertEqual(engine.ball_skin,"galaxy")
        engine.powerups=[{"type":"speed","x":engine.ball_x,"y":engine.ball_y}]
        engine._collect_powerup_if_hit()
        engine._score("left")
        self.assertEqual([e["kind"] for e in engine.events.events],["hit","bonus","score","victory"])

    def test_pong_lan_syncs_both_skins_and_events(self):
        host,client=PongHost(),PongClient();states=[]
        client.state_changed.connect(states.append)
        try:
            host.create_room("甲",port=0,skin_id="ocean")
            client.connect_to_room(f"127.0.0.1:{host.server.serverPort()}","乙",skin_id="lava")
            self.assertTrue(self.wait_for(lambda:bool(states)))
            host.timer.stop()
            self.assertEqual((states[-1]["left_skin"],states[-1]["right_skin"]),("ocean","lava"))
            host.engine._score("left");host._broadcast_state()
            self.assertTrue(self.wait_for(lambda:any(s.get("effects") for s in states)))
        finally:
            client.disconnect();host.close();client.deleteLater();host.deleteLater()

    def test_tank_four_player_skins_follow_slots(self):
        host=TankHost();clients=[];states=[]
        try:
            host.create_room("房主",max_players=4,port=0,skin_id="neon")
            for name,skin in (("乙","candy"),("丙","galaxy"),("丁","sakura")):
                client=TankClient();clients.append(client)
                client.state_changed.connect(states.append)
                client.connect_to_room(f"127.0.0.1:{host.server.serverPort()}",name,skin_id=skin)
                self.assertTrue(self.wait_for(lambda:client.slot>0))
            self.assertTrue(self.wait_for(lambda:host.engine.status=="playing"))
            host.timer.stop()
            expected={"房主":"neon","乙":"candy","丙":"galaxy","丁":"sakura"}
            self.assertEqual({p["name"]:p["skin_id"] for p in host.engine.players.values()},expected)
            self.assertTrue(self.wait_for(lambda:any(len(s["players"])==4 for s in states)))
            self.assertEqual({p["name"]:p["skin_id"] for p in states[-1]["players"]},expected)
        finally:
            for client in clients:client.disconnect();client.deleteLater()
            host.close();host.deleteLater()

    def test_tank_fire_wall_and_damage_effects_preserve_rules(self):
        engine=TankBattleEngine();engine.reset(["甲","乙"],skins=["lava"])
        player=engine.players[0]
        player.update(x=5,y=5,direction="right",cooldown=0)
        engine.map[5][6]=1
        engine.action(0,"shoot");engine.tick()
        self.assertEqual(engine.map[5][6],0)
        self.assertEqual([e["kind"] for e in engine.events.events],["shot","explosion"])
        target=engine.players[1];target["lives"]=1
        engine._damage(target);engine._finish_if_needed()
        self.assertFalse(target["alive"])
        self.assertEqual(engine.winner,"0")
        self.assertEqual(engine.events.events[-1]["kind"],"victory")

    def test_snake_lan_syncs_skins_and_food_event(self):
        host,client=SnakeHost(),SnakeClient();states=[]
        client.state_changed.connect(states.append)
        try:
            host.create_room("甲",port=0,skin_id="forest")
            client.connect_to_room(f"127.0.0.1:{host.transport.server.serverPort()}","乙",skin_id="ice")
            self.assertTrue(self.wait_for(lambda:bool(states)))
            host.timer.stop()
            self.assertEqual((states[-1]["left_skin"],states[-1]["right_skin"]),("forest","ice"))
            host.engine.food=(8,11);host._tick()
            self.assertEqual(host.engine.scores["left"],1)
            self.assertEqual(host.engine.events.events[-1]["kind"],"eat")
            host.engine.snakes["left"]=[(29,11),(28,11)]
            host.engine.tick()
            self.assertEqual(host.engine.status,"finished")
            self.assertIn("explosion",[e["kind"] for e in host.engine.events.events])
        finally:
            client.disconnect();host.close();client.deleteLater();host.deleteLater()

    def test_local_snake_food_bonus_and_collision_effects(self):
        board=SnakeBoard()
        try:
            board.running=True;board.food=(16,11)
            board.advance()
            self.assertEqual(board.score,10)
            self.assertEqual(board.effects.events[-1]["kind"],"eat")
            board.bonus_food=(17,11);board.bonus_ticks=10;board.food=(0,0)
            board.advance()
            self.assertEqual(board.score,40)
            self.assertEqual(board.effects.events[-1]["kind"],"bonus")
            board.snake=[(29,11),(28,11)];board.advance()
            self.assertFalse(board.running)
            self.assertEqual(board.effects.events[-1]["kind"],"explosion")
        finally:board.timer.stop();board.deleteLater()

    def test_bulls_lan_cosmetics_never_serialize_secrets(self):
        host,client=BullsAndCowsHost(),BullsAndCowsClient();states=[]
        client.state_changed.connect(states.append)
        try:
            host.create_room("甲",port=0,skin_id="sunset")
            client.connect_to_room(f"127.0.0.1:{host.transport.server.serverPort()}","乙",skin_id="sakura")
            self.assertTrue(self.wait_for(lambda:bool(states)))
            self.assertEqual((states[-1]["left_skin"],states[-1]["right_skin"]),("sunset","sakura"))
            host.set_secret("left","1234");client.set_secret("5678")
            self.assertTrue(self.wait_for(lambda:host.status=="playing"))
            payload=json.dumps(host.state())
            self.assertNotIn("1234",payload);self.assertNotIn("5678",payload)
            host.submit_guess("left","5678")
            self.assertEqual(host.state()["history"][-1]["result"],"4A0B")
        finally:
            client.disconnect();host.close();client.deleteLater();host.deleteLater()

    def test_bulls_result_not_reanimated_for_duplicate_snapshot(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            page=BullsAndCowsPage(lambda:None,chat_store=ChatRoomStore(root/"chat"),
                                  profile_store=ChatProfileStore(root/"profile.json"))
            page.mode="lan_client"
            state=dict(status="playing",match_id="one",active_side="right",
                       left_skin="galaxy",history=[dict(turn=1,side="left",name="甲",guess="1234",result="1A2B")])
            try:
                with patch.object(page.guess_reveal,"reveal",wraps=page.guess_reveal.reveal) as reveal:
                    page._apply_lan_state(state);page._apply_lan_state(state)
                    self.assertEqual(reveal.call_count,1)
                    self.assertEqual(page.guess_reveal.skin_id,"galaxy")
            finally:page.shutdown();page.deleteLater()

    def test_cpu_and_local_modes_use_selected_skin(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);stores=dict(chat_store=ChatRoomStore(root/"chat"),profile_store=ChatProfileStore(root/"profile.json"))
            pages=[PongGamePage(lambda:None,**stores),TankBattlePage(lambda:None,**stores),
                   SnakeGamePage(lambda:None,score_store=SnakeScoreStore(root/"scores.json"),**stores),
                   BullsAndCowsPage(lambda:None,**stores)]
            try:
                for page in pages:
                    page.skin_picker.apply_rank(1)
                    page.skin_picker.selector.setCurrentIndex(page.skin_picker.selector.findData("galaxy"))
                pages[0].player_name.setText("甲");pages[0].start_cpu_match();pages[0].local_timer.stop()
                self.assertEqual(pages[0].local_engine.left_skin,"galaxy")
                pages[1].player_name.setText("甲");pages[1].start_cpu_match();pages[1].local_timer.stop()
                self.assertEqual(pages[1].local_engine.players[0]["skin_id"],"galaxy")
                pages[2].show_local_mode()
                self.assertEqual(pages[2].board.skin_id,"galaxy")
                pages[3].start_single_player()
                self.assertEqual(pages[3].guess_reveal.skin_id,"galaxy")
            finally:
                for page in pages:page.shutdown();page.deleteLater()

    def test_cosmetics_do_not_change_pong_physics(self):
        a=PongEngine(random_source=random.Random(3))
        b=PongEngine(random_source=random.Random(3))
        a.reset_match("甲","乙");b.reset_match("甲","乙","sakura","lava")
        for _ in range(200):a.step(.016);b.step(.016)
        for name in ("ball_x","ball_y","ball_vx","ball_vy","left_score","right_score"):
            self.assertEqual(getattr(a,name),getattr(b,name))

if __name__=="__main__":
    unittest.main()
