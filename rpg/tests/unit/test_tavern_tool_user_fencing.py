"""test_tavern_tool_user_fencing.py — 跨用户/跨剧本工具围栏回归测试。

覆盖 2026-06-07 安全审计(对抗式验证)确认的 7 个越权点:dispatcher save_id 注入 +
执行器缺归属校验,使 LLM(origin=llm_chat,酒馆/GM agent)可借 tool args 里注入的
异档 save_id / 任意 script_id 读写他人数据。修复分两层:
  Layer 1 —— dispatcher 对 save 级工具**无条件覆盖** args["save_id"]=env.save_id。
  Layer 2 —— user/script 级执行器自带 _own_save / owner-or-subscriber 归属校验。
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from tools_dsl.command_dispatcher import (  # noqa: E402
    ToolCallEnvelope,
    ToolDispatcher,
    ToolRegistry,
    ToolSpec,
)


# ════════════════════════════════════════════════════════════════════
# Layer 1 (纯单元,无 DB):dispatcher 对 save 级工具无条件 pin save_id
# ════════════════════════════════════════════════════════════════════
class DispatcherSaveIdFencing(unittest.TestCase):
    def _run(self, env_save_id, args):
        captured: dict = {}

        def _exec(state, a):
            captured.update(a)
            return "ok"

        reg = ToolRegistry()
        reg.register(ToolSpec(
            name="_fake_save_tool",
            description="x",
            input_schema={"type": "object", "properties": {}, "required": []},
            executor=_exec, scope="save", origins=frozenset({"llm_chat"}),
        ))
        disp = ToolDispatcher(reg, state_provider=lambda env: object())
        env = ToolCallEnvelope(
            user_id=1, tool="_fake_save_tool", args=dict(args),
            origin="llm_chat", save_id=env_save_id,
        )
        r = disp.dispatch_sync(env)
        return r, captured

    def test_llm_supplied_save_id_is_overwritten(self):
        """LLM 在 args 里注入异档 save_id → dispatcher 用 env.save_id 覆盖之。"""
        r, captured = self._run(100, {"save_id": 999999, "x": 1})
        self.assertTrue(r.ok, r.error)
        self.assertEqual(captured.get("save_id"), 100,
                         "save 级工具必须恒用 env.save_id,绝不接受 LLM 注入的 save_id")

    def test_save_id_injected_when_absent(self):
        """args 无 save_id 时,dispatcher 注入 env.save_id(保持旧的便利行为)。"""
        r, captured = self._run(55, {"x": 1})
        self.assertTrue(r.ok, r.error)
        self.assertEqual(captured.get("save_id"), 55)


# ════════════════════════════════════════════════════════════════════
# Layer 2 (DB 集成):跨用户/跨剧本归属校验。需 ≥2 个用户。
# ════════════════════════════════════════════════════════════════════
def _two_users():
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute("select id from users order by id limit 2").fetchall()
        return [int(r["id"]) for r in (rows or [])]
    except Exception:
        return []


def _foreign_script(owner_not: int):
    """找一个 owner 不是 owner_not 的剧本 id(用作"他人私有剧本")。"""
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select id from scripts where owner_id <> %s order by id limit 1",
                (owner_not,),
            ).fetchone()
        return int(row["id"]) if row else None
    except Exception:
        return None


_UIDS = _two_users()


@unittest.skipUnless(len(_UIDS) >= 2, "本地需 ≥2 个用户做跨用户测试,跳过")
class CrossUserToolFencing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from platform_app import user_cards, workspace
        from tools_dsl.command_tools_register import ensure_registered
        ensure_registered()
        cls.victim, cls.attacker = _UIDS[0], _UIDS[1]
        cls._saves: list[int] = []
        cls._cards: list[int] = []
        cls.attacker_save = int(workspace.create_tavern_save(cls.attacker, None)["id"])
        cls.victim_save = int(workspace.create_tavern_save(cls.victim, None)["id"])
        cls._saves += [cls.attacker_save, cls.victim_save]
        # 受害者私有角色卡
        vc = user_cards.upsert_user_card(cls.victim, {"name": "受害者秘密角色", "identity": "机密"})
        cls.victim_card = int(vc["id"])
        cls._cards.append(cls.victim_card)
        # 他人私有剧本(owner != attacker)
        cls.foreign_script = _foreign_script(cls.attacker)

    @classmethod
    def tearDownClass(cls):
        from platform_app.db import connect
        try:
            with connect() as db:
                for sid in cls._saves:
                    db.execute("delete from save_worldbook_overlays where save_id=%s", (sid,))
                    db.execute("delete from game_saves where id=%s", (sid,))
                for cid in cls._cards:
                    db.execute("delete from character_cards where id=%s", (cid,))
        except Exception:
            pass

    def _state(self):
        from state import DEFAULT_STATE, GameState
        s = GameState(copy.deepcopy(DEFAULT_STATE))
        s.data["turn"] = 1
        return s

    # ---- worldbook_add:Layer1 经 dispatcher 把写入重定向到攻击者自己的存档 ----
    def test_worldbook_add_cannot_write_to_victim_save(self):
        from platform_app.db import connect
        from tools_dsl.command_dispatcher import ToolDispatcher, get_registry
        state = self._state()
        disp = ToolDispatcher(get_registry(), state_provider=lambda env, _s=state: _s)
        marker = "INJECT_TEST_8c1f"
        env = ToolCallEnvelope(
            user_id=self.attacker, tool="worldbook_add",
            args={"save_id": self.victim_save, "title": marker, "content": "x"},
            origin="llm_chat", save_id=self.attacker_save,
        )
        r = disp.dispatch_sync(env)
        self.assertTrue(r.ok, f"worldbook_add 应成功(写到自己存档): {r.error}")
        with connect() as db:
            on_victim = db.execute(
                "select count(*) c from save_worldbook_overlays where save_id=%s and title=%s",
                (self.victim_save, marker),
            ).fetchone()["c"]
            on_attacker = db.execute(
                "select count(*) c from save_worldbook_overlays where save_id=%s and title=%s",
                (self.attacker_save, marker),
            ).fetchone()["c"]
        self.assertEqual(on_victim, 0, "越权:写入落到了受害者存档")
        self.assertGreaterEqual(on_attacker, 1, "Layer1 应把写入重定向到攻击者自己的存档")

    # ---- anchors:user 级执行器自带 _own_save 校验 ----
    def test_record_history_anchor_rejects_foreign_save(self):
        from tools_dsl.command_tools_anchors import _t_record_history_anchor
        out = _t_record_history_anchor(self.attacker, {"save_id": self.victim_save, "summary": "x"})
        self.assertIn("权限", out, f"应拒绝跨用户写历史锚点,实际: {out}")

    def test_list_recent_history_rejects_foreign_save(self):
        from tools_dsl.command_tools_anchors import _t_list_recent_history
        out = _t_list_recent_history(self.attacker, {"save_id": self.victim_save})
        self.assertIn("权限", out, f"应拒绝跨用户读历史,实际: {out}")

    def test_check_pending_anchor_drift_rejects_foreign_save(self):
        from tools_dsl.command_tools_anchors import _t_check_pending_anchor_drift
        out = _t_check_pending_anchor_drift(self.attacker, {"save_id": self.victim_save, "anchor_keys": ["k"]})
        self.assertIn("权限", out, f"应拒绝跨用户读锚点漂移,实际: {out}")

    # ---- script 级执行器:owner-or-subscriber 校验 ----
    def test_script_read_tools_reject_foreign_script(self):
        if not self.foreign_script:
            self.skipTest("无他人剧本可测")
        from tools_dsl.command_tools_misc import _t_get_chapter_facts, _t_get_worldbook
        from tools_dsl.command_tools_queries import _t_get_script_chapters, _t_list_script_npcs
        sid = self.foreign_script
        for fn, name in (
            (_t_get_chapter_facts, "get_chapter_facts"),
            (_t_get_worldbook, "get_worldbook"),
            (_t_get_script_chapters, "get_script_chapters"),
            (_t_list_script_npcs, "list_script_npcs"),
        ):
            out = fn(self.attacker, None, {"script_id": sid}, None)
            self.assertIn("权限", out, f"{name} 应拒绝读他人私有剧本 #{sid},实际: {out}")

    # ---- set_tavern_character:经 dispatcher 不能借注入 save_id 读他人卡片 ----
    def test_set_tavern_character_cannot_load_victim_card(self):
        from tools_dsl.command_dispatcher import ToolDispatcher, get_registry
        state = self._state()
        disp = ToolDispatcher(get_registry(), state_provider=lambda env, _s=state: _s)
        env = ToolCallEnvelope(
            user_id=self.attacker, tool="set_tavern_character",
            args={"character_card_id": self.victim_card, "save_id": self.victim_save},
            origin="llm_chat", save_id=self.attacker_save,
        )
        r = disp.dispatch_sync(env)
        # Layer1 把 save_id 改回 attacker_save → _resolve_user_id 得到 attacker →
        # get_user_card(attacker, victim_card)=None → 找不到卡。受害者卡绝不应进 state。
        loaded = (state.data.get("tavern") or {}).get("character") or {}
        self.assertNotEqual(loaded.get("identity"), "机密",
                            "越权:受害者私有角色卡被载入了攻击者会话")
        self.assertFalse(r.ok and "已切换" in str(r.result),
                         f"不应成功载入他人卡片,实际: {r.result}")


if __name__ == "__main__":
    unittest.main()
