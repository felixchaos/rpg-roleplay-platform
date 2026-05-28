"""
test_command_tools_extended.py — task 87 Phase 2.2 / 2.3 / 3 工具测试

不需要真 DB / 真 LLM —— 用 monkeypatch 把 DB / branches / rules_bridge 替身掉,
聚焦验证:
  · 工具注册成功 + scope/origins 正确
  · 输入校验 / 执行结果文本格式
  · destructive 工具不允许 llm_chat / llm_set
  · query 工具返回 JSON 形态
  · 通过 dispatcher 调用,跨账号隔离仍有效
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state import GameState, DEFAULT_STATE  # noqa: E402
from tools_dsl.command_dispatcher import (  # noqa: E402
    ToolCallEnvelope, ToolDispatcher, get_registry,
)
from tools_dsl.command_tools_register import force_reset_for_tests  # noqa: E402


def _new_state(turn=3) -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = turn
    return s


class RegistrationSanity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def test_all_scopes_present(self):
        reg = get_registry()
        scopes = {t.scope for t in reg.list_all()}
        # task 87 应覆盖 4 个 scope
        self.assertIn("save", scopes)
        self.assertIn("user", scopes)
        self.assertIn("global", scopes)
        # script 工具至少有 2 个
        script_tools = [t for t in reg.list_all() if t.scope == "script"]
        self.assertGreaterEqual(len(script_tools), 2)

    def test_destructive_tools_block_llm_origins(self):
        """所有 destructive=True 的工具,llm_chat 必须不在 origins 里。"""
        for t in get_registry().list_all():
            if t.destructive:
                self.assertNotIn(
                    "llm_chat", t.origins,
                    f"destructive 工具 {t.name} 不应允许 llm_chat",
                )

    def test_total_tool_count(self):
        reg = get_registry()
        # 起步至少 60+ (18 base + 8 phase2 + 10 rules + 8 saves + 17 query)
        self.assertGreaterEqual(len(reg.list_all()), 60)


# ────────────────────────────────────────────────────────────
# Phase 2.2 saves/branches user 级工具
# ────────────────────────────────────────────────────────────


class SavesUserToolsExecution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state()
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.state,
        )

    def _call(self, tool, args, origin="ui_button", trace_id=None, user_id=1):
        env = ToolCallEnvelope(
            user_id=user_id, save_id=None, tool=tool, args=args,
            origin=origin, trace_id=trace_id or f"t-{tool}",
        )
        return self.dispatcher.dispatch_sync(env)

    def test_list_my_saves_handles_db_failure(self):
        """DB 失败时返回失败字符串,不抛异常。"""
        with patch("platform_app.db.connect") as conn:
            conn.side_effect = Exception("DB 不可用")
            r = self._call("list_my_saves", {})
            self.assertFalse(r.ok)
            self.assertIn("失败", r.result or "")

    def test_delete_save_blocked_from_llm_set(self):
        """delete_save destructive=True,llm_set 不允许 (只 ui_button)。"""
        r = self._call("delete_save", {"save_id": 1}, origin="llm_set", trace_id="td-1")
        self.assertFalse(r.ok)
        # destructive_blocked 或 origin_forbidden(取决于哪个先 fail)
        self.assertTrue(
            "origin_forbidden" in (r.error or "") or "destructive_blocked" in (r.error or ""),
            f"应拒绝;实际 {r.error}",
        )

    def test_delete_branch_blocked_from_llm_chat(self):
        r = self._call("delete_branch", {"branch_id": 5}, origin="llm_chat", trace_id="td-2")
        self.assertFalse(r.ok)
        self.assertTrue(
            "origin_forbidden" in (r.error or "") or "destructive_blocked" in (r.error or ""),
        )

    def test_rename_save_validates_args(self):
        r = self._call("rename_save", {"save_id": "abc", "title": "x"}, trace_id="tr-1")
        self.assertFalse(r.ok)
        self.assertIn("失败", r.result or "")

    def test_continue_branch_missing_args(self):
        r = self._call("continue_branch", {"save_id": 1}, trace_id="tcb-1")
        self.assertFalse(r.ok)


# ────────────────────────────────────────────────────────────
# Phase 2.3 rules 工具
# ────────────────────────────────────────────────────────────


class RulesToolsExecution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state(turn=5)
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.state,
        )

    def _call(self, tool, args, origin="ui_button", trace_id=None):
        env = ToolCallEnvelope(
            user_id=1, save_id=100, tool=tool, args=args,
            origin=origin, trace_id=trace_id or f"t-{tool}",
        )
        return self.dispatcher.dispatch_sync(env)

    def test_module_load_validates_module_id(self):
        r = self._call("module_load", {})
        self.assertFalse(r.ok)
        self.assertIn("module_id 为空", r.result or "")

    def test_module_load_destructive_blocked_from_llm_chat(self):
        r = self._call("module_load", {"module_id": "ash_mine"},
                       origin="llm_chat", trace_id="tml-llm")
        self.assertFalse(r.ok)
        self.assertTrue(
            "destructive_blocked" in (r.error or "") or "origin_forbidden" in (r.error or ""),
        )

    def test_combat_player_attack_validates_target(self):
        r = self._call("combat_player_attack", {})
        self.assertFalse(r.ok)
        self.assertIn("target_id 为空", r.result or "")

    def test_combat_next_turn_calls_rules_bridge(self):
        """patch rules_bridge.advance_turn 让它返 ok,验证工具调通。"""
        with patch("rules_bridge.advance_turn") as adv:
            adv.return_value = {"ok": True, "encounter": {"round": 2, "turn_index": 1}}
            r = self._call("combat_next_turn", {}, trace_id="tcnt-ok")
            self.assertTrue(r.ok, r.error or r.result)
            self.assertIn("round=2", r.result)

    def test_skill_check_requires_dc(self):
        r = self._call("skill_check", {"skill": "stealth"})
        self.assertFalse(r.ok)
        self.assertIn("dc 缺失", r.result or "")

    def test_consume_item_validates_item(self):
        r = self._call("consume_item", {})
        self.assertFalse(r.ok)
        self.assertIn("item_id 为空", r.result or "")


# ────────────────────────────────────────────────────────────
# Phase 3 query 工具
# ────────────────────────────────────────────────────────────


class QueryToolsExecution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state(turn=7)
        self.state.data["player"]["name"] = "蕾穆丽娜"
        self.state.data["player"]["current_location"] = "月球基地"
        self.state.data["memory"]["main_quest"] = "营救蕾穆丽娜"
        self.state.data["relationships"]["斯雷因"] = "信任"
        self.state.data["world"]["known_events"] = ["图卢兹陷落", "蛇信叛变"]
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.state,
        )

    def _call(self, tool, args, scope="save", **kwargs):
        env = ToolCallEnvelope(
            user_id=1,
            save_id=100 if scope == "save" else None,
            script_id=8 if scope == "script" else None,
            tool=tool, args=args, origin="llm_chat",
            trace_id=kwargs.get("trace_id") or f"t-{tool}",
        )
        return self.dispatcher.dispatch_sync(env)

    def test_get_game_state(self):
        r = self._call("get_game_state", {})
        self.assertTrue(r.ok, r.error)
        d = json.loads(r.result)
        self.assertEqual(d["player"]["name"], "蕾穆丽娜")
        self.assertEqual(d["memory"]["main_quest"], "营救蕾穆丽娜")

    def test_get_game_state_with_fields_filter(self):
        r = self._call("get_game_state", {"fields": ["player", "memory"]}, trace_id="tgs-2")
        self.assertTrue(r.ok)
        d = json.loads(r.result)
        self.assertIn("player", d)
        self.assertNotIn("world", d)

    def test_query_memory_filters_by_kind(self):
        # 注入一条 hypothesis,一条 runtime_fact
        self.state.add_memory_item(text="hypo A", kind="hypothesis")
        self.state.add_memory_item(text="fact B", kind="runtime_fact")
        r = self._call("query_memory", {"kind": "hypothesis", "limit": 5},
                       trace_id="tqm-1")
        self.assertTrue(r.ok)
        d = json.loads(r.result)
        for item in d:
            self.assertEqual(item["kind"], "hypothesis")

    def test_get_known_events(self):
        r = self._call("get_known_events", {"limit": 5}, trace_id="tke-1")
        self.assertTrue(r.ok)
        d = json.loads(r.result)
        self.assertIn("图卢兹陷落", d)

    def test_list_relationships(self):
        r = self._call("list_relationships", {}, trace_id="tlr-1")
        self.assertTrue(r.ok)
        d = json.loads(r.result)
        self.assertEqual(d["斯雷因"], "信任")

    def test_list_available_tools_global(self):
        env = ToolCallEnvelope(
            user_id=1, save_id=None, tool="list_available_tools",
            args={"origin": "llm_chat"}, origin="llm_chat",
            trace_id="t-lat-llm",
        )
        r = self.dispatcher.dispatch_sync(env)
        self.assertTrue(r.ok, r.error)
        tools = json.loads(r.result)
        # llm_chat 应能看到一些只读工具但看不到 destructive
        names = {t["name"] for t in tools}
        # 至少能看到 query 工具
        self.assertIn("get_game_state", names)
        # destructive 工具不该出现 (因为 llm_chat 不在它们的 origins 里)
        self.assertNotIn("delete_save", names)
        self.assertNotIn("delete_branch", names)


# ────────────────────────────────────────────────────────────
# 跨账号隔离: 同工具不同 user 不串
# ────────────────────────────────────────────────────────────


class CrossUserQueryIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        # 两个 user 的 state 各加自己的 fact
        s1 = _new_state(); s1.add_memory("facts", "user1 fact")
        s2 = _new_state(); s2.add_memory("facts", "user2 fact")
        self.states = {1: s1, 2: s2}
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.states[env.user_id],
        )

    def test_query_memory_returns_per_user_state(self):
        def q(uid):
            env = ToolCallEnvelope(
                user_id=uid, save_id=100, tool="query_memory",
                args={"limit": 10}, origin="llm_chat",
                trace_id=f"tu{uid}",
            )
            return self.dispatcher.dispatch_sync(env)
        r1 = q(1); r2 = q(2)
        self.assertTrue(r1.ok and r2.ok)
        self.assertIn("user1", r1.result)
        self.assertNotIn("user2", r1.result)
        self.assertIn("user2", r2.result)
        self.assertNotIn("user1", r2.result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
