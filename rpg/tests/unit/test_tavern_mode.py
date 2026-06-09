"""酒馆模式(Tavern Mode)回归测试。

覆盖:
  · content_pack 解析 → tavern_gm,且 provider 只产角色/persona 层,无 anchor/script 层
  · 工具可见性:tavern_gm 丢锚点/剧本/战斗,保留 memory/关系/世界书 overlay(决策4 持久记忆)
  · create_tavern_save:save_kind/script_id NULL/快照形状/first_mes 开场/character_book→overlay
  · save_to_chat_jsonl ↔ parse_chat_jsonl 文本无损往返(决策2)
  · save_io.import_save:tavern lane(script_id NULL)+ game lane 零回归

DB 类测试若本地无用户则 skip(纯逻辑测试始终运行)。所有创建的行在 tearDown 清理。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ── 纯逻辑(无 DB)──────────────────────────────────────────────────────────
class TavernManifestAndProvider(unittest.TestCase):
    def test_manifest_resolves_to_tavern_gm(self):
        from context_providers import DEFAULT_TAVERN_MANIFEST, resolve_content_pack

        class S:
            data = {"content_pack": DEFAULT_TAVERN_MANIFEST}
        m = resolve_content_pack(S())
        self.assertEqual(m["kind"], "tavern")
        self.assertEqual(m["gm_policy"]["mode"], "tavern_gm")
        # 无剧本:manifest 不含 anchor/script provider
        provs = m["context_providers"]
        self.assertIn("tavern_character", provs)
        self.assertIn("memory", provs)
        self.assertNotIn("script_phase_anticipation", provs)
        self.assertNotIn("novel_retrieval", provs)

    def test_provider_emits_character_persona_no_script_layers(self):
        from context_providers import get_provider
        from context_providers.base import Demand

        class S:
            data = {
                "tavern": {
                    "character": {"name": "伊蕾娜", "personality": "冷淡", "sample_dialogue": ["哼。"]},
                    "system_prompt": "始终第一人称。",
                    "scenario": "雨夜酒馆。",
                },
                "player": {"name": "阿白", "role": "旅人"},
            }
        contrib = get_provider("tavern_character").collect(S(), {}, Demand.empty(), None)
        ids = {layer["id"] for layer in contrib.layers}
        self.assertEqual(ids, {"tavern_card_system", "tavern_character", "tavern_persona"})
        # 卡内 system_prompt 走最高优先级层(强制注入)
        sys_layer = next(layer for layer in contrib.layers if layer["id"] == "tavern_card_system")
        self.assertGreaterEqual(sys_layer["priority"], 95)
        self.assertIn("始终第一人称", sys_layer["content"])

    def test_provider_skips_when_no_tavern_state(self):
        from context_providers import get_provider

        class S:
            data = {"player": {"name": "x"}}
        self.assertFalse(get_provider("tavern_character").applies(S(), {}, None))


class TavernToolVisibility(unittest.TestCase):
    def test_tavern_full_agent_drops_only_canon(self):
        # 酒馆 = 完整 harness agent:无绑定剧本只丢 canon 读/写;绑定只读剧本仅禁 canon 写(kb_)。
        # 非酒馆(游戏控制台 mode=None)反过来:丢掉酒馆自管理工具,别污染游戏工具表。
        from tools_dsl.chat_tool_router import build_unified_tool_list
        from tools_dsl.command_tools_register import ensure_registered
        ensure_registered()
        nontavern = {d["name"] for d in build_unified_tool_list(None, origin="llm_chat")}
        tav = {d["name"] for d in build_unified_tool_list(None, origin="llm_chat", mode="tavern_gm")}
        # 酒馆保留:记忆/关系/世界书 overlay/clarify(+ 战斗/物品/模组等完整 agent 工具)
        for keep in ("add_memory_fact", "set_relationship", "worldbook_add", "clarify"):
            self.assertIn(keep, tav, f"{keep} 酒馆应保留")
        # 酒馆自管理工具:酒馆里在、游戏控制台里不在(不污染、不抢 _rank 窗口)
        self.assertIn("edit_tavern_character", tav, "酒馆应有自管理工具")
        for tname in ("set_tavern_character", "edit_tavern_character", "tavern_list_scripts"):
            self.assertNotIn(tname, nontavern, f"{tname} 不应出现在非酒馆(游戏)工具表")
        # 无绑定剧本:canon 读/写工具无对象 → 丢
        for drop in ("search_canon", "kb_upsert_entity"):
            self.assertNotIn(drop, tav, f"{drop}(canon)无剧本时应丢")
        # 绑定只读剧本:canon 读放开(贴合原著),仅 canon 写(kb_)仍禁
        tav_b = {d["name"] for d in build_unified_tool_list(
            None, origin="llm_chat", mode="tavern_gm", bound_script_id=7)}
        self.assertIn("search_canon", tav_b, "绑定后 canon 读应放开")
        self.assertNotIn("kb_upsert_entity", tav_b, "绑定只读剧本后 canon 写(kb_)仍禁")


# ── DB 集成(需本地有用户;否则 skip)─────────────────────────────────────────
def _first_user_id():
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute("select id from users order by id limit 1").fetchone()
        return row["id"] if row else None
    except Exception:
        return None


@unittest.skipUnless(_first_user_id() is not None, "本地无用户,跳过 DB 集成测试")
class TavernSaveLifecycle(unittest.TestCase):
    def setUp(self):
        from platform_app import user_cards
        self.uid = _first_user_id()
        self._card_ids: list[int] = []
        self._save_ids: list[int] = []
        card = user_cards.upsert_user_card(self.uid, {
            "name": "测试酒馆角色",
            "identity": "流浪剑客", "personality": "冷淡毒舌",
            "metadata": {
                "first_mes": "……找我有事？",
                "system_prompt": "第一人称，绝不出戏。",
                "scenario": "雨夜酒馆。",
                "character_book": {"entries": [
                    {"keys": ["剑"], "content": "她的断剑名为'残'。", "comment": "武器", "priority": 70},
                    {"keys": ["x"], "content": "禁用条目", "enabled": False},
                ]},
            },
        })
        self.card_id = card["id"]
        self._card_ids.append(self.card_id)

    def tearDown(self):
        from platform_app.db import connect
        with connect() as db:
            for sid in self._save_ids:
                db.execute("delete from game_saves where id=%s", (sid,))
            for cid in self._card_ids:
                db.execute("delete from character_cards where id=%s", (cid,))

    def test_create_tavern_save_shape_and_overlay(self):
        from platform_app import workspace
        from platform_app.db import connect
        save = workspace.create_tavern_save(self.uid, self.card_id)
        self._save_ids.append(save["id"])
        with connect() as db:
            row = db.execute(
                "select script_id, save_kind, tavern_character_card_id, state_snapshot "
                "from game_saves where id=%s", (save["id"],),
            ).fetchone()
            snap = row["state_snapshot"]
            self.assertIsNone(row["script_id"])
            self.assertEqual(row["save_kind"], "tavern")
            self.assertEqual(row["tavern_character_card_id"], self.card_id)
            self.assertEqual((snap.get("content_pack") or {}).get("gm_policy", {}).get("mode"), "tavern_gm")
            self.assertEqual((snap.get("tavern") or {}).get("system_prompt"), "第一人称，绝不出戏。")
            # first_mes 进 history(开场 assistant)
            hist = snap.get("history") or []
            self.assertTrue(any(h.get("role") == "assistant" and "找我有事" in h.get("content", "") for h in hist))
            # character_book → save_worldbook_overlays(禁用条目被跳过 → 仅 1 条)
            ov = db.execute("select count(*) c from save_worldbook_overlays where save_id=%s", (save["id"],)).fetchone()
            self.assertEqual(ov["c"], 1)

    def test_first_mes_commit_turn_index_zero(self):
        """BUGFIX(回退多删一轮):first_mes 开场 round commit 的 turn_index 必须为 0,与 GM 运行期
        开场(record_runtime_turn 用 data['turn']=history_len//2)对齐。否则前端按 msg_index//2
        回退会命中早一个 turn → continue_from 多截一轮。"""
        from platform_app import workspace
        from platform_app.db import connect
        from platform_app.branches.tree_ops import resolve_commit_id_by_message
        save = workspace.create_tavern_save(self.uid, self.card_id)
        self._save_ids.append(save["id"])
        sid = int(save["id"])
        with connect() as db:
            rows = db.execute(
                "select id, turn_index, kind, gm_output from branch_commits where save_id=%s order by turn_index, id",
                (sid,),
            ).fetchall()
        # first_mes 开场必须是 turn_index=0 的 round commit(不是 turn 1)
        opening_rounds = [r for r in rows if r["kind"] == "round" and int(r["turn_index"]) == 0
                          and "找我有事" in (r.get("gm_output") or "")]
        self.assertTrue(opening_rounds, f"first_mes 开场应在 turn_index 0;实际 commits={[(r['kind'], r['turn_index']) for r in rows]}")
        # 回退到开场(msg_index=0)应命中 first_mes 的 round commit(turn 0,id>root),而非把它一起删掉
        cid = resolve_commit_id_by_message(self.uid, sid, 0)
        self.assertEqual(cid, int(opening_rounds[0]["id"]))

    def test_chat_jsonl_round_trip(self):
        from platform_app import tavern_chats, workspace
        save = workspace.create_tavern_save(self.uid, self.card_id)
        self._save_ids.append(save["id"])
        text = tavern_chats.save_to_chat_jsonl(save["id"])
        header, commits = tavern_chats.parse_chat_jsonl(text)  # 应无异常
        self.assertEqual(header["character_name"], "测试酒馆角色")
        self.assertTrue(len(commits) >= 1)


@unittest.skipUnless(_first_user_id() is not None, "本地无用户,跳过 DB 集成测试")
class ImportSaveTavernLane(unittest.TestCase):
    def setUp(self):
        self.uid = _first_user_id()
        self._save_ids: list[int] = []

    def tearDown(self):
        from platform_app.db import connect
        with connect() as db:
            for sid in self._save_ids:
                db.execute("delete from game_saves where id=%s", (sid,))

    def test_tavern_payload_creates_null_script_save(self):
        from platform_app import save_io, tavern_chats
        text = ('{"user_name":"我","character_name":"小红","create_date":""}\n'
                '{"name":"我","is_user":true,"mes":"你好"}\n'
                '{"name":"小红","is_user":false,"mes":"嗯？"}')
        header, commits = tavern_chats.parse_chat_jsonl(text)
        payload = tavern_chats.chat_to_save_payload(header, commits)
        self.assertEqual(payload["save"]["save_kind"], "tavern")
        res = save_io.import_save(self.uid, payload)
        self._save_ids.append(res["save_id"])
        self.assertIsNone(res["script_id"])
        self.assertEqual(res["save_kind"], "tavern")


if __name__ == "__main__":
    unittest.main()
