"""Computer ranking identity and per-player 1A2B history regressions."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from PySide6.QtNetwork import QTcpSocket
from PySide6.QtWidgets import QApplication
from app.features.chat_room.game_ranking import GameRankingStore,computer_rank,award_multiplayer_victory
from app.features.chat_room.store import ChatRoomStore,ChatProfileStore
from app.features.bulls_and_cows.engine import SinglePlayerGame
from app.features.pong.network import PongHost,PongClient
from app.features.tetris.network import TetrisHost,TetrisClient
from app.features.tank_battle.network import TankHost,TankClient
from app.features.snake.network import SnakeHost,SnakeClient
from app.features.bulls_and_cows.network import BullsAndCowsHost,BullsAndCowsClient
from ui.bulls_and_cows_page import BullsAndCowsPage
from ui.chat_room_page import ChatRoomPage

class ComputerRankingTests(unittest.TestCase):
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

    def profile(self,root,user,computer,nickname="暱稱"):
        path=root/"data"/"profiles"/(user+".json");path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(dict(user_id=user,identity_computer_name=computer,nickname=nickname)),encoding="utf-8")

    def legacy(self,root,event,user,name="舊暱稱",points=2):
        path=root/"data"/"game_ranking"/"events"/(event+".json");path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(dict(schema_version=1,event_id=event,user_id=user,player_key=user,nickname=name,game_id="pong",points=points)),encoding="utf-8")
        return path

    def test_machine_survives_rename_and_reinstalled_user_id(self):
        with TemporaryDirectory() as tmp:
            store=GameRankingStore(Path(tmp))
            store.record_win("舊名稱","pong",match_id="one",user_id="old",computer_name="cp2856")
            store.record_win("新名稱","tetris",match_id="two",user_id="new",computer_name=" CP2856 ")
            store.record_win("新名稱","snake",match_id="three",user_id="other",computer_name="CP9999")
            rows=store.leaderboard(10)
            self.assertEqual([(r.computer_name,r.points) for r in rows],[("CP2856",5),("CP9999",1)])
            self.assertEqual(rows[0].wins,2)
            self.assertEqual(computer_rank(rows,"cp2856"),1)

    def test_matching_legacy_profile_preserves_points_without_rewriting_file(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);old=self.legacy(root,"old","user-one");before=old.read_bytes()
            self.profile(root,"user-one","CP2856")
            store=GameRankingStore(root)
            store.record_win("改名","tetris",match_id="new",user_id="user-two",computer_name="CP2856")
            rows=store.leaderboard()
            self.assertEqual(len(rows),1);self.assertEqual(rows[0].points,5)
            self.assertEqual(rows[0].display_name,"CP2856")
            self.assertEqual(old.read_bytes(),before)

    def test_new_machine_evidence_can_attribute_legacy_uid_without_profile(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);self.legacy(root,"old","same-user")
            store=GameRankingStore(root)
            self.assertEqual(store.leaderboard()[0].computer_name,"")
            store.record_win("新名","pong",match_id="new",user_id="same-user",computer_name="CP2856")
            self.assertEqual(store.leaderboard()[0].points,4)
            self.assertEqual(store.leaderboard()[0].computer_name,"CP2856")

    def test_conflicting_copied_id_does_not_assign_unknown_wins_to_either_machine(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);self.legacy(root,"old","copied")
            store=GameRankingStore(root)
            for computer in ("CP1001","CP1002"):
                store.record_win("同名","snake",match_id=computer,user_id="copied",computer_name=computer)
            rows=store.leaderboard(10)
            self.assertEqual({r.computer_name:r.points for r in rows},{"":2,"CP1001":1,"CP1002":1})

    def test_unknown_old_name_is_never_treated_as_computer(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);self.legacy(root,"old","legacy-user","CP2856")
            row=GameRankingStore(root).leaderboard()[0]
            self.assertEqual(row.computer_name,"")
            self.assertIn("未識別電腦",row.display_name)
            self.assertEqual(computer_rank([row],"CP2856"),0)

    def test_explicit_computer_does_not_change_with_later_profile_rebinding(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);store=GameRankingStore(root)
            store.record_win("名字","pong",match_id="one",user_id="copied",computer_name="CP1001")
            self.profile(root,"copied","CP9999","另一台")
            self.assertEqual(store.leaderboard()[0].computer_name,"CP1001")

    def test_async_award_persists_computer_and_keeps_match_idempotent(self):
        with TemporaryDirectory() as tmp:
            store=GameRankingStore(Path(tmp))
            for _ in range(2):
                thread=award_multiplayer_victory("名字","pong",match_id="same",user_id="uid",computer_name="CP2856",store=store)
                thread.join(2);self.assertFalse(thread.is_alive())
            row=store.leaderboard()[0]
            self.assertEqual((row.computer_name,row.points,row.wins),("CP2856",2,1))

    def test_all_five_lan_games_report_guest_machine_even_for_identical_nicknames(self):
        cases=[
            ("pong",PongHost,PongClient,"app.features.pong.network","app.features.pong.network"),
            ("tetris",TetrisHost,TetrisClient,"app.features.tetris.network","app.features.tetris.network"),
            ("tank",TankHost,TankClient,"app.features.tank_battle.network","app.features.tank_battle.network"),
            ("snake",SnakeHost,SnakeClient,"app.features.snake.network","app.features.chat_room.arcade_lan"),
            ("bulls",BullsAndCowsHost,BullsAndCowsClient,"app.features.bulls_and_cows.network","app.features.chat_room.arcade_lan"),
        ]
        for game,Host,Client,host_module,client_module in cases:
            with self.subTest(game=game):
                host,client=Host(),Client()
                try:
                    with patch(host_module+".local_computer_name",return_value="CP2856"):
                        host.create_room("同名",port=0,user_id="host-id")
                    transport=getattr(host,"transport",host)
                    with patch(client_module+".local_computer_name",return_value="CP9002"):
                        client.connect_to_room(f"127.0.0.1:{transport.server.serverPort()}","同名",user_id="guest-id")
                    def joined():
                        if game=="tank":
                            return host.engine.status=="playing"
                        return getattr(transport,"guest_computer_name","")=="CP9002"
                    self.assertTrue(self.wait_for(joined))
                    if hasattr(host,"timer"):host.timer.stop()
                    if game=="tank":
                        host.engine.winner="1"
                        self.assertEqual(host.winner_ranking_identities(),[("同名","guest-id","CP9002")])
                    else:
                        owner=host.engine if game in ("pong","snake") else host
                        owner.winner="right"
                        self.assertEqual(host.winner_computer_name(),"CP9002")
                        self.assertEqual(host.winner_user_id(),"guest-id")
                        if game=="pong":
                            host.engine._apply_side_swap();host.engine.winner="left"
                            self.assertEqual(host.winner_computer_name(),"CP9002")
                            self.assertEqual(host.winner_user_id(),"guest-id")
                finally:
                    client.disconnect();host.close();client.deleteLater();host.deleteLater()

    def test_tank_team_rewards_keep_each_winner_computer(self):
        host=TankHost()
        try:
            host.host_user_id="u1";host.host_computer_name="CP1001"
            host.engine.reset(["同名"]*4,team_mode="teams")
            guest=QTcpSocket(host);guest.setProperty("user_id","u3");guest.setProperty("computer_name","CP1003")
            host.clients[2]=guest;host.engine.winner="team:0"
            self.assertEqual(host.winner_ranking_identities(),[("同名","u1","CP1001"),("同名","u3","CP1003")])
        finally:host.close();host.deleteLater()

    def test_chat_ranking_displays_computer_not_nickname(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);store=GameRankingStore(root)
            store.record_win("不應顯示的暱稱","pong",computer_name="CP2856")
            page=ChatRoomPage(lambda:None,lambda:None,chat_store=ChatRoomStore(root),profile_store=ChatProfileStore(root/"profile.json"))
            try:
                page._update_game_rankings(store.leaderboard())
                label=page.game_ranking_rows[0][1].text()
                self.assertIn("CP2856",label);self.assertNotIn("不應顯示的暱稱",label)
            finally:page.shutdown();page.deleteLater()

class SplitHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp=TemporaryDirectory();root=Path(self.tmp.name)
        self.page=BullsAndCowsPage(lambda:None,chat_store=ChatRoomStore(root),profile_store=ChatProfileStore(root/"profile.json"),
                                  single_game_factory=lambda:SinglePlayerGame("1234"))
    def tearDown(self):
        self.page.shutdown();self.page.close();self.page.deleteLater();self.tmp.cleanup()

    def state(self,history):
        return dict(status="playing",match_id="one",left_name="同名",right_name="同名",active_side="right",history=history)

    def test_identical_names_split_by_side_for_both_host_and_guest(self):
        history=[dict(turn=1,side="left",name="同名",guess="1234",result="1A2B"),
                 dict(turn=2,side="right",name="同名",guess="5678",result="0A1B"),
                 dict(turn=3,side="left",name="同名",guess="9012",result="2A0B")]
        for mode in ("lan_host","lan_client"):
            self.page.mode=mode;self.page._apply_lan_state(self.state(history))
            self.assertEqual(self.page.left_history_list.count(),2)
            self.assertEqual(self.page.right_history_list.count(),1)
            self.assertIn("1234",self.page.left_history_list.item(0).text())
            self.assertIn("5678",self.page.right_history_list.item(0).text())
            side="left" if mode=="lan_host" else "right"
            self.assertIn("（你）",self.page.history_headers[side].text())

    def test_snapshots_append_without_resetting_scroll_or_selection(self):
        self.page.mode="lan_host";self.page.resize(960,640);self.page.show()
        history=[dict(turn=i+1,side="left" if i%2==0 else "right",guess="1234",result="1A2B") for i in range(70)]
        self.page._apply_lan_state(self.state(history));self.app.processEvents()
        records=self.page.left_history_list
        records.setCurrentRow(2);records.verticalScrollBar().setValue(0)
        self.page._apply_lan_state(self.state(history+[dict(turn=71,side="left",guess="5678",result="0A1B")]))
        self.app.processEvents()
        self.assertEqual(records.verticalScrollBar().value(),0)
        self.assertEqual(records.currentRow(),2)
        self.assertEqual(records.count(),36)

    def test_new_match_clears_both_sides(self):
        self.page.mode="lan_host"
        self.page._apply_lan_state(self.state([dict(turn=1,side="right",guess="1234",result="1A2B")]))
        state=self.state([]);state["match_id"]="two"
        self.page._apply_lan_state(state)
        self.assertEqual(self.page.left_history_list.count(),0)
        self.assertEqual(self.page.right_history_list.count(),0)

    def test_local_two_player_uses_separate_records_then_single_mode_resets(self):
        self.page.start_two_player()
        self.page.two_player_game.set_secret(0,"1234");self.page.two_player_game.set_secret(1,"5678")
        with patch("ui.bulls_and_cows_page.QMessageBox.information"):
            self.page.guess_input.setText("9012");self.page.submit_guess()
            self.page.guess_input.setText("3456");self.page.submit_guess()
        self.assertEqual(self.page.left_history_list.count(),1)
        self.assertEqual(self.page.right_history_list.count(),1)
        self.page.start_single_player()
        self.assertEqual(self.page.history_pages.currentIndex(),0)
        self.assertEqual(self.page.left_history_list.count(),0)
        self.page.guess_input.setText("5678");self.page.submit_guess()
        self.assertEqual(self.page.history_list.count(),1)

if __name__=="__main__":
    unittest.main()
